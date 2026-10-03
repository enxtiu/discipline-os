"""Ranked food search and the food library (favorites, recents, barcodes).

Ranking contract (stage 2 spec)
-------------------------------
1. Russian and English full-text search (trigger-maintained ``tsvector``),
   plus ``pg_trgm`` similarity for typos and morphological near-misses
   ("гречка" → "Гречневая крупа", "chicken brest" → "Chicken breast").
2. The user's own world wins: custom foods, verified rows, favorites and
   recents are boosted above anonymous import rows.
3. Popular global rows (``use_count``) break the remaining ties.
4. p95 must stay comfortably below 150 ms: one query, GIN indexes only.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, case, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.logging import get_logger
from app.core.tz import utcnow
from app.models import Food, FoodFavorite, FoodRecent, Recipe

log = get_logger(__name__)

RU_CONFIG = "russian"
EN_CONFIG = "english"
TRGM_THRESHOLD = 0.45  # names
TRGM_ALIAS_THRESHOLD = 0.30  # word-similarity against the alias blob
PREFIX_FALLBACK_CHARS = 3  # short queries use ILIKE prefix matching instead

MIN_SIMILARITY = TRGM_THRESHOLD


@dataclass(slots=True)
class FoodHit:
    food: Food
    score: float
    match_method: str


def _visible_foods(user_id: uuid.UUID) -> Select:
    return select(Food).where(
        Food.is_archived.is_(False),
        or_(Food.is_global.is_(True), Food.user_id == user_id),
    )


async def get_food(db: AsyncSession, user_id: uuid.UUID, food_id: uuid.UUID) -> Food | None:
    return await db.scalar(_visible_foods(user_id).where(Food.id == food_id))


async def barcode_lookup(db: AsyncSession, user_id: uuid.UUID, code: str) -> Food | None:
    cleaned = code.strip()
    if not cleaned:
        return None
    return await db.scalar(_visible_foods(user_id).where(Food.barcode == cleaned))


async def search_foods(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = 24,
) -> tuple[list[FoodHit], int, bool]:
    """Ranked search. Returns (hits, took_ms, has_more)."""
    started = time.perf_counter()
    q = query.strip()

    if not q:
        hits = await _browse(db, user_id=user_id, limit=limit)
        return hits, _elapsed(started), False

    nq = q.lower()
    q_ru = func.plainto_tsquery(cast(RU_CONFIG, REGCONFIG), q)
    q_en = func.plainto_tsquery(cast(EN_CONFIG, REGCONFIG), q)

    fts_rank = func.greatest(
        func.coalesce(func.ts_rank(Food.search_vector_ru, q_ru), 0.0),
        func.coalesce(func.ts_rank(Food.search_vector_en, q_en), 0.0),
    )
    sim = func.greatest(
        func.similarity(func.lower(Food.name_ru), nq),
        func.similarity(func.lower(Food.name_en), nq),
        func.word_similarity(nq, func.lower(Food.aliases_text)),
    )
    exact = or_(func.lower(Food.name_ru) == nq, func.lower(Food.name_en) == nq)
    escaped = nq.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"{escaped}%" if len(nq) <= PREFIX_FALLBACK_CHARS else f"%{escaped}%"
    prefix = or_(
        func.lower(Food.name_ru).like(like),
        func.lower(Food.name_en).like(like),
        func.lower(Food.aliases_text).like(f"%{escaped}%"),
    )

    fav_sub = select(FoodFavorite.food_id).where(FoodFavorite.user_id == user_id).scalar_subquery()
    is_fav = Food.id.in_(fav_sub)
    own = Food.user_id.is_not(None)

    def _bit(expr) -> object:
        # Postgres cannot cast boolean → float directly
        return case((expr, 1.0), else_=0.0)

    # Relevance first, then the personal world: the user's own foods, favorites
    # and verified rows outrank anonymous library hits (stage-2 spec).
    score = (
        _bit(exact) * 1.0
        + fts_rank * 1.2
        + func.least(sim, 1.0) * 0.9
        + _bit(own) * 2.0
        + _bit(is_fav) * 0.5
        + _bit(Food.verified) * 0.15
        + func.least(Food.use_count, 200) / 200.0 * 0.25
    )

    stmt = (
        _visible_foods(user_id)
        .where(
            or_(
                exact,
                fts_rank > 0.001,
                sim >= MIN_SIMILARITY,
                prefix,
            )
        )
        .add_columns(score, sim, fts_rank)
        .order_by(score.desc(), Food.use_count.desc())
        .limit(limit + 1)
    )
    rows = (await db.execute(stmt)).all()

    hits: list[FoodHit] = []
    for food, row_score, row_sim, row_fts in rows:
        if row_fts > 0.001:
            method = "fts"
        elif row_sim >= MIN_SIMILARITY:
            method = "trgm"
        else:
            method = "exact"
        hits.append(FoodHit(food=food, score=float(row_score), match_method=method))
    return hits[:limit], _elapsed(started), len(hits) > limit


def _elapsed(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


async def _browse(db: AsyncSession, *, user_id: uuid.UUID, limit: int) -> list[FoodHit]:
    """Empty query: favorites, then recents, then popular verified globals."""
    hits: list[FoodHit] = []
    seen: set[uuid.UUID] = set()

    fav_rows = (
        await db.execute(
            select(Food, FoodFavorite.default_grams)
            .join(FoodFavorite, FoodFavorite.food_id == Food.id)
            .where(FoodFavorite.user_id == user_id, Food.is_archived.is_(False))
            .order_by(FoodFavorite.sort_order, FoodFavorite.created_at)
            .limit(limit)
        )
    ).all()
    for food, _grams in fav_rows:
        hits.append(FoodHit(food=food, score=3.0, match_method="none"))
        seen.add(food.id)

    if len(hits) < limit:
        rec_rows = (
            await db.execute(
                select(Food)
                .join(FoodRecent, FoodRecent.food_id == Food.id)
                .where(FoodRecent.user_id == user_id, Food.is_archived.is_(False))
                .order_by(FoodRecent.last_used_at.desc())
                .limit(limit)
            )
        ).all()
        for (food,) in rec_rows:
            if food.id in seen:
                continue
            hits.append(FoodHit(food=food, score=2.0, match_method="none"))
            seen.add(food.id)

    if len(hits) < limit:
        popular = (
            (
                await db.execute(
                    _visible_foods(user_id)
                    .where(Food.verified.is_(True), Food.id.notin_(seen))
                    .order_by(Food.use_count.desc())
                    .limit(limit - len(hits))
                )
            )
            .scalars()
            .all()
        )
        for food in popular:
            hits.append(FoodHit(food=food, score=1.0, match_method="none"))
            seen.add(food.id)
    return hits[:limit]


# --------------------------------------------------------------------------- #
# Favorites & recents
# --------------------------------------------------------------------------- #
async def favorite_ids(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, float | None]:
    rows = (
        await db.execute(
            select(FoodFavorite.food_id, FoodFavorite.default_grams).where(FoodFavorite.user_id == user_id)
        )
    ).all()
    return dict(rows)


async def list_favorites(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[FoodFavorite, Food]]:
    rows = (
        await db.execute(
            select(FoodFavorite, Food)
            .join(Food, Food.id == FoodFavorite.food_id)
            .where(FoodFavorite.user_id == user_id, Food.is_archived.is_(False))
            .order_by(FoodFavorite.sort_order, FoodFavorite.created_at)
        )
    ).all()
    return [(fav, food) for fav, food in rows]


async def add_favorite(
    db: AsyncSession, *, user_id: uuid.UUID, food_id: uuid.UUID, default_grams: float | None
) -> FoodFavorite:
    food = await get_food(db, user_id, food_id)
    if food is None:
        raise NotFound("Food not found", code="food_not_found")
    existing = await db.scalar(
        select(FoodFavorite).where(FoodFavorite.user_id == user_id, FoodFavorite.food_id == food_id)
    )
    if existing is not None:
        if default_grams is not None:
            existing.default_grams = default_grams
        return existing
    row = FoodFavorite(user_id=user_id, food_id=food_id, default_grams=default_grams, sort_order=0)
    db.add(row)
    await db.flush()
    return row


async def remove_favorite(db: AsyncSession, *, user_id: uuid.UUID, food_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(FoodFavorite).where(FoodFavorite.user_id == user_id, FoodFavorite.food_id == food_id)
    )
    return result.rowcount > 0


async def list_recents(db: AsyncSession, *, user_id: uuid.UUID, limit: int = 12) -> list[FoodRecent]:
    return (
        (
            await db.execute(
                select(FoodRecent)
                .where(FoodRecent.user_id == user_id)
                .order_by(FoodRecent.last_used_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def touch_recent(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    food_id: uuid.UUID | None = None,
    recipe_id: uuid.UUID | None = None,
    grams: float | None = None,
) -> None:
    """Upsert the one-tap "recents" row (exactly one of food/recipe)."""
    if (food_id is None) == (recipe_id is None):
        return
    stmt = select(FoodRecent).where(FoodRecent.user_id == user_id)
    stmt = stmt.where(FoodRecent.food_id == food_id) if food_id else stmt.where(FoodRecent.recipe_id == recipe_id)
    row = await db.scalar(stmt)
    now = utcnow()
    if row is None:
        db.add(
            FoodRecent(
                user_id=user_id,
                food_id=food_id,
                recipe_id=recipe_id,
                last_grams=grams,
                use_count=1,
                last_used_at=now,
            )
        )
    else:
        row.use_count += 1
        row.last_used_at = now
        if grams is not None:
            row.last_grams = grams
    await db.flush()


async def bump_use_count(db: AsyncSession, food_ids: set[uuid.UUID]) -> None:
    if not food_ids:
        return
    await db.execute(update(Food).where(Food.id.in_(food_ids)).values(use_count=Food.use_count + 1))


async def recipe_brief_map(db: AsyncSession, recipe_ids: set[uuid.UUID]) -> dict[uuid.UUID, Recipe]:
    if not recipe_ids:
        return {}
    rows = (await db.execute(select(Recipe).where(Recipe.id.in_(recipe_ids)))).scalars().all()
    return {row.id: row for row in rows}
