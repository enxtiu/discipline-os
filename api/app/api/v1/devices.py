"""Device management: list, rename, revoke, and mint new pairing codes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import device_out
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound
from app.core.logging import get_logger
from app.core.rate_limit import rate_limit_dependency
from app.models import Device
from app.schemas.auth import (
    DeviceListOut,
    DeviceOut,
    DeviceUpdateIn,
    PairingCodeInfoOut,
)
from app.schemas.common import DeletedOut
from app.services.pairing import (
    count_active_devices,
    create_pairing_code,
    rename_device,
    revoke_device,
)

log = get_logger(__name__)
router = APIRouter(
    prefix="/devices",
    tags=["devices"],
    dependencies=[Depends(require_device)],
)

_pairing_code_limit = rate_limit_dependency("devices:pairing-code", limit=6)


@router.get("", response_model=DeviceListOut, summary="List paired devices")
async def list_devices(principal: CurrentPrincipal, db: DbSession) -> DeviceListOut:
    rows = (
        (
            await db.execute(
                select(Device)
                .where(Device.user_id == principal.user_id)
                .order_by(Device.is_active.desc(), Device.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    current_id = principal.device.id if principal.device else None
    limit = principal.user.max_devices or get_settings().max_devices_default
    return DeviceListOut(
        devices=[device_out(row, current_id=current_id) for row in rows],
        active_count=sum(1 for row in rows if row.is_active),
        max_devices=limit,
    )


@router.post(
    "/pairing-code",
    response_model=PairingCodeInfoOut,
    summary="Create a pairing code for an additional device",
    dependencies=[Depends(_pairing_code_limit)],
)
async def create_code(principal: CurrentPrincipal, db: DbSession) -> PairingCodeInfoOut:
    if not principal.is_device and not principal.has_scope("read:all"):
        raise Forbidden("Only a paired device can create pairing codes")
    settings = get_settings()
    active = await count_active_devices(db, principal.user_id)
    limit = principal.user.max_devices or settings.max_devices_default
    cross = settings.cross_device_pairing_code_ttl_minutes
    issued = await create_pairing_code(
        db,
        principal.user,
        ttl_minutes=settings.pairing_code_ttl_minutes or cross or None,
        max_uses=2 if cross else 1,
        created_by="app",
        note=f"issued_from_device:{principal.device.id if principal.device else 'api'}",
    )
    log.info("pairing_code_issued_from_app", user_id=str(principal.user_id), active_devices=active, limit=limit)
    return PairingCodeInfoOut(code=issued.code, expires_at=issued.expires_at, max_uses=issued.max_uses)


@router.patch("/{device_id}", response_model=DeviceOut, summary="Rename a device / toggle push")
async def update_device(
    device_id: uuid.UUID, payload: DeviceUpdateIn, principal: CurrentPrincipal, db: DbSession
) -> DeviceOut:
    device = (
        await db.execute(select(Device).where(Device.id == device_id, Device.user_id == principal.user_id))
    ).scalar_one_or_none()
    if device is None:
        raise NotFound("Device not found")
    if payload.name is not None:
        device = await rename_device(db, principal.user, device_id, payload.name)
    if payload.push_enabled is not None:
        device.push_enabled = payload.push_enabled
    await db.flush()
    current_id = principal.device.id if principal.device else None
    return device_out(device, current_id=current_id)


@router.delete("/{device_id}", response_model=DeletedOut, summary="Revoke a device")
async def delete_device(device_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> DeletedOut:
    await revoke_device(db, principal.user, device_id)
    return DeletedOut(id=device_id)
