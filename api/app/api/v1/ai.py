"""AI meal logging (stage 3): photo or text in, reviewable suggestions out.

Neither endpoint writes to the diary. The client shows the review screen and
saves with ``POST /v1/nutrition/meals`` (``ai_job_id`` + the reviewed items)
only after the user confirms.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import ai_analysis_out, ai_usage_out
from app.core.config import get_settings
from app.core.errors import RateLimited, Unprocessable
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.schemas.ai import AiAnalysisOut, AiTextIn, AiUsageOut
from app.schemas.food import MealTypeLiteral
from app.services import ai_meal

log = get_logger(__name__)
router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require_device)])


async def _ai_rate_limit(principal: CurrentPrincipal) -> None:
    """Per-user burst limit (the daily budget is enforced in the runner)."""
    allowed, retry_after = await limiter.hit(f"ai:{principal.user_id}", get_settings().ai_rate_limit_per_minute)
    if not allowed:
        raise RateLimited(retry_after, "Too many AI requests, please wait a moment")


def _tz(principal: CurrentPrincipal) -> str | None:
    return principal.settings_row.timezone if principal.settings_row else None


def _language(principal: CurrentPrincipal) -> str | None:
    row = principal.settings_row
    return getattr(row.language, "value", row.language) if row else None


async def _read_bounded(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise Unprocessable("The photo is too large", code="image_too_large", details={"max_bytes": limit})
    return data


THUMBNAIL_MAX_BYTES = 300_000


async def _read_optional_thumbnail(upload: UploadFile | None) -> bytes | None:
    """The thumbnail is optional: an oversized one is dropped, never an error."""
    if upload is None:
        return None
    data = await upload.read(THUMBNAIL_MAX_BYTES + 1)
    if len(data) > THUMBNAIL_MAX_BYTES:
        log.info("ai_thumbnail_dropped", bytes=len(data))
        return None
    return data


@router.post(
    "/analyze-meal",
    response_model=AiAnalysisOut,
    summary="Recognise foods on a meal photo (suggestions only — never saved)",
    dependencies=[Depends(_ai_rate_limit)],
)
async def analyze_meal(
    principal: CurrentPrincipal,
    db: DbSession,
    photo: Annotated[UploadFile, File(description="JPEG, ~1280 px on the long side (the app downscales)")],
    thumbnail: Annotated[
        UploadFile | None, File(description="optional 400 px JPEG, kept only with 'keep photo thumbnail' on")
    ] = None,
    meal_type: Annotated[MealTypeLiteral | None, Form()] = None,
) -> AiAnalysisOut:
    settings = get_settings()
    data = await _read_bounded(photo, settings.ai_max_image_bytes)
    thumb = await _read_optional_thumbnail(thumbnail)
    row = principal.settings_row
    analysis = await ai_meal.analyze_photo(
        db,
        user=principal.user,
        tz=_tz(principal),
        language=_language(principal),
        keep_thumbnail=bool(row and row.keep_photo_thumbnail),
        photo=data,
        thumbnail=thumb,
        meal_type=meal_type,
    )
    return ai_analysis_out(analysis)


@router.post(
    "/parse-text",
    response_model=AiAnalysisOut,
    summary="Turn a free-text meal description into suggestions (never saved)",
    dependencies=[Depends(_ai_rate_limit)],
)
async def parse_text(payload: AiTextIn, principal: CurrentPrincipal, db: DbSession) -> AiAnalysisOut:
    analysis = await ai_meal.parse_text(
        db,
        user=principal.user,
        tz=_tz(principal),
        language=_language(principal),
        text=payload.text,
        meal_type=payload.meal_type,
    )
    return ai_analysis_out(analysis)


@router.get("/usage", response_model=AiUsageOut, summary="AI availability, your calls today, the shared daily budget")
async def usage(principal: CurrentPrincipal, db: DbSession) -> AiUsageOut:
    return ai_usage_out(await ai_meal.budget(db, user_id=principal.user_id, tz=_tz(principal)))
