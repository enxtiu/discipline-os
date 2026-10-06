"""Ingest (stage 5): what an iOS Shortcut pushes with a scoped API token.

``POST /v1/ingest/health`` is idempotent: it upserts the user's local day, so a
Shortcut that runs at 13:00 and 22:00 simply overwrites the numbers. A token
needs the matching scope (``ingest:health``, ``ingest:weight``,
``ingest:sleep``); a paired device may call it too.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentPrincipal, DbSession, require_scope
from app.api.v1.presenters import health_day_out, sleep_out, weight_out
from app.core.errors import RateLimited
from app.core.rate_limit import limiter
from app.schemas.health import (
    BatchResultOut,
    HealthBatchIn,
    HealthDayIn,
    IngestResultOut,
    SleepIngestIn,
    SleepOut,
    WeightIngestIn,
    WeightOut,
)
from app.services import health

router = APIRouter(prefix="/ingest", tags=["ingest"])

PER_MINUTE = 60


async def _limit(principal: CurrentPrincipal) -> None:
    key = f"ingest:{principal.api_token.id if principal.api_token else principal.user_id}"
    allowed, retry_after = await limiter.hit(key, PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many ingest requests")


def _tz(principal: CurrentPrincipal) -> str | None:
    return principal.settings_row.timezone if principal.settings_row else None


async def _views(db, principal: CurrentPrincipal, days: set) -> list:
    """Day views for the given days with one range query (a batch spans at most 62 days)."""
    views = await health.day_views(db, user_id=principal.user_id, start=min(days), end=max(days))
    return [health_day_out(view) for view in views if view.day in days]


@router.post(
    "/health",
    response_model=IngestResultOut,
    summary="Steps / active energy of a day (iOS Shortcut; upsert by local day)",
    dependencies=[Depends(require_scope("ingest:health")), Depends(_limit)],
)
async def ingest_health(payload: HealthDayIn, principal: CurrentPrincipal, db: DbSession) -> IngestResultOut:
    row, created = await health.ingest_day(db, user_id=principal.user_id, tz=_tz(principal), payload=payload)
    return IngestResultOut(day=(await _views(db, principal, {row.local_day}))[0], created=created)


@router.post(
    "/health/batch",
    response_model=BatchResultOut,
    summary="Up to 62 days at once (backfill from a Health export)",
    dependencies=[Depends(require_scope("ingest:health")), Depends(_limit)],
)
async def ingest_health_batch(payload: HealthBatchIn, principal: CurrentPrincipal, db: DbSession) -> BatchResultOut:
    created = 0
    days = []
    for item in payload.days:
        row, was_created = await health.ingest_day(db, user_id=principal.user_id, tz=_tz(principal), payload=item)
        created += int(was_created)
        days.append(row.local_day)
    views = await _views(db, principal, set(days))
    return BatchResultOut(days=views, created=created, updated=len(payload.days) - created)


@router.post(
    "/weight",
    response_model=WeightOut,
    summary="A weigh-in (smart scale / Shortcut; one per local day)",
    dependencies=[Depends(require_scope("ingest:weight")), Depends(_limit)],
)
async def ingest_weight(payload: WeightIngestIn, principal: CurrentPrincipal, db: DbSession) -> WeightOut:
    tz = _tz(principal)
    row = await health.set_weight(
        db,
        user_id=principal.user_id,
        tz=tz,
        day=health.resolve_day(payload.date, tz),
        weight_kg=payload.weight_kg,
        body_fat_pct=payload.body_fat_pct,
        source="shortcut",
    )
    return weight_out(row)


@router.post(
    "/sleep",
    response_model=SleepOut,
    summary="A night of sleep (bed and wake time; stored on the wake day)",
    dependencies=[Depends(require_scope("ingest:sleep")), Depends(_limit)],
)
async def ingest_sleep(payload: SleepIngestIn, principal: CurrentPrincipal, db: DbSession) -> SleepOut:
    row = await health.ingest_sleep(db, user_id=principal.user_id, tz=_tz(principal), payload=payload)
    return sleep_out(row)
