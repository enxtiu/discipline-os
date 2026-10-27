"""Stage 7 — web push subscriptions, notification preferences and history.

Local wall-clock times travel as ``HH:MM`` strings (the user's timezone; the
server turns them into instants). Push subscription keys are what
``PushSubscription.toJSON()`` gives in the browser: base64url without padding.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from app.schemas.common import ApiModel

# ``H:MM``/``HH:MM``; seconds (``<input type=time step=1>``) are accepted and dropped
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")
_B64URL = re.compile(r"^[A-Za-z0-9_\-+/]+={0,2}$")

MAX_ENDPOINT_LENGTH = 2048
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


def check_endpoint(endpoint: str, *, allow_local: bool) -> str:
    """A push service URL: ``https://host/…``.

    ``allow_local`` (development) also accepts ``http://127.0.0.1``/``localhost`` so a
    fake receiver can stand in for the push service. Outside development, IP literals
    of private/loopback/link-local networks are refused (the server POSTs to this URL).
    """
    value = endpoint.strip()
    if len(value) > MAX_ENDPOINT_LENGTH:
        raise ValueError("endpoint is too long")
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise ValueError("endpoint is not a URL") from exc
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password or any(ch.isspace() for ch in value):
        raise ValueError("endpoint is not a push service URL")
    local = host in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme == "http" and allow_local and local:
        return value
    if parts.scheme != "https":
        raise ValueError("endpoint must use https://")
    if not allow_local:
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("endpoint must be a public push service")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("endpoint must be a public push service")
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
        from app.core.config import get_settings

        return check_endpoint(value, allow_local=get_settings().env == "development")


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
