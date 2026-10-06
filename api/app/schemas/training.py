"""Training (stage 4): exercise library, templates, weekly plan, workouts, PRs.

Units are metric and integral where it matters for the UI: kilograms, seconds,
metres. Burned calories are reported on their own and are never part of the
food budget.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import ApiModel, ORMModel, UuidStr

MuscleLiteral = Literal[
    "chest", "back", "lats", "traps", "shoulders", "biceps", "triceps", "forearms", "abs", "obliques",
    "lower_back", "glutes", "quads", "hamstrings", "calves", "full_body", "cardio", "other",
]  # fmt: skip
EquipmentLiteral = Literal[
    "barbell", "dumbbell", "kettlebell", "machine", "cable", "bodyweight", "bands", "cardio_machine", "other"
]
ExerciseTypeLiteral = Literal["strength", "cardio", "mobility", "sport", "other"]
# how a set of this exercise is entered: kg × reps, reps only, a duration, or duration + distance
LogModeLiteral = Literal["weight_reps", "reps", "duration", "cardio"]
WorkoutStatusLiteral = Literal["planned", "in_progress", "completed", "skipped"]
KcalMethodLiteral = Literal["met", "steps", "manual", "none"]
RecordKindLiteral = Literal["weight", "est_1rm", "reps", "volume", "duration", "distance"]
Alias = Annotated[str, Field(max_length=64)]


class TrainingIn(ApiModel):
    """Request models of this module: no NaN/Infinity in any number."""

    model_config = ConfigDict(allow_inf_nan=False)


# --------------------------------------------------------------------------- #
# Exercises
# --------------------------------------------------------------------------- #
class ExerciseOut(ORMModel):
    id: uuid.UUID
    name_ru: str | None = None
    name_en: str | None = None
    aliases: list[str] = Field(default_factory=list)
    muscle_group: MuscleLiteral
    secondary_muscles: list[str] = Field(default_factory=list)
    equipment: EquipmentLiteral
    exercise_type: ExerciseTypeLiteral
    met: float
    is_unilateral: bool = False
    is_timed: bool = False
    difficulty: int | None = None
    instructions_md: str | None = None
    is_custom: bool = False
    log_mode: LogModeLiteral = "weight_reps"


class ExerciseListOut(ApiModel):
    query: str
    items: list[ExerciseOut]
    recent_ids: list[uuid.UUID] = Field(default_factory=list, description="your most recently used, newest first")


class ExerciseIn(TrainingIn):
    """A custom ("my") exercise."""

    id: UuidStr | None = None
    name_ru: str | None = Field(default=None, max_length=160)
    name_en: str | None = Field(default=None, max_length=160)
    aliases: list[Alias] = Field(default_factory=list, max_length=12)
    muscle_group: MuscleLiteral = "other"
    equipment: EquipmentLiteral = "other"
    exercise_type: ExerciseTypeLiteral = "strength"
    is_timed: bool = False
    is_unilateral: bool = False
    met: float | None = Field(default=None, gt=0, le=25, description="default by type when omitted")

    @model_validator(mode="after")
    def _named(self) -> ExerciseIn:
        if not (self.name_ru or self.name_en):
            raise ValueError("name_ru or name_en is required")
        return self


class ExercisePatchIn(TrainingIn):
    name_ru: str | None = Field(default=None, max_length=160)
    name_en: str | None = Field(default=None, max_length=160)
    aliases: list[Alias] | None = Field(default=None, max_length=12)
    muscle_group: MuscleLiteral | None = None
    equipment: EquipmentLiteral | None = None
    exercise_type: ExerciseTypeLiteral | None = None
    is_timed: bool | None = None
    is_unilateral: bool | None = None
    met: float | None = Field(default=None, gt=0, le=25)
    is_archived: bool | None = None


class BestsOut(ApiModel):
    """All-time bests over completed workouts (warm-up sets excluded)."""

    weight_kg: float | None = None
    est_1rm_kg: float | None = None
    reps: int | None = None
    volume_kg: float | None = Field(default=None, description="best single-workout volume for this exercise")
    duration_s: int | None = None
    distance_m: int | None = None


class RecordOut(ApiModel):
    id: uuid.UUID
    exercise_id: uuid.UUID
    exercise_name_ru: str | None = None
    exercise_name_en: str | None = None
    kind: RecordKindLiteral
    value: float
    previous_value: float | None = None
    unit: str
    reps: int | None = None
    achieved_at: datetime
    local_day: date = Field(description="the workout's day in your timezone")
    workout_id: uuid.UUID | None = None
    set_id: uuid.UUID | None = None


class ExerciseSessionOut(ApiModel):
    workout_id: uuid.UUID
    local_day: date
    sets: int
    top_weight_kg: float | None = None
    top_reps: int | None = None
    est_1rm_kg: float | None = None
    volume_kg: float = 0
    reps: int = 0
    duration_s: int | None = None
    distance_m: int | None = None


class ExerciseHistoryOut(ApiModel):
    exercise: ExerciseOut
    bests: BestsOut
    records: list[RecordOut]
    sessions: list[ExerciseSessionOut] = Field(description="newest first")


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
class TemplateItemIn(TrainingIn):
    exercise_id: uuid.UUID
    target_sets: int = Field(default=3, ge=1, le=20)
    target_reps: int | None = Field(default=None, ge=1, le=200)
    target_reps_min: int | None = Field(default=None, ge=1, le=200)
    target_reps_max: int | None = Field(default=None, ge=1, le=200)
    target_weight_kg: float | None = Field(default=None, ge=0, le=1000)
    target_duration_s: int | None = Field(default=None, ge=1, le=86_400)
    target_distance_m: int | None = Field(default=None, ge=1, le=1_000_000)
    rest_seconds: int = Field(default=90, ge=0, le=900)
    superset_group: int | None = Field(default=None, ge=1, le=20)
    notes: str | None = Field(default=None, max_length=500)


class TemplateIn(TrainingIn):
    id: UuidStr | None = None
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    kind: ExerciseTypeLiteral = "strength"
    estimated_minutes: int | None = Field(default=None, ge=1, le=600)
    color: str | None = Field(default=None, max_length=16)
    items: list[TemplateItemIn] = Field(default_factory=list, max_length=40)


class TemplateItemOut(ApiModel):
    id: uuid.UUID
    exercise: ExerciseOut
    sort_order: int
    target_sets: int
    target_reps: int | None = None
    target_reps_min: int | None = None
    target_reps_max: int | None = None
    target_weight_kg: float | None = None
    target_duration_s: int | None = None
    target_distance_m: int | None = None
    rest_seconds: int
    superset_group: int | None = None
    notes: str | None = None


class TemplateOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    kind: ExerciseTypeLiteral
    estimated_minutes: int | None = None
    color: str | None = None
    use_count: int = 0
    items: list[TemplateItemOut]
    updated_at: datetime


class TemplateBriefOut(ApiModel):
    id: uuid.UUID
    name: str
    kind: ExerciseTypeLiteral
    exercise_count: int
    estimated_minutes: int | None = None
    color: str | None = None


# --------------------------------------------------------------------------- #
# Weekly plan
# --------------------------------------------------------------------------- #
class PlanDayIn(TrainingIn):
    template_id: uuid.UUID | None = None
    is_rest_day: bool = False
    title: str | None = Field(default=None, max_length=160)
    planned_time: time | None = None

    @model_validator(mode="after")
    def _something(self) -> PlanDayIn:
        if self.is_rest_day and self.template_id is not None:
            raise ValueError("a rest day has no template")
        if not self.is_rest_day and self.template_id is None and not self.title:
            raise ValueError("choose a template, a title or a rest day")
        return self


class WorkoutBriefOut(ApiModel):
    id: uuid.UUID
    title: str | None = None
    kind: ExerciseTypeLiteral
    status: WorkoutStatusLiteral
    template_id: uuid.UUID | None = None
    started_at: datetime
    ended_at: datetime | None = None
    local_day: date
    duration_s: int | None = None
    total_sets: int = 0
    total_reps: int = 0
    total_volume_kg: float = 0
    kcal_burned: float | None = None
    kcal_method: KcalMethodLiteral = "none"
    pr_count: int = 0
    exercise_names: list[str] = Field(default_factory=list, description="first few, in order")


class PlanDayOut(ApiModel):
    date: date
    weekday: int = Field(description="0 = Monday … 6 = Sunday")
    source: Literal["weekday", "date", "none"]
    template: TemplateBriefOut | None = None
    is_rest_day: bool = False
    title: str | None = None
    planned_time: time | None = None
    is_today: bool = False
    workouts: list[WorkoutBriefOut] = Field(default_factory=list)


class RecurringDayOut(ApiModel):
    weekday: int
    template: TemplateBriefOut | None = None
    is_rest_day: bool = False
    title: str | None = None
    planned_time: time | None = None


class WeekPlanOut(ApiModel):
    start: date
    days: list[PlanDayOut]
    recurring: list[RecurringDayOut | None] = Field(description="index = weekday (0 = Monday)")


# --------------------------------------------------------------------------- #
# Workouts
# --------------------------------------------------------------------------- #
class WorkoutStartIn(TrainingIn):
    id: UuidStr = Field(description="client UUID: replaying the start returns the same workout")
    template_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=160)
    kind: ExerciseTypeLiteral | None = None
    started_at: datetime | None = None


class WorkoutSetIn(TrainingIn):
    exercise_id: uuid.UUID
    sort_order: int = Field(default=0, ge=0, le=1000)
    set_index: int = Field(default=1, ge=1, le=100)
    is_warmup: bool = False
    reps: int | None = Field(default=None, ge=0, le=1000)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    rpe: float | None = Field(default=None, ge=1, le=10)
    duration_s: int | None = Field(default=None, ge=0, le=86_400)
    distance_m: int | None = Field(default=None, ge=0, le=1_000_000)
    rest_s: int | None = Field(default=None, ge=0, le=3600)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _measured(self) -> WorkoutSetIn:
        if not any(value for value in (self.reps, self.duration_s, self.distance_m)):
            raise ValueError("a set needs reps, a duration or a distance")
        return self


class WorkoutFinishIn(TrainingIn):
    ended_at: datetime | None = None
    duration_s: int | None = Field(default=None, ge=60, le=86_400, description="overrides ended_at - started_at")
    rating: int | None = Field(default=None, ge=1, le=5)
    notes_md: str | None = Field(default=None, max_length=5000)


class WorkoutPatchIn(TrainingIn):
    title: str | None = Field(default=None, max_length=160)
    notes_md: str | None = Field(default=None, max_length=5000)
    rating: int | None = Field(default=None, ge=1, le=5)
    duration_s: int | None = Field(default=None, ge=60, le=86_400)
    kcal_burned: float | None = Field(default=None, ge=0, le=10_000, description="manual value; see kcal_auto")
    kcal_auto: bool | None = Field(default=None, description="true → back to the MET estimate")


class WorkoutQuickIn(TrainingIn):
    """A finished cardio (or any timed) session in one call — a run, a ride, a swim."""

    id: UuidStr
    exercise_id: uuid.UUID
    duration_s: int = Field(ge=60, le=86_400)
    distance_m: int | None = Field(default=None, ge=1, le=1_000_000)
    started_at: datetime | None = None
    notes_md: str | None = Field(default=None, max_length=5000)


class WorkoutSetOut(ORMModel):
    id: uuid.UUID
    exercise_id: uuid.UUID
    sort_order: int
    set_index: int
    is_warmup: bool = False
    reps: int | None = None
    weight_kg: float | None = None
    rpe: float | None = None
    duration_s: int | None = None
    distance_m: int | None = None
    rest_s: int | None = None
    note: str | None = None
    est_1rm_kg: float | None = None
    is_pr: bool = False


class PreviousSetOut(ApiModel):
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: int | None = None
    distance_m: int | None = None
    is_warmup: bool = False


class WorkoutExerciseOut(ApiModel):
    exercise: ExerciseOut
    target: TemplateItemOut | None = None
    sets: list[WorkoutSetOut]
    previous: list[PreviousSetOut] = Field(default_factory=list, description="sets of the last workout with it")
    previous_day: date | None = None
    bests: BestsOut = Field(description="bests before this workout — beat one and it is a PR")


class WorkoutOut(WorkoutBriefOut):
    notes_md: str | None = None
    rating: int | None = None
    template_name: str | None = None
    exercises: list[WorkoutExerciseOut]
    records: list[RecordOut] = Field(default_factory=list, description="PRs set in this workout")
    body_weight_kg: float | None = Field(default=None, description="the weight the kcal estimate used")


class WorkoutListOut(ApiModel):
    items: list[WorkoutBriefOut]
    has_more: bool = False


class BurnedDayOut(ApiModel):
    date: date
    kcal: float
    workouts: int


class BurnedOut(ApiModel):
    """Calories burned in workouts. Shown on their own — never added to the food budget."""

    days: list[BurnedDayOut]
    total_kcal: float
    average_kcal: float


class TrainingTodayOut(ApiModel):
    date: date
    plan: PlanDayOut
    active: WorkoutBriefOut | None = None
    done: list[WorkoutBriefOut] = Field(default_factory=list)
    burned_kcal: float = 0
