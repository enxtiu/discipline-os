"""Stage 7 review fixes (H1, M1, M5, L1, L5, L8): regression tests.

Each section reproduces one finding of the stage 6–7 review: what used to happen
is described in the test's docstring; the assertions are what must hold now.
Transport-level checks talk to a real HTTP server on 127.0.0.1 through the real
push transport (``LocalPushService``); the rest use the ``FakePushService`` double.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.models import PushSubscription
from tests.conftest import TEST_DSN
from tests.stage7_helpers import (  # noqa: F401  (fixtures: vapid, push_service, ready)
    NOON,
    BrowserKeys,
    FakePushService,
    LocalPushService,
    N,
    P,
    add_reminder,
    add_task,
    answer,
    b64u,
    dispatch,
    endpoint,
    log_rows,
    other_user,
    owner_id,
    push_service_fixture,
    ready_fixture,
    reminder,
    set_settings,
    slow,
    subscribe,
    trickle,
    utc,
    vapid_fixture,
)

pytestmark = pytest.mark.anyio

SYNC_DSN = TEST_DSN.replace("postgresql+psycopg://", "postgresql://")


def message(**values: Any):
    from app.services import push

    fields = {"title": "Зал", "body": "Сейчас", "url": "/today", "tag": "x", "kind": "test"} | values
    return push.PushMessage(**fields)


async def subscription(db, url: str) -> PushSubscription:
    db.expire_all()
    return (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == url))).scalar_one()


async def quiet_off(db, *user_ids: uuid.UUID) -> None:
    for user_id in user_ids:
        await set_settings(db, user_id, quiet_hours_enabled=False)


def trickle_head(every: float, size: int):
    """The status line at once, then a header one byte every ``every`` seconds: every read
    is quick, the answer never completes in time."""

    def route(request, server: LocalPushService) -> None:
        request.wfile.write(b"HTTP/1.1 201 Created\r\nX-Slow: ")
        request.wfile.flush()
        for _ in range(size):
            if server.stopping.wait(every):
                return
            request.wfile.write(b"x")
            request.wfile.flush()
        request.wfile.write(b"\r\nContent-Length: 0\r\n\r\n")
        request.wfile.flush()

    return route


# --------------------------------------------------------------------------- #
# H1 — SSRF: the server only ever POSTs to real push services (outside development)
# --------------------------------------------------------------------------- #
SSRF_ENDPOINTS = [
    "https://2130706433/push",  # 127.0.0.1 as one decimal number
    "https://0x7f000001/push",  # … in hex
    "https://127.1/push",  # … shortened
    "https://0/push",  # 0.0.0.0
    "https://localhost./push",  # trailing dot
    "https://postgres:5432/push",  # compose service names
    "https://api:8000/push",
    "https://metadata.internal/computeMetadata/v1/",  # any DNS name may resolve to a private address
    "https://169.254.169.254/latest/meta-data/",
    "https://[::1]/push",
    "https://fcm.googleapis.com.attacker.example/fcm/send/abc",
    "https://evil-fcm.googleapis.com/fcm/send/abc",
    "https://push.apple.com/abc",  # the bare suffix is not a push host
    "https://fcm.googleapis.com:8443/fcm/send/abc",  # not port 443
    "http://fcm.googleapis.com/fcm/send/abc",  # not https
    "https://@fcm.googleapis.com/fcm/send/abc",  # (empty) credentials
    "https://fcm.googleapis.com\\@evil.example/fcm/send/abc",  # parser confusion
]
PUSH_SERVICE_ENDPOINTS = [
    "https://fcm.googleapis.com/fcm/send/dXJ1:APA91bH-abc_DEF",  # Chrome, Android, Samsung Internet, Opera
    "https://fcm.googleapis.com:443/wp/dXJ1-abc",
    "https://web.push.apple.com/QGFi-rT2cZ0c_dummy",  # Safari (iOS 16.4+, macOS)
    "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABk-dummy",  # Firefox
    "https://wns2-par02p.notify.windows.com/w/?token=AgYAAAB%2bdummy%3d",  # Edge on Windows
]


@pytest.mark.parametrize("env", ["production", "staging"])
async def test_outside_development_only_known_push_services_are_accepted(
    authed: httpx.AsyncClient, vapid, monkeypatch, env: str
) -> None:
    """Before: only IP literals were checked — integer/hex/short IPs, compose names and any DNS
    name passed (blind SSRF with a status oracle)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "env", env)
    for url in SSRF_ENDPOINTS:
        response = await subscribe(authed, url)
        assert response.status_code == 422, (url, response.text)
        # malformed → validation_error; well-formed but not a push service → its own code
        assert response.json()["error"]["code"] in {"validation_error", "push_service_not_allowed"}, url
    refused = await subscribe(authed, "https://metadata.internal/computeMetadata/v1/")
    assert refused.json()["error"] == {
        "code": "push_service_not_allowed",
        "message": "This server does not send notifications to metadata.internal: endpoint is not a known push service",
        "details": {"host": "metadata.internal"},
    }
    for url in PUSH_SERVICE_ENDPOINTS:
        response = await subscribe(authed, url)
        assert response.status_code == 201, (url, response.text)
        assert response.json()["endpoint_host"] == url.split("/")[2].split(":")[0]


async def test_admin_can_allow_another_push_service(authed: httpx.AsyncClient, vapid, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "env", "production")
    assert (await subscribe(authed, "https://push.example.org/v1/abc")).status_code == 422
    monkeypatch.setattr(get_settings(), "push_extra_hosts", ["push.example.org", "*.push.example.net"])
    assert (await subscribe(authed, "https://push.example.org/v1/abc")).status_code == 201
    assert (await subscribe(authed, "https://eu-1.push.example.net/v1/abc")).status_code == 201
    for still_refused in ("https://push.example.net/v1/abc", "http://push.example.org/v1/abc"):
        assert (await subscribe(authed, still_refused)).status_code == 422, still_refused


async def test_development_keeps_the_loopback_receiver(authed: httpx.AsyncClient, vapid) -> None:
    """The smoke test and the browser checks post to a local receiver over plain http."""
    for url in ("http://127.0.0.1:8099/push/smoke", "http://localhost:8099/push/ui", "http://[::1]:8099/push/v6"):
        response = await subscribe(authed, url)
        assert response.status_code == 201, (url, response.text)
    assert (await subscribe(authed, "http://192.168.1.10:8099/push")).status_code == 422  # loopback only
    assert (await subscribe(authed, "ftp://127.0.0.1/push")).status_code == 422


# --------------------------------------------------------------------------- #
# M1 — a slow push service never stalls anybody else
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# M5 — a push never waits at the push service into quiet hours or past staleness
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# L1 — endpoints are ASCII and fit the unique index; no statement parameters in errors
# --------------------------------------------------------------------------- #
async def test_multibyte_endpoint_is_a_422_not_a_500(authed: httpx.AsyncClient, vapid) -> None:
    """Before: 2 000 CJK characters passed the 2 048-character limit, the 6 KB value broke the
    unique btree index → 500 (and the log line carried the endpoint and the keys)."""
    path = "".join(chr(0x4E00 + n % 500) for n in range(2000))
    for url in ("https://fcm.googleapis.com/fcm/send/" + path, "https://push.example.test/ä"):
        response = await subscribe(authed, url)
        assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error"), url[:40]


async def test_endpoint_length_is_counted_in_bytes(authed: httpx.AsyncClient, vapid) -> None:
    from app.schemas.notifications import MAX_ENDPOINT_BYTES

    base = "https://push.example.test/send/"
    fits = base + "a" * (MAX_ENDPOINT_BYTES - len(base))
    assert (await subscribe(authed, fits)).status_code == 201
    assert (await subscribe(authed, fits + "a")).status_code == 422


async def test_database_errors_do_not_carry_statement_parameters(db) -> None:
    from app.db import engine

    assert engine.sync_engine.hide_parameters is True
    secret = f"https://push.example.test/secret-{uuid.uuid4().hex}"
    p256dh = b64u(b"\x04" + bytes(range(64)))
    with pytest.raises(sa.exc.IntegrityError) as caught:
        await db.execute(
            sa.insert(PushSubscription).values(
                user_id=uuid.uuid4(),  # no such user: a foreign-key violation
                endpoint=secret,
                p256dh=p256dh,
                auth="c2VjcmV0LWF1dGgtMTIzNA",
            )
        )
    await db.rollback()
    text = str(caught.value)
    assert secret not in text and p256dh not in text and "c2VjcmV0LWF1dGgtMTIzNA" not in text
    assert "push_subscriptions" in text  # still says what failed


# --------------------------------------------------------------------------- #
# L5 — a subscription deleted while a push is in flight
# --------------------------------------------------------------------------- #
def delete_during_send(fake: FakePushService, victim: str) -> None:
    done = {"once": False}

    def before_answer(url: str) -> None:
        if not done["once"]:
            done["once"] = True
            with psycopg.connect(SYNC_DSN, autocommit=True) as conn:
                conn.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (victim,))

    fake.before_answer = before_answer


# --------------------------------------------------------------------------- #
# L8 — a subscription the push service keeps refusing (VAPID keys changed) is retired
# --------------------------------------------------------------------------- #
async def deliver_once(db, user_id: uuid.UUID):
    from app.services import push

    report = await push.deliver(db, await push.active_subscriptions(db, user_id), message())
    await db.commit()
    return report
