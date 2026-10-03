"""Authentication endpoints: pairing, session, refresh, logout.

Everything except ``POST /auth/pair`` requires a valid bearer token, and the
frontend only ever shows the pairing screen when a request comes back 401.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentPrincipal, DbSession
from app.api.v1.presenters import build_session, device_out
from app.core.config import get_settings
from app.core.errors import Conflict, Unauthorized
from app.core.logging import get_logger
from app.core.rate_limit import client_ip, rate_limit_dependency
from app.core.security import hash_token, new_secret_token, token_prefix
from app.core.tz import utcnow
from app.db import get_db
from app.models import User
from app.schemas.auth import PairingRequestIn, PairingResultOut, SessionOut
from app.services.pairing import (
    DEVICE_TOKEN_PREFIX,
    pair_device,
    register_failed_attempt,
    revoke_device,
)
from app.services.settings_service import get_or_create_settings, recompute_targets, targets_as_dict

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_pair_limit = rate_limit_dependency("auth:pair")
_refresh_limit = rate_limit_dependency("auth:refresh", limit=30)


@router.post(
    "/pair",
    response_model=PairingResultOut,
    summary="Exchange a one-time pairing code for a long-lived device token",
    dependencies=[Depends(_pair_limit)],
)
async def pair(
    payload: PairingRequestIn,
    request: Request,
    db: DbSession,
) -> PairingResultOut:
    settings = get_settings()
    try:
        result = await pair_device(
            db,
            code=payload.code,
            device_name=payload.device.name,
            platform=payload.device.platform,
            model=payload.device.model,
            app_version=payload.device.app_version,
            fingerprint_hash=_fingerprint_hash(payload.device.fingerprint),
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except Unauthorized:
        await register_failed_attempt(db, payload.code)
        raise

    user = await db.get(User, result.device.user_id)
    if user is None:  # pragma: no cover - pair_device guarantees the row exists
        raise Unauthorized("Account for this device no longer exists")
    row = await get_or_create_settings(db, user)

    # first pairing also carries the device's timezone/language
    if payload.timezone and row.timezone_auto:
        row.timezone = payload.timezone
    if payload.language:
        row.language = payload.language  # type: ignore[assignment]
    await recompute_targets(db, row)

    log.info(
        "device_paired",
        user_id=str(user.id),
        device_id=str(result.device.id),
        reused=result.reused,
        ttl_minutes=settings.pairing_code_ttl_minutes,
    )
    return PairingResultOut(
        token=result.token,
        device=device_out(result.device, current_id=result.device.id),
        user=user,
        settings=row,
        reused_device=result.reused,
    )


@router.get("/session", response_model=SessionOut, summary="Who am I / current settings")
async def session(principal: CurrentPrincipal, db: DbSession) -> SessionOut:
    row = await get_or_create_settings(db, principal.user)
    return build_session(principal, row, await targets_as_dict(db, row))


@router.post(
    "/refresh",
    response_model=PairingResultOut,
    summary="Rotate the current device token (keeps the device registered)",
    dependencies=[Depends(_refresh_limit)],
)
async def refresh(principal: CurrentPrincipal, db: DbSession) -> PairingResultOut:
    if principal.device is None:
        raise Conflict(
            "Only device tokens can be rotated; API tokens are managed in Settings",
            code="not_a_device_token",
        )
    device = principal.device
    raw = new_secret_token(DEVICE_TOKEN_PREFIX)
    device.token_hash = hash_token(raw)
    device.token_prefix = token_prefix(raw)
    device.last_seen_at = utcnow()
    await db.flush()
    row = await get_or_create_settings(db, principal.user)
    log.info("device_token_rotated", user_id=str(principal.user.id), device_id=str(device.id))
    return PairingResultOut(
        token=raw,
        device=device_out(device, current_id=device.id),
        user=principal.user,
        settings=row,
        reused_device=True,
    )


@router.post("/logout", summary="Revoke THIS device (refuses when it is the last one)")
async def logout(principal: CurrentPrincipal, db: DbSession) -> dict[str, object]:
    if principal.device is None:
        raise Conflict("API tokens cannot log a device out", code="not_a_device_token")
    device = await revoke_device(db, principal.user, principal.device.id)
    return {
        "ok": True,
        "device_id": str(device.id),
        "message": "Device revoked. Create a new pairing code to reconnect.",
    }


def _fingerprint_hash(fingerprint: str | None) -> str | None:
    if not fingerprint:
        return None
    from app.core.security import hash_token as _h

    return _h(fingerprint)


def get_db_dep():  # pragma: no cover - kept for symmetry/typing
    return Depends(get_db)
