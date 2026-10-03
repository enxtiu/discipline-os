"""Auth schemas: pairing, devices, sessions, scoped API tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel, ORMModel, ScopesList
from app.schemas.settings import TargetsOut, UserSettingsOut


# --------------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------------- #
class PairingDeviceIn(ApiModel):
    """What the new device reports about itself."""

    name: str = Field(default="Device", max_length=120)
    platform: Literal["ios", "android", "web", "macos", "windows", "linux", "other"] | None = None
    model: str | None = Field(default=None, max_length=120)
    app_version: str | None = Field(default=None, max_length=32)
    # stable client fingerprint (hashed client-side is preferred, raw is fine too)
    fingerprint: str | None = Field(default=None, max_length=200)


class PairingRequestIn(ApiModel):
    code: str = Field(min_length=4, max_length=20, description="One-time pairing code")
    device: PairingDeviceIn = Field(default_factory=PairingDeviceIn)
    # auto-detected on the device during onboarding
    timezone: str | None = Field(default=None, max_length=64)
    language: Literal["ru", "en"] | None = None

    @field_validator("code")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class DeviceOut(ORMModel):
    id: uuid.UUID
    name: str
    platform: str | None = None
    model: str | None = None
    app_version: str | None = None
    token_prefix: str
    scopes: list[str] = Field(default_factory=list)
    is_active: bool
    push_enabled: bool = False
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None = None
    last_ip: str | None = None
    revoked_at: datetime | None = None
    is_current: bool = False


class DeviceListOut(ApiModel):
    devices: list[DeviceOut]
    active_count: int
    max_devices: int


class DeviceUpdateIn(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    push_enabled: bool | None = None


class PairingResultOut(ApiModel):
    token: str = Field(description="Long-lived device token — store it in localStorage")
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    device: DeviceOut
    user: UserSummaryOut
    settings: UserSettingsOut
    reused_device: bool = False


class PairingCodeInfoOut(ApiModel):
    """Only returned by the CLI, never by a public endpoint."""

    code: str
    expires_at: datetime
    max_uses: int


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
class UserSummaryOut(ORMModel):
    id: uuid.UUID
    display_name: str
    is_owner: bool
    status: str
    max_devices: int
    created_at: datetime


class SessionOut(ApiModel):
    user: UserSummaryOut
    settings: UserSettingsOut
    device: DeviceOut | None = None
    api_token: ApiTokenOut | None = None
    auth_kind: Literal["device", "api_token"]
    scopes: list[str]
    server_time: datetime
    # convenience: the target block so the shell can render rings immediately
    targets: TargetsOut


# --------------------------------------------------------------------------- #
# Scoped API tokens
# --------------------------------------------------------------------------- #
class ScopeOut(ApiModel):
    scope: str
    description_en: str
    description_ru: str
    group: str


class ApiTokenCreateIn(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: ScopesList
    description: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None

    @field_validator("scopes")
    @classmethod
    def _not_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one scope is required")
        return value


class ApiTokenOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    token_prefix: str
    scopes: list[str]
    is_active: bool
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    request_count: int = 0
    created_at: datetime


class ApiTokenIssuedOut(ApiModel):
    token: str = Field(description="Shown once — copy it into the iOS Shortcut")
    api_token: ApiTokenOut


class ApiTokenListOut(ApiModel):
    tokens: list[ApiTokenOut]
    scopes: list[ScopeOut]


PairingResultOut.model_rebuild()
SessionOut.model_rebuild()
