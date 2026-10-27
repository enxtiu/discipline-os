"""Daily journal (one entry per local day) and the evening-review day summary.

The summary only reads other domains: food logged vs the calorie target,
workouts and their burned kcal (shown on their own — never food budget), steps,
sleep, tasks and tracked time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.ids import new_id
from app.models import Area, JournalEntry, MealLog, Task, TaskStatus, TimeEntry, UserSettings
from app.models.training import HealthDaily, SleepLog, WorkoutLog, WorkoutStatus
from app.services.planner.common import Clock, RefTuple, check_range, id_conflict, normalize_tags, validation_error
from app.services.planner.containers import entry_seconds
from app.services.planner.tasks import CLOSED
from app.services.settings_service import current_targets

PAST_DAYS = 3650
FUTURE_DAYS = 1
RANGE_DAYS = 366
MAX_JOURNAL_TAGS = 20
DONE_TITLES = 5
TEXT_FIELDS = ("note_md", "wins_md", "improvements_md")
FIELDS = ("note_md", "rating", "mood", "energy", "wins_md", "improvements_md", "tags")


def check_day(day: date, clock: Clock) -> None:
    today = clock.today
    if not today - timedelta(days=PAST_DAYS) <= day <= today + timedelta(days=FUTURE_DAYS):
        raise validation_error("The journal covers the last ten years up to tomorrow", date=day.isoformat())


def has_note(entry: JournalEntry) -> bool:
    return any((getattr(entry, name) or "").strip() for name in TEXT_FIELDS)


async def get_entry(db: AsyncSession, user_id: uuid.UUID, day: date) -> JournalEntry | None:
    return await db.scalar(
        select(JournalEntry)
        .where(JournalEntry.user_id == user_id, JournalEntry.entry_date == day)
        .execution_options(populate_existing=True)
    )


async def upsert(db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, day: date, payload) -> JournalEntry:
    """Fields not sent keep their value; the client id is used when the day's entry is first created."""
    check_day(day, clock)
    if payload.id is not None:
        existing = await db.get(JournalEntry, payload.id)
        if existing is not None and (existing.user_id != user_id or existing.entry_date != day):
            raise id_conflict("journal entry")
    values = {name: getattr(payload, name) for name in FIELDS if name in payload.model_fields_set}
    if "tags" in values:
        values["tags"] = normalize_tags(values["tags"] or [], limit=MAX_JOURNAL_TAGS)
    entry_id = payload.id or new_id()
    now = clock.now
    stmt = (
        pg_insert(JournalEntry)
        .values(
            id=entry_id,
            client_uuid=entry_id,
            user_id=user_id,
            entry_date=day,
            created_at=now,
            updated_at=now,
            **values,
        )
        .on_conflict_do_update(constraint="uq_journal_entries_user_day", set_={**values, "updated_at": now})
        .returning(JournalEntry.id)
    )
    try:
        async with db.begin_nested():
            row_id = (await db.execute(stmt)).scalar_one()
    except IntegrityError:  # the client id was taken by another day's entry in the meantime
        raise id_conflict("journal entry") from None
    entry = await db.scalar(
        select(JournalEntry).where(JournalEntry.id == row_id).execution_options(populate_existing=True)
    )
    assert entry is not None
    return entry


async def delete_entry(db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, day: date) -> None:
    check_day(day, clock)
    result = await db.execute(
        delete(JournalEntry)
        .where(JournalEntry.user_id == user_id, JournalEntry.entry_date == day)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        raise NotFound("No journal entry for this day", details={"date": day.isoformat()})


async def list_range(db: AsyncSession, *, user_id: uuid.UUID, first: date, last: date) -> list[JournalEntry]:
    check_range(first, last, RANGE_DAYS)
    return list(
        (
            await db.scalars(
                select(JournalEntry)
                .where(JournalEntry.user_id == user_id, JournalEntry.entry_date.between(first, last))
                .order_by(JournalEntry.entry_date)
            )
        ).all()
    )


@dataclass(slots=True)
class DaySummary:
    day: date
    tasks_done: int = 0
    tasks_done_titles: list[str] = field(default_factory=list)
    tasks_open_due: int = 0
    time_seconds: float = 0.0
    time_by_area: list[tuple[RefTuple | None, float]] = field(default_factory=list)
    kcal: float = 0.0
    target_kcal: int | None = None
    workouts: int = 0
    workout_kcal: float | None = None
    steps: int | None = None
    sleep_minutes: int | None = None


async def day_summary(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, day: date, settings_row: UserSettings | None
) -> DaySummary:
    out = DaySummary(day=day)
    start, end = clock.day_bounds(day)
    titles = (
        await db.scalars(
            select(Task.title)
            .where(
                Task.user_id == user_id,
                Task.status == TaskStatus.DONE,
                Task.completed_at >= start,
                Task.completed_at < end,
            )
            .order_by(Task.completed_at, Task.id)
        )
    ).all()
    out.tasks_done = len(titles)
    out.tasks_done_titles = list(titles[:DONE_TITLES])
    out.tasks_open_due = int(
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.user_id == user_id, Task.due_date == day, Task.status.not_in(CLOSED))
        )
        or 0
    )

    rows = (
        await db.execute(
            select(TimeEntry.area_id, func.sum(entry_seconds(clock.now)))
            .where(TimeEntry.user_id == user_id, TimeEntry.is_deleted.is_(False), TimeEntry.local_day == day)
            .group_by(TimeEntry.area_id)
        )
    ).all()
    area_ids = [row[0] for row in rows if row[0] is not None]
    areas: dict[uuid.UUID, RefTuple] = {}
    if area_ids:
        area_rows = await db.execute(
            select(Area.id, Area.name, Area.color).where(Area.id.in_(area_ids), Area.user_id == user_id)
        )
        areas = {row.id: (row.id, row.name, row.color) for row in area_rows}
    for area_id, seconds in rows:
        out.time_seconds += float(seconds or 0)
        out.time_by_area.append((areas.get(area_id) if area_id is not None else None, float(seconds or 0)))
    out.time_by_area.sort(key=lambda item: -item[1])

    out.kcal = float(
        await db.scalar(
            select(func.coalesce(func.sum(MealLog.kcal), 0)).where(
                MealLog.user_id == user_id, MealLog.local_day == day, MealLog.is_deleted.is_(False)
            )
        )
        or 0
    )
    if settings_row is not None:
        out.target_kcal = current_targets(settings_row, as_of=day).target_kcal

    workouts = (
        await db.execute(
            select(func.count(), func.sum(WorkoutLog.kcal_burned)).where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.local_day == day,
                WorkoutLog.status == WorkoutStatus.COMPLETED,
                WorkoutLog.is_deleted.is_(False),
            )
        )
    ).one()
    out.workouts = int(workouts[0] or 0)
    out.workout_kcal = float(workouts[1]) if workouts[1] is not None else None

    out.steps = await db.scalar(
        select(HealthDaily.steps).where(HealthDaily.user_id == user_id, HealthDaily.local_day == day)
    )
    out.sleep_minutes = await db.scalar(
        select(SleepLog.duration_min).where(SleepLog.user_id == user_id, SleepLog.local_day == day).limit(1)
    )
    return out
