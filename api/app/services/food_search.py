"""Ranked food search and the food library (favorites, recents, barcodes).

Ranking contract (stage 2 spec)
-------------------------------
1. Russian and English full-text search (trigger-maintained ``tsvector``),
   plus ``pg_trgm`` similarity for typos and morphological near-misses
   ("гречка" → "Гречневая крупа", "chicken brest" → "Chicken breast").
2. The user's own world wins: custom foods, verified rows, favorites and
   recents are boosted above anonymous import rows.
3. Popular global rows (``use_count``) break the remaining ties.
4. Every query must stay below 150 ms (``took_ms``): candidates come from a
   UNION of single-index branches (GIN on tsvector / pg_trgm), each capped at
   CANDIDATE_CAP library rows, and the full score runs on that bounded set only,
   never on the whole library. A branch still ranks every row its index
   matches before the cap, so the budget is verified by measurement, not
   assumed (stage 2b: ``tests/test_stage2b.py``, ``app.cli bench-search``).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, case, cast, delete, func, literal, or_, select, text, union, update
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
PREFIX_FALLBACK_CHARS = 3  # short queries use ILIKE prefix matching instead

MIN_SIMILARITY = TRGM_THRESHOLD
# rows each matching strategy may contribute before scoring (see search_foods)
CANDIDATE_CAP = 120


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
    # the user's own food for this barcode wins over library rows, then curated ones
    return await db.scalar(
        _visible_foods(user_id)
        .where(Food.barcode == cleaned)
        .order_by(Food.user_id.is_(None), Food.verified.desc(), Food.created_at)
        .limit(1)
    )


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
    name_sim = func.greatest(
        func.similarity(func.lower(Food.name_ru), nq),
        func.similarity(func.lower(Food.name_en), nq),
    )
    alias_sim = func.word_similarity(nq, func.lower(Food.aliases_text))
    sim = func.greatest(name_sim, alias_sim)
    exact = or_(func.lower(Food.name_ru) == nq, func.lower(Food.name_en) == nq)
    escaped = nq.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"{escaped}%" if len(nq) <= PREFIX_FALLBACK_CHARS else f"%{escaped}%"
    name_like = or_(func.lower(Food.name_ru).like(like), func.lower(Food.name_en).like(like))
    alias_like = func.lower(Food.aliases_text).like(f"%{escaped}%")

    fav_sub = select(FoodFavorite.food_id).where(FoodFavorite.user_id == user_id).scalar_subquery()
    is_fav = Food.id.in_(fav_sub)
    own = Food.user_id.is_not(None)

    def _bit(expr) -> object:
        # Postgres cannot cast boolean → float directly
        return case((expr, 1.0), else_=0.0)

    # Relevance first, then the personal world: the user's own foods, favorites
    # and verified rows outrank anonymous library hits (stage-2 spec).
    # A hit in the *name* outranks a hit that only came through an alias or the
    # brand (stage 2b: hundreds of library rows can share one alias word).
    score = (
        _bit(exact) * 1.0
        + fts_rank * 1.2
        + func.least(name_sim, 1.0) * 0.9
        + func.least(alias_sim, 1.0) * 0.6
        + _bit(name_like) * 0.3
        + _bit(own) * 2.0
        + _bit(is_fav) * 0.5
        + _bit(Food.verified) * 0.15
        # generic rows (no brand: curated seed, USDA, "my food") before branded
        # packs when the query names no brand; a brand in the query wins via FTS
        + _bit(Food.brand.is_(None)) * 0.2
        + func.least(Food.use_count, 200) / 200.0 * 0.25
    )

    # Candidates: one capped branch per matching strategy, UNIONed. Each library
    # branch is a single GIN index probe (tsvector @@, pg_trgm % / <% / LIKE)
    # whose recheck only evaluates its own predicate, and keeps its best
    # CANDIDATE_CAP rows by its own measure. Only that bounded set gets the full
    # score below, so a common word ("молоко" × 3 000 brands) no longer means
    # scoring thousands of rows. The thresholds make `%` / `<%` mean exactly
    # "similarity >= MIN_SIMILARITY" — the cut the ranking always used.
    await _set_trgm_thresholds(db)
    visible = (
        Food.is_archived.is_(False),
        or_(Food.is_global.is_(True), Food.user_id == user_id),
    )
    # cheap per-row tie-breakers so a cap keeps name hits and curated rows
    tie_break = (_bit(name_like).desc(), Food.verified.desc(), Food.use_count.desc())
    any_match = or_(
        Food.search_vector_ru.op("@@")(q_ru),
        Food.search_vector_en.op("@@")(q_en),
        func.lower(Food.name_ru).op("%")(nq),
        func.lower(Food.name_en).op("%")(nq),
        literal(nq).op("<%")(func.lower(Food.aliases_text)),
        name_like,
        alias_like,
    )
    library_branches = [
        select(Food.id)
        .where(Food.search_vector_ru.op("@@")(q_ru), *visible)
        .order_by(func.ts_rank(Food.search_vector_ru, q_ru).desc(), *tie_break),
        select(Food.id)
        .where(Food.search_vector_en.op("@@")(q_en), *visible)
        .order_by(func.ts_rank(Food.search_vector_en, q_en).desc(), *tie_break),
        select(Food.id)
        .where(func.lower(Food.name_ru).op("%")(nq), *visible)
        .order_by(func.similarity(func.lower(Food.name_ru), nq).desc(), *tie_break),
        select(Food.id)
        .where(func.lower(Food.name_en).op("%")(nq), *visible)
        .order_by(func.similarity(func.lower(Food.name_en), nq).desc(), *tie_break),
        select(Food.id)
        .where(literal(nq).op("<%")(func.lower(Food.aliases_text)), *visible)
        .order_by(func.word_similarity(nq, func.lower(Food.aliases_text)).desc(), *tie_break),
        # prefix / substring typing ("гре" → гречка); shortest names are closest
        select(Food.id).where(name_like, *visible).order_by(func.length(func.coalesce(Food.name_ru, Food.name_en))),
        select(Food.id).where(alias_like, *visible).order_by(func.length(Food.aliases_text)),
    ]
    # The personal world is never cut by a cap: own foods (ix_foods_user_id) and
    # favorites (food_favorites.user_id) are small per user, filtered by any match.
    personal_branches = [
        select(Food.id).where(Food.user_id == user_id, Food.is_archived.is_(False), any_match),
        select(Food.id)
        .join(FoodFavorite, FoodFavorite.food_id == Food.id)
        .where(FoodFavorite.user_id == user_id, Food.is_archived.is_(False), any_match),
    ]
    candidates = union(
        *(select(branch.limit(CANDIDATE_CAP).subquery().c.id) for branch in library_branches),
        *personal_branches,
    ).subquery()
    stmt = (
        select(Food)
        .where(Food.id.in_(select(candidates.c.id)))
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


async def _set_trgm_thresholds(db: AsyncSession) -> None:
    """Transaction-local pg_trgm thresholds for the `%` and `<%` operators."""
    await db.execute(
        text(
            "SELECT set_config('pg_trgm.similarity_threshold', :t, true),"
            " set_config('pg_trgm.word_similarity_threshold', :t, true)"
        ),
        {"t": str(MIN_SIMILARITY)},
    )


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
