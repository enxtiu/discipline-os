"""Planner (stage 6): tasks, quick add, views, counts and search.

Areas/projects/resources/tags live in ``planner_org``, time tracking and the
journal in ``planner_time`` — all under ``/planner``, all device-only, all
writes limited per user (``planner:{user_id}``, 240/min).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.exc import DBAPIError

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import area_out as planner_area_out
from app.api.v1.presenters import (
    counts_out,
    occurrence_out,
    parse_out,
    project_out,
    resource_out,
    running_timer_out,
    tag_out,
    task_detail_out,
    task_out,
)
from app.core.errors import Conflict, RateLimited
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.schemas.planner import (
    EARLIEST_DATE,
    LATEST_DATE,
    ArchiveViewOut,
    CalendarDayOut,
    CalendarViewOut,
    CountsOut,
    InboxViewOut,
    JournalSearchHitOut,
    ParseIn,
    ParseOut,
    QuickAddIn,
    QuickAddOut,
    SearchOut,
    TagViewOut,
    TaskCompleteIn,
    TaskCompleteOut,
    TaskDetailOut,
    TaskIn,
    TaskOut,
    TaskPatchIn,
    TaskPatchResultOut,
    TaskReopenOut,
    TodayViewOut,
    UpcomingDayOut,
    UpcomingViewOut,
)
from app.services.planner import containers, loader, quick_add, tasks, views
from app.services.planner.common import Clock, validation_error
from app.services.planner.time_tracking import running_entry

router = APIRouter(prefix="/planner", tags=["planner"], dependencies=[Depends(require_device)])
log = get_logger(__name__)

# writes per user and minute: a busy planning session is a few per second at most;
# this only stops a runaway client or a replay loop
WRITES_PER_MINUTE = 240
# deadlock_detected, serialization_failure: PostgreSQL aborted the transaction, a retry succeeds
RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})


async def _planner_write(principal: CurrentPrincipal) -> AsyncIterator[None]:
    """Every planner write: the per-user rate limit, and a transaction PostgreSQL aborted to
    break a deadlock answers a retryable ``409 conflict`` (``details.retryable``) instead of a 500."""
    allowed, retry_after = await limiter.hit(f"planner:{principal.user_id}", WRITES_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many planner changes, please slow down")
    try:
        yield
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) not in RETRYABLE_SQLSTATES:
            raise
        log.warning("planner_write_retryable", sqlstate=exc.orig.sqlstate)
        raise Conflict(
            "A concurrent change got in the way, please retry", code="conflict", details={"retryable": True}
        ) from None


Write = [Depends(_planner_write)]
TaskId = Annotated[uuid.UUID, Path(alias="id", description="task id")]


def clock_for(principal: CurrentPrincipal) -> Clock:
    return Clock.for_tz(principal.settings_row.timezone if principal.settings_row else None)


def week_start_of(principal: CurrentPrincipal) -> int:
    return principal.settings_row.week_start if principal.settings_row else 1


def sane_day(value: date | None, name: str = "date") -> date | None:
    """Query/path dates: a year-9999 day would overflow a day boundary into a 500."""
    if value is not None and not EARLIEST_DATE <= value <= LATEST_DATE:
        raise validation_error(f"{name} must be between {EARLIEST_DATE} and {LATEST_DATE}")
    return value


async def task_list(db, principal: CurrentPrincipal, rows, clock: Clock) -> list[TaskOut]:
    refs = await loader.load_refs(db, principal.user_id, rows, clock)
    return [task_out(row, refs) for row in rows]


async def detail(db, principal: CurrentPrincipal, task, clock: Clock) -> TaskDetailOut:
    return task_detail_out(await loader.load_tree(db, principal.user_id, task, clock))


async def single(db, principal: CurrentPrincipal, task, clock: Clock) -> TaskOut | None:
    if task is None:
        return None
    fresh = await loader.reload_tasks(db, principal.user_id, [task.id])
    if not fresh:
        return None
    return (await task_list(db, principal, fresh, clock))[0]


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
@router.post(
    "/tasks",
    response_model=TaskDetailOut,
    status_code=201,
    summary="Create a task (idempotent by client id)",
    dependencies=Write,
)
async def create_task(payload: TaskIn, principal: CurrentPrincipal, db: DbSession) -> TaskDetailOut:
    clock = clock_for(principal)
    task = await tasks.create_task(db, user_id=principal.user_id, clock=clock, payload=payload)
    return await detail(db, principal, task, clock)


@router.post(
    "/tasks/quick",
    response_model=QuickAddOut,
    status_code=201,
    summary="Quick add: parse a typed line (ru/en) and create the task",
    dependencies=Write,
)
async def quick(payload: QuickAddIn, principal: CurrentPrincipal, db: DbSession) -> QuickAddOut:
    clock = clock_for(principal)
    task, parsed = await quick_add.quick_add(
        db, user_id=principal.user_id, clock=clock, week_start=week_start_of(principal), payload=payload
    )
    return QuickAddOut(
        task=await detail(db, principal, task, clock), parsed=parse_out(parsed, quick_add.preset_for(parsed))
    )


@router.post("/parse", response_model=ParseOut, summary="Parse a quick-add line without saving anything")
async def parse(payload: ParseIn, principal: CurrentPrincipal, db: DbSession) -> ParseOut:
    parsed = await quick_add.parse(
        db,
        user_id=principal.user_id,
        clock=clock_for(principal),
        week_start=week_start_of(principal),
        text=payload.text,
        captured_at=payload.captured_at,
    )
    return parse_out(parsed, quick_add.preset_for(parsed))


@router.get("/tasks/{id}", response_model=TaskDetailOut, summary="A task with its subtasks and time")
async def get_task(task_id: TaskId, principal: CurrentPrincipal, db: DbSession) -> TaskDetailOut:
    clock = clock_for(principal)
    return await detail(db, principal, await tasks.get_task(db, principal.user_id, task_id), clock)


@router.patch(
    "/tasks/{id}",
    response_model=TaskPatchResultOut,
    summary="Edit a task (status changes complete or reopen it)",
    dependencies=Write,
)
async def patch_task(
    task_id: TaskId, payload: TaskPatchIn, principal: CurrentPrincipal, db: DbSession
) -> TaskPatchResultOut:
    clock = clock_for(principal)
    result = await tasks.update_task(db, user_id=principal.user_id, clock=clock, task_id=task_id, payload=payload)
    fresh = (await loader.reload_tasks(db, principal.user_id, [result.task.id]))[0]
    return TaskPatchResultOut(
        task=await detail(db, principal, fresh, clock),
        next_instance=await single(db, principal, result.next_instance, clock),
        removed_next_instance_id=result.removed_next_instance_id,
    )


@router.post(
    "/tasks/{id}/complete",
    response_model=TaskCompleteOut,
    summary="Complete a task (a recurring one gets its next instance, once)",
    dependencies=Write,
)
async def complete_task(
    task_id: TaskId, principal: CurrentPrincipal, db: DbSession, payload: TaskCompleteIn | None = None
) -> TaskCompleteOut:
    clock = clock_for(principal)
    task, following = await tasks.complete(
        db,
        user_id=principal.user_id,
        clock=clock,
        task_id=task_id,
        completed_at=payload.completed_at if payload is not None else None,
    )
    out = await single(db, principal, task, clock)
    assert out is not None
    return TaskCompleteOut(task=out, next_instance=await single(db, principal, following, clock))


@router.post(
    "/tasks/{id}/reopen",
    response_model=TaskReopenOut,
    summary="Reopen a completed task (an untouched next instance is removed)",
    dependencies=Write,
)
async def reopen_task(task_id: TaskId, principal: CurrentPrincipal, db: DbSession) -> TaskReopenOut:
    clock = clock_for(principal)
    task, removed = await tasks.reopen(db, user_id=principal.user_id, clock=clock, task_id=task_id)
    out = await single(db, principal, task, clock)
    assert out is not None
    return TaskReopenOut(task=out, removed_next_instance_id=removed)


@router.delete(
    "/tasks/{id}",
    status_code=204,
    response_class=Response,
    summary="Delete a task and its subtasks (time entries stay)",
    dependencies=Write,
)
async def delete_task(task_id: TaskId, principal: CurrentPrincipal, db: DbSession) -> Response:
    await tasks.delete_task(db, user_id=principal.user_id, task_id=task_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
@router.get("/counts", response_model=CountsOut, summary="Badges: inbox, today, overdue, timer")
async def get_counts(principal: CurrentPrincipal, db: DbSession) -> CountsOut:
    return counts_out(await views.counts(db, principal.user_id, clock_for(principal)))


@router.get("/views/today", response_model=TodayViewOut, summary="Overdue, due today, scheduled blocks, done")
async def today_view(
    principal: CurrentPrincipal,
    db: DbSession,
    day: date | None = Query(default=None, alias="date", description="local day; default: your today"),
) -> TodayViewOut:
    clock = clock_for(principal)
    sane_day(day)
    view = await views.today_view(db, principal.user_id, clock, day or clock.today)
    every = list({task.id: task for task in [*view.overdue, *view.today, *view.scheduled, *view.done]}.values())
    refs = await loader.load_refs(db, principal.user_id, every, clock)
    running = await running_entry(db, principal.user_id)
    timer = None
    if running is not None:
        timer = running_timer_out(running, await loader.load_entry_refs(db, principal.user_id, [running], clock))
    return TodayViewOut(
        date=view.day,
        overdue=[task_out(task, refs) for task in view.overdue],
        today=[task_out(task, refs) for task in view.today],
        scheduled=[task_out(task, refs) for task in view.scheduled],
        done=[task_out(task, refs) for task in view.done],
        timer=timer,
        counts=counts_out(await views.counts(db, principal.user_id, clock)),
    )


@router.get("/views/inbox", response_model=InboxViewOut, summary="Captured, not yet clarified (newest first)")
async def inbox_view(principal: CurrentPrincipal, db: DbSession) -> InboxViewOut:
    clock = clock_for(principal)
    rows = await views.inbox(db, principal.user_id)
    return InboxViewOut(items=await task_list(db, principal, rows, clock))


@router.get("/views/upcoming", response_model=UpcomingViewOut, summary="Day by day: tasks and recurring occurrences")
async def upcoming_view(
    principal: CurrentPrincipal,
    db: DbSession,
    first: date | None = Query(default=None, alias="from", description="default: your today"),
    days: int = Query(default=14, ge=1, le=views.UPCOMING_MAX_DAYS),
) -> UpcomingViewOut:
    clock = clock_for(principal)
    start = sane_day(first, "from") or clock.today
    buckets = await views.upcoming(db, principal.user_id, clock, start, days)
    every = {task.id: task for bucket in buckets for task in bucket.tasks}
    every.update({item.task.id: item.task for bucket in buckets for item in bucket.occurrences})
    refs = await loader.load_refs(db, principal.user_id, list(every.values()), clock)
    return UpcomingViewOut(
        from_=buckets[0].day,
        to=buckets[-1].day,
        days=[
            UpcomingDayOut(
                date=bucket.day,
                tasks=[task_out(task, refs) for task in bucket.tasks],
                occurrences=[occurrence_out(item, refs) for item in bucket.occurrences],
            )
            for bucket in buckets
        ],
    )


@router.get("/views/calendar", response_model=CalendarViewOut, summary="Blocks and all-day tasks per day (≤ 62 days)")
async def calendar_view(
    principal: CurrentPrincipal,
    db: DbSession,
    first: date = Query(alias="from"),
    last: date = Query(alias="to"),
) -> CalendarViewOut:
    clock = clock_for(principal)
    sane_day(first, "from")
    sane_day(last, "to")
    buckets = await views.calendar(db, principal.user_id, clock, first, last)
    every = {task.id: task for bucket in buckets for task in [*bucket.blocks, *bucket.all_day]}
    every.update({item.task.id: item.task for bucket in buckets for item in bucket.occurrences})
    refs = await loader.load_refs(db, principal.user_id, list(every.values()), clock)
    return CalendarViewOut(
        from_=first,
        to=last,
        days=[
            CalendarDayOut(
                date=bucket.day,
                all_day=[task_out(task, refs) for task in bucket.all_day],
                blocks=[task_out(task, refs) for task in bucket.blocks],
                occurrences=[occurrence_out(item, refs) for item in bucket.occurrences],
                done_count=bucket.done_count,
            )
            for bucket in buckets
        ],
    )


@router.get("/views/tag/{tag_id}", response_model=TagViewOut, summary="Tasks with one tag")
async def tag_view(
    tag_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, include_done: bool = False
) -> TagViewOut:
    clock = clock_for(principal)
    tag, rows = await views.tag_view(db, principal.user_id, tag_id, include_done)
    open_counts = await containers.tag_open_counts(db, principal.user_id, [tag.id])
    return TagViewOut(tag=tag_out(tag, open_counts.get(tag.id, 0)), items=await task_list(db, principal, rows, clock))


async def project_list(db, principal: CurrentPrincipal, rows, clock: Clock):
    stats = await containers.project_stats(db, principal.user_id, rows, clock)
    return [project_out(row, stats) for row in rows]


async def area_list(db, principal: CurrentPrincipal, rows, clock: Clock):
    stats = await containers.area_stats(db, principal.user_id, rows, clock, week_start_of(principal))
    return [planner_area_out(row, stats) for row in rows]


async def resource_list(db, principal: CurrentPrincipal, rows):
    _, areas = await loader.project_area_refs(db, principal.user_id, [], [row.area_id for row in rows])
    return [resource_out(row, areas) for row in rows]


@router.get("/views/archive", response_model=ArchiveViewOut, summary="Archived containers, latest done/cancelled tasks")
async def archive_view(principal: CurrentPrincipal, db: DbSession) -> ArchiveViewOut:
    clock = clock_for(principal)
    archive = await views.archive(db, principal.user_id)
    return ArchiveViewOut(
        projects=await project_list(db, principal, archive.projects, clock),
        areas=await area_list(db, principal, archive.areas, clock),
        resources=await resource_list(db, principal, archive.resources),
        tasks=await task_list(db, principal, archive.tasks, clock),
    )


@router.get("/search", response_model=SearchOut, summary="Search tasks (ru/en), projects, areas, resources, journal")
async def search(
    principal: CurrentPrincipal,
    db: DbSession,
    q: str = Query(min_length=2, max_length=100),
    include_done: bool = False,
) -> SearchOut:
    if len(" ".join(q.split())) < 2 or "\x00" in q:
        raise validation_error("q needs at least 2 characters")
    clock = clock_for(principal)
    result = await views.search(db, principal.user_id, q, include_done)
    return SearchOut(
        query=result.query,
        tasks=await task_list(db, principal, result.tasks, clock),
        projects=await project_list(db, principal, result.projects, clock),
        areas=await area_list(db, principal, result.areas, clock),
        resources=await resource_list(db, principal, result.resources),
        journal=[JournalSearchHitOut(date=day, snippet=snippet) for day, snippet in result.journal],
    )
