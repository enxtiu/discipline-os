"""i18n & meta schemas."""

from __future__ import annotations

from typing import Any, Literal

from app.schemas.common import ApiModel


class I18nMessageOut(ApiModel):
    code: str
    ru: str
    en: str


class AppConfigOut(ApiModel):
    """Everything the client needs to boot: limits, feature flags, constants."""

    api_version: str
    env: str
    language_default: Literal["ru", "en"]
    week_start_default: int
    max_devices_default: int
    pairing_code_ttl_minutes: int
    features: dict[str, bool]
    ai: dict[str, Any]
    limits: dict[str, Any]
    defaults: dict[str, Any]
    scopes: list[dict[str, str]]
    units: Literal["metric"] = "metric"


class FeatureFlagsOut(ApiModel):
    features: dict[str, bool]
