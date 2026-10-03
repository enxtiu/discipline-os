"""Recipes: CRUD with denormalised macros.

A recipe stores both the whole-dish totals and per-100 g values of the
*cooked* dish, so logging "350 g of my borscht" is one multiplication. Item
snapshots keep the recipe stable even if a food row is edited later.

Yield and portions
------------------
* ``cooked_weight_g`` is the weight of the finished dish (water boils off,
  grains absorb it). Unset (0) means "same as the raw ingredients" and then
  follows the ingredient list when it changes.
* A portion is defined either by ``servings`` (the dish is split into N) or
  by ``portion_size_g`` (one plate weighs X g, servings = cooked / X). Per-portion
  macros are always ``per_100g × portion_size_g / 100`` — exactly what logging
  one portion writes to the diary.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.models import Food, Recipe, RecipeItem
from app.services.nutrition import _d

log = get_logger(__name__)


async def get_recipe(db: AsyncSession, user_id: uuid.UUID, recipe_id: uuid.UUID) -> Recipe:
    recipe = await db.scalar(
        select(Recipe).where(Recipe.id == recipe_id, Recipe.user_id == user_id, Recipe.is_archived.is_(False))
    )
    if recipe is None:
        raise NotFound("Recipe not found", code="recipe_not_found")
    return recipe


async def list_recipes(db: AsyncSession, user_id: uuid.UUID, include_archived: bool = False) -> list[Recipe]:
    stmt = select(Recipe).where(Recipe.user_id == user_id)
    if not include_archived:
        stmt = stmt.where(Recipe.is_archived.is_(False))
    stmt = stmt.order_by(Recipe.is_pinned.desc(), Recipe.use_count.desc(), Recipe.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


def _recompute(recipe: Recipe, *, portion_g: float | None = None) -> None:
    raw_total = _d(sum((item.grams for item in recipe.items), Decimal("0")))
    recipe.total_raw_weight_g = raw_total
    if recipe.cooked_weight_g in (None, Decimal("0")):
        recipe.cooked_weight_g = raw_total
    recipe.total_kcal = _d(sum((item.kcal for item in recipe.items), Decimal("0")))
    recipe.total_protein_g = _d(sum((item.protein_g for item in recipe.items), Decimal("0")))
    recipe.total_fat_g = _d(sum((item.fat_g for item in recipe.items), Decimal("0")))
    recipe.total_carbs_g = _d(sum((item.carbs_g for item in recipe.items), Decimal("0")))
    recipe.total_fiber_g = _d(sum((item.fiber_g for item in recipe.items), Decimal("0")))
    base = recipe.cooked_weight_g if recipe.cooked_weight_g > 0 else Decimal("100")
    factor = Decimal("100") / base
    recipe.kcal_per_100g = _d(recipe.total_kcal * factor)
    recipe.protein_per_100g = _d(recipe.total_protein_g * factor)
    recipe.fat_per_100g = _d(recipe.total_fat_g * factor)
    recipe.carbs_per_100g = _d(recipe.total_carbs_g * factor)
    recipe.fiber_per_100g = _d(recipe.total_fiber_g * factor)
    if portion_g:
        portion = _d(portion_g)
        recipe.portion_size_g = portion
        recipe.servings = max(Decimal("0.01"), (base / portion).quantize(Decimal("0.01")))
    else:
        recipe.portion_size_g = _d(base / recipe.servings) if recipe.servings else _d(base)


async def _replace_items(db: AsyncSession, recipe: Recipe, items_in: list) -> None:
    food_ids = {item.food_id for item in items_in}
    foods = {
        row.id: row
        for row in (
            await db.execute(
                select(Food).where(
                    Food.id.in_(food_ids),
                    Food.is_archived.is_(False),
                    (Food.user_id.is_(None)) | (Food.user_id == recipe.user_id),
                )
            )
        )
        .scalars()
        .all()
    }
    # load the collection explicitly: lazy loads are forbidden in async sessions,
    # and delete-orphan needs the old rows in memory anyway
    await db.refresh(recipe, ["items"])
    for item in list(recipe.items):
        await db.delete(item)
    await db.flush()
    recipe.items = []
    for position, item_in in enumerate(items_in):
        food = foods.get(item_in.food_id)
        if food is None:
            raise NotFound("Unknown food in recipe items", code="food_not_found", details={"index": position})
        grams = _d(item_in.grams)
        factor = grams / Decimal("100")
        recipe.items.append(
            RecipeItem(
                user_id=recipe.user_id,
                food_id=food.id,
                grams=grams,
                sort_order=position,
                note=item_in.note,
                snapshot_name=food.name_ru or food.name_en or "",
                kcal=_d(food.kcal_per_100g * factor),
                protein_g=_d(food.protein_per_100g * factor),
                fat_g=_d(food.fat_per_100g * factor),
                carbs_g=_d(food.carbs_per_100g * factor),
                fiber_g=_d(food.fiber_per_100g * factor),
            )
        )


async def create_recipe(db: AsyncSession, *, user_id: uuid.UUID, payload) -> Recipe:
    if payload.id:
        # idempotent replay (offline queue / double tap): same id → same recipe
        existing = await db.scalar(select(Recipe).where(Recipe.id == payload.id))
        if existing is not None:
            if existing.user_id != user_id:
                raise Conflict("Recipe id already in use", code="conflict")
            await db.refresh(existing, ["items"])
            return existing
    recipe = Recipe(
        id=payload.id or None,
        user_id=user_id,
        name=payload.name,
        description=payload.description,
        notes_md=payload.notes_md,
        servings=Decimal(str(payload.servings)),
        cooked_weight_g=_d(payload.cooked_weight_g) if payload.cooked_weight_g else Decimal("0"),
        is_pinned=payload.is_pinned,
    )
    db.add(recipe)
    await db.flush()
    await _replace_items(db, recipe, payload.items)
    _recompute(recipe, portion_g=payload.portion_size_g)
    await db.flush()
    await db.refresh(recipe, ["items"])
    return recipe


async def update_recipe(db: AsyncSession, *, user_id: uuid.UUID, recipe_id: uuid.UUID, payload) -> Recipe:
    recipe = await get_recipe(db, user_id, recipe_id)
    if payload.name is not None:
        recipe.name = payload.name
    if payload.description is not None:
        recipe.description = payload.description
    if payload.notes_md is not None:
        recipe.notes_md = payload.notes_md
    if payload.servings is not None:
        recipe.servings = Decimal(str(payload.servings))
    if payload.is_pinned is not None:
        recipe.is_pinned = payload.is_pinned
    if payload.cooked_weight_g is not None:
        recipe.cooked_weight_g = _d(payload.cooked_weight_g)
    elif payload.items is not None and recipe.cooked_weight_g == recipe.total_raw_weight_g:
        # the yield was never set explicitly (it mirrored the raw weight):
        # keep following the ingredients instead of freezing the old total
        recipe.cooked_weight_g = Decimal("0")
    if payload.items is not None:
        await _replace_items(db, recipe, payload.items)
    reshaped = any(
        value is not None
        for value in (payload.items, payload.servings, payload.cooked_weight_g, payload.portion_size_g)
    )
    if reshaped:
        # a pin or a rename never re-derives the portion (no rounding drift)
        _recompute(recipe, portion_g=payload.portion_size_g)
    await db.flush()
    await db.refresh(recipe, ["items"])
    return recipe


async def archive_recipe(db: AsyncSession, *, user_id: uuid.UUID, recipe_id: uuid.UUID) -> None:
    recipe = await get_recipe(db, user_id, recipe_id)
    recipe.is_archived = True
    await db.flush()


async def delete_recipe(db: AsyncSession, *, user_id: uuid.UUID, recipe_id: uuid.UUID) -> None:
    recipe = await get_recipe(db, user_id, recipe_id)
    await db.delete(recipe)
    await db.flush()
