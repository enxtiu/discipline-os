"""Food search plumbing (database level).

Stage 2 builds the full ranked API on top of this. Verified here is the part
that lives in the database: trigger-maintained search columns, Russian/English
full-text search, trigram typo tolerance, barcode lookup and the ownership
rules (global rows vs user-owned rows).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG

from app.db import SessionLocal
from app.models import Food, FoodSource, User

pytestmark = pytest.mark.anyio


async def _add_food(
    session,
    *,
    name_ru: str | None = None,
    name_en: str | None = None,
    aliases: list[str] | None = None,
    brand: str | None = None,
    source: FoodSource = FoodSource.SEED,
    user_id=None,
    is_global: bool = True,
    verified: bool = False,
    kcal: float = 100,
    barcode: str | None = None,
) -> Food:
    food = Food(
        user_id=user_id,
        is_global=is_global,
        name_ru=name_ru,
        name_en=name_en,
        aliases=aliases or [],
        brand=brand,
        barcode=barcode,
        source=source,
        verified=verified,
        kcal_per_100g=kcal,
        protein_per_100g=kcal * 0.1,
        fat_per_100g=kcal * 0.05,
        carbs_per_100g=kcal * 0.15,
        fiber_per_100g=2,
    )
    session.add(food)
    await session.flush()
    return food


def _fts(query: str, config: str = "russian"):
    """``plainto_tsquery('russian', q)`` — the config must be cast to regconfig."""
    return func.plainto_tsquery(sa.cast(literal(config), REGCONFIG), literal(query))


def _similar(left, query: str):
    """Trigram similarity; thresholds in tests account for real-world noise."""
    return func.similarity(func.lower(left), literal(query))


def _word_similar(left, query: str):
    """Greatest similarity between ``query`` and any part of ``left`` — this is
    what makes "куриная грудка варёная" match the food "Куриная грудка"."""
    return func.word_similarity(literal(query), func.lower(left))


async def test_search_columns_are_filled_by_the_trigger() -> None:
    async with SessionLocal() as session:
        food = await _add_food(
            session,
            name_ru="Гречка отварная",
            name_en="Buckwheat, cooked",
            aliases=["греча", "гречка"],
            brand="Домашняя кухня",
        )
        await session.commit()

        row = (
            await session.execute(
                select(
                    Food.aliases_text,
                    Food.search_vector_ru.op("@@")(_fts("гречка")).label("ru_hit"),
                    Food.search_vector_ru.op("@@")(_fts("отварная")).label("ru_hit2"),
                    Food.search_vector_en.op("@@")(_fts("buckwheat", "english")).label("en_hit"),
                ).where(Food.id == food.id)
            )
        ).one()

        assert row.aliases_text == "греча гречка"
        assert row.ru_hit is True
        assert row.ru_hit2 is True
        assert row.en_hit is True


async def test_trigger_updates_when_names_change() -> None:
    async with SessionLocal() as session:
        food = await _add_food(session, name_ru="Курица", name_en="Chicken", aliases=["курочка"])
        await session.commit()

        food.name_ru = "Куриная грудка"
        food.aliases = ["грудка"]
        await session.commit()

        row = (await session.execute(select(Food.aliases_text, Food.search_vector_ru).where(Food.id == food.id))).one()
        assert row.aliases_text == "грудка"
        assert "грудк" in row.search_vector_ru  # stemmed by the russian config


async def test_trgm_finds_typos() -> None:
    async with SessionLocal() as session:
        await _add_food(session, name_ru="Куриная грудка", name_en="Chicken breast")
        await session.commit()

        # a longer phrase must still find the shorter food name
        phrase_hits = await session.scalar(
            select(func.count()).select_from(Food).where(_word_similar(Food.name_ru, "куриная грудка варёная") > 0.6)
        )
        assert phrase_hits == 1

        # typos / missing letters
        typos = await session.scalar(
            select(func.count()).select_from(Food).where(_similar(Food.name_ru, "курина грудка") > 0.45)
        )
        assert typos == 1

        # unrelated queries must not match at the same threshold
        unrelated = await session.scalar(
            select(func.count()).select_from(Food).where(_similar(Food.name_ru, "шоколадный торт") > 0.45)
        )
        assert unrelated == 0


async def test_ranked_search_prefers_custom_verified_and_used() -> None:
    """Ranking rule stage 2 will use: custom > verified > use_count > fts."""
    async with SessionLocal() as session:
        user = User(display_name="Owner", is_owner=True)
        session.add(user)
        await session.flush()

        await _add_food(session, name_ru="Овсянка", name_en="Oats", source=FoodSource.SEED)
        custom = await _add_food(
            session,
            name_ru="Овсянка на молоке",
            name_en="Oats with milk",
            source=FoodSource.CUSTOM,
            user_id=user.id,
            is_global=False,
            verified=True,
        )
        await session.commit()

        matched = (
            select(
                Food.id,
                Food.name_ru,
                (Food.source == FoodSource.CUSTOM).label("is_custom"),
            )
            .where(
                sa.or_(
                    Food.user_id == user.id,
                    Food.is_global.is_(True),
                    Food.search_vector_ru.op("@@")(_fts("овсянка")),
                )
            )
            .order_by(
                sa.desc("is_custom"),
                sa.desc(Food.verified),
                sa.desc(Food.use_count),
                sa.desc(func.similarity(func.lower(Food.name_ru), literal("овсянка"))),
            )
        )
        rows = (await session.execute(matched)).all()

        assert len(rows) == 2
        assert rows[0].name_ru == "Овсянка на молоке"
        assert rows[0].is_custom is True
        assert rows[0].id == custom.id


async def test_barcode_lookup() -> None:
    async with SessionLocal() as session:
        await _add_food(
            session,
            name_ru="Творог 5%",
            name_en="Cottage cheese 5%",
            barcode="4600000000001",
            source=FoodSource.OFF,
        )
        await session.commit()

        found = await session.scalar(select(Food.id).where(Food.barcode == "4600000000001"))
        assert found is not None
        assert await session.scalar(select(Food.id).where(Food.barcode == "0000000000000")) is None


async def test_ownership_rules_are_enforced() -> None:
    """Global rows have no owner; custom rows must have one."""
    async with SessionLocal() as session:
        with pytest.raises(sa.exc.IntegrityError):
            await _add_food(session, name_ru="Без владельца", is_global=False, user_id=None)
            await session.commit()

    async with SessionLocal() as session:
        with pytest.raises(sa.exc.IntegrityError):
            await _add_food(session, name_ru="Глобальный с владельцем", is_global=True, user_id=None, brand="x")
            # a global row with an owner violates the same constraint
            food = (await session.execute(select(Food).where(Food.name_ru == "Глобальный с владельцем"))).scalar_one()
            food.user_id = _random_uuid()
            await session.commit()


async def test_importer_idempotency_index() -> None:
    """(source, source_id) must be unique for global rows, so re-imports upsert."""
    async with SessionLocal() as session:
        first = await _add_food(session, name_ru="Яблоко", name_en="Apple", source=FoodSource.USDA)
        first.source_id = "171688"
        await session.commit()

    async with SessionLocal() as session:
        with pytest.raises(sa.exc.IntegrityError):
            second = await _add_food(session, name_ru="Яблоко (dup)", name_en="Apple dup", source=FoodSource.USDA)
            second.source_id = "171688"
            await session.commit()


def _random_uuid():
    import uuid

    return uuid.uuid4()
