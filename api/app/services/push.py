"""Web Push (stage 7): VAPID keys, subscriptions and the sender.

``pywebpush`` encrypts the payload (RFC 8291, ``aes128gcm``) and ``py_vapid`` signs
the RFC 8292 VAPID token; the POST goes out through ``httpx`` (like every other
outbound call of the API). What the sender enforces:

* **Where:** the endpoint passes ``check_endpoint`` again right before every POST —
  outside development only a known push service, ``https`` on port 443 (a stored row
  that no longer passes is switched off, ``last_error=endpoint_not_allowed``).
  Redirects are never followed: a 3xx is a permanent failure of that message.
* **How long:** each send has a total deadline (``SEND_DEADLINE_SECONDS``: connection,
  TLS, request and the answer's status line); the answer's body is never read. A send
  that misses the deadline is a transient failure (``timeout``), retried later.
* **When the database is touched:** ``send`` is network only; ``record`` books the
  results afterwards with plain ``UPDATE … WHERE id`` statements, so a subscription
  deleted while its push was in flight is simply skipped. Callers hold no row lock
  while the push services answer (see ``notifications.process_reminder``).
* **How many:** at most ``MAX_ACTIVE_SUBSCRIPTIONS`` active subscriptions per user; a
  new one switches off the least recently updated (registered, refreshed) beyond that.

The push service's answer decides what happens next:

* 2xx                → delivered: ``last_success_at``, ``failure_count`` reset
* 404 / 410          → the browser dropped the subscription → deactivated
* 401 / 403          → the push service refuses our VAPID signature (the keys changed
                       since the browser subscribed): ``VAPID_REJECTIONS_TO_RETIRE`` in a
                       row switch the subscription off (``last_error=vapid_rejected``), so
                       the settings screen asks to turn notifications on again there —
                       unless our own key pair is inconsistent (``vapid_key_mismatch``)
* 3xx, 400, 413, 4xx → permanent failure of *this* message
* 429 / 5xx / network / deadline → transient: the dispatcher retries on its next run

Keys and full endpoints never reach the logs — only the endpoint's host.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import ssl
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPusher, WebPushException
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ServiceUnavailable, Unprocessable
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.tz import ensure_utc, utcnow
from app.models import Device, PushSubscription
from app.schemas.notifications import PushSubscriptionIn, check_endpoint, endpoint_host

log = get_logger(__name__)

SEND_TIMEOUT_SECONDS = 10  # each connect / write / read
SEND_DEADLINE_SECONDS = 15.0  # one send in total, whatever the push service does
MAX_PAYLOAD_BYTES = 3072  # the push services accept ~4 KB encrypted
DEFAULT_TTL_SECONDS = 3600  # the dispatcher sets a tighter TTL per reminder (staleness, quiet hours)
TEST_TTL_SECONDS = 60
VAPID_TOKEN_SECONDS = 12 * 3600  # RFC 8292 allows up to 24 h
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 400
MAX_ACTIVE_SUBSCRIPTIONS = 10  # per user: bounds the fan-out of every message
VAPID_REJECTIONS_TO_RETIRE = 3  # 401/403 in a row (no other answer in between)
VAPID_REJECTED = "vapid_rejected"  # last_error of a retired subscription; "vapid_rejected:N" counts up to it
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
_REJECTION_STREAK = re.compile(rf"{VAPID_REJECTED}:([0-9]{{1,5}})")

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
def _derived_public(private_key: str) -> str:
    return _b64url(
        _load_vapid(private_key).public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


@lru_cache(maxsize=4)
def _check_pair(private_key: str, configured_public: str) -> None:
    """Log once when the public key the browsers subscribe with is not the private key's."""
    if _derived_public(private_key) != configured_public.strip():
        log.error(
            "vapid_key_mismatch",
            hint="VAPID_PUBLIC_KEY does not belong to VAPID_PRIVATE_KEY — regenerate both with "
            "`python -m app.cli vapid-keys`; push services will reject every message",
        )


def keys_match() -> bool:
    """Does ``VAPID_PUBLIC_KEY`` (what browsers subscribe with) belong to ``VAPID_PRIVATE_KEY``?"""
    settings = get_settings()
    try:
        return _derived_public(settings.vapid_private_key) == settings.vapid_public_key.strip()
    except Exception:
        return False


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


def iso_utc(moment: datetime) -> str:
    return ensure_utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class PushMessage:
    """What ``web/public/sw-push.js`` shows: ``{title, body, url, tag, renotify, kind, id}``,
    plus ``sent_at`` and ``expires_at`` (= ``sent_at`` + ``ttl``) once it is sent.

    ``ttl`` is how long the push service may keep the message for a phone that is offline
    (RFC 8030); the service worker drops a push that arrives after ``expires_at``.
    ``expires=False`` (the test notification) leaves ``expires_at`` out."""

    title: str
    body: str
    url: str
    tag: str
    kind: str
    ref_id: str | None = None
    renotify: bool = True
    ttl: int = DEFAULT_TTL_SECONDS
    urgency: Urgency = "normal"
    expires: bool = True

    @property
    def topic(self) -> str:
        """RFC 8030 ``Topic`` (≤ 32 base64url chars): an undelivered older message with the
        same topic is replaced by the push service while the phone is offline."""
        return _b64url(hashlib.sha256(self.tag.encode()).digest())[:32]

    def data(self, now: datetime | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": clip_text(self.title, MAX_TITLE_CHARS),
            "body": clip_text(self.body, MAX_BODY_CHARS),
            "url": self.url,
            "tag": self.tag,
            "renotify": self.renotify,
            "kind": self.kind,
            "id": self.ref_id,
        }
        if now is not None:
            sent = ensure_utc(now).replace(microsecond=0)
            data["sent_at"] = iso_utc(sent)
            if self.expires:
                data["expires_at"] = iso_utc(sent + timedelta(seconds=max(0, self.ttl)))
        return data

    def payload(self, now: datetime | None = None) -> str:
        encoded = json.dumps(self.data(now), ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES:  # pragma: no cover - the clips above keep it far below
            raise ValueError("push payload exceeds 3 KB")
        return encoded


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
class Outcome(str, Enum):
    SENT = "sent"
    GONE = "gone"  # 404/410 (or an endpoint the policy refuses): the subscription is switched off
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
    return Outcome.PERMANENT  # 3xx (never followed), 400, 401/403, 413, other 4xx


@dataclass(frozen=True, slots=True)
class Target:
    """What one send needs from a subscription — detached from the session, so nothing
    stays loaded or locked while the push service answers."""

    subscription_id: uuid.UUID
    device_id: uuid.UUID | None
    endpoint: str
    p256dh: str
    auth: str

    @classmethod
    def of(cls, sub: PushSubscription) -> Target:
        return cls(sub.id, sub.device_id, sub.endpoint, sub.p256dh, sub.auth)


@dataclass(slots=True)
class SendResult:
    subscription_id: uuid.UUID
    device_id: uuid.UUID | None
    outcome: Outcome
    status_code: int | None = None
    error: str | None = None
    booked: bool = False  # ``record`` found the subscription row (it was not deleted meanwhile)


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
        """The first delivery whose subscription still exists (history links to it)."""
        return next((r for r in self.results if r.outcome is Outcome.SENT and r.booked), None)

    def summary(self) -> str:
        """Short, endpoint-free description of what went wrong (for logs and history)."""
        errors = sorted({result.error for result in self.results if result.error})
        return ",".join(errors)[:200] or "no_result"


# tests swap in an ``httpx`` transport double; production uses the network
_transport_override: httpx.AsyncBaseTransport | None = None


@lru_cache(maxsize=1)
def _tls() -> ssl.SSLContext:
    """httpx's default verification, built once (a client per delivery is then ~1 ms)."""
    return httpx.create_ssl_context()


def policy_error(endpoint: str) -> str | None:
    """Why the server may not POST to ``endpoint`` under the current ENV (``None``: it may).
    See ``app.schemas.notifications.check_endpoint``."""
    settings = get_settings()
    try:
        check_endpoint(endpoint, allow_local=settings.env == "development", extra_hosts=settings.push_extra_hosts)
    except ValueError as exc:
        return str(exc)
    return None


def endpoint_allowed(endpoint: str) -> bool:
    return policy_error(endpoint) is None


def _audience(endpoint: str) -> str:
    """RFC 8292 ``aud``: the origin of the push resource (no default port)."""
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None and parts.port != {"https": 443, "http": 80}.get(parts.scheme):
        netloc += f":{parts.port}"
    return f"{parts.scheme}://{netloc}"


def _request(target: Target, data: bytes, ttl: int, headers: dict[str, str], key: Vapid02, subject: str):
    """Encrypt for this browser (RFC 8291) and sign for this push service (RFC 8292)."""
    info = {"endpoint": target.endpoint, "keys": {"p256dh": target.p256dh, "auth": target.auth}}
    body = WebPusher(info).encode(data, "aes128gcm")["body"]
    claims = {"sub": subject, "aud": _audience(target.endpoint), "exp": int(time.time()) + VAPID_TOKEN_SECONDS}
    return body, {**headers, "TTL": str(max(0, ttl)), "Content-Encoding": "aes128gcm", **key.sign(claims)}


async def _send_one(
    client: httpx.AsyncClient, target: Target, data: bytes, message: PushMessage, key: Vapid02, subject: str
) -> SendResult:
    def result(outcome: Outcome, status: int | None = None, error: str | None = None) -> SendResult:
        return SendResult(target.subscription_id, target.device_id, outcome, status, error)

    if not endpoint_allowed(target.endpoint):
        return result(Outcome.GONE, error="endpoint_not_allowed")
    try:
        body, headers = _request(
            target, data, message.ttl, {"Urgency": message.urgency, "Topic": message.topic}, key, subject
        )
    except WebPushException:  # the stored keys cannot be used
        return result(Outcome.PERMANENT, error="invalid_subscription")
    except Exception as exc:  # broken keys etc. — never let one subscription stop the batch
        return result(Outcome.PERMANENT, error=f"send_error:{type(exc).__name__}")
    try:
        async with asyncio.timeout(SEND_DEADLINE_SECONDS):
            async with client.stream("POST", target.endpoint, content=body, headers=headers) as response:
                status = response.status_code  # the body is never read
    except TimeoutError:
        return result(Outcome.TRANSIENT, error="timeout")
    except httpx.HTTPError as exc:  # DNS, refused, TLS, a per-operation timeout, protocol errors
        return result(Outcome.TRANSIENT, error=f"network:{type(exc).__name__}")
    except Exception as exc:
        return result(Outcome.PERMANENT, error=f"send_error:{type(exc).__name__}")
    outcome = classify(status)
    return result(outcome, status, None if outcome is Outcome.SENT else f"http_{status}")


async def send(targets: list[Target], message: PushMessage, *, now: datetime | None = None) -> DeliveryReport:
    """Send one message to every target at once. Network only: no database.

    Bounded by ``SEND_DEADLINE_SECONDS`` (the sends run side by side). Raises
    ``PushNotConfigured`` when the VAPID keys are missing or unreadable."""
    report = DeliveryReport()
    if not targets:
        return report
    key = vapid()
    subject = get_settings().vapid_subject
    data = message.payload(now or utcnow()).encode()
    async with httpx.AsyncClient(
        transport=_transport_override,
        verify=_tls(),
        timeout=httpx.Timeout(SEND_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        report.results = list(
            await asyncio.gather(*(_send_one(client, target, data, message, key, subject) for target in targets))
        )
    for target, result in zip(targets, report.results, strict=True):
        log.info(
            "push_result",
            subscription_id=str(target.subscription_id),
            host=endpoint_host(target.endpoint),
            kind=message.kind,
            outcome=result.outcome.value,
            status=result.status_code,
            error=result.error,
        )
    return report


def rejection_streak(last_error: str | None) -> int:
    """How many 401/403 answers in a row ``last_error`` records (``vapid_rejected:N``)."""
    match = _REJECTION_STREAK.fullmatch(last_error or "")
    return int(match.group(1)) if match else 0


def next_state(
    failure_count: int, last_error: str | None, result: SendResult, now: datetime, *, count_rejections: bool = True
) -> dict[str, Any]:
    """The subscription's columns after this result."""
    if result.outcome is Outcome.SENT:
        return {"last_success_at": now, "failure_count": 0, "last_error": None}
    values: dict[str, Any] = {
        "failure_count": min((failure_count or 0) + 1, 32_000),
        "last_failure_at": now,
        "last_error": result.error,
    }
    if result.outcome is Outcome.GONE:
        values["is_active"] = False
    elif count_rejections and result.status_code in (401, 403):
        streak = rejection_streak(last_error) + 1
        if streak >= VAPID_REJECTIONS_TO_RETIRE:
            values.update(is_active=False, last_error=VAPID_REJECTED)
        else:
            values["last_error"] = f"{VAPID_REJECTED}:{streak}"
    return values


async def record(db: AsyncSession, report: DeliveryReport, *, now: datetime | None = None) -> None:
    """Book the results on the subscriptions (after the network, in the caller's transaction).

    The rows are locked in id order (two bookings never deadlock) and updated one by one
    with ``UPDATE … WHERE id``: a subscription deleted while its push was in flight is
    skipped (``booked`` stays false), never an error."""
    if not report.results:
        return
    now = now or utcnow()
    by_id = {result.subscription_id: result for result in report.results}
    rows = (
        await db.execute(
            select(
                PushSubscription.id,
                PushSubscription.device_id,
                PushSubscription.failure_count,
                PushSubscription.last_error,
                PushSubscription.is_active,
            )
            .where(PushSubscription.id.in_(list(by_id)))
            .order_by(PushSubscription.id)
            .with_for_update()
        )
    ).all()
    # 401/403 only count against a subscription while our own key pair is consistent
    count_rejections = keys_match()
    switched_off: set[uuid.UUID] = set()
    for row in rows:
        result = by_id[row.id]
        result.booked = True
        result.device_id = row.device_id
        values = next_state(row.failure_count, row.last_error, result, now, count_rejections=count_rejections)
        await db.execute(update(PushSubscription).where(PushSubscription.id == row.id).values(**values))
        if row.is_active and values.get("is_active") is False:
            log.info("push_subscription_switched_off", subscription_id=str(row.id), reason=values["last_error"])
            if row.device_id is not None:
                switched_off.add(row.device_id)
    for device_id in switched_off:
        await sync_device_flag(db, device_id)


async def deliver(
    db: AsyncSession, subscriptions: list[PushSubscription], message: PushMessage, *, now: datetime | None = None
) -> DeliveryReport:
    """``send`` to the given subscriptions, then ``record`` the results in ``db``.

    Nothing is locked by this function while the push services answer; a caller that
    holds row locks should commit before calling it."""
    now = now or utcnow()
    report = await send([Target.of(sub) for sub in subscriptions], message, now=now)
    await record(db, report, now=now)
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
    """Active subscriptions of the user's active devices, most recently updated first
    (at most ``MAX_ACTIVE_SUBSCRIPTIONS``); expired ones are switched off."""
    now = now or utcnow()
    rows = list(
        (
            await db.scalars(
                select(PushSubscription)
                .join(Device, Device.id == PushSubscription.device_id)
                .where(*_active_filter(user_id))
                .order_by(PushSubscription.updated_at.desc(), PushSubscription.created_at.desc())
                .limit(MAX_ACTIVE_SUBSCRIPTIONS)
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


async def _enforce_cap(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Keep at most ``MAX_ACTIVE_SUBSCRIPTIONS`` active: the least recently updated give way
    (a browser that registers again counts as new)."""
    keep = (
        select(PushSubscription.id)
        .where(PushSubscription.user_id == user_id, PushSubscription.is_active.is_(True))
        .order_by(PushSubscription.updated_at.desc(), PushSubscription.created_at.desc(), PushSubscription.id.desc())
        .limit(MAX_ACTIVE_SUBSCRIPTIONS)
    )
    surplus = (
        await db.scalars(
            select(PushSubscription.id)
            .where(
                PushSubscription.user_id == user_id,
                PushSubscription.is_active.is_(True),
                PushSubscription.id.not_in(keep),
            )
            .order_by(PushSubscription.id)  # the same lock order as ``record``
            .with_for_update()
        )
    ).all()
    if not surplus:
        return
    retired = (
        await db.scalars(
            update(PushSubscription)
            .where(PushSubscription.id.in_(surplus))
            .values(is_active=False, last_error="subscription_limit")
            .returning(PushSubscription.device_id)
        )
    ).all()
    if retired:
        log.info("push_subscriptions_capped", user_id=str(user_id), switched_off=len(retired))
    for device_id in set(retired):
        await sync_device_flag(db, device_id)


async def upsert_subscription(
    db: AsyncSession, *, user_id: uuid.UUID, device: Device, payload: PushSubscriptionIn
) -> PushSubscription:
    """Register (or refresh) the browser's subscription for the calling device.

    Upsert by endpoint: re-subscribing the same browser updates the keys,
    reactivates the row and re-binds it to this device. An endpoint that belongs
    to another user is refused (``409 subscription_conflict``) — the client then
    drops its browser subscription and creates a new one. An endpoint the server
    may not send to (outside development: anything but a known push service) is
    refused with ``422 push_service_not_allowed``. Beyond ``MAX_ACTIVE_SUBSCRIPTIONS``
    active ones, the least recently updated are switched off
    (``last_error=subscription_limit``).
    """
    if not is_configured():
        raise PushNotConfigured("Web push is not configured on this server (VAPID keys)")
    refused = policy_error(payload.endpoint)
    if refused is not None:
        host = endpoint_host(payload.endpoint)
        log.warning("push_service_not_allowed", user_id=str(user_id), host=host, reason=refused)
        raise Unprocessable(
            f"This server does not send notifications to {host or 'this endpoint'}: {refused}",
            code="push_service_not_allowed",
            details={"host": host},
        )
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
    await _enforce_cap(db, user_id)
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
