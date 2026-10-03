"""USDA FoodData Central importer: Foundation Foods and SR Legacy.

Three input shapes are accepted, auto-detected from the path:

* **FDC JSON download** (``FoodData_Central_foundation_food_json_*.json`` /
  ``FoodData_Central_sr_legacy_food_json_*.json``): a top-level
  ``FoundationFoods`` or ``SRLegacyFoods`` array whose items carry
  ``fdcId``, ``description``, ``foodCategory`` and ``foodNutrients``.
* **FDC CSV download** (a directory, e.g. ``FoodData_Central_csv_2024-10-31/``):
  ``food.csv`` + ``food_nutrient.csv`` (+ optional ``food_category.csv``).
  Only ``foundation_food`` and ``sr_legacy_food`` rows are read.
* **Flat CSV** (the stage-2 format, kept for hand-made slices):
  ``id,name,kcal,protein,fat,carbs,fiber[,category]`` per 100 g.

FDC descriptions are English. ``data/usda_ru.json`` maps them to Russian names
and aliases so the rows are searchable in Russian (see :class:`RuNameMap`);
unmapped rows keep their English name and stay searchable in English.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.importers.base import RawFood
from app.models import FoodSource

RU_MAP_PATH = Path(__file__).parent / "data" / "usda_ru.json"

# FDC nutrient ids (stable across releases; ``number`` is the legacy SR code)
N_ENERGY_KCAL = 1008  # "208"
N_ENERGY_ATWATER_SPECIFIC = 2048  # Foundation foods often only carry these two
N_ENERGY_ATWATER_GENERAL = 2047
N_ENERGY_KJ = 1062  # "268"
N_PROTEIN = 1003  # "203"
N_FAT = 1004  # "204"
N_FAT_NLEA = 1085
N_CARBS = 1005  # "205" carbohydrate, by difference
N_CARBS_SUM = 1050  # carbohydrate, by summation
N_FIBER = 1079  # "291"

WANTED = {
    N_ENERGY_KCAL,
    N_ENERGY_ATWATER_SPECIFIC,
    N_ENERGY_ATWATER_GENERAL,
    N_ENERGY_KJ,
    N_PROTEIN,
    N_FAT,
    N_FAT_NLEA,
    N_CARBS,
    N_CARBS_SUM,
    N_FIBER,
}
_NUMBER_TO_ID = {
    "208": N_ENERGY_KCAL,
    "268": N_ENERGY_KJ,
    "203": N_PROTEIN,
    "204": N_FAT,
    "205": N_CARBS,
    "291": N_FIBER,
}

DATASETS = {"foundation", "sr_legacy"}
_JSON_KEYS = {"FoundationFoods": "foundation", "SRLegacyFoods": "sr_legacy"}
_CSV_TYPES = {"foundation_food": "foundation", "sr_legacy_food": "sr_legacy"}

KJ_TO_KCAL = 0.239006


# --------------------------------------------------------------------------- #
# Russian names
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[a-z]+")
_GENDERS = ("m", "f", "n", "pl")


@dataclass(slots=True)
class _Rule:
    prefix: str
    contains: tuple[str, ...]
    name_ru: str
    aliases: list[str]
    gender: str
    fixed: bool

    @property
    def specificity(self) -> tuple[int, int]:
        return (len(self.contains), len(self.prefix))


@dataclass(slots=True)
class RuNameMap:
    """English FDC description → Russian name + aliases.

    Resolution order: an exact ``by_fdc_id`` override, then the most specific
    rule whose ``prefix`` matches the start of the description up to a facet
    boundary and whose ``contains`` words all occur in it. Unless the rule is
    ``fixed``, the preparation state found in the description (raw, boiled,
    roasted, …) is appended in the grammatical gender of the rule
    ("Куриная грудка, запечённая"), so variants of one product stay
    distinguishable in Russian too.
    """

    rules: list[_Rule] = field(default_factory=list)
    by_fdc_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    states: list[tuple[tuple[str, ...], dict[str, str]]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None = RU_MAP_PATH) -> RuNameMap:
        if path is None:
            return cls()
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = []
        for raw in payload.get("rules", []):
            gender = raw.get("g", "m")
            if gender not in _GENDERS:
                raise ValueError(f"usda_ru.json: unknown gender {gender!r} in rule {raw.get('prefix')!r}")
            rules.append(
                _Rule(
                    prefix=raw["prefix"].lower(),
                    contains=tuple(word.lower() for word in raw.get("contains", [])),
                    name_ru=raw["name_ru"],
                    aliases=list(raw.get("aliases", [])),
                    gender=gender,
                    fixed=bool(raw.get("fixed", False)),
                )
            )
        rules.sort(key=lambda rule: rule.specificity, reverse=True)
        states = [(tuple(item["words"]), item["forms"]) for item in payload.get("states", [])]
        return cls(rules=rules, by_fdc_id=payload.get("by_fdc_id", {}), states=states)

    def resolve(self, fdc_id: str, description: str) -> tuple[str | None, list[str]]:
        override = self.by_fdc_id.get(str(fdc_id))
        if override:
            return override.get("name_ru"), list(override.get("aliases", []))
        text = description.lower().strip()
        words = set(_WORD.findall(text))
        for rule in self.rules:
            if not _prefix_match(text, rule.prefix):
                continue
            if not all(_contains(text, words, needle) for needle in rule.contains):
                continue
            name = rule.name_ru if rule.fixed else self._with_state(rule, words, text)
            return name, list(rule.aliases)
        return None, []

    def _with_state(self, rule: _Rule, words: set[str], text: str) -> str:
        for triggers, forms in self.states:  # ordered by priority in the file
            if any(_contains(text, words, trigger) for trigger in triggers):
                return f"{rule.name_ru}, {forms[rule.gender]}"
        return rule.name_ru


def _prefix_match(text: str, prefix: str) -> bool:
    if not text.startswith(prefix):
        return False
    # FDC descriptions are comma-separated facets ("Fish, salmon, Atlantic, raw");
    # a parenthesis may follow the first one ("Chickpeas (garbanzo beans, …)").
    rest = text[len(prefix) :]
    return not rest or rest.startswith(",") or rest.startswith(" (")


def _contains(text: str, words: set[str], needle: str) -> bool:
    return needle in words if needle.isalpha() else needle in text


# --------------------------------------------------------------------------- #
# Nutrients
# --------------------------------------------------------------------------- #
def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _macros(amounts: dict[int, float]) -> dict[str, float] | None:
    """Per-100 g macros from FDC nutrient amounts; ``None`` when energy is unknown."""
    kcal = amounts.get(N_ENERGY_KCAL)
    if kcal is None:
        kcal = amounts.get(N_ENERGY_ATWATER_SPECIFIC, amounts.get(N_ENERGY_ATWATER_GENERAL))
    if kcal is None and amounts.get(N_ENERGY_KJ) is not None:
        kcal = round(amounts[N_ENERGY_KJ] * KJ_TO_KCAL, 2)
    if kcal is None:
        return None
    macros = {
        "kcal": kcal,
        "protein": amounts.get(N_PROTEIN, 0.0),
        "fat": amounts.get(N_FAT, amounts.get(N_FAT_NLEA, 0.0)),
        "carbs": amounts.get(N_CARBS, amounts.get(N_CARBS_SUM, 0.0)),
        "fiber": amounts.get(N_FIBER, 0.0),
    }
    if macros["protein"] + macros["fat"] + macros["carbs"] > 105 or kcal > 950:
        return None  # broken row; better absent than wrong
    return macros


def _raw_food(
    fdc_id: str, description: str, category: str | None, amounts: dict[int, float], names: RuNameMap
) -> RawFood | None:
    macros = _macros(amounts)
    if macros is None or not description:
        return None
    name_ru, aliases = names.resolve(fdc_id, description)
    return RawFood(
        source=FoodSource.USDA,
        source_id=str(fdc_id),
        name_ru=name_ru,
        name_en=description,
        aliases=aliases,
        category=category,
        kcal_per_100g=macros["kcal"],
        protein_per_100g=macros["protein"],
        fat_per_100g=macros["fat"],
        carbs_per_100g=macros["carbs"],
        fiber_per_100g=macros["fiber"],
    )


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #
def _iterate_fdc_json(path: Path, datasets: set[str], names: RuNameMap) -> Iterator[RawFood]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key, dataset in _JSON_KEYS.items():
        if dataset not in datasets:
            continue
        for food in payload.get(key, []) or []:
            amounts: dict[int, float] = {}
            for entry in food.get("foodNutrients", []) or []:
                nutrient = entry.get("nutrient") or {}
                nid = nutrient.get("id")
                if nid not in WANTED:
                    nid = _NUMBER_TO_ID.get(str(nutrient.get("number", "")))
                value = _to_float(entry.get("amount"))
                if nid in WANTED and value is not None:
                    amounts.setdefault(nid, value)
            category = (food.get("foodCategory") or {}).get("description")
            row = _raw_food(
                str(food.get("fdcId", "")), (food.get("description") or "").strip(), category, amounts, names
            )
            if row is not None:
                yield row


def _iterate_fdc_csv_dir(directory: Path, datasets: set[str], names: RuNameMap) -> Iterator[RawFood]:
    foods: dict[str, tuple[str, str | None]] = {}
    categories: dict[str, str] = {}
    category_file = directory / "food_category.csv"
    if category_file.exists():
        with category_file.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                categories[(row.get("id") or "").strip()] = (row.get("description") or "").strip()
    with (directory / "food.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            dataset = _CSV_TYPES.get((row.get("data_type") or "").strip())
            if dataset not in datasets:
                continue
            fdc_id = (row.get("fdc_id") or "").strip()
            category = categories.get((row.get("food_category_id") or "").strip())
            foods[fdc_id] = ((row.get("description") or "").strip(), category or None)

    amounts: dict[str, dict[int, float]] = {}
    with (directory / "food_nutrient.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fdc_id = (row.get("fdc_id") or "").strip()
            if fdc_id not in foods:
                continue
            try:
                nid = int(row.get("nutrient_id") or 0)
            except ValueError:
                continue
            value = _to_float(row.get("amount"))
            if nid in WANTED and value is not None:
                amounts.setdefault(fdc_id, {}).setdefault(nid, value)

    for fdc_id, (description, category) in foods.items():
        food = _raw_food(fdc_id, description, category, amounts.get(fdc_id, {}), names)
        if food is not None:
            yield food


def _float(row: dict, *keys: str) -> float:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def _iterate_flat_csv(path: Path, names: RuNameMap) -> Iterator[RawFood]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = (row.get("id") or row.get("fdc_id") or "").strip()
            name = (row.get("name") or row.get("description") or "").strip()
            if not source_id or not name:
                continue
            name_ru, aliases = names.resolve(source_id, name)
            yield RawFood(
                source=FoodSource.USDA,
                source_id=source_id,
                name_ru=(row.get("name_ru") or "").strip() or name_ru,
                name_en=name,
                aliases=aliases,
                category=(row.get("category") or "").strip() or None,
                kcal_per_100g=_float(row, "kcal", "energy_kcal"),
                protein_per_100g=_float(row, "protein", "protein_g"),
                fat_per_100g=_float(row, "fat", "fat_g"),
                carbs_per_100g=_float(row, "carbs", "carbohydrate_g"),
                fiber_per_100g=_float(row, "fiber", "fiber_g"),
            )


def iterate_usda(
    path: str | Path,
    *,
    datasets: Iterable[str] | None = None,
    ru_map: str | Path | None = RU_MAP_PATH,
) -> Iterator[RawFood]:
    """Yield normalisable rows from any supported USDA input (see module doc)."""
    source = Path(path)
    wanted = set(datasets) if datasets else set(DATASETS)
    unknown = wanted - DATASETS
    if unknown:
        raise ValueError(f"unknown USDA dataset(s): {', '.join(sorted(unknown))}")
    names = RuNameMap.load(ru_map)
    if source.is_dir():
        return _iterate_fdc_csv_dir(source, wanted, names)
    if source.suffix.lower() == ".json":
        return _iterate_fdc_json(source, wanted, names)
    return _iterate_flat_csv(source, names)
