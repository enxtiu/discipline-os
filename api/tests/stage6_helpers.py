"""Helpers shared by the stage-6 (planner) tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

API = "/api/v1/planner"
BANGKOK = ZoneInfo("Asia/Bangkok")  # the device fixture pairs with this timezone
BERLIN = ZoneInfo("Europe/Berlin")


def uid() -> str:
    return str(uuid.uuid4())


def today(tz: ZoneInfo = BANGKOK) -> date:
    return datetime.now(tz).date()


def code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


async def make_task(client: httpx.AsyncClient, title: str = "задача", **fields: Any) -> dict[str, Any]:
    response = await client.post(f"{API}/tasks", json={"title": title, **fields})
    assert response.status_code == 201, response.text
    return response.json()


async def patch_task(client: httpx.AsyncClient, task_id: str, **fields: Any) -> dict[str, Any]:
    response = await client.patch(f"{API}/tasks/{task_id}", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


async def get_task(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    response = await client.get(f"{API}/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def complete(client: httpx.AsyncClient, task_id: str, **body: Any) -> dict[str, Any]:
    response = await client.post(f"{API}/tasks/{task_id}/complete", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def set_timezone(client: httpx.AsyncClient, tz: str) -> None:
    response = await client.patch("/api/v1/settings", json={"timezone": tz, "timezone_auto": False})
    assert response.status_code == 200, response.text


@asynccontextmanager
async def other_user() -> AsyncIterator[httpx.AsyncClient]:
    """A second user with a paired device of its own (scoping tests)."""
    from app.db import SessionLocal
    from app.main import app
    from app.models import User, UserStatus
    from app.services.pairing import create_pairing_code
    from app.services.settings_service import get_or_create_settings

    async with SessionLocal() as session:
        user = User(display_name="Other", status=UserStatus.ACTIVE, is_owner=False)
        session.add(user)
        await session.flush()
        await get_or_create_settings(session, user)
        issued = await create_pairing_code(session, user, created_by="test")
        await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        paired = await client.post(
            "/api/v1/auth/pair",
            json={
                "code": issued.code,
                "device": {"name": "Other phone", "platform": "ios", "fingerprint": "fp-other"},
                "timezone": "Asia/Bangkok",
                "language": "ru",
            },
        )
        assert paired.status_code == 200, paired.text
        client.headers["Authorization"] = f"Bearer {paired.json()['token']}"
        yield client
