"""Stage 7 review fixes (H1, M1, M5, L1, L5, L8): regression tests.

Each section reproduces one finding of the stage 6–7 review: what used to happen
is described in the test's docstring; the assertions are what must hold now.
Transport-level checks talk to a real HTTP server on 127.0.0.1 through the real
push transport (``LocalPushService``); the rest use the ``FakePushService`` double.
"""

from __future__ import annotations

import time as clock
import uuid
from datetime import time, timedelta
from typing import Any

import httpx
import psycopg
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from sqlalchemy import select

from app.models import Device, PushSubscription, ReminderKind, ReminderStatus
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


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_sender_never_follows_redirects(authed: httpx.AsyncClient, vapid, db, status: int) -> None:
    """Before: ``requests`` followed a 3xx from the push service to any internal URL."""
    from app.services import push

    with LocalPushService() as local:
        local.routes["/push/redirect"] = answer(status, {"Location": local.url("/internal/admin")})
        url = local.url("/push/redirect")
        assert (await subscribe(authed, url)).status_code == 201
        subs = await push.active_subscriptions(db, await owner_id(db))
        report = await push.deliver(db, subs, message())
        await db.commit()
        assert [hit.path for hit in local.hits] == ["/push/redirect"]  # nothing reached /internal/admin
    result = report.results[0]
    assert (result.outcome, result.status_code, result.error) == (push.Outcome.PERMANENT, status, f"http_{status}")
    assert (await subscription(db, url)).is_active is True  # the message failed, not the subscription


async def test_sender_rechecks_stored_endpoints_before_posting(
    authed: httpx.AsyncClient, vapid, db, monkeypatch
) -> None:
    """A row stored before the policy (or under another ENV) is never posted to again."""
    from app.core.config import get_settings
    from app.services import push

    with LocalPushService() as local:
        url = local.url("/internal/admin")
        assert (await subscribe(authed, url)).status_code == 201  # development: loopback receiver
        monkeypatch.setattr(get_settings(), "env", "production")
        subs = await push.active_subscriptions(db, await owner_id(db))
        report = await push.deliver(db, subs, message())
        await db.commit()
        assert local.hits == []
    assert (report.sent, report.deactivated) == (0, 1)
    assert report.results[0].error == "endpoint_not_allowed"
    row = await subscription(db, url)
    assert (row.is_active, row.last_error) == (False, "endpoint_not_allowed")
    device = (await db.execute(select(Device).where(Device.id == row.device_id))).scalar_one()
    assert device.push_enabled is False


# --------------------------------------------------------------------------- #
# M1 — a slow push service never stalls anybody else
# --------------------------------------------------------------------------- #
async def test_a_slow_answer_is_cut_off_at_the_send_deadline(authed: httpx.AsyncClient, vapid, db, monkeypatch) -> None:
    """Before: the 10 s timeout was per socket read, so a trickling answer took as long as it liked."""
    from app.services import push

    monkeypatch.setattr(push, "SEND_DEADLINE_SECONDS", 1.0, raising=False)
    with LocalPushService() as local:
        local.routes["/push/trickle"] = trickle_head(every=0.4, size=20)  # 8 s, each read well under 10 s
        url = local.url("/push/trickle")
        assert (await subscribe(authed, url)).status_code == 201
        subs = await push.active_subscriptions(db, await owner_id(db))
        started = clock.monotonic()
        report = await push.deliver(db, subs, message())
        took = clock.monotonic() - started
        await db.commit()
    assert (report.results[0].outcome, report.results[0].error) == (push.Outcome.TRANSIENT, "timeout")
    assert took < 4.0, f"the send took {took:.1f} s with a 1 s deadline"
    row = await subscription(db, url)
    assert (row.is_active, row.failure_count, row.last_error) == (True, 1, "timeout")


async def test_the_answer_body_is_never_waited_for(authed: httpx.AsyncClient, vapid, db) -> None:
    """201 is all a push service has to say; a body trickled after it does not hold the send."""
    from app.services import push

    with LocalPushService() as local:
        local.routes["/push/body"] = trickle(every=0.4, size=20)
        url = local.url("/push/body")
        assert (await subscribe(authed, url)).status_code == 201
        subs = await push.active_subscriptions(db, await owner_id(db))
        started = clock.monotonic()
        report = await push.deliver(db, subs, message())
        took = clock.monotonic() - started
        await db.commit()
    assert report.sent == 1
    assert took < 4.0, f"the send took {took:.1f} s"


async def test_one_slow_push_service_does_not_hold_up_other_users(
    authed: httpx.AsyncClient, client: httpx.AsyncClient, vapid, db, monkeypatch
) -> None:
    """Before: reminders went out one after another, so the next user waited for the slow one."""
    from app.services import push

    monkeypatch.setattr(push, "SEND_DEADLINE_SECONDS", 1.5, raising=False)
    with LocalPushService() as local:
        local.routes["/push/slow"] = slow(30)
        assert (await subscribe(authed, local.url("/push/slow"))).status_code == 201
        other, token = await other_user(client, "Other")
        response = await client.post(
            f"{P}/subscriptions",
            headers={"Authorization": f"Bearer {token}"},
            json={"endpoint": local.url("/push/fast"), "keys": BrowserKeys.new().json()},
        )
        assert response.status_code == 201, response.text
        me = await owner_id(db)
        await quiet_off(db, me, other)
        await db.execute(sa.text("DELETE FROM reminders"))  # smart rows planned on subscribe
        await db.commit()
        slow_one = await add_reminder(
            db, me, kind=ReminderKind.CUSTOM, title="slow", remind_at=NOON - timedelta(minutes=2)
        )
        fast_one = await add_reminder(
            db, other, kind=ReminderKind.CUSTOM, title="fast", remind_at=NOON - timedelta(minutes=1)
        )
        started = clock.monotonic()
        report = await dispatch(NOON)
        took = clock.monotonic() - started
        fast_hits = local.hits_to("/push/fast")
    assert len(fast_hits) == 1
    assert fast_hits[0].at - started < 3.0, f"the other user's push left after {fast_hits[0].at - started:.1f} s"
    assert took < 8.0, f"the run took {took:.1f} s with a 1.5 s deadline"
    assert (report.get("sent"), report.get("retrying")) == (1, 1)
    assert (await reminder(db, fast_one)).status == ReminderStatus.SENT
    slow_row = await reminder(db, slow_one)
    assert (slow_row.status, slow_row.attempts, slow_row.last_error) == (
        ReminderStatus.PENDING,
        1,
        "retrying:timeout",
    )


async def test_the_reminder_row_is_not_locked_while_the_push_service_answers(
    authed: httpx.AsyncClient, vapid, db
) -> None:
    """Before: the row lock (FOR UPDATE) was held during the POST, so editing or deleting the task
    blocked for as long as the push service took."""
    with LocalPushService() as local:
        seen: dict[str, Any] = {}

        def check_lock(request, server: LocalPushService) -> None:
            with psycopg.connect(SYNC_DSN, autocommit=True) as conn:
                try:
                    with conn.transaction():
                        conn.execute("SELECT id FROM reminders WHERE id = %s FOR UPDATE NOWAIT", (seen["id"],))
                        conn.execute("UPDATE tasks SET title = title WHERE id = %s", (seen["task"],))
                    seen["locked"] = False
                except psycopg.errors.LockNotAvailable:
                    seen["locked"] = True
            answer(201)(request, server)

        local.routes["/push/lock"] = check_lock
        assert (await subscribe(authed, local.url("/push/lock"))).status_code == 201
        me = await owner_id(db)
        await quiet_off(db, me)
        seen["task"] = await add_task(db, me, title="Позвонить", due_at=NOON)
        seen["id"] = await add_reminder(db, me, task_id=seen["task"], remind_at=NOON)
        report = await dispatch(NOON)
    assert report.get("sent") == 1
    assert seen["locked"] is False
    assert (await reminder(db, seen["id"])).status == ReminderStatus.SENT


async def test_dispatch_serves_users_concurrently_within_a_bound(
    client: httpx.AsyncClient, vapid, db, monkeypatch
) -> None:
    from app.services import notifications

    monkeypatch.setattr(notifications, "DISPATCH_CONCURRENCY", 2, raising=False)
    with LocalPushService() as local:
        users = []
        for n in range(4):
            path = f"/push/u{n}"
            local.routes[path] = slow(2.0)  # long enough for two sends to overlap on a busy machine
            user_id, token = await other_user(client, f"User{n}")
            response = await client.post(
                f"{P}/subscriptions",
                headers={"Authorization": f"Bearer {token}"},
                json={"endpoint": local.url(path), "keys": BrowserKeys.new().json()},
            )
            assert response.status_code == 201, response.text
            users.append(user_id)
        await quiet_off(db, *users)
        await db.execute(sa.text("DELETE FROM reminders"))
        await db.commit()
        for user_id in users:
            await add_reminder(db, user_id, kind=ReminderKind.CUSTOM, title="go", remind_at=NOON)
        report = await dispatch(NOON)
        peak = local.max_in_flight
    assert report.get("sent") == 4
    assert peak == 2, f"{peak} pushes were in flight at once"


async def test_active_subscriptions_are_capped_per_user(authed: httpx.AsyncClient, vapid, db) -> None:
    from app.services import push

    cap = push.MAX_ACTIVE_SUBSCRIPTIONS
    urls = [endpoint(n) for n in range(cap + 2)]
    for url in urls:
        assert (await subscribe(authed, url)).status_code == 201
    items = (await authed.get(f"{P}/subscriptions")).json()["items"]
    assert sum(item["is_active"] for item in items) == cap
    for oldest in urls[:2]:
        row = await subscription(db, oldest)
        assert (row.is_active, row.last_error) == (False, "subscription_limit")
    assert (await authed.get(f"{N}/prefs")).json()["active_subscriptions"] == cap
    assert (await authed.get("/api/v1/devices")).json()["devices"][0]["push_enabled"] is True

    # re-subscribing an old browser makes it the newest: the oldest active one gives way
    assert (await subscribe(authed, urls[0])).status_code == 201
    assert (await subscription(db, urls[0])).is_active is True
    assert (await subscription(db, urls[2])).last_error == "subscription_limit"

    # rows stored before the cap still fan out to at most `cap` endpoints, the newest first
    await db.execute(sa.update(PushSubscription).values(is_active=True))
    await db.commit()
    live = await push.active_subscriptions(db, await owner_id(db))
    assert len(live) == cap


# --------------------------------------------------------------------------- #
# M5 — a push never waits at the push service into quiet hours or past staleness
# --------------------------------------------------------------------------- #
async def test_task_reminder_expires_before_the_quiet_hours(ready, db) -> None:
    """Before: every reminder had a 12 h TTL — a phone offline at 22:30 got it at 03:00."""
    user_id, fake, keys = ready["user_id"], ready["push"], ready["keys"]
    at = utc(2026, 7, 15, 15, 30)  # 22:30 Bangkok; quiet hours from 23:00
    task_id = await add_task(db, user_id, title="Таблетки", due_at=at + timedelta(minutes=15))
    await add_reminder(db, user_id, task_id=task_id, remind_at=at)
    assert (await dispatch(at)).get("sent") == 1
    sent = fake.to(ready["endpoint"])[0]
    assert sent.headers["ttl"] == "1800"
    payload = keys.decrypt(sent.data)
    assert (payload["sent_at"], payload["expires_at"]) == ("2026-07-15T15:30:00Z", "2026-07-15T16:00:00Z")


async def test_reminder_ttl_follows_staleness_and_kind(ready, db) -> None:
    user_id, fake = ready["user_id"], ready["push"]

    # on time, quiet hours far away: the hour a reminder stays relevant
    on_time = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON)
    await dispatch(NOON)
    assert (await reminder(db, on_time)).status == ReminderStatus.SENT
    assert fake.requests[-1].headers["ttl"] == "3600"

    # 50 minutes late (the server was down): 10 minutes left before it is stale
    late = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON - timedelta(minutes=50))
    await dispatch(NOON)
    assert (await reminder(db, late)).status == ReminderStatus.SENT
    assert fake.requests[-1].headers["ttl"] == "600"

    # a daily nudge is worth less: half an hour, and still not into the quiet hours
    review = utc(2026, 7, 15, 14, 30)  # 21:30
    await add_reminder(
        db,
        user_id,
        kind=ReminderKind.EVENING_REVIEW,
        remind_at=review,
        payload={"date": "2026-07-15", "time": "21:30"},
        dedupe="evening_review:2026-07-15",
    )
    await dispatch(review)
    assert fake.requests[-1].headers["ttl"] == "1800"
    await ready["client"].put(f"{N}/prefs", json={"evening_review_time": "22:40"})
    later = utc(2026, 7, 15, 15, 40)  # 22:40, twenty minutes before the quiet hours
    await add_reminder(
        db,
        user_id,
        kind=ReminderKind.EVENING_REVIEW,
        remind_at=later,
        payload={"date": "2026-07-15", "time": "22:40"},
        dedupe="evening_review:2026-07-15:late",
    )
    await dispatch(later)
    assert fake.requests[-1].headers["ttl"] == "1200"

    # quiet hours off: only staleness counts
    await set_settings(db, user_id, quiet_hours_enabled=False)
    night = utc(2026, 7, 15, 16, 30)  # 23:30
    await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=night)
    await dispatch(night)
    assert fake.requests[-1].headers["ttl"] == "3600"
    assert len(fake.requests) == 5


async def test_test_push_keeps_its_minute_and_never_expires_in_the_service_worker(ready) -> None:
    fake, keys = ready["push"], ready["keys"]
    assert (await ready["client"].post(f"{P}/test")).json()["sent"] == 1
    sent = fake.to(ready["endpoint"])[0]
    assert sent.headers["ttl"] == "60"
    payload = keys.decrypt(sent.data)
    assert "sent_at" in payload and "expires_at" not in payload  # the user is waiting for it


def test_next_quiet_hours_start() -> None:
    from app.services.notifications import QuietHours

    night = QuietHours(True, time(23, 0), time(7, 0))
    assert night.next_start(utc(2026, 7, 15, 5, 0), "Asia/Bangkok") == utc(2026, 7, 15, 16, 0)  # 12:00 → 23:00
    assert night.next_start(utc(2026, 7, 15, 16, 30), "Asia/Bangkok") == utc(2026, 7, 16, 16, 0)  # inside → next
    siesta = QuietHours(True, time(13, 0), time(15, 0))
    assert siesta.next_start(utc(2026, 7, 15, 9, 0), "Asia/Bangkok") == utc(2026, 7, 16, 6, 0)  # 16:00 → tomorrow
    # DST: 23:00 in Berlin is 21:00 UTC in summer, 22:00 UTC after the clocks go back
    assert night.next_start(utc(2026, 10, 24, 12, 0), "Europe/Berlin") == utc(2026, 10, 24, 21, 0)
    assert night.next_start(utc(2026, 10, 25, 12, 0), "Europe/Berlin") == utc(2026, 10, 25, 22, 0)
    assert QuietHours(False, time(23, 0), time(7, 0)).next_start(NOON, "Asia/Bangkok") is None
    assert QuietHours(True, time(8, 0), time(8, 0)).next_start(NOON, "Asia/Bangkok") is None


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


async def test_a_subscription_deleted_mid_send_does_not_resend_the_reminder(
    authed: httpx.AsyncClient, vapid, push_service: FakePushService, db
) -> None:
    """Before: booking the result hit StaleDataError, the reminder rolled back and the next run
    sent it to the other device again."""
    phone, laptop = endpoint("phone"), endpoint("laptop")
    assert (await subscribe(authed, phone)).status_code == 201
    assert (await subscribe(authed, laptop)).status_code == 201
    me = await owner_id(db)
    await quiet_off(db, me)
    await db.execute(sa.text("DELETE FROM reminders"))
    await db.commit()
    delete_during_send(push_service, victim=phone)
    rid = await add_reminder(db, me, kind=ReminderKind.CUSTOM, title="x", remind_at=NOON - timedelta(minutes=1))

    first = await dispatch(NOON)
    assert (first.get("sent"), first.get("error")) == (1, 0)
    assert (await reminder(db, rid)).status == ReminderStatus.SENT
    logs = await log_rows(db, me)
    assert [row.status for row in logs] == ["sent"]
    assert logs[0].subscription_id in (None, (await subscription(db, laptop)).id)

    second = await dispatch(NOON + timedelta(minutes=1))
    assert second.total == 0
    assert len(push_service.to(laptop)) == 1


async def test_push_test_survives_a_subscription_deleted_mid_send(
    authed: httpx.AsyncClient, vapid, push_service: FakePushService, db
) -> None:
    phone, laptop = endpoint("phone"), endpoint("laptop")
    assert (await subscribe(authed, phone)).status_code == 201
    assert (await subscribe(authed, laptop)).status_code == 201
    delete_during_send(push_service, victim=phone)
    response = await authed.post(f"{P}/test")
    assert response.status_code == 200, response.text
    assert response.json()["sent"] == 2
    items = (await authed.get(f"{P}/subscriptions")).json()["items"]
    assert [(item["is_active"], item["last_success_at"] is not None) for item in items] == [(True, True)]


# --------------------------------------------------------------------------- #
# L8 — a subscription the push service keeps refusing (VAPID keys changed) is retired
# --------------------------------------------------------------------------- #
async def deliver_once(db, user_id: uuid.UUID):
    from app.services import push

    report = await push.deliver(db, await push.active_subscriptions(db, user_id), message())
    await db.commit()
    return report


async def test_repeated_vapid_rejections_retire_the_subscription(
    authed: httpx.AsyncClient, vapid, push_service: FakePushService, db
) -> None:
    """Before: after a VAPID key change every push got 401/403 forever and the subscription stayed
    active, so the settings screen never asked to turn notifications on again."""
    url = endpoint()
    assert (await subscribe(authed, url)).status_code == 201
    me = await owner_id(db)
    push_service.statuses[url] = [403, 401, 403]
    for attempt, expected in enumerate(
        [(True, "vapid_rejected:1"), (True, "vapid_rejected:2"), (False, "vapid_rejected")], start=1
    ):
        report = await deliver_once(db, me)
        assert report.results[0].error == f"http_{[403, 401, 403][attempt - 1]}"
        row = await subscription(db, url)
        assert (row.is_active, row.last_error, row.failure_count) == (*expected, attempt)
    assert (await deliver_once(db, me)).results == []  # retired: nothing is sent any more
    items = (await authed.get(f"{P}/subscriptions")).json()["items"]
    assert [(item["is_active"], item["last_error"]) for item in items] == [(False, "vapid_rejected")]
    assert (await authed.get("/api/v1/devices")).json()["devices"][0]["push_enabled"] is False
    assert (await authed.get(f"{N}/prefs")).json()["active_subscriptions"] == 0

    # the device turns notifications on again (the browser re-subscribes): back in service
    assert (await subscribe(authed, url)).status_code == 201
    row = await subscription(db, url)
    assert (row.is_active, row.last_error, row.failure_count) == (True, None, 0)


async def test_vapid_rejection_streak_resets_on_any_other_answer(
    authed: httpx.AsyncClient, vapid, push_service: FakePushService, db
) -> None:
    url = endpoint()
    assert (await subscribe(authed, url)).status_code == 201
    me = await owner_id(db)
    push_service.statuses[url] = [403, 201, 403, 503, 403, 403, 403]
    for _ in range(6):
        await deliver_once(db, me)
    row = await subscription(db, url)
    assert (row.is_active, row.last_error) == (True, "vapid_rejected:2")
    await deliver_once(db, me)
    assert (await subscription(db, url)).is_active is False


async def test_vapid_rejections_are_not_blamed_on_devices_while_the_server_keys_do_not_match(
    authed: httpx.AsyncClient, vapid, push_service: FakePushService, db, monkeypatch
) -> None:
    """A private key that does not belong to the public key is the server's fault: fixing the
    .env makes the existing subscriptions work again, so they are not retired."""
    from app.core.config import get_settings

    url = endpoint()
    assert (await subscribe(authed, url)).status_code == 201
    stranger = Vapid02()
    stranger.generate_keys()
    public = b64u(
        stranger.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    monkeypatch.setattr(get_settings(), "vapid_public_key", public)
    me = await owner_id(db)
    push_service.default_status = 403
    for _ in range(4):
        await deliver_once(db, me)
    row = await subscription(db, url)
    assert (row.is_active, row.last_error, row.failure_count) == (True, "http_403", 4)


async def test_dispatcher_survives_a_retired_subscription(ready, db) -> None:
    """The reminder that met the third refusal fails cleanly; the next one is suppressed."""
    user_id, fake = ready["user_id"], ready["push"]
    fake.default_status = 403
    for minute in range(3):
        rid = await add_reminder(
            db, user_id, task_id=await add_task(db, user_id), remind_at=NOON + timedelta(minutes=minute)
        )
        await dispatch(NOON + timedelta(minutes=minute))
        assert (await reminder(db, rid)).last_error == "delivery_failed:http_403"
    rid = await add_reminder(db, user_id, task_id=await add_task(db, user_id), remind_at=NOON + timedelta(minutes=3))
    await dispatch(NOON + timedelta(minutes=3))
    assert ((await reminder(db, rid)).status, (await reminder(db, rid)).last_error) == (
        ReminderStatus.SUPPRESSED,
        "no_subscription",
    )
    assert len(fake.requests) == 3
