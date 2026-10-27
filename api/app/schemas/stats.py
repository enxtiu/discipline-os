"""Stage 8 — insight: the ``/stats/*`` series behind the charts.

Every series is computed by the server in the user's timezone for one calendar
period (``week`` follows ``settings.week_start``, ``month``, ``quarter``, ``year``)
that contains the anchor ``date`` (default: your today). Every day of the period is
present, future days included (they carry no data); each field documents whether a
day without data is ``null`` ("nothing logged") or ``0`` ("nothing happened").

Burned calories are never part of nutrition: workout and Health active energy are
their own series in ``/stats/activity`` and never food budget.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.planner import RefOut
from app.schemas.training import MuscleLiteral, RecordOut

PeriodLiteral = Literal["week", "month", "quarter", "year"]


class StatsOut(BaseModel):
    """Response models of this module (built by the presenters; ``from`` is an alias)."""

    model_config = ConfigDict(populate_by_name=True)


class StatsRangeOut(StatsOut):
    period: PeriodLiteral
    from_: date = Field(alias="from", description="first day of the period (inclusive)")
    to: date = Field(description="last day of the period (inclusive)")
    today: date = Field(description="your today: days after it are in the future and carry no data")


# --------------------------------------------------------------------------- #
# Nutrition
# --------------------------------------------------------------------------- #
class ZoneOut(StatsOut):
    low: int = Field(description="max(floor, 0.92 × target) — never below the safe floor")
    high: int = Field(description="1.06 × target")


class NutritionDayOut(StatsOut):
    date: date
    kcal: float | None = Field(description="eaten (meals only, never burned calories); null = no meal logged")
    protein_g: float | None = Field(description="null = no meal logged")
    fat_g: float | None = Field(description="null = no meal logged")
    carbs_g: float | None = Field(description="null = no meal logged")
    fiber_g: float | None = Field(description="null = no meal logged")
    water_ml: int | None = Field(description="null = no water logged")
    meals: int = Field(description="meals logged (0 = none)")
    in_zone: bool | None = Field(
        description="finished days with meals: kcal inside the zone; null for days without meals, "
        "today (in progress), future days, or without a calorie target"
    )


class NutritionAveragesOut(StatsOut):
    """Means over finished days (before today) that have at least one meal — a partial
    day or a day without logging never pulls an average down."""

    days: int = Field(description="finished days with meals the food averages are taken over")
    kcal: float | None
    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None
    meals: float | None
    water_days: int = Field(description="finished days with water logged (the water average's days)")
    water_ml: float | None


class NutritionAdherenceOut(StatsOut):
    """Day counts up to today (future days never count)."""

    zone_days: int = Field(description="finished days with meals and kcal inside the zone")
    protein_days: int = Field(description="days with protein ≥ target and intake ≥ the safe floor")
    water_days: int = Field(description="days with water ≥ target")
    logged_days: int = Field(description="days with at least one meal (today included)")


class NutritionStatsOut(StatsRangeOut):
    target_kcal: int | None = Field(description="null until targets exist")
    target_protein_g: int | None
    target_fiber_g: int | None
    target_water_ml: int | None
    zone: ZoneOut | None = Field(description="the daily score's calorie zone; null without a calorie target")
    floor: int = Field(description="the safe floor (kcal); nothing below it is ever rewarded")
    days: list[NutritionDayOut]
    averages: NutritionAveragesOut
    adherence: NutritionAdherenceOut


# --------------------------------------------------------------------------- #
# Activity (burned calories live here, on their own)
# --------------------------------------------------------------------------- #
class ActivityDayOut(StatsOut):
    date: date
    steps: int | None = Field(description="null = nothing synced or entered that day")
    on_target: bool | None = Field(description="steps ≥ target_steps; null without steps or without a target")
    workouts: int = Field(description="completed workouts (0 = none)")
    workout_kcal: float = Field(description="burned in workouts — never food budget (0 = no workout)")
    active_kcal: float | None = Field(description="Health's active energy — never food budget; null = not reported")


class ActivityWeekOut(StatsOut):
    """A whole week (``week_start`` … +6 days) that overlaps the period."""

    week_start: date
    workouts: int = Field(description="completed workouts that week")
    planned: int = Field(description="workout days in your weekly plan that week (0 = none planned)")
    required: int = Field(description="what makes the week count for the workout streak: planned, or 3 without a plan")
    volume_kg: float = Field(description="kg × reps of the working sets")


class MuscleVolumeOut(StatsOut):
    muscle: MuscleLiteral = Field(description="the exercise's primary muscle group")
    sets: int = Field(description="working sets (warm-ups excluded)")
    volume_kg: float


class ActivityStatsOut(StatsRangeOut):
    target_steps: int | None
    days: list[ActivityDayOut]
    weeks: list[ActivityWeekOut]
    muscle_volume: list[MuscleVolumeOut] = Field(description="the period's days, most volume first")
    records: list[RecordOut] = Field(description="personal records of the period, newest first (at most 100)")
    records_total: int


# --------------------------------------------------------------------------- #
# Body
# --------------------------------------------------------------------------- #
class WeightPointOut(StatsOut):
    date: date
    kg: float | None = Field(description="the weigh-in of that day; null = none")
    avg7: float | None = Field(description="mean of the weigh-ins of the 7 days ending that day; null = none or future")


class SleepPointOut(StatsOut):
    date: date
    minutes: int | None = Field(description="the night ending that morning; null = no entry")


class BodyStatsOut(StatsRangeOut):
    weight: list[WeightPointOut]
    change_30d: float | None = Field(
        description="the period's newest weigh-in minus the newest one 30–45 days before it; null = not enough data"
    )
    sleep: list[SleepPointOut]
    sleep_avg: int | None = Field(description="mean minutes over the nights logged in the period; null = none")


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
class PlannerDayOut(StatsOut):
    date: date
    done: int = Field(description="tasks (and subtasks) completed that day")
    created: int = Field(description="tasks (and subtasks) created that day")
    tracked_minutes: int = Field(description="time tracked that day; the running timer counts up to now")


class AreaTimeOut(StatsOut):
    area: RefOut | None = Field(description="null = time without an area")
    minutes: int
    target_minutes: int | None = Field(
        description="the area's weekly target scaled to the period (week ×1, month ×4.35, quarter ×13.05, "
        "year ×52.2); null without a target"
    )


class ProjectProgressOut(StatsOut):
    project: RefOut
    done: int = Field(description="top-level tasks done (all time)")
    total: int = Field(description="top-level tasks, cancelled ones excluded (all time)")
    done_in_period: int = Field(description="top-level tasks completed within the period")


class PlannerStatsOut(StatsRangeOut):
    days: list[PlannerDayOut]
    by_area: list[AreaTimeOut] = Field(description="most time first")
    projects: list[ProjectProgressOut] = Field(
        description="open projects and any project with a task completed in the period"
    )


# --------------------------------------------------------------------------- #
# Progress (gamification history)
# --------------------------------------------------------------------------- #
class ScorePointOut(StatsOut):
    date: date
    score: float | None = Field(description="the stored daily score 0–100; null = never scored (no data yet, future)")


class XpPointOut(StatsOut):
    date: date
    xp: int = Field(description="XP earned that day (0 = none)")


class StreakDayOut(StatsOut):
    date: date
    overall: bool = Field(description="the day counted for the overall streak (score ≥ 60 with credit)")
    freeze: bool = Field(description="a freeze token saved the overall streak that day")


class ProgressStatsOut(StatsRangeOut):
    scores: list[ScorePointOut]
    xp: list[XpPointOut]
    streak_calendar: list[StreakDayOut]
