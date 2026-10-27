"""Stage 7: web push (VAPID), subscriptions, preferences, the dispatcher and the daily planner.

No network: the push service is a ``requests.Session`` double (``FakePushService``);
the encryption round-trip runs the real ``pywebpush`` code path and decrypts the
captured body with ``http_ece``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time as clock
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import http_ece
import httpx
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02
from sqlalchemy import select

from app.models import (
    Device,
    JournalEntry,
    MealLog,
    NotificationLog,
    PushSubscription,
    Reminder,
    ReminderKind,
    ReminderStatus,
    Task,
    TaskStatus,
    UserSettings,
    WaterLog,
    WeeklyPlan,
    WorkoutLog,
    WorkoutStatus,
)

pytestmark = pytest.mark.anyio

P = "/api/v1/push"
N = "/api/v1/notifications"


# --------------------------------------------------------------------------- #
# Test doubles & helpers
# --------------------------------------------------------------------------- #
def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class BrowserKeys:
    """What a browser holds for one subscription: an ECDH key pair and the auth secret."""

    private_key: ec.EllipticCurvePrivateKey
    auth: bytes

    @classmethod
    def new(cls) -> BrowserKeys:
        return cls(ec.generate_private_key(ec.SECP256R1()), os.urandom(16))

    @property
    def p256dh(self) -> str:
        return b64u(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )

    def json(self) -> dict[str, str]:
        return {"p256dh": self.p256dh, "auth": b64u(self.auth)}

    def decrypt(self, body: bytes) -> dict[str, Any]:
        return json.loads(
            http_ece.decrypt(body, private_key=self.private_key, auth_secret=self.auth, version="aes128gcm")
        )


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.reason = "fake"
        self.text = ""
        self.headers: dict[str, str] = {}


@dataclass
class Captured:
    url: str
    data: bytes
    headers: dict[str, str]
    timeout: Any


class FakePushService:
    """A ``requests.Session`` double: records every POST, answers 201 unless told otherwise."""

    def __init__(self) -> None:
        self.requests: list[Captured] = []
        self.statuses: dict[str, list[int]] = {}
        self.default_status = 201
        self.errors: dict[str, Exception] = {}
        self.delay = 0.0
        self._lock = threading.Lock()

    def post(self, url: str, data: bytes | None = None, headers: dict | None = None, timeout: Any = None):
        if self.delay:
            clock.sleep(self.delay)
        with self._lock:
            self.requests.append(
                Captured(url, data or b"", {k.lower(): str(v) for k, v in (headers or {}).items()}, timeout)
            )
            queue = self.statuses.get(url)
            status = queue.pop(0) if queue else self.default_status
        if url in self.errors:
            raise self.errors[url]
        return FakeResponse(status)

    def to(self, endpoint: str) -> list[Captured]:
        return [req for req in self.requests if req.url == endpoint]


@pytest.fixture
def vapid(monkeypatch) -> Vapid02:
    """Real VAPID keys in the settings, exactly as `python -m app.cli vapid-keys` prints them."""
    from app.core.config import get_settings

    key = Vapid02()
    key.generate_keys()
    public = b64u(
        key.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "vapid_public_key", public)
    monkeypatch.setattr(settings, "vapid_private_key", key.private_pem().decode().replace("\n", "\\n"))
    monkeypatch.setattr(settings, "vapid_subject", "mailto:owner@example.com")
    return key


@pytest.fixture
def push_service(monkeypatch) -> FakePushService:
    from app.services import push

    fake = FakePushService()
    monkeypatch.setattr(push, "_transport_override", fake)
    return fake


def endpoint(n: int | str = 1) -> str:
    return f"https://push.example.test/send/{n}-{uuid.uuid4().hex[:8]}"


async def subscribe(client: httpx.AsyncClient, url: str, keys: BrowserKeys | None = None, **extra) -> httpx.Response:
    keys = keys or BrowserKeys.new()
    return await client.post(f"{P}/subscriptions", json={"endpoint": url, "keys": keys.json(), **extra})


async def owner_id(db) -> uuid.UUID:
    from app.models import User

    return (await db.execute(select(User.id).where(User.is_owner.is_(True)))).scalar_one()


async def set_settings(db, user_id: uuid.UUID, **values) -> None:
    row = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one()
    for key, value in values.items():
        setattr(row, key, value)
    await db.commit()


async def add_task(db, user_id: uuid.UUID, **values) -> uuid.UUID:
    task = Task(user_id=user_id, title=values.pop("title", "Зал"), **values)
    db.add(task)
    await db.commit()
    return task.id


async def add_reminder(
    db,
    user_id: uuid.UUID,
    *,
    remind_at: datetime,
    kind: ReminderKind = ReminderKind.TASK,
    task_id: uuid.UUID | None = None,
    payload: dict | None = None,
    title: str = "Зал",
    dedupe: str | None = None,
) -> uuid.UUID:
    row = Reminder(
        user_id=user_id,
        kind=kind,
        task_id=task_id,
        title=title,
        payload=payload or {},
        remind_at=remind_at,
        lead_minutes=(payload or {}).get("lead_minutes", 0),
        status=ReminderStatus.PENDING,
        dedupe_key=dedupe or f"test:{uuid.uuid4()}",
    )
    db.add(row)
    await db.commit()
    return row.id


async def reminder(db, reminder_id: uuid.UUID) -> Reminder:
    db.expire_all()
    return (await db.execute(select(Reminder).where(Reminder.id == reminder_id))).scalar_one()


async def log_rows(db, user_id: uuid.UUID | None = None) -> list[NotificationLog]:
    db.expire_all()
    query = select(NotificationLog).order_by(NotificationLog.created_at)
    if user_id is not None:
        query = query.where(NotificationLog.user_id == user_id)
    return list((await db.execute(query)).scalars().all())


async def dispatch(now: datetime):
    from app.services.notifications import dispatch_due

    return await dispatch_due(now)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


async def second_user(client: httpx.AsyncClient) -> str:
    """Another account with its own paired device; returns its device token."""
    from app.db import SessionLocal
    from app.models import User
    from app.services.pairing import create_pairing_code
    from app.services.settings_service import get_or_create_settings

    async with SessionLocal() as session:
        user = User(display_name="Second", is_owner=False)
        session.add(user)
        await session.flush()
        await get_or_create_settings(session, user)
        issued = await create_pairing_code(session, user, created_by="test")
        await session.commit()
    response = await client.post(
        "/api/v1/auth/pair",
        json={"code": issued.code, "device": {"name": "Second phone", "platform": "ios", "fingerprint": "fp-second"}},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


# --------------------------------------------------------------------------- #
# Config, subscriptions
# --------------------------------------------------------------------------- #
async def test_config_follows_the_vapid_keys_and_the_feature_flag(authed: httpx.AsyncClient, monkeypatch) -> None:
    off = (await authed.get(f"{P}/config")).json()
    assert off == {"enabled": False, "vapid_public_key": None}
    assert (await authed.get("/api/v1/meta/version")).json()["features"]["push_notifications"] is False
    assert (await subscribe(authed, endpoint())).json()["error"]["code"] == "push_disabled"
    test = await authed.post(f"{P}/test")
    assert (test.status_code, test.json()["error"]["code"]) == (503, "push_disabled")

    from app.core.config import get_settings

    key = Vapid02()
    key.generate_keys()
    public = b64u(
        key.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    monkeypatch.setattr(get_settings(), "vapid_public_key", public)
    monkeypatch.setattr(get_settings(), "vapid_private_key", key.private_pem().decode().replace("\n", "\\n"))
    on = (await authed.get(f"{P}/config")).json()
    assert on == {"enabled": True, "vapid_public_key": public}
    assert (await authed.get("/api/v1/meta/version")).json()["features"]["push_notifications"] is True


async def test_subscription_upsert_rebinds_and_reactivates(authed: httpx.AsyncClient, vapid, db) -> None:
    url = endpoint()
    first_keys = BrowserKeys.new()
    created = await subscribe(authed, url, first_keys, user_agent="Mozilla/5.0 (iPhone)", expiration_time=None)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["endpoint_host"] == "push.example.test"
    assert body["is_current_device"] is True and body["device_name"] == "Test iPhone"
    assert body["is_active"] is True and body["failure_count"] == 0
    assert "endpoint" not in body and "p256dh" not in body and "auth" not in body  # never echoed back
    devices = (await authed.get("/api/v1/devices")).json()["devices"]
    assert devices[0]["push_enabled"] is True

    # the browser re-subscribes (keys rotated) after the row was deactivated by a 410
    row = (await db.execute(select(PushSubscription).where(PushSubscription.id == uuid.UUID(body["id"])))).scalar_one()
    row.is_active = False
    row.failure_count = 4
    row.last_error = "http_410"
    await db.commit()
    second_keys = BrowserKeys.new()
    again = await subscribe(authed, url, second_keys)
    assert again.status_code == 201 and again.json()["id"] == body["id"]  # upsert by endpoint
    db.expire_all()
    row = (await db.execute(select(PushSubscription).where(PushSubscription.id == uuid.UUID(body["id"])))).scalar_one()
    assert (row.p256dh, row.is_active, row.failure_count, row.last_error) == (second_keys.p256dh, True, 0, None)
    assert len((await authed.get(f"{P}/subscriptions")).json()["items"]) == 1

    # expiration_time arrives as PushSubscription.expirationTime (epoch milliseconds)
    expiring = await subscribe(authed, endpoint(2), expiration_time=1_893_456_000_000)
    assert expiring.json()["expiration_time"].startswith("2030-01-01")


@pytest.mark.parametrize(
    ("patch", "why"),
    [
        ({"endpoint": "http://push.example.test/abc"}, "plain http outside development"),
        ({"endpoint": "https://user:pw@push.example.test/abc"}, "credentials in the URL"),
        ({"endpoint": "not a url at all"}, "not a URL"),
        ({"endpoint": "https://push.example.test:99999/abc"}, "bad port"),
        ({"keys": {"p256dh": b64u(b"\x04" + os.urandom(64)), "auth": b64u(os.urandom(16))}}, "point not on P-256"),
        ({"keys": {"p256dh": b64u(os.urandom(65)), "auth": b64u(os.urandom(16))}}, "not an uncompressed point"),
        ({"keys": {"p256dh": BrowserKeys.new().p256dh, "auth": b64u(os.urandom(15))}}, "auth is not 16 bytes"),
        ({"keys": {"p256dh": "!" * 87, "auth": b64u(os.urandom(16))}}, "not base64url"),
        ({"keys": {"p256dh": BrowserKeys.new().p256dh}}, "auth missing"),
        ({"surprise": True}, "unknown field"),
    ],
)
async def test_subscription_validation_answers_422(authed: httpx.AsyncClient, vapid, patch: dict, why: str) -> None:
    body = {"endpoint": endpoint(), "keys": BrowserKeys.new().json(), **patch}
    response = await authed.post(f"{P}/subscriptions", json=body)
    assert response.status_code == 422, why
    assert response.json()["error"]["code"] == "validation_error"


async def test_local_receivers_only_in_development(authed: httpx.AsyncClient, vapid, monkeypatch) -> None:
    from app.core.config import get_settings

    local = await subscribe(authed, "http://127.0.0.1:8099/push/abc")
    assert local.status_code == 201  # ENV=development in tests: the smoke receiver is allowed
    monkeypatch.setattr(get_settings(), "env", "production")
    for url in (
        "http://127.0.0.1:8099/push/abc",
        "https://127.0.0.1/push",
        "https://10.0.0.5/push",
        "https://localhost/x",
    ):
        assert (await subscribe(authed, url)).status_code == 422, url
    assert (await subscribe(authed, "https://fcm.googleapis.com/fcm/send/abc")).status_code == 201


async def test_subscriptions_are_scoped_to_their_user(
    authed: httpx.AsyncClient, client: httpx.AsyncClient, vapid
) -> None:
    mine = (await subscribe(authed, (url := endpoint()))).json()
    own_token = authed.headers["Authorization"]
    other = await second_user(client)
    headers = {"Authorization": f"Bearer {other}"}

    assert (await client.get(f"{P}/subscriptions", headers=headers)).json()["items"] == []
    assert (await client.delete(f"{P}/subscriptions/{mine['id']}", headers=headers)).status_code == 404
    # the same browser endpoint presented by another account is refused, not hijacked
    taken = await client.post(
        f"{P}/subscriptions", headers=headers, json={"endpoint": url, "keys": BrowserKeys.new().json()}
    )
    assert (taken.status_code, taken.json()["error"]["code"]) == (409, "subscription_conflict")
    # unsubscribing someone else's endpoint changes nothing
    assert (await client.post(f"{P}/unsubscribe", headers=headers, json={"endpoint": url})).status_code == 204
    client.headers["Authorization"] = own_token
    assert len((await client.get(f"{P}/subscriptions")).json()["items"]) == 1

    # a scoped API token is not a device
    token = (await authed.post("/api/v1/tokens", json={"name": "auto", "scopes": ["read:all"]})).json()["token"]
    forbidden = await client.get(f"{P}/subscriptions", headers={"Authorization": f"Bearer {token}"})
    assert (forbidden.status_code, forbidden.json()["error"]["code"]) == (403, "device_only")
    assert (await client.get(f"{N}/prefs", headers={"Authorization": f"Bearer {token}"})).status_code == 403


async def test_delete_and_unsubscribe(authed: httpx.AsyncClient, vapid, db) -> None:
    first = (await subscribe(authed, (one := endpoint(1)))).json()
    await subscribe(authed, (two := endpoint(2)))
    deleted = await authed.delete(f"{P}/subscriptions/{first['id']}")
    assert deleted.status_code == 204 and deleted.content == b""
    assert (await authed.delete(f"{P}/subscriptions/{first['id']}")).status_code == 404
    assert (await authed.delete(f"{P}/subscriptions/not-a-uuid")).status_code == 422
    devices = (await authed.get("/api/v1/devices")).json()["devices"]
    assert devices[0]["push_enabled"] is True  # the second subscription is still on

    assert (await authed.post(f"{P}/unsubscribe", json={"endpoint": two})).status_code == 204
    assert (await authed.post(f"{P}/unsubscribe", json={"endpoint": one})).status_code == 204  # unknown: fine
    assert (await authed.get(f"{P}/subscriptions")).json()["items"] == []
    assert (await authed.get("/api/v1/devices")).json()["devices"][0]["push_enabled"] is False


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
async def test_prefs_defaults_partial_update_and_legacy_keys(authed: httpx.AsyncClient, db) -> None:
    prefs = (await authed.get(f"{N}/prefs")).json()
    assert {
        key: prefs[key]
        for key in ("task_reminders", "meal_logging", "water", "workout", "evening_review", "streak_at_risk")
    } == dict.fromkeys(("task_reminders", "meal_logging", "water", "workout", "evening_review", "streak_at_risk"), True)
    assert (prefs["meal_times"], prefs["water_times"]) == (["13:00", "19:30"], ["12:00", "16:00", "20:00"])
    assert (prefs["workout_time"], prefs["evening_review_time"]) == ("18:00", "21:30")
    assert prefs["quiet_hours"] == {"enabled": True, "start": "23:00", "end": "07:00"}
    assert (prefs["push_enabled"], prefs["active_subscriptions"]) == (False, 0)

    # legacy keys written by earlier screens survive a typed update
    await authed.patch(
        "/api/v1/settings", json={"notification_prefs": {"push_requested": True, "meals": False, "water": True}}
    )
    updated = await authed.put(
        f"{N}/prefs", json={"water": False, "meal_times": ["19:30", "8:05", "19:30"], "workout_time": "07:15:00"}
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["water"], body["meal_times"], body["workout_time"], body["evening_review"]) == (
        False,
        ["08:05", "19:30"],
        "07:15",
        True,
    )
    stored = (await authed.get("/api/v1/settings")).json()["settings"]["notification_prefs"]
    assert stored["push_requested"] is True and stored["meals"] is False and stored["water"] is False

    # quiet hours stay in the settings row and are mirrored here
    await authed.patch("/api/v1/settings", json={"quiet_hours_start": "22:30:00", "quiet_hours_enabled": False})
    assert (await authed.get(f"{N}/prefs")).json()["quiet_hours"] == {
        "enabled": False,
        "start": "22:30",
        "end": "07:00",
    }

    # the onboarding wizard sends on/off only; times are kept
    await authed.post("/api/v1/settings/onboarding", json={"notifications": {"water": True}})
    after = (await authed.get(f"{N}/prefs")).json()
    assert (after["water"], after["meal_times"]) == (True, ["08:05", "19:30"])


@pytest.mark.parametrize(
    "body",
    [
        {"meal_times": ["25:00"]},
        {"meal_times": ["12:60"]},
        {"meal_times": "13:00"},
        {"water_times": [f"{h:02d}:00" for h in range(9, 18)]},
        {"workout_time": "evening"},
        {"water": "yes please"},
        {"quiet_hours": {"enabled": False}},
    ],
)
async def test_prefs_validation(authed: httpx.AsyncClient, body: dict) -> None:
    response = await authed.put(f"{N}/prefs", json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")


async def test_malformed_stored_prefs_fall_back_to_defaults(authed: httpx.AsyncClient, db) -> None:
    await set_settings(
        db,
        await owner_id(db),
        notification_prefs={"meal_times": "lunch", "water_times": ["99:99"], "workout_time": 18, "water": "no"},
    )
    prefs = (await authed.get(f"{N}/prefs")).json()
    assert (prefs["meal_times"], prefs["water_times"], prefs["workout_time"], prefs["water"]) == (
        ["13:00", "19:30"],
        ["12:00", "16:00", "20:00"],
        "18:00",
        True,
    )


# --------------------------------------------------------------------------- #
# Sender
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "outcome", "active_after"),
    [
        (201, "sent", True),
        (202, "sent", True),
        (404, "gone", False),
        (410, "gone", False),
        (400, "permanent", True),
        (413, "permanent", True),
        (403, "permanent", True),
        (429, "transient", True),
        (500, "transient", True),
        (503, "transient", True),
    ],
)
async def test_sender_maps_push_service_answers(
    authed: httpx.AsyncClient, vapid, push_service: FakePushService, db, status: int, outcome: str, active_after: bool
) -> None:
    from app.services import push

    url = endpoint()
    sub_id = uuid.UUID((await subscribe(authed, url)).json()["id"])
    push_service.statuses[url] = [status]
    sub = (await db.execute(select(PushSubscription).where(PushSubscription.id == sub_id))).scalar_one()
    sub.failure_count = 2
    await db.commit()

    message = push.PushMessage(title="t", body="b", url="/today", tag="x", kind="test")
    report = await push.deliver(db, [sub], message)
    await db.commit()
    assert report.results[0].outcome.value == outcome
    db.expire_all()
    sub = (await db.execute(select(PushSubscription).where(PushSubscription.id == sub_id))).scalar_one()
    assert sub.is_active is active_after
    if outcome == "sent":
        assert sub.last_success_at is not None and sub.failure_count == 0 and sub.last_error is None
    else:
        assert sub.failure_count == 3 and sub.last_error == f"http_{status}" and sub.last_failure_at is not None
    device = (await db.execute(select(Device).where(Device.id == sub.device_id))).scalar_one()
    assert device.push_enabled is active_after


async def test_sender_network_errors_are_transient_and_headers_are_set(
    authed: httpx.AsyncClient, vapid, push_service: FakePushService, db
) -> None:
    from app.services import push

    down, up = endpoint("down"), endpoint("up")
    await subscribe(authed, down)
    await subscribe(authed, up)
    push_service.errors[down] = requests.ConnectionError("refused")
    subs = await push.active_subscriptions(db, await owner_id(db))
    message = push.PushMessage(
        title="Зал", body="Сейчас", url="/plan?task=1", tag="task:1", kind="task", urgency="high"
    )
    report = await push.deliver(db, subs, message)
    await db.commit()
    assert (report.sent, report.transient, report.failed) == (1, 1, 1)
    assert next(r for r in report.results if r.outcome.value == "transient").error == "network:ConnectionError"

    sent = push_service.to(up)[0]
    assert sent.headers["content-encoding"] == "aes128gcm"
    assert sent.headers["ttl"] == str(push.REMINDER_TTL_SECONDS)
    assert sent.headers["urgency"] == "high"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", sent.headers["topic"])
    assert sent.headers["authorization"].startswith("vapid t=")
    assert sent.timeout == push.SEND_TIMEOUT_SECONDS
    assert len(sent.data) < 4096


async def test_encryption_round_trip_through_the_real_pywebpush_path(
    authed: httpx.AsyncClient, vapid: Vapid02, push_service: FakePushService
) -> None:
    keys = BrowserKeys.new()
    url = endpoint("roundtrip")
    assert (await subscribe(authed, url, keys)).status_code == 201

    result = await authed.post(f"{P}/test")
    assert result.status_code == 200, result.text
    assert result.json() == {"sent": 1, "failed": 0, "deactivated": 0}

    captured = push_service.to(url)[0]
    payload = keys.decrypt(captured.data)  # only the browser's private key can read it
    assert payload["title"] == "Проверка уведомлений"
    assert payload["url"] == "/settings/notifications" and payload["tag"] == "test" and payload["kind"] == "test"
    assert set(payload) == {"title", "body", "url", "tag", "renotify", "kind", "id"}
    assert captured.headers["ttl"] == "60"

    # RFC 8292: the JWT is signed by our VAPID key, for this push service, with our contact
    authorization = captured.headers["authorization"]
    assert Vapid02.verify(authorization)
    k = authorization.split("k=", 1)[1]
    assert k == b64u(
        vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    token = authorization.split("t=", 1)[1].split(",", 1)[0]
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert claims["aud"] == "https://push.example.test" and claims["sub"] == "mailto:owner@example.com"
    assert claims["exp"] > clock.time()

    # the test is logged and shows up in the history
    items = (await authed.get(N)).json()["items"]
    assert (items[0]["kind"], items[0]["status"], items[0]["title"]) == ("test", "sent", "Проверка уведомлений")


async def test_test_endpoint_without_subscriptions_and_in_english(
    authed: httpx.AsyncClient, vapid, push_service, db
) -> None:
    assert (await authed.post(f"{P}/test")).json() == {"sent": 0, "failed": 0, "deactivated": 0}
    assert (await authed.get(N)).json()["items"][0]["error"] == "no_subscription"
    await authed.patch("/api/v1/settings", json={"language": "en"})
    keys = BrowserKeys.new()
    url = endpoint()
    await subscribe(authed, url, keys)
    await authed.post(f"{P}/test")
    assert keys.decrypt(push_service.to(url)[0].data)["title"] == "Test notification"


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
NOON = utc(2026, 7, 15, 5, 0)  # 12:00 in Asia/Bangkok (UTC+7, no DST)


@pytest.fixture
async def ready(authed: httpx.AsyncClient, vapid, push_service: FakePushService, db) -> dict[str, Any]:
    """Owner in Asia/Bangkok with one browser subscribed; quiet hours 23:00–07:00."""
    user_id = await owner_id(db)
    await set_settings(
        db,
        user_id,
        timezone="Asia/Bangkok",
        language="ru",
        quiet_hours_enabled=True,
        quiet_hours_start=time(23, 0),
        quiet_hours_end=time(7, 0),
    )
    keys = BrowserKeys.new()
    url = endpoint("phone")
    assert (await subscribe(authed, url, keys)).status_code == 201
    await db.execute(
        Reminder.__table__.delete()
    )  # smart rows planned on subscribe (real clock) are not under test here
    await db.commit()
    return {"user_id": user_id, "keys": keys, "endpoint": url, "push": push_service, "client": authed}


async def test_dispatch_due_and_not_due(ready, db) -> None:
    user_id, fake = ready["user_id"], ready["push"]
    task_id = await add_task(db, user_id, title="Позвонить маме", due_at=NOON + timedelta(minutes=15))
    due = await add_reminder(
        db, user_id, task_id=task_id, remind_at=NOON - timedelta(seconds=20), payload={"lead_minutes": 15}
    )
    later = await add_reminder(
        db, user_id, task_id=task_id, remind_at=NOON + timedelta(minutes=5), payload={"lead_minutes": 10}
    )

    report = await dispatch(NOON)
    assert report.get("sent") == 1
    assert (await reminder(db, due)).status == ReminderStatus.SENT
    assert (await reminder(db, due)).sent_at == NOON and (await reminder(db, due)).attempts == 1
    assert (await reminder(db, later)).status == ReminderStatus.PENDING

    payload = ready["keys"].decrypt(fake.to(ready["endpoint"])[0].data)
    assert payload == {
        "title": "Позвонить маме",
        "body": "Через 15 мин · 12:15",
        "url": f"/plan?task={task_id}",
        "tag": f"task:{task_id}",
        "renotify": True,
        "kind": "task",
        "id": str(task_id),
    }
    logs = await log_rows(db, user_id)
    assert len(logs) == 1
    assert (logs[0].dedupe_key, logs[0].status, logs[0].sent_at, logs[0].reminder_id) == (
        f"reminder:{due}",
        "sent",
        NOON,
        due,
    )
    assert logs[0].subscription_id is not None and logs[0].device_id is not None

    # dedupe: running again sends nothing new
    assert (await dispatch(NOON + timedelta(seconds=30))).get("sent") == 0
    assert len(fake.requests) == 1 and len(await log_rows(db, user_id)) == 1


async def test_dispatch_category_off_stale_and_closed_tasks(ready, db) -> None:
    user_id, fake = ready["user_id"], ready["push"]
    stale_task = await add_task(db, user_id, title="Старое", due_at=NOON - timedelta(hours=2))
    stale = await add_reminder(db, user_id, task_id=stale_task, remind_at=NOON - timedelta(minutes=61))
    done = await add_reminder(db, user_id, task_id=await add_task(db, user_id, status=TaskStatus.DONE), remind_at=NOON)
    cancelled = await add_reminder(
        db, user_id, task_id=await add_task(db, user_id, status=TaskStatus.CANCELLED), remind_at=NOON
    )
    orphan = await add_reminder(db, user_id, task_id=None, remind_at=NOON)
    await dispatch(NOON)
    assert ((await reminder(db, stale)).status, (await reminder(db, stale)).last_error) == (
        ReminderStatus.SUPPRESSED,
        "stale",
    )
    assert ((await reminder(db, done)).status, (await reminder(db, done)).last_error) == (
        ReminderStatus.CANCELLED,
        "task_closed",
    )
    assert (await reminder(db, cancelled)).status == ReminderStatus.CANCELLED
    assert ((await reminder(db, orphan)).status, (await reminder(db, orphan)).last_error) == (
        ReminderStatus.CANCELLED,
        "task_missing",
    )
    logs = await log_rows(db, user_id)
    assert [(row.status, row.error, row.title) for row in logs] == [
        ("suppressed", "stale", "Старое")
    ]  # cancellations are not history
    assert fake.requests == []

    # a deleted task takes its reminders with it (FK cascade): nothing left to send
    gone = await add_task(db, user_id, title="Удалю")
    gone_reminder = await add_reminder(db, user_id, task_id=gone, remind_at=NOON)
    await db.execute(Task.__table__.delete().where(Task.__table__.c.id == gone))
    await db.commit()
    assert (await db.execute(select(Reminder).where(Reminder.id == gone_reminder))).scalar_one_or_none() is None

    # category off → cancelled, nothing sent, nothing logged
    await ready["client"].put(f"{N}/prefs", json={"task_reminders": False})
    off = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON)
    await dispatch(NOON)
    assert ((await reminder(db, off)).status, (await reminder(db, off)).last_error) == (
        ReminderStatus.CANCELLED,
        "category_off",
    )
    assert fake.requests == [] and len(await log_rows(db, user_id)) == 1


async def test_quiet_hours_defer_task_reminders_across_midnight(ready, db) -> None:
    user_id, fake = ready["user_id"], ready["push"]
    late_evening = utc(2026, 7, 15, 16, 30)  # 23:30 Bangkok
    task_id = await add_task(db, user_id, title="Таблетки", due_at=late_evening)
    rid = await add_reminder(db, user_id, task_id=task_id, remind_at=late_evening)

    assert (await dispatch(late_evening)).get("deferred") == 1
    row = await reminder(db, rid)
    assert (row.status, row.remind_at) == (ReminderStatus.PENDING, utc(2026, 7, 16, 0, 0))  # 07:00 the next day
    log = (await log_rows(db, user_id))[0]
    assert (log.status, log.quiet_hours_suppressed, log.scheduled_for, log.error) == (
        "deferred",
        True,
        late_evening,
        "quiet_hours",
    )
    assert fake.requests == []

    # still quiet at 06:59 → nothing; at 07:00 it goes out, history keeps the quiet-hours mark
    assert (await dispatch(utc(2026, 7, 15, 23, 59))).total == 0
    assert (await dispatch(utc(2026, 7, 16, 0, 0, 30))).get("sent") == 1
    log = (await log_rows(db, user_id))[0]
    assert (log.status, log.quiet_hours_suppressed, log.scheduled_for) == ("sent", True, late_evening)
    assert ready["keys"].decrypt(fake.requests[0].data)["body"] == "Срок прошёл · 23:30"

    # the early-morning half of the range defers to 07:00 the same day
    early = utc(2026, 7, 16, 19, 0)  # 02:00 Bangkok on the 17th
    rid2 = await add_reminder(db, user_id, task_id=task_id, remind_at=early)
    await dispatch(early)
    assert (await reminder(db, rid2)).remind_at == utc(2026, 7, 17, 0, 0)


async def test_quiet_hours_deferral_is_dst_safe(ready, db) -> None:
    user_id = ready["user_id"]
    await set_settings(db, user_id, timezone="Europe/Berlin", quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0))
    # 2026-03-28 23:30 CET; clocks jump to CEST at 02:00 on the 29th → 07:00 CEST is 05:00 UTC
    spring = utc(2026, 3, 28, 22, 30)
    rid = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=spring)
    await dispatch(spring)
    assert (await reminder(db, rid)).remind_at == utc(2026, 3, 29, 5, 0)
    # 2026-10-24 23:30 CEST; back to CET at 03:00 on the 25th → 07:00 CET is 06:00 UTC
    autumn = utc(2026, 10, 24, 21, 30)
    rid2 = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=autumn)
    await dispatch(autumn)
    assert (await reminder(db, rid2)).remind_at == utc(2026, 10, 25, 6, 0)


async def test_quiet_hours_drop_smart_reminders(ready, db) -> None:
    user_id = ready["user_id"]
    at = utc(2026, 7, 15, 16, 30)  # 23:30
    rid = await add_reminder(
        db,
        user_id,
        kind=ReminderKind.EVENING_REVIEW,
        remind_at=at,
        title="Итоги дня",
        payload={"date": "2026-07-15", "time": "21:30"},
        dedupe="evening_review:2026-07-15",
    )
    await dispatch(at)
    row = await reminder(db, rid)
    assert (row.status, row.last_error) == (ReminderStatus.SUPPRESSED, "quiet_hours")
    log = (await log_rows(db, user_id))[0]
    assert (log.status, log.quiet_hours_suppressed, log.title) == ("suppressed", True, "Итоги дня")


async def test_transient_failures_retry_then_fail(ready, db) -> None:
    user_id, fake = ready["user_id"], ready["push"]
    fake.default_status = 503
    rid = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON)
    for attempt, expected in ((1, "retrying"), (2, "retrying"), (3, "failed")):
        report = await dispatch(NOON + timedelta(minutes=attempt - 1))
        assert report.get(expected) == 1
        row = await reminder(db, rid)
        assert row.attempts == attempt
        assert (await log_rows(db, user_id))[0].status == expected
    row = await reminder(db, rid)
    assert row.status == ReminderStatus.FAILED and row.last_error == "delivery_failed:http_503"
    assert len(fake.requests) == 3 and len(await log_rows(db, user_id)) == 1  # one history row per reminder
    assert (await dispatch(NOON + timedelta(minutes=5))).total == 0


async def test_gone_subscription_is_deactivated_then_reminders_are_suppressed(ready, db) -> None:
    user_id, fake = ready["user_id"], ready["push"]
    fake.statuses[ready["endpoint"]] = [410]
    first = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON)
    await dispatch(NOON)
    assert (await reminder(db, first)).status == ReminderStatus.FAILED
    db.expire_all()
    sub = (await db.execute(select(PushSubscription))).scalar_one()
    assert (sub.is_active, sub.last_error) == (False, "http_410")

    second = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON + timedelta(minutes=1))
    await dispatch(NOON + timedelta(minutes=1))
    assert ((await reminder(db, second)).status, (await reminder(db, second)).last_error) == (
        ReminderStatus.SUPPRESSED,
        "no_subscription",
    )
    assert len(fake.requests) == 1


async def test_any_success_marks_sent_and_revoked_devices_are_skipped(ready, db, client) -> None:
    user_id, fake = ready["user_id"], ready["push"]
    laptop = endpoint("laptop")
    await subscribe(ready["client"], laptop)
    fake.statuses[ready["endpoint"]] = [500]
    rid = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON)
    assert (await dispatch(NOON)).get("sent") == 1
    assert (await reminder(db, rid)).status == ReminderStatus.SENT

    # a revoked phone keeps its subscription row, but it is never used again
    db.expire_all()
    device = (await db.execute(select(Device))).scalar_one()
    device.is_active = False
    await db.commit()
    rid2 = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON + timedelta(minutes=1))
    await dispatch(NOON + timedelta(minutes=1))
    assert (await reminder(db, rid2)).last_error == "no_subscription"
    assert len(fake.requests) == 2


async def test_push_disabled_suppresses(ready, db, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "vapid_private_key", "")
    rid = await add_reminder(db, ready["user_id"], task_id=await add_task(db, ready["user_id"]), remind_at=NOON)
    await dispatch(NOON)
    assert (await reminder(db, rid)).last_error == "push_disabled"
    assert (await log_rows(db))[0].error == "push_disabled"

    # a private key that cannot be read is the same: suppressed once, not retried every minute
    monkeypatch.setattr(get_settings(), "vapid_private_key", "not a key")
    broken = await add_reminder(db, ready["user_id"], task_id=await add_task(db, ready["user_id"]), remind_at=NOON)
    report = await dispatch(NOON)
    assert report.get("suppressed") == 1 and report.get("error") == 0
    row = await reminder(db, broken)
    assert (row.status, row.last_error, row.attempts) == (ReminderStatus.SUPPRESSED, "push_disabled", 0)
    assert ready["push"].requests == []
    test = await ready["client"].post(f"{P}/test")
    assert (test.status_code, test.json()["error"]["code"]) == (503, "push_disabled")


async def test_two_overlapping_runs_send_once(ready, db) -> None:
    user_id, fake = ready["user_id"], ready["push"]
    fake.delay = 0.3  # the first run holds the row lock while "sending"
    rid = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON)
    first, second = await asyncio.gather(dispatch(NOON), dispatch(NOON))
    assert first.get("sent") + second.get("sent") == 1
    assert len(fake.requests) == 1
    assert (await reminder(db, rid)).status == ReminderStatus.SENT
    assert len(await log_rows(db, user_id)) == 1


@pytest.mark.parametrize(
    ("anchor", "lang", "expected"),
    [
        ({"due_at": NOON + timedelta(minutes=15)}, "ru", "Через 15 мин · 12:15"),
        ({"due_at": NOON + timedelta(minutes=14, seconds=30)}, "ru", "Через 15 мин · 12:14"),
        ({"due_at": NOON + timedelta(hours=1, minutes=30)}, "ru", "Через 1 ч 30 мин · 13:30"),
        ({"due_at": NOON + timedelta(hours=2)}, "en", "In 2 h · 14:00"),
        ({"due_at": NOON}, "ru", "Сейчас"),
        ({"due_at": NOON - timedelta(seconds=70)}, "en", "Now"),
        ({"scheduled_start": NOON + timedelta(days=1)}, "ru", "Завтра · 12:00"),
        ({"due_at": NOON + timedelta(days=3)}, "ru", "18 июл · 12:00"),
        ({"due_at": NOON + timedelta(days=3)}, "en", "Jul 18 · 12:00"),
        ({"due_date": date(2026, 7, 15)}, "ru", "Срок сегодня"),
        ({"due_date": date(2026, 7, 16)}, "en", "Due tomorrow"),
        ({"due_date": date(2026, 7, 20)}, "ru", "Срок: 20 июл"),
        ({"due_date": date(2026, 7, 10)}, "ru", "Срок прошёл"),
        ({}, "ru", "Напоминание"),
    ],
)
def test_task_message_bodies(anchor: dict, lang: str, expected: str) -> None:
    from app.services.notifications import TaskView, task_body

    task = TaskView(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="x",
        status=TaskStatus.OPEN,
        due_at=anchor.get("due_at"),
        due_date=anchor.get("due_date"),
        scheduled_start=anchor.get("scheduled_start"),
    )
    assert task_body(task, now=NOON, tz="Asia/Bangkok", lang=lang) == expected


def test_quiet_hours_ranges() -> None:
    from app.services.notifications import QuietHours

    overnight = QuietHours(True, time(23, 0), time(7, 0))
    assert [overnight.contains(time(h, m)) for h, m in ((22, 59), (23, 0), (3, 0), (6, 59), (7, 0), (12, 0))] == [
        False,
        True,
        True,
        True,
        False,
        False,
    ]
    siesta = QuietHours(True, time(13, 0), time(15, 0))
    assert [siesta.contains(time(h)) for h in (12, 13, 14, 15)] == [False, True, True, False]
    assert QuietHours(True, time(8, 0), time(8, 0)).contains(time(8, 0)) is False  # equal bounds = no quiet hours
    assert QuietHours(False, time(0, 0), time(23, 59)).contains(time(12, 0)) is False


# --------------------------------------------------------------------------- #
# Daily planner
# --------------------------------------------------------------------------- #
async def planned(db, user_id: uuid.UUID) -> dict[str, datetime]:
    db.expire_all()
    rows = (
        (await db.execute(select(Reminder).where(Reminder.user_id == user_id, Reminder.task_id.is_(None))))
        .scalars()
        .all()
    )
    return {row.dedupe_key: row.remind_at for row in rows}


async def test_planner_creates_todays_rows_once_in_the_users_timezone(ready, db) -> None:
    from app.services.notifications import plan_daily_reminders

    user_id = ready["user_id"]
    await set_settings(db, user_id, timezone="America/New_York", quiet_hours_enabled=False)
    morning = utc(2026, 7, 15, 12, 0)  # 08:00 EDT
    created = await plan_daily_reminders(morning)
    rows = await planned(db, user_id)
    assert created == len(rows) == 6  # 2 meals + 3 water + review (no workout planned)
    assert rows["meal:2026-07-15:13:00"] == utc(2026, 7, 15, 17, 0)
    assert rows["meal:2026-07-15:19:30"] == utc(2026, 7, 15, 23, 30)
    assert rows["water:2026-07-15:12:00"] == utc(2026, 7, 15, 16, 0)
    assert rows["evening_review:2026-07-15"] == utc(2026, 7, 16, 1, 30)
    assert await plan_daily_reminders(morning + timedelta(minutes=15)) == 0  # idempotent
    assert len(await planned(db, user_id)) == 6

    # winter: the same wall clock is an hour later in UTC
    await db.execute(Reminder.__table__.delete())
    await db.commit()
    await plan_daily_reminders(utc(2026, 1, 14, 13, 0))
    assert (await planned(db, user_id))["meal:2026-01-14:13:00"] == utc(2026, 1, 14, 18, 0)


async def test_planner_skips_the_past_quiet_hours_and_users_without_push(ready, db, client) -> None:
    from app.services.notifications import plan_daily_reminders

    user_id = ready["user_id"]
    await second_user(client)  # paired, but no subscription → nothing is planned for that account
    await set_settings(db, user_id, quiet_hours_start=time(19, 0), quiet_hours_end=time(7, 0))
    afternoon = utc(2026, 7, 15, 7, 0)  # 14:00 Bangkok
    await plan_daily_reminders(afternoon)
    assert sorted(await planned(db, user_id)) == [
        "water:2026-07-15:16:00"
    ]  # 13:00/12:00 passed; 19:30, 20:00, 21:30 quiet
    assert all(row.user_id == user_id for row in (await db.execute(select(Reminder))).scalars().all())


async def test_planner_dst_gap_and_workout_only_when_planned(ready, db) -> None:
    from app.services.notifications import plan_daily_reminders

    user_id = ready["user_id"]
    await set_settings(
        db,
        user_id,
        timezone="Europe/Berlin",
        quiet_hours_enabled=False,
        notification_prefs={"water_times": ["02:30"], "meal_logging": False, "evening_review": False},
    )
    before = utc(2026, 3, 29, 0, 0)  # 01:00 CET, an hour before the clocks jump
    await plan_daily_reminders(before)
    rows = await planned(db, user_id)
    assert rows == {"water:2026-03-29:02:30": utc(2026, 3, 29, 1, 30)}  # 02:30 does not exist → 03:30 CEST

    # a workout reminder needs a workout on today's plan (Wednesday = weekday 2)
    await set_settings(
        db,
        user_id,
        timezone="Asia/Bangkok",
        notification_prefs={"meal_logging": False, "water": False, "evening_review": False},
    )
    await plan_daily_reminders(NOON)
    assert "workout:2026-07-15" not in await planned(db, user_id)
    db.add(WeeklyPlan(user_id=user_id, weekday=2, title="Ноги"))
    await db.commit()
    await plan_daily_reminders(NOON)
    assert (await planned(db, user_id))["workout:2026-07-15"] == utc(2026, 7, 15, 11, 0)  # 18:00 Bangkok


async def test_prefs_change_replans_future_rows(ready, db, monkeypatch) -> None:
    from app.services import notifications

    user_id = ready["user_id"]
    await set_settings(db, user_id, quiet_hours_enabled=False)
    monkeypatch.setattr(notifications, "utcnow", lambda: NOON)
    await notifications.plan_daily_reminders(NOON)
    assert "meal:2026-07-15:19:30" in await planned(db, user_id)
    response = await ready["client"].put(f"{N}/prefs", json={"meal_times": ["13:00", "17:45"], "water": False})
    assert response.status_code == 200
    rows = await planned(db, user_id)
    assert "meal:2026-07-15:19:30" not in rows and "water:2026-07-15:16:00" not in rows
    assert rows["meal:2026-07-15:17:45"] == utc(2026, 7, 15, 10, 45)

    # the last device switched off → pending smart reminders go away
    sub_id = (await ready["client"].get(f"{P}/subscriptions")).json()["items"][0]["id"]
    assert (await ready["client"].delete(f"{P}/subscriptions/{sub_id}")).status_code == 204
    assert await planned(db, user_id) == {}


async def test_smart_slot_removed_from_prefs_is_cancelled_and_timezone_moves_are_rescheduled(ready, db) -> None:
    user_id = ready["user_id"]
    gone = await add_reminder(
        db,
        user_id,
        kind=ReminderKind.WATER,
        remind_at=NOON,
        payload={"date": "2026-07-15", "time": "11:00"},
        dedupe="water:2026-07-15:11:00",
    )
    await dispatch(NOON)
    assert ((await reminder(db, gone)).status, (await reminder(db, gone)).last_error) == (
        ReminderStatus.CANCELLED,
        "prefs_changed",
    )
    # planned in Bangkok for 12:00, the user flew to Moscow: 12:00 there is later in UTC
    moved = await add_reminder(
        db,
        user_id,
        kind=ReminderKind.WATER,
        remind_at=NOON,
        payload={"date": "2026-07-15", "time": "12:00"},
        dedupe="water:2026-07-15:12:00",
    )
    await set_settings(db, user_id, timezone="Europe/Moscow")
    assert (await dispatch(NOON)).get("rescheduled") == 1
    assert (await reminder(db, moved)).remind_at == utc(2026, 7, 15, 9, 0)


# --------------------------------------------------------------------------- #
# Conditions at send time
# --------------------------------------------------------------------------- #
async def smart(db, user_id, kind: ReminderKind, at: str, now: datetime) -> uuid.UUID:
    return await add_reminder(
        db,
        user_id,
        kind=kind,
        remind_at=now,
        title="—",
        payload={"date": "2026-07-15", "time": at},
        dedupe=f"{kind.value}:2026-07-15:{at}:{uuid.uuid4().hex[:4]}",
    )


async def test_meal_condition(ready, db) -> None:
    user_id, fake, keys = ready["user_id"], ready["push"], ready["keys"]
    one_pm = utc(2026, 7, 15, 6, 0)
    first = await smart(db, user_id, ReminderKind.MEAL, "13:00", one_pm)
    await dispatch(one_pm)
    assert (await reminder(db, first)).status == ReminderStatus.SENT
    meal = keys.decrypt(fake.requests[-1].data)
    assert (meal["title"], meal["body"], meal["url"]) == (
        "Дневник питания",
        "Запишите, что вы ели сегодня, — это займёт минуту",
        "/food?action=search",
    )

    # lunch logged at 12:30 (before 13:00): the 19:30 reminder still asks about the time since 13:00
    db.add(MealLog(user_id=user_id, logged_at=utc(2026, 7, 15, 5, 30), local_day=date(2026, 7, 15)))
    await db.commit()
    evening = utc(2026, 7, 15, 12, 30)
    second = await smart(db, user_id, ReminderKind.MEAL, "19:30", evening)
    await dispatch(evening)
    assert keys.decrypt(fake.requests[-1].data)["body"] == "Запишите приёмы пищи с 13:00"
    assert (await reminder(db, second)).status == ReminderStatus.SENT

    # something logged in the window → not needed
    db.add(MealLog(user_id=user_id, logged_at=utc(2026, 7, 15, 11, 0), local_day=date(2026, 7, 15)))
    await db.commit()
    third = await smart(db, user_id, ReminderKind.MEAL, "19:30", evening)
    again = await smart(db, user_id, ReminderKind.MEAL, "13:00", evening)
    await dispatch(evening)
    assert ((await reminder(db, third)).status, (await reminder(db, third)).last_error) == (
        ReminderStatus.CANCELLED,
        "not_needed",
    )
    assert (await reminder(db, again)).status == ReminderStatus.CANCELLED
    assert len(fake.requests) == 2


async def test_water_condition(ready, db) -> None:
    user_id, fake, keys = ready["user_id"], ready["push"], ready["keys"]
    await set_settings(db, user_id, target_kcal=2200, target_water_ml=2800)
    four_pm = utc(2026, 7, 15, 9, 0)  # 16:00: 8 of the 14 hours 08:00–22:00 → 1 600 ml expected by now
    db.add(WaterLog(user_id=user_id, logged_at=utc(2026, 7, 15, 2, 0), local_day=date(2026, 7, 15), amount_ml=1000))
    await db.commit()
    behind = await smart(db, user_id, ReminderKind.WATER, "16:00", four_pm)
    await dispatch(four_pm)
    assert (await reminder(db, behind)).status == ReminderStatus.SENT
    water = keys.decrypt(fake.requests[-1].data)
    assert (water["title"], water["body"], water["url"]) == ("Вода", "Вода: осталось 1 800 мл до цели", "/today")

    db.add(WaterLog(user_id=user_id, logged_at=utc(2026, 7, 15, 7, 0), local_day=date(2026, 7, 15), amount_ml=700))
    await db.commit()
    on_track = await smart(db, user_id, ReminderKind.WATER, "16:00", four_pm)
    await dispatch(four_pm)
    assert (await reminder(db, on_track)).last_error == "not_needed"

    # English: the body says how much is left
    await set_settings(db, user_id, language="en")
    late = utc(2026, 7, 15, 13, 0)  # 20:00 → 2 400 ml expected, 1 700 drunk
    left = await smart(db, user_id, ReminderKind.WATER, "20:00", late)
    await dispatch(late)
    assert (await reminder(db, left)).status == ReminderStatus.SENT
    assert keys.decrypt(fake.requests[-1].data)["body"] == "Water: 1,100 ml left to your goal"


async def test_workout_condition(ready, db) -> None:
    user_id, fake, keys = ready["user_id"], ready["push"], ready["keys"]
    six_pm = utc(2026, 7, 15, 11, 0)
    no_plan = await smart(db, user_id, ReminderKind.WORKOUT, "18:00", six_pm)
    await dispatch(six_pm)
    assert (await reminder(db, no_plan)).last_error == "not_needed"

    db.add(WeeklyPlan(user_id=user_id, weekday=2, title="Ноги"))
    await db.commit()
    planned_today = await smart(db, user_id, ReminderKind.WORKOUT, "18:00", six_pm)
    await dispatch(six_pm)
    assert (await reminder(db, planned_today)).status == ReminderStatus.SENT
    workout = keys.decrypt(fake.requests[-1].data)
    assert (workout["title"], workout["body"], workout["url"]) == (
        "Тренировка по плану",
        "Сегодня: Ноги. Ещё не начата",
        "/train",
    )

    # a dated rest day overrides the weekday plan
    db.add(WeeklyPlan(user_id=user_id, plan_date=date(2026, 7, 15), is_rest_day=True))
    await db.commit()
    rest = await smart(db, user_id, ReminderKind.WORKOUT, "18:00", six_pm)
    await dispatch(six_pm)
    assert (await reminder(db, rest)).last_error == "not_needed"
    await db.execute(WeeklyPlan.__table__.delete().where(WeeklyPlan.plan_date.is_not(None)))
    db.add(
        WorkoutLog(
            user_id=user_id,
            started_at=utc(2026, 7, 15, 10, 0),
            local_day=date(2026, 7, 15),
            status=WorkoutStatus.COMPLETED,
        )
    )
    await db.commit()
    done = await smart(db, user_id, ReminderKind.WORKOUT, "18:00", six_pm)
    await dispatch(six_pm)
    assert (await reminder(db, done)).last_error == "not_needed"
    assert len(fake.requests) == 1


async def test_evening_review_condition(ready, db) -> None:
    user_id, fake, keys = ready["user_id"], ready["push"], ready["keys"]
    evening = utc(2026, 7, 15, 14, 30)  # 21:30
    first = await smart(db, user_id, ReminderKind.EVENING_REVIEW, "21:30", evening)
    await dispatch(evening)
    assert (await reminder(db, first)).status == ReminderStatus.SENT
    review = keys.decrypt(fake.requests[-1].data)
    assert (review["title"], review["body"], review["url"]) == (
        "Итоги дня",
        "Как прошёл день? Заполните итоги",
        "/plan/journal",
    )

    db.add(JournalEntry(user_id=user_id, entry_date=date(2026, 7, 15), note_md="ok"))
    await db.commit()
    second = await smart(db, user_id, ReminderKind.EVENING_REVIEW, "21:30", evening)
    await dispatch(evening)
    assert (await reminder(db, second)).last_error == "not_needed"


async def test_streak_reminder_without_a_streak_sends_nothing(ready, db) -> None:
    """Stage 8 implements the rule (tests/test_stage8_streaks.py); no streak → nothing."""
    user_id = ready["user_id"]
    evening = utc(2026, 7, 15, 13, 0)  # 20:00 in Bangkok, the streak slot
    rid = await add_reminder(
        db, user_id, kind=ReminderKind.STREAK, remind_at=evening, payload={"date": "2026-07-15", "time": "20:00"}
    )
    await dispatch(evening)
    assert ((await reminder(db, rid)).status, (await reminder(db, rid)).last_error) == (
        ReminderStatus.CANCELLED,
        "not_needed",
    )
    assert ready["push"].requests == []


# --------------------------------------------------------------------------- #
# Safety, history, jobs
# --------------------------------------------------------------------------- #
FORBIDDEN = {
    "ru": (
        "меньше",
        "пропуст",
        "голод",
        "диет",
        "огранич",
        "лишн",
        "не ешь",
        "калори",
        "похуд",
        "сброс",
        "вес",
        "жир",
        "переед",
        "откаж",
    ),
    "en": (
        "less",
        "skip",
        "fast",
        "diet",
        "restrict",
        "cut",
        "hungry",
        "calorie",
        "lose",
        "weight",
        "fat",
        "burn",
        "overeat",
        "cheat",
        "binge",
    ),
}


def test_no_message_nudges_restriction() -> None:
    from app.services import notifications as n

    assert set(n.MESSAGES["ru"]) == set(n.MESSAGES["en"])
    rendered: dict[str, list[str]] = {lang: list(n.MESSAGES[lang].values()) for lang in n.MESSAGES}
    for lang in n.MESSAGES:
        for message in (
            n.meal_message(lang, None),
            n.meal_message(lang, "13:00"),
            n.water_message(lang, 900),
            n.workout_message(lang, "Ноги"),
            n.review_message(lang),
            n.test_message(lang),
        ):
            rendered[lang] += [message.title, message.body]
    for lang, texts in rendered.items():
        for text in texts:
            lowered = text.lower()
            hits = [word for word in FORBIDDEN[lang] if word in lowered]
            assert not hits, f"{lang}: {text!r} contains {hits}"
    # meal reminders are about logging, in both languages
    assert "запишите" in n.MESSAGES["ru"]["meal.first"].lower() and "log" in n.MESSAGES["en"]["meal.first"].lower()


async def test_history_is_scoped_and_bounded(ready, db, client) -> None:
    user_id = ready["user_id"]
    await add_reminder(db, user_id, task_id=await add_task(db, user_id, title="Отчёт", due_at=NOON), remind_at=NOON)
    await dispatch(NOON)
    items = (await ready["client"].get(N, params={"limit": 10})).json()["items"]
    assert len(items) == 1
    item = items[0]
    assert (item["kind"], item["title"], item["status"], item["quiet_hours_suppressed"], item["error"]) == (
        "task",
        "Отчёт",
        "sent",
        False,
        None,
    )
    assert datetime.fromisoformat(item["sent_at"]) == NOON == datetime.fromisoformat(item["scheduled_for"])
    assert item["url"].startswith("/plan?task=") and item["body"] == "Сейчас"
    other = await second_user(client)
    assert (await client.get(N, headers={"Authorization": f"Bearer {other}"})).json()["items"] == []
    for bad in (0, 201, "x"):
        assert (await ready["client"].get(N, params={"limit": bad})).status_code == 422


async def test_jobs_are_registered_and_run(ready, db) -> None:
    from app.services.hooks import registered_job_ids
    from app.services.scheduler import build_scheduler, run_job_now

    assert {"dispatch_notifications", "plan_daily_reminders"} <= set(registered_job_ids())
    jobs = {job.id: job for job in build_scheduler().get_jobs()}
    assert str(jobs["dispatch_notifications"].trigger) == "interval[0:01:00]"
    assert str(jobs["plan_daily_reminders"].trigger) == "interval[0:15:00]"
    assert await run_job_now("plan_daily_reminders") is True
    assert await run_job_now("dispatch_notifications") is True


def test_cli_vapid_keys_prints_a_working_pair(monkeypatch) -> None:
    """`python -m app.cli vapid-keys` → .env lines the server can sign with (and the browser can use)."""
    from typer.testing import CliRunner

    from app.cli import cli
    from app.core.config import get_settings
    from app.services import push

    result = CliRunner().invoke(cli, ["vapid-keys"])
    assert result.exit_code == 0, result.output
    lines = dict(line.split("=", 1) for line in result.output.splitlines() if line.startswith("VAPID_"))
    public, private = lines["VAPID_PUBLIC_KEY"], lines["VAPID_PRIVATE_KEY"]
    assert "\n" not in private and private.startswith("-----BEGIN PRIVATE KEY-----\\n")  # one .env line
    raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    assert len(raw) == 65 and raw[0] == 4  # the browser's applicationServerKey
    monkeypatch.setattr(get_settings(), "vapid_public_key", public)
    monkeypatch.setattr(get_settings(), "vapid_private_key", private)
    key = push.vapid()
    signed = key.sign({"sub": "mailto:owner@example.com", "aud": "https://push.example.test"})
    assert Vapid02.verify(signed["Authorization"]) and signed["Authorization"].endswith(f"k={public}")


async def test_a_failing_planner_never_blocks_delivery(ready, db, monkeypatch) -> None:
    from app.services import notifications

    async def broken(now=None) -> int:
        raise RuntimeError("planner down")

    monkeypatch.setattr(notifications, "plan_daily_reminders", broken)
    monkeypatch.setattr(notifications, "_last_planned_at", None)
    await set_settings(db, ready["user_id"], quiet_hours_enabled=False)
    now = datetime.now(UTC)
    rid = await add_reminder(
        db, ready["user_id"], task_id=await add_task(db, ready["user_id"], due_at=now), remind_at=now
    )
    await notifications.dispatch_job()  # the scheduler entry point: plans first (fails), then delivers
    assert (await reminder(db, rid)).status == ReminderStatus.SENT
    assert notifications._last_planned_at is not None  # no retry storm: the planner waits for its own schedule
