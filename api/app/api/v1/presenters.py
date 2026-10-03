"""Turn ORM rows into API schemas (single place, so shapes stay consistent)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from app.api.deps import Principal
from app.core.tz import utcnow
from app.models import (
    ApiToken,
    Device,
    Food,
    FoodFavorite,
    FoodRecent,
    MealLog,
    MealType,
    Recipe,
    User,
    UserSettings,
    WaterLog,
)
from app.schemas.auth import ApiTokenOut, DeviceOut, SessionOut, UserSummaryOut
from app.schemas.food import (
    DaySummaryOut,
    FavoriteOut,
    FoodMatchOut,
    FoodOut,
    MacrosOut,
    MealLogOut,
    MealTypeTotalOut,
    RecentOut,
    RecipeBriefOut,
    RecipeOut,
    WaterLogOut,
)
from app.services.food_search import FoodHit


def device_out(device: Device, *, current_id: uuid.UUID | None = None) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    out.is_current = bool(current_id and device.id == current_id)
    return out


def api_token_out(token: ApiToken) -> ApiTokenOut:
    return ApiTokenOut.model_validate(token)


def user_summary(user: User) -> UserSummaryOut:
    return UserSummaryOut.model_validate(user)


def build_session(principal: Principal, row: UserSettings, targets: dict[str, Any]) -> SessionOut:
    return SessionOut(
        user=user_summary(principal.user),
        settings=row,
        device=device_out(principal.device, current_id=principal.device.id) if principal.device else None,
        api_token=api_token_out(principal.api_token) if principal.api_token else None,
        auth_kind=principal.kind,
        scopes=sorted(principal.scopes),
        server_time=utcnow(),
        targets=targets,
    )


# --------------------------------------------------------------------------- #
# Nutrition (stage 2)
# --------------------------------------------------------------------------- #
def food_out(food: Food, *, favorites: dict[uuid.UUID, float | None] | None = None) -> FoodOut:
    out = FoodOut.model_validate(food)
    if favorites is not None:
        out.is_favorite = food.id in favorites
        out.favorite_grams = favorites.get(food.id)
    return out


def food_match_out(hit: FoodHit, *, favorites: dict[uuid.UUID, float | None]) -> FoodMatchOut:
    payload = food_out(hit.food, favorites=favorites).model_dump()
    payload["score"] = round(hit.score, 4)
    payload["match_method"] = hit.match_method
    return FoodMatchOut.model_validate(payload)


def favorite_out(fav: FoodFavorite, food: Food, *, favorites: dict[uuid.UUID, float | None]) -> FavoriteOut:
    return FavoriteOut(
        id=fav.id,
        food_id=fav.food_id,
        default_grams=fav.default_grams,
        sort_order=fav.sort_order,
        food=food_out(food, favorites=favorites),
    )


def recent_out(
    rec: FoodRecent,
    *,
    foods: dict[uuid.UUID, Food],
    recipes: dict[uuid.UUID, Recipe],
    favorites: dict[uuid.UUID, float | None],
) -> RecentOut:
    return RecentOut(
        id=rec.id,
        food=food_out(foods[rec.food_id], favorites=favorites) if rec.food_id and rec.food_id in foods else None,
        recipe=recipe_brief(recipes[rec.recipe_id]) if rec.recipe_id and rec.recipe_id in recipes else None,
        last_grams=rec.last_grams,
        use_count=rec.use_count,
        last_used_at=rec.last_used_at,
    )


def recipe_brief(recipe: Recipe) -> RecipeBriefOut:
    return RecipeBriefOut.model_validate(recipe)


def recipe_out(recipe: Recipe) -> RecipeOut:
    payload = {name: getattr(recipe, name) for name in RecipeOut.model_fields if name != "per_serving"}
    # one portion = portion_size_g of the cooked dish (the same grams the log
    # sheet writes for "1 portion"); computed from the unrounded totals
    base = float(recipe.cooked_weight_g or 0) or float(recipe.total_raw_weight_g or 0) or 100.0
    factor = float(recipe.portion_size_g or 0) / base
    payload["per_serving"] = MacrosOut(
        kcal=round(float(recipe.total_kcal) * factor, 2),
        protein_g=round(float(recipe.total_protein_g) * factor, 2),
        fat_g=round(float(recipe.total_fat_g) * factor, 2),
        carbs_g=round(float(recipe.total_carbs_g) * factor, 2),
        fiber_g=round(float(recipe.total_fiber_g) * factor, 2),
    )
    return RecipeOut.model_validate(payload)


def meal_out(meal: MealLog) -> MealLogOut:
    return MealLogOut.model_validate(meal)


def water_out(row: WaterLog) -> WaterLogOut:
    return WaterLogOut.model_validate(row)


def day_summary_out(day: date, meals: list[MealLog], water_logs: list[WaterLog]) -> DaySummaryOut:
    totals = MacrosOut()
    by_type: dict[str, MealTypeTotalOut] = {kind.value: MealTypeTotalOut(meal_type=kind.value) for kind in MealType}
    for meal in meals:
        totals.kcal += float(meal.kcal)
        totals.protein_g += float(meal.protein_g)
        totals.fat_g += float(meal.fat_g)
        totals.carbs_g += float(meal.carbs_g)
        totals.fiber_g += float(meal.fiber_g)
        bucket = by_type[meal.meal_type.value]
        bucket.meals += 1
        bucket.kcal += float(meal.kcal)
        bucket.protein_g += float(meal.protein_g)
        bucket.fat_g += float(meal.fat_g)
        bucket.carbs_g += float(meal.carbs_g)
        bucket.fiber_g += float(meal.fiber_g)
    return DaySummaryOut(
        date=day,
        totals=MacrosOut(
            kcal=round(totals.kcal, 2),
            protein_g=round(totals.protein_g, 2),
            fat_g=round(totals.fat_g, 2),
            carbs_g=round(totals.carbs_g, 2),
            fiber_g=round(totals.fiber_g, 2),
        ),
        by_meal_type=[by_type[kind.value] for kind in MealType if by_type[kind.value].meals],
        meals=[meal_out(meal) for meal in meals],
        water_ml=sum(row.amount_ml for row in water_logs),
        water_logs=[water_out(row) for row in water_logs],
        logged_meals=len(meals),
    )
