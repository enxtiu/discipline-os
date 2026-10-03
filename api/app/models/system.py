"""Cross-cutting tables: push subscriptions, notification log, AI usage."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
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


class PushSubscription(UserScopedMixin, UuidPkMixin, Base):
    """One Web Push endpoint (per device/browser)."""

    __tablename__ = "push_subscriptions"

    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    expiration_time: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failure_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_push_subscriptions_user_active", "user_id", "is_active"),)


class NotificationLog(UserScopedMixin, UuidPkMixin, Base):
    """Every notification we intended to send (sent, suppressed or failed)."""

    __tablename__ = "notifications_log"

    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("push_subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    reminder_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("reminders.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    scheduled_for: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    quiet_hours_suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_log_user_dedupe"),
        Index("ix_notifications_log_user_sent", "user_id", "sent_at"),
    )


# --------------------------------------------------------------------------- #
# AI jobs & usage
# --------------------------------------------------------------------------- #
class AiJobStatus(UiEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AiJobKind(UiEnum):
    ANALYZE_MEAL_PHOTO = "analyze_meal_photo"
    PARSE_FOOD_TEXT = "parse_food_text"
    SUGGEST_TASK_PARSE = "suggest_task_parse"
    WEEKLY_SUMMARY = "weekly_summary"


class AiJob(UserScopedMixin, UuidPkMixin, Base):
    """Audit record of every AI call (prompt version, usage, latency)."""

    __tablename__ = "ai_jobs"

    kind: Mapped[AiJobKind] = mapped_column(enum_col(AiJobKind, "ai_job_kind"), nullable=False)
    status: Mapped[AiJobStatus] = mapped_column(
        enum_col(AiJobStatus, "ai_job_status"), nullable=False, default=AiJobStatus.PENDING
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)

    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_thumb_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)

    __table_args__ = (Index("ix_ai_jobs_user_kind_created", "user_id", "kind", "created_at"),)


class AiUsageDaily(UserScopedMixin, UuidPkMixin, Base):
    """Counters backing the soft daily AI call limit."""

    __tablename__ = "ai_usage_daily"

    local_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)

    __table_args__ = (UniqueConstraint("user_id", "local_day", name="uq_ai_usage_daily_user_day"),)


# --------------------------------------------------------------------------- #
# Importers (stage 2 food DB) & maintenance
# --------------------------------------------------------------------------- #
class ImportJob(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "import_jobs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # off | usda | seed
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_etag: Mapped[str | None] = mapped_column(String(120), nullable=True)

    __table_args__ = (Index("ix_import_jobs_source_status", "source", "status"),)


class AppMeta(UuidPkMixin, TimestampMixin, Base):
    """Tiny key/value store (feature flags, last migration marker, etc.)."""

    __tablename__ = "app_meta"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    value: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
