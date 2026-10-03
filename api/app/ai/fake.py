"""Deterministic offline provider (``AI_PROVIDER=fake``).

* Text: a small rule-based parser for Russian and English ("200 г гречки,
  2 яйца и куриная грудка 150 g") with a reference table of common foods.
* Photo: a fixed, plausible plate (buckwheat, chicken breast, cucumber).

It exists so the whole flow — review screen included — runs in tests, in CI
and on a laptop without an API key. ``SCRIPT`` lets a test queue raw replies
(e.g. broken JSON) to exercise validation and the retry.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.ai.providers import AiProviderError, AiRequest, AiResponse

# Raw replies queued by tests; consumed before the rule-based answer.
SCRIPT: list[str | AiProviderError] = []
CALLS: list[AiRequest] = []


@dataclass(frozen=True, slots=True)
class _Ref:
    keys: tuple[str, ...]
    name_ru: str
    name_en: str
    per100: tuple[float, float, float, float, float]  # kcal, protein, fat, carbs, fiber
    piece_g: float = 100


_TABLE = (
    _Ref(("гречк", "buckwheat"), "Гречка отварная", "Buckwheat, cooked", (110, 4.2, 1.1, 21.3, 2.7), 150),
    _Ref(("рис", "rice"), "Рис отварной", "Rice, cooked", (130, 2.7, 0.3, 28.2, 0.4), 150),
    _Ref(("овсян", "oat"), "Каша овсяная на воде", "Oatmeal with water", (88, 3.0, 1.7, 15.0, 1.9), 250),
    _Ref(("грудк", "breast"), "Куриная грудка запечённая", "Chicken breast, roasted", (165, 31.0, 3.6, 0, 0), 150),
    _Ref(("куриц", "chicken"), "Курица запечённая", "Roast chicken", (239, 27.3, 13.6, 0, 0), 150),
    _Ref(("яйц", "яйко", "egg"), "Яйцо куриное", "Egg", (143, 12.6, 9.5, 0.7, 0), 55),
    _Ref(("творог", "cottage"), "Творог 5%", "Cottage cheese 5%", (121, 17.2, 5.0, 1.8, 0), 150),
    _Ref(("банан", "banana"), "Банан", "Banana", (89, 1.1, 0.3, 22.8, 2.6), 120),
    _Ref(("яблок", "apple"), "Яблоко", "Apple", (52, 0.3, 0.2, 13.8, 2.4), 180),
    _Ref(("огур", "cucumber"), "Огурец", "Cucumber", (15, 0.7, 0.1, 3.6, 0.5), 100),
    _Ref(("хлеб", "bread"), "Хлеб пшеничный белый", "White bread", (265, 9.0, 3.2, 49.0, 2.7), 30),
    _Ref(("кефир", "kefir"), "Кефир 1%", "Kefir 1%", (40, 3.0, 1.0, 4.0, 0), 250),
    _Ref(("борщ", "borscht"), "Борщ с мясом", "Borscht with meat", (49, 2.4, 2.2, 5.0, 1.0), 300),
)
_GENERIC = (150.0, 6.0, 5.0, 20.0, 2.0)

_UNIT_GRAMS = {
    "г": 1, "гр": 1, "грамм": 1, "граммов": 1, "грамма": 1, "g": 1, "gr": 1, "gram": 1, "grams": 1,
    "мл": 1, "ml": 1, "кг": 1000, "kg": 1000, "л": 1000, "l": 1000,
}  # fmt: skip
_WORD_NUMBERS = {
    "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}  # fmt: skip
_SPLIT = re.compile(r"[,;\n+]| и | and ", re.IGNORECASE)
_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([a-zа-яё]+)?", re.IGNORECASE)


def _lookup(name: str) -> _Ref | None:
    lowered = name.lower()
    return next((ref for ref in _TABLE if any(key in lowered for key in ref.keys)), None)


def _item(name_ru: str, name_en: str, grams: float, confidence: float, per100: tuple) -> dict:
    kcal, protein, fat, carbs, fiber = per100
    return {
        "name_ru": name_ru,
        "name_en": name_en,
        "estimated_grams": round(grams, 1),
        "confidence": confidence,
        "kcal_per_100g": kcal,
        "protein_per_100g": protein,
        "fat_per_100g": fat,
        "carbs_per_100g": carbs,
        "fiber_per_100g": fiber,
    }


def parse_text(text: str) -> dict:
    items = []
    for chunk in _SPLIT.split(text):
        chunk = chunk.strip(" .!")
        if not chunk:
            continue
        grams: float | None = None
        count: float | None = None
        name = chunk
        for match in _AMOUNT.finditer(chunk):
            value = float(match.group(1).replace(",", "."))
            unit = (match.group(2) or "").lower()
            if unit in _UNIT_GRAMS:
                grams = value * _UNIT_GRAMS[unit]
            else:
                count = value
            name = name.replace(match.group(0) if unit in _UNIT_GRAMS else match.group(1), " ")
        for word, number in _WORD_NUMBERS.items():
            if re.search(rf"\b{word}\b", name, re.IGNORECASE):
                count = count or number
                name = re.sub(rf"\b{word}\b", " ", name, flags=re.IGNORECASE)
        name = " ".join(name.replace("шт", " ").replace("pcs", " ").split())
        if not name:
            continue
        ref = _lookup(name)
        if grams is None:
            grams = (count or 1) * (ref.piece_g if ref else 100)
        confidence = 0.9 if ref and (grams is not None and count is None) else 0.6 if ref else 0.4
        if ref:
            items.append(_item(ref.name_ru, ref.name_en, grams, confidence, ref.per100))
        else:
            items.append(_item(name[:1].upper() + name[1:], name, grams, confidence, _GENERIC))
    return {"items": items, "notes": None if items else "No food found in the text."}


PHOTO_PLATE = {
    "items": [
        _item("Гречка отварная", "Buckwheat, cooked", 180, 0.82, (110, 4.2, 1.1, 21.3, 2.7)),
        _item("Куриная грудка запечённая", "Chicken breast, roasted", 140, 0.74, (165, 31.0, 3.6, 0, 0)),
        _item("Огурец", "Cucumber", 80, 0.55, (15, 0.7, 0.1, 3.6, 0.5)),
    ],
    "notes": "Oil or sauce are not visible and are not included.",
}


class FakeProvider:
    name = "fake"

    async def complete(self, request: AiRequest) -> AiResponse:
        CALLS.append(request)
        if SCRIPT:
            queued = SCRIPT.pop(0)
            if isinstance(queued, AiProviderError):
                raise queued
            return AiResponse(text=queued, input_tokens=100, output_tokens=50, raw={"fake": True, "scripted": True})
        if request.image_jpeg is not None:
            result = PHOTO_PLATE
        else:
            # the text sits after the prompt's "What I ate:" line
            marker = "What I ate:"
            text = request.user_text.split(marker, 1)[-1] if marker in request.user_text else request.user_text
            result = parse_text(text)
        body = json.dumps(result, ensure_ascii=False)
        return AiResponse(text=body, input_tokens=len(request.user_text) // 4 + 50, output_tokens=len(body) // 4)
