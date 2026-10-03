"""Curated RU seed library bundled with the repo.

``make seed-foods`` (or ``python -m app.cli import-foods --source seed``) fills
an empty database with ~85 everyday foods in both languages so search, logging
and the dashboards are useful before any external dump is imported. Rows are
marked ``verified`` — they come from published composition tables.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from app.importers.base import RawFood
from app.models import FoodSource

DATA = Path(__file__).parent / "data" / "foods_seed_ru.json"


def iterate_seed(path: str | Path | None = None) -> Iterator[RawFood]:
    source_path = Path(path) if path else DATA
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    for entry in payload:
        yield RawFood(
            source=FoodSource.SEED,
            source_id=str(entry["id"]),
            name_ru=entry.get("name_ru"),
            name_en=entry.get("name_en"),
            aliases=list(entry.get("aliases") or []),
            category=entry.get("category"),
            kcal_per_100g=entry.get("kcal", 0),
            protein_per_100g=entry.get("protein", 0),
            fat_per_100g=entry.get("fat", 0),
            carbs_per_100g=entry.get("carbs", 0),
            fiber_per_100g=entry.get("fiber", 0),
            default_serving_g=entry.get("serving_g"),
            default_serving_label=entry.get("serving_label"),
            is_liquid=bool(entry.get("liquid", False)),
        )
