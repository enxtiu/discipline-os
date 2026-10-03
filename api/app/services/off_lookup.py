"""Barcode resolution: local library first, then one live Open Food Facts call.

Flow for ``GET /v1/foods/barcode/{code}``:

1. **local** — the user's own foods and the global library (UPC-A/EAN-13
   variants included). Most scans end here.
2. **miss cache** — a code OFF did not know (or only knew half-empty) is not
   asked again for ``OFF_MISS_TTL_HOURS``. In-process, which is enough for one
   API container; a restart only costs one extra request per code.
3. **live** — ``GET {OFF_BASE_URL}/api/v2/product/{code}.json`` with a
   descriptive User-Agent (OFF's API rule) and a short timeout. A product that
   passes :func:`app.importers.off.product_to_raw` is **cached** as a global
   ``source=off`` row (unverified), so the next scan of that code is local.

Network trouble never raises to the client: the outcome is ``unavailable`` and
nothing is cached, so the scan can simply be retried later.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import RateLimited
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.importers.base import _dec, normalise
from app.importers.off import barcode_variants, normalise_barcode, product_to_raw
from app.models import Food, FoodSource
from app.services import food_search

log = get_logger(__name__)

Outcome = Literal["local", "off", "miss", "unavailable", "invalid"]

OFF_FIELDS = ",".join(
    (
        "code",
        "lang",
        "product_name",
        "product_name_ru",
        "product_name_en",
        "generic_name_ru",
        "generic_name_en",
        "brands",
        "categories_tags",
        "nutriments",
        "serving_quantity",
        "nutrition_data_per",
    )
)


@dataclass(slots=True)
class BarcodeResult:
    barcode: str
    outcome: Outcome
    food: Food | None = None


class _MissCache:
    """code → expiry (monotonic seconds). Bounded; oldest entries go first."""

    def __init__(self, max_entries: int = 5000) -> None:
        self._entries: dict[str, float] = {}
        self._max = max_entries

    def hit(self, code: str) -> bool:
        expires = self._entries.get(code)
        if expires is None:
            return False
        if expires < time.monotonic():
            self._entries.pop(code, None)
            return False
        return True

    def add(self, code: str, ttl_seconds: float) -> None:
        if len(self._entries) >= self._max:
            for key in list(self._entries)[: self._max // 10]:
                self._entries.pop(key, None)
        self._entries[code] = time.monotonic() + ttl_seconds

    def clear(self) -> None:
        self._entries.clear()


miss_cache = _MissCache()

# Tests swap this for an ``httpx.MockTransport``; production uses the default.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport
    miss_cache.clear()


async def _fetch_product(code: str) -> tuple[dict | None, bool]:
    """Returns ``(product_or_None, reachable)``."""
    settings = get_settings()
    url = f"{settings.off_base_url.rstrip('/')}/api/v2/product/{code}.json"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            transport=_transport,
            timeout=settings.off_timeout_seconds,
            headers={"User-Agent": settings.off_user_agent, "Accept": "application/json"},
        ) as client:
            response = await client.get(url, params={"fields": OFF_FIELDS})
    except httpx.HTTPError as error:
        log.warning("off_lookup_unreachable", barcode=code, error=type(error).__name__)
        return None, False
    took_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code == 404:
        log.info("off_lookup_done", barcode=code, status=404, took_ms=took_ms)
        return None, True
    if response.status_code >= 400:
        log.warning("off_lookup_http_error", barcode=code, status=response.status_code, took_ms=took_ms)
        return None, False
    try:
        payload = response.json()
    except ValueError:
        log.warning("off_lookup_bad_json", barcode=code, took_ms=took_ms)
        return None, False
    found = payload.get("status") == 1 and isinstance(payload.get("product"), dict)
    log.info("off_lookup_done", barcode=code, status=response.status_code, found=found, took_ms=took_ms)
    if not found:
        return None, True
    product = dict(payload["product"])
    product.setdefault("code", code)
    return product, True


async def _cache_product(db: AsyncSession, product: dict) -> Food | None:
    raw = product_to_raw(product)
    if raw is None:
        return None
    row = normalise(raw)
    existing = await db.scalar(
        select(Food).where(Food.source == FoodSource.OFF, Food.source_id == row.source_id, Food.is_global.is_(True))
    )
    if existing is not None:
        if existing.is_archived:
            return None
        return existing
    food = Food(
        user_id=None,
        is_global=True,
        source=FoodSource.OFF,
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
        is_liquid=row.is_liquid,
        verified=False,
        source_payload={"cached_from": "off_live"},
    )
    try:
        async with db.begin_nested():  # a concurrent scan may have cached it first
            db.add(food)
            await db.flush()
    except IntegrityError:
        return await db.scalar(
            select(Food).where(Food.source == FoodSource.OFF, Food.source_id == row.source_id, Food.is_global.is_(True))
        )
    log.info("off_product_cached", barcode=row.barcode, food_id=str(food.id))
    return food


async def resolve_barcode(db: AsyncSession, *, user_id: uuid.UUID, code: str, allow_live: bool = True) -> BarcodeResult:
    cleaned = normalise_barcode(code)
    if cleaned is None:
        return BarcodeResult(barcode=code.strip(), outcome="invalid")

    for variant in barcode_variants(cleaned):
        food = await food_search.barcode_lookup(db, user_id, variant)
        if food is not None:
            return BarcodeResult(barcode=cleaned, outcome="local", food=food)

    settings = get_settings()
    if not (settings.off_live_lookup and allow_live) or miss_cache.hit(cleaned):
        return BarcodeResult(barcode=cleaned, outcome="miss")

    # only live calls count against the per-user budget — local scans are free
    allowed, retry_after = await limiter.hit(f"off_lookup:{user_id}", settings.off_lookups_per_minute)
    if not allowed:
        raise RateLimited(retry_after, "Too many barcode lookups, please wait a moment")

    # Never hold a transaction (and a pooled connection) open across a network
    # call of up to OFF_TIMEOUT_SECONDS: end the read transaction first. Pending
    # writes of this request (e.g. the device's last-seen touch) are committed.
    await db.commit()
    product, reachable = await _fetch_product(cleaned)
    if not reachable:
        return BarcodeResult(barcode=cleaned, outcome="unavailable")
    food = await _cache_product(db, product) if product else None
    if food is None:
        miss_cache.add(cleaned, settings.off_miss_ttl_hours * 3600)
        return BarcodeResult(barcode=cleaned, outcome="miss")
    return BarcodeResult(barcode=cleaned, outcome="off", food=food)
