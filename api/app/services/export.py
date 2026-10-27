"""Data export (stage 8): ``/export/json``, ``/export/csv`` and ``/export/backup``.

What leaves the server
----------------------
**Every row the user owns**, table by table (:data:`SECTIONS`): settings, custom
foods, favourites and recents, recipes, meals with their items, water, saved meals,
custom exercises, workout templates, the weekly plan, workouts with their sets,
records, weigh-ins, sleep, Health days, the PARA planner (areas, projects,
resources, tags, tasks, time, journal, reminders), the gamification ledger (XP,
achievements, streaks, scores, quests) and the account's own records (the user row,
devices, API tokens, auth events, push subscriptions, notifications, AI calls).
The library rows the user's data points at (global foods, exercises, achievements,
quests) are listed under ``library`` so a backup can be restored on another server.

**Never a secret**: token hashes and prefixes, device fingerprints, the recovery
phrase hash, pairing codes (the table is not exported at all), push endpoints and
keys, raw AI request/response payloads (they can hold photos), an AI provider's error
body (``ai_jobs.error`` leaves as the status and the provider's error code only —
:func:`app.ai.providers.public_error`, also for rows stored before that rule), raw
provider payloads of library foods. VAPID keys live in the environment and are never
read here.
Columns maintained by database triggers (search vectors, ``aliases_text``) are left
out too — they are rebuilt on restore.

How it leaves
-------------
* **Streamed.** Rows are read with a server-side cursor in batches and written out as
  they come; the zip archives are produced entry by entry through a sink that is
  drained after every write (data descriptors, no seeking), so neither a big account
  nor its archive is ever held in memory.
* **One consistent snapshot.** The whole export runs in one ``REPEATABLE READ``,
  read-only transaction of its own: a write that lands mid-export is either entirely
  in it or not at all.
* **Formats.** JSON is UTF-8, one row per line, timestamps ISO-8601 in UTC, dates
  ``YYYY-MM-DD``, decimals as numbers. CSV is UTF-8 with a BOM (Excel), comma
  separated, ISO dates, JSON cells for JSON columns; a text cell that would start a
  spreadsheet formula (``= + - @``) is prefixed with ``'``. The backup is
  ``data.json`` (exactly the JSON export) plus ``manifest.json`` (format, app and
  schema version = the database's alembic revision, user id, created_at, row counts,
  sha256 and size per file); ``python -m app.cli restore-backup`` reads it back
  (``app.services.restore``).
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy import and_, select, text, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import public_error
from app.core.config import get_settings
from app.core.tz import get_zone
from app.models import (
    Achievement,
    AiJob,
    AiUsageDaily,
    ApiToken,
    Area,
    AuthEvent,
    BodyMetric,
    DailyScore,
    Device,
    Exercise,
    Food,
    FoodFavorite,
    FoodRecent,
    HealthDaily,
    JournalEntry,
    MealItem,
    MealLog,
    NotificationLog,
    PersonalRecord,
    Project,
    PushSubscription,
    Quest,
    Recipe,
    RecipeItem,
    Reminder,
    Resource,
    SavedMealTemplate,
    SleepLog,
    Streak,
    Tag,
    Task,
    TimeEntry,
    User,
    UserAchievement,
    UserQuest,
    UserSettings,
    WaterLog,
    WeeklyPlan,
    WorkoutLog,
    WorkoutSet,
    WorkoutTemplate,
    WorkoutTemplateItem,
    XpEvent,
    task_tags,
)

FORMAT = "disciplineos-export"
BACKUP_FORMAT = "disciplineos-backup"
FORMAT_VERSION = 1
DATA_FILE = "data.json"
MANIFEST_FILE = "manifest.json"
BATCH = 1000  # rows per server-side cursor fetch
FLUSH_BYTES = 256 * 1024  # hand a chunk to the client about every 256 KiB
CSV_BOM = "﻿"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
# trigger-maintained columns: rebuilt by the database on restore, noise in an export
DERIVED_COLUMNS = frozenset({"aliases_text", "search_vector_ru", "search_vector_en"})


# --------------------------------------------------------------------------- #
# What is exported
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class NaturalKey:
    """A unique key of a table besides its primary key, always together with the
    owner's ``user_id``: ``columns`` (``lower``: one text column compared
    case-insensitively) and, for a partial unique index, the condition — ``when`` for
    a row of the backup, ``where`` for the rows in the database. Restore uses it to
    find the row that already holds a backup row's key under another id."""

    columns: tuple[str, ...]
    lower: bool = False
    when: Callable[[dict[str, Any]], bool] | None = None
    where: Callable[[sa.Table], Any] | None = None


def _set(column: str) -> Callable[[dict[str, Any]], bool]:
    return lambda row: row.get(column) is not None


def _not_null(column: str) -> Callable[[sa.Table], Any]:
    return lambda table: table.c[column].is_not(None)


def _running(row: dict[str, Any]) -> bool:
    return row.get("ended_at") is None and not row.get("is_deleted")


def _running_sql(table: sa.Table) -> Any:
    return and_(table.c.ended_at.is_(None), table.c.is_deleted.is_(False))


CLIENT_UUID = NaturalKey(("client_uuid",))


@dataclass(frozen=True, slots=True)
class Section:
    """One table of the export. ``owner`` is the column holding the user's id;
    ``where`` narrows the rows further (custom foods/exercises only); ``exclude`` are
    secret or raw columns that never leave the server; ``sanitise`` maps a column to
    the function its value passes through on the way out; ``restore`` says whether
    ``restore-backup`` writes the section back (account records — devices, tokens,
    push subscriptions, logs — are not: they are bound to this server)."""

    name: str
    table: sa.Table
    owner: str = "user_id"
    where: Callable[[sa.Table], Any] | None = None
    exclude: frozenset[str] = frozenset()
    restore: bool = True
    # rows an otherwise "empty" account may already have: bootstrap defaults (areas,
    # tags), the settings row and what opening the app derives (streaks, scores, quests)
    in_empty_account: bool = False
    natural_keys: tuple[NaturalKey, ...] = ()
    sanitise: tuple[tuple[str, Callable[[Any], Any]], ...] = ()

    @property
    def columns(self) -> list[sa.Column]:
        return exported_columns(self.table, self.exclude)

    @property
    def sanitisers(self) -> dict[str, Callable[[Any], Any]]:
        return dict(self.sanitise)

    @property
    def primary_key(self) -> list[sa.Column]:
        return list(self.table.primary_key.columns)


def exported_columns(table: sa.Table, exclude: frozenset[str] = frozenset()) -> list[sa.Column]:
    """Every column but the secret/raw and trigger-maintained ones, primary key first."""
    columns = [
        column
        for column in table.columns
        if column.name not in exclude and column.name not in DERIVED_COLUMNS and not column.info.get("read_only")
    ]
    return sorted(columns, key=lambda column: (not column.primary_key, column.name != "user_id"))


def _custom(table: sa.Table) -> Any:
    return table.c.is_global.is_(False)


SECTIONS: tuple[Section, ...] = (
    # ---- account and settings -------------------------------------------------
    Section("user", User.__table__, owner="id", exclude=frozenset({"recovery_phrase_hash"}), restore=False),
    Section("settings", UserSettings.__table__, in_empty_account=True, natural_keys=(NaturalKey(()),)),
    # ---- nutrition ---------------------------------------------------------------
    Section(
        "foods",
        Food.__table__,
        where=_custom,
        exclude=frozenset({"source_payload"}),
        natural_keys=(NaturalKey(("barcode",), when=_set("barcode"), where=_not_null("barcode")),),
    ),
    Section("food_favorites", FoodFavorite.__table__, natural_keys=(NaturalKey(("food_id",)),)),
    Section("recipes", Recipe.__table__),
    Section("recipe_items", RecipeItem.__table__),
    Section(
        "food_recents",
        FoodRecent.__table__,
        natural_keys=(
            NaturalKey(("food_id",), when=_set("food_id"), where=_not_null("food_id")),
            NaturalKey(("recipe_id",), when=_set("recipe_id"), where=_not_null("recipe_id")),
        ),
    ),
    Section("saved_meal_templates", SavedMealTemplate.__table__),
    Section("meal_logs", MealLog.__table__, natural_keys=(CLIENT_UUID,)),
    Section("meal_items", MealItem.__table__),
    Section("water_logs", WaterLog.__table__, natural_keys=(CLIENT_UUID,)),
    # ---- training and health ---------------------------------------------------------
    Section("exercises", Exercise.__table__, where=_custom),
    Section("workout_templates", WorkoutTemplate.__table__),
    Section("workout_template_items", WorkoutTemplateItem.__table__),
    Section(
        "weekly_plan",
        WeeklyPlan.__table__,
        natural_keys=(
            NaturalKey(("weekday",), when=_set("weekday"), where=_not_null("weekday")),
            NaturalKey(("plan_date",), when=_set("plan_date"), where=_not_null("plan_date")),
        ),
    ),
    Section("workout_logs", WorkoutLog.__table__, natural_keys=(CLIENT_UUID,)),
    Section("workout_sets", WorkoutSet.__table__, natural_keys=(CLIENT_UUID,)),
    Section("personal_records", PersonalRecord.__table__),
    Section("body_metrics", BodyMetric.__table__, natural_keys=(NaturalKey(("measured_on",)), CLIENT_UUID)),
    Section("sleep_logs", SleepLog.__table__, natural_keys=(NaturalKey(("local_day",)), CLIENT_UUID)),
    Section("health_daily", HealthDaily.__table__, natural_keys=(NaturalKey(("local_day",)),)),
    # ---- planner ---------------------------------------------------------------------
    Section("areas", Area.__table__, in_empty_account=True, natural_keys=(NaturalKey(("name",), lower=True),)),
    Section(
        "projects",
        Project.__table__,
        natural_keys=(
            NaturalKey(("external_source", "external_id"), when=_set("external_id"), where=_not_null("external_id")),
        ),
    ),
    Section("resources", Resource.__table__),
    Section("tags", Tag.__table__, in_empty_account=True, natural_keys=(NaturalKey(("name",), lower=True),)),
    Section(
        "tasks",
        Task.__table__,
        natural_keys=(
            CLIENT_UUID,
            NaturalKey(
                ("recurrence_parent_id", "series_index"),
                when=_set("recurrence_parent_id"),
                where=_not_null("recurrence_parent_id"),
            ),
            NaturalKey(("external_source", "external_id"), when=_set("external_id"), where=_not_null("external_id")),
        ),
    ),
    Section("task_tags", task_tags),
    Section(
        "time_entries",
        TimeEntry.__table__,
        natural_keys=(CLIENT_UUID, NaturalKey((), when=_running, where=_running_sql)),
    ),
    Section("journal_entries", JournalEntry.__table__, natural_keys=(NaturalKey(("entry_date",)), CLIENT_UUID)),
    Section("reminders", Reminder.__table__, natural_keys=(NaturalKey(("dedupe_key",)),)),
    # ---- gamification (derived from the data above; an "empty" account may have them) --
    Section("xp_events", XpEvent.__table__, in_empty_account=True, natural_keys=(NaturalKey(("dedupe_key",)),)),
    Section(
        "user_achievements",
        UserAchievement.__table__,
        in_empty_account=True,
        natural_keys=(NaturalKey(("achievement_id",)),),
    ),
    Section("streaks", Streak.__table__, in_empty_account=True, natural_keys=(NaturalKey(("kind",)),)),
    Section("daily_scores", DailyScore.__table__, in_empty_account=True, natural_keys=(NaturalKey(("local_day",)),)),
    Section(
        "user_quests",
        UserQuest.__table__,
        in_empty_account=True,
        natural_keys=(NaturalKey(("quest_id", "period_start")),),
    ),
    # ---- account records (exported for you; bound to this server, never restored) ------
    Section(
        "devices",
        Device.__table__,
        exclude=frozenset({"token_hash", "token_prefix", "fingerprint_hash"}),
        restore=False,
    ),
    Section("api_tokens", ApiToken.__table__, exclude=frozenset({"token_hash", "token_prefix"}), restore=False),
    Section("auth_events", AuthEvent.__table__, restore=False),
    Section(
        "push_subscriptions",
        PushSubscription.__table__,
        exclude=frozenset({"endpoint", "p256dh", "auth"}),
        restore=False,
    ),
    Section("notifications_log", NotificationLog.__table__, restore=False),
    Section(
        "ai_jobs",
        AiJob.__table__,
        exclude=frozenset({"request_payload", "response_payload", "input_thumb_path"}),
        restore=False,
        sanitise=(("error", public_error),),  # never a provider's error body (rows stored before too)
    ),
    Section("ai_usage_daily", AiUsageDaily.__table__, restore=False),
)
SECTION_BY_NAME: dict[str, Section] = {section.name: section for section in SECTIONS}


@dataclass(frozen=True, slots=True)
class LibrarySection:
    """Global rows the user's data references (``foods`` are matched by id, then by
    ``source``/``source_id``; ``exercises`` by id, then by English name;
    ``achievements``/``quests`` by id, then by ``code``)."""

    name: str
    table: sa.Table
    columns: tuple[str, ...] | None  # None = every column but the excluded/derived ones
    exclude: frozenset[str] = frozenset()

    def selected(self) -> list[sa.Column]:
        if self.columns is not None:
            return [self.table.c[name] for name in self.columns]
        return exported_columns(self.table, self.exclude)


LIBRARY: tuple[LibrarySection, ...] = (
    LibrarySection("foods", Food.__table__, None, exclude=frozenset({"source_payload"})),
    LibrarySection("exercises", Exercise.__table__, None),
    LibrarySection("achievements", Achievement.__table__, ("id", "code", "name_ru", "name_en")),
    LibrarySection("quests", Quest.__table__, ("id", "code", "kind", "title_ru", "title_en")),
)


def _library_ids(name: str, user_id: uuid.UUID) -> Any:
    """The ids of the library rows of one kind that the user's rows reference."""
    if name == "foods":
        return union(
            select(MealItem.food_id).where(MealItem.user_id == user_id, MealItem.food_id.is_not(None)),
            select(RecipeItem.food_id).where(RecipeItem.user_id == user_id),
            select(FoodFavorite.food_id).where(FoodFavorite.user_id == user_id),
            select(FoodRecent.food_id).where(FoodRecent.user_id == user_id, FoodRecent.food_id.is_not(None)),
        )
    if name == "exercises":
        return union(
            select(WorkoutSet.exercise_id).where(WorkoutSet.user_id == user_id),
            select(WorkoutTemplateItem.exercise_id).where(WorkoutTemplateItem.user_id == user_id),
            select(PersonalRecord.exercise_id).where(PersonalRecord.user_id == user_id),
        )
    if name == "achievements":
        return select(UserAchievement.achievement_id).where(UserAchievement.user_id == user_id)
    if name == "quests":
        return select(UserQuest.quest_id).where(UserQuest.user_id == user_id)
    raise KeyError(name)  # pragma: no cover


def _library_select(section: LibrarySection, user_id: uuid.UUID) -> Any:
    table = section.table
    stmt = select(*section.selected()).where(table.c.id.in_(_library_ids(section.name, user_id)))
    if "is_global" in table.c:  # foods/exercises: custom rows are exported as the user's own section
        stmt = stmt.where(table.c.is_global.is_(True))
    return stmt.order_by(table.c.id)


def _section_select(section: Section, user_id: uuid.UUID) -> Any:
    table = section.table
    stmt = select(*section.columns).where(table.c[section.owner] == user_id)
    if section.where is not None:
        stmt = stmt.where(section.where(table))
    return stmt.order_by(*section.primary_key)


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
def _json_converter(column: sa.Column) -> Callable[[Any], Any] | None:
    """Per-column conversion before ``orjson``: instants in UTC, decimals as numbers
    (every restorable NUMERIC has ≤ 15 significant digits, so the float's shortest
    representation is the exact decimal)."""
    kind = column.type
    if isinstance(kind, sa.DateTime):
        return lambda value: value.astimezone(UTC) if isinstance(value, datetime) and value.tzinfo else value
    if isinstance(kind, sa.Numeric) and not isinstance(kind, sa.Float):
        return lambda value: float(value) if isinstance(value, Decimal) else value
    return None


def _then(first: Callable[[Any], Any] | None, second: Callable[[Any], Any] | None) -> Callable[[Any], Any] | None:
    """``second(first(value))``, either of them optional."""
    if first is None or second is None:
        return first or second
    return lambda value: second(first(value))


def _orjson_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def dumps(value: Any) -> bytes:
    return orjson.dumps(value, default=_orjson_default)


def csv_cell(value: Any) -> str:
    """One CSV cell: ISO dates and instants (UTC), plain decimals, ``true``/``false``,
    JSON for JSON columns, and no text that a spreadsheet would run as a formula."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return (value.astimezone(UTC) if value.tzinfo else value).isoformat()
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int | float | uuid.UUID):
        return str(value)
    if isinstance(value, dict | list):
        return orjson.dumps(value).decode()
    text_value = str(getattr(value, "value", value))
    if text_value.startswith(FORMULA_PREFIXES):
        return "'" + text_value
    return text_value


# --------------------------------------------------------------------------- #
# The export job
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ExportJob:
    user_id: uuid.UUID
    tz: str
    now: datetime  # aware UTC
    language: str = "ru"  # of the CSV export's README (the app's language)
    counts: dict[str, int] = field(default_factory=dict)
    schema_version: str | None = None

    @property
    def local_now(self) -> datetime:
        return self.now.astimezone(get_zone(self.tz))

    def filename(self, extension: str) -> str:
        return f"disciplineos-{self.local_now.date().isoformat()}.{extension}"


async def schema_version(db: AsyncSession) -> str | None:
    """The database's alembic revision (the schema the rows were written with)."""
    try:
        async with db.begin_nested():
            return await db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    except sa.exc.DBAPIError:  # pragma: no cover - a database without alembic
        return None


@asynccontextmanager
async def snapshot() -> AsyncIterator[AsyncSession]:
    """A session of its own in one ``REPEATABLE READ READ ONLY`` transaction."""
    from app.db import SessionLocal

    async with SessionLocal() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        try:
            yield db
        finally:
            await db.rollback()


async def _batches(db: AsyncSession, stmt: Any) -> AsyncIterator[list[Any]]:
    result = await db.stream(stmt.execution_options(yield_per=BATCH))
    async for rows in result.partitions():
        yield rows


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
async def json_chunks(db: AsyncSession, job: ExportJob) -> AsyncIterator[bytes]:
    """The JSON document, chunk by chunk; fills ``job.counts`` as it goes."""
    job.schema_version = job.schema_version or await schema_version(db)
    head = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": get_settings().api_version,
        "schema_version": job.schema_version,
        "exported_at": job.now.astimezone(UTC),
        "user_id": job.user_id,
        "timezone": job.tz,
    }
    yield b"{\n" + b",\n".join(dumps(key) + b": " + dumps(value) for key, value in head.items()) + b',\n"tables": {'
    for index, section in enumerate(SECTIONS):
        stmt = _section_select(section, job.user_id)
        async for chunk in _json_table(
            db, section.name, section.name, section.columns, stmt, job, first=index == 0, sanitise=section.sanitisers
        ):
            yield chunk
    yield b'\n},\n"library": {'
    for index, library in enumerate(LIBRARY):
        stmt = _library_select(library, job.user_id)
        count_key = f"library.{library.name}"
        async for chunk in _json_table(db, library.name, count_key, library.selected(), stmt, job, first=index == 0):
            yield chunk
    yield b'\n},\n"counts": ' + dumps(job.counts) + b"\n}\n"


async def _json_table(
    db: AsyncSession,
    key: str,
    count_key: str,
    columns: list[sa.Column],
    stmt: Any,
    job: ExportJob,
    *,
    first: bool,
    sanitise: dict[str, Callable[[Any], Any]] | None = None,
) -> AsyncIterator[bytes]:
    """``"key": [ rows… ]`` (preceded by a comma unless it is the first member)."""
    names = [str(column.name) for column in columns]  # plain str: orjson refuses subclasses
    sanitise = sanitise or {}
    converters = [_then(sanitise.get(column.name), _json_converter(column)) for column in columns]
    buffer = bytearray((b"" if first else b",") + b"\n" + dumps(key) + b": [")
    count = 0
    async for rows in _batches(db, stmt):
        for row in rows:
            values = {
                name: (convert(value) if convert is not None else value)
                for name, convert, value in zip(names, converters, row, strict=True)
            }
            buffer += (b",\n" if count else b"\n") + dumps(values)
            count += 1
        if len(buffer) >= FLUSH_BYTES:
            yield bytes(buffer)
            buffer.clear()
    buffer += b"\n]" if count else b"]"
    job.counts[count_key] = count
    yield bytes(buffer)


async def stream_json(job: ExportJob) -> AsyncIterator[bytes]:
    async with snapshot() as db:
        async for chunk in json_chunks(db, job):
            yield chunk


# --------------------------------------------------------------------------- #
# Zip, streamed
# --------------------------------------------------------------------------- #
class _Sink(io.RawIOBase):
    """A write-only, non-seekable file for :class:`zipfile.ZipFile` that keeps only
    what was written since the last :meth:`drain`."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[bytes] = []
        self._position = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def write(self, data) -> int:  # type: ignore[override]
        chunk = bytes(data)
        self._chunks.append(chunk)
        self._position += len(chunk)
        return len(chunk)

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


class ZipStream:
    """Builds a zip archive piece by piece: ``write`` returns the bytes ready to send."""

    def __init__(self, when: datetime) -> None:
        self._sink = _Sink()
        self._zip = zipfile.ZipFile(self._sink, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
        self._when = when.replace(tzinfo=None)
        self._entry: Any = None

    def open(self, name: str) -> None:
        info = zipfile.ZipInfo(name, date_time=self._when.timetuple()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        self._entry = self._zip.open(info, mode="w")

    def write(self, data: bytes) -> bytes:
        self._entry.write(data)
        return self._sink.drain()

    def close_entry(self) -> bytes:
        self._entry.close()
        self._entry = None
        return self._sink.drain()

    def finish(self) -> bytes:
        self._zip.close()
        return self._sink.drain()


# --------------------------------------------------------------------------- #
# CSV zip
# --------------------------------------------------------------------------- #
README = """DisciplineOS — data export (CSV)
================================

Exported {exported_at} (UTC) for account {user_id}, timezone {timezone}.

* One CSV file per table, UTF-8 with a byte order mark (Excel opens it directly),
  comma separated, first row = column names, one row per record.
* Dates are YYYY-MM-DD; timestamps are ISO-8601 in UTC (e.g. 2026-10-01T09:00:00+00:00);
  `local_day`, `entry_date`, `measured_on` … are days in your timezone.
* Numbers use a dot as the decimal separator; true/false for yes/no; empty = no value.
* Columns holding structured data (aliases, scopes, settings, components …) contain JSON.
* A text cell that would start with = + - @ is written with a leading ' so a spreadsheet
  shows it as text instead of running it as a formula.
* Burned calories (workout_logs.kcal_burned, health_daily.active_kcal) are activity, never
  food budget.
* library_*.csv list the shared library rows (foods, exercises, achievements, quests)
  that your data refers to by id.
* Secrets are never exported: no token hashes, pairing codes, push keys or raw AI payloads.

For a restorable backup download the backup (GET /api/v1/export/backup) instead.

Files and rows:
{files}
"""

README_RU = """DisciplineOS — выгрузка данных (CSV)
====================================

Выгружено {exported_at} (UTC), аккаунт {user_id}, часовой пояс {timezone}.

* Один CSV-файл на таблицу: UTF-8 с меткой BOM (Excel открывает его сразу), разделитель —
  запятая, первая строка — названия столбцов, одна строка — одна запись.
* Даты — ГГГГ-ММ-ДД; моменты времени — ISO-8601 в UTC (например, 2026-10-01T09:00:00+00:00);
  `local_day`, `entry_date`, `measured_on` … — дни в вашем часовом поясе.
* Десятичный разделитель — точка; да/нет — true/false; пустая ячейка — нет значения.
* Столбцы со структурированными данными (aliases, scopes, настройки, components …) — JSON.
* Текст, который начинается с = + - @, записан с ведущим ', чтобы таблица показала его
  как текст, а не выполнила как формулу.
* Сожжённые калории (workout_logs.kcal_burned, health_daily.active_kcal) — это активность,
  а не бюджет еды.
* library_*.csv — строки общей библиотеки (продукты, упражнения, достижения, задания),
  на которые ссылаются ваши данные.
* Секреты не выгружаются никогда: ни хэши токенов, ни коды привязки, ни ключи push,
  ни исходные ответы ИИ.

Для восстановимой копии скачайте резервную копию (GET /api/v1/export/backup).

Файлы и строки:
{files}
"""
READMES = {"ru": README_RU, "en": README}


async def _csv_table(
    db: AsyncSession,
    zs: ZipStream,
    filename: str,
    columns: list[sa.Column],
    stmt: Any,
    job: ExportJob,
    count_key: str,
    sanitise: dict[str, Callable[[Any], Any]] | None = None,
) -> AsyncIterator[bytes]:
    """One CSV entry of the zip, chunk by chunk; records its row count in ``job.counts``."""
    zs.open(filename)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    out.write(CSV_BOM)
    writer.writerow([column.name for column in columns])
    cleaners = [(sanitise or {}).get(column.name) for column in columns]
    if not any(cleaners):
        cleaners = []
    count = 0
    async for rows in _batches(db, stmt):
        for row in rows:
            if cleaners:
                row = [clean(value) if clean is not None else value for clean, value in zip(cleaners, row, strict=True)]
            writer.writerow([csv_cell(value) for value in row])
            count += 1
        if out.tell() >= FLUSH_BYTES:
            yield zs.write(out.getvalue().encode("utf-8"))
            out.seek(0)
            out.truncate()
    job.counts[count_key] = count
    yield zs.write(out.getvalue().encode("utf-8")) + zs.close_entry()


async def csv_zip_chunks(db: AsyncSession, job: ExportJob) -> AsyncIterator[bytes]:
    zs = ZipStream(job.local_now)
    files: list[tuple[str, int]] = []
    tables: list[tuple[str, list[sa.Column], Any, dict[str, Callable[[Any], Any]]]] = [
        (f"{section.name}.csv", section.columns, _section_select(section, job.user_id), section.sanitisers)
        for section in SECTIONS
    ]
    tables += [
        (f"library_{library.name}.csv", library.selected(), _library_select(library, job.user_id), {})
        for library in LIBRARY
    ]
    for filename, columns, stmt, sanitise in tables:
        count_key = filename.removesuffix(".csv").replace("library_", "library.", 1)
        async for chunk in _csv_table(db, zs, filename, columns, stmt, job, count_key, sanitise):
            if chunk:
                yield chunk
        files.append((filename, job.counts[count_key]))
    readme = READMES.get(job.language, README_RU).format(
        exported_at=job.now.astimezone(UTC).isoformat(timespec="seconds"),
        user_id=job.user_id,
        timezone=job.tz,
        files="\n".join(f"  {name:32} {count:>8}" for name, count in files),
    )
    zs.open("README.txt")
    yield zs.write(readme.encode("utf-8")) + zs.close_entry() + zs.finish()


async def stream_csv(job: ExportJob) -> AsyncIterator[bytes]:
    async with snapshot() as db:
        async for chunk in csv_zip_chunks(db, job):
            yield chunk


# --------------------------------------------------------------------------- #
# Backup zip
# --------------------------------------------------------------------------- #
async def backup_chunks(db: AsyncSession, job: ExportJob) -> AsyncIterator[bytes]:
    """``data.json`` (the JSON export, hashed while it streams) then ``manifest.json``."""
    zs = ZipStream(job.local_now)
    digest = hashlib.sha256()
    size = 0
    zs.open(DATA_FILE)
    async for chunk in json_chunks(db, job):
        digest.update(chunk)
        size += len(chunk)
        data = zs.write(chunk)
        if data:
            yield data
    yield zs.close_entry()
    manifest = {
        "format": BACKUP_FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": get_settings().api_version,
        "schema_version": job.schema_version,
        "user_id": job.user_id,
        "created_at": job.now.astimezone(UTC),
        "timezone": job.tz,
        "counts": job.counts,
        "files": {DATA_FILE: {"sha256": digest.hexdigest(), "bytes": size}},
    }
    zs.open(MANIFEST_FILE)
    yield zs.write(orjson.dumps(manifest, option=orjson.OPT_INDENT_2) + b"\n") + zs.close_entry() + zs.finish()


async def stream_backup(job: ExportJob) -> AsyncIterator[bytes]:
    async with snapshot() as db:
        async for chunk in backup_chunks(db, job):
            yield chunk
