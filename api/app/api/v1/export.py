"""Export (stage 8): your data as JSON, as CSV files in a zip, and as a restorable
backup zip. Streamed as they are produced; a paired device only (an API token gets
403), 6 per minute per user across the three. Nothing secret ever leaves: no token
hashes, pairing codes, push keys or raw AI payloads (``app.services.export``)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import attachment_headers
from app.core.errors import RateLimited
from app.core.rate_limit import limiter
from app.core.tz import utcnow
from app.services import export
from app.services.gamification.context import context_for

router = APIRouter(prefix="/export", tags=["export"], dependencies=[Depends(require_device)])

EXPORTS_PER_MINUTE = 6


async def _limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"export:{principal.user_id}", EXPORTS_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many exports, please wait a minute")


def _binary(media_type: str, description: str) -> dict[int | str, dict[str, Any]]:
    return {
        200: {"description": description, "content": {media_type: {"schema": {"type": "string", "format": "binary"}}}}
    }


async def _stream(
    principal: CurrentPrincipal,
    db: DbSession,
    produce: Callable[[export.ExportJob], AsyncIterator[bytes]],
    media_type: str,
    extension: str,
) -> StreamingResponse:
    ctx = context_for(principal.settings_row, principal.user_id)
    job = export.ExportJob(user_id=principal.user_id, tz=ctx.tz, now=utcnow())
    # the export reads in a snapshot of its own: end the request's transaction (and the
    # device row it touched) now instead of holding it open while the file streams
    await db.commit()
    return StreamingResponse(produce(job), media_type=media_type, headers=attachment_headers(job.filename(extension)))


@router.get(
    "/json",
    response_class=StreamingResponse,
    summary="Everything you own as one JSON document (streamed)",
    responses=_binary("application/json", "disciplineos-YYYY-MM-DD.json"),
    dependencies=[Depends(_limit)],
)
async def export_json(principal: CurrentPrincipal, db: DbSession) -> StreamingResponse:
    return await _stream(principal, db, export.stream_json, "application/json", "json")


@router.get(
    "/csv",
    response_class=StreamingResponse,
    summary="One CSV per table in a zip (UTF-8 with BOM, comma separated, ISO dates)",
    responses=_binary("application/zip", "disciplineos-YYYY-MM-DD.zip"),
    dependencies=[Depends(_limit)],
)
async def export_csv(principal: CurrentPrincipal, db: DbSession) -> StreamingResponse:
    return await _stream(principal, db, export.stream_csv, "application/zip", "zip")


@router.get(
    "/backup",
    response_class=StreamingResponse,
    summary="Restorable backup: manifest.json (versions, counts, sha256) + data.json",
    responses=_binary(
        "application/zip", "disciplineos-YYYY-MM-DD.zip — restore with `python -m app.cli restore-backup`"
    ),
    dependencies=[Depends(_limit)],
)
async def export_backup(principal: CurrentPrincipal, db: DbSession) -> StreamingResponse:
    return await _stream(principal, db, export.stream_backup, "application/zip", "zip")
