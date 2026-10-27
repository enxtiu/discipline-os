"""Per-day aggregates shared by stage 8: gamification (score, XP, streaks, quests,
achievements) and the stats/export endpoints.

Rules every function here follows:

* **Ranges, not days.** Each series takes an inclusive local-day range
  ``[first, last]`` and answers with **one** query grouped by day — never one
  query per day, so a year of history costs the same handful of round trips as a
  single day.
* **The user's days.** Tables that store a local day (``meal_logs.local_day``,
  ``workout_logs.local_day``, ``journal_entries.entry_date`` …) are grouped by it;
  instants (task/project completion) are converted with the user's timezone *in
  SQL*, so day boundaries are DST-safe and identical to the rest of the server.
* **Missing ≠ zero.** A day without rows is simply absent from the returned dict
  (``nutrition_by_day`` has no entry for a day without meals); callers decide what
  that means — "no sleep logged" is not "0 minutes slept". :func:`collect` is the
  one exception: it returns every day of the range with explicit defaults.
* **Burned calories are their own series.** Workout kcal and Health active energy
  are reported separately (``workouts_by_day``, ``health_by_day``) and are never
  folded into nutrition — they are not food budget.
* **Scoped.** Every query filters by ``user_id``; nothing here trusts a client.

The safe floor and the calorie zone live here too (:func:`safe_floor_kcal`,
:func:`kcal_zone`) so the daily score and the nutrition stats use exactly the same
numbers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date, String, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tz import day_range_utc, ensure_utc, iter_days
from app.models import (
    BodyMetric,
    HealthDaily,
    JournalEntry,
    MealLog,
    PersonalRecord,
    Project,
    ProjectStatus,
    SleepLog,
    Task,
    TaskStatus,
    TimeEntry,
    UserSettings,
    WaterLog,
    WeeklyPlan,
    WorkoutLog,
    WorkoutStatus,
)

__all__ = [
    "DEFAULT_WEEKLY_WORKOUTS",
    "FLOOR_FEMALE",
    "FLOOR_MALE",
    "FLOOR_OTHER",
    "DayMetrics",
    "HealthDay",
    "KcalZone",
    "MealRef",
    "NutritionDay",
    "ProjectRef",
    "RecordRef",
    "TaskRef",
    "TasksDay",
    "WeekPlan",
    "WorkoutDay",
    "WorkoutRef",
    "collect",
    "completed_projects",
    "completed_tasks",
    "completed_workouts",
    "first_activity_day",
    "health_by_day",
    "journal_days",
    "journal_has_content",
    "kcal_zone",
    "logged_meals",
    "nutrition_by_day",
    "personal_records",
    "plan_by_day",
    "required_workouts",
    "running_timer",
    "safe_floor_kcal",
    "sleep_by_day",
    "sql_timezone",
    "tasks_by_day",
    "tracked_seconds_by_day",
    "water_by_day",
    "week_plans",
    "week_start_for",
    "weeks_from_plan",
    "weigh_ins_by_day",
    "workouts_by_day",
]

# Contract (stage 8): settings.safe_floor_kcal, else 1500 male / 1200 female /
# 1500 other-or-unknown. NB: the stage-1 target engine clamps targets with 1200
# for "other/unknown"; for *rewards* the stricter 1500 applies.
FLOOR_MALE = 1500
FLOOR_FEMALE = 1200
FLOOR_OTHER = 1200  # stage-1 default for other/unknown (settings_service.default_floor_for)
# the calorie zone: [max(F, 0.92·T), 1.06·T]
ZONE_LOW_FACTOR = 0.92
ZONE_HIGH_FACTOR = 1.06
# weekly workout requirement when the user has no weekly plan at all
DEFAULT_WEEKLY_WORKOUTS = 3


def _f(value: Decimal | float | int | None) -> float:
    return float(value) if value is not None else 0.0


# --------------------------------------------------------------------------- #
# Safe floor and calorie zone
# --------------------------------------------------------------------------- #
def safe_floor_kcal(settings: UserSettings | None) -> int:
    """The user's safe floor — exactly the one the target engine applies and Settings
    shows: ``settings.safe_floor_kcal`` when set (it can only be lowered behind an explicit
    confirmation), else 1500 (male) / 1200 (female, other or unknown; the stage-1 default).
    One source of truth (``settings_service.default_floor_for``) so a target the engine
    recommends is never "below the floor" for scoring. No reward, streak or quest ever
    counts intake below it."""
    if settings is None:
        return FLOOR_OTHER
    from app.services.settings_service import default_floor_for

    return default_floor_for(settings)


@dataclass(slots=True, frozen=True)
class KcalZone:
    """The target zone of a day: ``[low, high]`` with ``low = max(floor, 0.92·T)`` and
    ``high = max(1.06·T, low)`` (the zone never starts below the floor; when the floor
    is above ``1.06·T`` the zone collapses onto the floor). ``target`` may be ``None``
    (no targets yet) — then there is no zone and ``low``/``high`` are ``None``."""

    target: int | None
    floor: int
    low: float | None
    high: float | None

    def contains(self, kcal: float) -> bool:
        return self.low is not None and self.high is not None and self.low <= kcal <= self.high

    @property
    def low_int(self) -> int | None:
        return round(self.low) if self.low is not None else None

    @property
    def high_int(self) -> int | None:
        return round(self.high) if self.high is not None else None


def kcal_zone(target_kcal: int | float | None, floor: int) -> KcalZone:
    """Zone for a calorie target and a floor. Burned calories never enter here."""
    if not target_kcal or target_kcal <= 0:
        return KcalZone(target=None, floor=floor, low=None, high=None)
    target = float(target_kcal)
    low = max(float(floor), ZONE_LOW_FACTOR * target)
    high = max(ZONE_HIGH_FACTOR * target, low)
    return KcalZone(target=int(target_kcal), floor=floor, low=low, high=high)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def sql_timezone(tz: str | None) -> str:
    """A timezone name PostgreSQL will accept (an unknown name would raise in SQL)."""
    if not tz:
        return "UTC"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return "UTC"
    return tz


def _local_date(column, tz: str | None):
    """``(column AT TIME ZONE tz)::date`` — the user's local day of an instant."""
    return cast(func.timezone(literal(sql_timezone(tz), String), column), Date)


def week_start_for(day: date, week_start: int) -> date:
    """First day of the week containing ``day``; ``week_start`` follows the settings
    (1 = Monday, 0 = Sunday — the same convention as stage 4)."""
    if week_start == 0:
        return day - timedelta(days=(day.weekday() + 1) % 7)
    return day - timedelta(days=day.weekday())


def journal_has_content():
    """SQL condition: the journal entry has any text (note, wins, improvements) or a
    rating / mood / energy. Tags alone are not content."""
    texts = [
        func.coalesce(column, "").op("~")(r"\S")
        for column in (JournalEntry.note_md, JournalEntry.wins_md, JournalEntry.improvements_md)
    ]
    scales = [column.is_not(None) for column in (JournalEntry.rating, JournalEntry.mood, JournalEntry.energy)]
    return or_(*texts, *scales)


# --------------------------------------------------------------------------- #
# Nutrition and water
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class NutritionDay:
    kcal: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carbs_g: float = 0.0
    fiber_g: float = 0.0
    meals: int = 0


async def nutrition_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, NutritionDay]:
    """Food logged per local day (deleted meals excluded): kcal, macros, fiber and the
    number of meals. Days without meals are absent. Burned calories are not here."""
    rows = await db.execute(
        select(
            MealLog.local_day,
            func.sum(MealLog.kcal),
            func.sum(MealLog.protein_g),
            func.sum(MealLog.fat_g),
            func.sum(MealLog.carbs_g),
            func.sum(MealLog.fiber_g),
            func.count(),
        )
        .where(MealLog.user_id == user_id, MealLog.is_deleted.is_(False), MealLog.local_day.between(first, last))
        .group_by(MealLog.local_day)
    )
    return {
        row[0]: NutritionDay(
            kcal=_f(row[1]),
            protein_g=_f(row[2]),
            fat_g=_f(row[3]),
            carbs_g=_f(row[4]),
            fiber_g=_f(row[5]),
            meals=int(row[6] or 0),
        )
        for row in rows
    }


@dataclass(slots=True, frozen=True)
class MealRef:
    id: uuid.UUID
    local_day: date
    logged_at: datetime


async def logged_meals(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> list[MealRef]:
    """Every non-deleted meal of the range, ordered by day, time and id (XP awards)."""
    rows = await db.execute(
        select(MealLog.id, MealLog.local_day, MealLog.logged_at)
        .where(MealLog.user_id == user_id, MealLog.is_deleted.is_(False), MealLog.local_day.between(first, last))
        .order_by(MealLog.local_day, MealLog.logged_at, MealLog.id)
    )
    return [MealRef(row[0], row[1], ensure_utc(row[2])) for row in rows]


async def water_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, int]:
    """Millilitres of water per local day. Days without water are absent."""
    rows = await db.execute(
        select(WaterLog.local_day, func.sum(WaterLog.amount_ml))
        .where(WaterLog.user_id == user_id, WaterLog.local_day.between(first, last))
        .group_by(WaterLog.local_day)
    )
    return {row[0]: int(row[1] or 0) for row in rows}


# --------------------------------------------------------------------------- #
# Health: steps, sleep, weight
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class HealthDay:
    steps: int
    active_kcal: float | None
    distance_m: int | None


async def health_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, HealthDay]:
    """Steps, Health's active energy and distance per local day (one ``health_daily``
    row per day). Active energy is burned calories — its own series, never budget."""
    rows = await db.execute(
        select(HealthDaily.local_day, HealthDaily.steps, HealthDaily.active_kcal, HealthDaily.distance_m).where(
            HealthDaily.user_id == user_id, HealthDaily.local_day.between(first, last)
        )
    )
    return {
        row[0]: HealthDay(
            steps=int(row[1] or 0),
            active_kcal=float(row[2]) if row[2] is not None else None,
            distance_m=int(row[3]) if row[3] is not None else None,
        )
        for row in rows
    }


async def sleep_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, int]:
    """Minutes slept per local day; a night belongs to the day you woke up (stage 5).
    Days without a sleep entry are absent (not 0)."""
    rows = await db.execute(
        select(SleepLog.local_day, func.max(SleepLog.duration_min))
        .where(SleepLog.user_id == user_id, SleepLog.local_day.between(first, last))
        .group_by(SleepLog.local_day)
    )
    return {row[0]: int(row[1] or 0) for row in rows}


async def weigh_ins_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, float]:
    """Weigh-ins (kg) per local day — rows without a weight (waist only…) are ignored."""
    rows = await db.execute(
        select(BodyMetric.measured_on, BodyMetric.weight_kg).where(
            BodyMetric.user_id == user_id,
            BodyMetric.weight_kg.is_not(None),
            BodyMetric.measured_on.between(first, last),
        )
    )
    return {row[0]: float(row[1]) for row in rows}


# --------------------------------------------------------------------------- #
# Training
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class WorkoutDay:
    completed: int = 0
    kcal_burned: float = 0.0  # burned calories: their own series, never food budget
    volume_kg: float = 0.0
    records: int = 0
    duration_s: int = 0


async def workouts_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, WorkoutDay]:
    """Completed (not deleted) workouts per local day: count, burned kcal, volume,
    personal records and duration. Days without a completed workout are absent."""
    rows = await db.execute(
        select(
            WorkoutLog.local_day,
            func.count(),
            func.sum(WorkoutLog.kcal_burned),
            func.sum(WorkoutLog.total_volume_kg),
            func.sum(WorkoutLog.pr_count),
            func.sum(WorkoutLog.duration_s),
        )
        .where(
            WorkoutLog.user_id == user_id,
            WorkoutLog.is_deleted.is_(False),
            WorkoutLog.status == WorkoutStatus.COMPLETED,
            WorkoutLog.local_day.between(first, last),
        )
        .group_by(WorkoutLog.local_day)
    )
    return {
        row[0]: WorkoutDay(
            completed=int(row[1] or 0),
            kcal_burned=_f(row[2]),
            volume_kg=_f(row[3]),
            records=int(row[4] or 0),
            duration_s=int(row[5] or 0),
        )
        for row in rows
    }


@dataclass(slots=True, frozen=True)
class WorkoutRef:
    id: uuid.UUID
    local_day: date
    started_at: datetime


async def completed_workouts(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> list[WorkoutRef]:
    """Completed, non-deleted workouts of the range ordered by start (XP awards)."""
    rows = await db.execute(
        select(WorkoutLog.id, WorkoutLog.local_day, WorkoutLog.started_at)
        .where(
            WorkoutLog.user_id == user_id,
            WorkoutLog.is_deleted.is_(False),
            WorkoutLog.status == WorkoutStatus.COMPLETED,
            WorkoutLog.local_day.between(first, last),
        )
        .order_by(WorkoutLog.local_day, WorkoutLog.started_at, WorkoutLog.id)
    )
    return [WorkoutRef(row[0], row[1], ensure_utc(row[2])) for row in rows]


@dataclass(slots=True, frozen=True)
class RecordRef:
    workout_id: uuid.UUID
    exercise_id: uuid.UUID
    kind: str
    local_day: date
    started_at: datetime


async def personal_records(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> list[RecordRef]:
    """Personal records set by completed, non-deleted workouts of the range, keyed by
    (workout, exercise, record kind) — stable even though stage 4 re-detects records
    (new row ids) whenever a workout is edited."""
    rows = await db.execute(
        select(
            PersonalRecord.workout_log_id,
            PersonalRecord.exercise_id,
            PersonalRecord.kind,
            WorkoutLog.local_day,
            WorkoutLog.started_at,
        )
        .join(WorkoutLog, WorkoutLog.id == PersonalRecord.workout_log_id)
        .where(
            PersonalRecord.user_id == user_id,
            WorkoutLog.user_id == user_id,
            WorkoutLog.is_deleted.is_(False),
            WorkoutLog.status == WorkoutStatus.COMPLETED,
            WorkoutLog.local_day.between(first, last),
        )
        .order_by(WorkoutLog.local_day, WorkoutLog.started_at, PersonalRecord.workout_log_id)
    )
    seen: set[tuple[uuid.UUID, uuid.UUID, str]] = set()
    out: list[RecordRef] = []
    for row in rows:
        key = (row[0], row[1], row[2])
        if key in seen:
            continue
        seen.add(key)
        out.append(RecordRef(row[0], row[1], row[2], row[3], ensure_utc(row[4])))
    out.sort(key=lambda ref: (ref.local_day, ref.started_at, str(ref.workout_id), str(ref.exercise_id), ref.kind))
    return out


# --------------------------------------------------------------------------- #
# Weekly plan (plan-aware workout streak, "no workout quest on a rest day")
# --------------------------------------------------------------------------- #
async def plan_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, str]:
    """The effective training plan per day: ``"workout"`` or ``"rest"``; days without a
    plan are absent. A dated override beats the weekday plan (stage 4); a non-rest row
    is a workout when it names a template or a title (as the stage-7 reminder does)."""
    rows = (
        await db.scalars(
            select(WeeklyPlan).where(
                WeeklyPlan.user_id == user_id,
                or_(WeeklyPlan.weekday.is_not(None), WeeklyPlan.plan_date.between(first, last)),
            )
        )
    ).all()
    recurring: dict[int, WeeklyPlan] = {}
    dated: dict[date, WeeklyPlan] = {}
    for row in rows:
        if row.plan_date is not None:
            dated[row.plan_date] = row
        elif row.weekday is not None:
            recurring[row.weekday] = row
    out: dict[date, str] = {}
    if not recurring and not dated:
        return out
    for day in iter_days(first, last):
        row = dated.get(day) or recurring.get(day.weekday())
        if row is None:
            continue
        if row.is_rest_day:
            out[day] = "rest"
        elif row.template_id is not None or (row.title or "").strip():
            out[day] = "workout"
    return out


@dataclass(slots=True, frozen=True)
class WeekPlan:
    week_start: date
    planned: int  # planned workout days in that week
    has_plan: bool  # any plan row (workout or rest) applies to that week


def required_workouts(plan: WeekPlan) -> int:
    """Workouts that make a week count: the planned number, or 3 without any plan.
    A week planned entirely as rest requires 0 (rest never breaks a streak)."""
    return plan.planned if plan.has_plan else DEFAULT_WEEKLY_WORKOUTS


def weeks_from_plan(plan: dict[date, str], first_week: date, last_week: date, week_start: int) -> dict[date, WeekPlan]:
    """Week plans (keys are week starts) from a per-day plan that covers whole weeks."""
    first_week = week_start_for(first_week, week_start)
    last_week = week_start_for(last_week, week_start)
    out: dict[date, WeekPlan] = {}
    cursor = first_week
    while cursor <= last_week:
        values = [plan[day] for day in (cursor + timedelta(days=offset) for offset in range(7)) if day in plan]
        out[cursor] = WeekPlan(cursor, sum(1 for value in values if value == "workout"), bool(values))
        cursor += timedelta(days=7)
    return out


async def week_plans(
    db: AsyncSession, user_id: uuid.UUID, first_week: date, last_week: date, week_start: int
) -> dict[date, WeekPlan]:
    """Planned workouts per week (keys are week starts) for ``first_week..last_week``."""
    first_week = week_start_for(first_week, week_start)
    last_week = week_start_for(last_week, week_start)
    plan = await plan_by_day(db, user_id, first_week, last_week + timedelta(days=6))
    return weeks_from_plan(plan, first_week, last_week, week_start)


# --------------------------------------------------------------------------- #
# Planner: tasks, projects, tracked time, journal
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class TasksDay:
    done: int = 0  # completed that local day (status done, cancelled excluded)
    done_p1: int = 0
    open_due: int = 0  # still open (not done/cancelled, not archived) and due that day
    created: int = 0


async def tasks_by_day(
    db: AsyncSession, user_id: uuid.UUID, first: date, last: date, tz: str | None
) -> dict[date, TasksDay]:
    """Tasks done (by the local day of ``completed_at``), still-open tasks due that day
    and tasks created, per local day. Subtasks count like tasks. Three queries."""
    start, end = day_range_utc(first, last, tz)
    done_day = _local_date(Task.completed_at, tz)
    done_rows = await db.execute(
        select(done_day, func.count(), func.count().filter(Task.priority == 1))
        .where(
            Task.user_id == user_id,
            Task.status == TaskStatus.DONE,
            Task.completed_at >= start,
            Task.completed_at < end,
        )
        .group_by(done_day)
    )
    due_rows = await db.execute(
        select(Task.due_date, func.count())
        .where(
            Task.user_id == user_id,
            Task.due_date.between(first, last),
            Task.status.not_in((TaskStatus.DONE, TaskStatus.CANCELLED)),
            Task.is_archived.is_(False),
        )
        .group_by(Task.due_date)
    )
    created_day = _local_date(Task.created_at, tz)
    created_rows = await db.execute(
        select(created_day, func.count())
        .where(Task.user_id == user_id, Task.created_at >= start, Task.created_at < end)
        .group_by(created_day)
    )
    acc: dict[date, dict[str, int]] = {}
    for day, done, p1 in done_rows:
        acc.setdefault(day, {}).update(done=int(done or 0), done_p1=int(p1 or 0))
    for day, count in due_rows:
        acc.setdefault(day, {})["open_due"] = int(count or 0)
    for day, count in created_rows:
        acc.setdefault(day, {})["created"] = int(count or 0)
    return {day: TasksDay(**values) for day, values in acc.items() if first <= day <= last}


@dataclass(slots=True, frozen=True)
class TaskRef:
    id: uuid.UUID
    local_day: date
    priority: int
    is_subtask: bool
    completed_at: datetime


async def completed_tasks(
    db: AsyncSession, user_id: uuid.UUID, first: date, last: date, tz: str | None
) -> list[TaskRef]:
    """Tasks completed (done, not cancelled) in the range, by completion time (XP)."""
    start, end = day_range_utc(first, last, tz)
    rows = await db.execute(
        select(Task.id, _local_date(Task.completed_at, tz), Task.priority, Task.parent_id, Task.completed_at)
        .where(
            Task.user_id == user_id,
            Task.status == TaskStatus.DONE,
            Task.completed_at >= start,
            Task.completed_at < end,
        )
        .order_by(Task.completed_at, Task.id)
    )
    return [TaskRef(row[0], row[1], int(row[2] or 3), row[3] is not None, ensure_utc(row[4])) for row in rows]


@dataclass(slots=True, frozen=True)
class ProjectRef:
    id: uuid.UUID
    local_day: date


async def completed_projects(
    db: AsyncSession, user_id: uuid.UUID, first: date, last: date, tz: str | None
) -> list[ProjectRef]:
    """Projects that are completed and were completed in the range (local day)."""
    start, end = day_range_utc(first, last, tz)
    rows = await db.execute(
        select(Project.id, _local_date(Project.completed_at, tz))
        .where(
            Project.user_id == user_id,
            Project.status == ProjectStatus.COMPLETED,
            Project.completed_at >= start,
            Project.completed_at < end,
        )
        .order_by(Project.completed_at, Project.id)
    )
    return [ProjectRef(row[0], row[1]) for row in rows]


async def tracked_seconds_by_day(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> dict[date, float]:
    """Seconds of *finished* time entries per local day (an entry belongs to the day
    it started, as in stage 6). The running timer is reported by :func:`running_timer`."""
    rows = await db.execute(
        select(TimeEntry.local_day, func.sum(TimeEntry.duration_s))
        .where(
            TimeEntry.user_id == user_id,
            TimeEntry.is_deleted.is_(False),
            TimeEntry.duration_s.is_not(None),
            TimeEntry.local_day.between(first, last),
        )
        .group_by(TimeEntry.local_day)
    )
    return {row[0]: float(row[1] or 0) for row in rows}


async def running_timer(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> tuple[date, float] | None:
    """``(local day, seconds so far)`` of the running timer, if one is running."""
    row = (
        await db.execute(
            select(TimeEntry.local_day, TimeEntry.started_at)
            .where(TimeEntry.user_id == user_id, TimeEntry.ended_at.is_(None), TimeEntry.is_deleted.is_(False))
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], max(0.0, (ensure_utc(now) - ensure_utc(row[1])).total_seconds())


async def journal_days(db: AsyncSession, user_id: uuid.UUID, first: date, last: date) -> set[date]:
    """Days with a journal entry that has content (text or a rating/mood/energy)."""
    rows = await db.scalars(
        select(JournalEntry.entry_date).where(
            JournalEntry.user_id == user_id, JournalEntry.entry_date.between(first, last), journal_has_content()
        )
    )
    return set(rows.all())


# --------------------------------------------------------------------------- #
# History start
# --------------------------------------------------------------------------- #
async def first_activity_day(db: AsyncSession, user_id: uuid.UUID, tz: str | None) -> date | None:
    """The earliest local day with any user data (meals, water, workouts, Health,
    sleep, weigh-ins, journal, tracked time, completed tasks) — one query."""
    sources = [
        select(func.min(MealLog.local_day)).where(MealLog.user_id == user_id, MealLog.is_deleted.is_(False)),
        select(func.min(WaterLog.local_day)).where(WaterLog.user_id == user_id),
        select(func.min(WorkoutLog.local_day)).where(WorkoutLog.user_id == user_id, WorkoutLog.is_deleted.is_(False)),
        select(func.min(HealthDaily.local_day)).where(HealthDaily.user_id == user_id),
        select(func.min(SleepLog.local_day)).where(SleepLog.user_id == user_id),
        select(func.min(BodyMetric.measured_on)).where(BodyMetric.user_id == user_id),
        select(func.min(JournalEntry.entry_date)).where(JournalEntry.user_id == user_id),
        select(func.min(TimeEntry.local_day)).where(TimeEntry.user_id == user_id, TimeEntry.is_deleted.is_(False)),
        select(_local_date(func.min(Task.completed_at), tz)).where(
            Task.user_id == user_id, Task.status == TaskStatus.DONE
        ),
    ]
    return await db.scalar(select(func.least(*(source.scalar_subquery() for source in sources))))


# --------------------------------------------------------------------------- #
# Everything for a range
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class DayMetrics:
    """All per-day numbers of one local day. Absent data has an explicit meaning:
    ``steps``/``sleep_minutes``/``weight_kg`` are ``None`` when nothing was recorded,
    counters are 0, ``plan`` is ``None`` without a training plan for the day."""

    day: date
    kcal: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carbs_g: float = 0.0
    fiber_g: float = 0.0
    meals: int = 0
    water_ml: int = 0
    steps: int | None = None
    active_kcal: float | None = None  # burned (Health) — never food budget
    sleep_minutes: int | None = None
    weight_kg: float | None = None
    workouts: int = 0
    workout_kcal: float = 0.0  # burned (workouts) — never food budget
    workout_volume_kg: float = 0.0
    records: int = 0
    tasks_done: int = 0
    tasks_done_p1: int = 0
    tasks_open_due: int = 0
    tasks_created: int = 0
    tracked_seconds: float = 0.0  # finished entries
    running_seconds: float = 0.0  # the running timer (only when ``now`` was given)
    journal: bool = False
    plan: str | None = None  # "workout" | "rest" | None

    @property
    def tracked_minutes(self) -> int:
        """Finished + running time, whole minutes (round half up)."""
        return int((self.tracked_seconds + self.running_seconds) / 60 + 0.5)


@dataclass(slots=True)
class _Series:
    nutrition: dict[date, NutritionDay] = field(default_factory=dict)
    water: dict[date, int] = field(default_factory=dict)
    health: dict[date, HealthDay] = field(default_factory=dict)
    sleep: dict[date, int] = field(default_factory=dict)
    weights: dict[date, float] = field(default_factory=dict)
    workouts: dict[date, WorkoutDay] = field(default_factory=dict)
    tasks: dict[date, TasksDay] = field(default_factory=dict)
    tracked: dict[date, float] = field(default_factory=dict)
    journal: set[date] = field(default_factory=set)
    plan: dict[date, str] = field(default_factory=dict)


async def collect(
    db: AsyncSession,
    user_id: uuid.UUID,
    first: date,
    last: date,
    tz: str | None,
    *,
    now: datetime | None = None,
    plan: dict[date, str] | None = None,
) -> dict[date, DayMetrics]:
    """Every day of ``[first, last]`` with all series (11 queries for any range length).
    With ``now`` the running timer counts towards its day's ``running_seconds``; pass
    ``plan`` (from :func:`plan_by_day`) when you already have it."""
    series = _Series(
        nutrition=await nutrition_by_day(db, user_id, first, last),
        water=await water_by_day(db, user_id, first, last),
        health=await health_by_day(db, user_id, first, last),
        sleep=await sleep_by_day(db, user_id, first, last),
        weights=await weigh_ins_by_day(db, user_id, first, last),
        workouts=await workouts_by_day(db, user_id, first, last),
        tasks=await tasks_by_day(db, user_id, first, last, tz),
        tracked=await tracked_seconds_by_day(db, user_id, first, last),
        journal=await journal_days(db, user_id, first, last),
        plan=plan if plan is not None else await plan_by_day(db, user_id, first, last),
    )
    running = await running_timer(db, user_id, now) if now is not None else None
    out: dict[date, DayMetrics] = {}
    empty_n, empty_w, empty_t = NutritionDay(), WorkoutDay(), TasksDay()
    for day in iter_days(first, last):
        n = series.nutrition.get(day, empty_n)
        w = series.workouts.get(day, empty_w)
        t = series.tasks.get(day, empty_t)
        h = series.health.get(day)
        out[day] = DayMetrics(
            day=day,
            kcal=n.kcal,
            protein_g=n.protein_g,
            fat_g=n.fat_g,
            carbs_g=n.carbs_g,
            fiber_g=n.fiber_g,
            meals=n.meals,
            water_ml=series.water.get(day, 0),
            steps=h.steps if h is not None else None,
            active_kcal=h.active_kcal if h is not None else None,
            sleep_minutes=series.sleep.get(day),
            weight_kg=series.weights.get(day),
            workouts=w.completed,
            workout_kcal=w.kcal_burned,
            workout_volume_kg=w.volume_kg,
            records=w.records,
            tasks_done=t.done,
            tasks_done_p1=t.done_p1,
            tasks_open_due=t.open_due,
            tasks_created=t.created,
            tracked_seconds=series.tracked.get(day, 0.0),
            running_seconds=running[1] if running is not None and running[0] == day else 0.0,
            journal=day in series.journal,
            plan=series.plan.get(day),
        )
    return out
