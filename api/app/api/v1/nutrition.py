"""Nutrition: the day dashboard, meal logging, copy-previous-meal, water."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import day_summary_out, meal_out, water_out
from app.core.logging import get_logger
from app.core.tz import today_in
from app.schemas.common import OkOut
from app.schemas.food import (
    CopiedMealOut,
    CopyMealIn,
    DaySummaryOut,
    MealLogIn,
    MealLogOut,
    MealLogPatchIn,
    WaterIn,
    WaterLogOut,
)
from app.services import nutrition

log = get_logger(__name__)
router = APIRouter(prefix="/nutrition", tags=["nutrition"], dependencies=[Depends(require_device)])


def _tz(principal: CurrentPrincipal) -> str | None:
    return principal.settings_row.timezone if principal.settings_row else None


@router.get("/day", response_model=DaySummaryOut, summary="Day dashboard: meals, macros, water (user's day)")
async def day(
    principal: CurrentPrincipal,
    db: DbSession,
    date: date = Query(default=None, description="Local calendar day; defaults to today"),
) -> DaySummaryOut:
    day_value = date or today_in(_tz(principal))
    meals = await nutrition.day_meals(db, principal.user_id, day_value)
    water_logs = await nutrition.day_water(db, principal.user_id, day_value)
    return day_summary_out(day_value, meals, water_logs)


@router.post("/meals", response_model=MealLogOut, status_code=201, summary="Log a meal (idempotent by client UUID)")
async def create_meal(payload: MealLogIn, principal: CurrentPrincipal, db: DbSession) -> MealLogOut:
    meal, _created = await nutrition.create_meal_log(db, user=principal.user, timezone=_tz(principal), payload=payload)
    return meal_out(meal)


@router.patch("/meals/{meal_id}", response_model=MealLogOut, summary="Edit a logged meal")
async def patch_meal(
    meal_id: uuid.UUID, payload: MealLogPatchIn, principal: CurrentPrincipal, db: DbSession
) -> MealLogOut:
    meal = await nutrition.update_meal_log(
        db, user=principal.user, timezone=_tz(principal), meal_id=meal_id, payload=payload
    )
    return meal_out(meal)


@router.delete("/meals/{meal_id}", response_model=OkOut, summary="Delete a logged meal (soft)")
async def delete_meal(meal_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await nutrition.delete_meal_log(db, principal.user_id, meal_id)
    return OkOut()


@router.post("/meals/copy", response_model=CopiedMealOut, summary="Copy a previous day's meals")
async def copy_meals(payload: CopyMealIn, principal: CurrentPrincipal, db: DbSession) -> CopiedMealOut:
    copies = await nutrition.copy_previous_meal(
        db,
        user=principal.user,
        timezone=_tz(principal),
        from_day=payload.from_day,
        meal_type=payload.meal_type,
        to_meal_type=payload.to_meal_type,
        logged_at=payload.logged_at,
    )
    return CopiedMealOut(copied=len(copies), meals=[meal_out(meal) for meal in copies])


@router.post("/water", response_model=WaterLogOut, status_code=201, summary="Add a water log")
async def add_water(payload: WaterIn, principal: CurrentPrincipal, db: DbSession) -> WaterLogOut:
    row, _created = await nutrition.add_water(
        db,
        user=principal.user,
        timezone=_tz(principal),
        amount_ml=payload.amount_ml,
        logged_at=payload.logged_at,
        client_uuid=payload.id,
        source=payload.source,
    )
    return water_out(row)


@router.delete("/water/{water_id}", response_model=OkOut, summary="Delete a water log")
async def delete_water(water_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await nutrition.delete_water(db, principal.user_id, water_id)
    return OkOut()
