"""Pydantic v2 schemas (request/response models) — grouped by domain.

Stage 1 ships ``common``, ``auth``, ``settings`` and ``meta``; later stages add
``nutrition``, ``training``, ``planner``, ``gamification`` next to them.
"""

from app.schemas.auth import (
    ApiTokenCreateIn,
    ApiTokenIssuedOut,
    ApiTokenListOut,
    ApiTokenOut,
    DeviceListOut,
    DeviceOut,
    DeviceUpdateIn,
    PairingCodeInfoOut,
    PairingDeviceIn,
    PairingRequestIn,
    PairingResultOut,
    ScopeOut,
    SessionOut,
    UserSettingsOut,
    UserSummaryOut,
)
from app.schemas.common import (
    ApiModel,
    DeletedOut,
    ErrorBody,
    ErrorResponse,
    HealthOut,
    IdOut,
    OkOut,
    Page,
    SyncTimestamp,
    TimezoneHint,
    VersionOut,
)
from app.schemas.meta import AppConfigOut, FeatureFlagsOut, I18nMessageOut
from app.schemas.settings import (
    BodyParamsOut,
    OnboardingIn,
    SettingsEnvelopeOut,
    SettingsUpdateIn,
    TargetPreviewIn,
    TargetsIn,
    TargetsOut,
)

__all__ = [
    # common
    "ApiModel",
    "DeletedOut",
    "ErrorBody",
    "ErrorResponse",
    "HealthOut",
    "IdOut",
    "OkOut",
    "Page",
    "SyncTimestamp",
    "TimezoneHint",
    "VersionOut",
    # auth
    "ApiTokenCreateIn",
    "ApiTokenIssuedOut",
    "ApiTokenListOut",
    "ApiTokenOut",
    "DeviceListOut",
    "DeviceOut",
    "DeviceUpdateIn",
    "PairingCodeInfoOut",
    "PairingDeviceIn",
    "PairingRequestIn",
    "PairingResultOut",
    "ScopeOut",
    "SessionOut",
    "UserSettingsOut",
    "UserSummaryOut",
    # settings
    "BodyParamsOut",
    "OnboardingIn",
    "SettingsEnvelopeOut",
    "SettingsUpdateIn",
    "TargetPreviewIn",
    "TargetsIn",
    "TargetsOut",
    # meta
    "AppConfigOut",
    "FeatureFlagsOut",
    "I18nMessageOut",
]
