"""Gamification: XP, levels, achievements, streaks, daily score, quests.

Safety principle baked into the schema: XP is only ever awarded for *positive*
behaviour (logging, hitting targets, training, finishing).  There is no negative
XP and no reward for eating below the safe floor — that rule lives in the
scoring service, and the daily score peaks inside the target zone.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

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


class XpKind(UiEnum):
    MEAL_LOGGED = "meal_logged"
    PROTEIN_TARGET = "protein_target"
    FIBER_TARGET = "fiber_target"
    WATER_TARGET = "water_target"
    KCAL_IN_ZONE = "kcal_in_zone"
    WORKOUT_COMPLETED = "workout_completed"
    WORKOUT_VOLUME = "workout_volume"
    TASK_COMPLETED = "task_completed"
    PROJECT_COMPLETED = "project_completed"
    TIMER_SESSION = "timer_session"
    DAILY_REVIEW = "daily_review"
    SLEEP_LOGGED = "sleep_logged"
    WEIGHT_LOGGED = "weight_logged"
    STREAK_BONUS = "streak_bonus"
    QUEST_COMPLETED = "quest_completed"
    MANUAL = "manual"


class StreakKind(UiEnum):
    MEAL_LOGGING = "meal_logging"
    WORKOUT = "workout"
    WATER = "water"
    STEPS = "steps"
    TASKS = "tasks"
    REVIEW = "review"
    OVERALL = "overall"


class AchievementRarity(UiEnum):
    COMMON = "common"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"


class QuestKind(UiEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class QuestStatus(UiEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CLAIMED = "claimed"


class XpEvent(UserScopedMixin, UuidPkMixin, Base):
    """Append-only XP ledger.  ``dedupe_key`` keeps awards idempotent."""

    __tablename__ = "xp_events"

    kind: Mapped[XpKind] = mapped_column(enum_col(XpKind, "xp_kind"), nullable=False, default=XpKind.MANUAL)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    awarded_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    local_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (
        CheckConstraint("points >= 0", name="points_non_negative"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_xp_events_user_dedupe"),
        Index("ix_xp_events_user_day", "user_id", "local_day"),
    )


class Level(UuidPkMixin, TimestampMixin, Base):
    """Level curve definition (global reference data, seeded in stage 8)."""

    __tablename__ = "levels"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    min_total_xp: Mapped[int] = mapped_column(Integer, nullable=False)
    title_ru: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    title_en: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    icon: Mapped[str | None] = mapped_column(String(48), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reward: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (CheckConstraint("level >= 1", name="level_positive"),)


class Achievement(UuidPkMixin, TimestampMixin, Base):
    """Definition of a badge (30+ seeded in stage 8)."""

    __tablename__ = "achievements"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name_ru: Mapped[str] = mapped_column(String(120), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    description_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    rarity: Mapped[AchievementRarity] = mapped_column(
        enum_col(AchievementRarity, "achievement_rarity"),
        nullable=False,
        default=AchievementRarity.COMMON,
    )
    # machine-readable condition, e.g. {"metric": "meals_logged", "gte": 100}
    criteria: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    xp_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UserAchievement(UserScopedMixin, UuidPkMixin, Base):
    __tablename__ = "user_achievements"

    achievement_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("achievements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    unlocked_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)
    progress: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    celebrated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("user_id", "achievement_id", name="uq_user_achievements"),)


class Streak(UserScopedMixin, UuidPkMixin, Base):
    __tablename__ = "streaks"

    kind: Mapped[StreakKind] = mapped_column(enum_col(StreakKind, "streak_kind"), nullable=False)
    current_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    longest_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    broken_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # weekly freeze token: consumes 1 to survive a single missed day
    freeze_tokens: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    freeze_used_dates: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    total_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_streaks_user_kind"),)


class DailyScore(UserScopedMixin, UuidPkMixin, Base):
    """0..100 composite score with a stored component breakdown."""

    __tablename__ = "daily_scores"

    local_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    components: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    weights: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    xp_earned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "local_day", name="uq_daily_scores_user_day"),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
    )


class Quest(UuidPkMixin, TimestampMixin, Base):
    """Quest definition (weekly/daily templates, seeded in stage 8)."""

    __tablename__ = "quests"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[QuestKind] = mapped_column(enum_col(QuestKind, "quest_kind"), nullable=False)
    title_ru: Mapped[str] = mapped_column(String(160), nullable=False)
    title_en: Mapped[str] = mapped_column(String(160), nullable=False)
    description_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. {"metric": "workouts_completed", "gte": 3}
    criteria: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    target_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=1)
    xp_reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    icon: Mapped[str | None] = mapped_column(String(48), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserQuest(UserScopedMixin, UuidPkMixin, Base):
    __tablename__ = "user_quests"

    quest_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("quests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    progress: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    status: Mapped[QuestStatus] = mapped_column(
        enum_col(QuestStatus, "quest_status"), nullable=False, default=QuestStatus.ACTIVE
    )
    completed_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "quest_id", "period_start", name="uq_user_quests_period"),
        Index("ix_user_quests_user_period", "user_id", "period_start"),
    )
