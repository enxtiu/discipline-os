"""Nutrition schemas: foods, search, favorites/recents, recipes, meal logs.

Contract notes
--------------
* Macros travel as ``float`` (the DB keeps ``NUMERIC(8,2)``; two decimals are
  more than nutrition needs and floats keep the JSON/small and the client dumb).
* Every write that creates history accepts a client-generated ``id``
  (``client_uuid``) so offline queues can replay safely.
* ``local_day`` is always computed server-side from the user's timezone — the
  client sends wall-clock ``logged_at`` or nothing at all.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, ORMModel, UuidStr

MealTypeLiteral = Literal["breakfast", "lunch", "dinner", "snack", "other"]
MatchMethodLiteral = Literal["exact", "fts", "trgm", "barcode", "manual", "ai", "none"]
ItemSourceLiteral = Literal["food", "recipe", "ai", "custom"]
MealSourceLiteral = Literal[
    "photo", "text", "search", "barcode", "favorite", "recent", "recipe", "copy", "manual", "import"
]

GRAMS = Field(gt=0, le=100_000, description="grams (or ml for liquids)")


class MacrosOut(ApiModel):
    kcal: float = 0
    protein_g: float = 0
    fat_g: float = 0
    carbs_g: float = 0
    fiber_g: float = 0


# --------------------------------------------------------------------------- #
# Foods
# --------------------------------------------------------------------------- #
class FoodOut(ORMModel):
    id: uuid.UUID
    is_global: bool
    name_ru: str | None = None
    name_en: str | None = None
    aliases: list[str] = Field(default_factory=list)
    brand: str | None = None
    barcode: str | None = None
    category: str | None = None
    kcal_per_100g: float
    protein_per_100g: float
    fat_per_100g: float
    carbs_per_100g: float
    fiber_per_100g: float
    default_serving_g: float | None = None
    default_serving_label: str | None = None
    is_liquid: bool = False
    source: str
    verified: bool = False
    use_count: int = 0
    is_favorite: bool = False
    favorite_grams: float | None = None


class FoodMatchOut(FoodOut):
    score: float
    match_method: MatchMethodLiteral


class FoodSearchOut(ApiModel):
    query: str
    items: list[FoodMatchOut]
    took_ms: int
    limit: int
    has_more: bool = False


class FoodBarcodeOut(ApiModel):
    food: FoodOut | None = None
    barcode: str
    # local: library/own food · off: fetched live from Open Food Facts and cached
    # miss: unknown (or incomplete on OFF) · unavailable: OFF unreachable, retry
    # later · invalid: not an EAN-8…GTIN-14 code
    lookup: Literal["local", "off", "miss", "unavailable", "invalid"] = "local"


class FoodIn(ApiModel):
    """Creating a custom ("my food") row."""

    id: UuidStr | None = None
    name_ru: str | None = None
    name_en: str | None = None
    aliases: list[str] = Field(default_factory=list, max_length=12)
    brand: str | None = Field(default=None, max_length=160)
    barcode: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=120)
    kcal_per_100g: float = Field(ge=0, le=5000)
    protein_per_100g: float = Field(default=0, ge=0, le=100)
    fat_per_100g: float = Field(default=0, ge=0, le=100)
    carbs_per_100g: float = Field(default=0, ge=0, le=100)
    fiber_per_100g: float = Field(default=0, ge=0, le=100)
    default_serving_g: float | None = Field(default=None, gt=0, le=5000)
    default_serving_label: str | None = Field(default=None, max_length=64)
    is_liquid: bool = False

    @field_validator("barcode")
    @classmethod
    def _digits(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class FoodPatchIn(ApiModel):
    name_ru: str | None = None
    name_en: str | None = None
    aliases: list[str] | None = Field(default=None, max_length=12)
    brand: str | None = Field(default=None, max_length=160)
    category: str | None = Field(default=None, max_length=120)
    kcal_per_100g: float | None = Field(default=None, ge=0, le=5000)
    protein_per_100g: float | None = Field(default=None, ge=0, le=100)
    fat_per_100g: float | None = Field(default=None, ge=0, le=100)
    carbs_per_100g: float | None = Field(default=None, ge=0, le=100)
    fiber_per_100g: float | None = Field(default=None, ge=0, le=100)
    default_serving_g: float | None = Field(default=None, gt=0, le=5000)
    default_serving_label: str | None = Field(default=None, max_length=64)
    is_liquid: bool | None = None
    verified: bool | None = None


class FavoriteIn(ApiModel):
    food_id: uuid.UUID
    default_grams: float | None = Field(default=None, gt=0, le=100_000)


class FavoriteOut(ORMModel):
    id: uuid.UUID
    food_id: uuid.UUID
    default_grams: float | None = None
    sort_order: int = 0
    food: FoodOut


class RecentOut(ORMModel):
    id: uuid.UUID
    food: FoodOut | None = None
    recipe: RecipeBriefOut | None = None
    last_grams: float | None = None
    use_count: int
    last_used_at: datetime


class RecentListOut(ApiModel):
    items: list[RecentOut]


# --------------------------------------------------------------------------- #
# Recipes
# --------------------------------------------------------------------------- #
class RecipeBriefOut(ORMModel):
    id: uuid.UUID
    name: str
    servings: float
    portion_size_g: float
    kcal_per_100g: float
    protein_per_100g: float
    fat_per_100g: float
    carbs_per_100g: float
    fiber_per_100g: float
    is_pinned: bool = False
    use_count: int = 0


class RecipeItemIn(ApiModel):
    food_id: uuid.UUID
    grams: float = GRAMS
    note: str | None = Field(default=None, max_length=160)


class RecipeItemOut(ORMModel):
    id: uuid.UUID
    food_id: uuid.UUID
    grams: float
    sort_order: int
    note: str | None = None
    snapshot_name: str
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float


class RecipeIn(ApiModel):
    id: UuidStr | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    notes_md: str | None = None
    servings: float = Field(default=1, gt=0, le=1000)
    cooked_weight_g: float | None = Field(default=None, ge=0, le=100_000)
    # alternative to servings: one portion weighs X g (servings = cooked / X)
    portion_size_g: float | None = Field(default=None, gt=0, le=100_000)
    items: list[RecipeItemIn] = Field(min_length=1, max_length=80)
    is_pinned: bool = False


class RecipePatchIn(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    notes_md: str | None = None
    servings: float | None = Field(default=None, gt=0, le=1000)
    cooked_weight_g: float | None = Field(default=None, ge=0, le=100_000)
    portion_size_g: float | None = Field(default=None, gt=0, le=100_000)
    items: list[RecipeItemIn] | None = Field(default=None, min_length=1, max_length=80)
    is_pinned: bool | None = None


class RecipeOut(RecipeBriefOut):
    description: str | None = None
    notes_md: str | None = None
    total_raw_weight_g: float
    cooked_weight_g: float
    total_kcal: float
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
    total_fiber_g: float
    per_serving: MacrosOut
    items: list[RecipeItemOut] = Field(default_factory=list)


class RecipeListOut(ApiModel):
    items: list[RecipeOut]


# --------------------------------------------------------------------------- #
# Meal logging
# --------------------------------------------------------------------------- #
class MealItemIn(ApiModel):
    food_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    grams: float = GRAMS
    quantity: float = Field(default=1, gt=0, le=10_000)
    unit: str = Field(default="g", max_length=24)
    # custom (unmatched) item: the user typed macros by hand
    name: str | None = Field(default=None, max_length=255)
    name_en: str | None = Field(default=None, max_length=255)
    kcal_per_100g: float | None = Field(default=None, ge=0, le=5000)
    protein_per_100g: float | None = Field(default=None, ge=0, le=100)
    fat_per_100g: float | None = Field(default=None, ge=0, le=100)
    carbs_per_100g: float | None = Field(default=None, ge=0, le=100)
    fiber_per_100g: float | None = Field(default=None, ge=0, le=100)
    match_method: MatchMethodLiteral = "manual"

    @field_validator("grams")
    @classmethod
    def _needs_target(cls, value: float) -> float:
        return value


class MealItemOut(ORMModel):
    id: uuid.UUID
    food_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    name: str
    name_en: str | None = None
    brand: str | None = None
    grams: float
    quantity: float
    unit: str
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    item_source: ItemSourceLiteral
    match_method: MatchMethodLiteral
    match_score: float | None = None
    sort_order: int


class MealLogIn(ApiModel):
    id: UuidStr | None = Field(default=None, description="client UUID → idempotent replay")
    logged_at: datetime | None = Field(default=None, description="defaults to now (UTC)")
    meal_type: MealTypeLiteral = "other"
    source: MealSourceLiteral = "search"
    title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    items: list[MealItemIn] = Field(min_length=1, max_length=60)


class MealLogPatchIn(ApiModel):
    logged_at: datetime | None = None
    meal_type: MealTypeLiteral | None = None
    title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    items: list[MealItemIn] | None = Field(default=None, min_length=1, max_length=60)


class MealLogOut(ORMModel):
    id: uuid.UUID
    client_uuid: uuid.UUID
    logged_at: datetime
    local_day: date
    meal_type: MealTypeLiteral
    source: MealSourceLiteral
    title: str | None = None
    notes: str | None = None
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    item_count: int
    items: list[MealItemOut] = Field(default_factory=list)


class MealLogListOut(ApiModel):
    items: list[MealLogOut]


class CopyMealIn(ApiModel):
    from_day: date
    meal_type: MealTypeLiteral | None = None
    to_meal_type: MealTypeLiteral | None = None
    logged_at: datetime | None = None


class CopiedMealOut(ApiModel):
    copied: int
    meals: list[MealLogOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Day dashboard + water
# --------------------------------------------------------------------------- #
class MealTypeTotalOut(ApiModel):
    meal_type: MealTypeLiteral
    meals: int = 0
    kcal: float = 0
    protein_g: float = 0
    fat_g: float = 0
    carbs_g: float = 0
    fiber_g: float = 0


class WaterLogOut(ORMModel):
    id: uuid.UUID
    client_uuid: uuid.UUID
    logged_at: datetime
    amount_ml: int
    source: str = "manual"


class WaterIn(ApiModel):
    id: UuidStr | None = None
    amount_ml: int = Field(gt=0, le=5000)
    logged_at: datetime | None = None
    source: str = Field(default="manual", max_length=24)


class DaySummaryOut(ApiModel):
    date: date
    totals: MacrosOut
    by_meal_type: list[MealTypeTotalOut]
    meals: list[MealLogOut]
    water_ml: int = 0
    water_logs: list[WaterLogOut] = Field(default_factory=list)
    logged_meals: int = 0


# fix forward reference (RecentOut → RecipeBriefOut)
RecentOut.model_rebuild()
