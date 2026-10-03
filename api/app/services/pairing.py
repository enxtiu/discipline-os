"""Pairing codes and device tokens.

Flow
----
1. On the server:  ``python -m app.cli create-owner`` (first time) and
   ``python -m app.cli pairing-code`` (any time) print a one-time code that
   lives for ``PAIRING_CODE_TTL_MINUTES`` (default 15).
2. In the browser: the user types the code.
   ``POST /api/v1/auth/pair`` consumes it and returns a long-lived **device
   token**, stored hashed on the server and in localStorage on the device.
3. Settings lists devices with last-seen and lets the user revoke them.
   ``MAX_DEVICES_DEFAULT`` (default 2) limits *active* devices.

Security notes: codes are short, so they are stored as keyed HMAC (pepper =
``SECRET_KEY``) and single-use by default; brute force is bounded by the
per-IP rate limit plus the ``attempts`` counter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, Unauthorized
from app.core.ids import new_token_id
from app.core.logging import get_logger
from app.core.security import (
    constant_time_eq,
    generate_pairing_code,
    hash_token,
    hmac_sha256,
    new_secret_token,
    normalise_pairing_code,
    token_prefix,
)
from app.core.tz import utcnow
from app.models import AuthEvent, Device, PairingCode, User

log = get_logger(__name__)

DEVICE_TOKEN_PREFIX = "dsos_dev_"  # noqa: S105  (token prefix, not a secret)
MAX_FAILED_ATTEMPTS = 5


@dataclass(slots=True)
class IssuedPairingCode:
    code: str
    expires_at: object  # datetime
    max_uses: int


@dataclass(slots=True)
class PairedDevice:
    token: str
    device: Device
    reused: bool


async def create_pairing_code(
    db: AsyncSession,
    user: User,
    *,
    ttl_minutes: int | None = None,
    max_uses: int = 1,
    created_by: str = "cli",
    note: str | None = None,
) -> IssuedPairingCode:
    settings = get_settings()
    ttl = ttl_minutes or settings.pairing_code_ttl_minutes

    # Invalidate previous unused codes so only the newest one works — keeps the
    # "print a code, type it" flow unambiguous.
    now = utcnow()
    await db.execute(
        PairingCode.__table__.update()
        .where(PairingCode.user_id == user.id, PairingCode.revoked_at.is_(None), PairingCode.expires_at > now)
        .values(revoked_at=now)
    )

    for _ in range(5):  # collision retry
        code = generate_pairing_code()
        digest = hmac_sha256(normalise_pairing_code(code), settings.secret_key)
        exists = (await db.execute(select(PairingCode.id).where(PairingCode.code_hmac == digest))).scalar_one_or_none()
        if exists is None:
            break
    else:  # pragma: no cover - 40 bits of entropy makes this practically dead
        raise Conflict("Could not allocate a unique pairing code, please retry")

    row = PairingCode(
        user_id=user.id,
        code_hmac=digest,
        code_hint=f"{normalise_pairing_code(code)[:4]}-…",
        expires_at=now + timedelta(minutes=ttl),
        max_uses=max(1, max_uses),
        created_by=created_by,
        note=note,
    )
    db.add(row)
    await db.flush()
    log.info("pairing_code_created", user_id=str(user.id), max_uses=row.max_uses, ttl_minutes=ttl)
    return IssuedPairingCode(code=code, expires_at=row.expires_at, max_uses=row.max_uses)


async def count_active_devices(db: AsyncSession, user_id) -> int:
    total = await db.scalar(select(func.count(Device.id)).where(Device.user_id == user_id, Device.is_active.is_(True)))
    return int(total or 0)


async def pair_device(
    db: AsyncSession,
    *,
    code: str,
    device_name: str,
    platform: str | None = None,
    model: str | None = None,
    app_version: str | None = None,
    fingerprint_hash: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> PairedDevice:
    settings = get_settings()
    now = utcnow()
    digest = hmac_sha256(normalise_pairing_code(code), settings.secret_key)

    row = (
        await db.execute(select(PairingCode).where(PairingCode.code_hmac == digest).with_for_update())
    ).scalar_one_or_none()

    if row is None or row.revoked_at is not None or row.expires_at <= now:
        await _record_event(db, None, None, "pairing_failed", False, ip, user_agent, {"reason": "bad_code"})
        raise Unauthorized("Pairing code is invalid or has expired", code="invalid_pairing_code")

    if row.uses >= row.max_uses:
        await _record_event(db, row.user_id, None, "pairing_failed", False, ip, user_agent, {"reason": "used"})
        raise Unauthorized("This pairing code has already been used", code="pairing_code_used")

    if row.attempts >= MAX_FAILED_ATTEMPTS:
        await _record_event(
            db, row.user_id, None, "pairing_failed", False, ip, user_agent, {"reason": "too_many_attempts"}
        )
        raise Unauthorized("Too many attempts with this code, generate a new one", code="pairing_code_locked")

    user = await db.get(User, row.user_id)
    if user is None:
        raise NotFound("User for this pairing code no longer exists")

    # ---- device slot bookkeeping ----------------------------------------
    reused = False
    device: Device | None = None
    if fingerprint_hash:
        device = (
            await db.execute(
                select(Device).where(
                    Device.user_id == user.id,
                    Device.fingerprint_hash == fingerprint_hash,
                    Device.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        reused = device is not None

    if device is None:
        active = await count_active_devices(db, user.id)
        limit = user.max_devices or settings.max_devices_default
        if active >= limit:
            await _record_event(
                db,
                user.id,
                None,
                "pairing_failed",
                False,
                ip,
                user_agent,
                {"reason": "device_limit", "active": active, "limit": limit},
            )
            raise Unauthorized(
                f"Device limit reached ({active}/{limit}). Revoke a device in Settings or raise the limit.",
                code="device_limit_reached",
                details={"active": active, "limit": limit},
            )
        device = Device(
            user_id=user.id,
            name=device_name[:120] or "Device",
            platform=platform,
            model=model,
            app_version=app_version,
            fingerprint_hash=fingerprint_hash,
            token_hash="pending",  # noqa: S106 - replaced below before the INSERT flush
            token_prefix="pending",  # noqa: S106
        )
        db.add(device)

    # ---- mint (or rotate) the device token -------------------------------
    raw_token = new_secret_token(DEVICE_TOKEN_PREFIX)
    device.token_hash = hash_token(raw_token)
    device.token_prefix = token_prefix(raw_token)
    device.name = device_name[:120] or device.name
    device.platform = platform or device.platform
    device.model = model or device.model
    device.app_version = app_version or device.app_version
    device.fingerprint_hash = fingerprint_hash or device.fingerprint_hash
    device.is_active = True
    device.revoked_at = None
    device.last_seen_at = now
    device.last_ip = ip
    device.last_user_agent = (user_agent or "")[:255] or None
    if device.scopes is None or not device.scopes:
        device.scopes = ["*"]

    row.uses += 1
    row.last_used_at = now
    if row.first_used_at is None:
        row.first_used_at = now
    if row.uses >= row.max_uses:
        row.revoked_at = now

    await db.flush()
    await _record_event(
        db,
        user.id,
        device.id,
        "pairing_succeeded",
        True,
        ip,
        user_agent,
        {"reused_device": reused, "device_name": device.name},
    )
    log.info("device_paired", user_id=str(user.id), device_id=str(device.id), reused=reused)
    return PairedDevice(token=raw_token, device=device, reused=reused)


async def register_failed_attempt(db: AsyncSession, code: str) -> None:
    """Called by the endpoint when validation fails, to bound brute force."""
    settings = get_settings()
    digest = hmac_sha256(normalise_pairing_code(code), settings.secret_key)
    row = (await db.execute(select(PairingCode).where(PairingCode.code_hmac == digest))).scalar_one_or_none()
    if row is not None:
        row.attempts += 1


async def list_devices(db: AsyncSession, user_id, current_device_id=None):
    rows = (
        (await db.execute(select(Device).where(Device.user_id == user_id).order_by(Device.created_at.desc())))
        .scalars()
        .all()
    )
    return rows, current_device_id


async def revoke_device(db: AsyncSession, user: User, device_id) -> Device:
    device = (
        await db.execute(select(Device).where(Device.id == device_id, Device.user_id == user.id))
    ).scalar_one_or_none()
    if device is None:
        raise NotFound("Device not found")

    if device.is_active:
        active = await count_active_devices(db, user.id)
        if active <= 1:
            raise Conflict(
                "Refusing to revoke the last active device — you would lock yourself out. "
                "Create a new pairing code first.",
                code="last_device",
            )

    device.is_active = False
    device.revoked_at = utcnow()
    # rotate the token so the revoked device cannot come back with the old one
    device.token_hash = hash_token(new_secret_token(DEVICE_TOKEN_PREFIX))
    await db.flush()
    await _record_event(db, user.id, device.id, "device_revoked", True, None, None, {})
    log.info("device_revoked", user_id=str(user.id), device_id=str(device.id))
    return device


async def rename_device(db: AsyncSession, user: User, device_id, name: str) -> Device:
    device = (
        await db.execute(select(Device).where(Device.id == device_id, Device.user_id == user.id))
    ).scalar_one_or_none()
    if device is None:
        raise NotFound("Device not found")
    device.name = name[:120]
    await db.flush()
    return device


async def prune_expired_codes(db: AsyncSession) -> int:
    """Housekeeping: mark expired codes revoked (keeps the table small)."""
    now = utcnow()
    result = await db.execute(
        PairingCode.__table__.update()
        .where(PairingCode.revoked_at.is_(None), PairingCode.expires_at <= now)
        .values(revoked_at=now)
    )
    return int(result.rowcount or 0)


async def _record_event(
    db: AsyncSession,
    user_id,
    device_id,
    event: str,
    success: bool,
    ip: str | None,
    user_agent: str | None,
    detail: dict,
) -> None:
    db.add(
        AuthEvent(
            user_id=user_id,
            device_id=device_id,
            event=event,
            success=success,
            ip=ip,
            user_agent=(user_agent or "")[:255] or None,
            detail=detail,
        )
    )


__all__ = [
    "DEVICE_TOKEN_PREFIX",
    "IssuedPairingCode",
    "PairedDevice",
    "constant_time_eq",
    "count_active_devices",
    "create_pairing_code",
    "list_devices",
    "new_token_id",
    "pair_device",
    "prune_expired_codes",
    "register_failed_attempt",
    "rename_device",
    "revoke_device",
]
