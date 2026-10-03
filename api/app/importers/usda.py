"""USDA-style CSV importer (FoodData Central / SR Legacy export slice).

Header (order-free): ``id,name,kcal,protein,fat,carbs,fiber[,category]``
with macros per 100 g. English-only names are fine — the search covers both
languages independently.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from app.importers.base import RawFood
from app.models import FoodSource


def _float(row: dict, *keys: str) -> float:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def iterate_usda(path: str | Path) -> Iterator[RawFood]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = (row.get("id") or row.get("fdc_id") or "").strip()
            name = (row.get("name") or row.get("description") or "").strip()
            if not source_id or not name:
                continue
            yield RawFood(
                source=FoodSource.USDA,
                source_id=source_id,
                name_en=name,
                category=(row.get("category") or "").strip() or None,
                kcal_per_100g=_float(row, "kcal", "energy_kcal"),
                protein_per_100g=_float(row, "protein", "protein_g"),
                fat_per_100g=_float(row, "fat", "fat_g"),
                carbs_per_100g=_float(row, "carbs", "carbohydrate_g"),
                fiber_per_100g=_float(row, "fiber", "fiber_g"),
            )
