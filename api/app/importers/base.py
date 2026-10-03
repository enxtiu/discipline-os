"""Food importers: one normalisation pipeline, many sources.

Every source yields :class:`RawFood` rows; :func:`run_import` upserts them into
the global library keyed by ``(source, source_id)`` so re-running an import is
idempotent and never duplicates a product. Search columns are maintained by
database triggers — importers only write plain columns.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Food, FoodSource

log = get_logger(__name__)

MAX_NAME = 255


@dataclass(slots=True)
class RawFood:
    source: FoodSource
    source_id: str
    name_ru: str | None = None
    name_en: str | None = None
    aliases: list[str] = field(default_factory=list)
    brand: str | None = None
    barcode: str | None = None
    category: str | None = None
    kcal_per_100g: float = 0
    protein_per_100g: float = 0
    fat_per_100g: float = 0
    carbs_per_100g: float = 0
    fiber_per_100g: float = 0
    default_serving_g: float | None = None
    default_serving_label: str | None = None
    is_liquid: bool = False

    def valid(self) -> bool:
        return bool(self.source_id) and bool(self.name_ru or self.name_en) and self.kcal_per_100g >= 0


def _clean(value: str | None, limit: int = MAX_NAME) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.replace("\x00", " ").split())
    return cleaned[:limit] or None


def _dec(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    if not parsed.is_finite() or parsed < 0:
        return Decimal("0")
    return parsed.quantize(Decimal("0.01"))


def normalise(row: RawFood) -> RawFood:
    """Trim names, drop empty aliases, clamp negative macros."""
    row.name_ru = _clean(row.name_ru)
    row.name_en = _clean(row.name_en)
    row.brand = _clean(row.brand, 160)
    row.barcode = _clean(row.barcode, 32)
    row.category = _clean(row.category, 120)
    row.default_serving_label = _clean(row.default_serving_label, 64)
    row.aliases = [a for a in (_clean(x, 120) for x in row.aliases) if a][:12]
    row.kcal_per_100g = float(_dec(row.kcal_per_100g))
    row.protein_per_100g = float(_dec(row.protein_per_100g))
    row.fat_per_100g = float(_dec(row.fat_per_100g))
    row.carbs_per_100g = float(_dec(row.carbs_per_100g))
    row.fiber_per_100g = float(_dec(row.fiber_per_100g))
    return row


@dataclass(slots=True)
class ImportStats:
    source: str
    seen: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int | str]:
        return {
            "source": self.source,
            "seen": self.seen,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }


def _changed(existing: Food, row: RawFood) -> bool:
    return (
        existing.name_ru != row.name_ru
        or existing.name_en != row.name_en
        or existing.brand != row.brand
        or existing.barcode != row.barcode
        or existing.category != row.category
        or existing.kcal_per_100g != _dec(row.kcal_per_100g)
        or existing.protein_per_100g != _dec(row.protein_per_100g)
        or existing.fat_per_100g != _dec(row.fat_per_100g)
        or existing.carbs_per_100g != _dec(row.carbs_per_100g)
        or existing.fiber_per_100g != _dec(row.fiber_per_100g)
        or sorted(existing.aliases) != sorted(row.aliases)
    )


async def run_import(
    session: AsyncSession,
    rows: Iterable[RawFood],
    *,
    source: FoodSource,
    limit: int | None = None,
    commit_every: int = 500,
) -> ImportStats:
    """Upsert global food rows. Idempotent by ``(source, source_id)``."""
    stats = ImportStats(source=source.value)
    existing = {
        sid: row
        for row in (await session.execute(select(Food).where(Food.source == source, Food.is_global.is_(True))))
        .scalars()
        .all()
        if (sid := row.source_id)
    }

    pending = 0
    for raw in rows:
        if limit is not None and stats.seen >= limit:
            break
        stats.seen += 1
        row = normalise(raw)
        if not row.valid():
            stats.skipped += 1
            continue

        current = existing.get(row.source_id)
        if current is None:
            session.add(
                Food(
                    user_id=None,
                    is_global=True,
                    source=source,
                    source_id=row.source_id,
                    name_ru=row.name_ru,
                    name_en=row.name_en,
                    aliases=row.aliases,
                    brand=row.brand,
                    barcode=row.barcode,
                    category=row.category,
                    kcal_per_100g=_dec(row.kcal_per_100g),
                    protein_per_100g=_dec(row.protein_per_100g),
                    fat_per_100g=_dec(row.fat_per_100g),
                    carbs_per_100g=_dec(row.carbs_per_100g),
                    fiber_per_100g=_dec(row.fiber_per_100g),
                    default_serving_g=_dec(row.default_serving_g) if row.default_serving_g else None,
                    default_serving_label=row.default_serving_label,
                    is_liquid=row.is_liquid,
                    verified=source is FoodSource.SEED,
                )
            )
            stats.created += 1
        elif _changed(current, row):
            current.name_ru = row.name_ru or current.name_ru
            current.name_en = row.name_en or current.name_en
            current.aliases = row.aliases or current.aliases
            current.brand = row.brand or current.brand
            current.barcode = row.barcode or current.barcode
            current.category = row.category or current.category
            current.kcal_per_100g = _dec(row.kcal_per_100g)
            current.protein_per_100g = _dec(row.protein_per_100g)
            current.fat_per_100g = _dec(row.fat_per_100g)
            current.carbs_per_100g = _dec(row.carbs_per_100g)
            current.fiber_per_100g = _dec(row.fiber_per_100g)
            stats.updated += 1
        else:
            stats.skipped += 1
            continue

        pending += 1
        if pending >= commit_every:
            await session.flush()
            pending = 0
    if pending:
        await session.flush()
    log.info(
        "food_import_finished",
        source=source.value,
        seen=stats.seen,
        created=stats.created,
        updated=stats.updated,
        skipped=stats.skipped,
    )
    return stats
