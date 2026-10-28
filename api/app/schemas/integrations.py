"""Stage 9 — integrations (the optional one-way Notion sync).

The secret travels in exactly one request (``POST /integrations/notion/connect``) and
is never returned: responses say only *that* one is set, plus the bot/workspace name
and the page title Notion reported.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr

from app.schemas.common import ApiModel

NotionKind = Literal["areas", "projects", "tasks", "journal", "days"]
IntegrationStatusOut = Literal["connected", "error", "disconnected"]


class NotionConnectIn(ApiModel):
    secret: SecretStr = Field(
        min_length=1,
        max_length=1000,
        description="The internal connection's secret (Notion → Developer portal → the connection → Configuration)",
    )
    page: str = Field(
        min_length=1,
        max_length=2000,
        description="Link to the page (or its id) the connection was given access to",
    )


class NotionKindsIn(ApiModel):
    kinds: list[NotionKind] = Field(max_length=5, description="The kinds to sync (others are switched off)")


class NotionSetupIn(ApiModel):
    kinds: list[NotionKind] = Field(
        min_length=1, max_length=5, description="Create the databases these kinds lack (and enable them)"
    )


class IntegrationPageOut(ApiModel):
    id: str
    title: str | None
    url: str


class NotionKindCountsOut(ApiModel):
    created: int = 0
    updated: int = 0
    trashed: int = 0
    unchanged: int = 0
    failed: int = 0
    pending: int = 0
    total: int = 0
    state: str = "ok"


class NotionKindOut(ApiModel):
    kind: NotionKind
    enabled: bool
    database: Literal["ok", "missing", "none"] = Field(
        description="ok: the database exists · missing: deleted or unshared in Notion · none: not created yet"
    )
    url: str | None
    last_run: NotionKindCountsOut | None = None


class NotionTotalsOut(ApiModel):
    created: int = 0
    updated: int = 0
    trashed: int = 0
    unchanged: int = 0
    failed: int = 0
    pending: int = 0
    total: int = 0


class NotionSyncOut(ApiModel):
    state: Literal["idle", "running"]
    run_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    last_sync_at: datetime | None
    result: Literal["ok", "partial", "error", "interrupted"] | None = Field(
        description="The last run: ok · partial (continues on the next run) · error · interrupted"
    )
    error: str | None = Field(description="notion_* code the app explains (what happened, what to do)")
    error_kind: NotionKind | None = None
    totals: NotionTotalsOut | None = None
    requests: int = 0


class NotionIntegrationOut(ApiModel):
    provider: Literal["notion"] = "notion"
    status: IntegrationStatusOut
    connected: bool
    secret_set: bool
    bot_name: str | None
    workspace_name: str | None
    page: IntegrationPageOut | None
    kinds: list[NotionKindOut]
    sync: NotionSyncOut
    error: str | None = Field(description="Why the status is error (notion_* code), else null")
    notion_version: str
    interval_minutes: int = Field(description="How often the server syncs on its own")


class IntegrationSummaryOut(ApiModel):
    provider: Literal["notion"]
    status: IntegrationStatusOut
    connected: bool
    last_sync_at: datetime | None
    error: str | None


class IntegrationListOut(ApiModel):
    items: list[IntegrationSummaryOut]


class NotionSyncStartOut(ApiModel):
    run_id: uuid.UUID
    state: Literal["running"] = "running"
