"""Stage 2: importers, ranked search, meal logging, recipes, day dashboard.

Runs against the real PostgreSQL (FTS + pg_trgm) through the ASGI client, the
same way ``test_auth.py`` does. The paired device reports ``Asia/Bangkok``
(UTC+7), which is what makes the day-boundary test meaningful.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.db import SessionLocal
from app.importers.base import RawFood, run_import
from app.models import Food, FoodSource, MealLog, User

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _add_food(
    *,
    name_ru: str | None = None,
    name_en: str | None = None,
    kcal: float = 100,
    protein: float = 10,
    fat: float = 5,
    carbs: float = 8,
    fiber: float = 1,
    source: FoodSource = FoodSource.SEED,
    source_id: str | None = None,
    user: User | None = None,
    verified: bool = True,
    barcode: str | None = None,
    aliases: list[str] | None = None,
) -> Food:
    async with SessionLocal() as session:
        food = Food(
            user_id=user.id if user else None,
            is_global=user is None,
            source=source,
            source_id=source_id,
            name_ru=name_ru,
            name_en=name_en,
            aliases=aliases or [],
            barcode=barcode,
            kcal_per_100g=Decimal(str(kcal)),
            protein_per_100g=Decimal(str(protein)),
            fat_per_100g=Decimal(str(fat)),
            carbs_per_100g=Decimal(str(carbs)),
            fiber_per_100g=Decimal(str(fiber)),
            verified=verified,
        )
        session.add(food)
        await session.commit()
        await session.refresh(food)
        return food


def _log_payload(food_id: uuid.UUID, grams: float = 100, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "meal_type": "lunch",
        "items": [{"food_id": str(food_id), "grams": grams, "match_method": "fts"}],
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# importers
# --------------------------------------------------------------------------- #
async def test_import_is_idempotent_and_normalises() -> None:
    rows = [
        RawFood(
            source=FoodSource.OFF,
            source_id="1",
            name_ru="  Тест   продукт ",
            name_en="Test product",
            kcal_per_100g=123.456,
            protein_per_100g=-5,  # clamped to 0
            barcode="4600000000001",
        ),
        RawFood(source=FoodSource.OFF, source_id="2", name_ru=None, name_en=None),  # invalid
    ]
    async with SessionLocal() as session:
        first = await run_import(session, rows, source=FoodSource.OFF)
        await session.commit()
    assert (first.created, first.skipped) == (1, 1)

    async with SessionLocal() as session:
        food = (await session.execute(Food.__table__.select())).first()  # type: ignore[attr-defined]
    assert food.name_ru == "Тест продукт"
    assert float(food.kcal_per_100g) == 123.46
    assert float(food.protein_per_100g) == 0

    async with SessionLocal() as session:
        second = await run_import(session, rows, source=FoodSource.OFF)
        await session.commit()
    assert (second.created, second.updated, second.skipped) == (0, 0, 2)


async def test_seed_bundle_loads() -> None:
    from app.importers.seed import iterate_seed

    async with SessionLocal() as session:
        stats = await run_import(session, iterate_seed(), source=FoodSource.SEED)
        await session.commit()
    assert stats.created >= 80
    async with SessionLocal() as session:
        from sqlalchemy import func, select

        count = await session.scalar(select(func.count()).select_from(Food))
    assert count == stats.created


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
async def test_search_ranking_prefers_own_and_favorites(authed: Any) -> None:
    global_food = await _add_food(name_ru="Протеиновый батончик", name_en="Protein bar", source_id="g1")
    other_global = await _add_food(name_ru="Протеиновый батончик фитнес", name_en="Protein bar fit", source_id="g1b")
    own = await _add_food(
        name_ru="Мой протеиновый батончик",
        name_en="My protein bar",
        source=FoodSource.CUSTOM,
        user=(await _owner()),
    )
    response = await authed.get("/api/v1/foods/search", params={"q": "протеиновый батончик"})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["id"] == str(own.id), items[:2]

    # favouriting a library row lifts it above the other anonymous rows
    await authed.post("/api/v1/foods/favorites", json={"food_id": str(global_food.id)})
    response = await authed.get("/api/v1/foods/search", params={"q": "протеиновый батончик"})
    ids = [item["id"] for item in response.json()["items"]]
    assert ids.index(str(global_food.id)) < ids.index(str(other_global.id))
    assert ids[0] == str(own.id)


async def _owner() -> User:
    async with SessionLocal() as session:
        from sqlalchemy import select

        return (await session.execute(select(User).where(User.is_owner.is_(True)))).scalars().one()


async def test_search_typo_and_english(authed: Any) -> None:
    await _add_food(name_ru="Гречневая крупа", name_en="Buckwheat groats", source_id="g2", aliases=["гречка"])
    await _add_food(name_ru="Куриная грудка", name_en="Chicken breast", source_id="g3")

    response = await authed.get("/api/v1/foods/search", params={"q": "гречка"})
    assert response.json()["items"][0]["name_ru"] == "Гречневая крупа"

    response = await authed.get("/api/v1/foods/search", params={"q": "chicken brest"})  # typo
    items = response.json()["items"]
    assert items and items[0]["name_en"] == "Chicken breast"
    assert items[0]["match_method"] in {"trgm", "fts"}

    response = await authed.get("/api/v1/foods/search", params={"q": "куриная грудка"})
    assert response.json()["took_ms"] < 150  # the stage-2 latency budget


async def test_search_hides_other_users_foods(authed: Any) -> None:
    stranger = await _add_food(name_ru="Чужой суп", source=FoodSource.CUSTOM, user=await _other_user())
    response = await authed.get("/api/v1/foods/search", params={"q": "чужой суп"})
    assert all(item["id"] != str(stranger.id) for item in response.json()["items"])


async def _other_user() -> User:
    async with SessionLocal() as session:
        user = User(display_name="Stranger", is_owner=False)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_barcode_lookup(authed: Any) -> None:
    food = await _add_food(name_ru="Кефир", name_en="Kefir", source_id="g4", barcode="4600000000004")
    response = await authed.get("/api/v1/foods/barcode/4600000000004")
    assert response.json()["food"]["id"] == str(food.id)
    response = await authed.get("/api/v1/foods/barcode/0000000000000")
    assert response.json()["food"] is None


# --------------------------------------------------------------------------- #
# meal logging
# --------------------------------------------------------------------------- #
async def test_meal_snapshot_math_and_idempotency(authed: Any) -> None:
    food = await _add_food(name_ru="Рис отварной", kcal=130, protein=2.7, fat=0.3, carbs=28.2, source_id="g5")
    client_uuid = str(uuid.uuid4())
    payload = _log_payload(food.id, grams=250, id=client_uuid)

    first = await authed.post("/api/v1/nutrition/meals", json=payload)
    assert first.status_code == 201, first.text
    meal = first.json()
    assert meal["kcal"] == 325.0  # 130 * 2.5
    assert meal["protein_g"] == 6.75
    assert meal["item_count"] == 1

    again = await authed.post("/api/v1/nutrition/meals", json=payload)
    assert again.json()["id"] == meal["id"]  # replay is a no-op


async def test_meal_local_day_uses_user_timezone(authed: Any) -> None:
    """18:00 UTC is 01:00 the *next* day in Asia/Bangkok (the paired device tz)."""
    food = await _add_food(name_ru="Вода", kcal=0, protein=0, fat=0, carbs=0, source_id="g6")
    logged_at = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
    response = await authed.post(
        "/api/v1/nutrition/meals",
        json=_log_payload(food.id, grams=100, logged_at=logged_at.isoformat()),
    )
    assert response.status_code == 201
    assert response.json()["local_day"] == "2026-10-02"

    day = await authed.get("/api/v1/nutrition/day", params={"date": "2026-10-02"})
    assert day.json()["logged_meals"] == 1
    empty = await authed.get("/api/v1/nutrition/day", params={"date": "2026-10-01"})
    assert empty.json()["logged_meals"] == 0


async def test_day_totals_water_and_soft_delete(authed: Any) -> None:
    food = await _add_food(name_ru="Творог", kcal=121, protein=17.2, fat=5.0, carbs=1.8, source_id="g7")
    meal = (await authed.post("/api/v1/nutrition/meals", json=_log_payload(food.id, grams=100))).json()
    await authed.post("/api/v1/nutrition/meals", json=_log_payload(food.id, grams=100, meal_type="dinner"))
    await authed.post("/api/v1/nutrition/water", json={"amount_ml": 250})
    await authed.post("/api/v1/nutrition/water", json={"amount_ml": 250})

    day = (await authed.get("/api/v1/nutrition/day")).json()
    assert day["logged_meals"] == 2
    assert day["totals"]["kcal"] == 242.0
    assert day["water_ml"] == 500
    types = {bucket["meal_type"]: bucket for bucket in day["by_meal_type"]}
    assert types["lunch"]["meals"] == 1 and types["dinner"]["kcal"] == 121.0

    assert (await authed.delete(f"/api/v1/nutrition/meals/{meal['id']}")).status_code == 200
    day = (await authed.get("/api/v1/nutrition/day")).json()
    assert day["logged_meals"] == 1 and day["totals"]["kcal"] == 121.0


async def test_copy_previous_meal(authed: Any) -> None:
    food = await _add_food(name_ru="Суп", kcal=60, protein=2, fat=2, carbs=8, source_id="g8")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    await authed.post(
        "/api/v1/nutrition/meals",
        json=_log_payload(food.id, grams=300, meal_type="lunch", logged_at=f"{yesterday}T05:00:00+00:00"),
    )
    response = await authed.post(
        "/api/v1/nutrition/meals/copy",
        json={"from_day": str(yesterday), "meal_type": "lunch", "to_meal_type": "dinner"},
    )
    assert response.status_code == 200, response.text
    copied = response.json()
    assert copied["copied"] == 1
    assert copied["meals"][0]["meal_type"] == "dinner"
    assert copied["meals"][0]["source"] == "copy"
    assert copied["meals"][0]["kcal"] == 180.0

    missing = await authed.post("/api/v1/nutrition/meals/copy", json={"from_day": "2020-01-01"})
    assert missing.status_code == 404


async def test_recents_track_logging(authed: Any) -> None:
    food = await _add_food(name_ru="Банан", kcal=89, source_id="g9")
    await authed.post("/api/v1/nutrition/meals", json=_log_payload(food.id, grams=120))
    await authed.post("/api/v1/nutrition/meals", json=_log_payload(food.id, grams=120))
    recents = (await authed.get("/api/v1/foods/recents")).json()["items"]
    assert recents[0]["food"]["name_ru"] == "Банан"
    assert recents[0]["use_count"] == 2
    assert recents[0]["last_grams"] == 120.0


# --------------------------------------------------------------------------- #
# recipes
# --------------------------------------------------------------------------- #
async def test_recipe_macros_and_logging(authed: Any) -> None:
    buck = await _add_food(name_ru="Гречка отварная", kcal=110, protein=4.2, fat=1.1, carbs=21.3, source_id="g10")
    chicken = await _add_food(name_ru="Курица", kcal=165, protein=31, fat=3.6, carbs=0, source_id="g11")

    response = await authed.post(
        "/api/v1/recipes",
        json={
            "name": "Гречка с курицей",
            "servings": 2,
            "items": [{"food_id": str(buck.id), "grams": 300}, {"food_id": str(chicken.id), "grams": 240}],
        },
    )
    assert response.status_code == 201, response.text
    recipe = response.json()
    assert recipe["total_kcal"] == 726.0  # 330 + 396
    assert recipe["cooked_weight_g"] == 540.0
    assert recipe["kcal_per_100g"] == 134.44
    assert recipe["per_serving"]["kcal"] == 363.0
    assert len(recipe["items"]) == 2
    assert recipe["items"][0]["snapshot_name"] == "Гречка отварная"

    logged = await authed.post(
        "/api/v1/nutrition/meals",
        json={"meal_type": "dinner", "items": [{"recipe_id": recipe["id"], "grams": 270}]},
    )
    assert logged.status_code == 201
    assert logged.json()["kcal"] == 362.99 or logged.json()["kcal"] == 363.0

    # editing ingredients recomputes everything
    patched = await authed.patch(
        f"/api/v1/recipes/{recipe['id']}",
        json={"items": [{"food_id": str(buck.id), "grams": 100}]},
    )
    assert patched.json()["total_kcal"] == 110.0
    assert patched.json()["per_serving"]["kcal"] == 55.0  # two servings


async def test_recipe_is_owner_scoped(authed: Any, client: Any) -> None:
    buck = await _add_food(name_ru="Рис", kcal=130, source_id="g12")
    created = (
        await authed.post(
            "/api/v1/recipes", json={"name": "Мой рис", "items": [{"food_id": str(buck.id), "grams": 100}]}
        )
    ).json()
    # without a token the recipe is unreachable (authed == client: drop the header)
    client.headers.pop("Authorization", None)
    assert (await client.get(f"/api/v1/recipes/{created['id']}")).status_code == 401


# --------------------------------------------------------------------------- #
# custom foods & library rules
# --------------------------------------------------------------------------- #
async def test_custom_food_crud_and_global_readonly(authed: Any) -> None:
    created = await authed.post(
        "/api/v1/foods",
        json={"name_ru": "Мой коктейль", "kcal_per_100g": 60, "protein_per_100g": 10},
    )
    assert created.status_code == 201, created.text
    food_id = created.json()["id"]

    patched = await authed.patch(f"/api/v1/foods/{food_id}", json={"kcal_per_100g": 70})
    assert patched.json()["kcal_per_100g"] == 70.0

    library = await _add_food(name_ru="Библиотечный", source_id="g13")
    forbidden = await authed.patch(f"/api/v1/foods/{library.id}", json={"kcal_per_100g": 1})
    assert forbidden.status_code == 403
    allowed = await authed.patch(f"/api/v1/foods/{library.id}", json={"verified": True})
    assert allowed.status_code == 200 and allowed.json()["verified"] is True

    deleted = await authed.delete(f"/api/v1/foods/{food_id}")
    assert deleted.status_code == 200
    gone = await authed.get(f"/api/v1/foods/{food_id}")
    assert gone.status_code == 404


async def test_meal_item_validation(authed: Any) -> None:
    food = await _add_food(name_ru="Хлеб", kcal=265, source_id="g14")
    bad = await authed.post("/api/v1/nutrition/meals", json=_log_payload(food.id, grams=-5))
    assert bad.status_code == 422
    incomplete = await authed.post(
        "/api/v1/nutrition/meals",
        json={"meal_type": "snack", "items": [{"grams": 100}]},
    )
    assert incomplete.status_code == 422  # no food/recipe and no custom macros


async def test_water_bounds(authed: Any) -> None:
    assert (await authed.post("/api/v1/nutrition/water", json={"amount_ml": 0})).status_code == 422
    assert (await authed.post("/api/v1/nutrition/water", json={"amount_ml": 6000})).status_code == 422


async def test_meal_log_row_is_soft_deleted(authed: Any, db: Any) -> None:
    food = await _add_food(name_ru="Каша", kcal=90, source_id="g15")
    meal = (await authed.post("/api/v1/nutrition/meals", json=_log_payload(food.id, grams=100))).json()
    await authed.delete(f"/api/v1/nutrition/meals/{meal['id']}")
    async with SessionLocal() as session:
        from sqlalchemy import select

        row = await session.scalar(select(MealLog).where(MealLog.id == uuid.UUID(meal["id"])))
    assert row is not None and row.is_deleted is True
