"""The one output contract every provider must meet.

Models return per-100 g values (they know reference tables far better than
they know a specific plate), plus their estimate of the grams on the plate.
The API derives the portion numbers from both; the review screen rescales them
when the user moves the grams slider.

``MEAL_ITEMS_SCHEMA`` is a *strict* JSON schema (OpenAI structured outputs
rules: every property required, ``additionalProperties: false``, nullable as a
type union) and is translated per provider in ``providers.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ITEMS = 15

_ITEM_PROPERTIES = {
    "name_ru": {"type": "string", "description": "Russian name of the food, as on a Russian menu"},
    "name_en": {"type": "string", "description": "English name of the same food"},
    "estimated_grams": {"type": "number", "description": "Estimated edible weight on the plate, grams"},
    "confidence": {"type": "number", "description": "0..1, how sure you are about identity AND weight"},
    "kcal_per_100g": {"type": "number"},
    "protein_per_100g": {"type": "number"},
    "fat_per_100g": {"type": "number"},
    "carbs_per_100g": {"type": "number"},
    "fiber_per_100g": {"type": "number"},
}

MEAL_ITEMS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "notes"],
    "properties": {
        "items": {
            # no maxItems: OpenAI strict mode rejects it; AiMealResult truncates instead
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_ITEM_PROPERTIES),
                "properties": _ITEM_PROPERTIES,
            },
        },
        "notes": {
            "type": ["string", "null"],
            "description": "Short note for the user (e.g. 'sauce not visible'), or null",
        },
    },
}
SCHEMA_NAME = "meal_items"


class AiItem(BaseModel):
    """One recognised food. Bounds reject nonsense instead of storing it."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name_ru: str = Field(min_length=1, max_length=200)
    name_en: str = Field(min_length=1, max_length=200)
    estimated_grams: float = Field(gt=0, le=3000)
    confidence: float = Field(ge=0, le=1)
    kcal_per_100g: float = Field(ge=0, le=950)
    protein_per_100g: float = Field(ge=0, le=100)
    fat_per_100g: float = Field(ge=0, le=100)
    carbs_per_100g: float = Field(ge=0, le=100)
    fiber_per_100g: float = Field(ge=0, le=100)
    # set when the stated energy disagreed with the macros and was recomputed
    kcal_adjusted: bool = False

    @model_validator(mode="after")
    def _macros_fit_in_100g(self) -> AiItem:
        if self.protein_per_100g + self.fat_per_100g + self.carbs_per_100g > 105:
            raise ValueError("protein + fat + carbs exceed 100 g per 100 g")
        # Energy must agree with the macros (Atwater). A model that says
        # "chicken breast, 600 kcal / 100 g, 31 g protein" gets its energy
        # recomputed instead of being trusted.
        atwater = 4 * self.protein_per_100g + 9 * self.fat_per_100g + 4 * self.carbs_per_100g
        if atwater > 0 and abs(self.kcal_per_100g - atwater) > max(40.0, 0.35 * max(self.kcal_per_100g, atwater)):
            self.kcal_per_100g = round(atwater, 1)
            self.kcal_adjusted = True
        return self


class AiMealResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[AiItem] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("items")
    @classmethod
    def _at_most_max_items(cls, value: list[AiItem]) -> list[AiItem]:
        return value[:MAX_ITEMS]

    @field_validator("notes")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return (value.strip()[:500] or None) if value else None
