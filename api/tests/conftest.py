"""Pytest fixtures.

The test suite runs against a **real PostgreSQL** database (never SQLite): the
schema leans on ``timestamptz``, partial unique indexes, JSONB, full-text search
and ``pg_trgm``, so testing anywhere else would be theatre.

Configuration:
    TEST_DATABASE_URL  (default: postgresql+psycopg://dsos:dsos@localhost:5432/dsos_test)

The schema is built by running the real migrations (``alembic upgrade head``),
which also proves the migrations work from scratch. Each test runs in a clean
database: tables are truncated after every test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

# --- environment must be set BEFORE importing the app ------------------------
TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://dsos:dsos@localhost:5432/dsos_test")
os.environ["ENV"] = "development"
os.environ["DATABASE_URL"] = TEST_DSN
os.environ["SECRET_KEY"] = "test-secret-key-please-change-0123456789"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "10000"  # limits are tested explicitly
os.environ["AI_PROVIDER"] = "fake"
os.environ["OFF_LIVE_LOOKUP"] = "false"  # never reach the internet; OFF tests mock the transport

import httpx  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from alembic.config import Config  # noqa: E402

from alembic import command  # noqa: E402

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# Schema (session scoped)
# --------------------------------------------------------------------------- #
def _sync_dsn() -> str:
    return TEST_DSN


def _admin_dsn() -> tuple[str, str]:
    """(admin_dsn pointing at the maintenance DB, database name)."""
    from sqlalchemy.engine import make_url

    url = make_url(TEST_DSN)
    db_name = url.database or "dsos_test"
    admin = url.set(database="postgres")
    return admin.render_as_string(hide_password=False), db_name


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Create the test database and run migrations into it."""
    import psycopg

    admin_dsn, db_name = _admin_dsn()
    dsn = admin_dsn.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{db_name}"')

    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn())
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def _clean_tables(_database: None) -> AsyncIterator[None]:
    """Truncate every table between tests (fast, keeps the schema)."""
    yield
    from app.db import engine

    async with engine.begin() as conn:
        tables = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            await conn.execute(sa.text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    from app.core.rate_limit import reset

    reset()


# --------------------------------------------------------------------------- #
# App / HTTP client
# --------------------------------------------------------------------------- #
@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client — no network, no lifespan (so no scheduler in tests)."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
async def db():
    """Raw async session for assertions inside tests."""
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session


# --------------------------------------------------------------------------- #
# Domain helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
async def owner() -> Any:
    """The owner user (created the same way the CLI does it)."""
    from app.db import SessionLocal
    from app.services.bootstrap import create_owner

    async with SessionLocal() as session:
        result = await create_owner(session, display_name="Test Owner")
        await session.commit()
        await session.refresh(result.user)
        return result.user


@pytest.fixture
async def pairing_code(owner: Any) -> str:
    from app.db import SessionLocal
    from app.services.pairing import create_pairing_code

    async with SessionLocal() as session:
        issued = await create_pairing_code(session, owner, created_by="test")
        await session.commit()
        return issued.code


@pytest.fixture
async def device_token(client: httpx.AsyncClient, pairing_code: str) -> str:
    response = await client.post(
        "/api/v1/auth/pair",
        json={
            "code": pairing_code,
            "device": {"name": "Test iPhone", "platform": "ios", "model": "iPhone 15", "fingerprint": "fp-test"},
            "timezone": "Asia/Bangkok",
            "language": "ru",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture
async def authed(client: httpx.AsyncClient, device_token: str) -> httpx.AsyncClient:
    client.headers["Authorization"] = f"Bearer {device_token}"
    return client


# --------------------------------------------------------------------------- #
# Small utilities used across tests
# --------------------------------------------------------------------------- #
def utc(*args: int, **kwargs: int) -> datetime:
    return datetime(*args, tzinfo=UTC, **kwargs)


def days_ago(n: int) -> date:
    return (datetime.now(UTC) - timedelta(days=n)).date()
