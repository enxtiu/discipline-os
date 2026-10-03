"""Open Food Facts importer (JSONL dump slice / export) and product parser.

:func:`product_to_raw` is shared by the bulk importer and the live barcode
fallback (``app/services/off_lookup.py``), so both apply the same quality bar:

* a **Russian or English name** — ``product_name_ru`` / ``product_name_en``,
  or the generic ``product_name`` only when the product's ``lang`` is ru/en;
* **complete core nutriments per 100 g** — energy (kcal, or kJ converted),
  proteins, fat and carbohydrates must all be present. Fiber is optional
  (OFF omits it for most products) and defaults to 0;
* **plausible numbers** — macros ≤ 100 g, energy ≤ 950 kcal and within a wide
  band of the Atwater estimate, which drops unit mix-ups (kJ typed as kcal).

Everything else is skipped and counted, never imported half-empty.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.importers.base import RawFood
from app.models import FoodSource

_KCAL_KEYS = ("energy-kcal_100g", "energy-kcal_value", "energy_100g_kcal")
_KJ_KEYS = ("energy-kj_100g", "energy_100g")
KJ_TO_KCAL = 0.239006
NAME_LANGS = ("ru", "en")
_BARCODE = re.compile(r"^\d{8,14}$")


def normalise_barcode(code: str | None) -> str | None:
    """Digits only, EAN-8 … GTIN-14; anything else is not a barcode."""
    cleaned = re.sub(r"\D", "", code or "")
    return cleaned if _BARCODE.match(cleaned) else None


def barcode_variants(code: str) -> list[str]:
    """UPC-A (12) and EAN-13 with a leading zero are the same product."""
    variants = [code]
    if len(code) == 12:
        variants.append("0" + code)
    elif len(code) == 13 and code.startswith("0"):
        variants.append(code[1:])
    return variants


def _number(nutriments: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = nutriments.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _names(product: dict[str, Any]) -> tuple[str | None, str | None]:
    name_ru = (product.get("product_name_ru") or product.get("generic_name_ru") or "").strip() or None
    name_en = (product.get("product_name_en") or product.get("generic_name_en") or "").strip() or None
    generic = (product.get("product_name") or "").strip() or None
    lang = (product.get("lang") or product.get("lc") or "").strip().lower()
    if generic and lang in NAME_LANGS:
        if lang == "ru" and not name_ru:
            name_ru = generic
        elif lang == "en" and not name_en:
            name_en = generic
    return name_ru, name_en


def product_to_raw(product: dict[str, Any]) -> RawFood | None:
    """One OFF product → :class:`RawFood`, or ``None`` when it fails the quality bar."""
    code = normalise_barcode(str(product.get("code") or product.get("_id") or ""))
    if code is None:
        return None
    name_ru, name_en = _names(product)
    if not (name_ru or name_en):
        return None

    nutriments = product.get("nutriments") or {}
    kcal = _number(nutriments, _KCAL_KEYS)
    if kcal is None:
        kj = _number(nutriments, _KJ_KEYS)
        kcal = round(kj * KJ_TO_KCAL, 2) if kj is not None else None
    protein = _number(nutriments, ("proteins_100g",))
    fat = _number(nutriments, ("fat_100g",))
    carbs = _number(nutriments, ("carbohydrates_100g",))
    if kcal is None or protein is None or fat is None or carbs is None:
        return None
    fiber = _number(nutriments, ("fiber_100g",)) or 0.0

    if protein + fat + carbs > 100.5 or kcal > 950:
        return None
    atwater = 4 * protein + 9 * fat + 4 * carbs
    if abs(kcal - atwater) > max(40.0, 0.35 * max(kcal, atwater)):
        return None

    categories = product.get("categories_tags") or []
    brands = str(product.get("brands") or "").split(",")
    serving = _number(product, ("serving_quantity",))
    per = str(product.get("nutrition_data_per") or "").lower()
    return RawFood(
        source=FoodSource.OFF,
        source_id=code,
        name_ru=name_ru,
        name_en=name_en,
        brand=brands[0].strip() or None,
        barcode=code,
        category=str(categories[0]).split(":", 1)[-1] if categories else None,
        kcal_per_100g=kcal,
        protein_per_100g=protein,
        fat_per_100g=fat,
        carbs_per_100g=carbs,
        fiber_per_100g=fiber,
        default_serving_g=serving if serving and 0 < serving <= 2000 else None,
        is_liquid=per == "100ml",
    )


def iterate_off(path: str | Path) -> Iterator[RawFood]:
    """Usable products from a JSONL file; rejected lines are simply not yielded.

    Rejections still count as *seen* in the import stats: the generator yields
    an invalid placeholder for them so ``run_import`` reports them as skipped.
    """
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                product = json.loads(line)
            except json.JSONDecodeError:
                yield _rejected()
                continue
            row = product_to_raw(product) if isinstance(product, dict) else None
            yield row if row is not None else _rejected()


def _rejected() -> RawFood:
    # empty source_id → RawFood.valid() is False → counted as skipped
    return RawFood(source=FoodSource.OFF, source_id="")
