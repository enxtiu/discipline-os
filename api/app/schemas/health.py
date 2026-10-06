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
IngestSourceLiteral = Literal["shortcut", "apple_health", "import"]
_SPACES = re.compile(r"[\s  ']")
_COMMA_GROUPS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")  # "8,342" / "1,234.5" (en-US grouping)
_EARLIEST = datetime(2000, 1, 1)
_LATEST = datetime(2100, 1, 1)


def _lenient_number(value: Any) -> Any:
    """Shortcuts may send numbers as text: "8 342", "512,5" (ru) or "8,342" (en)."""
    if isinstance(value, str):
        cleaned = _SPACES.sub("", value)
        if cleaned == "":
            return None
        cleaned = cleaned.replace(",", "") if _COMMA_GROUPS.match(cleaned) else cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return value  # let pydantic report it
    return value


def _sane_datetime(value: Any) -> Any:
    """Years 2000–2099 only: anything else is a typo, and 9999-12-31 overflows timezone maths."""
    if isinstance(value, datetime):
        naive = value.replace(tzinfo=None)
        if not _EARLIEST <= naive < _LATEST:
            raise ValueError("date out of range")
    return value


def _round_within(value: float | None, digits: int, low: float, high: float) -> float | None:
    """Round as stored, then check the bounds the database enforces (20.004 kg would round to 20.0)."""
    if value is None:
        return None
    rounded = round(value, digits)
    if not low < rounded < high:
        raise ValueError(f"must be between {low} and {high}")
    return rounded


Num = Annotated[float, BeforeValidator(_lenient_number)]
OptNum = Annotated[float | None, BeforeValidator(_lenient_number)]


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
    steps: OptNum = Field(default=None, ge=0, le=200_000)
    active_kcal: OptNum = Field(default=None, ge=0, le=10_000)
    basal_kcal: OptNum = Field(default=None, ge=0, le=10_000)
    distance_m: OptNum = Field(default=None, ge=0, le=300_000)
    distance_km: OptNum = Field(
        default=None, ge=0, le=300, description="alternative to distance_m (Health reports km in metric locales)"
    )
    floors: OptNum = Field(default=None, ge=0, le=2000)
    resting_hr: OptNum = Field(default=None, ge=20, le=250)
    hrv_ms: OptNum = Field(default=None, ge=1, le=500)
    source: IngestSourceLiteral = "shortcut"

    @field_validator("date", mode="before")
    @classmethod
    def _blank_date(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("date")
    @classmethod
    def _date_range(cls, value: Any) -> Any:
        return _sane_datetime(value)

    @model_validator(mode="after")
    def _something(self) -> HealthDayIn:
        if all(
            getattr(self, key) is None
            for key in (
                "steps",
                "active_kcal",
                "basal_kcal",
                "distance_m",
                "distance_km",
                "floors",
                "resting_hr",
                "hrv_ms",
            )
        ):
            raise ValueError("send at least one value (steps, active_kcal, distance_m, …)")
        if self.distance_m is None and self.distance_km is not None:
            self.distance_m = self.distance_km * 1000
        return self


class HealthBatchIn(HealthIn):
    days: list[HealthDayIn] = Field(min_length=1, max_length=62)


class WeightIngestIn(HealthIn):
    date: Date | datetime | None = None
    weight_kg: Num = Field(gt=20, lt=400)
    body_fat_pct: OptNum = Field(default=None, gt=2, lt=70)

    @field_validator("date")
    @classmethod
    def _date_range(cls, value: Any) -> Any:
        return _sane_datetime(value)

    @field_validator("weight_kg")
    @classmethod
    def _kg(cls, value: float) -> float | None:
        return _round_within(value, 2, 20, 400)

    @field_validator("body_fat_pct")
    @classmethod
    def _fat(cls, value: float | None) -> float | None:
        return _round_within(value, 1, 2, 70)


class SleepIngestIn(HealthIn):
    bed_at: datetime
    wake_at: datetime
    quality: int | None = Field(default=None, ge=1, le=5)
    awakenings: int | None = Field(default=None, ge=0, le=50)

    @field_validator("bed_at", "wake_at")
    @classmethod
    def _date_range(cls, value: datetime) -> datetime:
        return _sane_datetime(value)


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

    @field_validator("weight_kg")
    @classmethod
    def _kg(cls, value: float) -> float | None:
        return _round_within(value, 2, 20, 400)

    @field_validator("body_fat_pct")
    @classmethod
    def _fat(cls, value: float | None) -> float | None:
        return _round_within(value, 1, 2, 70)

    @field_validator("waist_cm")
    @classmethod
    def _waist(cls, value: float | None) -> float | None:
        return _round_within(value, 1, 30, 300)


class SleepIn(HealthIn):
    """``bed_time``/``wake_time`` are local wall-clock times; bed before midnight is the previous day."""

    bed_time: time
    wake_time: time
    quality: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("bed_time", "wake_time")
    @classmethod
    def _wall_clock(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("send local wall-clock time without a UTC offset")
        return value.replace(second=0, microsecond=0)


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
    on_target: bool = Field(default=False, description="steps reached the target that day")
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
