"""Read views: today, inbox, upcoming, calendar, tag, archive, counts, search.

Every day boundary is the user's (``Clock``): a task completed at 23:30 in
Bangkok belongs to that Bangkok day, whatever the UTC date says.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Area, JournalEntry, Project, ProjectStatus, Resource, Tag, Task, TaskStatus, task_tags
from app.services.planner import recur
from app.services.planner.common import Clock, check_range, owned
from app.services.planner.tasks import CLOSED, recurrence_anchor, task_query
from app.services.planner.time_tracking import running_entry

UPCOMING_MAX_DAYS = 31
CALENDAR_MAX_DAYS = 62
ARCHIVE_TASKS = 100
SEARCH_TASKS = 50
SEARCH_OTHERS = 20
SEARCH_JOURNAL = 20
SNIPPET = 160

OPEN = Task.status.not_in(CLOSED)


def sort_key(task: Task, clock: Clock):
    """Pinned first, then priority, then the local due time (none last), then age."""
    due = clock.hhmm(task.due_at) if task.due_at is not None else "99:99"
    return (not task.is_pinned, task.priority, due, task.created_at, task.id)


def block_day(task: Task, clock: Clock) -> date | None:
    return clock.local_day(task.scheduled_start) if task.scheduled_start is not None else None


async def _tasks(db: AsyncSession, *conditions, order=None) -> list[Task]:
    stmt = task_query().where(*conditions).execution_options(populate_existing=True)
    if order is not None:
        stmt = stmt.order_by(*order)
    return list((await db.scalars(stmt)).all())


# --------------------------------------------------------------------------- #
# Counts & today
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Counts:
    day: date
    inbox: int = 0
    today: int = 0
    overdue: int = 0
    timer_running: bool = False


async def counts(db: AsyncSession, user_id: uuid.UUID, clock: Clock) -> Counts:
    today = clock.today
    start, end = clock.day_bounds(today)
    row = (
        await db.execute(
            select(
                func.count().filter(Task.status == TaskStatus.INBOX),
                func.count().filter(and_(OPEN, Task.due_date < today)),
                func.count().filter(
                    and_(
                        OPEN,
                        or_(Task.due_date == today, and_(Task.scheduled_start >= start, Task.scheduled_start < end)),
                    )
                ),
            ).where(Task.user_id == user_id)
        )
    ).one()
    running = await running_entry(db, user_id)
    return Counts(
        day=today, inbox=int(row[0]), overdue=int(row[1]), today=int(row[2]), timer_running=running is not None
    )


@dataclass(slots=True)
class TodayView:
    day: date
    overdue: list[Task]
    today: list[Task]
    scheduled: list[Task]
    done: list[Task]


async def today_view(db: AsyncSession, user_id: uuid.UUID, clock: Clock, day: date) -> TodayView:
    start, end = clock.day_bounds(day)
    on_day = and_(Task.scheduled_start >= start, Task.scheduled_start < end)
    overdue = []
    if day == clock.today:
        overdue = await _tasks(db, Task.user_id == user_id, OPEN, Task.due_date < day)
    due = await _tasks(
        db,
        Task.user_id == user_id,
        OPEN,
        Task.due_date == day,
        or_(Task.scheduled_start.is_(None), Task.scheduled_start < start, Task.scheduled_start >= end),
    )
    scheduled = await _tasks(
        db, Task.user_id == user_id, OPEN, on_day, order=(Task.scheduled_start, Task.priority, Task.created_at)
    )
    done = await _tasks(
        db, Task.user_id == user_id, Task.status == TaskStatus.DONE, Task.completed_at >= start, Task.completed_at < end
    )
    return TodayView(
        day=day,
        overdue=sorted(overdue, key=lambda task: sort_key(task, clock)),
        today=sorted(due, key=lambda task: sort_key(task, clock)),
        scheduled=scheduled,
        done=sorted(done, key=lambda task: sort_key(task, clock)),
    )


async def inbox(db: AsyncSession, user_id: uuid.UUID) -> list[Task]:
    return await _tasks(
        db, Task.user_id == user_id, Task.status == TaskStatus.INBOX, order=(Task.created_at.desc(), Task.id.desc())
    )


# --------------------------------------------------------------------------- #
# Virtual occurrences
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Occurrence:
    task: Task
    day: date
    due_time: str | None
    block: tuple[str, str, int] | None  # start, end, minutes


async def occurrences(
    db: AsyncSession, user_id: uuid.UUID, clock: Clock, first: date, last: date
) -> dict[date, list[Occurrence]]:
    """Future occurrences of open recurring tasks in ``[first, last]`` — after the real
    open instance's date and after today (nothing before them can still happen)."""
    rows = await _tasks(db, Task.user_id == user_id, OPEN, Task.recurrence_rule.is_not(None))
    latest: dict[uuid.UUID, Task] = {}
    for task in rows:  # one expansion per series: its newest open instance
        key = task.recurrence_parent_id or task.id
        if key not in latest or (task.series_index or 0) > (latest[key].series_index or 0):
            latest[key] = task
    out: dict[date, list[Occurrence]] = {}
    for task in latest.values():
        anchor = recurrence_anchor(task, clock)
        if anchor is None:
            continue
        rule = recur.parse_stored(task.recurrence_rule or "")
        remaining = None if rule.count is None else rule.count - 1 - (task.series_index or 0)
        dates = recur.occurrences_between(
            rule,
            anchor,
            first=first,
            last=last,
            after=max(anchor.date(), clock.today),
            until=task.recurrence_until,
            remaining=remaining,
        )
        if not dates:
            continue
        due_time = clock.hhmm(task.due_at) if task.due_at is not None else None
        block = None
        if task.scheduled_start is not None and task.scheduled_end is not None:
            minutes = round((task.scheduled_end - task.scheduled_start).total_seconds() / 60)
            block = (clock.hhmm(task.scheduled_start), clock.hhmm(task.scheduled_end), minutes)
        for day in dates:
            out.setdefault(day, []).append(Occurrence(task=task, day=day, due_time=due_time, block=block))
    for items in out.values():
        items.sort(key=lambda item: (item.block[0] if item.block else item.due_time or "99:99", item.task.priority))
    return out


# --------------------------------------------------------------------------- #
# Upcoming & calendar
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class DayBucket:
    day: date
    tasks: list[Task] = field(default_factory=list)  # upcoming: due or block that day
    all_day: list[Task] = field(default_factory=list)  # calendar
    blocks: list[Task] = field(default_factory=list)  # calendar
    occurrences: list[Occurrence] = field(default_factory=list)
    done_count: int = 0


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


async def upcoming(db: AsyncSession, user_id: uuid.UUID, clock: Clock, first: date, days: int) -> list[DayBucket]:
    last = first + timedelta(days=days - 1)
    start, end = clock.range_bounds(first, last)
    rows = await _tasks(
        db,
        Task.user_id == user_id,
        OPEN,
        or_(Task.due_date.between(first, last), and_(Task.scheduled_start >= start, Task.scheduled_start < end)),
    )
    buckets = {day: DayBucket(day=day) for day in _days(first, last)}
    for task in rows:
        days_of_task = {task.due_date, block_day(task, clock)}
        for day in days_of_task:
            if day is not None and day in buckets:
                buckets[day].tasks.append(task)
    virtual = await occurrences(db, user_id, clock, first, last)
    for day, bucket in buckets.items():
        bucket.tasks.sort(key=lambda task: sort_key(task, clock))
        bucket.occurrences = virtual.get(day, [])
    return list(buckets.values())


async def calendar(db: AsyncSession, user_id: uuid.UUID, clock: Clock, first: date, last: date) -> list[DayBucket]:
    check_range(first, last, CALENDAR_MAX_DAYS)
    start, end = clock.range_bounds(first, last)
    rows = await _tasks(
        db,
        Task.user_id == user_id,
        Task.status != TaskStatus.CANCELLED,
        or_(Task.due_date.between(first, last), and_(Task.scheduled_start >= start, Task.scheduled_start < end)),
    )
    buckets = {day: DayBucket(day=day) for day in _days(first, last)}
    for task in rows:
        if task.scheduled_start is not None:
            day = block_day(task, clock)
            if day in buckets:
                buckets[day].blocks.append(task)
        elif task.due_date in buckets:
            buckets[task.due_date].all_day.append(task)
    completed = (
        await db.scalars(
            select(Task.completed_at).where(
                Task.user_id == user_id,
                Task.status == TaskStatus.DONE,
                Task.completed_at >= start,
                Task.completed_at < end,
            )
        )
    ).all()
    for instant in completed:
        day = clock.local_day(instant)
        if day in buckets:
            buckets[day].done_count += 1
    virtual = await occurrences(db, user_id, clock, first, last)
    for day, bucket in buckets.items():
        bucket.blocks.sort(key=lambda task: (task.scheduled_start, task.priority, task.created_at))
        bucket.all_day.sort(key=lambda task: sort_key(task, clock))
        bucket.occurrences = virtual.get(day, [])
    return list(buckets.values())


# --------------------------------------------------------------------------- #
# Tag view & archive
# --------------------------------------------------------------------------- #
def list_key(task: Task):
    return (task.status in CLOSED, not task.is_pinned, task.priority, task.due_date or date.max, task.created_at)


async def tag_view(
    db: AsyncSession, user_id: uuid.UUID, tag_id: uuid.UUID, include_done: bool
) -> tuple[Tag, list[Task]]:
    tag = await owned(db, Tag, user_id, tag_id, "Tag")
    conditions = [
        Task.user_id == user_id,
        Task.id.in_(select(task_tags.c.task_id).where(task_tags.c.tag_id == tag.id, task_tags.c.user_id == user_id)),
    ]
    if not include_done:
        conditions.append(OPEN)
    rows = await _tasks(db, *conditions)
    return tag, sorted(rows, key=list_key)


@dataclass(slots=True)
class Archive:
    projects: list[Project]
    areas: list[Area]
    resources: list[Resource]
    tasks: list[Task]


async def archive(db: AsyncSession, user_id: uuid.UUID) -> Archive:
    projects = (
        await db.scalars(
            select(Project)
            .where(
                Project.user_id == user_id,
                or_(
                    Project.is_archived.is_(True),
                    Project.status.in_((ProjectStatus.COMPLETED, ProjectStatus.CANCELLED, ProjectStatus.ARCHIVED)),
                ),
            )
            .order_by(Project.completed_at.desc().nulls_last(), Project.updated_at.desc())
        )
    ).all()
    areas = (
        await db.scalars(
            select(Area).where(Area.user_id == user_id, Area.is_archived.is_(True)).order_by(Area.updated_at.desc())
        )
    ).all()
    resources = (
        await db.scalars(
            select(Resource)
            .where(Resource.user_id == user_id, Resource.is_archived.is_(True))
            .order_by(Resource.updated_at.desc())
        )
    ).all()
    tasks = (
        await db.scalars(
            task_query()
            .where(Task.user_id == user_id, Task.status.in_(CLOSED))
            .order_by(Task.completed_at.desc().nulls_last(), Task.updated_at.desc())
            .limit(ARCHIVE_TASKS)
            .execution_options(populate_existing=True)
        )
    ).all()
    return Archive(projects=list(projects), areas=list(areas), resources=list(resources), tasks=list(tasks))


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def _norm(column):
    return func.replace(func.lower(func.coalesce(column, "")), "ё", "е")


def _snippet(text: str, needle: str) -> str:
    flat = " ".join(text.split())
    index = flat.lower().replace("ё", "е").find(needle)
    if index < 0:
        return flat[:SNIPPET] + ("…" if len(flat) > SNIPPET else "")
    begin = max(0, index - SNIPPET // 3)
    piece = flat[begin : begin + SNIPPET]
    return ("…" if begin > 0 else "") + piece + ("…" if begin + SNIPPET < len(flat) else "")


@dataclass(slots=True)
class SearchResult:
    query: str
    tasks: list[Task]
    projects: list[Project]
    areas: list[Area]
    resources: list[Resource]
    journal: list[tuple[date, str]]


async def search(db: AsyncSession, user_id: uuid.UUID, query: str, include_done: bool) -> SearchResult:
    q = " ".join(query.split())
    needle = q.lower().replace("ё", "е")
    ts_ru = func.plainto_tsquery(cast("russian", REGCONFIG), q)
    ts_en = func.plainto_tsquery(cast("english", REGCONFIG), q)
    title = _norm(Task.title)
    partial = title.contains(needle, autoescape=True)
    fts = or_(Task.search_vector_ru.op("@@")(ts_ru), Task.search_vector_en.op("@@")(ts_en))
    rank = (
        func.ts_rank(func.coalesce(Task.search_vector_ru, func.to_tsvector("")), ts_ru)
        + func.ts_rank(func.coalesce(Task.search_vector_en, func.to_tsvector("")), ts_en)
        + case((partial, 0.5), else_=0.0)
        + case((title.startswith(needle, autoescape=True), 0.3), else_=0.0)
    )
    conditions = [Task.user_id == user_id, or_(fts, partial)]
    if not include_done:
        conditions.append(OPEN)
    tasks = (
        await db.scalars(
            task_query()
            .where(*conditions)
            .order_by(case((Task.status.in_(CLOSED), 1), else_=0), rank.desc(), Task.updated_at.desc())
            .limit(SEARCH_TASKS)
            .execution_options(populate_existing=True)
        )
    ).all()

    def like(*columns):
        return or_(*(_norm(column).contains(needle, autoescape=True) for column in columns))

    project_filter = [Project.user_id == user_id, like(Project.name, Project.outcome, Project.description_md)]
    area_filter = [Area.user_id == user_id, like(Area.name, Area.description)]
    resource_filter = [
        Resource.user_id == user_id,
        like(Resource.name, Resource.notes_md, Resource.url, cast(Resource.tags, String)),
    ]
    if not include_done:
        project_filter.append(Project.is_archived.is_(False))
        area_filter.append(Area.is_archived.is_(False))
        resource_filter.append(Resource.is_archived.is_(False))
    projects = (
        await db.scalars(
            select(Project)
            .where(*project_filter)
            .order_by(
                case((Project.status.in_((ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD)), 0), else_=1), Project.name
            )
            .limit(SEARCH_OTHERS)
        )
    ).all()
    areas = (
        await db.scalars(select(Area).where(*area_filter).order_by(Area.sort_order, Area.name).limit(SEARCH_OTHERS))
    ).all()
    resources = (
        await db.scalars(
            select(Resource).where(*resource_filter).order_by(Resource.created_at.desc()).limit(SEARCH_OTHERS)
        )
    ).all()

    text = func.concat_ws(" ", JournalEntry.note_md, JournalEntry.wins_md, JournalEntry.improvements_md)
    journal_rows = (
        await db.execute(
            select(JournalEntry.entry_date, JournalEntry.note_md, JournalEntry.wins_md, JournalEntry.improvements_md)
            .where(
                JournalEntry.user_id == user_id,
                or_(
                    _norm(text).contains(needle, autoescape=True),
                    func.to_tsvector(cast("russian", REGCONFIG), text).op("@@")(ts_ru),
                    func.to_tsvector(cast("english", REGCONFIG), text).op("@@")(ts_en),
                ),
            )
            .order_by(JournalEntry.entry_date.desc())
            .limit(SEARCH_JOURNAL)
        )
    ).all()
    journal = []
    for row in journal_rows:
        fields = [value for value in (row.note_md, row.wins_md, row.improvements_md) if value]
        hit = next(
            (value for value in fields if needle in value.lower().replace("ё", "е")), fields[0] if fields else ""
        )
        journal.append((row.entry_date, _snippet(hit, needle)))
    return SearchResult(
        query=q,
        tasks=list(tasks),
        projects=list(projects),
        areas=list(areas),
        resources=list(resources),
        journal=journal,
    )
