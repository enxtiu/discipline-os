"""Restore a backup zip — ``python -m app.cli restore-backup <zip> [--user-id …] [--dry-run]``.

* **Verified before anything is written.** The zip must hold ``manifest.json`` and
  ``data.json``; every file listed in the manifest must match its sha256 and size (and
  its zip CRC), both documents must be known formats of the same account and schema,
  the row counts must match the data, and the backup's schema version must be this
  database's alembic revision.
* **Signed by this server, or into an empty account.** Checksums only say a file is
  intact; the manifest's HMAC signature (``app.services.export``) says this server — or
  one sharing its ``BACKUP_SIGNING_KEY`` — made it, unchanged. A **signed** backup goes
  back to the account it came from, or with ``--user-id`` into an empty one. A backup
  **not signed** here (edited, made by another server, or before the signing key
  changed) goes only into an account named with ``--user-id`` that is empty — the
  manifest's ``user_id`` never chooses an account — and its derived gamification rows
  (XP, daily scores, streaks, achievements, quests) are not taken: the engine rebuilds
  them from the restored records on its next run (bounds cannot tell a plausible
  forgery from real history; the records can). "Empty" means holding none of the
  user's own records — what a new account starts with (settings, the default areas
  and tags, streak rows, today's score and quests) is fine: the backup's rows take
  their place. For a signed backup rows of this very backup are fine too (the same
  restore run again changes nothing); an unsigned one is taken by a truly empty
  account only (a second run is refused).
* **Values are checked, never trusted** (signed or not). Every value must have its
  column's type; JSON columns the shape the app's readers rely on (an object or an
  array as the column's default, never ``null``; lists of text, reminder definitions,
  a score breakdown of numbers in range …); settings the API's bounds and its safety
  rule — a safe floor below the default, or a calorie target below the floor, without
  ``safe_floor_confirmed_at`` is put back to the floor (and reported), never stored
  as it came; derived rows of a signed backup must be non-negative and within what the
  engine can award (e.g. at most 1 000 XP per event).
* **Idempotent upserts by primary key in one transaction.** A missing row is inserted,
  a changed one updated, an identical one left alone — restoring twice changes
  nothing. A row whose id belongs to another account aborts the restore before
  anything is written to it, and so does a foreign key (after remapping) that points
  at another account's row: a backup row may only point at the target's rows (those
  of the backup restored before included) or at library rows — nothing of another
  user is ever touched or reachable. The caller owns the transaction (commit, or roll
  back for ``--dry-run``).
* **No server paths from a backup.** A meal keeps ``photo_thumb_path`` only when it is
  exactly the path this server builds for that meal of the target account and the
  file exists (a restore on the same server); otherwise the meal has no photo. Recipes
  have no thumbnails here: their path is never restored.
* **Natural keys.** When a backup row's one-per-day / per-name / per-kind key is held
  by another row of the account under a different id (the settings row of a new
  account, its default "Health" area, a day re-entered after a deletion), that row
  takes the backup's values and keeps its id, and the backup rows pointing at the
  backup's id are pointed at it.
* **Library rows.** Global foods, exercises, achievements and quests have their own
  ids on every server. A reference is kept when the id exists here, else matched by
  ``source``+``source_id`` (foods), English name (exercises) or ``code``
  (achievements, quests). A food or exercise found nowhere is recreated as the
  user's own custom row (same id, same values), never as a library row; an
  achievement or quest this server does not know is skipped with its rows.
* **Bounded reading.** Before any member is inflated its declared size and compression
  ratio are checked (``manifest.json`` ≤ 1 MiB, ``data.json`` ≤ ``BACKUP_MAX_DATA_BYTES``,
  512 MiB by default, and above 16 MiB at most 200× its compressed size); members are
  read in 1 MiB steps that stop at the limit even when a header lies. ``data.json`` is
  then held in memory (the export streams; a restore is an offline admin command).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import uuid
import zipfile
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy import and_, exists, func, literal_column, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Achievement, Exercise, Food, Quest, User
from app.schemas.appearance import AppearanceOut
from app.schemas.gamification import validate_xp_weights
from app.services import export, media
from app.services.export import (
    BACKUP_FORMAT,
    DATA_FILE,
    FORMAT,
    FORMAT_VERSION,
    LIBRARY,
    MANIFEST_FILE,
    SECTIONS,
    NaturalKey,
    Section,
)
from app.services.planner.common import advisory_lock
from app.services.settings_service import default_floor_for
from app.services.targets import ABSOLUTE_FLOOR_KCAL

MAX_BIND_PARAMS = 30_000  # PostgreSQL allows 65 535 per statement
MAX_BATCH = 500
LOCK_SCOPE = "gamification"  # the engine's per-user lock: no score run interleaves a restore
MAX_MANIFEST_BYTES = 1 << 20  # the real one is a few KiB
# deflated exports compress 5-20×; deflate cannot pass ~1 000×. Smaller members are harmless.
MAX_COMPRESSION_RATIO = 200
RATIO_CHECKED_FROM = 16 << 20
READ_STEP = 1 << 20


class RestoreError(Exception):
    """The backup cannot (or must not) be restored here; nothing was written."""


# --------------------------------------------------------------------------- #
# Reading and verifying the archive
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class Signature:
    """Whether this server made the backup: ``problem`` says why it does not count."""

    valid: bool
    problem: str | None = None


def check_signature(manifest: dict[str, Any]) -> Signature:
    raw = manifest.get("signature")
    if raw is None:
        return Signature(False, "it carries no signature")
    if not isinstance(raw, dict) or raw.get("alg") != export.SIGNATURE_ALG or not isinstance(raw.get("value"), str):
        return Signature(False, "its signature is not one this server knows")
    if raw.get("key_id") != export.signing_key_id():
        return Signature(False, "it was signed by another server, or before the signing key changed")
    if not hmac.compare_digest(raw["value"], export.manifest_signature(manifest)):
        return Signature(False, "its signature does not match: it was changed after it was made")
    return Signature(True)


@dataclass(slots=True)
class Backup:
    manifest: dict[str, Any]
    data: dict[str, Any]
    signature: Signature = field(default_factory=lambda: Signature(False, "it was not checked"))

    @property
    def signed(self) -> bool:
        return self.signature.valid

    @property
    def user_id(self) -> uuid.UUID:
        return uuid.UUID(str(self.manifest["user_id"]))

    @property
    def schema_version(self) -> str | None:
        return self.manifest.get("schema_version")

    @property
    def created_at(self) -> str | None:
        return self.manifest.get("created_at")

    def rows(self, name: str) -> list[dict[str, Any]]:
        return self.data["tables"].get(name) or []

    def library(self, name: str) -> list[dict[str, Any]]:
        return (self.data.get("library") or {}).get(name) or []


def _load_json(raw: bytes | bytearray, name: str) -> dict[str, Any]:
    try:
        value = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise RestoreError(f"{name} is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise RestoreError(f"{name} must hold a JSON object")
    return value


def _member(archive: zipfile.ZipFile, name: str, limit: int) -> zipfile.ZipInfo:
    """A member's header, checked before a byte of it is inflated: its declared size
    and its compression ratio (a few KiB must not become gigabytes)."""
    info = archive.getinfo(name)
    if info.file_size > limit:
        raise RestoreError(
            f"{name} would unpack to {info.file_size:,} bytes, more than the {limit:,} a backup may hold"
            + (" (BACKUP_MAX_DATA_BYTES)" if name == DATA_FILE else "")
        )
    if info.file_size > RATIO_CHECKED_FROM and info.file_size > MAX_COMPRESSION_RATIO * max(info.compress_size, 1):
        raise RestoreError(
            f"{name} is compressed {info.file_size // max(info.compress_size, 1):,}× — far more than any export "
            "(a zip bomb?): refused before unpacking it"
        )
    return info


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int, digest: Any = None) -> bytearray:
    """Inflate a member step by step, never past ``limit`` bytes (whatever its header says)."""
    out = bytearray()
    with archive.open(info) as handle:
        while chunk := handle.read(READ_STEP):
            if len(out) + len(chunk) > limit:
                raise RestoreError(f"{info.filename} unpacks to more than {limit:,} bytes — refused")
            out += chunk
            if digest is not None:
                digest.update(chunk)
    return out


def read_backup(path: str | os.PathLike[str]) -> Backup:
    """Open and verify a backup zip (sizes, checksum, formats, account, schema, counts)."""
    limit = get_settings().backup_max_data_bytes
    try:
        on_disk = os.path.getsize(path)
    except FileNotFoundError:
        raise RestoreError(f"no such file: {path}") from None
    except OSError as exc:
        raise RestoreError(f"not a readable zip archive: {exc}") from None
    if on_disk > limit + MAX_MANIFEST_BYTES + READ_STEP:  # an archive is never larger than its members + headers
        raise RestoreError(f"the archive is {on_disk:,} bytes, larger than any backup may be (BACKUP_MAX_DATA_BYTES)")
    try:
        archive = zipfile.ZipFile(path)
    except FileNotFoundError:
        raise RestoreError(f"no such file: {path}") from None
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"not a readable zip archive: {exc}") from None
    try:
        with archive:
            names = set(archive.namelist())
            for required in (MANIFEST_FILE, DATA_FILE):
                if required not in names:
                    raise RestoreError(f"the archive has no {required} — is it a DisciplineOS backup?")
            raw_manifest = _read(archive, _member(archive, MANIFEST_FILE, MAX_MANIFEST_BYTES), MAX_MANIFEST_BYTES)
            manifest = _load_json(raw_manifest, MANIFEST_FILE)
            if manifest.get("format") != BACKUP_FORMAT:
                raise RestoreError(f"{MANIFEST_FILE}: unknown format {manifest.get('format')!r}")
            if manifest.get("format_version") != FORMAT_VERSION:
                raise RestoreError(f"{MANIFEST_FILE}: unsupported format version {manifest.get('format_version')!r}")
            files = manifest.get("files")
            if not isinstance(files, dict) or not isinstance(files.get(DATA_FILE), dict):
                raise RestoreError(f"{MANIFEST_FILE}: no checksum for {DATA_FILE}")
            if set(files) != {DATA_FILE}:
                raise RestoreError(
                    f"{MANIFEST_FILE}: lists files a backup does not have: {sorted(set(files) - {DATA_FILE})}"
                )
            digest = hashlib.sha256()
            raw = _read(archive, _member(archive, DATA_FILE, limit), limit, digest)
            if digest.hexdigest() != files[DATA_FILE].get("sha256") or len(raw) != files[DATA_FILE].get("bytes"):
                raise RestoreError(f"{DATA_FILE}: checksum mismatch — the file is damaged or was changed")
            data = _load_json(raw, DATA_FILE)
            del raw
    except zipfile.BadZipFile as exc:  # a CRC error while reading
        raise RestoreError(f"the archive is damaged: {exc}") from None
    except (zlib.error, EOFError, NotImplementedError, RuntimeError) as exc:  # corrupt, cut, unsupported, encrypted
        raise RestoreError(f"the archive cannot be read: {exc}") from None
    _check_data(manifest, data)
    return Backup(manifest=manifest, data=data, signature=check_signature(manifest))


def _check_data(manifest: dict[str, Any], data: dict[str, Any]) -> None:
    try:
        uuid.UUID(str(manifest.get("user_id")))
    except ValueError:
        raise RestoreError(f"{MANIFEST_FILE}: bad user_id") from None
    if data.get("format") != FORMAT or data.get("format_version") != FORMAT_VERSION:
        raise RestoreError(f"{DATA_FILE}: unknown format")
    if str(data.get("user_id")) != str(manifest.get("user_id")):
        raise RestoreError(f"{DATA_FILE} and {MANIFEST_FILE} name different accounts")
    if data.get("schema_version") != manifest.get("schema_version"):
        raise RestoreError(f"{DATA_FILE} and {MANIFEST_FILE} name different schema versions")
    tables, library = data.get("tables"), data.get("library")
    if not isinstance(tables, dict) or not isinstance(library, dict):
        raise RestoreError(f"{DATA_FILE}: tables or library missing")
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    for section in SECTIONS:
        rows = tables.get(section.name)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RestoreError(f"{DATA_FILE}: table {section.name} is missing or malformed")
        if counts.get(section.name) != len(rows):
            raise RestoreError(f"{section.name}: {len(rows)} rows, the manifest says {counts.get(section.name)}")
    for item in LIBRARY:
        rows = library.get(item.name)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RestoreError(f"{DATA_FILE}: library {item.name} is missing or malformed")
        if counts.get(f"library.{item.name}") != len(rows):
            raise RestoreError(f"library {item.name}: the row count does not match the manifest")


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class TableReport:
    rows: int = 0  # rows of this table in the backup (library copies included)
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    merged: int = 0  # matched an existing row of the account by its natural key
    skipped: int = 0  # refer to a library row this server does not have
    rebuilt: int = 0  # derived rows of a backup not signed here: left for the engine to rebuild

    @property
    def changed(self) -> int:
        return self.inserted + self.updated


@dataclass(slots=True)
class LibraryReport:
    referenced: int = 0
    same_id: int = 0
    matched: int = 0  # same food/exercise/achievement/quest under another id here
    copied: int = 0  # recreated as the user's own row
    missing: int = 0  # unknown here: the rows referring to it are skipped


@dataclass(slots=True)
class RestoreReport:
    source_user_id: uuid.UUID
    target_user_id: uuid.UUID
    schema_version: str | None
    created_at: str | None
    dry_run: bool = False
    signed: bool = False
    signature_problem: str | None = None
    tables: dict[str, TableReport] = field(default_factory=dict)
    library: dict[str, LibraryReport] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # values put back to what the app allows

    @property
    def inserted(self) -> int:
        return sum(item.inserted for item in self.tables.values())

    @property
    def updated(self) -> int:
        return sum(item.updated for item in self.tables.values())

    @property
    def unchanged(self) -> int:
        return sum(item.unchanged for item in self.tables.values())

    @property
    def skipped(self) -> int:
        return sum(item.skipped for item in self.tables.values())

    @property
    def rebuilt(self) -> int:
        return sum(item.rebuilt for item in self.tables.values())


# --------------------------------------------------------------------------- #
# Values: JSON → Python, strictly by column type
# --------------------------------------------------------------------------- #
def _instant(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("an integer is required")
    return value


def _real(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("a number is required")
    return float(value)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("a number is required")
    try:
        return Decimal(str(value))
    except InvalidOperation:  # pragma: no cover - str() of a finite number always parses
        raise ValueError("a number is required") from None


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("true or false is required")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("text is required")
    return value


def _decoder(column: sa.Column) -> Callable[[Any], Any]:
    kind = column.type
    convert: Callable[[Any], Any]
    if isinstance(kind, sa.Uuid):
        convert = lambda value: uuid.UUID(_text(value))  # noqa: E731
    elif isinstance(kind, sa.DateTime):
        convert = _instant
    elif isinstance(kind, sa.Date):
        convert = lambda value: date.fromisoformat(_text(value))  # noqa: E731
    elif isinstance(kind, sa.Time):
        convert = lambda value: time.fromisoformat(_text(value))  # noqa: E731
    elif isinstance(kind, sa.Float):
        convert = _real
    elif isinstance(kind, sa.Numeric):
        convert = _decimal
    elif isinstance(kind, sa.Boolean):
        convert = _boolean
    elif isinstance(kind, sa.Integer):
        convert = _integer
    elif isinstance(kind, sa.Enum):
        allowed = frozenset(kind.enums)

        def convert(value: Any) -> str:
            if value not in allowed:
                raise ValueError(f"one of {sorted(allowed)} is required")
            return value

    elif isinstance(kind, sa.JSON):
        return _json_decoder(column)
    elif isinstance(kind, sa.String):
        convert = _text
    else:  # pragma: no cover - every exported column type is listed above
        return lambda value: value
    return lambda value: None if value is None else convert(value)


def _json_decoder(column: sa.Column) -> Callable[[Any], Any]:
    """A JSON column holds the kind of value its default has (``'{}'`` an object, ``'[]'``
    an array) and is never JSON ``null`` where the column is NOT NULL (readers rely on
    both: ``row.tags`` is a list of text, ``payload.get(…)`` needs an object)."""
    default = str(column.server_default.arg) if column.server_default is not None else ""  # e.g. '{}'::jsonb
    container: type | None = dict if default.startswith("'{") else list if default.startswith("'[") else None
    nullable = bool(column.nullable)

    def convert(value: Any) -> Any:
        if value is None:
            if nullable:
                return None
            raise ValueError("a value is required, not null")
        if container is not None and not isinstance(value, container):
            raise ValueError("a JSON object is required" if container is dict else "a JSON array is required")
        return value

    return convert


# --------------------------------------------------------------------------- #
# JSON shapes: what the app's readers rely on (a restored value never 500s a page)
# --------------------------------------------------------------------------- #
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _number(value: Any, low: float, high: float) -> bool:
    return (
        not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value) and low <= value <= high
    )


def _texts(value: list[Any]) -> None:
    if not all(isinstance(item, str) for item in value):
        raise ValueError("every item must be text")


def _objects(value: list[Any]) -> None:
    if not all(isinstance(item, dict) for item in value):
        raise ValueError("every item must be an object")


def _days(value: list[Any]) -> None:
    for item in value:
        if not isinstance(item, str):
            raise ValueError("every item must be a YYYY-MM-DD day")
        date.fromisoformat(item)


def _score_components(value: dict[str, Any]) -> None:
    """``daily_scores.components`` as the score service writes it (extra keys are fine)."""
    for flag in ("provisional", "credit", "final"):
        if flag in value and not isinstance(value[flag], bool):
            raise ValueError(f"{flag} must be true or false")
    if "version" in value and not _number(value["version"], 0, 10**6):
        raise ValueError("version must be a number")
    items = value.get("items", [])
    if not isinstance(items, list):
        raise ValueError("items must be an array")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise ValueError("every item must be an object with a text key")
        for name, high in (("value", 1), ("weight", 100)):
            if item.get(name) is not None and not _number(item[name], 0, high):
                raise ValueError(f"items[{item['key']}].{name} must be a number in 0..{high}")
        if item.get("excluded") is not None and not isinstance(item["excluded"], bool):
            raise ValueError(f"items[{item['key']}].excluded must be true or false")
        if item.get("detail") is not None and not isinstance(item["detail"], dict):
            raise ValueError(f"items[{item['key']}].detail must be an object")


def _score_weights(value: dict[str, Any]) -> None:
    for key, weight in value.items():
        if not _number(weight, 0, 100):
            raise ValueError(f"{key} must be a number in 0..100")


def _reminder_payload(value: dict[str, Any]) -> None:
    """A reminder's definition: ``{"lead_minutes": n}`` or ``{"date": …, "time": "HH:MM"}``."""
    lead = value.get("lead_minutes")
    if "lead_minutes" in value and not (isinstance(lead, int) and _number(lead, 0, 10_080)):
        raise ValueError("lead_minutes must be a whole number of minutes in 0..10080")
    if "date" in value:
        if not isinstance(value["date"], str):
            raise ValueError("date must be a YYYY-MM-DD day")
        date.fromisoformat(value["date"])
    if "time" in value and not (isinstance(value["time"], str) and HHMM.fullmatch(value["time"])):
        raise ValueError("time must be HH:MM")


SHAPES: dict[str, dict[str, Callable[[Any], None]]] = {
    "foods": {"aliases": _texts},
    "saved_meal_templates": {"items": _objects},
    "exercises": {"aliases": _texts, "secondary_muscles": _texts},
    "resources": {"tags": _texts},
    "tasks": {"tags_snapshot": _texts},
    "journal_entries": {"tags": _texts},
    "reminders": {"payload": _reminder_payload},
    "streaks": {"freeze_used_dates": _days},
    "daily_scores": {"components": _score_components, "weights": _score_weights},
}


# --------------------------------------------------------------------------- #
# Values: the app's rules, re-applied (never trusted from a file)
# --------------------------------------------------------------------------- #
MAX_EVENT_POINTS = 1_000  # the largest award: the 365-day streak milestone (500) at the top multiplier (2)
MAX_DAY_XP = 100_000
MAX_COUNT = 100_000  # days (or weeks) a streak or a score can count
MAX_FREEZE_TOKENS = 2
BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    # the API's bounds (SettingsUpdateIn, TargetsIn), widened where the target engine or a
    # weigh-in may store more (auto targets of a heavy, very active body; weigh-ins 20–400 kg)
    "settings": {
        "week_start": (0, 6),
        "goal_rate_pct": (0, 30),
        "height_cm": (100, 250),
        "weight_kg": (20, 400),
        "body_fat_pct": (2, 70),
        "goal_weight_kg": (30, 300),
        "goal_protein_g_per_kg": (1, 3.5),
        "goal_fat_min_g_per_kg": (0.4, 2),
        "goal_water_ml_per_kg": (25, 45),
        "target_kcal": (ABSOLUTE_FLOOR_KCAL, 20_000),
        "target_protein_g": (0, 2_000),
        "target_fat_g": (0, 2_000),
        "target_carbs_g": (0, 5_000),
        "target_fiber_g": (0, 500),
        "target_water_ml": (0, 20_000),
        "target_steps": (0, 60_000),
        "safe_floor_kcal": (ABSOLUTE_FLOOR_KCAL, 3_500),
    },
    # derived rows (signed backups only): never negative, never more than the engine awards
    "xp_events": {"points": (0, MAX_EVENT_POINTS)},
    "daily_scores": {"score": (0, 100), "xp_earned": (0, MAX_DAY_XP), "streak_days": (0, MAX_COUNT)},
    "streaks": {
        "current_count": (0, MAX_COUNT),
        "longest_count": (0, MAX_COUNT),
        "total_events": (0, MAX_COUNT),
        "freeze_tokens": (0, MAX_FREEZE_TOKENS),
    },
    "user_achievements": {"progress": (0, 10**9)},
    "user_quests": {"progress": (0, 10**9)},
}


def _settings_rules(row: dict[str, Any], notes: list[str]) -> None:
    """The settings API's rules. A safe floor below the default for the user's sex, or a
    calorie target below the floor, is stored only behind an explicit confirmation
    (``safe_floor_confirmed_at``): without one it is put back to the floor — the safe
    side, reported — never stored as it came."""
    if row.get("units") != "metric":
        raise RestoreError(f"settings.units: {row.get('units')!r} — the app is metric only")
    try:
        if not isinstance(row.get("xp_weights"), dict):
            raise ValueError("an object is required")
        validate_xp_weights(row["xp_weights"])
    except ValueError as exc:
        raise RestoreError(f"settings.xp_weights: {exc}") from None
    if row.get("appearance") is not None:  # stage 10: the same object the API stores (unknown keys refused)
        try:
            AppearanceOut.model_validate(row["appearance"])
        except ValueError:
            raise RestoreError("settings.appearance: not an appearance object this app stores") from None
    confirmed = row.get("safe_floor_confirmed_at") is not None
    default = default_floor_for(SimpleNamespace(safe_floor_kcal=None, sex=row.get("sex")))  # type: ignore[arg-type]
    floor = row.get("safe_floor_kcal")
    if floor is not None and floor < default and not confirmed:
        notes.append(
            f"settings: a safe floor of {floor} kcal, below the default {default} kcal, was never confirmed — "
            "the default applies"
        )
        row["safe_floor_kcal"] = floor = None
    floor = floor or default
    target = row.get("target_kcal")
    if target is not None and target < floor and not confirmed:
        notes.append(
            f"settings: a calorie target of {target} kcal, below the safe floor {floor} kcal, was never confirmed — "
            f"set to {floor} kcal"
        )
        row["target_kcal"] = floor


def _quest_period(row: dict[str, Any], notes: list[str]) -> None:
    if row["period_end"] < row["period_start"]:
        raise RestoreError(f"user_quests: period {row['period_start']}..{row['period_end']} ends before it starts")


ROW_RULES: dict[str, Callable[[dict[str, Any], list[str]], None]] = {
    "settings": _settings_rules,
    "user_quests": _quest_period,
}


def _check_values(section: Section, row: dict[str, Any], plan: _Plan) -> None:
    for column, shape in SHAPES.get(section.name, {}).items():
        try:
            shape(row[column])
        except (TypeError, ValueError) as exc:
            raise RestoreError(f"{section.name}.{column}: {exc} — not a shape this app stores") from None
    for column, (low, high) in BOUNDS.get(section.name, {}).items():
        value = row.get(column)
        if value is not None and not low <= value <= high:
            raise RestoreError(
                f"{section.name}.{column}: {value} is outside {low:g}..{high:g} — not a value this app stores"
            )
    rule = ROW_RULES.get(section.name)
    if rule is not None:
        rule(row, plan.notes)


# --------------------------------------------------------------------------- #
# The restore
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _Plan:
    target: uuid.UUID
    ids: dict[str, dict[uuid.UUID, uuid.UUID]] = field(default_factory=lambda: defaultdict(dict))
    dropped: dict[str, set[uuid.UUID]] = field(default_factory=lambda: defaultdict(set))
    copies: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    notes: list[str] = field(default_factory=list)


async def restore(
    db: AsyncSession, backup: Backup, *, user_id: uuid.UUID | None = None, dry_run: bool = False
) -> RestoreReport:
    """Write the backup into the account (see the module docstring). The caller commits —
    or rolls back for a dry run; every check that can refuse runs before the first write."""
    current = await export.schema_version(db)
    if backup.schema_version != current:
        raise RestoreError(
            f"the backup has schema version {backup.schema_version}, this database {current}: "
            "restore it with the app version that made it"
        )
    source = backup.user_id
    if not backup.signed and user_id is None:  # the manifest's user_id never chooses an account
        raise RestoreError(
            f"this server did not sign the backup ({backup.signature.problem}): such a backup goes only into an "
            "empty account named with --user-id, and its XP, scores, streaks, achievements and quests are rebuilt "
            "from the restored records"
        )
    target = user_id or source
    if await db.scalar(select(User.id).where(User.id == target)) is None:
        owners = (await db.scalars(select(User.id).where(User.is_owner.is_(True)))).all()
        hint = f" (the owner here is {owners[0]})" if owners else " (create one with create-owner)"
        raise RestoreError(f"account {target} does not exist here: pass --user-id <account>{hint}")
    await advisory_lock(db, target, LOCK_SCOPE)
    if target != source or not backup.signed:
        held = await _records_held(db, target, backup)
        if held:
            raise RestoreError(
                f"account {target} is not empty ({', '.join(held[:5])}{'…' if len(held) > 5 else ''}): "
                + (
                    "a backup goes back into its own account or into an empty one"
                    if backup.signed
                    else f"a backup this server did not sign ({backup.signature.problem}) goes only into an empty one"
                )
            )
    from app.seeds.gamification import ensure_seeded

    await ensure_seeded(db)
    report = RestoreReport(
        source_user_id=source,
        target_user_id=target,
        schema_version=backup.schema_version,
        created_at=backup.created_at,
        dry_run=dry_run,
        signed=backup.signed,
        signature_problem=backup.signature.problem,
    )
    plan = _Plan(target=target)
    await _resolve_library(db, backup, plan, report)
    for section in SECTIONS:
        if not section.restore:
            continue
        rows = backup.rows(section.name) + plan.copies.get(section.name, [])
        if section.derived and not backup.signed:  # untrusted: the engine derives them from the records
            report.tables[section.name] = TableReport(rows=len(rows), rebuilt=len(rows))
            continue
        report.tables[section.name] = await _restore_section(db, section, rows, plan)
    report.notes = plan.notes
    if not backup.signed:
        report.notes.append(
            "XP, daily scores, streaks, achievements and quests were not taken from this unsigned backup: "
            "the gamification engine rebuilds them from the restored records on its next run"
        )
    await db.flush()
    return report


async def _records_held(db: AsyncSession, user_id: uuid.UUID, backup: Backup) -> list[str]:
    """Sections in which the account holds records of its own — for a signed backup,
    rows that are not rows of this very backup (an account that took this backup before
    may take it again: restoring twice changes nothing). For a backup this server did
    not sign every record counts: whoever made it may know the account's row ids, so
    only a truly empty account takes it (running it twice is refused the second time).
    One query, then the ids of the sections that have rows at all."""
    checks = []
    candidates: dict[str, Section] = {}
    for section in SECTIONS:
        if not section.restore or section.in_empty_account:
            continue
        candidates[section.name] = section
        checks.append(exists().where(_owned_by(section, user_id)).label(section.name))
    row = (await db.execute(select(*checks))).one()
    held = []
    for name, has_rows in row._mapping.items():
        if not has_rows:
            continue
        if not backup.signed:
            held.append(name)
            continue
        section = candidates[name]
        stmt = select(*section.primary_key).where(_owned_by(section, user_id))
        ours = {tuple(str(value) for value in found) for found in (await db.execute(stmt)).all()}
        if not ours <= _backup_keys(backup, section):
            held.append(name)
    return held


def _owned_by(section: Section, user_id: uuid.UUID) -> Any:
    table = section.table
    condition = table.c[section.owner] == user_id
    return and_(condition, section.where(table)) if section.where is not None else condition


def _backup_keys(backup: Backup, section: Section) -> set[tuple[str, ...]]:
    """Primary keys (as text) of a section's rows in the backup — for foods and exercises
    the library rows too: a restore recreates the ones a server lacks as your own."""
    names = [column.name for column in section.primary_key]
    rows = backup.rows(section.name)
    if section.name in {item.name for item in LIBRARY}:
        rows = rows + backup.library(section.name)
    return {tuple(str(row.get(name)) for name in names) for row in rows}


# --------------------------------------------------------------------------- #
# Library references
# --------------------------------------------------------------------------- #
def _uuid(value: Any, where: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise RestoreError(f"{where}: bad id {value!r}") from None


async def _resolve_library(db: AsyncSession, backup: Backup, plan: _Plan, report: RestoreReport) -> None:
    for name, model in (("achievements", Achievement), ("quests", Quest)):
        rows = backup.library(name)
        item = report.library.setdefault(name, LibraryReport(referenced=len(rows)))
        ids = {_uuid(row.get("id"), f"library {name}"): str(row.get("code")) for row in rows}
        here = set((await db.scalars(select(model.id).where(model.id.in_(list(ids))))).all()) if ids else set()
        wanted = {code: row_id for row_id, code in ids.items() if row_id not in here}
        by_code = (
            {
                code: row_id
                for row_id, code in (
                    await db.execute(select(model.id, model.code).where(model.code.in_(list(wanted))))
                ).all()
            }
            if wanted
            else {}
        )
        for row_id, code in ids.items():
            if row_id in here:
                item.same_id += 1
            elif code in by_code:
                plan.ids[model.__tablename__][row_id] = by_code[code]
                item.matched += 1
            else:
                plan.dropped[model.__tablename__].add(row_id)
                item.missing += 1

    await _resolve_owned_library(
        db, backup, plan, report, "exercises", Exercise, lambda row: (row.get("name_en") or "").strip() or None
    )
    await _resolve_owned_library(
        db,
        backup,
        plan,
        report,
        "foods",
        Food,
        lambda row: (row.get("source"), row.get("source_id")) if row.get("source_id") else None,
    )


async def _resolve_owned_library(
    db: AsyncSession,
    backup: Backup,
    plan: _Plan,
    report: RestoreReport,
    name: str,
    model: Any,
    natural: Callable[[dict[str, Any]], Any],
) -> None:
    """Foods/exercises: the same id here (a library row, or one of the target's own),
    else the library row with the same natural key, else a custom copy for the user."""
    rows = backup.library(name)
    item = report.library.setdefault(name, LibraryReport(referenced=len(rows)))
    if not rows:
        return
    ids = [_uuid(row.get("id"), f"library {name}") for row in rows]
    found = {
        row_id: (is_global, owner)
        for row_id, is_global, owner in (
            await db.execute(select(model.id, model.is_global, model.user_id).where(model.id.in_(ids)))
        ).all()
    }
    keys = {natural(row) for row in rows} - {None}
    by_key: dict[Any, uuid.UUID] = {}
    if keys and name == "exercises":
        stmt = select(model.name_en, model.id).where(model.is_global.is_(True), model.name_en.in_(list(keys)))
        by_key = dict((await db.execute(stmt)).all())
    elif keys:
        stmt = select(model.source, model.source_id, model.id).where(
            model.is_global.is_(True), tuple_(model.source, model.source_id).in_(list(keys))
        )
        by_key = {
            (getattr(source, "value", source), source_id): row_id
            for source, source_id, row_id in (await db.execute(stmt)).all()
        }
    for row, row_id in zip(rows, ids, strict=True):
        if row_id in found:
            is_global, owner = found[row_id]
            if not is_global and owner != plan.target:
                raise RestoreError(f"library {name} {row_id} belongs to another account here")
            item.same_id += 1
        elif natural(row) in by_key:
            plan.ids[model.__tablename__][row_id] = by_key[natural(row)]
            item.matched += 1
        else:
            plan.copies[name].append({**row, "user_id": str(plan.target), "is_global": False})
            item.copied += 1


# --------------------------------------------------------------------------- #
# One table
# --------------------------------------------------------------------------- #
def _foreign_keys(section: Section) -> dict[str, str]:
    """Exported column → the table it references (the owner column excluded)."""
    out: dict[str, str] = {}
    for column in section.columns:
        if column.name == section.owner:
            continue
        for key in column.foreign_keys:
            out[column.name] = key.column.table.name
    return out


def _decode(section: Section, raw: dict[str, Any], decoders: dict[str, Callable[[Any], Any]]) -> dict[str, Any]:
    if set(raw) != set(decoders):
        missing, extra = sorted(set(decoders) - set(raw)), sorted(set(raw) - set(decoders))
        raise RestoreError(f"{section.name}: a row does not match this schema (missing {missing}, unknown {extra})")
    row: dict[str, Any] = {}
    for name, decode in decoders.items():
        try:
            row[name] = decode(raw[name])
        except (TypeError, ValueError) as exc:
            shown = repr(raw[name])
            shown = shown if len(shown) <= 120 else shown[:117] + "…"
            raise RestoreError(f"{section.name}.{name}: bad value {shown} ({exc})") from None
    return row


def _levels(rows: list[dict[str, Any]], pk: str, self_refs: list[str]) -> list[list[dict[str, Any]]]:
    """A self-referencing table (tasks → parent, series root) in dependency levels:
    every row comes after the rows of the backup it points at."""
    if not self_refs:
        return [rows]
    by_id = {row[pk]: row for row in rows}
    waiting = {
        row[pk]: {row[column] for column in self_refs if row.get(column) in by_id and row[column] != row[pk]}
        for row in rows
    }
    placed: set[uuid.UUID] = set()
    levels: list[list[dict[str, Any]]] = []
    while waiting:
        ready = [row_id for row_id, parents in waiting.items() if parents <= placed]
        if not ready:  # a cycle cannot pass the foreign keys anyway; let the database say so
            ready = list(waiting)
        levels.append([by_id[row_id] for row_id in ready])
        placed.update(ready)
        for row_id in ready:
            del waiting[row_id]
    return levels


def _remap(row: dict[str, Any], foreign: dict[str, str], plan: _Plan) -> None:
    for column, target_table in foreign.items():
        value = row.get(column)
        if value is not None:
            mapped = plan.ids.get(target_table, {}).get(value)
            if mapped is not None:
                row[column] = mapped


def _chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


async def _restore_section(
    db: AsyncSession, section: Section, raw_rows: list[dict[str, Any]], plan: _Plan
) -> TableReport:
    report = TableReport(rows=len(raw_rows))
    if not raw_rows:
        return report
    table = section.table
    columns = section.columns
    decoders = {column.name: _decoder(column) for column in columns}
    foreign = _foreign_keys(section)
    pk = [column.name for column in section.primary_key]
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = _decode(section, raw, decoders)
        _check_values(section, row, plan)
        row[section.owner] = plan.target
        if any(row.get(column) in plan.dropped.get(ref, ()) for column, ref in foreign.items()):
            report.skipped += 1
            continue
        rows.append(row)
    single = pk[0] if len(pk) == 1 else None
    backup_ids = {row[single] for row in rows} if single else set()
    self_refs = [column for column, ref in foreign.items() if ref == table.name]
    size = max(1, min(MAX_BATCH, MAX_BIND_PARAMS // max(1, len(columns))))
    for level in _levels(rows, single or "", self_refs) if single else [rows]:
        for batch in _chunks(level, size):
            for row in batch:
                _remap(row, foreign, plan)
            owners = await _owners(db, section, batch)
            foreign_owned = [key for key, owner in owners.items() if owner != plan.target]
            if foreign_owned:
                raise RestoreError(
                    f"{section.name}: row {foreign_owned[0][0] if len(foreign_owned[0]) == 1 else foreign_owned[0]} "
                    "belongs to another account here — nothing was restored"
                )
            if single and section.natural_keys:
                report.merged += await _merge_natural_keys(db, section, batch, owners, backup_ids, plan)
                for row in batch:
                    _remap(row, foreign, plan)  # a parent in this very batch may have been merged
            await _check_references(db, section, batch, plan.target)
            _server_paths(section, batch, plan.target)
            inserted, updated = await _upsert(db, section, batch, plan.target)
            report.inserted += inserted
            report.updated += updated
            report.unchanged += len(batch) - inserted - updated
    return report


async def _check_references(db: AsyncSession, section: Section, batch: list[dict[str, Any]], target: uuid.UUID) -> None:
    """Every foreign key of these rows (remapped) points at a row of the target account
    — rows of this backup restored before are the target's by now — or at a library row
    (``user_id`` NULL, global foods/exercises), or at nothing in this database (the
    foreign key itself then refuses the row). A row of another account aborts the
    restore before anything is written. One query per column and batch."""
    for column in section.columns:
        if column.name == section.owner:
            continue
        for key in column.foreign_keys:
            referenced = key.column.table
            owner = referenced.c.get("user_id")
            if owner is None:  # achievements: library rows only
                continue
            values = {row[column.name] for row in batch if row.get(column.name) is not None}
            if not values:
                continue
            if "is_global" in referenced.c:
                theirs = and_(referenced.c.is_global.is_(False), or_(owner.is_(None), owner != target))
            else:
                theirs = and_(owner.is_not(None), owner != target)
            found = await db.scalar(select(key.column).where(key.column.in_(values), theirs).limit(1))
            if found is not None:
                raise RestoreError(
                    f"{section.name}.{column.name}: {referenced.name} {found} belongs to another account here "
                    "— nothing was restored"
                )


# Columns holding a path on this server's disk: never taken from a backup. A meal keeps its
# thumbnail only when the path is exactly the one built for that meal of the target account
# and the file is here (a restore on the same server); a recipe has no thumbnails at all.
SERVER_PATHS: dict[str, dict[str, Callable[[uuid.UUID, dict[str, Any]], str | None]]] = {
    "meal_logs": {"photo_thumb_path": lambda target, row: media.meal_thumb_relpath(target, row["id"])},
    "recipes": {"photo_thumb_path": lambda target, row: None},
}


def _server_paths(section: Section, batch: list[dict[str, Any]], target: uuid.UUID) -> None:
    for column, built in SERVER_PATHS.get(section.name, {}).items():
        for row in batch:
            path = row.get(column)
            if path is None:
                continue
            expected = built(target, row)
            row[column] = path if expected is not None and path == expected and media.path_of(path) else None


async def _owners(db: AsyncSession, section: Section, batch: list[dict[str, Any]]) -> dict[tuple, uuid.UUID]:
    table = section.table
    pk_columns = section.primary_key
    keys = [tuple(row[column.name] for column in pk_columns) for row in batch]
    stmt = select(*pk_columns, table.c[section.owner]).where(tuple_(*pk_columns).in_(keys))
    return {tuple(found[:-1]): found[-1] for found in (await db.execute(stmt)).all()}


def _key_of(row: dict[str, Any], key: NaturalKey) -> tuple:
    values = tuple(row.get(column) for column in key.columns)
    if key.lower:
        values = tuple(value.lower() if isinstance(value, str) else value for value in values)
    return values


async def _merge_natural_keys(
    db: AsyncSession,
    section: Section,
    batch: list[dict[str, Any]],
    owners: dict[tuple, uuid.UUID],
    backup_ids: set[uuid.UUID],
    plan: _Plan,
) -> int:
    """Point new backup rows whose natural key another row of the account already holds
    at that row (it takes the backup's values, keeps its id). Rows that are themselves
    in the backup are never taken over (that would lose one of the two)."""
    table = section.table
    pk = section.primary_key[0].name
    merged = 0
    for key in section.natural_keys:
        candidates = {}
        for row in batch:
            if (row[pk],) in owners or (key.when is not None and not key.when(row)):
                continue
            candidates.setdefault(_key_of(row, key), row)
        if not candidates:
            continue
        expressions = [func.lower(table.c[column]) if key.lower else table.c[column] for column in key.columns]
        stmt = select(table.c[pk], *expressions).where(table.c[section.owner] == plan.target)
        if key.where is not None:
            stmt = stmt.where(key.where(table))
        if expressions:
            stmt = stmt.where(tuple_(*expressions).in_(list(candidates)))
        for found in (await db.execute(stmt)).all():
            twin, value = found[0], tuple(getattr(item, "value", item) for item in found[1:])  # enums → values
            row = candidates.get(value)
            if row is None or twin in backup_ids or (twin,) in owners:
                continue
            plan.ids[table.name][row[pk]] = twin
            row[pk] = twin
            owners[(twin,)] = plan.target
            merged += 1
    return merged


async def _upsert(
    db: AsyncSession, section: Section, batch: list[dict[str, Any]], target: uuid.UUID
) -> tuple[int, int]:
    """``INSERT … ON CONFLICT (pk) DO UPDATE`` only for the target's rows whose values
    differ; returns ``(inserted, updated)`` (``xmax = 0`` marks an insert)."""
    table = section.table
    pk = [column.name for column in section.primary_key]
    names = [column.name for column in section.columns]
    settable = [name for name in names if name not in pk]
    stmt = pg_insert(table).values(batch)
    changed = tuple_(*(table.c[name] for name in settable)).is_distinct_from(
        tuple_(*(stmt.excluded[name] for name in settable))
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=pk,
        set_={name: stmt.excluded[name] for name in settable},
        where=and_(table.c[section.owner] == target, changed),
    ).returning(literal_column("xmax = 0", type_=sa.Boolean))
    try:
        flags = (await db.execute(stmt)).scalars().all()
    except sa.exc.DBAPIError as exc:
        detail = str(getattr(exc, "orig", exc)).splitlines()[0]
        raise RestoreError(f"{section.name}: the database refused the rows ({detail}) — nothing was restored") from None
    inserted = sum(1 for flag in flags if flag)
    return inserted, len(flags) - inserted
