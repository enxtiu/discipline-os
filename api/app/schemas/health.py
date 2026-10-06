"""Health (stage 5): steps / active energy ingest, manual steps, weight and sleep.

Ingest payloads come from an iOS Shortcut, so numbers are accepted leniently:
``8342``, ``8342.0``, ``"8 342"`` and ``"8342,5"`` all parse. Everything is
metric; days are the user's local calendar days.
"""

from __future__ import annotations

import re
from datetime import date as Date
from datetime import datetime, time
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import ApiModel

SourceLiteral = Literal["shortcut", "manual", "apple_health", "import"]
_SPACES = re.compile(r"[\s  ']")


def _lenient_number(value: Any) -> Any:
    """Shortcuts may send numbers as text, with spaces or a decimal comma."""
    if isinstance(value, str):
        cleaned = _SPACES.sub("", value).replace(",", ".")
        if cleaned == "":
            return None
        try:
            return float(cleaned)
        except ValueError:
            return value  # let pydantic report it
    return value


Num = Annotated[float, BeforeValidator(_lenient_number)]


class HealthIn(ApiModel):
    """Strict-ish base for every request of this module (no NaN/Infinity)."""

    model_config = ConfigDict(allow_inf_nan=False)


# --------------------------------------------------------------------------- #
# Ingest (scoped API token)
# --------------------------------------------------------------------------- #
class HealthDayIn(HealthIn):
    date: Date | datetime | None = Field(
        default=None, description="local day (YYYY-MM-DD); a timestamp is converted to your timezone; default today"
    )
    steps: Num | None = Field(default=None, ge=0, le=200_000)
    active_kcal: Num | None = Field(default=None, ge=0, le=10_000)
    basal_kcal: Num | None = Field(default=None, ge=0, le=10_000)
    distance_m: Num | None = Field(default=None, ge=0, le=300_000)
    floors: Num | None = Field(default=None, ge=0, le=2000)
    resting_hr: Num | None = Field(default=None, ge=20, le=250)
    hrv_ms: Num | None = Field(default=None, ge=1, le=500)
    source: SourceLiteral = "shortcut"

    @field_validator("date", mode="before")
    @classmethod
    def _blank_date(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _something(self) -> HealthDayIn:
        if all(
            getattr(self, key) is None
            for key in ("steps", "active_kcal", "basal_kcal", "distance_m", "floors", "resting_hr", "hrv_ms")
        ):
            raise ValueError("send at least one value (steps, active_kcal, distance_m, …)")
        return self


class HealthBatchIn(HealthIn):
    days: list[HealthDayIn] = Field(min_length=1, max_length=62)


class WeightIngestIn(HealthIn):
    date: Date | datetime | None = None
    weight_kg: Num = Field(gt=20, lt=400)
    body_fat_pct: Num | None = Field(default=None, gt=2, lt=70)


class SleepIngestIn(HealthIn):
    bed_at: datetime
    wake_at: datetime
    quality: int | None = Field(default=None, ge=1, le=5)
    awakenings: int | None = Field(default=None, ge=0, le=50)


# --------------------------------------------------------------------------- #
# App (device) — manual entries
# --------------------------------------------------------------------------- #
class StepsIn(HealthIn):
    steps: int = Field(ge=0, le=200_000)
    distance_m: int | None = Field(default=None, ge=0, le=300_000)
    active_kcal: float | None = Field(default=None, ge=0, le=10_000)


class WeightIn(HealthIn):
    weight_kg: float = Field(gt=20, lt=400)
    body_fat_pct: float | None = Field(default=None, gt=2, lt=70)
    waist_cm: float | None = Field(default=None, gt=30, lt=300)
    note: str | None = Field(default=None, max_length=200)


class SleepIn(HealthIn):
    """``bed_time``/``wake_time`` are local wall-clock times; bed before midnight is the previous day."""

    bed_time: time
    wake_time: time
    quality: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=300)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class HealthDayOut(ApiModel):
    date: Date
    steps: int = 0
    target_steps: int = 0
    distance_m: int | None = None
    active_kcal: float | None = None
    floors: int | None = None
    resting_hr: int | None = None
    hrv_ms: int | None = None
    steps_kcal: float | None = Field(
        default=None, description="active energy from Health, else an estimate from steps — never food budget"
    )
    steps_kcal_estimated: bool = False
    source: SourceLiteral | None = None
    updated_at: datetime | None = None


class IngestResultOut(ApiModel):
    day: HealthDayOut
    created: bool


class BatchResultOut(ApiModel):
    days: list[HealthDayOut]
    created: int
    updated: int


class WeightOut(ApiModel):
    date: Date
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    waist_cm: float | None = None
    note: str | None = None
    source: str


class WeightSeriesOut(ApiModel):
    items: list[WeightOut] = Field(description="oldest first")
    latest: WeightOut | None = None
    trend_kg: float | None = Field(default=None, description="7-day average of the latest weigh-ins")
    change_30d_kg: float | None = Field(default=None, description="latest minus the weigh-in ~30 days earlier")


class SleepOut(ApiModel):
    date: Date = Field(description="the day you woke up")
    bed_at: datetime
    wake_at: datetime
    duration_min: int
    quality: int | None = None
    note: str | None = None
    source: str


class SleepSeriesOut(ApiModel):
    items: list[SleepOut] = Field(description="newest first")
    average_min: int | None = None


class StepsSeriesOut(ApiModel):
    days: list[HealthDayOut] = Field(description="oldest first, zero-filled")
    target_steps: int
    average_steps: int
    days_on_target: int


class HealthTodayOut(ApiModel):
    day: HealthDayOut
    weight: WeightOut | None = None
    weight_change_7d_kg: float | None = None
    sleep: SleepOut | None = None
    last_sync_at: datetime | None = Field(default=None, description="last Shortcut/Health ingest")
