"""Identity: users, settings, devices, pairing codes, scoped API tokens."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
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
    Time,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    JsonType,
    TimestampMixin,
    TzDateTime,
    UiEnum,
    UuidPkMixin,
    UuidType,
    enum_col,
    uuid_fk,
)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class LanguageCode(UiEnum):
    RU = "ru"
    EN = "en"


class UserStatus(UiEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Sex(UiEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class ActivityLevel(UiEnum):
    """Multipliers for TDEE (Harris-Benedict style activity factors)."""

    SEDENTARY = "sedentary"  # 1.2  — desk, no training
    LIGHT = "light"  # 1.375
    MODERATE = "moderate"  # 1.55
    ACTIVE = "active"  # 1.725
    VERY_ACTIVE = "very_active"  # 1.9


ACTIVITY_FACTORS: dict[ActivityLevel, float] = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.ACTIVE: 1.725,
    ActivityLevel.VERY_ACTIVE: 1.9,
}


class GoalMode(UiEnum):
    LOSE = "lose"
    MAINTAIN = "maintain"
    GAIN = "gain"


class BmrFormula(UiEnum):
    AUTO = "auto"  # Katch-McArdle if body fat known, else Mifflin-St Jeor
    MIFFLIN = "mifflin"
    KATCH = "katch"


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
class User(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="Owner")
    status: Mapped[UserStatus] = mapped_column(
        enum_col(UserStatus, "user_status"), nullable=False, default=UserStatus.ACTIVE
    )
    # The owner is `is_owner=True`; more users can be created later.
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # argon2 hash of an optional recovery phrase (no passwords in this product)
    recovery_phrase_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_devices: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)

    settings: Mapped[UserSettings] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    devices: Mapped[list[Device]] = relationship(back_populates="user", cascade="all, delete-orphan")
    api_tokens: Mapped[list[ApiToken]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSettings(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # ---- locale & time -----------------------------------------------------
    language: Mapped[LanguageCode] = mapped_column(
        enum_col(LanguageCode, "language_code"), nullable=False, default=LanguageCode.RU
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Bangkok")
    timezone_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    week_start: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)  # 0=Sun,1=Mon
    units: Mapped[str] = mapped_column(String(16), nullable=False, default="metric")

    # ---- quiet hours -------------------------------------------------------
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quiet_hours_start: Mapped[time] = mapped_column(Time, nullable=False, default=time(23, 0))
    quiet_hours_end: Mapped[time] = mapped_column(Time, nullable=False, default=time(7, 0))

    # ---- body parameters (manual, no watch) --------------------------------
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[Sex | None] = mapped_column(enum_col(Sex, "sex"), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    # Profile weight used by every calculation. It is a *cache* of the newest
    # body_metrics row (kept in sync by `services.settings_service`), so the
    # dashboard never needs a second query and onboarding works before the
    # first weigh-in exists.
    weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    activity_level: Mapped[ActivityLevel] = mapped_column(
        enum_col(ActivityLevel, "activity_level"), nullable=False, default=ActivityLevel.LIGHT
    )
    bmr_formula: Mapped[BmrFormula] = mapped_column(
        enum_col(BmrFormula, "bmr_formula"), nullable=False, default=BmrFormula.AUTO
    )

    # ---- goal --------------------------------------------------------------
    goal_mode: Mapped[GoalMode] = mapped_column(
        enum_col(GoalMode, "goal_mode"), nullable=False, default=GoalMode.MAINTAIN
    )
    # deficit (lose) / surplus (gain) preset in percent: 0/10/15/20
    goal_rate_pct: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    goal_weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    goal_protein_g_per_kg: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=2.0)
    goal_fat_min_g_per_kg: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=0.8)
    goal_water_ml_per_kg: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False, default=33.0)

    # ---- targets (auto-computed unless targets_manual) ---------------------
    targets_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    target_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_protein_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_fat_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_carbs_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_fiber_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_water_ml: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)

    # ---- safety ------------------------------------------------------------
    # Never recommend or reward an intake below this floor.  Editable only via
    # an explicit confirmation on the client; defaults: 1500 (m) / 1200 (f).
    safe_floor_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safe_floor_confirmed_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)

    # ---- behaviour ---------------------------------------------------------
    keep_photo_thumbnail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notification_prefs: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    xp_weights: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    extras: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))

    user: Mapped[User] = relationship(back_populates="settings")

    __table_args__ = (
        sa.CheckConstraint("goal_rate_pct >= 0 AND goal_rate_pct <= 30", name="goal_rate_range"),
        sa.CheckConstraint("week_start >= 0 AND week_start <= 6", name="week_start_range"),
        sa.CheckConstraint("safe_floor_kcal IS NULL OR safe_floor_kcal >= 1000", name="safe_floor_min"),
    )


# --------------------------------------------------------------------------- #
# Devices & auth
# --------------------------------------------------------------------------- #
class Device(UuidPkMixin, TimestampMixin, Base):
    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Device")
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ios | android | web
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # sha256 of a client-generated fingerprint (helps detect duplicates)
    fingerprint_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=sa.text("'[\"*\"]'::jsonb"))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship(back_populates="devices")

    __table_args__ = (Index("ix_devices_user_active", "user_id", "is_active"),)


class PairingCode(UuidPkMixin, TimestampMixin, Base):
    """One-time (or short-window, N-use) code that mints a device token."""

    __tablename__ = "pairing_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # HMAC-SHA256(normalised_code, SECRET_KEY)
    code_hmac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    code_hint: Mapped[str] = mapped_column(String(16), nullable=False)  # "K7QP-…"
    expires_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False, index=True)
    max_uses: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    uses: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    first_used_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="cli")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (sa.CheckConstraint("uses >= 0 AND uses <= max_uses", name="uses_bounds"),)

    @property
    def is_exhausted(self) -> bool:
        return self.uses >= self.max_uses

    def is_valid(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now and self.uses < self.max_uses


class ApiToken(UuidPkMixin, TimestampMixin, Base):
    """Scoped bearer token for automations (iOS Shortcuts, scripts)."""

    __tablename__ = "api_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped[User] = relationship(back_populates="api_tokens")


class AuthEvent(UuidPkMixin, TimestampMixin, Base):
    """Audit trail for pairing / revocation (security forensics)."""

    __tablename__ = "auth_events"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID | None] = uuid_fk("devices.id", ondelete="SET NULL", nullable=True)
    event: Mapped[str] = mapped_column(String(48), nullable=False)  # pairing_succeeded, ...
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))

    __table_args__ = (Index("ix_auth_events_event_created", "event", "created_at"),)
