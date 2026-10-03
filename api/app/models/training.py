"""Training domain: exercise library, templates, weekly plan, logs, sets, PRs.

Also holds the manual health metrics that are *not* nutrition: body weight,
body fat, sleep and the daily health roll-up (steps / active energy) that the
iOS Shortcut pushes in stage 5.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import new_id
from app.models.base import (
    Base,
    JsonType,
    TimestampMixin,
    TzDateTime,
    UiEnum,
    UserScopedMixin,
    UuidPkMixin,
    UuidType,
    enum_col,
)

MACRO = Numeric(8, 2)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class MuscleGroup(UiEnum):
    CHEST = "chest"
    BACK = "back"
    LATS = "lats"
    TRAPS = "traps"
    SHOULDERS = "shoulders"
    BICEPS = "biceps"
    TRICEPS = "triceps"
    FOREARMS = "forearms"
    ABS = "abs"
    OBLIQUES = "obliques"
    LOWER_BACK = "lower_back"
    GLUTES = "glutes"
    QUADS = "quads"
    HAMSTRINGS = "hamstrings"
    CALVES = "calves"
    FULL_BODY = "full_body"
    CARDIO = "cardio"
    OTHER = "other"


class Equipment(UiEnum):
    BARBELL = "barbell"
    DUMBBELL = "dumbbell"
    KETTLEBELL = "kettlebell"
    MACHINE = "machine"
    CABLE = "cable"
    BODYWEIGHT = "bodyweight"
    BANDS = "bands"
    CARDIO_MACHINE = "cardio_machine"
    OTHER = "other"


class ExerciseType(UiEnum):
    STRENGTH = "strength"
    CARDIO = "cardio"
    MOBILITY = "mobility"
    SPORT = "sport"
    OTHER = "other"


class WorkoutStatus(UiEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class KcalMethod(UiEnum):
    MET = "met"  # MET x weight x hours
    STEPS = "steps"  # step-based estimate from health_daily
    MANUAL = "manual"
    NONE = "none"


class HealthSource(UiEnum):
    SHORTCUT = "shortcut"
    MANUAL = "manual"
    APPLE_HEALTH = "apple_health"
    IMPORT = "import"


class SymptomScale(UiEnum):
    """Sleep quality / energy, 1..5."""

    TERRIBLE = "1"
    BAD = "2"
    OK = "3"
    GOOD = "4"
    GREAT = "5"


# --------------------------------------------------------------------------- #
# Exercise library
# --------------------------------------------------------------------------- #
class Exercise(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "exercises"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    name_ru: Mapped[str | None] = mapped_column(String(160), nullable=True)
    name_en: Mapped[str | None] = mapped_column(String(160), nullable=True)
    aliases: Mapped[list[str]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'[]'::jsonb"))
    aliases_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("''"))

    muscle_group: Mapped[MuscleGroup] = mapped_column(
        enum_col(MuscleGroup, "muscle_group"), nullable=False, default=MuscleGroup.OTHER
    )
    secondary_muscles: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    equipment: Mapped[Equipment] = mapped_column(
        enum_col(Equipment, "equipment"), nullable=False, default=Equipment.OTHER
    )
    exercise_type: Mapped[ExerciseType] = mapped_column(
        enum_col(ExerciseType, "exercise_type"), nullable=False, default=ExerciseType.STRENGTH
    )
    # MET value used for the calories-burned estimate
    met: Mapped[float] = mapped_column(Float, nullable=False, default=3.5)
    is_unilateral: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_timed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    instructions_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    difficulty: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1..5
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="seed")

    search_vector_ru: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True, info={"read_only": True})
    search_vector_en: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True, info={"read_only": True})

    __table_args__ = (
        CheckConstraint("met > 0 AND met <= 25", name="met_range"),
        CheckConstraint("difficulty IS NULL OR (difficulty >= 1 AND difficulty <= 5)", name="difficulty_range"),
        CheckConstraint("name_ru IS NOT NULL OR name_en IS NOT NULL", name="at_least_one_name"),
        Index("ix_exercises_search_ru", "search_vector_ru", postgresql_using="gin"),
        Index("ix_exercises_search_en", "search_vector_en", postgresql_using="gin"),
        Index("ix_exercises_name_en_trgm", sa.text("lower(name_en) gin_trgm_ops"), postgresql_using="gin"),
        Index("ix_exercises_name_ru_trgm", sa.text("lower(name_ru) gin_trgm_ops"), postgresql_using="gin"),
        Index("ix_exercises_muscle", "muscle_group", "equipment"),
    )


# --------------------------------------------------------------------------- #
# Templates & weekly plan
# --------------------------------------------------------------------------- #
class WorkoutTemplate(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "workout_templates"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[ExerciseType] = mapped_column(
        enum_col(ExerciseType, "exercise_type"), nullable=False, default=ExerciseType.STRENGTH
    )
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)

    items: Mapped[list[WorkoutTemplateItem]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="WorkoutTemplateItem.sort_order",
        lazy="selectin",
    )


class WorkoutTemplateItem(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "workout_template_items"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("workout_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    target_sets: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    target_reps: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    target_reps_min: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    target_reps_max: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    target_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    target_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rest_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=90)
    superset_group: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    template: Mapped[WorkoutTemplate] = relationship(back_populates="items")


class WeeklyPlan(UuidPkMixin, TimestampMixin, Base):
    """Two flavours in one table:

    * recurring plan  -> ``weekday`` set, ``plan_date`` NULL
    * dated override  -> ``plan_date`` set (takes precedence for that date)
    """

    __tablename__ = "weekly_plan"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    weekday: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 0=Mon .. 6=Sun
    plan_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("workout_templates.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_rest_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("weekday IS NULL OR (weekday >= 0 AND weekday <= 6)", name="weekday_range"),
        UniqueConstraint("user_id", "weekday", name="uq_weekly_plan_user_weekday"),
        Index(
            "uq_weekly_plan_user_date",
            "user_id",
            "plan_date",
            unique=True,
            postgresql_where=sa.text("plan_date IS NOT NULL"),
        ),
    )


# --------------------------------------------------------------------------- #
# Workout logs
# --------------------------------------------------------------------------- #
class WorkoutLog(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "workout_logs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_uuid: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False, unique=True, default=new_id)

    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("workout_templates.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    kind: Mapped[ExerciseType] = mapped_column(
        enum_col(ExerciseType, "exercise_type"), nullable=False, default=ExerciseType.STRENGTH
    )
    status: Mapped[WorkoutStatus] = mapped_column(
        enum_col(WorkoutStatus, "workout_status"), nullable=False, default=WorkoutStatus.COMPLETED
    )

    started_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    local_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1..5 feel

    # aggregates (recomputed when sets change)
    total_sets: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    total_reps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_volume_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    pr_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # calories burned — deliberately NOT part of the food budget
    kcal_burned: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    kcal_method: Mapped[KcalMethod] = mapped_column(
        enum_col(KcalMethod, "kcal_method"), nullable=False, default=KcalMethod.NONE
    )
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_hr: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    sets: Mapped[list[WorkoutSet]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        order_by="WorkoutSet.sort_order",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="rating_range"),
        Index("ix_workout_logs_user_day", "user_id", "local_day"),
        Index("ix_workout_logs_user_started", "user_id", "started_at"),
    )


class WorkoutSet(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "workout_sets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workout_log_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("workout_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    client_uuid: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False, unique=True, default=new_id)

    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    set_index: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    is_warmup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reps: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    rpe: Mapped[float | None] = mapped_column(Float, nullable=True)  # 1..10
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)  # cardio / isometrics
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rest_s: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    tempo: Mapped[str | None] = mapped_column(String(24), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # estimated 1RM (Epley) cached at write time for fast PR/chart queries
    est_1rm_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)

    workout: Mapped[WorkoutLog] = relationship(back_populates="sets")

    __table_args__ = (
        CheckConstraint("reps IS NULL OR reps >= 0", name="reps_non_negative"),
        CheckConstraint("weight_kg IS NULL OR weight_kg >= 0", name="weight_non_negative"),
        CheckConstraint("rpe IS NULL OR (rpe >= 1 AND rpe <= 10)", name="rpe_range"),
        Index("ix_workout_sets_exercise", "exercise_id", "workout_log_id"),
    )


class PersonalRecord(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "personal_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("exercises.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # weight|est_1rm|volume|reps|time|distance
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    previous_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="kg")
    reps: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    achieved_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    workout_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("workout_logs.id", ondelete="SET NULL"), nullable=True
    )
    workout_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("workout_sets.id", ondelete="SET NULL"), nullable=True
    )
    is_celebrated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_personal_records_exercise_kind", "exercise_id", "kind", "achieved_at"),)


# --------------------------------------------------------------------------- #
# Body metrics, sleep, daily health
# --------------------------------------------------------------------------- #
class BodyMetric(UserScopedMixin, UuidPkMixin, Base):
    """Manual weight / body-fat entries (no smart scale in this setup)."""

    __tablename__ = "body_metrics"

    client_uuid: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False, unique=True, default=new_id)
    measured_on: Mapped[date] = mapped_column(Date, nullable=False)
    measured_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    body_fat_pct: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    muscle_mass_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    waist_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")

    __table_args__ = (
        CheckConstraint("weight_kg IS NULL OR (weight_kg > 20 AND weight_kg < 400)", name="weight_range"),
        CheckConstraint("body_fat_pct IS NULL OR (body_fat_pct > 2 AND body_fat_pct < 70)", name="body_fat_range"),
        UniqueConstraint("user_id", "measured_on", name="uq_body_metrics_user_day"),
        Index("ix_body_metrics_user_day", "user_id", "measured_on"),
    )


class SleepLog(UserScopedMixin, UuidPkMixin, Base):
    """Manual bed/wake entry; ``local_day`` is the *wake* day."""

    __tablename__ = "sleep_logs"

    client_uuid: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False, unique=True, default=new_id)
    local_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bed_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    wake_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    quality: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1..5
    awakenings: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")

    __table_args__ = (
        CheckConstraint("quality IS NULL OR (quality >= 1 AND quality <= 5)", name="quality_range"),
        CheckConstraint("duration_min >= 0 AND duration_min <= 1440", name="duration_range"),
        UniqueConstraint("user_id", "local_day", name="uq_sleep_logs_user_day"),
    )


class HealthDaily(UserScopedMixin, UuidPkMixin, Base):
    """Daily steps / active energy / distance.

    One row per user per local day; the ingest endpoint *upserts* by
    ``(user_id, local_day)``, which makes the Shortcut idempotent — running it
    twice simply overwrites today's numbers.
    """

    __tablename__ = "health_daily"

    client_uuid: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    local_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_kcal: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    basal_kcal: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resting_hr: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    hrv_ms: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    source: Mapped[HealthSource] = mapped_column(
        enum_col(HealthSource, "health_source"), nullable=False, default=HealthSource.MANUAL
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    ingested_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("steps >= 0 AND steps <= 200000", name="steps_range"),
        UniqueConstraint("user_id", "local_day", name="uq_health_daily_user_day"),
        Index("ix_health_daily_user_day", "user_id", "local_day"),
    )
