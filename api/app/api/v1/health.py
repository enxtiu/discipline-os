"""Health (stage 5): what the app shows and the manual entries.

Steps can be typed in when the Shortcut is not set up; weight and sleep are
manual by design. Every write is an idempotent upsert by local day, so the
offline queue may replay it.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import health_day_out, sleep_out, weight_out
from app.core.tz import today_in
from app.schemas.common import OkOut
from app.schemas.health import (
    HealthDayOut,
    HealthTodayOut,
    SleepIn,
    SleepOut,
    SleepSeriesOut,
    StepsIn,
    StepsSeriesOut,
    WeightIn,
    WeightOut,
    WeightSeriesOut,
)
from app.services import health

router = APIRouter(prefix="/health", tags=["health"], dependencies=[Depends(require_device)])


def _tz(principal: CurrentPrincipal) -> str | None:
    return principal.settings_row.timezone if principal.settings_row else None


@router.get("/today", response_model=HealthTodayOut, summary="Steps, latest weight and last night's sleep")
async def today(principal: CurrentPrincipal, db: DbSession) -> HealthTodayOut:
    day = today_in(_tz(principal))
    view = (await health.day_views(db, user_id=principal.user_id, start=day, end=day))[0]
    latest = await health.latest_weight(db, principal.user_id)
    change = None
    if latest is not None:
        earlier = await health.weight_before(db, principal.user_id, latest.measured_on - timedelta(days=7))
        # a weigh-in from 7–14 days back; anything older is not a "week" change
        if earlier is not None and earlier.measured_on >= latest.measured_on - timedelta(days=14):
            change = round(float(latest.weight_kg) - float(earlier.weight_kg), 2)
    sleep = await health.sleep_series(db, user_id=principal.user_id, start=day - timedelta(days=1), end=day)
    return HealthTodayOut(
        day=health_day_out(view),
        weight=weight_out(latest) if latest is not None else None,
        weight_change_7d_kg=change,
        sleep=sleep_out(sleep[0]) if sleep else None,
        last_sync_at=await health.last_sync_at(db, principal.user_id),
    )


@router.get("/steps", response_model=StepsSeriesOut, summary="Steps per day (zero-filled), target and average")
async def steps(
    principal: CurrentPrincipal, db: DbSession, days: int = Query(default=14, ge=1, le=90)
) -> StepsSeriesOut:
    end = today_in(_tz(principal))
    views = await health.day_views(db, user_id=principal.user_id, start=end - timedelta(days=days - 1), end=end)
    rows = [health_day_out(view) for view in views]
    target = views[0].target_steps if views else 0
    return StepsSeriesOut(
        days=rows,
        target_steps=target,
        average_steps=round(sum(row.steps for row in rows) / len(rows)) if rows else 0,
        days_on_target=sum(1 for row in rows if target and row.steps >= target),
    )


@router.put("/steps/{day}", response_model=HealthDayOut, summary="Type in a day's steps (manual fallback)")
async def put_steps(day: date, payload: StepsIn, principal: CurrentPrincipal, db: DbSession) -> HealthDayOut:
    await health.set_manual_steps(db, user_id=principal.user_id, tz=_tz(principal), day=day, payload=payload)
    view = (await health.day_views(db, user_id=principal.user_id, start=day, end=day))[0]
    return health_day_out(view)


@router.get("/weight", response_model=WeightSeriesOut, summary="Weigh-ins, 7-day trend and 30-day change")
async def weight(
    principal: CurrentPrincipal, db: DbSession, days: int = Query(default=90, ge=7, le=400)
) -> WeightSeriesOut:
    end = today_in(_tz(principal))
    rows = await health.weight_series(db, user_id=principal.user_id, start=end - timedelta(days=days - 1), end=end)
    latest = rows[-1] if rows else None
    change = None
    if latest is not None:
        earlier = await health.weight_before(db, principal.user_id, latest.measured_on - timedelta(days=30))
        if earlier is not None:
            change = round(float(latest.weight_kg) - float(earlier.weight_kg), 2)
    return WeightSeriesOut(
        items=[weight_out(row) for row in rows],
        latest=weight_out(latest) if latest is not None else None,
        trend_kg=health.trend(rows),
        change_30d_kg=change,
    )


@router.put("/weight/{day}", response_model=WeightOut, summary="Log the weigh-in of a day (one per day)")
async def put_weight(day: date, payload: WeightIn, principal: CurrentPrincipal, db: DbSession) -> WeightOut:
    row = await health.set_weight(
        db,
        user_id=principal.user_id,
        tz=_tz(principal),
        day=day,
        weight_kg=payload.weight_kg,
        body_fat_pct=payload.body_fat_pct,
        waist_cm=payload.waist_cm,
        note=payload.note,
    )
    return weight_out(row)


@router.delete("/weight/{day}", response_model=OkOut, summary="Remove a weigh-in")
async def delete_weight(day: date, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await health.delete_weight(db, user_id=principal.user_id, day=day)
    return OkOut()


@router.get("/sleep", response_model=SleepSeriesOut, summary="Sleep entries (newest first) and the average")
async def sleep(
    principal: CurrentPrincipal, db: DbSession, days: int = Query(default=14, ge=1, le=90)
) -> SleepSeriesOut:
    end = today_in(_tz(principal))
    rows = await health.sleep_series(db, user_id=principal.user_id, start=end - timedelta(days=days - 1), end=end)
    return SleepSeriesOut(
        items=[sleep_out(row) for row in rows],
        average_min=round(sum(row.duration_min for row in rows) / len(rows)) if rows else None,
    )


@router.put("/sleep/{day}", response_model=SleepOut, summary="Log the night that ended on this day")
async def put_sleep(day: date, payload: SleepIn, principal: CurrentPrincipal, db: DbSession) -> SleepOut:
    row = await health.set_sleep(db, user_id=principal.user_id, tz=_tz(principal), day=day, payload=payload)
    return sleep_out(row)


@router.delete("/sleep/{day}", response_model=OkOut, summary="Remove a sleep entry")
async def delete_sleep(day: date, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await health.delete_sleep(db, user_id=principal.user_id, day=day)
    return OkOut()
