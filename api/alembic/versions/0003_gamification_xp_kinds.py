"""gamification — two new XP kinds: steps_target, achievement_unlocked

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28

Stage 8 awards XP for reaching the step target and for unlocking an
achievement. Enums are VARCHAR + CHECK precisely so that a new member is a
constraint swap: no table is reshaped, no row is touched on upgrade.

Downgrade: rows of the two new kinds cannot satisfy the old constraint; they are
re-labelled ``manual`` (points and dedupe keys kept, so nobody loses XP) before
the old constraint is restored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_xp_events_xp_kind"
OLD_KINDS = (
    "meal_logged",
    "protein_target",
    "fiber_target",
    "water_target",
    "kcal_in_zone",
    "workout_completed",
    "workout_volume",
    "task_completed",
    "project_completed",
    "timer_session",
    "daily_review",
    "sleep_logged",
    "weight_logged",
    "streak_bonus",
    "quest_completed",
    "manual",
)
NEW_KINDS = ("steps_target", "achievement_unlocked")


def _check(kinds: Sequence[str]) -> str:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    return f"kind IN ({values})"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "xp_events", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "xp_events", sa.text(_check(OLD_KINDS + NEW_KINDS)))


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE xp_events SET kind = 'manual' WHERE kind IN :kinds").bindparams(
            sa.bindparam("kinds", value=NEW_KINDS, expanding=True)
        )
    )
    op.drop_constraint(op.f(CONSTRAINT), "xp_events", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "xp_events", sa.text(_check(OLD_KINDS)))
