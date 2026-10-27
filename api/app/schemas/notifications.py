"""Stage 7 — web push subscriptions, notification preferences and history.

Local wall-clock times travel as ``HH:MM`` strings (the user's timezone; the
server turns them into instants). Push subscription keys are what
``PushSubscription.toJSON()`` gives in the browser: base64url without padding.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from app.schemas.common import ApiModel

# ``H:MM``/``HH:MM``; seconds (``<input type=time step=1>``) are accepted and dropped
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")
_B64URL = re.compile(r"^[A-Za-z0-9_\-+/]+={0,2}$")
# RFC 3986 characters only: no spaces, quotes, backslashes, braces or anything non-ASCII
_URL_CHARS = re.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_DNS_NAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")

# Bytes, and the endpoint is ASCII: the unique btree index on push_subscriptions.endpoint
# holds at most ~2.7 KB per entry, so this always fits (real endpoints are 150–500 bytes).
MAX_ENDPOINT_BYTES = 2048
MAX_ENDPOINT_LENGTH = MAX_ENDPOINT_BYTES  # characters = bytes for an ASCII endpoint
# The push services browsers use (RFC 8030). Outside development the API POSTs nowhere
# else — https on port 443 only; PUSH_EXTRA_HOSTS adds more. "*.x" = any subdomain of x.
PUSH_SERVICE_HOSTS: tuple[str, ...] = (
    "fcm.googleapis.com",  # Chrome, Chrome/Edge on Android, Samsung Internet, Opera, Brave
    "*.push.apple.com",  # Safari: iOS/iPadOS 16.4+ Home Screen apps, macOS (web.push.apple.com)
    "*.push.services.mozilla.com",  # Firefox (updates.push.services.mozilla.com)
    "*.notify.windows.com",  # Edge on Windows (WNS)
)
# development only: the local receiver the smoke test and the browser checks POST to
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_MEAL_TIMES = 6
MAX_WATER_TIMES = 8

NotificationCategory = Literal["task_reminders", "meal_logging", "water", "workout", "evening_review", "streak_at_risk"]


def normalise_hhmm(value: str) -> str:
    """``"9:05"`` → ``"09:05"``; raises ``ValueError`` for anything that is not a wall-clock time."""
    match = _HHMM.match(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise ValueError("time must be HH:MM (24 h)")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def normalise_times(values: list[str]) -> list[str]:
    """Validated, de-duplicated and sorted ``HH:MM`` list."""
    return sorted({normalise_hhmm(value) for value in values})


def b64url_decode(value: str) -> bytes:
    """Base64url (padding optional; plain base64 is tolerated) → bytes, ``ValueError`` otherwise."""
    text = value.strip()
    if not text or not _B64URL.match(text):
        raise ValueError("not base64url")
    text = text.rstrip("=").replace("+", "-").replace("/", "_")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - the regex already filters
        raise ValueError("not base64url") from exc


def endpoint_host(endpoint: str) -> str:
    return urlsplit(endpoint).hostname or ""


def push_host_allowed(host: str, extra_hosts: Iterable[str] = ()) -> bool:
    """Is ``host`` (lower case) one of the push services (``PUSH_SERVICE_HOSTS`` + ``extra_hosts``)?"""
    if not _DNS_NAME.fullmatch(host):
        return False
    for pattern in (*PUSH_SERVICE_HOSTS, *extra_hosts):
        pattern = pattern.strip().lower()
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):  # at least one label in front (the name is valid)
                return True
        elif pattern and host == pattern:
            return True
    return False


def check_endpoint_format(endpoint: str) -> str:
    """What every endpoint must look like (``ValueError`` otherwise): ASCII, at most
    ``MAX_ENDPOINT_BYTES``, RFC 3986 characters, an ``https://`` URL — plain ``http://``
    only to a loopback host — with a host and without credentials."""
    value = endpoint.strip()
    if not value.isascii():
        raise ValueError("endpoint must be ASCII")
    if len(value.encode()) > MAX_ENDPOINT_BYTES:
        raise ValueError("endpoint is too long")
    if not _URL_CHARS.fullmatch(value):
        raise ValueError("endpoint is not a URL")
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise ValueError("endpoint is not a URL") from exc
    host = (parts.hostname or "").lower()
    if not host or "@" in parts.netloc:
        raise ValueError("endpoint is not a push service URL")
    if not (parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK_HOSTS)):
        raise ValueError("endpoint must use https://")
    return value


def check_endpoint(endpoint: str, *, allow_local: bool, extra_hosts: Iterable[str] = ()) -> str:
    """The URL the server will POST every notification to; ``ValueError`` when it may not.

    ``check_endpoint_format``, then: ``allow_local`` (ENV=development) accepts any of
    those — also plain ``http://`` to the loopback receiver the smoke test and the
    browser checks use. Otherwise (production, staging) only a known push service will
    do — ``https://``, port 443, host in ``PUSH_SERVICE_HOSTS`` or ``extra_hosts``
    (``PUSH_EXTRA_HOSTS``) — so no URL a device registers can make the server reach an
    internal address. Checked when a subscription is registered (``422
    push_service_not_allowed``) and again before every POST, which never follows a
    redirect (``app.services.push``).
    """
    value = check_endpoint_format(endpoint)
    if allow_local:
        return value
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError("endpoint must use https://")
    if parts.port not in (None, 443):
        raise ValueError("endpoint must use the standard https port")
    if not push_host_allowed((parts.hostname or "").lower(), extra_hosts):
        raise ValueError("endpoint is not a known push service")
    return value


# --------------------------------------------------------------------------- #
# Push subscriptions
# --------------------------------------------------------------------------- #
class PushConfigOut(ApiModel):
    enabled: bool
    vapid_public_key: str | None = None


class PushKeysIn(ApiModel):
    p256dh: str = Field(min_length=80, max_length=120, description="P-256 public key, uncompressed, base64url")
    auth: str = Field(min_length=16, max_length=48, description="16-byte auth secret, base64url")

    @field_validator("p256dh")
    @classmethod
    def _p256dh(cls, value: str) -> str:
        raw = b64url_decode(value)
        if len(raw) != 65 or raw[0] != 0x04:
            raise ValueError("p256dh must be an uncompressed P-256 point (65 bytes)")
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except ValueError as exc:
            raise ValueError("p256dh is not a point on P-256") from exc
        return value.strip()

    @field_validator("auth")
    @classmethod
    def _auth(cls, value: str) -> str:
        if len(b64url_decode(value)) != 16:
            raise ValueError("auth must be 16 bytes")
        return value.strip()


class PushSubscriptionIn(ApiModel):
    endpoint: str = Field(min_length=12, max_length=MAX_ENDPOINT_LENGTH)
    keys: PushKeysIn
    # PushSubscription.expirationTime: epoch milliseconds (or null)
    expiration_time: datetime | None = None
    user_agent: str | None = Field(default=None, max_length=1000)

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        # where the server may send (known push services outside development) is checked
        # on registration: 422 push_service_not_allowed (app.services.push)
        return check_endpoint_format(value)


class PushUnsubscribeIn(ApiModel):
    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT_LENGTH)


class PushSubscriptionOut(ApiModel):
    id: uuid.UUID
    device_id: uuid.UUID | None = None
    device_name: str | None = None
    device_active: bool = False
    is_current_device: bool = False
    endpoint_host: str
    is_active: bool
    failure_count: int
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    expiration_time: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PushSubscriptionListOut(ApiModel):
    items: list[PushSubscriptionOut]


class PushTestOut(ApiModel):
    sent: int
    failed: int
    deactivated: int


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
class QuietHoursOut(ApiModel):
    enabled: bool
    start: str
    end: str


class NotificationPrefsIn(ApiModel):
    """Every field optional: fields not sent keep their value (unknown legacy keys are kept too)."""

    task_reminders: bool | None = None
    meal_logging: bool | None = None
    water: bool | None = None
    workout: bool | None = None
    evening_review: bool | None = None
    streak_at_risk: bool | None = None
    meal_times: list[str] | None = Field(default=None, max_length=MAX_MEAL_TIMES)
    water_times: list[str] | None = Field(default=None, max_length=MAX_WATER_TIMES)
    workout_time: str | None = None
    evening_review_time: str | None = None

    @field_validator("meal_times", "water_times")
    @classmethod
    def _times(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalise_times(value)

    @field_validator("workout_time", "evening_review_time")
    @classmethod
    def _time(cls, value: str | None) -> str | None:
        return None if value is None else normalise_hhmm(value)


class NotificationPrefsOut(ApiModel):
    task_reminders: bool
    meal_logging: bool
    water: bool
    workout: bool
    evening_review: bool
    streak_at_risk: bool
    meal_times: list[str]
    water_times: list[str]
    workout_time: str
    evening_review_time: str
    quiet_hours: QuietHoursOut
    timezone: str
    # server side: VAPID keys configured (= feature flag push_notifications)
    push_enabled: bool
    active_subscriptions: int


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
class NotificationOut(ApiModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str | None = None
    url: str | None = None
    status: str
    sent_at: datetime | None = None
    scheduled_for: datetime | None = None
    quiet_hours_suppressed: bool
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class NotificationListOut(ApiModel):
    items: list[NotificationOut]
