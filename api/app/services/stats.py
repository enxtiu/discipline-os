"""Stage 8 — the ``/stats/*`` series (charts and dashboards).

* **One calendar period per request**, in the user's timezone: the ``week``
  (following ``settings.week_start``, 0 = Sunday … 6 = Saturday), ``month``,
  ``quarter`` or ``year`` that contains the anchor day (default: today). Every day of
  the period is present, future days included — they carry no data; ``today`` tells
  the client where the future starts (it never computes a day boundary itself).
* **Ranges, not days.** Each series is a fixed handful of grouped range queries
  (``app.services.day_metrics`` plus a few of its own here), so a year costs what a
  week costs.
* **Missing is not zero.** A day without a meal has ``kcal: null`` (not logged), a
  day without a workout has ``workouts: 0`` (none happened) — every field of
  ``app.schemas.stats`` says which.
* **Exactly the score's numbers.** Targets, the safe floor and the calorie zone come
  from the gamification context (``context_for``), the same object the daily score
  uses, and every per-day verdict (``in_zone``, ``on_target``, the adherence counts)
  goes through the same predicates as the XP, streaks, quests and achievements: a
  finished day is judged against the targets it was finalised with (its stored score
  row), a finished week against the requirement frozen when it ended; the current
  targets (the top-level ``target_*``/``zone``/``floor``) apply to the open window
  (today, yesterday) and the current plan to the current week — as in the streaks.
* **Burned calories are never nutrition.** Workout kcal and Health's active energy
  are their own series in activity; nothing here adds them to intake or a target.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tz import day_range_utc, iter_days
from app.models import (
    Area,
    Exercise,
    PersonalRecord,
    Project,
    Streak,
    StreakKind,
    Task,
    TaskStatus,
    TimeEntry,
    WorkoutLog,
    WorkoutSet,
    WorkoutStatus,
)
from app.services import day_metrics, training
from app.services.day_metrics import WeekPlan, required_workouts
from app.services.gamification import score, streaks, xp
from app.services.gamification.context import GameContext
from app.services.planner.containers import OPEN_PROJECT, entry_seconds
from app.services.planner.containers import week_start_for as _week_start_for
from app.services.planner.tasks import CLOSED

PERIODS: tuple[str, ...] = ("week", "month", "quarter", "year")
# an area's weekly target over a period (week ×1, month ×4.35 as the stage-6 summary)
TARGET_FACTORS: dict[str, float] = {"week": 1.0, "month": 4.35, "quarter": 13.05, "year": 52.2}
RECORDS_LIMIT = 100
WEIGHT_AVG_DAYS = 7
CHANGE_DAYS, CHANGE_MAX_DAYS = 30, 45  # as the stage-5 weight series


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class Span:
    period: str
    first: date
    last: date
    today: date

    @property
    def days(self) -> list[date]:
        return list(iter_days(self.first, self.last))


def week_start_for(day: date, week_start: int) -> date:
    """First day of ``day``'s week (0 = Sunday … 6 = Saturday, as the planner)."""
    return _week_start_for(day, week_start)


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def span_for(period: str, anchor: date, week_start: int, today: date) -> Span:
    """The calendar period of ``period`` that contains ``anchor``."""
    if period == "week":
        first = week_start_for(anchor, week_start)
        last = first + timedelta(days=6)
    elif period == "month":
        first = anchor.replace(day=1)
        last = _add_months(first, 1) - timedelta(days=1)
    elif period == "quarter":
        first = date(anchor.year, 3 * ((anchor.month - 1) // 3) + 1, 1)
        last = _add_months(first, 3) - timedelta(days=1)
    elif period == "year":
        first, last = date(anchor.year, 1, 1), date(anchor.year, 12, 31)
    else:  # pragma: no cover - the API only accepts PERIODS
        raise ValueError(f"unknown period: {period}")
    return Span(period=period, first=first, last=last, today=today)


def _mean(values: list[float], digits: int = 1) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


# --------------------------------------------------------------------------- #
# Nutrition
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class NutritionDay:
    day: date
    kcal: float | None
    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None
    water_ml: int | None
    meals: int
    in_zone: bool | None


@dataclass(slots=True)
class NutritionSeries:
    span: Span
    ctx: GameContext
    days: list[NutritionDay]
    averages: dict[str, Any]
    adherence: dict[str, int]


async def nutrition(db: AsyncSession, ctx: GameContext, span: Span) -> NutritionSeries:
    """Food and water per day (three grouped queries). Burned calories are not here."""
    await score.load_frozen(db, ctx, span.first, span.last)  # finished days: their own targets
    food = await day_metrics.nutrition_by_day(db, ctx.user_id, span.first, span.last)
    water = await day_metrics.water_by_day(db, ctx.user_id, span.first, span.last)
    days: list[NutritionDay] = []
    adherence = {"zone_days": 0, "protein_days": 0, "water_days": 0, "logged_days": 0}
    finished: list[NutritionDay] = []
    water_finished: list[int] = []
    for day in span.days:
        n = food.get(day)
        ml = water.get(day)
        meals = n.meals if n is not None else 0
        logged = n is not None and meals > 0
        # the day's verdicts go through the same predicates as XP, streaks and achievements
        rules = ctx.rules_for(day)
        metrics = day_metrics.DayMetrics(
            day=day,
            kcal=n.kcal if n is not None else 0.0,
            protein_g=n.protein_g if n is not None else 0.0,
            fiber_g=n.fiber_g if n is not None else 0.0,
            meals=meals,
            water_ml=ml or 0,
        )
        in_zone = None
        if logged and day < span.today and rules.zone.target is not None:
            in_zone = rules.in_zone(metrics)
        item = NutritionDay(
            day=day,
            kcal=round(n.kcal, 2) if logged else None,
            protein_g=round(n.protein_g, 2) if logged else None,
            fat_g=round(n.fat_g, 2) if logged else None,
            carbs_g=round(n.carbs_g, 2) if logged else None,
            fiber_g=round(n.fiber_g, 2) if logged else None,
            water_ml=ml,
            meals=meals,
            in_zone=in_zone,
        )
        days.append(item)
        if day > span.today:
            continue
        if logged:
            adherence["logged_days"] += 1
            # nutrition credit (a meal at or above the floor) — the same rule as the XP,
            # the streaks, the quests and the achievements
            if rules.protein_met(metrics):
                adherence["protein_days"] += 1
            if in_zone:
                adherence["zone_days"] += 1
        if rules.water_met(metrics):
            adherence["water_days"] += 1
        if day < span.today:
            if logged:
                finished.append(item)
            if ml is not None:
                water_finished.append(ml)
    averages: dict[str, Any] = {
        "days": len(finished),
        "kcal": _mean([item.kcal or 0.0 for item in finished]),
        "protein_g": _mean([item.protein_g or 0.0 for item in finished]),
        "fat_g": _mean([item.fat_g or 0.0 for item in finished]),
        "carbs_g": _mean([item.carbs_g or 0.0 for item in finished]),
        "fiber_g": _mean([item.fiber_g or 0.0 for item in finished]),
        "meals": _mean([float(item.meals) for item in finished]),
        "water_days": len(water_finished),
        "water_ml": _mean([float(ml) for ml in water_finished]),
    }
    return NutritionSeries(span=span, ctx=ctx, days=days, averages=averages, adherence=adherence)


# --------------------------------------------------------------------------- #
# Activity
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ActivityDay:
    day: date
    steps: int | None
    on_target: bool | None
    workouts: int
    workout_kcal: float
    active_kcal: float | None


@dataclass(slots=True)
class ActivityWeek:
    week_start: date
    workouts: int
    planned: int
    required: int
    volume_kg: float


@dataclass(slots=True)
class MuscleVolume:
    muscle: str
    sets: int
    volume_kg: float


@dataclass(slots=True)
class ActivitySeries:
    span: Span
    ctx: GameContext
    days: list[ActivityDay]
    weeks: list[ActivityWeek]
    muscles: list[MuscleVolume]
    records: list[tuple[PersonalRecord, date]]
    records_total: int
    exercises: dict[uuid.UUID, Exercise] = field(default_factory=dict)


async def activity(db: AsyncSession, ctx: GameContext, span: Span) -> ActivitySeries:
    """Steps, workouts and burned calories per day, whole weeks against the plan (a
    finished week against the requirement frozen when it ended), working-set volume per
    muscle and the period's records (seven queries)."""
    user_id = ctx.user_id
    week_first = week_start_for(span.first, ctx.week_start)
    week_last = week_start_for(span.last, ctx.week_start) + timedelta(days=6)
    frozen_weeks = await score.load_frozen(db, ctx, week_first, week_last)
    health = await day_metrics.health_by_day(db, user_id, span.first, span.last)
    workouts = await day_metrics.workouts_by_day(db, user_id, week_first, week_last)
    plan = await day_metrics.plan_by_day(db, user_id, week_first, week_last)
    days = []
    for day in span.days:
        h = health.get(day)
        w = workouts.get(day)
        steps = h.steps if h is not None else None
        target_steps = ctx.rules_for(day).targets.steps
        days.append(
            ActivityDay(
                day=day,
                steps=steps,
                on_target=(steps >= target_steps) if steps is not None and target_steps else None,
                workouts=w.completed if w is not None else 0,
                workout_kcal=round(w.kcal_burned, 1) if w is not None else 0.0,
                active_kcal=round(h.active_kcal, 1) if h is not None and h.active_kcal is not None else None,
            )
        )
    weeks = []
    cursor = week_first
    while cursor <= week_last:
        week_days = [cursor + timedelta(days=offset) for offset in range(7)]
        week_plan = frozen_weeks.get(cursor) or WeekPlan(
            cursor,
            sum(1 for day in week_days if plan.get(day) == "workout"),
            any(day in plan for day in week_days),
        )
        weeks.append(
            ActivityWeek(
                week_start=cursor,
                workouts=sum(workouts[day].completed for day in week_days if day in workouts),
                planned=week_plan.planned,
                required=required_workouts(week_plan),
                volume_kg=round(sum(workouts[day].volume_kg for day in week_days if day in workouts), 2),
            )
        )
        cursor += timedelta(days=7)
    muscles = await _muscle_volume(db, user_id, span)
    records, total = await _records(db, user_id, span)
    exercises = await training.exercise_names(db, user_id, {record.exercise_id for record, _ in records})
    return ActivitySeries(
        span=span,
        ctx=ctx,
        days=days,
        weeks=weeks,
        muscles=muscles,
        records=records,
        records_total=total,
        exercises=exercises,
    )


def _completed_in(span: Span, user_id: uuid.UUID) -> tuple[Any, ...]:
    return (
        WorkoutLog.user_id == user_id,
        WorkoutLog.is_deleted.is_(False),
        WorkoutLog.status == WorkoutStatus.COMPLETED,
        WorkoutLog.local_day.between(span.first, span.last),
    )


async def _muscle_volume(db: AsyncSession, user_id: uuid.UUID, span: Span) -> list[MuscleVolume]:
    """Working sets and kg × reps per primary muscle group (one grouped query)."""
    volume = func.sum(func.coalesce(WorkoutSet.weight_kg, 0) * func.coalesce(WorkoutSet.reps, 0))
    rows = await db.execute(
        select(Exercise.muscle_group, func.count(), volume)
        .select_from(WorkoutSet)
        .join(WorkoutLog, WorkoutLog.id == WorkoutSet.workout_log_id)
        .join(Exercise, Exercise.id == WorkoutSet.exercise_id)
        .where(WorkoutSet.user_id == user_id, WorkoutSet.is_warmup.is_(False), *_completed_in(span, user_id))
        .group_by(Exercise.muscle_group)
    )
    out = [
        MuscleVolume(muscle=getattr(muscle, "value", muscle), sets=int(sets or 0), volume_kg=round(float(kg or 0), 2))
        for muscle, sets, kg in rows
    ]
    return sorted(out, key=lambda item: (-item.volume_kg, -item.sets, item.muscle))


async def _records(db: AsyncSession, user_id: uuid.UUID, span: Span) -> tuple[list[tuple[PersonalRecord, date]], int]:
    """Records set by the period's completed workouts, newest first, one per
    (workout, exercise, kind) — stage 4 re-detects records with new ids on edits."""
    rows = await db.execute(
        select(PersonalRecord, WorkoutLog.local_day)
        .join(WorkoutLog, WorkoutLog.id == PersonalRecord.workout_log_id)
        .where(PersonalRecord.user_id == user_id, *_completed_in(span, user_id))
        .order_by(PersonalRecord.achieved_at.desc(), PersonalRecord.id.desc())
    )
    seen: set[tuple[Any, ...]] = set()
    out: list[tuple[PersonalRecord, date]] = []
    for record, day in rows:
        key = (record.workout_log_id, record.exercise_id, record.kind)
        if key not in seen:
            seen.add(key)
            out.append((record, day))
    return out[:RECORDS_LIMIT], len(out)


# --------------------------------------------------------------------------- #
# Body
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class WeightPoint:
    day: date
    kg: float | None
    avg7: float | None


@dataclass(slots=True)
class BodySeries:
    span: Span
    weight: list[WeightPoint]
    change_30d: float | None
    sleep: list[tuple[date, int | None]]
    sleep_avg: int | None


async def body(db: AsyncSession, ctx: GameContext, span: Span) -> BodySeries:
    """Weigh-ins with a 7-day mean, the 30-day change and sleep per night (two queries)."""
    weights = await day_metrics.weigh_ins_by_day(
        db, ctx.user_id, span.first - timedelta(days=CHANGE_MAX_DAYS), span.last
    )
    nights = await day_metrics.sleep_by_day(db, ctx.user_id, span.first, span.last)
    points = []
    for day in span.days:
        window = [weights[d] for d in iter_days(day - timedelta(days=WEIGHT_AVG_DAYS - 1), day) if d in weights]
        points.append(
            WeightPoint(
                day=day,
                kg=round(weights[day], 2) if day in weights else None,
                avg7=_mean(window, 2) if day <= span.today else None,
            )
        )
    in_period = [day for day in weights if span.first <= day <= span.last]
    change = None
    if in_period:
        latest = max(in_period)
        earlier = [
            day
            for day in weights
            if latest - timedelta(days=CHANGE_MAX_DAYS) <= day <= latest - timedelta(days=CHANGE_DAYS)
        ]
        if earlier:
            change = round(weights[latest] - weights[max(earlier)], 2)
    sleep = [(day, nights.get(day)) for day in span.days]
    logged = list(nights.values())
    return BodySeries(
        span=span,
        weight=points,
        change_30d=change,
        sleep=sleep,
        sleep_avg=round(sum(logged) / len(logged)) if logged else None,
    )


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PlannerDay:
    day: date
    done: int
    created: int
    tracked_seconds: float


@dataclass(slots=True)
class AreaTime:
    area: Area | None
    seconds: float
    target_minutes: int | None


@dataclass(slots=True)
class ProjectProgress:
    project: Project
    done: int
    total: int
    done_in_period: int


@dataclass(slots=True)
class PlannerSeries:
    span: Span
    days: list[PlannerDay]
    by_area: list[AreaTime]
    projects: list[ProjectProgress]


async def planner(db: AsyncSession, ctx: GameContext, span: Span) -> PlannerSeries:
    """Tasks done/created and tracked time per day, time per area against its target,
    and project progress (eight queries for any period)."""
    user_id = ctx.user_id
    tasks = await day_metrics.tasks_by_day(db, user_id, span.first, span.last, ctx.tz)
    tracked = await day_metrics.tracked_seconds_by_day(db, user_id, span.first, span.last)
    running = await day_metrics.running_timer(db, user_id, ctx.now)
    days = []
    for day in span.days:
        t = tasks.get(day)
        seconds = tracked.get(day, 0.0) + (running[1] if running is not None and running[0] == day else 0.0)
        days.append(
            PlannerDay(
                day=day,
                done=t.done if t is not None else 0,
                created=t.created if t is not None else 0,
                tracked_seconds=seconds,
            )
        )
    return PlannerSeries(
        span=span,
        days=days,
        by_area=await _area_time(db, ctx, span),
        projects=await _projects(db, ctx, span),
    )


async def _area_time(db: AsyncSession, ctx: GameContext, span: Span) -> list[AreaTime]:
    """Tracked time per area (the running timer up to now), with every active area
    that has a weekly target even without time — as the stage-6 summary."""
    rows = await db.execute(
        select(TimeEntry.area_id, func.sum(entry_seconds(ctx.now)))
        .where(
            TimeEntry.user_id == ctx.user_id,
            TimeEntry.is_deleted.is_(False),
            TimeEntry.local_day.between(span.first, span.last),
        )
        .group_by(TimeEntry.area_id)
    )
    seconds = {area_id: float(total or 0) for area_id, total in rows}
    areas = (
        await db.scalars(
            select(Area).where(
                Area.user_id == ctx.user_id,
                or_(
                    Area.id.in_([key for key in seconds if key is not None]),
                    and_(Area.weekly_target_minutes.is_not(None), Area.is_archived.is_(False)),
                ),
            )
        )
    ).all()
    factor = TARGET_FACTORS[span.period]
    out = [
        AreaTime(
            area=area,
            seconds=seconds.get(area.id, 0.0),
            target_minutes=round(area.weekly_target_minutes * factor) if area.weekly_target_minutes else None,
        )
        for area in areas
    ]
    if None in seconds:
        out.append(AreaTime(area=None, seconds=seconds[None], target_minutes=None))
    return sorted(
        out,
        key=lambda item: (
            -item.seconds,
            item.area.sort_order if item.area is not None else 1 << 16,
            (item.area.name.lower() if item.area is not None else ""),
        ),
    )


async def _projects(db: AsyncSession, ctx: GameContext, span: Span) -> list[ProjectProgress]:
    """Open projects and any project with a top-level task completed in the period:
    top-level tasks done / total (cancelled ones excluded, as the project screen) and
    done within the period (two queries)."""
    start, end = day_range_utc(span.first, span.last, ctx.tz)
    in_period = and_(Task.status == TaskStatus.DONE, Task.completed_at >= start, Task.completed_at < end)
    rows = await db.execute(
        select(
            Task.project_id,
            func.count().filter(Task.status.not_in(CLOSED)),
            func.count().filter(Task.status == TaskStatus.DONE),
            func.count().filter(in_period),
        )
        .where(Task.user_id == ctx.user_id, Task.parent_id.is_(None), Task.project_id.is_not(None))
        .group_by(Task.project_id)
    )
    counts = {
        project_id: (int(open_ or 0), int(done or 0), int(recent or 0)) for project_id, open_, done, recent in rows
    }
    active = [project_id for project_id, (_, _, recent) in counts.items() if recent]
    projects = (
        await db.scalars(
            select(Project).where(
                Project.user_id == ctx.user_id,
                or_(
                    and_(Project.status.in_(OPEN_PROJECT), Project.is_archived.is_(False)),
                    Project.id.in_(active),
                ),
            )
        )
    ).all()
    out = []
    for project in projects:
        open_count, done, recent = counts.get(project.id, (0, 0, 0))
        out.append(ProjectProgress(project=project, done=done, total=open_count + done, done_in_period=recent))
    return sorted(out, key=lambda item: (-item.done_in_period, item.project.sort_order, item.project.name.lower()))


# --------------------------------------------------------------------------- #
# Progress (gamification history)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ProgressDay:
    day: date
    score: float | None
    xp: int
    overall: bool
    freeze: bool


@dataclass(slots=True)
class ProgressSeries:
    span: Span
    days: list[ProgressDay]


async def progress(db: AsyncSession, ctx: GameContext, span: Span) -> ProgressSeries:
    """Stored daily scores, XP per day and the overall-streak calendar (three queries).
    Scores are read as stored, never recomputed here: the endpoint lets the engine
    refresh the open window (today, yesterday) first, older days are frozen."""
    flags = await score.load_flags(db, ctx.user_id, span.first, span.last)
    per_day = await xp.per_day(db, ctx.user_id, span.first, span.last)
    freeze = await _freeze_days(db, ctx.user_id)
    days = []
    for day in span.days:
        flag = flags.get(day)
        days.append(
            ProgressDay(
                day=day,
                score=round(flag.score, 1) if flag is not None else None,
                xp=per_day.get(day, 0),
                overall=bool(flag is not None and flag.counts_for_overall),
                freeze=day in freeze,
            )
        )
    return ProgressSeries(span=span, days=days)


async def _freeze_days(db: AsyncSession, user_id: uuid.UUID) -> set[date]:
    """Days a freeze token saved the overall streak (``freeze_used_dates``). None while
    freezes are off, whatever an older row (before the switch, a restored backup) still holds."""
    if not streaks.freezes_enabled():
        return set()
    raw = await db.scalar(
        select(Streak.freeze_used_dates).where(Streak.user_id == user_id, Streak.kind == StreakKind.OVERALL)
    )
    out: set[date] = set()
    for value in raw if isinstance(raw, list) else []:
        try:
            out.add(date.fromisoformat(str(value)))
        except ValueError:
            continue  # a malformed stored value degrades, never a 500
    return out
