"""Stage 9 — optional one-way integrations (Notion).

Two tables of their own (migration 0004), nothing earlier reshaped:

* ``integrations`` — one row per (user, provider): status, the provider secret
  **encrypted at rest** (Fernet, ``app.core.secret_box``), the provider's state in
  ``config`` (the chosen parent page, enabled kinds, the databases created there with
  their property ids), the last run (``run_*``, ``last_sync_at``, ``last_error``,
  ``counts``). ``generation`` grows with every connect/disconnect, so a run that
  started under an older connection never writes into a newer one.
* ``integration_links`` — one row per mirrored entity: which local row
  (``entity_type`` + ``entity_id``) is which provider page (``external_id``), the
  hash of what was last written (``content_hash``) and whether the page was moved to
  the provider's trash. ``external_id IS NULL`` marks a create whose outcome is not
  known yet (it is looked up before anything is created again).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonType, TzDateTime, UiEnum, UserScopedMixin, UuidPkMixin, UuidType


class IntegrationProvider(UiEnum):
    NOTION = "notion"


class IntegrationStatus(UiEnum):
    CONNECTED = "connected"
    ERROR = "error"  # connected, but the last attempt needs the owner (bad secret, page gone…)
    DISCONNECTED = "disconnected"


ENTITY_TYPES: tuple[str, ...] = ("area", "project", "task", "journal", "day")


class Integration(UserScopedMixin, UuidPkMixin, Base):
    __tablename__ = "integrations"

    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=IntegrationStatus.DISCONNECTED.value)
    # Fernet token (ASCII); NULL when disconnected. Never returned, never logged.
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=sa.text("0"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UuidType, nullable=True)
    run_started_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    run_finished_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counts: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_integrations_user_provider"),
        CheckConstraint("provider IN ('notion')", name="provider"),
        CheckConstraint("status IN ('connected', 'error', 'disconnected')", name="status"),
    )


class IntegrationLink(UserScopedMixin, UuidPkMixin, Base):
    __tablename__ = "integration_links"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UuidType, ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # a row id (uuid) or, for days, the local date (YYYY-MM-DD)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(72), nullable=True)
    in_trash: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa.text("false"))
    synced_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("integration_id", "entity_type", "entity_id", name="uq_integration_links_entity"),
        CheckConstraint("entity_type IN ('area', 'project', 'task', 'journal', 'day')", name="entity_type"),
        Index("ix_integration_links_integration_type", "integration_id", "entity_type"),
    )
