"""Application settings.

Every knob is an environment variable (see ``.env.example``); secrets never
live in the repository.  A single cached :class:`Settings` instance is shared
through :func:`get_settings`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: Any) -> list[str]:
    """Accept ``a,b``, ``["a","b"]`` and empty strings for list-like settings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


# NoDecode: pydantic-settings would otherwise JSON-decode ``a,b`` before the
# validator runs and fail on the documented comma-separated form.
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]

_INSECURE_SECRET = "change-me-openssl-rand-hex-32"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- general
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    api_title: str = "DisciplineOS API"
    api_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"

    # ---------------------------------------------------------------- logging
    log_level: str = "INFO"
    log_json: bool = False

    # ---------------------------------------------------------------- database
    database_url: str = "postgresql+psycopg://dsos:dsos@localhost:5432/dsos"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # ------------------------------------------------------------------ auth
    secret_key: str = _INSECURE_SECRET
    max_devices_default: int = 2
    pairing_code_ttl_minutes: int = 15
    # When > 0, the same pairing code can pair an additional device (laptop +
    # phone in one sitting) inside this window. 0 = strictly single use.
    cross_device_pairing_code_ttl_minutes: int = 0
    api_token_prefix: str = "dsos_pat_"
    auth_rate_limit_per_minute: int = 10

    # ------------------------------------------------------------------- cors
    cors_origins: CsvList = Field(default_factory=list)

    # --------------------------------------------------------------- locale
    default_timezone: str = "Asia/Bangkok"
    default_language: str = "ru"
    default_week_start: int = 1  # 0 = Sunday, 1 = Monday

    # -------------------------------------------------------------- scheduler
    scheduler_enabled: bool = True
    housekeeping_interval_minutes: int = 15

    # --------------------------------------------------------------------- ai
    ai_provider: Literal["openai", "anthropic", "google", "openrouter", "fake"] = "openai"
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model_vision: str = "gpt-4o"
    ai_model_text: str = "gpt-4o-mini"
    ai_daily_call_limit: int = 500
    ai_timeout_seconds: int = 60
    # per-user burst limit on the AI endpoints (the daily cap is the soft budget)
    ai_rate_limit_per_minute: int = 6
    # photo upload bounds: the client sends a ~1280 px JPEG, usually 150-400 KB
    ai_max_image_bytes: int = 3_000_000
    ai_max_image_px: int = 2048
    ai_max_text_chars: int = 1000
    # any request body (rejected with 413 before parsing/auth); fits photo + thumbnail
    max_request_bytes: int = 4_000_000
    # an analysed photo's 400 px thumbnail waits this long for the meal to be saved
    ai_pending_thumb_ttl_hours: int = 24
    # AiJob request/response payloads older than this are pruned (counters stay)
    ai_job_payload_retention_days: int = 30

    # ------------------------------------------------------------------ media
    # Meal thumbnails (only when the user opted in). A docker volume in compose.
    media_dir: str = "/data/media"

    # ------------------------------------------------------- open food facts
    # Barcode misses in the local library fall back to one live OFF lookup;
    # a usable product is cached as a library row, a miss is remembered in memory.
    off_live_lookup: bool = True
    off_base_url: str = "https://world.openfoodfacts.org"
    off_timeout_seconds: float = 6.0
    off_user_agent: str = "DisciplineOS/0.1 (self-hosted personal tracker)"
    off_lookups_per_minute: int = 20
    off_miss_ttl_hours: int = 24

    # --------------------------------------------------------------- web push
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    # Outside development the API only POSTs to the browsers' push services (FCM, Apple,
    # Mozilla, WNS — app.schemas.notifications.PUSH_SERVICE_HOSTS). Another one can be
    # added here: exact hosts or "*.example.com" (https on port 443 is still required).
    push_extra_hosts: CsvList = Field(default_factory=list)

    # --------------------------------------------------------------- frontend
    # Used only to build links inside notifications/backup mails.
    public_base_url: str = ""

    # ------------------------------------------------- data backups (stage 8)
    # `restore-backup` refuses a data.json that would unpack to more than this
    # (checked before anything is inflated: a zip bomb never reaches memory)
    backup_max_data_bytes: int = 512 * 1024 * 1024

    @field_validator("secret_key")
    @classmethod
    def _secret_must_be_set_in_prod(cls, v: str, info) -> str:
        env = (info.data or {}).get("env", "development")
        if env == "production" and (not v or v == _INSECURE_SECRET or len(v) < 24):
            raise ValueError(
                "SECRET_KEY must be set to a random value of at least 24 characters "
                "when ENV=production (try: openssl rand -hex 32)"
            )
        return v

    @field_validator("database_url")
    @classmethod
    def _normalise_driver(cls, v: str) -> str:
        """Allow plain ``postgresql://`` URLs (Heroku-style) transparently."""
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Blocking URL (Alembic, one-off scripts)."""
        return self.database_url.replace("+psycopg", "+psycopg").replace("+asyncpg", "+psycopg")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key) or self.ai_provider == "fake"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
