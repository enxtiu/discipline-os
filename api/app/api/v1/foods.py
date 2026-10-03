"""Food library: ranked search, barcode lookup, favorites, recents, custom foods."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import favorite_out, food_match_out, food_out, recent_out
from app.core.errors import Forbidden, NotFound
from app.core.logging import get_logger
from app.models import Food, FoodSource
from app.schemas.common import DeletedOut, OkOut
from app.schemas.food import (
    FavoriteIn,
    FavoriteOut,
    FoodBarcodeOut,
    FoodIn,
    FoodOut,
    FoodPatchIn,
    FoodSearchOut,
    RecentListOut,
)
from app.services import food_search
from app.services.nutrition import _d

log = get_logger(__name__)
router = APIRouter(prefix="/foods", tags=["foods"], dependencies=[Depends(require_device)])


@router.get("/search", response_model=FoodSearchOut, summary="Ranked food search (FTS + trgm, personal boosts)")
async def search(
    principal: CurrentPrincipal,
    db: DbSession,
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=24, ge=1, le=50),
) -> FoodSearchOut:
    hits, took_ms, has_more = await food_search.search_foods(db, user_id=principal.user_id, query=q, limit=limit)
    favorites = await food_search.favorite_ids(db, principal.user_id)
    return FoodSearchOut(
        query=q,
        items=[food_match_out(hit, favorites=favorites) for hit in hits],
        took_ms=took_ms,
        limit=limit,
        has_more=has_more,
    )


@router.get("/barcode/{code}", response_model=FoodBarcodeOut, summary="Look a food up by barcode")
async def barcode(code: str, principal: CurrentPrincipal, db: DbSession) -> FoodBarcodeOut:
    food = await food_search.barcode_lookup(db, principal.user_id, code)
    favorites = await food_search.favorite_ids(db, principal.user_id)
    return FoodBarcodeOut(barcode=code, food=food_out(food, favorites=favorites) if food else None)


@router.get("/favorites", response_model=list[FavoriteOut], summary="Favorite foods (one-tap row)")
async def favorites(principal: CurrentPrincipal, db: DbSession) -> list[FavoriteOut]:
    fav_map = await food_search.favorite_ids(db, principal.user_id)
    rows = await food_search.list_favorites(db, principal.user_id)
    return [favorite_out(fav, food, favorites=fav_map) for fav, food in rows]


@router.post("/favorites", response_model=FavoriteOut, status_code=201, summary="Add a favorite")
async def add_favorite(payload: FavoriteIn, principal: CurrentPrincipal, db: DbSession) -> FavoriteOut:
    fav = await food_search.add_favorite(
        db, user_id=principal.user_id, food_id=payload.food_id, default_grams=payload.default_grams
    )
    food = await food_search.get_food(db, principal.user_id, payload.food_id)
    fav_map = await food_search.favorite_ids(db, principal.user_id)
    assert food is not None
    return favorite_out(fav, food, favorites=fav_map)


@router.delete("/favorites/{food_id}", response_model=OkOut, summary="Remove a favorite")
async def remove_favorite(food_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    removed = await food_search.remove_favorite(db, user_id=principal.user_id, food_id=food_id)
    if not removed:
        raise NotFound("Not in favorites", code="favorite_not_found")
    return OkOut()


@router.get("/recents", response_model=RecentListOut, summary="Recently logged foods and recipes")
async def recents(principal: CurrentPrincipal, db: DbSession) -> RecentListOut:
    rows = await food_search.list_recents(db, user_id=principal.user_id)
    food_ids = {row.food_id for row in rows if row.food_id}
    recipe_ids = {row.recipe_id for row in rows if row.recipe_id}
    foods = (
        {row.id: row for row in (await db.execute(select(Food).where(Food.id.in_(food_ids)))).scalars().all()}
        if food_ids
        else {}
    )
    recipes = await food_search.recipe_brief_map(db, recipe_ids)
    fav_map = await food_search.favorite_ids(db, principal.user_id)
    return RecentListOut(
        items=[
            recent_out(row, foods=foods, recipes=recipes, favorites=fav_map)
            for row in rows
            if (row.food_id in foods) or (row.recipe_id in recipes)
        ]
    )


@router.post("", response_model=FoodOut, status_code=201, summary='Create a custom food ("my food")')
async def create_food(payload: FoodIn, principal: CurrentPrincipal, db: DbSession) -> FoodOut:
    food = Food(
        id=payload.id or None,
        user_id=principal.user_id,
        is_global=False,
        source=FoodSource.CUSTOM,
        name_ru=payload.name_ru,
        name_en=payload.name_en,
        aliases=payload.aliases,
        brand=payload.brand,
        barcode=payload.barcode,
        category=payload.category,
        kcal_per_100g=_d(payload.kcal_per_100g),
        protein_per_100g=_d(payload.protein_per_100g),
        fat_per_100g=_d(payload.fat_per_100g),
        carbs_per_100g=_d(payload.carbs_per_100g),
        fiber_per_100g=_d(payload.fiber_per_100g),
        default_serving_g=_d(payload.default_serving_g) if payload.default_serving_g else None,
        default_serving_label=payload.default_serving_label,
        is_liquid=payload.is_liquid,
        verified=True,
    )
    db.add(food)
    await db.flush()
    return food_out(food, favorites=await food_search.favorite_ids(db, principal.user_id))


@router.get("/{food_id}", response_model=FoodOut, summary="One food")
async def get_food(food_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> FoodOut:
    food = await food_search.get_food(db, principal.user_id, food_id)
    if food is None:
        raise NotFound("Food not found", code="food_not_found")
    return food_out(food, favorites=await food_search.favorite_ids(db, principal.user_id))


@router.patch("/{food_id}", response_model=FoodOut, summary="Edit a food (custom rows; verify library rows)")
async def patch_food(food_id: uuid.UUID, payload: FoodPatchIn, principal: CurrentPrincipal, db: DbSession) -> FoodOut:
    food = await food_search.get_food(db, principal.user_id, food_id)
    if food is None:
        raise NotFound("Food not found", code="food_not_found")
    if food.is_global and payload.verified is None:
        # library rows are read-only except the "verified" flag
        raise Forbidden("Global foods are read-only except verification", code="global_food_readonly")
    updates = payload.model_dump(exclude_unset=True, exclude={"verified"})
    for key, value in updates.items():
        if key.endswith("_per_100g") and value is not None:
            setattr(food, key, _d(value))
        elif value is not None or key in {"brand", "category", "default_serving_label"}:
            setattr(food, key, value)
    if payload.verified is not None:
        food.verified = payload.verified
    await db.flush()
    return food_out(food, favorites=await food_search.favorite_ids(db, principal.user_id))


@router.delete("/{food_id}", response_model=DeletedOut, summary="Archive a custom food")
async def delete_food(food_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> DeletedOut:
    food = await food_search.get_food(db, principal.user_id, food_id)
    if food is None or food.is_global:
        raise NotFound("Food not found", code="food_not_found")
    food.is_archived = True
    await db.flush()
    return DeletedOut(id=food_id)
