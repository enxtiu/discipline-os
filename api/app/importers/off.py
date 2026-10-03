"""Open Food Facts importer (JSONL, e.g. a dump slice or an export).

Expected per-line keys (all optional, parsed defensively):
``code``, ``product_name_ru``, ``product_name_en`` (fallback ``product_name``),
``brands``, ``categories_tags``, ``nutriments`` with ``*_100g`` entries.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from app.importers.base import RawFood
from app.models import FoodSource

_KCAL = ("energy-kcal_100g", "energy-kcal_value", "energy_100g_kcal")


def _nutri(nutriments: dict, keys: tuple[str, ...], per_kj: float | None = None) -> float:
    for key in keys:
        value = nutriments.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    if per_kj is not None:  # some exports carry kJ only
        kj = nutriments.get("energy_100g")
        if kj not in (None, ""):
            try:
                return round(float(kj) * per_kj, 2)
            except (TypeError, ValueError):
                pass
    return 0.0


def iterate_off(path: str | Path) -> Iterator[RawFood]:
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                product = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = str(product.get("code") or "").strip()
            name_ru = product.get("product_name_ru") or product.get("generic_name_ru")
            name_en = product.get("product_name_en") or product.get("product_name") or product.get("generic_name")
            if not code or not (name_ru or name_en):
                continue
            nutriments = product.get("nutriments") or {}
            categories = product.get("categories_tags") or []
            brands = str(product.get("brands") or "").split(",")
            yield RawFood(
                source=FoodSource.OFF,
                source_id=code,
                name_ru=name_ru,
                name_en=name_en,
                brand=brands[0].strip() or None,
                barcode=code,
                category=str(categories[0]).replace("en:", "") if categories else None,
                kcal_per_100g=_nutri(nutriments, _KCAL, per_kj=0.239),
                protein_per_100g=_nutri(nutriments, ("proteins_100g",)),
                fat_per_100g=_nutri(nutriments, ("fat_100g",)),
                carbs_per_100g=_nutri(nutriments, ("carbohydrates_100g",)),
                fiber_per_100g=_nutri(nutriments, ("fiber_100g",)),
            )
