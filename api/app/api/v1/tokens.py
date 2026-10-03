"""Scoped API tokens (iOS Shortcuts, scripts, integrations)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import api_token_out
from app.core.logging import get_logger
from app.schemas.auth import (
    ApiTokenCreateIn,
    ApiTokenIssuedOut,
    ApiTokenListOut,
    ScopeOut,
)
from app.schemas.common import DeletedOut
from app.services.tokens import create_api_token, list_api_tokens, revoke_api_token, scope_catalog

log = get_logger(__name__)
router = APIRouter(
    prefix="/tokens",
    tags=["tokens"],
    dependencies=[Depends(require_device)],
)


@router.get("", response_model=ApiTokenListOut, summary="List API tokens and the scope catalog")
async def index(principal: CurrentPrincipal, db: DbSession) -> ApiTokenListOut:
    rows = await list_api_tokens(db, principal.user_id)
    return ApiTokenListOut(
        tokens=[api_token_out(row) for row in rows],
        scopes=[
            ScopeOut(
                scope=info.scope,
                description_en=info.description_en,
                description_ru=info.description_ru,
                group=info.group,
            )
            for info in scope_catalog()
        ],
    )


@router.post("", response_model=ApiTokenIssuedOut, summary="Create a scoped API token (secret shown once)")
async def create(payload: ApiTokenCreateIn, principal: CurrentPrincipal, db: DbSession) -> ApiTokenIssuedOut:
    issued = await create_api_token(
        db,
        principal.user,
        name=payload.name,
        scopes=payload.scopes,
        description=payload.description,
        expires_at=payload.expires_at,
    )
    return ApiTokenIssuedOut(token=issued.token, api_token=api_token_out(issued.row))


@router.delete("/{token_id}", response_model=DeletedOut, summary="Revoke an API token")
async def revoke(token_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> DeletedOut:
    await revoke_api_token(db, principal.user, token_id)
    return DeletedOut(id=token_id)
