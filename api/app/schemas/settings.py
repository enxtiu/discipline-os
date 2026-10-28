"""Settings & onboarding schemas: profile, body params, goals, targets."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.appearance import AppearanceBaseIn, AppearanceIn, AppearanceOut, coerce_stored
from app.schemas.common import ApiModel, ORMModel
from app.schemas.gamification import validate_xp_weights

LanguageLiteral = Literal["ru", "en"]
SexLiteral = Literal["male", "female", "other"]
ActivityLiteral = Literal["sedentary", "light", "moderate", "active", "very_active"]
GoalModeLiteral = Literal["lose", "maintain", "gain"]
FormulaLiteral = Literal["auto", "mifflin", "katch"]


class TargetsOut(ApiModel):
    bmr_kcal: int | None = None
    tdee_kcal: int | None = None
    target_kcal: int | None = None
    target_protein_g: int | None = None
    target_fat_g: int | None = None
    target_carbs_g: int | None = None
    target_fiber_g: int | None = None
    target_water_ml: int | None = None
    target_steps: int | None = None
    safe_floor_kcal: int
    deficit_pct: float | None = None
    formula_used: str
    warnings: list[str] = Field(default_factory=list)
    macros_kcal: dict[str, int] = Field(default_factory=dict)
    is_manual: bool = False


class TargetsIn(ApiModel):
    """Partial or full manual target override."""

    target_kcal: int | None = Field(default=None, ge=1000, le=8000)
    target_protein_g: int | None = Field(default=None, ge=0, le=500)
    target_fat_g: int | None = Field(default=None, ge=0, le=400)
    target_carbs_g: int | None = Field(default=None, ge=0, le=900)
    target_fiber_g: int | None = Field(default=None, ge=0, le=120)
    target_water_ml: int | None = Field(default=None, ge=200, le=8000)
    target_steps: int | None = Field(default=None, ge=0, le=60000)
    # must be set when the user intentionally goes below the safe floor
    confirm_floor: bool = False


class TargetPreviewIn(ApiModel):
    """Stateless computation used by the onboarding wizard."""

    sex: SexLiteral | None = None
    birth_date: date | None = None
    age_years: int | None = Field(default=None, ge=10, le=100)
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=30, le=300)
    body_fat_pct: float | None = Field(default=None, ge=2, le=70)
    activity_level: ActivityLiteral = "light"
    formula: FormulaLiteral = "auto"
    goal_mode: GoalModeLiteral = "maintain"
    goal_rate_pct: int = Field(default=0, ge=0, le=30)
    protein_g_per_kg: float = Field(default=2.0, ge=1.0, le=3.5)
    fat_min_g_per_kg: float = Field(default=0.8, ge=0.4, le=2.0)
    water_ml_per_kg: float = Field(default=33.0, ge=25, le=45)
    safe_floor_kcal: int | None = Field(default=None, ge=1000, le=3500)
    target_steps: int = Field(default=8000, ge=0, le=60000)


class SettingsBaseIn(ApiModel):
    """``base`` of ``PATCH /settings`` (stage 9 rules, see docs/stage-10-appearance-sync.md §3):
    the values an edit started from. Only ``appearance`` takes part today: ``null`` = "the
    account had none", an object = the keys the edit depends on."""

    appearance: AppearanceBaseIn | None = None


class SettingsUpdateIn(ApiModel):
    language: LanguageLiteral | None = None
    timezone: str | None = Field(default=None, max_length=64)
    timezone_auto: bool | None = None
    week_start: int | None = Field(default=None, ge=0, le=6)
    units: Literal["metric"] | None = None

    quiet_hours_enabled: bool | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None

    birth_date: date | None = None
    sex: SexLiteral | None = None
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=30, le=300)
    body_fat_pct: float | None = Field(default=None, ge=2, le=70)
    activity_level: ActivityLiteral | None = None
    bmr_formula: FormulaLiteral | None = None

    goal_mode: GoalModeLiteral | None = None
    goal_rate_pct: int | None = Field(default=None, ge=0, le=30)
    goal_weight_kg: float | None = Field(default=None, ge=30, le=300)
    goal_protein_g_per_kg: float | None = Field(default=None, ge=1.0, le=3.5)
    goal_fat_min_g_per_kg: float | None = Field(default=None, ge=0.4, le=2.0)
    goal_water_ml_per_kg: float | None = Field(default=None, ge=25, le=45)

    targets_manual: bool | None = None
    target_steps: int | None = Field(default=None, ge=0, le=60000)
    safe_floor_kcal: int | None = Field(default=None, ge=1000, le=3500)
    confirm_floor: bool = False

    keep_photo_thumbnail: bool | None = None
    onboarding_completed: bool | None = None
    notification_prefs: dict[str, Any] | None = None
    xp_weights: dict[str, Any] | None = None

    # stage 10: any subset of the keys (merged onto the stored object); null = back to "never synced"
    appearance: AppearanceIn | None = None
    # stage 10: field-level compare-and-set, as the planner's edits (stage 9); absent = last write wins
    base: SettingsBaseIn | None = None

    @field_validator("xp_weights")
    @classmethod
    def _xp_weights(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """stage 8: ``score_weights`` 0..100 per score part, ``xp_multipliers`` 0..2 per category."""
        return validate_xp_weights(value)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value


class BodyParamsOut(ApiModel):
    sex: str | None = None
    birth_date: date | None = None
    age_years: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    activity_level: str
    formula: str
    lean_body_mass_kg: float | None = None
    bmi: float | None = None


class OnboardingIn(ApiModel):
    """One-shot payload from the first-run wizard (all fields optional)."""

    language: LanguageLiteral = "ru"
    timezone: str | None = None
    sex: SexLiteral | None = None
    birth_date: date | None = None
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=30, le=300)
    body_fat_pct: float | None = Field(default=None, ge=2, le=70)
    activity_level: ActivityLiteral = "light"
    goal_mode: GoalModeLiteral = "maintain"
    goal_rate_pct: int = Field(default=0, ge=0, le=30)
    goal_weight_kg: float | None = Field(default=None, ge=30, le=300)
    target_steps: int = Field(default=8000, ge=0, le=60000)
    quiet_hours_start: time = time(23, 0)
    quiet_hours_end: time = time(7, 0)
    notifications: dict[str, bool] = Field(default_factory=dict)


class UserSettingsOut(ORMModel):
    user_id: uuid.UUID
    language: str
    timezone: str
    timezone_auto: bool
    week_start: int
    units: str

    quiet_hours_enabled: bool
    quiet_hours_start: time
    quiet_hours_end: time

    birth_date: date | None = None
    sex: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    activity_level: str
    bmr_formula: str

    goal_mode: str
    goal_rate_pct: int
    goal_weight_kg: float | None = None
    goal_protein_g_per_kg: float
    goal_fat_min_g_per_kg: float
    goal_water_ml_per_kg: float

    targets_manual: bool
    target_kcal: int | None = None
    target_protein_g: int | None = None
    target_fat_g: int | None = None
    target_carbs_g: int | None = None
    target_fiber_g: int | None = None
    target_water_ml: int | None = None
    target_steps: int

    safe_floor_kcal: int | None = None
    safe_floor_confirmed_at: datetime | None = None

    keep_photo_thumbnail: bool
    onboarding_completed: bool
    notification_prefs: dict = Field(default_factory=dict)
    xp_weights: dict = Field(default_factory=dict)
    # stage 10: the account's appearance; null = never synced (see schemas/appearance.py)
    appearance: AppearanceOut | None = None

    updated_at: datetime

    @field_validator("appearance", mode="before")
    @classmethod
    def _stored_appearance(cls, value: Any) -> Any:
        return coerce_stored(value)


class SettingsEnvelopeOut(ApiModel):
    """The settings screen payload: the row itself + the derived targets."""

    settings: UserSettingsOut
    targets: TargetsOut
