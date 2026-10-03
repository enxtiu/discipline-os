"""Authentication dependencies.

Auth model (no passwords):

* **Device token** — minted once by ``POST /auth/pair`` from a CLI pairing code.
  Long lived, stored hashed, bound to a device row, revocable from Settings.
* **API token** — scoped bearer token for automations (iOS Shortcuts). Its
  scopes restrict what the token may do (e.g. ``ingest:health`` only).

Every dependency here returns a :class:`Principal`; endpoints then use
``principal.user`` and never trust a ``user_id`` coming from the client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, Unauthorized
from app.core.logging import get_logger, user_id_var
from app.core.security import hash_token
from app.core.tz import utcnow
from app.db import get_db
from app.models import ApiToken, Device, User, UserSettings, UserStatus

log = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False, description="Device or API bearer token")

LAST_SEEN_THROTTLE_SECONDS = 300


@dataclass(slots=True)
class Principal:
    user: User
    settings_row: UserSettings | None
    device: Device | None = None
    api_token: ApiToken | None = None
    scopes: set[str] = field(default_factory=set)
    kind: Literal["device", "api_token"] = "device"

    @property
    def is_device(self) -> bool:
        return self.device is not None

    @property
    def user_id(self):
        return self.user.id

    def has_scope(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise Forbidden(
                f"Token is missing the required scope: {scope}",
                details={"required_scope": scope, "granted": sorted(self.scopes)},
            )


async def _touch_device(db: AsyncSession, device: Device) -> None:
    """Update ``last_seen_at`` at most every 5 minutes (avoids write storms)."""
    now = utcnow()
    if device.last_seen_at is None or (now - device.last_seen_at).total_seconds() > LAST_SEEN_THROTTLE_SECONDS:
        await db.execute(update(Device).where(Device.id == device.id).values(last_seen_at=now))
        device.last_seen_at = now


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise Unauthorized("Missing bearer token")

    raw = credentials.credentials.strip()
    digest = hash_token(raw)
    now = utcnow()

    # ---- device token ----------------------------------------------------
    device = (await db.execute(select(Device).where(Device.token_hash == digest))).scalar_one_or_none()
    if device is not None:
        if not device.is_active or device.revoked_at is not None:
            raise Unauthorized("This device has been revoked", code="device_revoked")
        user = await db.get(User, device.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise Unauthorized("Account is not active", code="account_inactive")
        await _touch_device(db, device)
        user_id_var.set(str(user.id))
        return Principal(
            user=user,
            settings_row=await _load_settings(db, user.id),
            device=device,
            scopes=set(device.scopes or ["*"]),
            kind="device",
        )

    # ---- scoped API token ------------------------------------------------
    token = (await db.execute(select(ApiToken).where(ApiToken.token_hash == digest))).scalar_one_or_none()
    if token is not None:
        if not token.is_active or token.revoked_at is not None:
            raise Unauthorized("This API token has been revoked", code="token_revoked")
        if token.expires_at is not None and token.expires_at <= now:
            raise Unauthorized("This API token has expired", code="token_expired")
        user = await db.get(User, token.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise Unauthorized("Account is not active", code="account_inactive")
        token.last_used_at = now
        token.request_count = (token.request_count or 0) + 1
        token.last_ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
            request.client.host if request.client else None
        )
        user_id_var.set(str(user.id))
        return Principal(
            user=user,
            settings_row=await _load_settings(db, user.id),
            api_token=token,
            scopes=set(token.scopes or []),
            kind="api_token",
        )

    log.info("auth_failed", reason="unknown_token", path=str(request.url.path))
    raise Unauthorized("Invalid token")


async def _load_settings(db: AsyncSession, user_id) -> UserSettings | None:
    return (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()


async def get_current_user(
    principal: Annotated[Principal, Depends(get_principal)],
) -> User:
    return principal.user


async def get_owner(
    principal: Annotated[Principal, Depends(get_principal)],
) -> User:
    """This deployment has one owner; the check documents the intent."""
    if not principal.user.is_owner:
        raise Forbidden("Owner access required")
    return principal.user


def require_scope(*scopes: str):
    """Dependency factory: ``Depends(require_scope("ingest:health"))``."""

    async def _dep(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        for scope in scopes:
            principal.require_scope(scope)
        return principal

    return _dep


async def require_device(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Device-only endpoints: device management and API-token management.

    A scoped API token must never be able to mint another token or revoke the
    device that owns it (privilege escalation).
    """
    if not principal.is_device:
        raise Forbidden(
            "This endpoint is only available from a paired device",
            code="device_only",
        )
    return principal


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
