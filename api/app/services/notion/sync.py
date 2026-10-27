"""The one-way Notion mirror (stage 9): connect, create databases, sync runs, the job.

DisciplineOS stays the source of truth. Nothing happens until the owner connects
(``POST /integrations/notion/connect``); the job skips users without a connection.

Connect
    The secret's format and the page link are checked locally first (no request for
    obvious mistakes); then ``GET /users/me`` proves the secret and ``GET /pages/{id}``
    proves the page is shared with the connection. The secret is stored sealed
    (:mod:`app.core.secret_box`) and never leaves the server again.

Create databases (setup)
    One database per enabled kind under the chosen page, in dependency order (areas →
    projects → tasks → journal → days), with a fixed schema in the owner's language
    (:mod:`.schema`); the property ids Notion assigns are read back and stored, so
    pages are written by id. Relations whose target exists are added by the *schema
    reconcile* step (also at the start of every run — no request when nothing changed).

A run (idempotent, resumable)
    For every enabled kind with a database: compute every row's property payload,
    hash it, create pages for new rows, update pages whose hash changed, move pages of
    deleted or archived rows to the trash, and record each result in
    ``integration_links`` right away. Nothing is sent for an unchanged row. A run that
    hits its request budget simply stops; the next run continues from the links.

    Creates are not idempotent in Notion, so a create is announced first: a link
    without page id is committed *before* the request. If the answer is lost (timeout,
    5xx), Notion's ``committed_resource_id`` is adopted when present; otherwise the next
    run looks the row up by its «ID DisciplineOS» property before creating anything —
    a page is never created twice.

    One run per user at a time: a session-level PostgreSQL advisory lock on a
    dedicated connection (it dies with the connection, so a crash never leaves a stale
    lock). Every link write locks the integration row and checks its ``generation``:
    after a disconnect or a new connect, a run still in flight stops at its next write.

    A 404/403 on a database (deleted or unshared in Notion) marks that database
    ``missing`` — the other kinds go on, the app offers «Создать заново». A column the
    owner deleted is put back (schema repair) before a failed row is retried.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, ServiceUnavailable, Unprocessable
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.secret_box import SecretUnreadable, open_secret, seal_secret
from app.core.tz import utcnow
from app.db import SessionLocal, engine, session_scope
from app.models import Integration, IntegrationLink, IntegrationProvider, IntegrationStatus, UserSettings
from app.services.gamification.context import context_for
from app.services.notion import mapping
from app.services.notion.client import (
    BudgetExhausted,
    NotionClient,
    NotionError,
    NotionForbidden,
    NotionNotFound,
    NotionRateLimited,
    NotionUnauthorized,
    NotionUnavailable,
    NotionUncertain,
    NotionValidation,
    RequestBudget,
    norm_id,
)
from app.services.notion.schema import (
    DESCRIPTION,
    ENTITY_OF,
    ID_KEY,
    KIND_DEFS,
    KINDS,
    Prop,
    lang_of,
    property_config,
)

__all__ = [
    "KINDS",
    "LOCK_SCOPE",
    "RunLock",
    "RunReport",
    "check_secret",
    "connect",
    "disconnect",
    "load",
    "lock_held",
    "parse_page",
    "run_sync",
    "set_kinds",
    "setup",
    "start_background_run",
    "sync_job",
    "wait_for_background_runs",
]

log = get_logger(__name__)

PROVIDER = IntegrationProvider.NOTION.value
LOCK_SCOPE = "notion-sync"
CONNECT_BUDGET = 8
SETUP_BUDGET = 60
FAILED_PREFIX = "!"  # content_hash of a row Notion refused (retried once the row changes)
# errors that need the owner: the integration shows status "error" until they act
NEEDS_OWNER = frozenset(
    {"notion_unauthorized", "notion_secret_unreadable", "notion_forbidden", "notion_page_not_shared"}
)
# … and those the 10-minute job does not retry on its own (a new secret is needed)
JOB_SKIPS = frozenset({"notion_unauthorized", "notion_secret_unreadable"})

SECRET_RE = re.compile(r"[\x21-\x7e]{20,512}")  # printable ASCII, no spaces: it goes into a header
HEX32 = re.compile(r"[0-9a-fA-F]{32}")
NOTION_HOSTS = ("notion.so", "notion.site", "notion.com")


def now_fn() -> datetime:
    """The run's clock (tests pin it; "today" of the days kind follows it)."""
    return utcnow()


# --------------------------------------------------------------------------- #
# Input checks (local, before any request)
# --------------------------------------------------------------------------- #
def check_secret(raw: str) -> str:
    """The connection secret: 20–512 printable ASCII characters without spaces (Notion
    does not document a format; ``ntn_…`` and the older ``secret_…`` both pass)."""
    secret = (raw or "").strip()
    if not SECRET_RE.fullmatch(secret):
        raise Unprocessable(
            "This does not look like a Notion connection secret",
            code="notion_secret_invalid",
            details={"field": "secret"},
        )
    return secret


def parse_page(raw: str) -> str:
    """A Notion page link (``notion.so``, ``*.notion.site``, ``app.notion.com/p/…``, with
    a title slug, query or fragment; a page opened as a peek ``?p=<id>``) or a bare
    page id → the dashed page id. The link is only parsed, never requested."""
    value = (raw or "").strip()
    invalid = Unprocessable(
        "This is not a link to a Notion page", code="notion_page_invalid", details={"field": "page"}
    )
    if not value or len(value) > 2000:
        raise invalid
    compact = value.replace("-", "")
    if HEX32.fullmatch(compact):
        return norm_id(compact)
    parts = urlsplit(value if "://" in value else f"https://{value}")
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not any(host == h or host.endswith(f".{h}") for h in NOTION_HOSTS):
        raise invalid
    for candidate in parse_qs(parts.query).get("p", []):
        if HEX32.fullmatch(candidate.replace("-", "")):
            return norm_id(candidate.replace("-", ""))
    segment = parts.path.rstrip("/").rsplit("/", 1)[-1].replace("-", "")
    if len(segment) >= 32 and HEX32.fullmatch(segment[-32:]):
        return norm_id(segment[-32:])
    raise invalid


def page_title(page: dict[str, Any]) -> str | None:
    """The plain text of a page's title property (whatever its name)."""
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            text_value = "".join(
                str(part.get("plain_text") or (part.get("text") or {}).get("content") or "")
                for part in prop.get("title") or []
                if isinstance(part, dict)
            ).strip()
            return text_value[:200] or None
    return None


# --------------------------------------------------------------------------- #
# The run lock (session-level advisory lock on a dedicated connection)
# --------------------------------------------------------------------------- #
def lock_key(user_id: uuid.UUID) -> int:
    digest = hashlib.blake2b(user_id.bytes + LOCK_SCOPE.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


class RunLock:
    """Held for a whole run. ``pg_try_advisory_lock`` is taken on a connection of its own
    that is not in a transaction while the run talks to Notion; it is released with the
    connection, so a crashed process never leaves a stale lock behind."""

    def __init__(self, conn: AsyncConnection, key: int) -> None:
        self._conn = conn
        self._key = key

    @classmethod
    async def acquire(cls, user_id: uuid.UUID) -> RunLock | None:
        key = lock_key(user_id)
        conn = await engine.connect()
        try:
            got = await conn.scalar(select(func.pg_try_advisory_lock(key)))
            await conn.commit()  # no open transaction: the session-level lock stays
        except BaseException:
            await conn.close()
            raise
        if not got:
            await conn.close()
            return None
        return cls(conn, key)

    async def release(self) -> None:
        try:
            await self._conn.execute(select(func.pg_advisory_unlock(self._key)))
            await self._conn.commit()
        except Exception:  # pragma: no cover - the lock dies with the connection anyway
            log.warning("notion_lock_release_failed")
        finally:
            await self._conn.close()


async def lock_held(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Is a run holding the user's lock right now (any process)? Read from ``pg_locks``:
    a bigint key shows its high half in ``classid``, its low half in ``objid``."""
    unsigned = lock_key(user_id) & 0xFFFFFFFFFFFFFFFF
    return bool(
        await db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND classid = CAST(:hi AS oid) AND objid = CAST(:lo AS oid) AND objsubid = 1 "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()))"
            ),
            {"hi": unsigned >> 32, "lo": unsigned & 0xFFFFFFFF},
        )
    )


# --------------------------------------------------------------------------- #
# The integration row
# --------------------------------------------------------------------------- #
async def load(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> Integration | None:
    stmt = select(Integration).where(Integration.user_id == user_id, Integration.provider == PROVIDER)
    if lock:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


def is_active(row: Integration | None) -> bool:
    return row is not None and row.status != IntegrationStatus.DISCONNECTED.value and row.secret_encrypted is not None


def _databases(row: Integration) -> dict[str, dict[str, Any]]:
    value = (row.config or {}).get("databases")
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def enabled_kinds(row: Integration) -> list[str]:
    value = (row.config or {}).get("kinds")
    if not isinstance(value, list):
        return list(KINDS)
    return [kind for kind in KINDS if kind in value]


def _ok(database: dict[str, Any] | None) -> bool:
    return bool(database) and database.get("state") == "ok" and bool(database.get("data_source_id"))


class Cancelled(Exception):
    """The connection changed (disconnect, a new connect) while a run was in flight."""


def _object_id(obj: Any) -> str:
    """The id of an object Notion returned, normalised — an answer without a usable id is
    treated like no answer (the next run looks the row up)."""
    try:
        return norm_id(str(obj["id"]))
    except (KeyError, TypeError, ValueError):
        raise NotionUncertain("Notion answered without a usable id") from None


def _app_error(exc: NotionError, *, page_stage: bool = False) -> AppError:
    """A Notion failure → the API error the app translates (never Notion's own text)."""
    if isinstance(exc, NotionUnauthorized):
        return Unprocessable("Notion refused the connection secret", code="notion_unauthorized")
    if isinstance(exc, NotionForbidden):
        return Unprocessable("The Notion connection lacks a capability", code="notion_forbidden")
    if isinstance(exc, NotionNotFound):
        if page_stage:
            return Unprocessable("The page is not shared with the connection", code="notion_page_not_shared")
        return Unprocessable("Notion could not find it", code="notion_not_found")
    if isinstance(exc, NotionValidation):
        return Unprocessable("Notion rejected the request", code="notion_rejected")
    if isinstance(exc, NotionRateLimited):
        wait = int(exc.retry_after or 60)
        return AppError(
            "Notion asks to slow down",
            code="notion_rate_limited",
            status_code=503,
            details={"retry_after": wait},
        )
    return ServiceUnavailable("Notion is not reachable right now", code="notion_unavailable")


# --------------------------------------------------------------------------- #
# Connect / disconnect / kinds
# --------------------------------------------------------------------------- #
async def connect(db: AsyncSession, *, user_id: uuid.UUID, secret_raw: str, page_raw: str) -> Integration:
    """Validate the secret and the page with Notion, store the connection (sealed secret).
    Reconnecting to the same page keeps the databases and links (e.g. a refreshed
    secret); another page starts a fresh mirror there (the old pages stay in Notion)."""
    secret = check_secret(secret_raw)
    page_id = parse_page(page_raw)
    await db.commit()  # nothing (e.g. the device row) stays locked over the network
    async with NotionClient(secret, budget=RequestBudget(CONNECT_BUDGET)) as client:
        try:
            me = await client.me()
        except NotionNotFound:
            raise Unprocessable("Notion refused the connection secret", code="notion_unauthorized") from None
        except NotionError as exc:
            raise _app_error(exc) from None
        try:
            page = await client.retrieve_page(page_id)
        except NotionValidation:  # e.g. the link of a database, not of a page
            raise Unprocessable("This is not a link to a Notion page", code="notion_page_invalid") from None
        except NotionError as exc:
            raise _app_error(exc, page_stage=True) from None
    if page.get("in_trash") or page.get("archived"):
        raise Unprocessable("The page is in the Notion trash", code="notion_page_in_trash")
    bot = me.get("bot") if isinstance(me.get("bot"), dict) else {}
    row = await load(db, user_id, lock=True)
    if row is None:
        row = Integration(user_id=user_id, provider=PROVIDER, status=IntegrationStatus.DISCONNECTED.value, config={})
        row.generation = 0
        db.add(row)
        await db.flush()
    previous = row.config or {}
    same_page = is_active(row) and (previous.get("page") or {}).get("id") == page_id
    if not same_page:
        await db.execute(delete(IntegrationLink).where(IntegrationLink.integration_id == row.id))
        row.counts = {}
        row.last_sync_at = None
        row.run_id = row.run_started_at = row.run_finished_at = None
    elif row.run_started_at is not None and (row.run_finished_at is None or row.run_finished_at < row.run_started_at):
        row.run_finished_at = utcnow()  # a run in flight stops at its next write (new generation)
    row.config = {
        "page": {"id": page_id, "title": page_title(page), "url": page.get("url")},
        "bot": {
            "name": (str(me.get("name"))[:120] if me.get("name") else None),
            "workspace": (str(bot.get("workspace_name"))[:120] if bot.get("workspace_name") else None),
        },
        "kinds": enabled_kinds(row) if same_page else list(KINDS),
        "databases": _databases(row) if same_page else {},
        "connected_at": utcnow().isoformat(),
    }
    row.secret_encrypted = seal_secret(secret)
    row.status = IntegrationStatus.CONNECTED.value
    row.last_error = None
    row.generation = int(row.generation or 0) + 1
    await db.flush()
    log.info("notion_connected", same_page=same_page)
    return row


async def disconnect(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Stop syncing: the secret and every link are deleted; Notion content stays. A run in
    flight stops at its next write (the generation changes)."""
    row = await load(db, user_id, lock=True)
    if row is None:
        return
    await db.execute(delete(IntegrationLink).where(IntegrationLink.integration_id == row.id))
    row.secret_encrypted = None
    row.status = IntegrationStatus.DISCONNECTED.value
    row.generation = int(row.generation or 0) + 1
    row.config = {}
    row.counts = {}
    row.last_error = None
    row.last_sync_at = None
    row.run_id = row.run_started_at = row.run_finished_at = None
    await db.flush()
    log.info("notion_disconnected")


async def set_kinds(db: AsyncSession, user_id: uuid.UUID, kinds: list[str]) -> Integration:
    """Enable exactly ``kinds`` (a disabled kind keeps its database and pages; it is just
    not synced any more)."""
    row = await load(db, user_id, lock=True)
    if not is_active(row):
        raise Conflict("Notion is not connected", code="notion_not_connected")
    assert row is not None
    row.config = {**(row.config or {}), "kinds": [kind for kind in KINDS if kind in kinds]}
    await db.flush()
    return row


# --------------------------------------------------------------------------- #
# Schema: create databases, reconcile relations, repair deleted columns
# --------------------------------------------------------------------------- #
class Schema:
    """The databases of one connection while a setup or a run works on them."""

    def __init__(self, client: NotionClient, *, page_id: str, databases: dict[str, dict[str, Any]], lang: str) -> None:
        self.client = client
        self.page_id = page_id
        self.databases = databases
        self.lang = lang
        self.dirty: set[str] = set()

    def db(self, kind: str) -> dict[str, Any] | None:
        return self.databases.get(kind)

    def lang_for(self, kind: str) -> str:
        return str((self.databases.get(kind) or {}).get("lang") or self.lang)

    def mark_missing(self, kind: str) -> None:
        database = self.databases.get(kind)
        if database and database.get("state") != "missing":
            database["state"] = "missing"
            self.dirty.add(kind)
            log.info("notion_database_missing", kind=kind)

    @staticmethod
    def _ids_by_name(data_source: dict[str, Any]) -> dict[str, tuple[str, str]]:
        out: dict[str, tuple[str, str]] = {}
        for name, prop in (data_source.get("properties") or {}).items():
            if isinstance(prop, dict) and prop.get("id"):
                out[str(prop.get("name") or name)] = (str(prop["id"]), str(prop.get("type") or ""))
        return out

    async def create(self, kind: str) -> None:
        """A new database for ``kind`` (also «Создать заново» for a missing one)."""
        kdef = KIND_DEFS[kind]
        lang = self.lang
        properties: dict[str, Any] = {}
        relations: dict[str, str] = {}
        for prop in kdef.props:
            if prop.type == "relation":
                target = self.databases.get(prop.target or "")
                if prop.target == kind or not _ok(target):
                    continue  # added by reconcile once the target exists
                assert target is not None
                config = property_config(prop, lang, target_data_source=target["data_source_id"])
                relations[prop.key] = target["data_source_id"]
            else:
                config = property_config(prop, lang)
            if config is not None:
                properties[prop.name(lang)] = config
        try:
            created = await self.client.create_database(
                page_id=self.page_id,
                title=kdef.title(lang),
                icon=kdef.icon,
                properties=properties,
                description=DESCRIPTION[lang],
            )
        except NotionUncertain as exc:
            if not exc.committed_id:
                raise
            created = await self.client.retrieve_database(exc.committed_id)  # Notion saved it after all
        database_id = _object_id(created)
        sources = created.get("data_sources") or []
        if not sources or not isinstance(sources[0], dict):
            raise NotionUnavailable("the new database has no data source")
        data_source_id = _object_id(sources[0])
        data_source = await self.client.retrieve_data_source(data_source_id)
        by_name = self._ids_by_name(data_source)
        props: dict[str, str] = {}
        for prop in kdef.props:
            found = by_name.get(prop.name(lang))
            if found is not None and (prop.type != "relation" or prop.key in relations):
                props[prop.key] = found[0]
        self.databases[kind] = {
            "database_id": database_id,
            "data_source_id": data_source_id,
            "url": created.get("url"),
            "lang": lang,
            "state": "ok",
            "props": props,
            "relations": relations,
            "created_at": utcnow().isoformat(),
        }
        self.dirty.add(kind)
        log.info("notion_database_created", kind=kind)

    async def reconcile(self) -> None:
        """Add relations whose target exists now (or was created again). No request when
        every relation already points at its current target. A database found gone on
        the way is marked missing; the others go on."""
        for kind in KINDS:
            database = self.databases.get(kind)
            if not _ok(database):
                continue
            assert database is not None
            lang = self.lang_for(kind)
            props: dict[str, str] = database.setdefault("props", {})
            relations: dict[str, str] = database.setdefault("relations", {})
            removals: dict[str, Any] = {}
            additions: dict[str, Any] = {}
            for prop in KIND_DEFS[kind].relations:
                target = self.databases.get(prop.target or "")
                if not _ok(target):
                    continue  # nothing to point at: values for it are simply not written
                assert target is not None
                if relations.get(prop.key) == target["data_source_id"] and prop.key in props:
                    continue
                if prop.key in props:
                    removals[props[prop.key]] = None  # it points at a database that is gone
                additions[prop.name(lang)] = property_config(prop, lang, target_data_source=target["data_source_id"])
            if not additions:
                continue
            try:
                if removals:
                    await self.client.update_data_source(database["data_source_id"], removals)
                updated = await self.client.update_data_source(database["data_source_id"], additions)
            except (NotionNotFound, NotionForbidden):
                self.mark_missing(kind)
                continue
            by_name = self._ids_by_name(updated)
            for prop in KIND_DEFS[kind].relations:
                found = by_name.get(prop.name(lang))
                target = self.databases.get(prop.target or "")
                if prop.name(lang) in additions and found is not None and _ok(target):
                    assert target is not None
                    props[prop.key] = found[0]
                    relations[prop.key] = target["data_source_id"]
            self.dirty.add(kind)

    async def repair(self, kind: str) -> bool:
        """Put back properties the owner deleted (or turned into another type) in Notion;
        a deleted relation is added again by :meth:`reconcile`. Returns whether anything
        was changed."""
        database = self.databases.get(kind)
        if not _ok(database):
            return False
        assert database is not None
        lang = self.lang_for(kind)
        data_source = await self.client.retrieve_data_source(database["data_source_id"])
        if data_source.get("in_trash") or data_source.get("archived"):
            self.mark_missing(kind)  # the database is in the Notion trash: nothing to repair
            raise NotionNotFound("the database is in the trash")
        by_id = {prop_id: (name, type_) for name, (prop_id, type_) in self._ids_by_name(data_source).items()}
        names = {name for name, _ in by_id.values()}
        props: dict[str, str] = database.setdefault("props", {})
        relations: dict[str, str] = database.setdefault("relations", {})
        additions: dict[str, Any] = {}
        wanted: dict[str, Prop] = {}
        dropped = False
        for prop in KIND_DEFS[kind].props:
            current = by_id.get(props.get(prop.key, ""))
            if current is not None and current[1] == prop.type:
                continue
            if prop.type == "relation":
                if prop.key in props:  # deleted in Notion: forget it, reconcile adds it again
                    props.pop(prop.key, None)
                    relations.pop(prop.key, None)
                    dropped = True
                continue
            if prop.type == "title":
                continue  # a data source always keeps its title property
            name = prop.name(lang)
            if name in names:
                name = f"{name} (DisciplineOS)"  # the name is taken by another column
            config = property_config(prop, lang)
            if config is not None:
                additions[name] = config
                wanted[name] = prop
        if additions:
            updated = await self.client.update_data_source(database["data_source_id"], additions)
            for name, (prop_id, _type) in self._ids_by_name(updated).items():
                if name in wanted:
                    props[wanted[name].key] = prop_id
        if not additions and not dropped:
            return False
        self.dirty.add(kind)
        if dropped:
            await self.reconcile()
        log.info("notion_schema_repaired", kind=kind, properties=len(additions), relations=dropped)
        return True

    async def check(self, kind: str) -> bool:
        """Is the database still there for the connection? (404/403/trash → missing)"""
        database = self.databases.get(kind)
        if not _ok(database):
            return False
        assert database is not None
        try:
            data_source = await self.client.retrieve_data_source(database["data_source_id"])
        except (NotionNotFound, NotionForbidden):
            self.mark_missing(kind)
            return False
        if data_source.get("in_trash") or data_source.get("archived"):
            self.mark_missing(kind)
            return False
        return True


async def _persist_databases(user_id: uuid.UUID, generation: int, schema: Schema) -> None:
    if not schema.dirty:
        return
    async with session_scope() as db:
        row = await load(db, user_id, lock=True)
        if row is None or row.generation != generation or not is_active(row):
            raise Cancelled
        databases = _databases(row)
        for kind in schema.dirty:
            if kind in schema.databases:
                databases[kind] = schema.databases[kind]
        row.config = {**(row.config or {}), "databases": databases}
    schema.dirty.clear()


async def setup(user_id: uuid.UUID, kinds: list[str]) -> None:
    """Create the databases ``kinds`` are missing (none yet, or ``missing`` in Notion) and
    enable those kinds. Idempotent: a kind with a working database is left alone."""
    lock = await RunLock.acquire(user_id)
    if lock is None:
        raise Conflict("A Notion sync is running; try again when it is done", code="notion_sync_running")
    try:
        async with session_scope() as db:
            row = await load(db, user_id, lock=True)
            if not is_active(row):
                raise Conflict("Notion is not connected", code="notion_not_connected")
            assert row is not None
            settings_row = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
            try:
                secret = open_secret(row.secret_encrypted or "")
            except SecretUnreadable:
                secret = None
                row.status, row.last_error = IntegrationStatus.ERROR.value, "notion_secret_unreadable"
            wanted = [kind for kind in KINDS if kind in kinds]
            row.config = {**(row.config or {}), "kinds": sorted(set(enabled_kinds(row)) | set(wanted), key=KINDS.index)}
            generation = row.generation
            page_id = str(((row.config or {}).get("page") or {}).get("id") or "")
            databases = _databases(row)
            lang = lang_of(getattr(settings_row, "language", None))
        if secret is None:  # the error state above is committed first
            raise Unprocessable("The stored secret cannot be read; connect again", code="notion_secret_unreadable")
        async with NotionClient(secret, budget=RequestBudget(SETUP_BUDGET)) as client:
            schema = Schema(client, page_id=page_id, databases=databases, lang=lang)
            try:
                for kind in wanted:
                    if not _ok(databases.get(kind)):
                        recreate = kind in databases
                        await schema.create(kind)
                        if recreate:
                            await _drop_links(user_id, generation, ENTITY_OF[kind])
                        await _persist_databases(user_id, generation, schema)
                await schema.reconcile()
            except NotionError as exc:
                await _persist_databases(user_id, generation, schema)
                await _note_error(user_id, generation, exc, page_stage=True)
                raise _app_error(exc, page_stage=True) from None
            await _persist_databases(user_id, generation, schema)
    except Cancelled:
        raise Conflict("The Notion connection changed meanwhile", code="notion_not_connected") from None
    finally:
        await lock.release()


async def _drop_links(user_id: uuid.UUID, generation: int, entity_type: str) -> None:
    async with session_scope() as db:
        row = await load(db, user_id, lock=True)
        if row is None or row.generation != generation:
            raise Cancelled
        await db.execute(
            delete(IntegrationLink).where(
                IntegrationLink.integration_id == row.id, IntegrationLink.entity_type == entity_type
            )
        )


def _error_code(exc: NotionError, *, page_stage: bool = False) -> str:
    if isinstance(exc, NotionNotFound) and page_stage:
        return "notion_page_not_shared"
    return exc.code


async def _note_error(user_id: uuid.UUID, generation: int, exc: NotionError, *, page_stage: bool = False) -> None:
    """A setup failure the owner must act on is shown on the card (status ``error``)."""
    code = _error_code(exc, page_stage=page_stage)
    if code not in NEEDS_OWNER:
        return
    async with session_scope() as db:
        row = await load(db, user_id, lock=True)
        if row is not None and row.generation == generation and is_active(row):
            row.status, row.last_error = IntegrationStatus.ERROR.value, code


# --------------------------------------------------------------------------- #
# A run
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class KindCounts:
    created: int = 0
    updated: int = 0
    trashed: int = 0
    unchanged: int = 0
    failed: int = 0
    pending: int = 0
    total: int = 0  # pages mirrored after the run
    state: str = "ok"  # ok | missing | none (no database) | skipped (disabled) | stopped

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "trashed": self.trashed,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "pending": self.pending,
            "total": self.total,
            "state": self.state,
        }


@dataclass(slots=True)
class RunReport:
    run_id: uuid.UUID | None
    result: str  # ok | partial | error | busy | skipped | cancelled
    error: str | None = None
    error_kind: str | None = None
    requests: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    kinds: dict[str, KindCounts] = field(default_factory=dict)

    def totals(self) -> dict[str, int]:
        keys = ("created", "updated", "trashed", "unchanged", "failed", "pending", "total")
        return {key: sum(getattr(counts, key) for counts in self.kinds.values()) for key in keys}

    def as_counts(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id) if self.run_id else None,
            "result": self.result,
            "error": self.error,
            "error_kind": self.error_kind,
            "requests": self.requests,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "totals": self.totals(),
            "kinds": {kind: counts.as_dict() for kind, counts in self.kinds.items()},
        }


class _Stop(Exception):
    """End the run with ``code`` (the kinds done so far keep their results)."""

    def __init__(self, code: str, *, partial: bool, kind: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.partial = partial
        self.kind = kind


@dataclass(slots=True)
class _Link:
    external_id: str | None
    content_hash: str | None
    in_trash: bool


class _Missing(Exception):
    """The kind's database is gone in Notion (marked missing; the run goes on)."""


class Engine:
    """One run's work on the kinds of one connection."""

    def __init__(
        self,
        *,
        user_id: uuid.UUID,
        integration_id: uuid.UUID,
        generation: int,
        schema: Schema,
        client: NotionClient,
        report: RunReport,
    ) -> None:
        self.user_id = user_id
        self.integration_id = integration_id
        self.generation = generation
        self.schema = schema
        self.client = client
        self.report = report
        self.pages: dict[str, dict[str, str]] = {}  # kind → entity id → page id (live pages)
        self.repaired: set[str] = set()

    # ---------------------------------------------------------------- links
    async def _write(self, entity_type: str, entity_id: str, *, drop: bool = False, **values: Any) -> None:
        """Record one result right away. The integration row is locked meanwhile, so a
        disconnect either waits for this write or happens before it — then nothing is
        written and the run stops."""
        async with SessionLocal() as db:
            row = await db.scalar(select(Integration).where(Integration.id == self.integration_id).with_for_update())
            if row is None or row.generation != self.generation or not is_active(row):
                await db.rollback()
                raise Cancelled
            if drop:
                await db.execute(
                    delete(IntegrationLink).where(
                        IntegrationLink.integration_id == self.integration_id,
                        IntegrationLink.entity_type == entity_type,
                        IntegrationLink.entity_id == entity_id,
                    )
                )
            else:
                now = utcnow()
                stmt = pg_insert(IntegrationLink).values(
                    id=new_id(),
                    user_id=self.user_id,
                    integration_id=self.integration_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    external_id=values.get("external_id"),
                    content_hash=values.get("content_hash"),
                    in_trash=bool(values.get("in_trash", False)),
                    synced_at=now if values.get("external_id") else None,
                    created_at=now,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_integration_links_entity",
                    set_={
                        "external_id": stmt.excluded.external_id,
                        "content_hash": stmt.excluded.content_hash,
                        "in_trash": stmt.excluded.in_trash,
                        "synced_at": stmt.excluded.synced_at,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                await db.execute(stmt)
            await db.commit()

    async def _links(self, entity_type: str) -> dict[str, _Link]:
        async with SessionLocal() as db:
            rows = await db.execute(
                select(
                    IntegrationLink.entity_id,
                    IntegrationLink.external_id,
                    IntegrationLink.content_hash,
                    IntegrationLink.in_trash,
                ).where(
                    IntegrationLink.integration_id == self.integration_id,
                    IntegrationLink.user_id == self.user_id,
                    IntegrationLink.entity_type == entity_type,
                )
            )
            return {row[0]: _Link(row[1], row[2], bool(row[3])) for row in rows}

    @staticmethod
    def _live(links: dict[str, _Link]) -> dict[str, str]:
        return {
            entity: link.external_id
            for entity, link in links.items()
            if link.external_id is not None and not link.in_trash
        }

    async def _pages(self, kind: str) -> dict[str, str]:
        if kind not in self.pages:
            self.pages[kind] = self._live(await self._links(ENTITY_OF[kind]))
        return self.pages[kind]

    # -------------------------------------------------------------- payloads
    async def _payload(self, kind: str, row: mapping.Row) -> dict[str, Any]:
        """Internal keys → property ids, relations and options resolved; a property the
        database does not have (yet) is left out."""
        database = self.schema.db(kind) or {}
        props: dict[str, str] = database.get("props") or {}
        relations: dict[str, str] = database.get("relations") or {}
        lang = self.schema.lang_for(kind)
        kdef = KIND_DEFS[kind]
        payload: dict[str, Any] = {}
        for key, value in row.values.items():
            prop_id = props.get(key)
            if prop_id is None:
                continue
            if isinstance(value, mapping.RelationRef):
                target = self.schema.db(value.target)
                if not _ok(target) or relations.get(key) != (target or {}).get("data_source_id"):
                    continue
                pages = await self._pages(value.target)
                payload[prop_id] = {"relation": [{"id": pages[ref]} for ref in value.ids if ref in pages]}
            elif isinstance(value, tuple) and len(value) == 2 and value[0] == "option":
                try:
                    payload[prop_id] = mapping.select_value(kdef.prop(key).option(value[1], lang))
                except KeyError:
                    payload[prop_id] = mapping.select_value(None)
            else:
                payload[prop_id] = value
        return payload

    # ----------------------------------------------------------------- kinds
    async def run(self, kinds: list[str]) -> None:
        for kind in KINDS:
            counts = self.report.kinds.setdefault(kind, KindCounts())
            database = self.schema.db(kind)
            if kind not in kinds:
                counts.state = "skipped"
            elif not database:
                counts.state = "none"
            elif not _ok(database):
                counts.state = "missing"
            else:
                counts.state = "queued"
        for kind in KINDS:
            counts = self.report.kinds[kind]
            if counts.state != "queued":
                continue
            counts.state = "running"
            try:
                await self._sync_kind(kind, counts)
                counts.state = "ok"
            except _Missing:
                counts.state = "missing"

    async def _sync_kind(self, kind: str, counts: KindCounts) -> None:
        entity_type = ENTITY_OF[kind]
        async with SessionLocal() as db:
            settings_row = await db.scalar(select(UserSettings).where(UserSettings.user_id == self.user_id))
            ctx = context_for(settings_row, self.user_id, now_fn())
            built = await mapping.rows_for(kind, db, ctx, self.schema.lang_for(kind))
            await db.rollback()  # read only; nothing stays open while Notion is called
        links = await self._links(entity_type)
        self.pages[kind] = self._live(links)
        for row in sorted(built.rows.values(), key=lambda item: item.order):
            await self._sync_row(kind, row, links.get(row.entity_id), counts)
        for entity_id, link in links.items():
            if entity_id in built.rows or link.in_trash or not built.in_scope(entity_id):
                continue
            await self._trash(kind, entity_id, link, counts)

    async def _sync_row(self, kind: str, row: mapping.Row, link: _Link | None, counts: KindCounts) -> None:
        payload = await self._payload(kind, row)
        digest = mapping.content_hash(payload)
        if link is None:
            await self._create(kind, row, payload, digest, counts, look_up=False)
        elif link.external_id is None:
            if link.content_hash == FAILED_PREFIX + digest:
                counts.failed += 1  # refused before with exactly this content
                return
            # announced earlier but the answer was lost: look it up before creating
            await self._create(kind, row, payload, digest, counts, look_up=link.content_hash is None)
        elif link.in_trash:
            await self._update(kind, row, link, payload, digest, counts)  # back from the archive
        elif link.content_hash == digest:
            counts.unchanged += 1
        elif link.content_hash == FAILED_PREFIX + digest:
            counts.failed += 1
        else:
            await self._update(kind, row, link, payload, digest, counts)

    async def _gone_or_stop(self, kind: str, exc: NotionError) -> None:
        """After a 404/403 on a page: is the whole database gone (→ ``_Missing``), is a
        capability missing (403 with the database readable → stop the run), or only
        this page affected (returns)?"""
        if not await self.schema.check(kind):
            await _persist_databases(self.user_id, self.generation, self.schema)
            raise _Missing
        if isinstance(exc, NotionForbidden):
            raise _Stop("notion_forbidden", partial=True, kind=kind)

    async def _missing(self, kind: str) -> None:
        self.schema.mark_missing(kind)
        await _persist_databases(self.user_id, self.generation, self.schema)
        raise _Missing

    async def _find(self, kind: str, entity_id: str) -> str | None:
        """The page of ``entity_id`` if an earlier create was saved after all (by the ID
        property); duplicates, should there be any, go to the trash."""
        database = self.schema.db(kind) or {}
        prop_id = (database.get("props") or {}).get(ID_KEY)
        if not prop_id:
            return None
        try:
            found = await self.client.query_data_source(
                database["data_source_id"], filter={"property": prop_id, "rich_text": {"equals": entity_id}}
            )
        except (NotionNotFound, NotionForbidden):
            await self._missing(kind)
        ids: list[str] = []
        for item in found.get("results") or []:
            if isinstance(item, dict) and not item.get("in_trash"):
                try:
                    ids.append(_object_id(item))
                except NotionUncertain:
                    continue
        for duplicate in ids[1:]:
            await self.client.update_page(duplicate, in_trash=True)
        return ids[0] if ids else None

    async def _create(
        self,
        kind: str,
        row: mapping.Row,
        payload: dict[str, Any],
        digest: str,
        counts: KindCounts,
        *,
        look_up: bool,
    ) -> None:
        entity_type = ENTITY_OF[kind]
        if look_up:
            existing = await self._find(kind, row.entity_id)
            if existing is not None:
                await self._update(kind, row, _Link(existing, None, False), payload, digest, counts, adopted=True)
                return
        # announced before the request: a lost answer is looked up next time, never created twice
        await self._write(entity_type, row.entity_id, external_id=None, content_hash=None)
        database = self.schema.db(kind) or {}
        page_id: str | None = None
        for attempt in (1, 2):
            try:
                page = await self.client.create_page(database["data_source_id"], payload)
                page_id = _object_id(page)
                break
            except NotionUncertain as exc:
                if exc.committed_id:  # Notion said it saved the page
                    try:
                        page_id = norm_id(exc.committed_id)
                        break
                    except ValueError:
                        pass
                counts.pending += 1  # stays announced: the next run looks it up
                return
            except NotionValidation:
                if attempt == 1 and await self._repair(kind):
                    payload = await self._payload(kind, row)
                    digest = mapping.content_hash(payload)
                    continue
                await self._write(entity_type, row.entity_id, external_id=None, content_hash=FAILED_PREFIX + digest)
                counts.failed += 1
                log.info("notion_row_refused", kind=kind, entity_id=row.entity_id)
                return
            except (NotionNotFound, NotionForbidden) as exc:
                await self._write(entity_type, row.entity_id, drop=True)  # nothing was created
                await self._gone_or_stop(kind, exc)
                counts.failed += 1
                return
            except NotionError:
                # 401, 429 or the budget: the request was refused or never sent — nothing created
                await self._write(entity_type, row.entity_id, drop=True)
                raise
        assert page_id is not None
        await self._write(entity_type, row.entity_id, external_id=page_id, content_hash=digest)
        self.pages.setdefault(kind, {})[row.entity_id] = page_id
        counts.created += 1

    async def _update(
        self,
        kind: str,
        row: mapping.Row,
        link: _Link,
        payload: dict[str, Any],
        digest: str,
        counts: KindCounts,
        *,
        adopted: bool = False,
    ) -> None:
        entity_type = ENTITY_OF[kind]
        page_id = link.external_id
        assert page_id is not None
        restore = link.in_trash
        repaired = False
        while True:
            try:
                await self.client.update_page(page_id, properties=payload, in_trash=False if restore else None)
                break
            except NotionValidation:
                if not restore:
                    restore = True  # the page may have been trashed in Notion: the mirror brings it back
                    continue
                if not repaired and await self._repair(kind):
                    repaired = True
                    payload = await self._payload(kind, row)
                    digest = mapping.content_hash(payload)
                    continue
                await self._write(entity_type, row.entity_id, external_id=page_id, content_hash=FAILED_PREFIX + digest)
                counts.failed += 1
                log.info("notion_row_refused", kind=kind, entity_id=row.entity_id)
                return
            except (NotionNotFound, NotionForbidden) as exc:
                await self._gone_or_stop(kind, exc)
                # the page alone is gone for good (deleted from the trash): create it again
                await self._write(entity_type, row.entity_id, drop=True)
                self.pages.get(kind, {}).pop(row.entity_id, None)
                await self._create(kind, row, payload, digest, counts, look_up=False)
                return
        await self._write(entity_type, row.entity_id, external_id=page_id, content_hash=digest)
        self.pages.setdefault(kind, {})[row.entity_id] = page_id
        if adopted:
            counts.created += 1
        else:
            counts.updated += 1

    async def _trash(self, kind: str, entity_id: str, link: _Link, counts: KindCounts) -> None:
        entity_type = ENTITY_OF[kind]
        if link.external_id is None:
            if link.content_hash is None:
                # announced, outcome unknown: if it was saved after all, trash that page
                found = await self._find(kind, entity_id)
                if found is not None:
                    await self.client.update_page(found, in_trash=True)
            await self._write(entity_type, entity_id, drop=True)
            return
        try:
            await self.client.update_page(link.external_id, in_trash=True)
        except (NotionNotFound, NotionValidation):
            pass  # already gone, or already in the trash
        except NotionForbidden as exc:
            await self._gone_or_stop(kind, exc)
        await self._write(entity_type, entity_id, external_id=link.external_id, content_hash=None, in_trash=True)
        self.pages.get(kind, {}).pop(entity_id, None)
        counts.trashed += 1

    async def _repair(self, kind: str) -> bool:
        """Once per kind and run: put back columns deleted in Notion."""
        if kind in self.repaired:
            return False
        self.repaired.add(kind)
        try:
            changed = await self.schema.repair(kind)
        except (NotionNotFound, NotionForbidden):
            await self._missing(kind)
        if changed:
            await _persist_databases(self.user_id, self.generation, self.schema)
        return changed


async def _claim(user_id: uuid.UUID, run_id: uuid.UUID) -> Integration | None:
    """Mark the run as started on the integration row (``None``: nothing to sync)."""
    async with session_scope() as db:
        row = await load(db, user_id, lock=True)
        if not is_active(row):
            return None
        assert row is not None
        if row.run_id != run_id or row.run_finished_at is not None or row.run_started_at is None:
            row.run_id = run_id
            row.run_started_at = utcnow()
            row.run_finished_at = None
        await db.flush()
        db.expunge(row)
        return row


async def _finish(user_id: uuid.UUID, generation: int, report: RunReport) -> None:
    async with session_scope() as db:
        row = await load(db, user_id, lock=True)
        if row is None or row.generation != generation or not is_active(row):
            return  # a newer connection (or none) owns the row now
        live = dict(
            (
                await db.execute(
                    select(IntegrationLink.entity_type, func.count())
                    .where(
                        IntegrationLink.integration_id == row.id,
                        IntegrationLink.external_id.is_not(None),
                        IntegrationLink.in_trash.is_(False),
                    )
                    .group_by(IntegrationLink.entity_type)
                )
            ).all()
        )
        for kind, counts in report.kinds.items():
            counts.total = int(live.get(ENTITY_OF[kind], 0))
        report.finished_at = utcnow()
        row.run_finished_at = report.finished_at
        row.counts = report.as_counts()
        row.last_error = report.error
        if report.result in ("ok", "partial"):
            row.last_sync_at = report.finished_at
        row.status = IntegrationStatus.ERROR.value if report.error in NEEDS_OWNER else IntegrationStatus.CONNECTED.value


async def _run_locked(user_id: uuid.UUID, run_id: uuid.UUID, budget: int | None) -> RunReport:
    row = await _claim(user_id, run_id)
    if row is None:
        return RunReport(run_id=None, result="skipped")
    report = RunReport(run_id=run_id, result="ok", started_at=row.run_started_at)
    generation = row.generation
    try:
        secret = open_secret(row.secret_encrypted or "")
    except SecretUnreadable:
        report.result, report.error = "error", "notion_secret_unreadable"
        await _finish(user_id, generation, report)
        return report
    async with session_scope() as db:
        settings_row = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
        lang = lang_of(getattr(settings_row, "language", None))
    page_id = str(((row.config or {}).get("page") or {}).get("id") or "")
    limit = budget if budget is not None else get_settings().notion_request_budget
    async with NotionClient(secret, budget=RequestBudget(limit)) as client:
        schema = Schema(client, page_id=page_id, databases=_databases(row), lang=lang)
        runner = Engine(
            user_id=user_id,
            integration_id=row.id,
            generation=generation,
            schema=schema,
            client=client,
            report=report,
        )
        try:
            await schema.reconcile()
            await _persist_databases(user_id, generation, schema)
            await runner.run(enabled_kinds(row))
        except Cancelled:
            log.info("notion_sync_cancelled")
            report.result = "cancelled"
            return report
        except BudgetExhausted:
            report.result = "partial"  # the next run continues from the links
        except _Stop as stop:
            report.result = "partial" if stop.partial else "error"
            report.error, report.error_kind = stop.code, stop.kind
        except NotionRateLimited:
            report.result, report.error = "partial", "notion_rate_limited"
        except NotionError as exc:
            report.result = "error"
            report.error = "notion_unavailable" if isinstance(exc, NotionUnavailable) else exc.code
        except Exception as exc:  # a bug or the database: logged, and the card says the run failed
            log.exception("notion_sync_crashed", error=type(exc).__name__)
            report.result, report.error = "error", "notion_error"
        finally:
            report.requests = client.requests
        for counts in report.kinds.values():
            if counts.state in ("queued", "running"):
                counts.state = "stopped"  # the next run picks it up
        if report.error is None and any(counts.state == "missing" for counts in report.kinds.values()):
            report.error = "notion_database_missing"
        try:
            await _persist_databases(user_id, generation, schema)
        except Cancelled:
            report.result = "cancelled"
            return report
    await _finish(user_id, generation, report)
    log.info(
        "notion_sync_finished",
        result=report.result,
        error=report.error,
        requests=report.requests,
        **{key: value for key, value in report.totals().items() if value},
    )
    return report


async def run_sync(user_id: uuid.UUID, *, run_id: uuid.UUID | None = None, budget: int | None = None) -> RunReport:
    """One run for one user now (the job, tests). ``busy`` when another one holds the lock."""
    lock = await RunLock.acquire(user_id)
    if lock is None:
        return RunReport(run_id=None, result="busy")
    try:
        return await _run_locked(user_id, run_id or new_id(), budget)
    finally:
        await lock.release()


# --------------------------------------------------------------------------- #
# Background runs (POST /integrations/notion/sync) and the job
# --------------------------------------------------------------------------- #
_BACKGROUND: set[asyncio.Task[RunReport | None]] = set()


async def start_background_run(user_id: uuid.UUID) -> uuid.UUID:
    """Take the lock now (409 when a run holds it), mark the run started and continue it
    in the background; the app polls ``GET /integrations/notion``."""
    lock = await RunLock.acquire(user_id)
    if lock is None:
        raise Conflict("A Notion sync is already running", code="notion_sync_running")
    run_id = new_id()
    try:
        if await _claim(user_id, run_id) is None:
            raise Conflict("Notion is not connected", code="notion_not_connected")
    except BaseException:
        await lock.release()
        raise

    async def _go() -> RunReport | None:
        try:
            return await _run_locked(user_id, run_id, None)
        except Exception as exc:  # logged; the card shows the run as interrupted
            log.exception("notion_sync_failed", error=type(exc).__name__)
            return None
        finally:
            await lock.release()

    task = asyncio.create_task(_go(), name=f"notion-sync-{run_id}")
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return run_id


async def wait_for_background_runs() -> None:
    """Tests (and a graceful shutdown) wait for runs started by the API."""
    while _BACKGROUND:
        await asyncio.gather(*list(_BACKGROUND), return_exceptions=True)


async def sync_job() -> None:
    """Every 10 minutes (``app.services.hooks``): one run per connected user, one user
    after another. No connection → no query beyond this one, no request to Notion."""
    async with session_scope() as db:
        user_ids = (
            await db.scalars(
                select(Integration.user_id).where(
                    Integration.provider == PROVIDER,
                    Integration.status != IntegrationStatus.DISCONNECTED.value,
                    Integration.secret_encrypted.is_not(None),
                    (Integration.last_error.is_(None)) | (Integration.last_error.not_in(sorted(JOB_SKIPS))),
                )
            )
        ).all()
    for user_id in user_ids:
        try:
            report = await run_sync(user_id)
            if report.result == "busy":
                log.info("notion_sync_busy")
        except Exception as exc:  # one user's trouble never stops the others
            log.exception("notion_sync_failed", error=type(exc).__name__)
