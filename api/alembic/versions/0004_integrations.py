"""integrations — the optional one-way Notion sync (stage 9)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-28

Two new tables, nothing existing is touched (self-contained: the downgrade drops
exactly these two tables, so the migration can be re-chained after another one):

* ``integrations`` — one row per (user, provider): status, the provider secret
  encrypted at rest (Fernet), the provider state in ``config`` (parent page, enabled
  kinds, the databases created there), the last run and its counts;
* ``integration_links`` — which local row (``entity_type`` + ``entity_id``) is which
  provider page, the hash of what was written last and whether the page was trashed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("config", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("counts", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("provider IN ('notion')", name=op.f("ck_integrations_provider")),
        sa.CheckConstraint("status IN ('connected', 'error', 'disconnected')", name=op.f("ck_integrations_status")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_integrations_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integrations")),
        sa.UniqueConstraint("user_id", "provider", name="uq_integrations_user_provider"),
    )
    op.create_index(op.f("ix_integrations_user_id"), "integrations", ["user_id"], unique=False)

    op.create_table(
        "integration_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=72), nullable=True),
        sa.Column("in_trash", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('area', 'project', 'task', 'journal', 'day')",
            name=op.f("ck_integration_links_entity_type"),
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["integrations.id"],
            name=op.f("fk_integration_links_integration_id_integrations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_integration_links_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_links")),
        sa.UniqueConstraint("integration_id", "entity_type", "entity_id", name="uq_integration_links_entity"),
    )
    op.create_index(op.f("ix_integration_links_integration_id"), "integration_links", ["integration_id"], unique=False)
    op.create_index(op.f("ix_integration_links_user_id"), "integration_links", ["user_id"], unique=False)
    op.create_index(
        "ix_integration_links_integration_type", "integration_links", ["integration_id", "entity_type"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_integration_links_integration_type", table_name="integration_links")
    op.drop_index(op.f("ix_integration_links_user_id"), table_name="integration_links")
    op.drop_index(op.f("ix_integration_links_integration_id"), table_name="integration_links")
    op.drop_table("integration_links")
    op.drop_index(op.f("ix_integrations_user_id"), table_name="integrations")
    op.drop_table("integrations")
