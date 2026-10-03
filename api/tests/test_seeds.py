"""Seed framework + exercise library.

The contract that matters: seeds are **idempotent** (running them on every boot
must not duplicate rows) and they leave the database-maintained search columns
in a usable state for the Russian and English queries stage 4 will run.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG

from app.db import SessionLocal
from app.models.training import Exercise, MuscleGroup
from app.seeds import get_seed, list_seeds, run_seeds
from app.seeds.exercises import ROWS, seed_exercises

pytestmark = pytest.mark.anyio


async def test_registry_exposes_the_exercise_seed() -> None:
    names = [seed.name for seed in list_seeds()]
    assert "exercises" in names
    assert get_seed("exercises").run is seed_exercises

    with pytest.raises(KeyError):
        get_seed("nope")


async def test_seed_is_idempotent() -> None:
    async with SessionLocal() as session:
        first = await seed_exercises(session)
        await session.commit()

    assert first["created"] == len(ROWS)
    assert first["updated"] == 0

    async with SessionLocal() as session:
        second = await seed_exercises(session)
        await session.commit()

    assert second["created"] == 0
    assert second["updated"] == len(ROWS)

    async with SessionLocal() as session:
        total = await session.scalar(select(func.count()).select_from(Exercise))
    assert total == len(ROWS)


async def test_seeded_exercises_are_searchable_in_both_languages() -> None:
    async with SessionLocal() as session:
        await run_seeds(session, ["exercises"])

        ru = select(Exercise.name_ru).where(
            Exercise.search_vector_ru.op("@@")(func.plainto_tsquery(sa_literal(), "становая"))
        )
        rows_ru = (await session.execute(ru)).scalars().all()

        en = select(Exercise.name_en).where(
            Exercise.search_vector_en.op("@@")(func.plainto_tsquery(sa_literal("english"), "bench"))
        )
        rows_en = (await session.execute(en)).scalars().all()
        # alias text is part of the trigger-fed vector
        alias = select(Exercise.name_en).where(
            Exercise.search_vector_ru.op("@@")(func.plainto_tsquery(sa_literal(), "турник"))
        )
        rows_alias = (await session.execute(alias)).scalars().all()

    assert "Становая тяга" in rows_ru
    assert any("bench" in (name or "").lower() for name in rows_en)
    assert "Pull-up" in rows_alias


def sa_literal(config: str = "russian"):
    """`plainto_tsquery` needs an explicit regconfig cast (Cyrillic + ICU)."""
    return literal(config, REGCONFIG)


async def test_seed_sets_met_and_muscle_groups() -> None:
    async with SessionLocal() as session:
        await run_seeds(session, ["exercises"])
        row = (await session.execute(select(Exercise).where(Exercise.name_en == "Deadlift"))).scalar_one()

    assert row.met == pytest.approx(6.5)
    assert row.muscle_group == MuscleGroup.BACK
    assert "glutes" in row.secondary_muscles
    assert row.is_global is True
    assert row.user_id is None
