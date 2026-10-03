"""Device-token auth: pairing, refresh, revocation, scoped API tokens."""

from __future__ import annotations

import pytest


async def test_health_is_public(client) -> None:
    response = await client.get("/api/v1/meta/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] is True


async def test_protected_endpoint_requires_a_token(client) -> None:
    response = await client.get("/api/v1/settings")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_app_config_exposes_limits_and_scopes(client) -> None:
    response = await client.get("/api/v1/meta/app-config")
    assert response.status_code == 200
    body = response.json()
    assert body["language_default"] == "ru"
    assert body["limits"]["safe_floor_female_kcal"] == 1200
    assert body["limits"]["fiber_per_1000_kcal"] == 14
    scopes = {s["scope"] for s in body["scopes"]}
    assert "ingest:health" in scopes and "read:all" in scopes


# --------------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------------- #
async def test_pairing_flow_end_to_end(client, pairing_code) -> None:
    response = await client.post(
        "/api/v1/auth/pair",
        json={
            "code": pairing_code.lower().replace("-", " "),  # forgiving input
            "device": {"name": "iPhone 15 Pro", "platform": "ios", "model": "iPhone 15 Pro"},
            "timezone": "Asia/Bangkok",
            "language": "ru",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"].startswith("dsos_dev_")
    assert body["device"]["is_current"] is True
    assert body["device"]["name"] == "iPhone 15 Pro"
    assert body["settings"]["timezone"] == "Asia/Bangkok"
    assert body["settings"]["language"] == "ru"
    assert body["reused_device"] is False

    token = body["token"]
    session = await client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert session.status_code == 200
    session_body = session.json()
    assert session_body["auth_kind"] == "device"
    assert session_body["device"]["is_current"] is True
    assert session_body["targets"]["safe_floor_kcal"] == 1200  # sex not set yet


async def test_pairing_code_is_single_use(client, pairing_code) -> None:
    payload = {"code": pairing_code, "device": {"name": "A", "platform": "ios"}}
    first = await client.post("/api/v1/auth/pair", json=payload)
    assert first.status_code == 200
    second = await client.post("/api/v1/auth/pair", json=payload)
    assert second.status_code == 401
    assert second.json()["error"]["code"] in {"invalid_pairing_code", "pairing_code_used"}


async def test_wrong_code_is_rejected_and_recorded(client) -> None:
    response = await client.post("/api/v1/auth/pair", json={"code": "ZZZZ-ZZZZ"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_pairing_code"


async def test_expired_code_is_rejected(client, owner) -> None:

    from app.db import SessionLocal
    from app.services.pairing import create_pairing_code

    async with SessionLocal() as session:
        issued = await create_pairing_code(session, owner, ttl_minutes=-1)  # already expired
        await session.commit()
        code = issued.code

    response = await client.post("/api/v1/auth/pair", json={"code": code})
    assert response.status_code == 401


async def test_device_limit_is_enforced(client, owner) -> None:
    from app.db import SessionLocal
    from app.services.pairing import create_pairing_code

    # owner.max_devices default = 2
    for index in range(2):
        async with SessionLocal() as session:
            issued = await create_pairing_code(session, owner, created_by="test")
            await session.commit()
        response = await client.post(
            "/api/v1/auth/pair",
            json={"code": issued.code, "device": {"name": f"dev{index}", "fingerprint": f"fp-{index}"}},
        )
        assert response.status_code == 200, response.text

    async with SessionLocal() as session:
        issued = await create_pairing_code(session, owner, created_by="test")
        await session.commit()
    third = await client.post(
        "/api/v1/auth/pair", json={"code": issued.code, "device": {"name": "third", "fingerprint": "fp-3"}}
    )
    assert third.status_code == 401
    assert third.json()["error"]["code"] == "device_limit_reached"
    assert third.json()["error"]["details"]["limit"] == 2


async def test_repairing_with_the_same_fingerprint_reuses_the_device(client, owner) -> None:
    from app.db import SessionLocal
    from app.services.pairing import create_pairing_code

    async def pair() -> dict:
        async with SessionLocal() as session:
            issued = await create_pairing_code(session, owner, created_by="test")
            await session.commit()
        response = await client.post(
            "/api/v1/auth/pair",
            json={"code": issued.code, "device": {"name": "MacBook", "platform": "macos", "fingerprint": "fp-same"}},
        )
        assert response.status_code == 200, response.text
        return response.json()

    first = await pair()
    second = await pair()
    assert second["reused_device"] is True
    assert second["device"]["id"] == first["device"]["id"]
    assert second["token"] != first["token"]  # token is rotated on re-pair


# --------------------------------------------------------------------------- #
# Session, refresh, logout
# --------------------------------------------------------------------------- #
async def test_refresh_rotates_the_token(authed) -> None:
    old_token = authed.headers["Authorization"].split(" ", 1)[1]
    response = await authed.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    new_token = response.json()["token"]
    assert new_token != old_token

    stale = await authed.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {old_token}"})
    assert stale.status_code == 401  # the previous token is dead

    fresh = await authed.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {new_token}"})
    assert fresh.status_code == 200


async def test_second_device_keeps_working_after_refresh(authed, owner, client) -> None:
    from app.db import SessionLocal
    from app.services.pairing import create_pairing_code

    async with SessionLocal() as session:
        issued = await create_pairing_code(session, owner, created_by="test")
        await session.commit()
    second = await client.post(
        "/api/v1/auth/pair",
        json={"code": issued.code, "device": {"name": "iPad", "platform": "ios", "fingerprint": "fp-ipad"}},
    )
    assert second.status_code == 200

    await authed.post("/api/v1/auth/refresh")
    still_valid = await client.get(
        "/api/v1/auth/session", headers={"Authorization": f"Bearer {second.json()['token']}"}
    )
    assert still_valid.status_code == 200


async def test_revoking_devices_and_the_last_device_guard(authed, client) -> None:
    listing = await authed.get("/api/v1/devices")
    assert listing.status_code == 200
    devices = listing.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["is_current"] is True
    assert listing.json()["max_devices"] == 2

    # the only device cannot revoke itself (would lock the owner out)
    blocked = await authed.delete(f"/api/v1/devices/{devices[0]['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "last_device"

    # add a second device, then revoke the first one
    owner_devices = await authed.get("/api/v1/devices")
    assert owner_devices.status_code == 200
    code_response = await authed.post("/api/v1/devices/pairing-code")
    assert code_response.status_code == 200
    code = code_response.json()["code"]

    paired = await client.post(
        "/api/v1/auth/pair",
        json={"code": code, "device": {"name": "Second", "platform": "ios", "fingerprint": "fp-second"}},
    )
    assert paired.status_code == 200
    second_token = paired.json()["token"]

    revoked = await authed.delete(f"/api/v1/devices/{devices[0]['id']}")
    assert revoked.status_code == 200
    # the current (revoked) token stops working immediately
    assert (await authed.get("/api/v1/auth/session")).status_code == 401
    # the other device is unaffected
    assert (
        await client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {second_token}"})
    ).status_code == 200


async def test_rename_device(authed) -> None:
    listing = (await authed.get("/api/v1/devices")).json()
    device_id = listing["devices"][0]["id"]
    response = await authed.patch(f"/api/v1/devices/{device_id}", json={"name": "My Phone"})
    assert response.status_code == 200
    assert response.json()["name"] == "My Phone"


# --------------------------------------------------------------------------- #
# Scoped API tokens
# --------------------------------------------------------------------------- #
async def test_api_token_scopes_are_enforced(authed, client) -> None:
    created = await authed.post(
        "/api/v1/tokens",
        json={"name": "iOS Shortcut", "scopes": ["ingest:health"], "description": "steps + energy"},
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    assert token.startswith("dsos_pat_")

    headers = {"Authorization": f"Bearer {token}"}
    # allowed: its own session view (read of self is always permitted)
    session = await client.get("/api/v1/auth/session", headers=headers)
    assert session.status_code == 200
    assert session.json()["auth_kind"] == "api_token"
    assert session.json()["scopes"] == ["ingest:health"]

    # forbidden: settings require a device (or read:all)
    settings_response = await client.get("/api/v1/settings", headers=headers)
    assert settings_response.status_code in (401, 403)


async def test_unknown_scope_is_rejected(authed) -> None:
    response = await authed.post("/api/v1/tokens", json={"name": "bad", "scopes": ["nonsense:scope"]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_scopes"

    empty = await authed.post("/api/v1/tokens", json={"name": "empty", "scopes": []})
    assert empty.status_code == 422


async def test_token_can_be_revoked(authed, client) -> None:
    created = await authed.post("/api/v1/tokens", json={"name": "laptop script", "scopes": ["read:all"]})
    token_id = created.json()["api_token"]["id"]
    token = created.json()["token"]

    assert (await client.get("/api/v1/settings", headers={"Authorization": f"Bearer {token}"})).status_code == 200
    revoked = await authed.delete(f"/api/v1/tokens/{token_id}")
    assert revoked.status_code == 200
    after = await client.get("/api/v1/settings", headers={"Authorization": f"Bearer {token}"})
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "token_revoked"


async def test_garbage_token_is_rejected(client) -> None:
    response = await client.get("/api/v1/settings", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


async def test_rate_limiting_kicks_in(client, monkeypatch) -> None:
    from app.core import rate_limit
    from app.core.config import get_settings

    rate_limit.reset()
    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_minute", 3)
    for _ in range(12):
        response = await client.post("/api/v1/auth/pair", json={"code": "AAAA-AAAA"})
        if response.status_code == 429:
            assert response.json()["error"]["code"] == "rate_limited"
            assert "retry_after" in response.json()["error"]["details"]
            break
    else:  # pragma: no cover
        pytest.fail("rate limit never triggered")
