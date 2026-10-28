"""Planner (stage 6): time tracking (timer, entries, summary) and the daily journal."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.planner import EDIT_CONFLICT, Write, clock_for, sane_day, week_start_of
from app.api.v1.presenters import (
    journal_day_summary_out,
    journal_has_note_out,
    journal_out,
    planner_ref_out,
    running_timer_out,
    time_entry_out,
)
from app.schemas.planner import (
    AreaMinutesOut,
    DayMinutesOut,
    JournalDayOut,
    JournalIn,
    JournalListItemOut,
    JournalListOut,
    JournalOut,
    ProjectMinutesOut,
    SummaryPeriodLiteral,
    TimeEntryIn,
    TimeEntryListOut,
    TimeEntryOut,
    TimeEntryPatchIn,
    TimerStartIn,
    TimerStartOut,
    TimerStateOut,
    TimerStopIn,
    TimerStopOut,
    TimeSummaryOut,
)
from app.services.planner import conflicts, journal, loader, time_tracking
from app.services.planner.common import Clock, advisory_lock, minutes

router = APIRouter(prefix="/planner", tags=["planner"], dependencies=[Depends(require_device)])
EntryId = Annotated[uuid.UUID, Path(alias="id", description="time entry id")]
JournalDay = Annotated[date, Path(alias="date", description="local day, YYYY-MM-DD")]


async def _entries(db, principal: CurrentPrincipal, rows, clock: Clock) -> list[TimeEntryOut]:
    refs = await loader.load_entry_refs(db, principal.user_id, rows, clock)
    return [time_entry_out(row, refs) for row in rows]


async def _running(db, principal: CurrentPrincipal, entry, clock: Clock):
    if entry is None:
        return None
    return running_timer_out(entry, await loader.load_entry_refs(db, principal.user_id, [entry], clock))


# --------------------------------------------------------------------------- #
# Timer
# --------------------------------------------------------------------------- #
@router.get("/timer", response_model=TimerStateOut, summary="The running timer (survives reloads and devices)")
async def get_timer(principal: CurrentPrincipal, db: DbSession) -> TimerStateOut:
    clock = clock_for(principal)
    running = await time_tracking.running_entry(db, principal.user_id)
    return TimerStateOut(running=await _running(db, principal, running, clock), server_now=clock.now)


@router.post(
    "/timer/start",
    response_model=TimerStartOut,
    summary="Start a timer (a running one is stopped at the new start: switch)",
    dependencies=Write,
)
async def start_timer(payload: TimerStartIn, principal: CurrentPrincipal, db: DbSession) -> TimerStartOut:
    clock = clock_for(principal)
    state = await time_tracking.start_timer(db, user_id=principal.user_id, clock=clock, payload=payload)
    rows = [row for row in (state.entry, state.stopped) if row is not None]
    refs = await loader.load_entry_refs(db, principal.user_id, rows, clock)
    assert state.entry is not None
    return TimerStartOut(
        entry=time_entry_out(state.entry, refs),
        running=await _running(db, principal, state.running, clock),
        stopped=time_entry_out(state.stopped, refs) if state.stopped is not None else None,
    )


@router.post(
    "/timer/stop", response_model=TimerStopOut, summary="Stop the running timer (idempotent)", dependencies=Write
)
async def stop_timer(principal: CurrentPrincipal, db: DbSession, payload: TimerStopIn | None = None) -> TimerStopOut:
    clock = clock_for(principal)
    entry = await time_tracking.stop_timer(db, user_id=principal.user_id, clock=clock, payload=payload or TimerStopIn())
    stopped = (await _entries(db, principal, [entry], clock))[0] if entry is not None else None
    return TimerStopOut(stopped=stopped, server_now=clock.now)


# --------------------------------------------------------------------------- #
# Entries & summary
# --------------------------------------------------------------------------- #
@router.get("/time-entries", response_model=TimeEntryListOut, summary="Time entries by their start day (≤ 62 days)")
async def list_entries(
    principal: CurrentPrincipal,
    db: DbSession,
    first: date = Query(alias="from"),
    last: date = Query(alias="to"),
) -> TimeEntryListOut:
    clock = clock_for(principal)
    sane_day(first, "from")
    sane_day(last, "to")
    rows = await time_tracking.list_entries(db, user_id=principal.user_id, clock=clock, first=first, last=last)
    per_day = time_tracking.by_day(rows, first, last, clock)
    return TimeEntryListOut(
        from_=first,
        to=last,
        items=await _entries(db, principal, rows, clock),
        total_minutes=minutes(sum(seconds for _, seconds in per_day)),
        by_day=[DayMinutesOut(date=day, minutes=minutes(seconds)) for day, seconds in per_day],
    )


@router.post(
    "/time-entries",
    response_model=TimeEntryOut,
    status_code=201,
    summary="Log time by hand (local date, start and end)",
    dependencies=Write,
)
async def create_entry(payload: TimeEntryIn, principal: CurrentPrincipal, db: DbSession) -> TimeEntryOut:
    clock = clock_for(principal)
    entry = await time_tracking.create_entry(db, user_id=principal.user_id, clock=clock, payload=payload)
    return (await _entries(db, principal, [entry], clock))[0]


@router.patch(
    "/time-entries/{id}",
    response_model=TimeEntryOut,
    summary="Correct a time entry",
    dependencies=Write,
    responses=EDIT_CONFLICT,
)
async def patch_entry(
    entry_id: EntryId, payload: TimeEntryPatchIn, principal: CurrentPrincipal, db: DbSession
) -> TimeEntryOut:
    clock = clock_for(principal)
    if payload.base is not None:  # compare-and-set (stage 9), in update_entry's lock order
        await advisory_lock(db, principal.user_id, time_tracking.LOCK_SCOPE)
        locked = await time_tracking._own_entry(db, principal.user_id, entry_id)
        current = (await _entries(db, principal, [locked], clock))[0]
        values = conflicts.time_entry_values(
            current, task_id=locked.task_id, project_id=locked.project_id, area_id=locked.area_id
        )
        conflicts.check_base("time_entry", payload, values, current)
    entry = await time_tracking.update_entry(
        db, user_id=principal.user_id, clock=clock, entry_id=entry_id, payload=payload
    )
    return (await _entries(db, principal, [entry], clock))[0]


@router.delete(
    "/time-entries/{id}",
    status_code=204,
    response_class=Response,
    summary="Delete a time entry",
    dependencies=Write,
)
async def delete_entry(entry_id: EntryId, principal: CurrentPrincipal, db: DbSession) -> Response:
    await time_tracking.delete_entry(db, user_id=principal.user_id, entry_id=entry_id)
    return Response(status_code=204)


@router.get("/time/summary", response_model=TimeSummaryOut, summary="Tracked time by area, project and day")
async def summary(
    principal: CurrentPrincipal,
    db: DbSession,
    period: SummaryPeriodLiteral = "week",
    day: date | None = Query(default=None, alias="date", description="any day of the period; default: today"),
) -> TimeSummaryOut:
    clock = clock_for(principal)
    sane_day(day)
    result = await time_tracking.summary(
        db,
        user_id=principal.user_id,
        clock=clock,
        period=period,
        day=day or clock.today,
        week_start=week_start_of(principal),
    )
    return TimeSummaryOut(
        period=period,
        from_=result.first,
        to=result.last,
        total_minutes=minutes(result.total_seconds),
        by_area=[
            AreaMinutesOut(
                area=planner_ref_out((area.id, area.name, area.color)) if area is not None else None,
                minutes=minutes(seconds),
                target_minutes=target,
            )
            for area, seconds, target in result.by_area
        ],
        by_project=[
            ProjectMinutesOut(
                project=planner_ref_out((project.id, project.name, project.color)) if project is not None else None,
                minutes=minutes(seconds),
            )
            for project, seconds in result.by_project
        ],
        by_day=[DayMinutesOut(date=day_, minutes=minutes(seconds)) for day_, seconds in result.by_day],
    )


# --------------------------------------------------------------------------- #
# Journal
# --------------------------------------------------------------------------- #
@router.get("/journal", response_model=JournalListOut, summary="Journal days in a range (≤ 366 days)")
async def list_journal(
    principal: CurrentPrincipal,
    db: DbSession,
    first: date = Query(alias="from"),
    last: date = Query(alias="to"),
) -> JournalListOut:
    sane_day(first, "from")
    sane_day(last, "to")
    rows = await journal.list_range(db, user_id=principal.user_id, first=first, last=last)
    return JournalListOut(
        items=[
            JournalListItemOut(
                date=row.entry_date,
                rating=row.rating,
                mood=row.mood,
                energy=row.energy,
                has_note=journal_has_note_out(row),
            )
            for row in rows
        ]
    )


@router.get("/journal/{date}", response_model=JournalDayOut, summary="A day's journal entry and its summary")
async def get_journal(day: JournalDay, principal: CurrentPrincipal, db: DbSession) -> JournalDayOut:
    clock = clock_for(principal)
    journal.check_day(day, clock)
    entry = await journal.get_entry(db, principal.user_id, day)
    summary_ = await journal.day_summary(
        db, user_id=principal.user_id, clock=clock, day=day, settings_row=principal.settings_row
    )
    return JournalDayOut(entry=journal_out(entry) if entry is not None else None, day=journal_day_summary_out(summary_))


@router.put(
    "/journal/{date}",
    response_model=JournalOut,
    summary="Write the day's journal (fields not sent keep their value)",
    dependencies=Write,
    responses=EDIT_CONFLICT,
)
async def put_journal(day: JournalDay, payload: JournalIn, principal: CurrentPrincipal, db: DbSession) -> JournalOut:
    clock = clock_for(principal)
    if payload.base is not None:
        # compare-and-set (stage 9): one base-carrying write per day at a time; no entry = every field empty
        journal.check_day(day, clock)
        await advisory_lock(db, principal.user_id, f"journal:{day.isoformat()}")
        existing = await journal.get_entry(db, principal.user_id, day, lock=True)
        current = journal_out(existing) if existing is not None else None
        conflicts.check_base("journal", payload, conflicts.journal_values(current), current, ignore=("base", "id"))
    entry = await journal.upsert(db, user_id=principal.user_id, clock=clock, day=day, payload=payload)
    return journal_out(entry)


@router.delete(
    "/journal/{date}",
    status_code=204,
    response_class=Response,
    summary="Delete the day's journal entry",
    dependencies=Write,
)
async def delete_journal(day: JournalDay, principal: CurrentPrincipal, db: DbSession) -> Response:
    await journal.delete_entry(db, user_id=principal.user_id, clock=clock_for(principal), day=day)
    return Response(status_code=204)
