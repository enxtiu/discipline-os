"""Async engine / session factory.

The API is async end-to-end (SQLAlchemy 2.0 async + psycopg 3). The CLI and the
scheduler use the very same factory, so there is exactly one session recipe.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
    # statement parameters (push endpoints and keys, token hashes, notes…) never end up in
    # an exception message or the log — also not with DB_ECHO=true
    hide_parameters=True,
    future=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one transaction per request.

    Commits on success, rolls back on any exception and always closes.
    Endpoints therefore never need to call ``commit()`` themselves.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Standalone session for CLI commands, scheduler jobs and scripts."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping() -> bool:
    import sqlalchemy as sa

    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - depends on environment
        return False


_search_ready: bool | None = None


async def search_ready(force: bool = False) -> bool | None:
    """Is ``pg_trgm`` actually working for non-ASCII text?

    ``pg_trgm`` builds trigrams through the database ctype: a cluster created
    with the ``C`` / ``POSIX`` locale returns 0.0 for *every* Cyrillic
    similarity comparison, which would silently turn the typo-tolerant food
    search into an exact-match-only search. This probe surfaces that at
    ``/api/v1/meta/health`` instead of leaving it to be discovered by a user
    who cannot find "гречка".

    Requires the ``pg_trgm`` extension; returns ``None`` when it is missing.
    """
    global _search_ready
    if _search_ready is not None and not force:
        return _search_ready
    import sqlalchemy as sa

    try:
        async with engine.connect() as conn:
            value = await conn.scalar(sa.text("SELECT similarity('гречка', 'гречка') > 0"))
        _search_ready = None if value is None else bool(value)
    except Exception:  # pragma: no cover - extension missing / db down
        _search_ready = None
    return _search_ready


async def dispose_engine() -> None:
    await engine.dispose()
