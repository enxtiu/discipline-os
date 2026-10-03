"""Stage 2b: curated seed growth, USDA Foundation/SR Legacy with Russian names,
Open Food Facts quality bar + live barcode fallback, recipe yield/portions,
and the 150 ms search budget on an enlarged library.

All network access is mocked (``httpx.MockTransport``); the database is the real
PostgreSQL the rest of the suite uses.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.db import SessionLocal
from app.importers import off, usda
from app.importers.base import run_import
from app.importers.seed import DATA as SEED_PATH
from app.importers.seed import iterate_seed
from app.models import Food, FoodSource, User
from app.services import off_lookup
from app.services.search_bench import insert_synthetic_foods, run_benchmark

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _import_seed():
    async with SessionLocal() as session:
        stats = await run_import(session, iterate_seed(), source=FoodSource.SEED)
        await session.commit()
    return stats


async def _food(**where: Any) -> Food | None:
    async with SessionLocal() as session:
        return (await session.execute(select(Food).filter_by(**where))).scalars().first()


async def _add_food(name_ru: str, kcal: float, *, grams_protein: float = 0, fat: float = 0, carbs: float = 0) -> Food:
    async with SessionLocal() as session:
        food = Food(
            is_global=True,
            source=FoodSource.SEED,
            source_id=f"t-{name_ru}",
            name_ru=name_ru,
            kcal_per_100g=Decimal(str(kcal)),
            protein_per_100g=Decimal(str(grams_protein)),
            fat_per_100g=Decimal(str(fat)),
            carbs_per_100g=Decimal(str(carbs)),
            fiber_per_100g=Decimal("0"),
            verified=True,
        )
        session.add(food)
        await session.commit()
        await session.refresh(food)
        return food


# --------------------------------------------------------------------------- #
# 1. curated seed
# --------------------------------------------------------------------------- #
def test_seed_file_shape() -> None:
    entries = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    assert 290 <= len(entries) <= 340
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))
    names = [entry["name_ru"].lower() for entry in entries]
    assert len(names) == len(set(names))

    original, added = entries[:84], entries[84:]
    assert all("verified" not in entry for entry in original)  # the stage-2 rows are untouched
    assert all(entry["verified"] is False for entry in added)
    for entry in added:
        assert entry["name_ru"] and entry["name_en"] and entry["aliases"], entry["id"]
        assert entry["serving_g"] > 0
        if entry.get("alcohol"):
            continue
        atwater = 4 * entry["protein"] + 9 * entry["fat"] + 4 * entry["carbs"]
        net = atwater - 2 * entry["fiber"]
        if entry["kcal"] > 20:  # composition sanity: stated energy matches the macros
            assert 0.85 * entry["kcal"] <= max(atwater, net), entry["id"]
            assert min(atwater, net) <= 1.15 * entry["kcal"], entry["id"]


async def test_seed_import_verification_flags_and_confirmation_survives() -> None:
    first = await _import_seed()
    assert first.created == 320
    async with SessionLocal() as session:
        verified = await session.scalar(select(func.count()).where(Food.verified.is_(True)))
        unverified = await session.scalar(select(func.count()).where(Food.verified.is_(False)))
    assert (verified, unverified) == (84, 236)

    borscht = await _food(source_id="borshch")
    assert borscht is not None and borscht.verified is False and "борщ" in borscht.aliases
    async with SessionLocal() as session:
        await session.execute(update(Food).where(Food.id == borscht.id).values(verified=True))
        await session.commit()

    again = await _import_seed()
    assert (again.created, again.updated) == (0, 0)
    assert (await _food(source_id="borshch")).verified is True  # the owner's confirmation is kept


async def test_confirm_library_food_owner_only_and_no_mass_assignment(authed: Any, owner: User) -> None:
    await _import_seed()
    food = await _food(source_id="syrniki")
    assert food is not None and not food.verified

    sneaky = await authed.patch(f"/api/v1/foods/{food.id}", json={"verified": True, "kcal_per_100g": 1})
    assert sneaky.status_code == 403
    assert sneaky.json()["error"]["code"] == "global_food_readonly"
    assert float((await _food(source_id="syrniki")).kcal_per_100g) == 218.0

    confirmed = await authed.patch(f"/api/v1/foods/{food.id}", json={"verified": True})
    assert confirmed.status_code == 200 and confirmed.json()["verified"] is True

    async with SessionLocal() as session:  # a non-owner user of the same instance
        await session.execute(update(User).where(User.id == owner.id).values(is_owner=False))
        await session.commit()
    denied = await authed.patch(f"/api/v1/foods/{food.id}", json={"verified": False})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "verify_owner_only"


async def test_seed_dishes_are_searchable_in_russian(authed: Any) -> None:
    await _import_seed()
    for query, expected in (
        ("борщ", "Борщ с мясом"),
        ("сырники", "Сырники"),
        ("оливье", "Салат Оливье"),
        ("пельмени", "Пельмени (варёные)"),
        ("гречка с тушёнкой", "Гречка с тушёнкой"),
        ("ряженка", "Ряженка 4%"),
    ):
        body = (await authed.get("/api/v1/foods/search", params={"q": query})).json()
        assert body["items"], query
        assert body["items"][0]["name_ru"] == expected, (query, [item["name_ru"] for item in body["items"][:3]])
        assert body["items"][0]["verified"] is False


# --------------------------------------------------------------------------- #
# 2. USDA Foundation + SR Legacy
# --------------------------------------------------------------------------- #
def _fdc_food(fdc_id: int, description: str, category: str, nutrients: dict[int, float]) -> dict[str, Any]:
    numbers = {1008: "208", 1003: "203", 1004: "204", 1005: "205", 1079: "291", 1062: "268"}
    return {
        "fdcId": fdc_id,
        "description": description,
        "foodCategory": {"description": category},
        "foodNutrients": [
            {"nutrient": {"id": nid, "number": numbers.get(nid, ""), "unitName": "g"}, "amount": amount}
            for nid, amount in nutrients.items()
        ],
    }


FOUNDATION = [
    # Foundation rows often carry Atwater energy (2047/2048) instead of 1008
    _fdc_food(
        2646170,
        "Chicken, breast, boneless, skinless, raw",
        "Poultry Products",
        {2047: 106.0, 1003: 22.5, 1004: 1.93, 1005: 0.0},
    ),
    _fdc_food(
        2346396, "Kale, raw", "Vegetables and Vegetable Products", {2048: 39.0, 1003: 2.92, 1004: 1.49, 1005: 4.42}
    ),
]
SR_LEGACY = [
    _fdc_food(
        171477,
        "Chicken, broilers or fryers, breast, meat only, cooked, roasted",
        "Poultry Products",
        {1008: 165.0, 1003: 31.02, 1004: 3.57, 1005: 0.0, 1079: 0.0},
    ),
    _fdc_food(
        170286,
        "Buckwheat groats, roasted, cooked",
        "Cereal Grains and Pasta",
        {1008: 92.0, 1003: 3.38, 1004: 0.62, 1005: 19.94, 1079: 2.7},
    ),
    _fdc_food(171000, "Spices, mystery blend", "Spices and Herbs", {1062: 1000.0, 1003: 10.0, 1004: 5.0, 1005: 40.0}),
    _fdc_food(171001, "Broken row without energy", "Unknown", {1003: 10.0}),
    _fdc_food(171002, "Impossible macros", "Unknown", {1008: 500.0, 1003: 60.0, 1004: 30.0, 1005: 40.0}),
]


@pytest.fixture
def fdc_json(tmp_path: Path) -> tuple[Path, Path]:
    foundation = tmp_path / "FoodData_Central_foundation_food_json.json"
    foundation.write_text(json.dumps({"FoundationFoods": FOUNDATION}), encoding="utf-8")
    legacy = tmp_path / "FoodData_Central_sr_legacy_food_json.json"
    legacy.write_text(json.dumps({"SRLegacyFoods": SR_LEGACY}), encoding="utf-8")
    return foundation, legacy


def test_usda_ru_map_resolution() -> None:
    names = usda.RuNameMap.load()
    cases = {
        "Chicken, broilers or fryers, breast, meat only, cooked, roasted": "Куриная грудка, запечённая",
        "Chicken, breast, boneless, skinless, raw": "Куриная грудка, сырая",
        "Buckwheat groats, roasted, cooked": "Гречка отварная",
        "Beef, ground, 85% lean meat / 15% fat, raw": "Фарш говяжий, сырой",
        "Chickpeas (garbanzo beans, bengal gram), mature seeds, cooked, boiled, without salt": "Нут, варёный",
        "Fish, salmon, Atlantic, farmed, cooked, dry heat": "Лосось атлантический, запечённый",
        "Egg, whole, cooked, fried": "Яйцо куриное, жареное",
        "Milk, whole, 3.25% milkfat, with added vitamin D": "Молоко цельное",
        "Apricots, dried, sulfured, uncooked": "Курага",
    }
    for description, expected in cases.items():
        assert names.resolve("0", description)[0] == expected, description
    assert names.resolve("0", "Cream substitute, liquid") == (None, [])  # facet boundary, not a word prefix
    assert "курица" in names.resolve("0", "Chicken, breast, boneless, skinless, raw")[1]


def test_usda_ru_map_by_fdc_id_override(tmp_path: Path) -> None:
    custom = tmp_path / "map.json"
    custom.write_text(
        json.dumps(
            {
                "by_fdc_id": {"42": {"name_ru": "Особое блюдо", "aliases": ["особое"]}},
                "states": [],
                "rules": [{"prefix": "chicken", "name_ru": "Курица", "g": "f"}],
            }
        ),
        encoding="utf-8",
    )
    names = usda.RuNameMap.load(custom)
    assert names.resolve("42", "Chicken, raw") == ("Особое блюдо", ["особое"])
    assert names.resolve("43", "Chicken, raw") == ("Курица", [])


def test_usda_fdc_json_parsing_and_dataset_filter(fdc_json: tuple[Path, Path]) -> None:
    foundation, legacy = fdc_json
    rows = list(usda.iterate_usda(foundation))
    assert {row.source_id for row in rows} == {"2646170", "2346396"}
    chicken = next(row for row in rows if row.source_id == "2646170")
    assert chicken.kcal_per_100g == 106.0  # Atwater general energy used when 1008 is absent
    assert chicken.name_ru == "Куриная грудка, сырая" and chicken.name_en.startswith("Chicken, breast")
    assert chicken.category == "Poultry Products"

    legacy_rows = {row.source_id: row for row in usda.iterate_usda(legacy)}
    assert set(legacy_rows) == {"171477", "170286", "171000"}  # no energy / impossible macros are dropped
    assert legacy_rows["171000"].kcal_per_100g == pytest.approx(239.01, abs=0.01)  # kJ → kcal
    assert legacy_rows["171000"].name_ru is None  # unmapped: English only
    assert list(usda.iterate_usda(legacy, datasets=["foundation"])) == []
    with pytest.raises(ValueError):
        list(usda.iterate_usda(legacy, datasets=["branded"]))


def test_usda_fdc_csv_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "FoodData_Central_csv"
    bundle.mkdir()
    (bundle / "food_category.csv").write_text('"id","code","description"\n"5","0500","Poultry Products"\n')
    (bundle / "food.csv").write_text(
        '"fdc_id","data_type","description","food_category_id","publication_date"\n'
        '"171477","sr_legacy_food","Chicken, broilers or fryers, breast, meat only, cooked, roasted","5","2019-04-01"\n'
        '"2646170","foundation_food","Chicken, breast, boneless, skinless, raw","5","2023-04-20"\n'
        '"999","branded_food","Some branded nuggets","5","2023-04-20"\n'
    )
    (bundle / "food_nutrient.csv").write_text(
        '"id","fdc_id","nutrient_id","amount","data_points","derivation_id","min","max","median","footnote"\n'
        '"1","171477","1008","165","","","","","",""\n'
        '"2","171477","1003","31.02","","","","","",""\n'
        '"3","171477","1004","3.57","","","","","",""\n'
        '"4","171477","1005","0","","","","","",""\n'
        '"5","2646170","2048","112","","","","","",""\n'
        '"6","2646170","1003","22.5","","","","","",""\n'
        '"7","999","1008","250","","","","","",""\n'
    )
    rows = {row.source_id: row for row in usda.iterate_usda(bundle)}
    assert set(rows) == {"171477", "2646170"}  # branded rows are never read
    assert rows["171477"].name_ru == "Куриная грудка, запечённая"
    assert rows["171477"].category == "Poultry Products"
    assert rows["2646170"].kcal_per_100g == 112.0
    assert [row.source_id for row in usda.iterate_usda(bundle, datasets=["sr_legacy"])] == ["171477"]


def test_usda_flat_csv_still_supported(tmp_path: Path) -> None:
    flat = tmp_path / "slice.csv"
    flat.write_text('id,name,kcal,protein,fat,carbs,fiber\n100,"Kale, raw",35,2.9,1.5,4.4,4.1\n')
    (row,) = list(usda.iterate_usda(flat))
    assert (row.source_id, row.name_ru, row.kcal_per_100g) == ("100", "Кейл, сырой", 35.0)


async def test_usda_import_is_idempotent_and_searchable_in_russian(authed: Any, fdc_json: tuple[Path, Path]) -> None:
    foundation, legacy = fdc_json
    async with SessionLocal() as session:
        first = await run_import(session, usda.iterate_usda(foundation), source=FoodSource.USDA)
        second = await run_import(session, usda.iterate_usda(legacy), source=FoodSource.USDA)
        await session.commit()
    assert (first.created, second.created) == (2, 3)
    async with SessionLocal() as session:
        again = await run_import(session, usda.iterate_usda(legacy), source=FoodSource.USDA)
        await session.commit()
    assert (again.created, again.updated, again.skipped) == (0, 0, 3)
    row = await _food(source=FoodSource.USDA, source_id="171477")
    assert row.verified is False and row.is_global

    body = (await authed.get("/api/v1/foods/search", params={"q": "куриная грудка"})).json()
    names = [item["name_ru"] for item in body["items"]]
    assert "Куриная грудка, запечённая" in names and "Куриная грудка, сырая" in names
    buckwheat = (await authed.get("/api/v1/foods/search", params={"q": "гречка"})).json()
    assert buckwheat["items"][0]["name_ru"] == "Гречка отварная"


# --------------------------------------------------------------------------- #
# 3. Open Food Facts: quality bar, importer, live barcode fallback
# --------------------------------------------------------------------------- #
def _off_product(code: str = "4607001234567", **overrides: Any) -> dict[str, Any]:
    product: dict[str, Any] = {
        "code": code,
        "lang": "ru",
        "product_name_ru": "Кефир 2,5% Тестовый",
        "brands": "Тестово, Другое",
        "categories_tags": ["en:dairies", "en:kefirs"],
        "nutriments": {"energy-kcal_100g": 53, "proteins_100g": 2.9, "fat_100g": 2.5, "carbohydrates_100g": 4.0},
        "serving_quantity": 250,
        "nutrition_data_per": "100ml",
    }
    product.update(overrides)
    return product


def test_off_quality_bar() -> None:
    good = off.product_to_raw(_off_product())
    assert good is not None
    assert (good.name_ru, good.brand, good.category, good.fiber_per_100g) == (
        "Кефир 2,5% Тестовый",
        "Тестово",
        "dairies",
        0.0,
    )
    assert good.default_serving_g == 250 and good.is_liquid

    nutr = _off_product()["nutriments"]
    no_fat = {k: v for k, v in nutr.items() if k != "fat_100g"}
    assert off.product_to_raw(_off_product(nutriments=no_fat)) is None  # incomplete nutriments
    assert off.product_to_raw(_off_product(product_name_ru="", lang="de", product_name="Kefir")) is None
    assert off.product_to_raw(_off_product(product_name_ru="", lang="en", product_name="Kefir")).name_en == "Kefir"
    kj_only = {k: v for k, v in nutr.items() if not k.startswith("energy")} | {"energy_100g": 222}
    assert off.product_to_raw(_off_product(nutriments=kj_only)).kcal_per_100g == pytest.approx(53.06, abs=0.01)
    kj_as_kcal = nutr | {"energy-kcal_100g": 222}  # kJ typed into the kcal field
    assert off.product_to_raw(_off_product(nutriments=kj_as_kcal)) is None
    assert off.product_to_raw(_off_product(code="12AB")) is None


async def test_off_jsonl_import_skips_incomplete(tmp_path: Path) -> None:
    dump = tmp_path / "off.jsonl"
    lines = [
        _off_product("4600000000011"),
        _off_product("4600000000028", nutriments={"energy-kcal_100g": 50}),
        _off_product("4600000000035", product_name_ru="", lang="fr", product_name="Fromage"),
    ]
    dump.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\nnot json\n")
    async with SessionLocal() as session:
        stats = await run_import(session, off.iterate_off(dump), source=FoodSource.OFF)
        await session.commit()
    assert (stats.seen, stats.created, stats.skipped) == (4, 1, 3)


class _OffMock:
    """Counts calls; answers from a code → (status, json) table."""

    def __init__(self, answers: dict[str, tuple[int, Any]]) -> None:
        self.answers = answers
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        code = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        status, payload = self.answers.get(code, (200, {"status": 0, "status_verbose": "product not found"}))
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(status, json=payload)


@pytest.fixture
def off_live(monkeypatch: pytest.MonkeyPatch):
    def install(answers: dict[str, tuple[int, Any]]) -> _OffMock:
        mock = _OffMock(answers)
        monkeypatch.setattr(get_settings(), "off_live_lookup", True)
        off_lookup.set_transport(httpx.MockTransport(mock))
        return mock

    yield install
    off_lookup.set_transport(None)


async def test_barcode_live_fallback_caches_product(authed: Any, off_live) -> None:
    code = "4607001234567"
    mock = off_live({code: (200, {"status": 1, "product": _off_product(code)})})

    first = (await authed.get(f"/api/v1/foods/barcode/{code}")).json()
    assert first["lookup"] == "off"
    assert first["food"]["name_ru"] == "Кефир 2,5% Тестовый" and first["food"]["source"] == "off"
    assert first["food"]["verified"] is False
    assert len(mock.calls) == 1
    request = mock.calls[0]
    assert request.headers["user-agent"].startswith("DisciplineOS")
    assert "nutriments" in request.url.params["fields"]

    second = (await authed.get(f"/api/v1/foods/barcode/{code}")).json()
    assert second["lookup"] == "local" and second["food"]["id"] == first["food"]["id"]
    assert len(mock.calls) == 1  # cached: no second network call

    search = (await authed.get("/api/v1/foods/search", params={"q": "кефир тестовый"})).json()
    assert search["items"][0]["id"] == first["food"]["id"]


async def test_barcode_misses_are_remembered_and_errors_are_not(authed: Any, off_live) -> None:
    incomplete = _off_product("4600000000042", nutriments={"energy-kcal_100g": 40})
    mock = off_live(
        {
            "4600000000042": (200, {"status": 1, "product": incomplete}),
            "4600000000059": (503, {"error": "busy"}),
            "4600000000066": (200, httpx.ConnectTimeout("slow")),
        }
    )
    for _ in range(2):
        unknown = (await authed.get("/api/v1/foods/barcode/4600000000073")).json()
        assert (unknown["lookup"], unknown["food"]) == ("miss", None)
        half_empty = (await authed.get("/api/v1/foods/barcode/4600000000042")).json()
        assert half_empty["lookup"] == "miss"
    assert len(mock.calls) == 2  # each miss asked OFF once
    assert await _food(source=FoodSource.OFF) is None  # nothing half-empty was cached

    for code in ("4600000000059", "4600000000066"):
        for _ in range(2):
            assert (await authed.get(f"/api/v1/foods/barcode/{code}")).json()["lookup"] == "unavailable"
    assert len(mock.calls) == 6  # outages are retried, never cached as misses


async def test_barcode_local_first_variants_and_invalid(authed: Any, off_live) -> None:
    mock = off_live({})
    async with SessionLocal() as session:
        session.add(
            Food(
                is_global=True,
                source=FoodSource.OFF,
                source_id="0049000028911",
                name_en="Test cola",
                barcode="0049000028911",
                kcal_per_100g=Decimal("42"),
                protein_per_100g=Decimal("0"),
                fat_per_100g=Decimal("0"),
                carbs_per_100g=Decimal("10.6"),
                fiber_per_100g=Decimal("0"),
            )
        )
        await session.commit()
    upc = (await authed.get("/api/v1/foods/barcode/049000028911")).json()  # UPC-A = EAN-13 with 0
    assert upc["lookup"] == "local" and upc["food"]["name_en"] == "Test cola"
    invalid = (await authed.get("/api/v1/foods/barcode/abc")).json()
    assert invalid["lookup"] == "invalid"
    offline_mode = (await authed.get("/api/v1/foods/barcode/4600000000080", params={"live": "false"})).json()
    assert offline_mode["lookup"] == "miss"
    assert mock.calls == []


async def test_barcode_live_lookups_are_rate_limited(authed: Any, off_live, monkeypatch: pytest.MonkeyPatch) -> None:
    off_live({})
    monkeypatch.setattr(get_settings(), "off_lookups_per_minute", 2)
    statuses = [(await authed.get(f"/api/v1/foods/barcode/46000000001{n}0")).status_code for n in range(3)]
    assert statuses == [200, 200, 429]


# --------------------------------------------------------------------------- #
# 4. recipes: cooked-weight yield, portions, pinning
# --------------------------------------------------------------------------- #
async def test_recipe_yield_portion_and_logging_end_to_end(authed: Any) -> None:
    buckwheat = await _add_food("Гречка сухая тест", 343, grams_protein=13.3, fat=3.4, carbs=71.5)
    oil = await _add_food("Масло тест", 900, fat=100)
    payload = {
        "name": "Гречка с маслом",
        "cooked_weight_g": 600,  # 200 g dry groats absorb water → 600 g cooked
        "portion_size_g": 250,
        "items": [{"food_id": str(buckwheat.id), "grams": 200}, {"food_id": str(oil.id), "grams": 10}],
    }
    recipe = (await authed.post("/api/v1/recipes", json=payload)).json()
    assert recipe["total_raw_weight_g"] == 210.0 and recipe["cooked_weight_g"] == 600.0
    assert recipe["total_kcal"] == 776.0  # 686 + 90
    assert recipe["kcal_per_100g"] == 129.33  # 776 / 600 × 100 — per 100 g of the *cooked* dish
    assert recipe["portion_size_g"] == 250.0 and recipe["servings"] == 2.4
    assert recipe["per_serving"]["kcal"] == pytest.approx(323.33, abs=0.01)  # 776 × 250 / 600

    meal = await authed.post(
        "/api/v1/nutrition/meals",
        json={"meal_type": "lunch", "items": [{"recipe_id": recipe["id"], "grams": recipe["portion_size_g"]}]},
    )
    assert meal.status_code == 201, meal.text
    assert meal.json()["kcal"] == pytest.approx(recipe["per_serving"]["kcal"], abs=0.05)

    by_servings = (await authed.patch(f"/api/v1/recipes/{recipe['id']}", json={"servings": 4})).json()
    assert by_servings["portion_size_g"] == 150.0 and by_servings["per_serving"]["kcal"] == 194.0


async def test_recipe_cooked_weight_follows_ingredients_until_set(authed: Any) -> None:
    rice = await _add_food("Рис тест", 130, carbs=28)
    created = (
        await authed.post("/api/v1/recipes", json={"name": "Рис", "items": [{"food_id": str(rice.id), "grams": 300}]})
    ).json()
    assert created["cooked_weight_g"] == 300.0  # unset → mirrors the raw weight

    grown = (
        await authed.patch(
            f"/api/v1/recipes/{created['id']}", json={"items": [{"food_id": str(rice.id), "grams": 500}]}
        )
    ).json()
    assert grown["cooked_weight_g"] == 500.0  # still follows the ingredients, no stale 300

    set_yield = (await authed.patch(f"/api/v1/recipes/{created['id']}", json={"cooked_weight_g": 900})).json()
    assert set_yield["kcal_per_100g"] == pytest.approx(72.22, abs=0.01)
    kept = (
        await authed.patch(
            f"/api/v1/recipes/{created['id']}", json={"items": [{"food_id": str(rice.id), "grams": 600}]}
        )
    ).json()
    assert kept["cooked_weight_g"] == 900.0  # an explicit yield survives an ingredient edit


async def test_recipe_pinning_and_idempotent_create(authed: Any) -> None:
    egg = await _add_food("Яйцо тест", 155, grams_protein=12.6, fat=10.6, carbs=1.1)
    plain = (
        await authed.post("/api/v1/recipes", json={"name": "Омлет", "items": [{"food_id": str(egg.id), "grams": 120}]})
    ).json()
    client_id = "0192f0a0-0000-7000-8000-000000000b2b"
    body = {"id": client_id, "name": "Яйца пашот", "is_pinned": True, "items": [{"food_id": str(egg.id), "grams": 100}]}
    pinned = await authed.post("/api/v1/recipes", json=body)
    replay = await authed.post("/api/v1/recipes", json=body)
    assert pinned.status_code == replay.status_code == 201
    assert replay.json()["id"] == pinned.json()["id"] == client_id
    listing = (await authed.get("/api/v1/recipes")).json()["items"]
    assert [item["name"] for item in listing] == ["Яйца пашот", "Омлет"]  # pinned first, no duplicate

    unpinned = (await authed.patch(f"/api/v1/recipes/{client_id}", json={"is_pinned": False})).json()
    assert unpinned["is_pinned"] is False and unpinned["portion_size_g"] == pinned.json()["portion_size_g"]
    repinned = (await authed.patch(f"/api/v1/recipes/{plain['id']}", json={"is_pinned": True})).json()
    assert repinned["is_pinned"] is True


# --------------------------------------------------------------------------- #
# 5. search budget on an enlarged library
# --------------------------------------------------------------------------- #
async def test_search_stays_under_150_ms_with_a_large_library(authed: Any, owner: User) -> None:
    await _import_seed()
    async with SessionLocal() as session:
        await insert_synthetic_foods(session, 50_000)
    async with SessionLocal() as session:
        result = await run_benchmark(session, user_id=owner.id, rounds=2)
    print(f"\nsearch bench: rows={result.rows} p50={result.p50_ms} p95={result.p95_ms} max={result.max_ms} ms")
    assert result.rows >= 50_320
    assert result.max_ms < 150, result.per_query_max  # the spec budget, every single query

    # relevance survives the crowd: curated dishes beat 50k branded look-alikes
    for query, expected in (("борщ", "Борщ с мясом"), ("пельмени", "Пельмени (варёные)"), ("кефир 1%", "Кефир 1%")):
        body = (await authed.get("/api/v1/foods/search", params={"q": query})).json()
        assert body["items"][0]["name_ru"] == expected, (query, [item["name_ru"] for item in body["items"][:3]])
        assert body["took_ms"] < 150


async def test_barcode_prefers_the_users_own_food(authed: Any) -> None:
    code = "4600000000097"
    async with SessionLocal() as session:
        session.add(
            Food(
                is_global=True,
                source=FoodSource.OFF,
                source_id=code,
                name_ru="Библиотечный йогурт",
                barcode=code,
                verified=True,
                kcal_per_100g=Decimal("80"),
                protein_per_100g=Decimal("3"),
                fat_per_100g=Decimal("2"),
                carbs_per_100g=Decimal("12"),
                fiber_per_100g=Decimal("0"),
            )
        )
        await session.commit()
    own = await authed.post("/api/v1/foods", json={"name_ru": "Мой йогурт", "barcode": code, "kcal_per_100g": 70})
    assert own.status_code == 201, own.text
    for _ in range(3):
        found = (await authed.get(f"/api/v1/foods/barcode/{code}")).json()
        assert (found["lookup"], found["food"]["name_ru"]) == ("local", "Мой йогурт")
