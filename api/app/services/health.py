"""Health (stage 5): daily steps / active energy, manual weight and sleep.

* **Ingest is an upsert by local day.** The iOS Shortcut may run several times a
  day; each run overwrites that day's numbers (fields it does not send stay).
  A manual entry in the app is the same upsert with ``source=manual``.
* **Days are the user's calendar days.** A date without time is taken as is; a
  timestamp is converted to the user's timezone first.
* **Calories from steps are informational and never food budget.** Active
  energy from Apple Health is used when the Shortcut sends it; otherwise a net
  walking estimate (0.5 kcal per kg per km) from the step count.
* **Weight keeps the profile current.** The newest weigh-in becomes
  ``settings.weight_kg`` and the targets are recomputed (stage-1 service).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, Unprocessable
from app.core.logging import get_logger
from app.core.tz import ensure_utc, get_zone, local_day, today_in, utcnow
from app.models import UserSettings
from app.models.training import BodyMetric, HealthDaily, HealthSource, SleepLog
from app.services.settings_service import sync_profile_weight

log = get_logger(__name__)

PAST_DAYS = 400
DEFAULT_STEP_M = 0.75
NET_WALK_KCAL_PER_KG_KM = 0.5
MAX_SLEEP_MIN = 20 * 60
INGESTED = (HealthSource.SHORTCUT, HealthSource.APPLE_HEALTH, HealthSource.IMPORT)


@dataclass(slots=True)
class DayView:
    day: date
    row: HealthDaily | None
    target_steps: int
    steps_kcal: float | None
    estimated: bool


# --------------------------------------------------------------------------- #
# Days and bounds
# --------------------------------------------------------------------------- #
def resolve_day(value: date | datetime | None, tz: str | None) -> date:
    """The user's calendar day for a payload value (default: today)."""
    if value is None:
        day = today_in(tz)
    elif isinstance(value, datetime):
        day = local_day(value, tz) if value.tzinfo is not None else value.date()
    else:
        day = value
    check_day(day, tz)
    return day


def check_day(day: date, tz: str | None) -> None:
    today = today_in(tz)
    if day > today + timedelta(days=1) or day < today - timedelta(days=PAST_DAYS):
        raise Unprocessable("The day must be within the last year", code="health_day_out_of_range")


def _local_instant(value: datetime, tz: str | None) -> datetime:
    return ensure_utc(value if value.tzinfo is not None else value.replace(tzinfo=get_zone(tz)))


# --------------------------------------------------------------------------- #
# Steps / active energy
# --------------------------------------------------------------------------- #
_INT_FIELDS = ("steps", "distance_m", "floors", "resting_hr", "hrv_ms")
_KCAL_FIELDS = ("active_kcal", "basal_kcal")


def _values(payload: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _INT_FIELDS:
        value = getattr(payload, key, None)
        if value is not None:
            out[key] = round(value)
    for key in _KCAL_FIELDS:
        value = getattr(payload, key, None)
        if value is not None:
            out[key] = Decimal(str(round(float(value), 1)))
    return out


async def upsert_day(
    db: AsyncSession, *, user_id: uuid.UUID, day: date, values: dict[str, Any], source: HealthSource, raw: dict
) -> tuple[HealthDaily, bool]:
    """Insert or overwrite one day; returns (row, created)."""
    now = utcnow()
    changes = {**values, "source": source, "ingested_at": now, "raw_payload": raw, "updated_at": now}
    stmt = (
        pg_insert(HealthDaily)
        .values(id=uuid.uuid4(), user_id=user_id, local_day=day, created_at=now, **{"steps": 0, **changes})
        .on_conflict_do_update(constraint="uq_health_daily_user_day", set_=changes)
        .returning(HealthDaily.id, literal_column("(xmax = 0)").label("created"))
    )
    row_id, created = (await db.execute(stmt)).one()
    row = await db.get(HealthDaily, row_id, populate_existing=True)
    assert row is not None
    log.info("health_day_upserted", day=day.isoformat(), source=source.value, created=bool(created))
    return row, bool(created)


async def ingest_day(db: AsyncSession, *, user_id: uuid.UUID, tz: str | None, payload) -> tuple[HealthDaily, bool]:
    day = resolve_day(payload.date, tz)
    raw = payload.model_dump(mode="json", exclude_none=True)
    return await upsert_day(
        db, user_id=user_id, day=day, values=_values(payload), source=HealthSource(payload.source), raw=raw
    )


async def set_manual_steps(db: AsyncSession, *, user_id: uuid.UUID, tz: str | None, day: date, payload) -> HealthDaily:
    check_day(day, tz)
    values: dict[str, Any] = {"steps": payload.steps}
    if payload.distance_m is not None:
        values["distance_m"] = payload.distance_m
    if payload.active_kcal is not None:
        values["active_kcal"] = Decimal(str(round(payload.active_kcal, 1)))
    row, _created = await upsert_day(
        db, user_id=user_id, day=day, values=values, source=HealthSource.MANUAL, raw={"manual": True}
    )
    return row


async def _settings(db: AsyncSession, user_id: uuid.UUID) -> UserSettings | None:
    return await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))


async def weight_on(db: AsyncSession, user_id: uuid.UUID, day: date) -> float | None:
    measured = await db.scalar(
        select(BodyMetric.weight_kg)
        .where(BodyMetric.user_id == user_id, BodyMetric.measured_on <= day, BodyMetric.weight_kg.is_not(None))
        .order_by(BodyMetric.measured_on.desc())
        .limit(1)
    )
    if measured is not None:
        return float(measured)
    settings = await _settings(db, user_id)
    return float(settings.weight_kg) if settings and settings.weight_kg is not None else None


def steps_kcal(row: HealthDaily | None, weight_kg: float | None, height_cm: float | None) -> tuple[float | None, bool]:
    """(kcal, estimated). Health's active energy wins; otherwise a net walking estimate."""
    if row is None:
        return None, False
    if row.active_kcal is not None:
        return float(row.active_kcal), False
    if not row.steps or not weight_kg:
        return None, False
    stride = (float(height_cm) * 0.415 / 100) if height_cm else DEFAULT_STEP_M
    km = (row.distance_m / 1000) if row.distance_m else row.steps * stride / 1000
    return round(NET_WALK_KCAL_PER_KG_KM * weight_kg * km), True


async def day_views(db: AsyncSession, *, user_id: uuid.UUID, start: date, end: date) -> list[DayView]:
    rows = (
        await db.scalars(
            select(HealthDaily).where(HealthDaily.user_id == user_id, HealthDaily.local_day.between(start, end))
        )
    ).all()
    by_day = {row.local_day: row for row in rows}
    settings = await _settings(db, user_id)
    target = settings.target_steps if settings else 8000
    height = float(settings.height_cm) if settings and settings.height_cm is not None else None
    weight = await weight_on(db, user_id, end)
    views = []
    cursor = start
    while cursor <= end:
        row = by_day.get(cursor)
        kcal, estimated = steps_kcal(row, weight, height)
        views.append(DayView(day=cursor, row=row, target_steps=target, steps_kcal=kcal, estimated=estimated))
        cursor += timedelta(days=1)
    return views


async def last_sync_at(db: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    return await db.scalar(
        select(func.max(HealthDaily.ingested_at)).where(
            HealthDaily.user_id == user_id, HealthDaily.source.in_(INGESTED)
        )
    )


# --------------------------------------------------------------------------- #
# Weight
# --------------------------------------------------------------------------- #
async def set_weight(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    tz: str | None,
    day: date,
    weight_kg: float,
    body_fat_pct: float | None = None,
    waist_cm: float | None = None,
    note: str | None = None,
    source: str = "manual",
) -> BodyMetric:
    check_day(day, tz)
    now = utcnow()
    values = {
        "weight_kg": Decimal(str(round(weight_kg, 2))),
        "body_fat_pct": Decimal(str(round(body_fat_pct, 1))) if body_fat_pct is not None else None,
        "waist_cm": Decimal(str(round(waist_cm, 1))) if waist_cm is not None else None,
        "note": note,
        "source": source,
        "measured_at": now,
        "updated_at": now,
    }
    stmt = (
        pg_insert(BodyMetric)
        .values(id=uuid.uuid4(), client_uuid=uuid.uuid4(), user_id=user_id, measured_on=day, created_at=now, **values)
        .on_conflict_do_update(constraint="uq_body_metrics_user_day", set_=values)
        .returning(BodyMetric.id)
    )
    row_id = (await db.execute(stmt)).scalar_one()
    await _sync_profile(db, user_id)
    row = await db.get(BodyMetric, row_id, populate_existing=True)
    assert row is not None
    return row


async def delete_weight(db: AsyncSession, *, user_id: uuid.UUID, day: date) -> None:
    result = await db.execute(delete(BodyMetric).where(BodyMetric.user_id == user_id, BodyMetric.measured_on == day))
    if not result.rowcount:
        raise NotFound("No weigh-in on this day", code="weight_not_found")
    await _sync_profile(db, user_id)


async def _sync_profile(db: AsyncSession, user_id: uuid.UUID) -> None:
    """The newest weigh-in is the profile weight; targets follow (stage-1 rules)."""
    settings = await _settings(db, user_id)
    if settings is not None:
        await db.flush()
        await sync_profile_weight(db, settings, None)


async def weight_series(db: AsyncSession, *, user_id: uuid.UUID, start: date, end: date) -> list[BodyMetric]:
    rows = await db.scalars(
        select(BodyMetric)
        .where(
            BodyMetric.user_id == user_id,
            BodyMetric.measured_on.between(start, end),
            BodyMetric.weight_kg.is_not(None),
        )
        .order_by(BodyMetric.measured_on)
    )
    return list(rows.all())


async def latest_weight(db: AsyncSession, user_id: uuid.UUID) -> BodyMetric | None:
    return await db.scalar(
        select(BodyMetric)
        .where(BodyMetric.user_id == user_id, BodyMetric.weight_kg.is_not(None))
        .order_by(BodyMetric.measured_on.desc())
        .limit(1)
    )


async def weight_before(db: AsyncSession, user_id: uuid.UUID, day: date) -> BodyMetric | None:
    """The weigh-in on or just before ``day``."""
    return await db.scalar(
        select(BodyMetric)
        .where(BodyMetric.user_id == user_id, BodyMetric.weight_kg.is_not(None), BodyMetric.measured_on <= day)
        .order_by(BodyMetric.measured_on.desc())
        .limit(1)
    )


def trend(rows: list[BodyMetric]) -> float | None:
    """Average of the weigh-ins in the 7 days ending at the newest one."""
    if not rows:
        return None
    newest = rows[-1].measured_on
    window = [float(row.weight_kg) for row in rows if row.measured_on > newest - timedelta(days=7)]
    return round(sum(window) / len(window), 2) if window else None


# --------------------------------------------------------------------------- #
# Sleep
# --------------------------------------------------------------------------- #
async def _upsert_sleep(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    day: date,
    bed_at: datetime,
    wake_at: datetime,
    quality: int | None,
    awakenings: int | None,
    note: str | None,
    source: str,
) -> SleepLog:
    minutes = int((wake_at - bed_at).total_seconds() // 60)
    if minutes <= 0 or minutes > MAX_SLEEP_MIN:
        raise Unprocessable("Sleep must last between a minute and 20 hours", code="sleep_duration_invalid")
    now = utcnow()
    values = {
        "bed_at": bed_at,
        "wake_at": wake_at,
        "duration_min": minutes,
        "quality": quality,
        "awakenings": awakenings,
        "note": note,
        "source": source,
        "updated_at": now,
    }
    stmt = (
        pg_insert(SleepLog)
        .values(id=uuid.uuid4(), client_uuid=uuid.uuid4(), user_id=user_id, local_day=day, created_at=now, **values)
        .on_conflict_do_update(constraint="uq_sleep_logs_user_day", set_=values)
        .returning(SleepLog.id)
    )
    row_id = (await db.execute(stmt)).scalar_one()
    row = await db.get(SleepLog, row_id, populate_existing=True)
    assert row is not None
    return row


async def set_sleep(db: AsyncSession, *, user_id: uuid.UUID, tz: str | None, day: date, payload) -> SleepLog:
    """Manual entry: wall-clock bed/wake times for the night ending on ``day``."""
    check_day(day, tz)
    zone = get_zone(tz)
    wake_local = datetime.combine(day, payload.wake_time).replace(tzinfo=zone)
    bed_day = day if payload.bed_time < payload.wake_time else day - timedelta(days=1)
    bed_local = datetime.combine(bed_day, payload.bed_time).replace(tzinfo=zone)
    return await _upsert_sleep(
        db,
        user_id=user_id,
        day=day,
        bed_at=ensure_utc(bed_local),
        wake_at=ensure_utc(wake_local),
        quality=payload.quality,
        awakenings=None,
        note=payload.note,
        source="manual",
    )


async def ingest_sleep(db: AsyncSession, *, user_id: uuid.UUID, tz: str | None, payload) -> SleepLog:
    bed_at = _local_instant(payload.bed_at, tz)
    wake_at = _local_instant(payload.wake_at, tz)
    day = local_day(wake_at, tz)
    check_day(day, tz)
    return await _upsert_sleep(
        db,
        user_id=user_id,
        day=day,
        bed_at=bed_at,
        wake_at=wake_at,
        quality=payload.quality,
        awakenings=payload.awakenings,
        note=None,
        source="shortcut",
    )


async def delete_sleep(db: AsyncSession, *, user_id: uuid.UUID, day: date) -> None:
    result = await db.execute(delete(SleepLog).where(SleepLog.user_id == user_id, SleepLog.local_day == day))
    if not result.rowcount:
        raise NotFound("No sleep entry for this day", code="sleep_not_found")


async def sleep_series(db: AsyncSession, *, user_id: uuid.UUID, start: date, end: date) -> list[SleepLog]:
    rows = await db.scalars(
        select(SleepLog)
        .where(SleepLog.user_id == user_id, SleepLog.local_day.between(start, end))
        .order_by(SleepLog.local_day.desc())
    )
    return list(rows.all())
