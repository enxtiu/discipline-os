"""AI meal logging (stage 3): analysis results the user reviews before saving."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel
from app.schemas.food import FoodOut, MatchMethodLiteral, MealTypeLiteral

ConfidenceLevel = Literal["high", "medium", "low"]


class AiPer100Out(ApiModel):
    kcal_per_100g: float
    protein_per_100g: float
    fat_per_100g: float
    carbs_per_100g: float
    fiber_per_100g: float


class AiMatchOut(ApiModel):
    """The local food the item was resolved to (custom and verified rows first)."""

    food: FoodOut
    match_method: MatchMethodLiteral
    score: float


class AiItemOut(ApiModel):
    key: str = Field(description="stable id of this suggestion inside the analysis")
    name_ru: str
    name_en: str
    estimated_grams: float = Field(description="an estimate — the user adjusts it on the review screen")
    confidence: float
    confidence_level: ConfidenceLevel
    # the model's numbers for estimated_grams (spec: items[] kcal/protein/fat/carbs/fiber)
    kcal: float
    protein: float
    fat: float
    carbs: float
    fiber: float
    ai_per_100g: AiPer100Out
    kcal_adjusted: bool = Field(description="energy was recomputed from the macros (model disagreed with itself)")
    # None → nothing local matched: the review shows the AI macros, flagged source=ai
    match: AiMatchOut | None = None
    alternatives: list[FoodOut] = Field(default_factory=list, description="other local candidates to swap to")


class AiUsageOut(ApiModel):
    enabled: bool
    provider: str | None = None
    calls_today: int
    daily_limit: int
    remaining: int | None = None


class AiAnalysisOut(ApiModel):
    job_id: uuid.UUID
    kind: Literal["photo", "text"]
    provider: str
    model: str
    prompt_version: str
    took_ms: int
    items: list[AiItemOut]
    notes: str | None = None
    thumbnail_kept: bool = Field(description="a 400 px thumbnail waits for the save (setting 'keep thumbnail')")
    usage: AiUsageOut


class AiTextIn(ApiModel):
    text: str = Field(min_length=1, max_length=4000, description="free text in Russian or English (dictation OK)")
    meal_type: MealTypeLiteral | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text is empty")
        return value
