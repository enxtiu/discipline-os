"""Meta endpoints: health, version, client bootstrap config, i18n labels.

These are the only endpoints that work without a token (the pairing screen has
to render *something*). Nothing sensitive is exposed here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.tz import utcnow
from app.db import get_db, ping, search_ready
from app.schemas.common import HealthOut, VersionOut
from app.schemas.meta import AppConfigOut, I18nMessageOut
from app.services.meta_i18n import I18N_ENUM_LABELS
from app.services.scheduler import scheduler_status
from app.services.tokens import scope_catalog

log = get_logger(__name__)
router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/health", response_model=HealthOut, summary="Liveness / readiness probe")
async def health(db: AsyncSession = Depends(get_db)) -> HealthOut:
    settings = get_settings()
    db_ok = await ping()
    fuzzy = await search_ready() if db_ok else None
    status = "ok" if db_ok else "degraded"
    if not db_ok:
        log.warning("health_degraded", reason="database_unreachable")
    elif fuzzy is False:
        log.warning(
            "health_degraded",
            reason="trigram_search_disabled",
            hint="recreate the database with a UTF-8 locale (see docker-compose POSTGRES_INITDB_ARGS)",
        )
    return HealthOut(
        status=status,
        version=settings.api_version,
        env=settings.env,
        time_utc=utcnow(),
        database=db_ok,
        fuzzy_search=fuzzy,
        scheduler=scheduler_status(),
    )


@router.get("/version", response_model=VersionOut, summary="Build & feature information")
async def version() -> VersionOut:
    settings = get_settings()
    return VersionOut(
        name=settings.api_title,
        version=settings.api_version,
        env=settings.env,
        api_prefix=settings.api_v1_prefix,
        features=feature_flags(),
    )


def feature_flags() -> dict[str, bool]:
    """Server-side truth about which stage features are live.

    The client hides UI it cannot use (photo logging without an AI key, push
    before stage 7, …) instead of failing at runtime.
    """
    settings = get_settings()
    return {
        "ai_meal_photo": settings.ai_enabled,
        "ai_meal_text": settings.ai_enabled,
        "barcode_scan": True,  # local DB + Open Food Facts lookup
        "push_notifications": bool(settings.vapid_public_key and settings.vapid_private_key),
        "scheduler": bool(settings.scheduler_enabled),
        "export": True,
        "multi_user": False,  # flips to True when a second user is added
    }


@router.get("/app-config", response_model=AppConfigOut, summary="Client bootstrap configuration")
async def app_config() -> AppConfigOut:
    settings = get_settings()
    return AppConfigOut(
        api_version=settings.api_version,
        env=settings.env,
        language_default=settings.default_language,  # type: ignore[arg-type]
        week_start_default=settings.default_week_start,
        max_devices_default=settings.max_devices_default,
        pairing_code_ttl_minutes=settings.pairing_code_ttl_minutes,
        features=feature_flags(),
        ai={
            "enabled": settings.ai_enabled,
            "provider": settings.ai_provider if settings.ai_enabled else None,
            "photo_downscale_px": 1280,
            "thumbnail_px": 400,
            "daily_call_limit": settings.ai_daily_call_limit,
        },
        limits={
            "safe_floor_male_kcal": 1500,
            "safe_floor_female_kcal": 1200,
            "absolute_floor_kcal": 1000,
            "fiber_per_1000_kcal": 14,
            "water_ml_per_kg_min": 30,
            "water_ml_per_kg_max": 35,
            "max_photo_upload_mb": 6,
        },
        defaults={
            "target_kcal": None,
            "target_steps": 8000,
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "07:00",
            "water_quick_add_ml": [200, 250, 330, 500, 750],
            "goal_rate_presets": [10, 15, 20],
            "activity_levels": ["sedentary", "light", "moderate", "active", "very_active"],
        },
        scopes=[
            {
                "scope": info.scope,
                "description_en": info.description_en,
                "description_ru": info.description_ru,
                "group": info.group,
            }
            for info in scope_catalog()
        ],
    )


@router.get(
    "/i18n/messages",
    response_model=list[I18nMessageOut],
    summary="Enum/status labels in ru & en (keeps DB enums out of the UI code)",
)
async def i18n_messages() -> list[I18nMessageOut]:
    return [I18nMessageOut(**item) for item in I18N_ENUM_LABELS]
