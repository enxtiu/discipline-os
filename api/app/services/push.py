"""Web Push (stage 7): VAPID keys, subscriptions and the sender.

``pywebpush`` does the RFC 8291 payload encryption (``aes128gcm``) and the
RFC 8292 VAPID signature. It is synchronous (``requests``), so every send runs
in a worker thread. The push service's answer decides what happens next:

* 2xx              → delivered: ``last_success_at``, ``failure_count`` reset
* 404 / 410        → the browser dropped the subscription → deactivated
* 400 / 413 / 4xx  → permanent failure of *this* message (401/403 = VAPID trouble)
* 429 / 5xx / net  → transient: the dispatcher retries on its next run

Keys and full endpoints never reach the logs — only the endpoint's host.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Literal

import requests
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ServiceUnavailable
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.tz import utcnow
from app.models import Device, PushSubscription
from app.schemas.notifications import PushSubscriptionIn, endpoint_host

log = get_logger(__name__)

SEND_TIMEOUT_SECONDS = 10
MAX_PAYLOAD_BYTES = 3072  # the push services accept ~4 KB encrypted
REMINDER_TTL_SECONDS = 12 * 3600
TEST_TTL_SECONDS = 60
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 400
_SPACES = re.compile(r"[ \t\r\n\f\v]+")

Urgency = Literal["very-low", "low", "normal", "high"]


class PushNotConfigured(ServiceUnavailable):
    """VAPID keys are missing (or unreadable) on the server."""

    code = "push_disabled"


# --------------------------------------------------------------------------- #
# VAPID
# --------------------------------------------------------------------------- #
def is_configured() -> bool:
    """Same rule as ``feature_flags()["push_notifications"]`` (the UI's source of truth)."""
    settings = get_settings()
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def public_key() -> str | None:
    return get_settings().vapid_public_key.strip() if is_configured() else None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@lru_cache(maxsize=4)
def _load_vapid(private_key: str) -> Vapid02:
    # .env keeps the PEM on one line with "\n" escaped (python -m app.cli vapid-keys)
    text = private_key.strip().strip("\"'").replace("\\n", "\n")
    if "-----BEGIN" in text:
        return Vapid02.from_pem(text.encode())
    return Vapid02.from_string(text)  # raw 32-byte key or DER, base64url


@lru_cache(maxsize=4)
def _check_pair(private_key: str, configured_public: str) -> None:
    """Log once when the public key the browsers subscribe with is not the private key's."""
    derived = _b64url(
        _load_vapid(private_key).public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    if derived != configured_public.strip():
        log.error(
            "vapid_key_mismatch",
            hint="VAPID_PUBLIC_KEY does not belong to VAPID_PRIVATE_KEY — regenerate both with "
            "`python -m app.cli vapid-keys`; push services will reject every message",
        )


def vapid() -> Vapid02:
    if not is_configured():
        raise PushNotConfigured("Web push is not configured on this server (VAPID keys)")
    settings = get_settings()
    try:
        key = _load_vapid(settings.vapid_private_key)
        _check_pair(settings.vapid_private_key, settings.vapid_public_key)
    except Exception as exc:
        log.error("vapid_private_key_invalid", error=type(exc).__name__)
        raise PushNotConfigured("VAPID_PRIVATE_KEY cannot be read") from exc
    return key


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def clip_text(text: str, limit: int) -> str:
    """Single-line text of at most ``limit`` characters (an ellipsis marks a cut).
    Only ordinary whitespace is collapsed: the no-break space in "1 800 мл" stays."""
    text = _SPACES.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(slots=True)
class PushMessage:
    """What ``web/public/sw-push.js`` shows: ``{title, body, url, tag, renotify}`` (+ kind, id)."""

    title: str
    body: str
    url: str
    tag: str
    kind: str
    ref_id: str | None = None
    renotify: bool = True
    ttl: int = REMINDER_TTL_SECONDS
    urgency: Urgency = "normal"

    @property
    def topic(self) -> str:
        """RFC 8030 ``Topic`` (≤ 32 base64url chars): an undelivered older message with the
        same topic is replaced by the push service while the phone is offline."""
        return _b64url(hashlib.sha256(self.tag.encode()).digest())[:32]

    def data(self) -> dict[str, Any]:
        return {
            "title": clip_text(self.title, MAX_TITLE_CHARS),
            "body": clip_text(self.body, MAX_BODY_CHARS),
            "url": self.url,
            "tag": self.tag,
            "renotify": self.renotify,
            "kind": self.kind,
            "id": self.ref_id,
        }

    def payload(self) -> str:
        encoded = json.dumps(self.data(), ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES:  # pragma: no cover - the clips above keep it far below
            raise ValueError("push payload exceeds 3 KB")
        return encoded


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
class Outcome(str, Enum):
    SENT = "sent"
    GONE = "gone"  # 404/410: subscription expired or unsubscribed
    PERMANENT = "permanent"  # this message will never be accepted
    TRANSIENT = "transient"  # try again later


def classify(status: int | None) -> Outcome:
    if status is None:
        return Outcome.TRANSIENT
    if 200 <= status < 300:
        return Outcome.SENT
    if status in (404, 410):
        return Outcome.GONE
    if status == 429 or status >= 500:
        return Outcome.TRANSIENT
    return Outcome.PERMANENT


@dataclass(slots=True)
class SendResult:
    subscription_id: uuid.UUID
    device_id: uuid.UUID | None
    outcome: Outcome
    status_code: int | None = None
    error: str | None = None


@dataclass(slots=True)
class DeliveryReport:
    results: list[SendResult] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for result in self.results if result.outcome is outcome)

    @property
    def sent(self) -> int:
        return self.count(Outcome.SENT)

    @property
    def deactivated(self) -> int:
        return self.count(Outcome.GONE)

    @property
    def transient(self) -> int:
        return self.count(Outcome.TRANSIENT)

    @property
    def failed(self) -> int:
        return len(self.results) - self.sent

    @property
    def first_success(self) -> SendResult | None:
        return next((result for result in self.results if result.outcome is Outcome.SENT), None)

    def summary(self) -> str:
        """Short, endpoint-free description of what went wrong (for logs and history)."""
        errors = sorted({result.error for result in self.results if result.error})
        return ",".join(errors)[:200] or "no_result"


# tests swap in a ``requests.Session`` double; production uses one session per worker thread
_transport_override: Any = None
_local = threading.local()


def _session() -> Any:
    if _transport_override is not None:
        return _transport_override
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        _local.session = session
    return session


def _send_sync(
    info: dict[str, Any], data: str, *, ttl: int, headers: dict[str, str], key: Vapid02, subject: str
) -> tuple[Outcome, int | None, str | None]:
    """One encrypted POST to the push service (runs in a worker thread)."""
    try:
        response = webpush(
            info,
            data=data,
            vapid_private_key=key,
            vapid_claims={"sub": subject},  # fresh dict: pywebpush fills in aud/exp per endpoint
            ttl=ttl,
            headers=dict(headers),
            timeout=SEND_TIMEOUT_SECONDS,
            requests_session=_session(),
        )
        status = getattr(response, "status_code", 201)
        return classify(status), status, None
    except WebPushException as exc:
        status = exc.status_code
        if status is None:  # rejected before sending (malformed subscription data)
            return Outcome.PERMANENT, None, "invalid_subscription"
        return classify(status), status, f"http_{status}"
    except requests.RequestException as exc:  # DNS, refused, timeout, TLS
        return Outcome.TRANSIENT, None, f"network:{type(exc).__name__}"
    except Exception as exc:  # broken keys etc. — never let one subscription stop the batch
        return Outcome.PERMANENT, None, f"send_error:{type(exc).__name__}"


def _record(sub: PushSubscription, result: SendResult, now: datetime) -> None:
    if result.outcome is Outcome.SENT:
        sub.last_success_at = now
        sub.failure_count = 0
        sub.last_error = None
        return
    sub.failure_count = min((sub.failure_count or 0) + 1, 32_000)
    sub.last_failure_at = now
    sub.last_error = result.error
    if result.outcome is Outcome.GONE:
        sub.is_active = False


async def deliver(
    db: AsyncSession, subscriptions: list[PushSubscription], message: PushMessage, *, now: datetime | None = None
) -> DeliveryReport:
    """Send one message to every given subscription (concurrently) and book the results."""
    report = DeliveryReport()
    if not subscriptions:
        return report
    key = vapid()
    subject = get_settings().vapid_subject
    data = message.payload()
    headers = {"Urgency": message.urgency, "Topic": message.topic}

    async def one(sub: PushSubscription) -> SendResult:
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        outcome, status, error = await asyncio.to_thread(
            _send_sync, info, data, ttl=message.ttl, headers=headers, key=key, subject=subject
        )
        return SendResult(sub.id, sub.device_id, outcome, status, error)

    report.results = list(await asyncio.gather(*(one(sub) for sub in subscriptions)))
    now = now or utcnow()
    gone_devices: set[uuid.UUID] = set()
    for sub, result in zip(subscriptions, report.results, strict=True):
        _record(sub, result, now)
        if result.outcome is Outcome.GONE and sub.device_id is not None:
            gone_devices.add(sub.device_id)
        log.info(
            "push_result",
            subscription_id=str(sub.id),
            host=endpoint_host(sub.endpoint),
            kind=message.kind,
            outcome=result.outcome.value,
            status=result.status_code,
            error=result.error,
        )
    await db.flush()
    for device_id in gone_devices:
        await sync_device_flag(db, device_id)
    return report


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #
def _active_filter(user_id: uuid.UUID):
    return (
        PushSubscription.user_id == user_id,
        PushSubscription.is_active.is_(True),
        Device.user_id == user_id,
        Device.is_active.is_(True),  # a revoked phone must not keep showing task titles
    )


async def active_subscriptions(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> list[PushSubscription]:
    """Active subscriptions of the user's active devices; expired ones are switched off."""
    now = now or utcnow()
    rows = list(
        (
            await db.scalars(
                select(PushSubscription)
                .join(Device, Device.id == PushSubscription.device_id)
                .where(*_active_filter(user_id))
                .order_by(PushSubscription.created_at)
            )
        ).all()
    )
    live = []
    for sub in rows:
        if sub.expiration_time is not None and sub.expiration_time <= now:
            sub.is_active = False
            sub.last_error = "expired"
            await db.flush()
            await sync_device_flag(db, sub.device_id)
            continue
        live.append(sub)
    return live


async def count_active(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(PushSubscription)
            .join(Device, Device.id == PushSubscription.device_id)
            .where(*_active_filter(user_id))
        )
        or 0
    )


async def users_with_push(db: AsyncSession) -> list[uuid.UUID]:
    """Users who have at least one active subscription on an active device."""
    rows = await db.scalars(
        select(PushSubscription.user_id)
        .join(Device, Device.id == PushSubscription.device_id)
        .where(
            PushSubscription.is_active.is_(True),
            Device.is_active.is_(True),
            Device.user_id == PushSubscription.user_id,
        )
        .distinct()
    )
    return list(rows.all())


async def sync_device_flag(db: AsyncSession, device_id: uuid.UUID | None) -> None:
    """``devices.push_enabled`` = the device has an active subscription."""
    if device_id is None:
        return
    active = await db.scalar(
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.device_id == device_id, PushSubscription.is_active.is_(True))
    )
    await db.execute(update(Device).where(Device.id == device_id).values(push_enabled=bool(active)))


async def upsert_subscription(
    db: AsyncSession, *, user_id: uuid.UUID, device: Device, payload: PushSubscriptionIn
) -> PushSubscription:
    """Register (or refresh) the browser's subscription for the calling device.

    Upsert by endpoint: re-subscribing the same browser updates the keys,
    reactivates the row and re-binds it to this device. An endpoint that belongs
    to another user is refused (``409 subscription_conflict``) — the client then
    drops its browser subscription and creates a new one.
    """
    if not is_configured():
        raise PushNotConfigured("Web push is not configured on this server (VAPID keys)")
    now = utcnow()
    previous = (
        await db.execute(
            select(PushSubscription.user_id, PushSubscription.device_id).where(
                PushSubscription.endpoint == payload.endpoint
            )
        )
    ).first()
    values = {
        "device_id": device.id,
        "p256dh": payload.keys.p256dh,
        "auth": payload.keys.auth,
        "expiration_time": payload.expiration_time,
        "user_agent": (payload.user_agent or "")[:255] or None,
        "is_active": True,
        "failure_count": 0,
        "last_error": None,
        "updated_at": now,
    }
    stmt = pg_insert(PushSubscription).values(
        id=new_id(), user_id=user_id, endpoint=payload.endpoint, created_at=now, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PushSubscription.endpoint],
        set_=values,
        where=PushSubscription.user_id == user_id,
    ).returning(PushSubscription.id)
    row_id = (await db.execute(stmt)).scalar_one_or_none()
    if row_id is None:
        log.warning("push_subscription_conflict", user_id=str(user_id), host=endpoint_host(payload.endpoint))
        raise Conflict("This browser subscription belongs to another account", code="subscription_conflict")
    sub = await db.get(PushSubscription, row_id, populate_existing=True)
    assert sub is not None
    await sync_device_flag(db, device.id)
    if previous is not None and previous.device_id not in (None, device.id):
        await sync_device_flag(db, previous.device_id)
    log.info(
        "push_subscribed",
        user_id=str(user_id),
        device_id=str(device.id),
        host=endpoint_host(payload.endpoint),
        renewed=previous is not None,
    )
    return sub


async def list_subscriptions(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[PushSubscription, Device | None]]:
    rows = await db.execute(
        select(PushSubscription, Device)
        .outerjoin(Device, Device.id == PushSubscription.device_id)
        .where(PushSubscription.user_id == user_id)
        .order_by(PushSubscription.is_active.desc(), PushSubscription.updated_at.desc())
    )
    return [(sub, device) for sub, device in rows.all()]


async def _delete(db: AsyncSession, sub: PushSubscription) -> None:
    device_id, user_id, host = sub.device_id, sub.user_id, endpoint_host(sub.endpoint)
    await db.delete(sub)
    await db.flush()
    await sync_device_flag(db, device_id)
    log.info("push_unsubscribed", user_id=str(user_id), host=host)


async def delete_subscription(db: AsyncSession, *, user_id: uuid.UUID, subscription_id: uuid.UUID) -> None:
    sub = await db.scalar(
        select(PushSubscription).where(PushSubscription.id == subscription_id, PushSubscription.user_id == user_id)
    )
    if sub is None:
        raise NotFound("Push subscription not found")
    await _delete(db, sub)


async def unsubscribe_endpoint(db: AsyncSession, *, user_id: uuid.UUID, endpoint: str) -> bool:
    """Forget the browser's subscription; unknown endpoints (or someone else's) change nothing."""
    sub = await db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint == endpoint.strip(), PushSubscription.user_id == user_id
        )
    )
    if sub is None:
        return False
    await _delete(db, sub)
    return True
