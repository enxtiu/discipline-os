"""Notification preferences and history (stage 7).

Preferences live in ``user_settings.notification_prefs`` (unknown legacy keys
are kept); quiet hours stay in the settings row (``PATCH /settings``) and are
returned here too, so the settings screen needs one request.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import notification_out, notification_prefs_out
from app.core.errors import RateLimited
from app.core.rate_limit import limiter
from app.schemas.notifications import NotificationListOut, NotificationPrefsIn, NotificationPrefsOut
from app.services import notifications, push
from app.services.settings_service import get_or_create_settings

router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_device)])

# every toggle and time field saves on its own; this only stops a runaway client
WRITES_PER_MINUTE = 60


async def _write_limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"notifications:{principal.user_id}", WRITES_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many changes, please slow down")


async def _prefs_response(principal: CurrentPrincipal, db: DbSession) -> NotificationPrefsOut:
    row = await get_or_create_settings(db, principal.user)
    return notification_prefs_out(
        notifications.read_prefs(row.notification_prefs),
        row,
        push_enabled=push.is_configured(),
        active_subscriptions=await push.count_active(db, principal.user_id),
    )


@router.get("/prefs", response_model=NotificationPrefsOut, summary="Categories, their times, quiet hours")
async def get_prefs(principal: CurrentPrincipal, db: DbSession) -> NotificationPrefsOut:
    return await _prefs_response(principal, db)


@router.put(
    "/prefs",
    response_model=NotificationPrefsOut,
    summary="Update categories and times (fields not sent keep their value)",
    dependencies=[Depends(_write_limit)],
)
async def put_prefs(payload: NotificationPrefsIn, principal: CurrentPrincipal, db: DbSession) -> NotificationPrefsOut:
    row = await get_or_create_settings(db, principal.user)
    row.notification_prefs = notifications.merge_prefs(row.notification_prefs, payload)
    await db.flush()
    await notifications.on_prefs_changed(db, principal.user_id)
    return await _prefs_response(principal, db)


@router.get("", response_model=NotificationListOut, summary="Recent notifications (sent, deferred, suppressed, failed)")
async def list_notifications(
    principal: CurrentPrincipal, db: DbSession, limit: int = Query(default=50, ge=1, le=200)
) -> NotificationListOut:
    rows = await notifications.history(db, principal.user_id, limit=limit)
    return NotificationListOut(items=[notification_out(row) for row in rows])
