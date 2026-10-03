"""Scoped API tokens for automations (iOS Shortcuts, scripts, servers).

A token carries an explicit scope list. The catalog below is the single source
of truth and is exposed to the frontend so Settings can render the picker with
translated descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unprocessable
from app.core.logging import get_logger
from app.core.security import hash_token, new_secret_token, token_prefix
from app.core.tz import utcnow
from app.models import ApiToken, AuthEvent, User

log = get_logger(__name__)


@dataclass(frozen=True)
class ScopeInfo:
    scope: str
    description_en: str
    description_ru: str
    group: str


SCOPE_CATALOG: tuple[ScopeInfo, ...] = (
    ScopeInfo(
        "ingest:health",
        "Push daily steps / active energy (iOS Shortcut)",
        "Передать шаги и активную энергию за день (iOS Команда)",
        "ingest",
    ),
    ScopeInfo("ingest:weight", "Push body weight measurements", "Передать вес тела", "ingest"),
    ScopeInfo("ingest:sleep", "Push sleep records", "Передать записи сна", "ingest"),
    ScopeInfo("read:all", "Read any of your data", "Читать любые ваши данные", "read"),
    ScopeInfo("write:nutrition", "Log meals and water", "Записывать приёмы пищи и воду", "write"),
    ScopeInfo("write:training", "Log workouts", "Записывать тренировки", "write"),
    ScopeInfo("write:tasks", "Create and complete tasks", "Создавать и завершать задачи", "write"),
    ScopeInfo("write:time", "Start/stop the time tracker", "Управлять трекером времени", "write"),
    ScopeInfo("ai:use", "Use the AI meal analysis", "Использовать ИИ-анализ приёмов пищи", "ai"),
    ScopeInfo("export:data", "Export your data", "Экспортировать данные", "export"),
)

ALL_SCOPES = frozenset(s.scope for s in SCOPE_CATALOG)


@dataclass(slots=True)
class IssuedToken:
    token: str
    row: ApiToken


def scope_catalog() -> list[ScopeInfo]:
    return list(SCOPE_CATALOG)


def validate_scopes(scopes: list[str]) -> list[str]:
    cleaned = [s.strip() for s in scopes if s and s.strip()]
    if not cleaned:
        raise Unprocessable("At least one scope is required", code="scopes_required")
    unknown = [s for s in cleaned if s not in ALL_SCOPES]
    if unknown:
        raise Unprocessable(
            "Unknown scope(s)", code="unknown_scopes", details={"unknown": unknown, "allowed": sorted(ALL_SCOPES)}
        )
    # de-duplicate, keep catalog order for stable display
    order = {s.scope: i for i, s in enumerate(SCOPE_CATALOG)}
    return sorted(set(cleaned), key=lambda s: order.get(s, 999))


async def create_api_token(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    scopes: list[str],
    description: str | None = None,
    expires_at: datetime | None = None,
) -> IssuedToken:
    settings = get_settings()
    clean_scopes = validate_scopes(scopes)
    raw = new_secret_token(settings.api_token_prefix)
    row = ApiToken(
        user_id=user.id,
        name=name[:120],
        description=description,
        token_hash=hash_token(raw),
        token_prefix=token_prefix(raw, 24),
        scopes=clean_scopes,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    db.add(
        AuthEvent(
            user_id=user.id, event="api_token_created", success=True, detail={"name": row.name, "scopes": clean_scopes}
        )
    )
    log.info("api_token_created", user_id=str(user.id), token_id=str(row.id), scopes=clean_scopes)
    return IssuedToken(token=raw, row=row)


async def list_api_tokens(db: AsyncSession, user_id) -> list[ApiToken]:
    rows = (
        (await db.execute(select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc())))
        .scalars()
        .all()
    )
    return list(rows)


async def revoke_api_token(db: AsyncSession, user: User, token_id) -> ApiToken:
    row = (
        await db.execute(select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("API token not found")
    row.is_active = False
    row.revoked_at = utcnow()
    await db.flush()
    db.add(AuthEvent(user_id=user.id, event="api_token_revoked", success=True, detail={"token_id": str(row.id)}))
    log.info("api_token_revoked", user_id=str(user.id), token_id=str(row.id))
    return row


async def assert_scope(db: AsyncSession, token_id, scope: str) -> None:
    row = await db.get(ApiToken, token_id)
    if row is None:
        raise NotFound("API token not found")
    if "*" not in (row.scopes or []) and scope not in (row.scopes or []):
        raise Forbidden(f"Token lacks scope {scope}")
