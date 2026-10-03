"""Meal logging, the day dashboard and water.

Rules kept here (never in the client):
* ``local_day`` is derived from ``logged_at`` and the user's timezone.
* Macro snapshots are computed from the food/recipe rows at write time, so
  history never drifts when the library changes.
* Writes are idempotent through the client-generated ``client_uuid``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, Unprocessable
from app.core.logging import get_logger
from app.core.tz import get_zone, local_day, utcnow
from app.models import (
    Food,
    ItemSource,
    MatchMethod,
    MealItem,
    MealLog,
    MealSource,
    MealType,
    Recipe,
    User,
    WaterLog,
)
from app.services import food_search

log = get_logger(__name__)

_D = Decimal


def _d(value: float | int | Decimal) -> Decimal:
    return _D(str(value)).quantize(_D("0.01"))


def _f(value: Decimal | float | None) -> float:
    return float(value or 0)


@dataclass(slots=True)
class ItemSnapshot:
    name: str
    name_en: str | None
    brand: str | None
    grams: Decimal
    quantity: Decimal
    unit: str
    kcal: Decimal
    protein_g: Decimal
    fat_g: Decimal
    carbs_g: Decimal
    fiber_g: Decimal
    item_source: ItemSource
    match_method: MatchMethod
    food_id: uuid.UUID | None
    recipe_id: uuid.UUID | None
    confidence: float | None = None
    ai_name_raw: str | None = None
    is_edited: bool = False


def _macros_for_100g(food_or_recipe_per100: tuple[float, float, float, float, float], grams: Decimal) -> tuple:
    kcal, protein, fat, carbs, fiber = (_D(str(v)) for v in food_or_recipe_per100)
    factor = grams / _D("100")
    return (
        _d(kcal * factor),
        _d(protein * factor),
        _d(fat * factor),
        _d(carbs * factor),
        _d(fiber * factor),
    )


async def build_item_snapshots(db: AsyncSession, user_id: uuid.UUID, items: list) -> list[ItemSnapshot]:
    """Resolve every incoming item to a macro snapshot.

    ``items`` are ``MealItemIn`` schemas: a food id, a recipe id, or hand-typed
    macros (``item_source=custom``).
    """
    food_ids = {item.food_id for item in items if item.food_id}
    recipe_ids = {item.recipe_id for item in items if item.recipe_id}

    foods: dict[uuid.UUID, Food] = {}
    if food_ids:
        rows = (
            (
                await db.execute(
                    select(Food).where(
                        Food.id.in_(food_ids),
                        Food.is_archived.is_(False),
                        # global rows or the caller's own
                        (Food.user_id.is_(None)) | (Food.user_id == user_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        foods = {row.id: row for row in rows}
    recipes: dict[uuid.UUID, Recipe] = {}
    if recipe_ids:
        rows = (
            (await db.execute(select(Recipe).where(Recipe.id.in_(recipe_ids), Recipe.user_id == user_id)))
            .scalars()
            .all()
        )
        recipes = {row.id: row for row in rows}

    snapshots: list[ItemSnapshot] = []
    for position, item in enumerate(items):
        grams = _d(item.grams) * _d(item.quantity)
        if item.food_id:
            food = foods.get(item.food_id)
            if food is None:
                raise NotFound("Unknown food in items", code="food_not_found", details={"index": position})
            kcal, protein, fat, carbs, fiber = _macros_for_100g(
                (
                    food.kcal_per_100g,
                    food.protein_per_100g,
                    food.fat_per_100g,
                    food.carbs_per_100g,
                    food.fiber_per_100g,
                ),
                grams,
            )
            snapshots.append(
                ItemSnapshot(
                    name=food.name_ru or food.name_en or "",
                    name_en=food.name_en,
                    brand=food.brand,
                    grams=grams,
                    quantity=_d(item.quantity),
                    unit=item.unit,
                    kcal=kcal,
                    protein_g=protein,
                    fat_g=fat,
                    carbs_g=carbs,
                    fiber_g=fiber,
                    item_source=ItemSource.FOOD,
                    match_method=MatchMethod(item.match_method),
                    food_id=food.id,
                    recipe_id=None,
                )
            )
        elif item.recipe_id:
            recipe = recipes.get(item.recipe_id)
            if recipe is None:
                raise NotFound("Unknown recipe in items", code="recipe_not_found", details={"index": position})
            kcal, protein, fat, carbs, fiber = _macros_for_100g(
                (
                    recipe.kcal_per_100g,
                    recipe.protein_per_100g,
                    recipe.fat_per_100g,
                    recipe.carbs_per_100g,
                    recipe.fiber_per_100g,
                ),
                grams,
            )
            snapshots.append(
                ItemSnapshot(
                    name=recipe.name,
                    name_en=None,
                    brand=None,
                    grams=grams,
                    quantity=_d(item.quantity),
                    unit=item.unit,
                    kcal=kcal,
                    protein_g=protein,
                    fat_g=fat,
                    carbs_g=carbs,
                    fiber_g=fiber,
                    item_source=ItemSource.RECIPE,
                    match_method=MatchMethod(item.match_method),
                    food_id=None,
                    recipe_id=recipe.id,
                )
            )
        else:
            if item.name is None or item.kcal_per_100g is None:
                raise Unprocessable(
                    "Custom items need a name and macros",
                    code="custom_item_incomplete",
                    details={"index": position},
                )
            kcal, protein, fat, carbs, fiber = _macros_for_100g(
                (
                    item.kcal_per_100g,
                    item.protein_per_100g or 0,
                    item.fat_per_100g or 0,
                    item.carbs_per_100g or 0,
                    item.fiber_per_100g or 0,
                ),
                grams,
            )
            snapshots.append(
                ItemSnapshot(
                    name=item.name,
                    name_en=item.name_en,
                    brand=None,
                    grams=grams,
                    quantity=_d(item.quantity),
                    unit=item.unit,
                    kcal=kcal,
                    protein_g=protein,
                    fat_g=fat,
                    carbs_g=carbs,
                    fiber_g=fiber,
                    # an unmatched item the user accepted from the AI review keeps source=ai
                    item_source=ItemSource.AI if getattr(item, "item_source", None) == "ai" else ItemSource.CUSTOM,
                    match_method=MatchMethod.AI if getattr(item, "item_source", None) == "ai" else MatchMethod.MANUAL,
                    food_id=None,
                    recipe_id=None,
                )
            )
    for snap, item in zip(snapshots, items, strict=True):
        snap.confidence = getattr(item, "confidence", None)
        snap.ai_name_raw = getattr(item, "ai_name_raw", None)
        snap.is_edited = bool(getattr(item, "is_edited", False))
    return snapshots


def _apply_snapshots(meal: MealLog, snapshots: list[ItemSnapshot], sort_from: int = 0) -> None:
    for offset, snap in enumerate(snapshots):
        meal.items.append(
            MealItem(
                user_id=meal.user_id,
                name=snap.name,
                name_en=snap.name_en,
                brand=snap.brand,
                grams=snap.grams,
                quantity=snap.quantity,
                unit=snap.unit,
                kcal=snap.kcal,
                protein_g=snap.protein_g,
                fat_g=snap.fat_g,
                carbs_g=snap.carbs_g,
                fiber_g=snap.fiber_g,
                item_source=snap.item_source,
                match_method=snap.match_method,
                food_id=snap.food_id,
                recipe_id=snap.recipe_id,
                confidence=snap.confidence,
                ai_name_raw=snap.ai_name_raw,
                is_edited=snap.is_edited,
                sort_order=sort_from + offset,
            )
        )
    meal.kcal = _d(sum((item.kcal for item in meal.items), _D("0")))
    meal.protein_g = _d(sum((item.protein_g for item in meal.items), _D("0")))
    meal.fat_g = _d(sum((item.fat_g for item in meal.items), _D("0")))
    meal.carbs_g = _d(sum((item.carbs_g for item in meal.items), _D("0")))
    meal.fiber_g = _d(sum((item.fiber_g for item in meal.items), _D("0")))
    meal.item_count = len(meal.items)


async def create_meal_log(
    db: AsyncSession,
    *,
    user: User,
    timezone: str | None,
    payload,
) -> tuple[MealLog, bool]:
    """Returns (meal, created). Replaying the same client UUID is a no-op."""
    if payload.id is not None:
        existing = await db.scalar(select(MealLog).where(MealLog.client_uuid == payload.id))
        if existing is not None and existing.user_id == user.id:
            return existing, False

    logged_at = payload.logged_at or utcnow()
    if logged_at.tzinfo is None:  # client sent local wall clock → localise it
        logged_at = logged_at.replace(tzinfo=get_zone(timezone))
    meal = MealLog(
        user_id=user.id,
        client_uuid=payload.id or uuid.uuid4(),
        logged_at=logged_at,
        local_day=local_day(logged_at, timezone),
        meal_type=MealType(payload.meal_type),
        source=MealSource(payload.source),
        title=payload.title,
        notes=payload.notes,
    )
    snapshots = await build_item_snapshots(db, user.id, payload.items)
    _apply_snapshots(meal, snapshots)
    db.add(meal)
    await db.flush()
    if getattr(payload, "ai_job_id", None) is not None:
        from app.services import ai_meal  # stage 3; imported lazily to keep stage-2 imports flat

        await ai_meal.attach_job_to_meal(db, user=user, meal=meal, job_id=payload.ai_job_id)

    for snap in snapshots:
        if snap.food_id:
            await food_search.touch_recent(db, user_id=user.id, food_id=snap.food_id, grams=float(snap.grams))
        elif snap.recipe_id:
            await food_search.touch_recent(db, user_id=user.id, recipe_id=snap.recipe_id, grams=float(snap.grams))
    await food_search.bump_use_count(db, {s.food_id for s in snapshots if s.food_id})
    return meal, True


async def get_meal_log(db: AsyncSession, user_id: uuid.UUID, meal_id: uuid.UUID) -> MealLog:
    meal = await db.scalar(select(MealLog).where(MealLog.id == meal_id, MealLog.user_id == user_id))
    if meal is None or meal.is_deleted:
        raise NotFound("Meal not found", code="meal_not_found")
    return meal


async def update_meal_log(
    db: AsyncSession,
    *,
    user: User,
    timezone: str | None,
    meal_id: uuid.UUID,
    payload,
) -> MealLog:
    meal = await get_meal_log(db, user.id, meal_id)
    if payload.logged_at is not None:
        meal.logged_at = payload.logged_at
        meal.local_day = local_day(payload.logged_at, timezone)
    if payload.meal_type is not None:
        meal.meal_type = MealType(payload.meal_type)
    if payload.title is not None:
        meal.title = payload.title
    if payload.notes is not None:
        meal.notes = payload.notes
    if payload.items is not None:
        for item in list(meal.items):
            await db.delete(item)
        await db.flush()
        meal.items = []
        snapshots = await build_item_snapshots(db, user.id, payload.items)
        _apply_snapshots(meal, snapshots)
    await db.flush()
    return meal


async def delete_meal_log(db: AsyncSession, user_id: uuid.UUID, meal_id: uuid.UUID) -> None:
    meal = await get_meal_log(db, user_id, meal_id)
    meal.is_deleted = True
    if meal.photo_thumb_path:  # a deleted meal keeps no picture
        from app.services import media

        media.delete(meal.photo_thumb_path)
        meal.photo_thumb_path = None
    await db.flush()


async def copy_previous_meal(
    db: AsyncSession,
    *,
    user: User,
    timezone: str | None,
    from_day: date,
    meal_type: str | None,
    to_meal_type: str | None,
    logged_at: datetime | None,
) -> list[MealLog]:
    """Duplicate a previous day's meals into a new timestamp (source=copy)."""
    stmt = (
        select(MealLog)
        .where(MealLog.user_id == user.id, MealLog.local_day == from_day, MealLog.is_deleted.is_(False))
        .order_by(MealLog.logged_at)
    )
    if meal_type:
        stmt = stmt.where(MealLog.meal_type == MealType(meal_type))
    sources = (await db.execute(stmt)).scalars().all()
    if not sources:
        raise NotFound("Nothing to copy for that day", code="nothing_to_copy", details={"from_day": str(from_day)})

    now = logged_at or utcnow()
    copies: list[MealLog] = []
    for source in sources:
        clone = MealLog(
            user_id=user.id,
            client_uuid=uuid.uuid4(),
            logged_at=now,
            local_day=local_day(now, timezone),
            meal_type=MealType(to_meal_type) if to_meal_type else source.meal_type,
            source=MealSource.COPY,
            title=source.title,
            notes=source.notes,
        )
        for item in source.items:
            clone.items.append(
                MealItem(
                    user_id=user.id,
                    name=item.name,
                    name_en=item.name_en,
                    brand=item.brand,
                    grams=item.grams,
                    quantity=item.quantity,
                    unit=item.unit,
                    kcal=item.kcal,
                    protein_g=item.protein_g,
                    fat_g=item.fat_g,
                    carbs_g=item.carbs_g,
                    fiber_g=item.fiber_g,
                    item_source=item.item_source,
                    match_method=item.match_method,
                    food_id=item.food_id,
                    recipe_id=item.recipe_id,
                    sort_order=item.sort_order,
                )
            )
        clone.kcal = source.kcal
        clone.protein_g = source.protein_g
        clone.fat_g = source.fat_g
        clone.carbs_g = source.carbs_g
        clone.fiber_g = source.fiber_g
        clone.item_count = source.item_count
        db.add(clone)
        copies.append(clone)
    await db.flush()
    return copies


# --------------------------------------------------------------------------- #
# Day dashboard + water
# --------------------------------------------------------------------------- #
async def day_meals(db: AsyncSession, user_id: uuid.UUID, day: date) -> list[MealLog]:
    return (
        (
            await db.execute(
                select(MealLog)
                .where(MealLog.user_id == user_id, MealLog.local_day == day, MealLog.is_deleted.is_(False))
                .order_by(MealLog.logged_at)
            )
        )
        .scalars()
        .all()
    )


async def day_water(db: AsyncSession, user_id: uuid.UUID, day: date) -> list[WaterLog]:
    return (
        (
            await db.execute(
                select(WaterLog)
                .where(WaterLog.user_id == user_id, WaterLog.local_day == day)
                .order_by(WaterLog.logged_at)
            )
        )
        .scalars()
        .all()
    )


async def add_water(
    db: AsyncSession,
    *,
    user: User,
    timezone: str | None,
    amount_ml: int,
    logged_at: datetime | None,
    client_uuid: uuid.UUID | None,
    source: str,
) -> tuple[WaterLog, bool]:
    if client_uuid is not None:
        existing = await db.scalar(select(WaterLog).where(WaterLog.client_uuid == client_uuid))
        if existing is not None and existing.user_id == user.id:
            return existing, False
    at = logged_at or utcnow()
    row = WaterLog(
        user_id=user.id,
        client_uuid=client_uuid or uuid.uuid4(),
        logged_at=at,
        local_day=local_day(at, timezone),
        amount_ml=amount_ml,
        source=source,
    )
    db.add(row)
    await db.flush()
    return row, True


async def delete_water(db: AsyncSession, user_id: uuid.UUID, water_id: uuid.UUID) -> None:
    row = await db.scalar(select(WaterLog).where(WaterLog.id == water_id, WaterLog.user_id == user_id))
    if row is None:
        raise NotFound("Water log not found", code="water_not_found")
    await db.delete(row)
    await db.flush()
