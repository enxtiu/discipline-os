"""Integrations (stage 9): the optional one-way Notion mirror.

Off until the owner connects it. A paired device only (an API token gets 403): the
endpoints handle a secret and change what leaves the server. Writes are rate-limited
per user; the secret is accepted by ``connect`` and never returned by anything.

Errors use the standard envelope with ``notion_*`` codes the app explains:
``notion_secret_invalid`` / ``notion_page_invalid`` (checked locally, no request),
``notion_unauthorized``, ``notion_forbidden``, ``notion_page_not_shared``,
``notion_page_in_trash``, ``notion_rate_limited``, ``notion_unavailable``,
``notion_not_connected``, ``notion_sync_running``, ``notion_secret_unreadable``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import integration_summary_out, notion_integration_out
from app.core.errors import RateLimited
from app.core.rate_limit import limiter
from app.schemas.integrations import (
    IntegrationListOut,
    NotionConnectIn,
    NotionIntegrationOut,
    NotionKindsIn,
    NotionSetupIn,
    NotionSyncStartOut,
)
from app.services.notion import sync as notion

router = APIRouter(prefix="/integrations", tags=["integrations"], dependencies=[Depends(require_device)])

# connecting or changing this happens a few times a year: these only stop a runaway client
WRITES_PER_MINUTE = 20
CONNECTS_PER_MINUTE = 6
SYNC_INTERVAL_MINUTES = 10  # the notion_sync job (app.services.hooks)


async def _write_limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"integrations:{principal.user_id}", WRITES_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many integration changes, please slow down")


async def _connect_limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"integrations-connect:{principal.user_id}", CONNECTS_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many connection attempts, please wait a minute")


Write = [Depends(_write_limit)]


async def _notion_out(principal: CurrentPrincipal, db: DbSession) -> NotionIntegrationOut:
    row = await notion.load(db, principal.user_id)
    held = await notion.lock_held(db, principal.user_id) if row is not None else False
    return notion_integration_out(row, lock_held=held, interval_minutes=SYNC_INTERVAL_MINUTES)


@router.get("", response_model=IntegrationListOut, summary="Integrations and their status (Notion)")
async def list_integrations(principal: CurrentPrincipal, db: DbSession) -> IntegrationListOut:
    return IntegrationListOut(items=[integration_summary_out(await notion.load(db, principal.user_id))])


@router.get(
    "/notion",
    response_model=NotionIntegrationOut,
    summary="The Notion mirror: connection, page, databases per kind, the last run (polled while running)",
)
async def get_notion(principal: CurrentPrincipal, db: DbSession) -> NotionIntegrationOut:
    return await _notion_out(principal, db)


@router.post(
    "/notion/connect",
    response_model=NotionIntegrationOut,
    status_code=201,
    summary="Connect: check the secret (GET /users/me) and the page (GET /pages/{id}), store the secret sealed",
    dependencies=[Depends(_write_limit), Depends(_connect_limit)],
)
async def connect_notion(payload: NotionConnectIn, principal: CurrentPrincipal, db: DbSession) -> NotionIntegrationOut:
    await notion.connect(
        db, user_id=principal.user_id, secret_raw=payload.secret.get_secret_value(), page_raw=payload.page
    )
    return await _notion_out(principal, db)


@router.post(
    "/notion/setup",
    response_model=NotionIntegrationOut,
    summary="Create the databases the given kinds lack under the page (also «create again» for a missing one)",
    dependencies=Write,
)
async def setup_notion(payload: NotionSetupIn, principal: CurrentPrincipal, db: DbSession) -> NotionIntegrationOut:
    await db.commit()  # nothing stays locked while Notion is called
    await notion.setup(principal.user_id, list(payload.kinds))
    return await _notion_out(principal, db)


@router.patch(
    "/notion",
    response_model=NotionIntegrationOut,
    summary="Choose the kinds to sync (a kind switched off keeps its database and pages in Notion)",
    dependencies=Write,
)
async def patch_notion(payload: NotionKindsIn, principal: CurrentPrincipal, db: DbSession) -> NotionIntegrationOut:
    await notion.set_kinds(db, principal.user_id, list(payload.kinds))
    return await _notion_out(principal, db)


@router.post(
    "/notion/sync",
    response_model=NotionSyncStartOut,
    status_code=202,
    summary="Sync now, in the background (409 notion_sync_running while one runs); poll GET /integrations/notion",
    dependencies=Write,
)
async def sync_notion(principal: CurrentPrincipal, db: DbSession) -> NotionSyncStartOut:
    await db.commit()
    run_id = await notion.start_background_run(principal.user_id)
    return NotionSyncStartOut(run_id=run_id)


@router.delete(
    "/notion",
    status_code=204,
    response_class=Response,
    summary="Disconnect: stop syncing, delete the secret and the links (everything in Notion stays)",
    dependencies=Write,
)
async def disconnect_notion(principal: CurrentPrincipal, db: DbSession) -> Response:
    await notion.disconnect(db, principal.user_id)
    return Response(status_code=204)
