"""Time tracking: one running timer per user, manual entries, summaries.

* **One running timer.** The partial unique index ``uq_time_entries_running``
  holds the line in the database; start/stop also take a per-user advisory
  lock, so two devices tapping "start" at once end with one timer running and
  the other stopped (a switch), never an error.
* **Switch.** Starting while a timer runs stops it at the new ``started_at``.
* **Replays.** A replayed start (same client id) returns the current state and
  never creates a second row; stopping without a running timer is a no-op.
* **Offline catch-up.** A start dated before the running timer began (a device
  replaying its offline queue) never stops that newer timer: the entry is
  stored already ended where the next timer began (any start ends what runs),
  and it ends, at its own start, a replayed entry that had been ended at that
  same point (an offline switch). A stop names its entry (``entry_id``) and
  touches nothing else; stopping an entry that already ended moves its end
  earlier when this stop came first (the first stop wins), never later.
* **Bounds.** A timer may start ≤ 24 h back and ≤ 5 min ahead; an entry lasts at
  most 24 h; a timer running longer than that needs an explicit ``ended_at``. A day
  is a day of wall clock: midnight to midnight across a fall-back night (25 real
  hours) is within the limit.
* **Attribution.** An entry belongs to the local day of ``started_at``; a timer
  on a task inherits the task's project and area (an area also comes from the
  project), and the task's ``actual_minutes`` is recomputed after every change.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import Integer, cast, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.ids import new_id
from app.core.logging import get_logger
from app.models import Area, Project, Task, TimeEntry, TimeEntrySource
from app.services.planner.common import (
    FUTURE_TOLERANCE,
    Clock,
    advisory_lock,
    check_range,
    id_conflict,
    owned,
    parse_hhmm,
)
from app.services.planner.containers import entry_seconds, week_start_for
from app.services.planner.tasks import get_task

log = get_logger(__name__)

MAX_ENTRY = timedelta(hours=24)
MAX_START_BACK = timedelta(hours=24)
MIN_MANUAL = timedelta(minutes=1)
ENTRY_RANGE_DAYS = 62
LOCK_SCOPE = "planner-timer"


def _invalid(message: str, **details) -> Unprocessable:
    return Unprocessable(message, code="invalid_time_entry", details=details or None)


@dataclass(slots=True)
class TimerState:
    entry: TimeEntry | None
    running: TimeEntry | None
    stopped: TimeEntry | None = None


async def running_entry(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> TimeEntry | None:
    stmt = select(TimeEntry).where(
        TimeEntry.user_id == user_id, TimeEntry.ended_at.is_(None), TimeEntry.is_deleted.is_(False)
    )
    if lock:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt.execution_options(populate_existing=True).limit(1))


async def recompute_actual(db: AsyncSession, user_id: uuid.UUID, task_ids: Iterable[uuid.UUID | None]) -> None:
    """``actual_minutes`` = round(sum(duration_s) / 60) of the task's non-deleted entries."""
    ids = {task_id for task_id in task_ids if task_id is not None}
    if not ids:
        return
    total = (
        select(func.coalesce(func.round(func.sum(TimeEntry.duration_s) / 60.0), 0))
        .where(
            TimeEntry.task_id == Task.id,
            TimeEntry.is_deleted.is_(False),
            TimeEntry.duration_s.is_not(None),
        )
        .scalar_subquery()
    )
    await db.execute(
        update(Task)
        .where(Task.id.in_(ids), Task.user_id == user_id)
        .values(actual_minutes=cast(total, Integer))
        .execution_options(synchronize_session=False)
    )


async def _attribution(
    db: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    area_id: uuid.UUID | None,
    *,
    fill_project: bool = True,
    fill_area: bool = True,
) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
    """Validated (task, project, area); a missing project/area comes from the task, an area from the project."""
    task = await get_task(db, user_id, task_id) if task_id is not None else None
    project = await owned(db, Project, user_id, project_id, "Project") if project_id is not None else None
    if area_id is not None:
        await owned(db, Area, user_id, area_id, "Area")
    if task is not None:
        if fill_project and project_id is None:
            project_id = task.project_id
        if fill_area and area_id is None:
            area_id = task.area_id
    if fill_area and area_id is None and project_id is not None:
        if project is None:
            project = await db.scalar(select(Project).where(Project.id == project_id, Project.user_id == user_id))
        area_id = project.area_id if project is not None else None
    return task_id, project_id, area_id


def _longer_than_a_day(start: datetime, end: datetime, clock: Clock) -> bool:
    """More than 24 hours both in real time and on the user's wall clock: 00:00–00:00 on a
    fall-back day (25 real hours) is one day, 00:00–00:30 the next day is not."""
    return end - start > MAX_ENTRY and clock.naive(end) - clock.naive(start) > MAX_ENTRY


def _stop(entry: TimeEntry, ended_at: datetime) -> None:
    entry.ended_at = ended_at
    entry.duration_s = int((ended_at - entry.started_at).total_seconds())


def _too_long(entry: TimeEntry) -> Conflict:
    return Conflict(
        "This timer ran for more than 24 hours: stop it with an explicit end time",
        code="timer_too_long",
        details={"started_at": entry.started_at.isoformat(), "entry_id": str(entry.id)},
    )


async def timer_state(db: AsyncSession, user_id: uuid.UUID) -> TimeEntry | None:
    return await running_entry(db, user_id)


async def start_timer(db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, payload) -> TimerState:
    await advisory_lock(db, user_id, LOCK_SCOPE)
    existing = await db.get(TimeEntry, payload.id, populate_existing=True)
    if existing is not None:
        if existing.user_id != user_id:
            raise id_conflict("time entry")
        if existing.is_deleted:
            raise NotFound("Time entry not found", details={"id": str(payload.id)})
        return TimerState(entry=existing, running=await running_entry(db, user_id))
    started_at = clock.instant(payload.started_at)
    if started_at > clock.now + FUTURE_TOLERANCE or started_at < clock.now - MAX_START_BACK:
        raise _invalid("A timer starts at most 24 hours back and 5 minutes ahead")
    task_id, project_id, area_id = await _attribution(db, user_id, payload.task_id, payload.project_id, payload.area_id)
    entry = TimeEntry(
        id=payload.id,
        client_uuid=payload.id,
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        area_id=area_id,
        description=payload.description,
        started_at=started_at,
        local_day=clock.local_day(started_at),
        source=TimeEntrySource.TIMER,
        is_deleted=False,
    )
    running = await running_entry(db, user_id, lock=True)
    if running is not None and started_at < running.started_at <= clock.now:
        return await _start_before(db, user_id=user_id, entry=entry, running=running)
    stopped = running
    if stopped is not None:
        ended_at = max(started_at, stopped.started_at)
        if _longer_than_a_day(stopped.started_at, ended_at, clock):
            raise _too_long(stopped)
        _stop(stopped, ended_at)
        await db.flush()
    await _insert(db, entry)
    if stopped is not None:
        await recompute_actual(db, user_id, [stopped.task_id])
    log.info("timer_started", entry_id=str(entry.id), switched=str(stopped.id) if stopped else None)
    return TimerState(entry=entry, running=entry, stopped=stopped)


async def _insert(db: AsyncSession, entry: TimeEntry) -> None:
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except IntegrityError:
        raise id_conflict("time entry") from None


async def _start_before(db: AsyncSession, *, user_id: uuid.UUID, entry: TimeEntry, running: TimeEntry) -> TimerState:
    """A start dated before the running timer began: a device replaying its offline queue.

    The newer timer keeps running. Any start ends the timer that ran before it, so the
    replayed entry is stored already ended where the next timer began; and it ends, at
    its own start, an entry that had been ended at that same point — the previous step
    of the same offline sequence (start A, switch to C) — instead of overlapping it.
    """
    timers = (TimeEntry.user_id == user_id, TimeEntry.source == TimeEntrySource.TIMER, TimeEntry.is_deleted.is_(False))
    later = await db.scalar(
        select(func.min(TimeEntry.started_at)).where(*timers, TimeEntry.started_at > entry.started_at)
    )
    next_start = running.started_at if later is None else min(later, running.started_at)
    switched = (
        await db.scalars(
            select(TimeEntry)
            .where(*timers, TimeEntry.started_at <= entry.started_at, TimeEntry.ended_at == next_start)
            .order_by(TimeEntry.started_at.desc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    for row in switched:
        _stop(row, entry.started_at)
    await db.flush()
    _stop(entry, next_start)
    await _insert(db, entry)
    await recompute_actual(db, user_id, [entry.task_id, *(row.task_id for row in switched)])
    log.info("timer_start_replayed", entry_id=str(entry.id), running=str(running.id), switched=len(switched))
    return TimerState(entry=entry, running=running, stopped=switched[0] if switched else None)


async def stop_timer(db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, payload) -> TimeEntry | None:
    await advisory_lock(db, user_id, LOCK_SCOPE)
    if payload.entry_id is not None:
        entry = await db.scalar(
            select(TimeEntry)
            .where(TimeEntry.id == payload.entry_id, TimeEntry.user_id == user_id, TimeEntry.is_deleted.is_(False))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if entry is None:
            raise NotFound("Time entry not found", details={"id": str(payload.entry_id)})
        if entry.ended_at is not None:
            return await _stop_again(db, user_id=user_id, clock=clock, entry=entry, ended_at=payload.ended_at)
    else:
        entry = await running_entry(db, user_id, lock=True)
        if entry is None:
            return None
    if payload.ended_at is None:
        if _longer_than_a_day(entry.started_at, clock.now, clock):
            raise _too_long(entry)
        ended_at = max(clock.now, entry.started_at)
    else:
        ended_at = clock.instant(payload.ended_at)
        if ended_at < entry.started_at or ended_at > clock.now + FUTURE_TOLERANCE:
            raise _invalid("ended_at must be after the start and not in the future")
        if _longer_than_a_day(entry.started_at, ended_at, clock):
            raise _invalid("A time entry lasts at most 24 hours")
    _stop(entry, ended_at)
    await db.flush()
    await recompute_actual(db, user_id, [entry.task_id])
    log.info("timer_stopped", entry_id=str(entry.id), seconds=entry.duration_s)
    return entry


async def _stop_again(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, entry: TimeEntry, ended_at: datetime | None
) -> TimeEntry:
    """A stop of a timer entry that already ended: a replay changes nothing, but a stop that
    came first — an offline stop replayed after a switch or a replayed start ended the entry
    at the next timer's start — moves the end back to it. The first stop wins."""
    if ended_at is None or entry.source != TimeEntrySource.TIMER:
        return entry
    when = clock.instant(ended_at)
    if entry.ended_at is None or when >= entry.ended_at:
        return entry
    if when < entry.started_at:
        raise _invalid("ended_at must be after the start and not in the future")
    _stop(entry, when)
    await db.flush()
    await recompute_actual(db, user_id, [entry.task_id])
    log.info("timer_stop_replayed", entry_id=str(entry.id), seconds=entry.duration_s)
    return entry


def _manual_bounds(day: date, start: str, end: str, clock: Clock) -> tuple[datetime, datetime]:
    start_t, end_t = parse_hhmm(start), parse_hhmm(end)
    end_day = day + timedelta(days=1) if end_t <= start_t else day
    started_at, ended_at = clock.at(day, start_t), clock.at(end_day, end_t)
    if ended_at - started_at < MIN_MANUAL or _longer_than_a_day(started_at, ended_at, clock):
        raise _invalid("A time entry lasts between 1 minute and 24 hours")
    if ended_at > clock.now + FUTURE_TOLERANCE:
        raise _invalid("A time entry cannot end in the future")
    return started_at, ended_at


async def create_entry(db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, payload) -> TimeEntry:
    if payload.id is not None:
        existing = await db.get(TimeEntry, payload.id, populate_existing=True)
        if existing is not None:
            if existing.user_id != user_id:
                raise id_conflict("time entry")
            if existing.is_deleted:
                raise NotFound("Time entry not found", details={"id": str(payload.id)})
            return existing
    started_at, ended_at = _manual_bounds(payload.date, payload.start, payload.end, clock)
    task_id, project_id, area_id = await _attribution(db, user_id, payload.task_id, payload.project_id, payload.area_id)
    entry_id = payload.id or new_id()
    entry = TimeEntry(
        id=entry_id,
        client_uuid=entry_id,
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        area_id=area_id,
        description=payload.description,
        note=payload.note,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=int((ended_at - started_at).total_seconds()),
        local_day=clock.local_day(started_at),
        source=TimeEntrySource.MANUAL,
        is_deleted=False,
    )
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except IntegrityError:
        existing = await db.get(TimeEntry, entry_id, populate_existing=True)
        if existing is None or existing.user_id != user_id or existing.is_deleted:
            raise id_conflict("time entry") from None
        return existing
    await recompute_actual(db, user_id, [task_id])
    return entry


async def _own_entry(db: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> TimeEntry:
    entry = await db.scalar(
        select(TimeEntry)
        .where(TimeEntry.id == entry_id, TimeEntry.user_id == user_id, TimeEntry.is_deleted.is_(False))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if entry is None:
        raise NotFound("Time entry not found", details={"id": str(entry_id)})
    return entry


async def update_entry(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, entry_id: uuid.UUID, payload
) -> TimeEntry:
    await advisory_lock(db, user_id, LOCK_SCOPE)
    entry = await _own_entry(db, user_id, entry_id)
    fs = payload.model_fields_set
    old_task = entry.task_id
    if {"task_id", "project_id", "area_id"} & fs:
        # a new task brings its project/area, a new project its area — unless they are sent too
        task_changed = "task_id" in fs and payload.task_id != entry.task_id
        project_changed = "project_id" in fs and payload.project_id != entry.project_id
        task_id = payload.task_id if "task_id" in fs else entry.task_id
        kept_project = None if task_changed else entry.project_id
        kept_area = None if task_changed or project_changed else entry.area_id
        project_id = payload.project_id if "project_id" in fs else kept_project
        area_id = payload.area_id if "area_id" in fs else kept_area
        entry.task_id, entry.project_id, entry.area_id = await _attribution(
            db,
            user_id,
            task_id,
            project_id,
            area_id,
            fill_project=task_changed and "project_id" not in fs,
            fill_area=(task_changed or project_changed) and "area_id" not in fs,
        )
    for name in ("description", "note"):
        if name in fs:
            setattr(entry, name, getattr(payload, name))
    if "date" in fs:
        started_at, ended_at = _manual_bounds(payload.date, payload.start, payload.end, clock)
        entry.started_at, entry.ended_at = started_at, ended_at
        entry.duration_s = int((ended_at - started_at).total_seconds())
        entry.local_day = clock.local_day(started_at)
    await db.flush()
    await recompute_actual(db, user_id, [old_task, entry.task_id])
    return entry


async def delete_entry(db: AsyncSession, *, user_id: uuid.UUID, entry_id: uuid.UUID) -> None:
    await advisory_lock(db, user_id, LOCK_SCOPE)
    entry = await _own_entry(db, user_id, entry_id)
    entry.is_deleted = True
    await db.flush()
    await recompute_actual(db, user_id, [entry.task_id])


# --------------------------------------------------------------------------- #
# Lists and summaries
# --------------------------------------------------------------------------- #
def entry_total_seconds(entry: TimeEntry, clock: Clock) -> float:
    if entry.duration_s is not None:
        return float(entry.duration_s)
    return max(0.0, (clock.now - entry.started_at).total_seconds())


async def list_entries(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, first: date, last: date
) -> list[TimeEntry]:
    check_range(first, last, ENTRY_RANGE_DAYS)
    return list(
        (
            await db.scalars(
                select(TimeEntry)
                .where(
                    TimeEntry.user_id == user_id,
                    TimeEntry.is_deleted.is_(False),
                    TimeEntry.local_day.between(first, last),
                )
                .order_by(TimeEntry.started_at.desc(), TimeEntry.id.desc())
                .execution_options(populate_existing=True)
            )
        ).all()
    )


def by_day(entries: Iterable[TimeEntry], first: date, last: date, clock: Clock) -> list[tuple[date, float]]:
    seconds: dict[date, float] = {}
    for entry in entries:
        seconds[entry.local_day] = seconds.get(entry.local_day, 0.0) + entry_total_seconds(entry, clock)
    out = []
    day = first
    while day <= last:
        out.append((day, seconds.get(day, 0.0)))
        day += timedelta(days=1)
    return out


@dataclass(slots=True)
class Summary:
    period: str
    first: date
    last: date
    total_seconds: float = 0.0
    by_area: list[tuple[Area | None, float, int | None]] = field(default_factory=list)
    by_project: list[tuple[Project | None, float]] = field(default_factory=list)
    by_day: list[tuple[date, float]] = field(default_factory=list)


MONTH_FACTOR = 4.35


def period_bounds(period: str, day: date, week_start: int) -> tuple[date, date]:
    if period == "month":
        first = day.replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
        return first, following - timedelta(days=1)
    first = week_start_for(day, week_start)
    return first, first + timedelta(days=6)


async def summary(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, period: str, day: date, week_start: int
) -> Summary:
    first, last = period_bounds(period, day, week_start)
    seconds = entry_seconds(clock.now)
    base = (
        TimeEntry.user_id == user_id,
        TimeEntry.is_deleted.is_(False),
        TimeEntry.local_day.between(first, last),
    )
    out = Summary(period=period, first=first, last=last)
    area_rows = (
        await db.execute(select(TimeEntry.area_id, func.sum(seconds)).where(*base).group_by(TimeEntry.area_id))
    ).all()
    project_rows = (
        await db.execute(select(TimeEntry.project_id, func.sum(seconds)).where(*base).group_by(TimeEntry.project_id))
    ).all()
    day_rows = (
        await db.execute(select(TimeEntry.local_day, func.sum(seconds)).where(*base).group_by(TimeEntry.local_day))
    ).all()
    area_seconds = {row[0]: float(row[1] or 0) for row in area_rows}
    project_seconds = {row[0]: float(row[1] or 0) for row in project_rows}
    out.total_seconds = sum(area_seconds.values())

    areas = (
        await db.scalars(
            select(Area).where(
                Area.user_id == user_id,
                (Area.id.in_([key for key in area_seconds if key is not None]))
                | ((Area.weekly_target_minutes.is_not(None)) & (Area.is_archived.is_(False))),
            )
        )
    ).all()
    for area in areas:
        target = area.weekly_target_minutes
        if target is not None and period == "month":
            target = round(target * MONTH_FACTOR)
        out.by_area.append((area, area_seconds.get(area.id, 0.0), target))
    if None in area_seconds:
        out.by_area.append((None, area_seconds[None], None))
    out.by_area.sort(
        key=lambda item: (-item[1], item[0].sort_order if item[0] else 1 << 16, item[0].name if item[0] else "")
    )

    projects = {}
    project_ids = [key for key in project_seconds if key is not None]
    if project_ids:
        projects = {
            row.id: row
            for row in (
                await db.scalars(select(Project).where(Project.id.in_(project_ids), Project.user_id == user_id))
            ).all()
        }
    for key, value in project_seconds.items():
        out.by_project.append((projects.get(key) if key is not None else None, value))
    out.by_project.sort(key=lambda item: (-item[1], item[0].name if item[0] else ""))

    per_day = {row[0]: float(row[1] or 0) for row in day_rows}
    cursor = first
    while cursor <= last:
        out.by_day.append((cursor, per_day.get(cursor, 0.0)))
        cursor += timedelta(days=1)
    return out


def local_start_day_end(entry: TimeEntry, clock: Clock) -> tuple[str, str | None, bool]:
    start = clock.local(entry.started_at)
    if entry.ended_at is None:
        return start.strftime("%H:%M"), None, False
    end = clock.local(entry.ended_at)
    return start.strftime("%H:%M"), end.strftime("%H:%M"), end.date() > entry.local_day
