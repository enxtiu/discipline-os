"""Helpers shared by the stage-7 (web push, notifications) tests.

No internet: the push service is either ``FakePushService`` (an ``httpx`` transport
installed as ``push._transport_override``) or ``LocalPushService`` (a real HTTP
server on 127.0.0.1, reached through the real transport — development accepts
``http://`` loopback endpoints). The fixtures are registered under their short
names (``vapid``, ``push_service``, ``ready``): a test module imports
``vapid_fixture, push_service_fixture, ready_fixture`` and asks for them by name.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time as clock
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import http_ece
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02
from sqlalchemy import select

from app.models import NotificationLog, Reminder, ReminderKind, ReminderStatus, Task, UserSettings

P = "/api/v1/push"
N = "/api/v1/notifications"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


NOON = utc(2026, 7, 15, 5, 0)  # 12:00 in Asia/Bangkok (UTC+7, no DST)


# --------------------------------------------------------------------------- #
# The browser and the push service
# --------------------------------------------------------------------------- #
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


@dataclass
class Captured:
    url: str
    data: bytes
    headers: dict[str, str]
    timeout: Any


class FakePushService(httpx.AsyncBaseTransport):
    """The push service as an ``httpx`` transport: records every POST, answers 201 unless told otherwise.

    ``statuses[url]`` queues answers for one endpoint, ``errors[url]`` raises instead
    (e.g. ``httpx.ConnectError``), ``delay`` keeps every request waiting that long,
    ``before_answer`` runs (once per request) while the request is in flight.
    """

    def __init__(self) -> None:
        self.requests: list[Captured] = []
        self.statuses: dict[str, list[int]] = {}
        self.default_status = 201
        self.errors: dict[str, Exception] = {}
        self.delay = 0.0
        self.delays: dict[str, float] = {}
        self.before_answer: Callable[[str], None] | None = None
        self.answered_at: dict[str, float] = {}
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def _book(self, url: str, data: bytes, headers: dict[str, str], timeout: Any) -> int:
        with self._lock:
            self.requests.append(Captured(url, data, {k.lower(): str(v) for k, v in headers.items()}, timeout))
            queue = self.statuses.get(url)
            status = queue.pop(0) if queue else self.default_status
        if self.before_answer is not None:
            self.before_answer(url)
        return status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        data = await request.aread()
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            pause = self.delays.get(url, self.delay)
            if pause:
                await asyncio.sleep(pause)
            status = self._book(url, data, dict(request.headers), request.extensions.get("timeout"))
            self.answered_at[url] = clock.monotonic()
            if url in self.errors:
                raise self.errors[url]
            return httpx.Response(status, request=request)
        finally:
            self.in_flight -= 1

    def to(self, endpoint: str) -> list[Captured]:
        return [req for req in self.requests if req.url == endpoint]


@dataclass
class Hit:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    at: float  # time.monotonic() when the request arrived


Route = Callable[[BaseHTTPRequestHandler, "LocalPushService"], None]


class LocalPushService:
    """A real HTTP server on 127.0.0.1 (reached through the real push transport).

    ``routes[path]`` decides the answer (default: 201). Waiting routes wake up when the
    server stops, so no thread outlives the test.
    """

    def __init__(self) -> None:
        self.hits: list[Hit] = []
        self.routes: dict[str, Route] = {}
        self.stopping = threading.Event()
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                outer._handle(self, "POST")

            def do_GET(self) -> None:
                outer._handle(self, "GET")

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> LocalPushService:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stopping.set()
        self.server.shutdown()
        self.server.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def hits_to(self, path: str) -> list[Hit]:
        return [hit for hit in self.hits if hit.path == path]

    def _handle(self, request: BaseHTTPRequestHandler, method: str) -> None:
        length = int(request.headers.get("Content-Length") or 0)
        body = request.rfile.read(length) if length else b""
        hit = Hit(method, request.path, {k.lower(): v for k, v in request.headers.items()}, body, clock.monotonic())
        with self._lock:
            self.hits.append(hit)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.routes.get(request.path, answer(201))(request, self)
        except (BrokenPipeError, ConnectionResetError):  # the client gave up (deadline): fine
            pass
        finally:
            with self._lock:
                self.in_flight -= 1


def answer(status: int, headers: dict[str, str] | None = None) -> Route:
    def route(request: BaseHTTPRequestHandler, server: LocalPushService) -> None:
        request.send_response(status)
        for key, value in (headers or {}).items():
            request.send_header(key, value)
        request.send_header("Content-Length", "0")
        request.end_headers()

    return route


def slow(seconds: float, status: int = 201) -> Route:
    """Answers after ``seconds`` (or when the server stops)."""

    def route(request: BaseHTTPRequestHandler, server: LocalPushService) -> None:
        server.stopping.wait(seconds)
        answer(status)(request, server)

    return route


def trickle(every: float, size: int) -> Route:
    """201 with a ``size``-byte body sent one byte every ``every`` seconds: each read is quick,
    the whole answer is not."""

    def route(request: BaseHTTPRequestHandler, server: LocalPushService) -> None:
        request.send_response(201)
        request.send_header("Content-Length", str(size))
        request.end_headers()
        for _ in range(size):
            if server.stopping.wait(every):
                return
            request.wfile.write(b"x")
            request.wfile.flush()

    return route


# --------------------------------------------------------------------------- #
# Fixtures (import them by these names; tests ask for vapid / push_service / ready)
# --------------------------------------------------------------------------- #
@pytest.fixture(name="vapid")
def vapid_fixture(monkeypatch) -> Vapid02:
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


@pytest.fixture(name="push_service")
def push_service_fixture(monkeypatch) -> FakePushService:
    from app.services import push

    fake = FakePushService()
    monkeypatch.setattr(push, "_transport_override", fake)
    return fake


@pytest.fixture(name="ready")
async def ready_fixture(authed: httpx.AsyncClient, vapid, push_service: FakePushService, db) -> dict[str, Any]:
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


# --------------------------------------------------------------------------- #
# API and database helpers
# --------------------------------------------------------------------------- #
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


async def other_user(client: httpx.AsyncClient, name: str = "Second") -> tuple[uuid.UUID, str]:
    """Another account with its own paired device: ``(user id, device token)``."""
    from app.db import SessionLocal
    from app.models import User
    from app.services.pairing import create_pairing_code
    from app.services.settings_service import get_or_create_settings

    async with SessionLocal() as session:
        user = User(display_name=name, is_owner=False)
        session.add(user)
        await session.flush()
        await get_or_create_settings(session, user)
        issued = await create_pairing_code(session, user, created_by="test")
        await session.commit()
        user_id = user.id
    response = await client.post(
        "/api/v1/auth/pair",
        json={
            "code": issued.code,
            "device": {"name": f"{name} phone", "platform": "ios", "fingerprint": f"fp-{name.lower()}"},
        },
    )
    assert response.status_code == 200, response.text
    return user_id, response.json()["token"]


async def second_user(client: httpx.AsyncClient) -> str:
    """Another account with its own paired device; returns its device token."""
    return (await other_user(client))[1]
