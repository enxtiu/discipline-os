"""XP: a deterministic, append-only ledger (``xp_events``), never negative.

Day awards are a pure function of that day's data. They are synced for the
**open window** (today and yesterday in the user's timezone): missing awards are
inserted, awards whose source disappeared (a deleted meal, a reopened task) are
removed, changed ones updated. Older days are frozen — each is synced one last
time after its grace day ended and never again. Streak-milestone, quest and
achievement awards are permanent. Every row has a ``dedupe_key``, so replays and
overlapping runs never duplicate.

=================== ====== ===================================================
kind                points rule
=================== ====== ===================================================
meal_logged         5      per meal, at most 5 meals a day (logging, not eating)
protein_target      20     protein ≥ target *and* intake ≥ the safe floor
fiber_target        10     fiber ≥ target *and* intake ≥ the safe floor
water_target        10     water ≥ target
kcal_in_zone        30     finished days only, kcal inside the zone (≥ floor)
steps_target        15     steps ≥ target
workout_completed   40     per completed workout, at most 2 a day
workout_volume      15     per new personal record, at most 3 a day
task_completed      P1 12 · P2 9 · P3 6 · P4 4 · subtask 2, at most 120 a day
project_completed   60     per project completed that day
timer_session       1      per 10 finished tracked minutes, at most 48 a day
daily_review        15     journal entry with content
sleep_logged        5      a sleep entry for the day
weight_logged       5      a weigh-in that day
=================== ====== ===================================================

``settings.xp_weights.xp_multipliers`` scales a category by 0..2 (default 1;
rounded half up; a 0-point award is not stored).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models import XpEvent, XpKind
from app.services import day_metrics
from app.services.day_metrics import DayMetrics, MealRef, ProjectRef, RecordRef, TaskRef, WorkoutRef
from app.services.gamification.context import GameContext

MEAL_POINTS, MEALS_PER_DAY = 5, 5
PROTEIN_POINTS = 20
FIBER_POINTS = 10
WATER_POINTS = 10
KCAL_ZONE_POINTS = 30
STEPS_POINTS = 15
WORKOUT_POINTS, WORKOUTS_PER_DAY = 40, 2
RECORD_POINTS, RECORDS_PER_DAY = 15, 3
TASK_POINTS = {1: 12, 2: 9, 3: 6, 4: 4}
SUBTASK_POINTS = 2
TASKS_CAP = 120
PROJECT_POINTS = 60
TIMER_STEP_MINUTES, TIMER_CAP = 10, 48
REVIEW_POINTS = 15
SLEEP_POINTS = 5
WEIGHT_POINTS = 5
CHUNK = 400  # rows per INSERT (bind-parameter limit)

CATEGORY: dict[XpKind, str] = {
    XpKind.MEAL_LOGGED: "nutrition",
    XpKind.PROTEIN_TARGET: "nutrition",
    XpKind.FIBER_TARGET: "nutrition",
    XpKind.WATER_TARGET: "nutrition",
    XpKind.KCAL_IN_ZONE: "nutrition",
    XpKind.WORKOUT_COMPLETED: "training",
    XpKind.WORKOUT_VOLUME: "training",
    XpKind.STEPS_TARGET: "activity",
    XpKind.SLEEP_LOGGED: "activity",
    XpKind.WEIGHT_LOGGED: "activity",
    XpKind.TASK_COMPLETED: "planner",
    XpKind.PROJECT_COMPLETED: "planner",
    XpKind.TIMER_SESSION: "planner",
    XpKind.DAILY_REVIEW: "review",
    XpKind.STREAK_BONUS: "bonus",
    XpKind.QUEST_COMPLETED: "bonus",
    XpKind.ACHIEVEMENT_UNLOCKED: "bonus",
}
# derived from a day's data — synced in the open window, frozen afterwards
DAY_KINDS: frozenset[XpKind] = frozenset(kind for kind, category in CATEGORY.items() if category != "bonus")
# never revoked
PERMANENT_KINDS: frozenset[XpKind] = frozenset(
    {XpKind.STREAK_BONUS, XpKind.QUEST_COMPLETED, XpKind.ACHIEVEMENT_UNLOCKED, XpKind.MANUAL}
)


def scaled(points: int, kind: XpKind, multipliers: dict[str, float]) -> int:
    """Apply the category multiplier (0..2), half up; never negative."""
    factor = max(0.0, float(multipliers.get(CATEGORY.get(kind, "bonus"), 1.0)))
    value = (Decimal(points) * Decimal(str(factor))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return max(0, int(value))


@dataclass(slots=True, frozen=True)
class Award:
    key: str
    kind: XpKind
    points: int
    day: date
    reason: str | None = None
    ref_type: str | None = None
    ref_id: uuid.UUID | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DayRefs:
    meals: list[MealRef] = field(default_factory=list)
    workouts: list[WorkoutRef] = field(default_factory=list)
    records: list[RecordRef] = field(default_factory=list)
    tasks: list[TaskRef] = field(default_factory=list)
    projects: list[ProjectRef] = field(default_factory=list)


async def load_refs(db: AsyncSession, ctx: GameContext, first: date, last: date) -> dict[date, DayRefs]:
    """The rows behind entity awards for a range (five queries)."""
    out: dict[date, DayRefs] = defaultdict(DayRefs)
    for meal in await day_metrics.logged_meals(db, ctx.user_id, first, last):
        out[meal.local_day].meals.append(meal)
    for workout in await day_metrics.completed_workouts(db, ctx.user_id, first, last):
        out[workout.local_day].workouts.append(workout)
    for record in await day_metrics.personal_records(db, ctx.user_id, first, last):
        out[record.local_day].records.append(record)
    for task in await day_metrics.completed_tasks(db, ctx.user_id, first, last, ctx.tz):
        out[task.local_day].tasks.append(task)
    for project in await day_metrics.completed_projects(db, ctx.user_id, first, last, ctx.tz):
        out[project.local_day].projects.append(project)
    return out


def day_awards(day: date, m: DayMetrics, refs: DayRefs | None, ctx: GameContext, *, finished: bool) -> list[Award]:
    """Everything a day earns (pure). ``finished``: the day has ended (kcal_in_zone)."""
    refs = refs or DayRefs()
    t, iso = ctx.targets, day.isoformat()
    raw: list[Award] = []

    for meal in refs.meals[:MEALS_PER_DAY]:
        raw.append(Award(f"meal:{meal.id}", XpKind.MEAL_LOGGED, MEAL_POINTS, day, "meal_logged", "meal", meal.id))

    intake_ok = m.meals >= 1 and m.kcal >= ctx.floor  # intake-tied awards never below the floor
    if t.protein_g and intake_ok and m.protein_g >= t.protein_g:
        raw.append(Award(f"protein:{iso}", XpKind.PROTEIN_TARGET, PROTEIN_POINTS, day, "protein_target"))
    if t.fiber_g and intake_ok and m.fiber_g >= t.fiber_g:
        raw.append(Award(f"fiber:{iso}", XpKind.FIBER_TARGET, FIBER_POINTS, day, "fiber_target"))
    if t.water_ml and m.water_ml >= t.water_ml:
        raw.append(Award(f"water:{iso}", XpKind.WATER_TARGET, WATER_POINTS, day, "water_target"))
    if finished and m.meals >= 1 and ctx.zone.contains(m.kcal):  # zone_low ≥ floor by construction
        raw.append(Award(f"kcal_zone:{iso}", XpKind.KCAL_IN_ZONE, KCAL_ZONE_POINTS, day, "kcal_in_zone"))
    if t.steps and m.steps is not None and m.steps >= t.steps:
        raw.append(Award(f"steps:{iso}", XpKind.STEPS_TARGET, STEPS_POINTS, day, "steps_target"))

    for workout in refs.workouts[:WORKOUTS_PER_DAY]:
        raw.append(
            Award(
                f"workout:{workout.id}",
                XpKind.WORKOUT_COMPLETED,
                WORKOUT_POINTS,
                day,
                "workout_completed",
                "workout",
                workout.id,
            )
        )
    for record in refs.records[:RECORDS_PER_DAY]:
        raw.append(
            Award(
                f"pr:{record.workout_id}:{record.exercise_id}:{record.kind}",
                XpKind.WORKOUT_VOLUME,
                RECORD_POINTS,
                day,
                "personal_record",
                "workout",
                record.workout_id,
                {"exercise_id": str(record.exercise_id), "record": record.kind},
            )
        )

    left = TASKS_CAP
    for task in refs.tasks:
        if left <= 0:
            break
        base = SUBTASK_POINTS if task.is_subtask else TASK_POINTS.get(task.priority, TASK_POINTS[4])
        points = min(base, left)
        left -= points
        raw.append(
            Award(
                f"task:{task.id}",
                XpKind.TASK_COMPLETED,
                points,
                day,
                "subtask_completed" if task.is_subtask else f"task_completed_p{task.priority}",
                "task",
                task.id,
            )
        )
    for project in refs.projects:
        raw.append(
            Award(
                f"project:{project.id}",
                XpKind.PROJECT_COMPLETED,
                PROJECT_POINTS,
                day,
                "project_completed",
                "project",
                project.id,
            )
        )

    timer = min(TIMER_CAP, int(m.tracked_seconds // 60) // TIMER_STEP_MINUTES)  # finished entries only
    if timer > 0:
        raw.append(Award(f"timer:{iso}", XpKind.TIMER_SESSION, timer, day, "timer_session"))
    if m.journal:
        raw.append(Award(f"review:{iso}", XpKind.DAILY_REVIEW, REVIEW_POINTS, day, "daily_review"))
    if m.sleep_minutes is not None:
        raw.append(Award(f"sleep:{iso}", XpKind.SLEEP_LOGGED, SLEEP_POINTS, day, "sleep_logged"))
    if m.weight_kg is not None:
        raw.append(Award(f"weight:{iso}", XpKind.WEIGHT_LOGGED, WEIGHT_POINTS, day, "weight_logged"))

    out = []
    for award in raw:
        points = scaled(award.points, award.kind, ctx.multipliers)
        if points > 0:
            out.append(
                Award(award.key, award.kind, points, day, award.reason, award.ref_type, award.ref_id, award.meta)
            )
    return out


@dataclass(slots=True)
class SyncReport:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0


def _row(ctx: GameContext, award: Award, awarded_on: date) -> dict[str, Any]:
    return {
        "id": new_id(),
        "user_id": ctx.user_id,
        "kind": award.kind,
        "points": award.points,
        "ref_type": award.ref_type,
        "ref_id": award.ref_id,
        "reason": award.reason,
        "awarded_on": awarded_on,
        "local_day": award.day,
        "meta": award.meta,
        "dedupe_key": award.key,
        "created_at": ctx.now,
        "updated_at": ctx.now,
    }


async def _insert(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    inserted = 0
    for start in range(0, len(rows), CHUNK):
        stmt = (
            pg_insert(XpEvent)
            .values(rows[start : start + CHUNK])
            .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
            .returning(XpEvent.id)
        )
        inserted += len((await db.execute(stmt)).all())
    return inserted


async def sync_days(db: AsyncSession, ctx: GameContext, days: Collection[date], awards: Iterable[Award]) -> SyncReport:
    """Make the day-derived XP of ``days`` exactly ``awards``. A key that already exists
    on a frozen day keeps that award (``ON CONFLICT DO NOTHING``): nothing is paid twice."""
    report = SyncReport()
    days = sorted(set(days))
    if not days:
        return report
    wanted = {award.key: award for award in awards if award.day in set(days) and award.points > 0}
    existing = (
        await db.scalars(
            select(XpEvent).where(
                XpEvent.user_id == ctx.user_id,
                XpEvent.local_day.in_(days),
                XpEvent.kind.in_(tuple(DAY_KINDS)),
            )
        )
    ).all()
    doomed = [row.id for row in existing if row.dedupe_key not in wanted]
    if doomed:
        for start in range(0, len(doomed), CHUNK):
            await db.execute(
                delete(XpEvent)
                .where(XpEvent.user_id == ctx.user_id, XpEvent.id.in_(doomed[start : start + CHUNK]))
                .execution_options(synchronize_session=False)
            )
        report.deleted = len(doomed)
    have = {row.dedupe_key: row for row in existing if row.dedupe_key in wanted}
    for key, row in have.items():
        award = wanted[key]
        if (row.points, row.local_day, row.reason, row.kind) != (award.points, award.day, award.reason, award.kind):
            await db.execute(
                update(XpEvent)
                .where(XpEvent.id == row.id, XpEvent.user_id == ctx.user_id)
                .values(
                    points=award.points,
                    kind=award.kind,
                    local_day=award.day,
                    awarded_on=award.day,
                    reason=award.reason,
                    meta=award.meta,
                    updated_at=ctx.now,
                )
                .execution_options(synchronize_session=False)
            )
            report.updated += 1
    rows = [_row(ctx, award, award.day) for key, award in wanted.items() if key not in have]
    report.inserted = await _insert(db, rows)
    return report


async def award_permanent(
    db: AsyncSession,
    ctx: GameContext,
    *,
    key: str,
    kind: XpKind,
    points: int,
    reason: str | None = None,
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    """Insert a permanent award (dated today) once; returns the points actually added
    (0 when the key was already paid, or when a multiplier of 0 makes it worthless)."""
    assert kind in PERMANENT_KINDS
    value = scaled(points, kind, ctx.multipliers)
    if value <= 0:
        return 0
    award = Award(key, kind, value, ctx.today, reason, ref_type, ref_id, meta or {})
    return value if await _insert(db, [_row(ctx, award, ctx.today)]) else 0


async def total(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(await db.scalar(select(func.coalesce(func.sum(XpEvent.points), 0)).where(XpEvent.user_id == user_id)))


async def per_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, int]:
    rows = await db.execute(
        select(XpEvent.local_day, func.sum(XpEvent.points))
        .where(XpEvent.user_id == user_id, XpEvent.local_day.between(first, last))
        .group_by(XpEvent.local_day)
    )
    return {row[0]: int(row[1] or 0) for row in rows}


async def events_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, list[XpEvent]]:
    rows = await db.scalars(
        select(XpEvent)
        .where(XpEvent.user_id == user_id, XpEvent.local_day.between(first, last))
        .order_by(XpEvent.local_day, XpEvent.created_at, XpEvent.dedupe_key)
    )
    out: dict[date, list[XpEvent]] = defaultdict(list)
    for row in rows.all():
        out[row.local_day].append(row)
    return out
