"""planner indexes — idempotent recurrence, case-insensitive names

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27

Stage 6 (planner) adds indexes only — no table or data is reshaped:

* ``uq_tasks_recurrence_series`` — at most one task per (series root,
  series_index): completing a recurring task twice (double tap, two devices, a
  replayed offline request) can never create a second next instance;
* ``uq_areas_user_lower_name`` / ``uq_tags_user_lower_name`` — area and tag names
  are unique per user case-insensitively ("Здоровье" == "здоровье"), so two
  concurrent creates cannot both pass the ``name_taken`` check;
* ``ix_tasks_user_completed`` — the "done" lists (today, archive, day summary).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_tasks_recurrence_series",
        "tasks",
        ["recurrence_parent_id", "series_index"],
        unique=True,
        postgresql_where=sa.text("recurrence_parent_id IS NOT NULL"),
    )
    op.create_index("ix_tasks_user_completed", "tasks", ["user_id", "completed_at"], unique=False)
    op.create_index(
        "uq_areas_user_lower_name", "areas", ["user_id", sa.literal_column("lower(name)")], unique=True
    )
    op.create_index("uq_tags_user_lower_name", "tags", ["user_id", sa.literal_column("lower(name)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_tags_user_lower_name", table_name="tags")
    op.drop_index("uq_areas_user_lower_name", table_name="areas")
    op.drop_index("ix_tasks_user_completed", table_name="tasks")
    op.drop_index("uq_tasks_recurrence_series", table_name="tasks")
