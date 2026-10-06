"""Health (stage 5): what the app shows and the manual entries.

Steps can be typed in when the Shortcut is not set up; weight and sleep are
manual by design. Every write is an idempotent upsert by local day, so the
offline queue may replay it.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import (
    health_day_out,
    health_today_out,
    sleep_out,
    sleep_series_out,
    steps_series_out,
    weight_out,
    weight_series_out,
)
from app.core.errors import RateLimited
from app.core.rate_limit import limiter
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

# manual entries per user and minute: a person types a few a day; this only stops a runaway client
WRITES_PER_MINUTE = 60


async def _write_limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"health:{principal.user_id}", WRITES_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many health changes, please slow down")


Write = [Depends(_write_limit)]


def _tz(principal: CurrentPrincipal) -> str | None:
    return principal.settings_row.timezone if principal.settings_row else None


@router.get("/today", response_model=HealthTodayOut, summary="Steps, latest weight and last night's sleep")
async def today(principal: CurrentPrincipal, db: DbSession) -> HealthTodayOut:
    day = today_in(_tz(principal))
    view = (await health.day_views(db, user_id=principal.user_id, start=day, end=day))[0]
    latest = await health.latest_weight(db, principal.user_id)
    change = await health.weight_change(db, principal.user_id, latest, days=7, max_days=14)
    sleep = await health.sleep_series(db, user_id=principal.user_id, start=day - timedelta(days=1), end=day)
    last_sync = await health.last_sync_at(db, principal.user_id)
    return health_today_out(view, latest, change, sleep[0] if sleep else None, last_sync)


@router.get("/steps", response_model=StepsSeriesOut, summary="Steps per day (zero-filled), target and average")
async def steps(
    principal: CurrentPrincipal,
    db: DbSession,
    days: int = Query(default=14, ge=1, le=90),
    end: date | None = Query(default=None, description="last day of the window (default: today)"),
) -> StepsSeriesOut:
    if end is None:
        end = today_in(_tz(principal))
    else:
        health.check_day(end, _tz(principal))
    views = await health.day_views(db, user_id=principal.user_id, start=end - timedelta(days=days - 1), end=end)
    return steps_series_out(views)


@router.put(
    "/steps/{day}", response_model=HealthDayOut, summary="Type in a day's steps (manual fallback)", dependencies=Write
)
async def put_steps(day: date, payload: StepsIn, principal: CurrentPrincipal, db: DbSession) -> HealthDayOut:
    await health.set_manual_steps(db, user_id=principal.user_id, tz=_tz(principal), day=day, payload=payload)
    view = (await health.day_views(db, user_id=principal.user_id, start=day, end=day))[0]
    return health_day_out(view)


@router.delete("/steps/{day}", response_model=OkOut, summary="Clear a day's steps and activity", dependencies=Write)
async def delete_steps(day: date, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await health.delete_day(db, user_id=principal.user_id, day=day)
    return OkOut()


@router.get("/weight", response_model=WeightSeriesOut, summary="Weigh-ins, 7-day trend and 30-day change")
async def weight(
    principal: CurrentPrincipal, db: DbSession, days: int = Query(default=90, ge=7, le=400)
) -> WeightSeriesOut:
    end = today_in(_tz(principal))
    rows = await health.weight_series(db, user_id=principal.user_id, start=end - timedelta(days=days - 1), end=end)
    change = await health.weight_change(db, principal.user_id, rows[-1] if rows else None, days=30, max_days=45)
    return weight_series_out(rows, change)


@router.put(
    "/weight/{day}", response_model=WeightOut, summary="Log the weigh-in of a day (one per day)", dependencies=Write
)
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


@router.delete(
    "/weight/{day}", response_model=OkOut, summary="Remove a weigh-in (not the only one)", dependencies=Write
)
async def delete_weight(day: date, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await health.delete_weight(db, user_id=principal.user_id, day=day)
    return OkOut()


@router.get("/sleep", response_model=SleepSeriesOut, summary="Sleep entries (newest first) and the average")
async def sleep(
    principal: CurrentPrincipal, db: DbSession, days: int = Query(default=14, ge=1, le=90)
) -> SleepSeriesOut:
    end = today_in(_tz(principal))
    rows = await health.sleep_series(db, user_id=principal.user_id, start=end - timedelta(days=days - 1), end=end)
    return sleep_series_out(rows)


@router.put("/sleep/{day}", response_model=SleepOut, summary="Log the night that ended on this day", dependencies=Write)
async def put_sleep(day: date, payload: SleepIn, principal: CurrentPrincipal, db: DbSession) -> SleepOut:
    row = await health.set_sleep(db, user_id=principal.user_id, tz=_tz(principal), day=day, payload=payload)
    return sleep_out(row)


@router.delete("/sleep/{day}", response_model=OkOut, summary="Remove a sleep entry", dependencies=Write)
async def delete_sleep(day: date, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await health.delete_sleep(db, user_id=principal.user_id, day=day)
    return OkOut()
