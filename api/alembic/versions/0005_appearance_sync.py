"""appearance — the account's appearance on the user's settings (stage 10)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-29

One nullable JSONB column, ``user_settings.appearance``: the validated object of
``app/schemas/appearance.py`` (skin, mode, the stickers' parameters, large text, reduced
motion) that «Одинаково на всех устройствах» keeps in step across a user's devices.
``NULL`` means "never synced" — every existing row stays NULL, nothing is reshaped.

Self-contained: the downgrade drops exactly this column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("appearance", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("user_settings", "appearance")
