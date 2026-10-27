"""Web push (stage 7): VAPID config, browser subscriptions, test notification.

Every endpoint needs a paired device: a subscription is bound to the device
that registered it, so revoking a phone also stops its notifications.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import push_subscription_out
from app.core.errors import RateLimited
from app.core.rate_limit import limiter
from app.core.tz import utcnow
from app.schemas.notifications import (
    PushConfigOut,
    PushSubscriptionIn,
    PushSubscriptionListOut,
    PushSubscriptionOut,
    PushTestOut,
    PushUnsubscribeIn,
)
from app.services import notifications, push

router = APIRouter(prefix="/push", tags=["push"], dependencies=[Depends(require_device)])

# a person enables push a few times a year; these only stop a runaway client
WRITES_PER_MINUTE = 30
TESTS_PER_MINUTE = 5


async def _write_limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"push:{principal.user_id}", WRITES_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many push changes, please slow down")


async def _test_limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"push-test:{principal.user_id}", TESTS_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many test notifications, please wait a moment")


Write = [Depends(_write_limit)]


@router.get("/config", response_model=PushConfigOut, summary="Is web push configured? The VAPID public key")
async def config() -> PushConfigOut:
    return PushConfigOut(enabled=push.is_configured(), vapid_public_key=push.public_key())


@router.post(
    "/subscriptions",
    response_model=PushSubscriptionOut,
    status_code=201,
    summary="Register this browser's push subscription (upsert by endpoint)",
    dependencies=Write,
)
async def subscribe(payload: PushSubscriptionIn, principal: CurrentPrincipal, db: DbSession) -> PushSubscriptionOut:
    assert principal.device is not None  # require_device
    sub = await push.upsert_subscription(db, user_id=principal.user_id, device=principal.device, payload=payload)
    await notifications.on_subscriptions_changed(db, principal.user_id)
    return push_subscription_out(sub, principal.device, current_device_id=principal.device.id)


@router.get("/subscriptions", response_model=PushSubscriptionListOut, summary="Your push subscriptions (no keys)")
async def list_subscriptions(principal: CurrentPrincipal, db: DbSession) -> PushSubscriptionListOut:
    current = principal.device.id if principal.device else None
    rows = await push.list_subscriptions(db, principal.user_id)
    return PushSubscriptionListOut(
        items=[push_subscription_out(sub, device, current_device_id=current) for sub, device in rows]
    )


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=204,
    response_class=Response,
    summary="Remove a push subscription (e.g. of a lost phone)",
    dependencies=Write,
)
async def delete_subscription(subscription_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> Response:
    await push.delete_subscription(db, user_id=principal.user_id, subscription_id=subscription_id)
    await notifications.on_subscriptions_changed(db, principal.user_id)
    return Response(status_code=204)


@router.post(
    "/unsubscribe",
    status_code=204,
    response_class=Response,
    summary="Forget the subscription this browser knows (by endpoint; unknown ones are ignored)",
    dependencies=Write,
)
async def unsubscribe(payload: PushUnsubscribeIn, principal: CurrentPrincipal, db: DbSession) -> Response:
    if await push.unsubscribe_endpoint(db, user_id=principal.user_id, endpoint=payload.endpoint):
        await notifications.on_subscriptions_changed(db, principal.user_id)
    return Response(status_code=204)


@router.post(
    "/test",
    response_model=PushTestOut,
    summary="Send a test notification to your devices now (ignores quiet hours and categories)",
    dependencies=[Depends(_test_limit)],
)
async def test(principal: CurrentPrincipal, db: DbSession) -> PushTestOut:
    """Answers within the push sender's deadline (``SEND_DEADLINE_SECONDS``) whatever the
    push services do; nothing stays locked meanwhile."""
    push.vapid()  # 503 push_disabled when the server has no (readable) VAPID keys
    now = utcnow()
    message = notifications.test_message(notifications.language_of(principal.settings_row))
    subscriptions = await push.active_subscriptions(db, principal.user_id, now=now)
    await db.commit()  # e.g. an expired subscription switched off above: not held during the POSTs
    report = await push.deliver(db, subscriptions, message, now=now)
    await notifications.log_test(db, user_id=principal.user_id, message=message, report=report, now=now)
    return PushTestOut(sent=report.sent, failed=report.failed, deactivated=report.deactivated)
