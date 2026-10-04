"""Stage 3: AI meal logging (photo + text), provider abstraction, review → save.

Everything runs against the deterministic ``fake`` provider or real provider
adapters behind an ``httpx.MockTransport`` — no network, no API key.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from app.ai import fake, providers
from app.ai.prompt_store import CURRENT, PROMPTS_DIR, load_prompt
from app.ai.providers import AiProviderError, AiRequest, to_gemini_schema
from app.ai.schema import MAX_ITEMS, MEAL_ITEMS_SCHEMA, AiMealResult
from app.core.config import get_settings
from app.db import SessionLocal
from app.importers.base import run_import
from app.importers.seed import iterate_seed
from app.models import AiJob, AiJobKind, AiJobStatus, AiUsageDaily, Food, FoodSource, MealItem, MealLog, User
from app.services import ai_meal, media

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# fixtures & helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _media_and_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "media_dir", str(tmp_path / "media"))
    monkeypatch.setattr(get_settings(), "ai_provider", "fake")
    monkeypatch.setattr(ai_meal.runner, "RETRY_DELAY_SECONDS", 0)
    fake.SCRIPT.clear()
    fake.CALLS.clear()
    yield
    fake.SCRIPT.clear()
    fake.CALLS.clear()
    providers.set_transport(None)


def jpeg(width: int = 1280, height: int = 960, pad: int = 4000) -> bytes:
    """A structurally valid baseline JPEG header (SOI, APP0, SOF0) + padding.

    Enough for the server's marker parser; the fake provider never decodes it.
    """
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + sof0 + b"\x00" * pad + b"\xff\xd9"


async def _seed() -> None:
    async with SessionLocal() as session:
        await run_import(session, iterate_seed(), source=FoodSource.SEED)
        await session.commit()


async def _count(model) -> int:
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _job(job_id: str) -> AiJob:
    async with SessionLocal() as session:
        return (await session.execute(select(AiJob).where(AiJob.id == uuid.UUID(job_id)))).scalar_one()


def _item_payload(item: dict[str, Any], *, grams: float | None = None) -> dict[str, Any]:
    """What the review screen sends for one confirmed suggestion."""
    payload: dict[str, Any] = {
        "grams": grams or item["estimated_grams"],
        "confidence": item["confidence"],
        "ai_name_raw": item["name_ru"],
        "is_edited": grams is not None,
    }
    if item["match"]:
        payload |= {"food_id": item["match"]["food"]["id"], "match_method": item["match"]["match_method"]}
    else:
        per100 = item["ai_per_100g"]
        payload |= {
            "item_source": "ai",
            "name": item["name_ru"],
            "name_en": item["name_en"],
            "kcal_per_100g": per100["kcal_per_100g"],
            "protein_per_100g": per100["protein_per_100g"],
            "fat_per_100g": per100["fat_per_100g"],
            "carbs_per_100g": per100["carbs_per_100g"],
            "fiber_per_100g": per100["fiber_per_100g"],
            "match_method": "ai",
        }
    return payload


def _reply(items: list[dict[str, Any]], notes: str | None = None) -> str:
    return json.dumps({"items": items, "notes": notes}, ensure_ascii=False)


def _ai_item(**overrides: Any) -> dict[str, Any]:
    item = {
        "name_ru": "Гречка отварная",
        "name_en": "Buckwheat, cooked",
        "estimated_grams": 200,
        "confidence": 0.8,
        "kcal_per_100g": 110,
        "protein_per_100g": 4.2,
        "fat_per_100g": 1.1,
        "carbs_per_100g": 21.3,
        "fiber_per_100g": 2.7,
    }
    item.update(overrides)
    return item


# --------------------------------------------------------------------------- #
# prompts & schema
# --------------------------------------------------------------------------- #
def test_prompts_are_versioned_files() -> None:
    for prompt_id, version in CURRENT.items():
        assert (PROMPTS_DIR / f"{prompt_id}.{version}.md").exists()
        prompt = load_prompt(prompt_id)
        assert prompt.version == version and "PER 100 g" in prompt.system and "JSON" in prompt.system
    text_prompt = load_prompt("parse_food_text")
    rendered = text_prompt.render_user(locale="ru", meal_hint="обед", text="гречка {locale} {meal_hint}")
    assert rendered.endswith("гречка {locale} {meal_hint}")  # user text is never re-substituted


def test_strict_schema_and_validation_bounds() -> None:
    item_schema = MEAL_ITEMS_SCHEMA["properties"]["items"]["items"]
    assert item_schema["additionalProperties"] is False
    assert set(item_schema["required"]) == set(item_schema["properties"])  # strict mode: all required
    assert "maxItems" not in MEAL_ITEMS_SCHEMA["properties"]["items"]  # rejected by OpenAI strict mode

    with pytest.raises(ValueError):
        AiMealResult.model_validate({"items": [_ai_item(estimated_grams=0)], "notes": None})
    with pytest.raises(ValueError):
        AiMealResult.model_validate({"items": [_ai_item(protein_per_100g=60, carbs_per_100g=60)], "notes": None})
    adjusted = AiMealResult.model_validate({"items": [_ai_item(kcal_per_100g=600)], "notes": None}).items[0]
    assert adjusted.kcal_adjusted and adjusted.kcal_per_100g == pytest.approx(111.9, abs=0.1)
    many = AiMealResult.model_validate({"items": [_ai_item()] * 20, "notes": "x" * 900})
    assert len(many.items) == MAX_ITEMS and len(many.notes or "") == 500

    gemini = to_gemini_schema(MEAL_ITEMS_SCHEMA)
    assert gemini["type"] == "OBJECT" and gemini["properties"]["notes"] == {
        "nullable": True,
        "type": "STRING",
        "description": MEAL_ITEMS_SCHEMA["properties"]["notes"]["description"],
    }
    assert "additionalProperties" not in json.dumps(gemini)


def test_fake_text_parser_ru_and_en() -> None:
    for text in ("200 г гречки, 2 яйца и куриная грудка 150 g", "200 g buckwheat, 2 eggs, chicken breast 150 g"):
        items = fake.parse_text(text)["items"]
        assert [(item["name_ru"], item["estimated_grams"]) for item in items] == [
            ("Гречка отварная", 200.0),
            ("Яйцо куриное", 110.0),
            ("Куриная грудка запечённая", 150.0),
        ]


def test_name_overlap_stemming() -> None:
    assert ai_meal.name_overlap("Куриная грудка запечённая", "Куриная грудка приготовленная") == pytest.approx(2 / 3)
    assert ai_meal.name_overlap("Огурцы", "Огурец") == 1.0
    assert ai_meal.name_overlap("Яйца", "Яйцо куриное") == 1.0
    assert ai_meal.name_overlap("Сыр", "Сырники") == 0.0


# --------------------------------------------------------------------------- #
# text flow
# --------------------------------------------------------------------------- #
async def test_parse_text_matches_locally_and_never_saves(authed: Any) -> None:
    await _seed()
    response = await authed.post(
        "/api/v1/ai/parse-text", json={"text": "200 г гречки, 2 яйца и куриная грудка 150 g", "meal_type": "lunch"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "text" and body["provider"] == "fake"
    assert body["prompt_version"] == "parse_food_text.v1"
    names = [(item["name_ru"], item["match"]["food"]["name_ru"] if item["match"] else None) for item in body["items"]]
    assert names == [
        ("Гречка отварная", "Гречка отварная"),
        ("Яйцо куриное", "Яйцо куриное"),
        ("Куриная грудка запечённая", "Куриная грудка приготовленная"),  # same words + same kcal beats «голень»
    ]
    buckwheat = body["items"][0]
    assert buckwheat["estimated_grams"] == 200 and buckwheat["kcal"] == 220.0
    assert buckwheat["confidence_level"] == "high"
    assert any(alt["name_ru"] == "Гречка (ядрица, сухая)" for alt in buckwheat["alternatives"])
    assert body["usage"]["calls_today"] == 1

    assert await _count(MealLog) == 0  # AI never auto-saves
    job = await _job(body["job_id"])
    assert job.status == AiJobStatus.SUCCEEDED and job.prompt_version == "v1" and job.input_text
    assert job.result["matched"] == 3


async def test_own_food_wins_the_match(authed: Any) -> None:
    await _seed()
    mine = await authed.post(
        "/api/v1/foods",
        json={"name_ru": "Гречка отварная домашняя", "kcal_per_100g": 105, "protein_per_100g": 4, "carbs_per_100g": 20},
    )
    assert mine.status_code == 201
    body = (await authed.post("/api/v1/ai/parse-text", json={"text": "гречка 250 г"})).json()
    assert body["items"][0]["match"]["food"]["id"] == mine.json()["id"]


async def test_unmatched_item_keeps_ai_macros_and_saves_after_review(authed: Any) -> None:
    await _seed()
    body = (await authed.post("/api/v1/ai/parse-text", json={"text": "соус тартар 30 г, банан"})).json()
    sauce, banana = body["items"]
    assert sauce["match"] is None and sauce["confidence_level"] == "low"  # → source=ai on the review screen
    assert banana["match"]["food"]["name_ru"] == "Банан"

    saved = await authed.post(
        "/api/v1/nutrition/meals",
        json={
            "id": str(uuid.uuid4()),
            "meal_type": "snack",
            "source": "text",
            "ai_job_id": body["job_id"],
            "items": [_item_payload(sauce), _item_payload(banana, grams=100)],
        },
    )
    assert saved.status_code == 201, saved.text
    meal = saved.json()
    assert meal["ai_job_id"] == body["job_id"] and meal["source"] == "text"
    ai_item, banana_item = meal["items"]
    assert (ai_item["item_source"], ai_item["match_method"]) == ("ai", "ai")
    assert ai_item["kcal"] == 45.0 and ai_item["confidence"] == sauce["confidence"]
    assert (banana_item["item_source"], banana_item["is_edited"], banana_item["grams"]) == ("food", True, 100.0)
    async with SessionLocal() as session:
        row = (await session.execute(select(MealLog))).scalar_one()
        assert row.ai_input_text == "соус тартар 30 г, банан"
    job = await _job(body["job_id"])
    assert job.result["saved_meal_id"] == meal["id"]


async def test_text_validation(authed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (await authed.post("/api/v1/ai/parse-text", json={"text": "   "})).status_code == 422
    monkeypatch.setattr(get_settings(), "ai_max_text_chars", 10)
    too_long = await authed.post("/api/v1/ai/parse-text", json={"text": "гречка 200 г и много всего"})
    assert too_long.status_code == 422 and too_long.json()["error"]["code"] == "text_too_long"


# --------------------------------------------------------------------------- #
# photo flow & the thumbnail privacy contract
# --------------------------------------------------------------------------- #
async def _analyze_photo(authed: Any, *, thumbnail: bool) -> httpx.Response:
    files: dict[str, Any] = {"photo": ("meal.jpg", jpeg(), "image/jpeg")}
    if thumbnail:
        files["thumbnail"] = ("thumb.jpg", jpeg(400, 300, 800), "image/jpeg")
    return await authed.post("/api/v1/ai/analyze-meal", files=files, data={"meal_type": "dinner"})


async def test_photo_flow_without_keeping_anything(authed: Any) -> None:
    await _seed()
    response = await _analyze_photo(authed, thumbnail=True)  # setting is off by default
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "photo" and body["thumbnail_kept"] is False
    assert [item["name_ru"] for item in body["items"]] == ["Гречка отварная", "Куриная грудка запечённая", "Огурец"]
    assert body["notes"]
    assert fake.CALLS[0].image_jpeg is not None and fake.CALLS[0].model == "fake"
    media_root = Path(get_settings().media_dir)
    assert not media_root.exists() or not any(media_root.rglob("*.jpg"))  # the original never touches disk
    assert await _count(MealLog) == 0
    job = await _job(body["job_id"])
    assert job.request_payload["meta"]["width"] == 1280 and job.input_thumb_path is None


async def test_photo_thumbnail_kept_only_when_opted_in(authed: Any) -> None:
    await _seed()
    assert (await authed.patch("/api/v1/settings", json={"keep_photo_thumbnail": True})).status_code == 200
    body = (await _analyze_photo(authed, thumbnail=True)).json()
    assert body["thumbnail_kept"] is True
    pending = Path(get_settings().media_dir) / "pending" / f"{body['job_id']}.jpg"
    assert pending.exists()

    meal = (
        await authed.post(
            "/api/v1/nutrition/meals",
            json={
                "meal_type": "dinner",
                "source": "photo",
                "ai_job_id": body["job_id"],
                "items": [_item_payload(item) for item in body["items"]],
            },
        )
    ).json()
    assert meal["has_photo"] is True and not pending.exists()
    photo = await authed.get(f"/api/v1/nutrition/meals/{meal['id']}/photo")
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert photo.content[:3] == b"\xff\xd8\xff" and len(photo.content) < 2000  # the thumbnail, not the original

    assert (await authed.delete(f"/api/v1/nutrition/meals/{meal['id']}")).status_code == 200
    assert not any(Path(get_settings().media_dir).rglob("*.jpg"))  # deleting the meal deletes the picture


async def test_thumbnail_deleted_when_setting_turned_off_before_save(authed: Any) -> None:
    await authed.patch("/api/v1/settings", json={"keep_photo_thumbnail": True})
    body = (await _analyze_photo(authed, thumbnail=True)).json()
    await authed.patch("/api/v1/settings", json={"keep_photo_thumbnail": False})
    item = body["items"][0]
    meal = (
        await authed.post(
            "/api/v1/nutrition/meals",
            json={"source": "photo", "ai_job_id": body["job_id"], "items": [_item_payload(item)]},
        )
    ).json()
    assert meal["has_photo"] is False
    assert not any(Path(get_settings().media_dir).rglob("*.jpg"))
    assert (await authed.get(f"/api/v1/nutrition/meals/{meal['id']}/photo")).status_code == 404


async def test_photo_validation(authed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    not_jpeg = await authed.post("/api/v1/ai/analyze-meal", files={"photo": ("x.png", b"\x89PNG....", "image/png")})
    assert not_jpeg.status_code == 422 and not_jpeg.json()["error"]["code"] == "image_invalid"
    huge = await authed.post("/api/v1/ai/analyze-meal", files={"photo": ("x.jpg", jpeg(4000, 3000), "image/jpeg")})
    assert huge.status_code == 422 and huge.json()["error"]["code"] == "image_too_large"
    monkeypatch.setattr(get_settings(), "ai_max_image_bytes", 1000)
    heavy = await authed.post("/api/v1/ai/analyze-meal", files={"photo": ("x.jpg", jpeg(), "image/jpeg")})
    assert heavy.status_code == 422 and heavy.json()["error"]["code"] == "image_too_large"
    assert fake.CALLS == []  # nothing reached the model


def test_jpeg_marker_parser() -> None:
    assert media.jpeg_info(jpeg(800, 600)) == media.JpegInfo(width=800, height=600, size=len(jpeg(800, 600)))
    assert media.jpeg_info(b"GIF89a....") is None
    assert media.jpeg_info(b"\xff\xd8\xff\xda\x00\x02") is None  # scan before any frame header
    assert media.check_thumbnail(jpeg(1280, 960)) is None  # a "thumbnail" that is really a photo is not kept


# --------------------------------------------------------------------------- #
# validation, retry, budget
# --------------------------------------------------------------------------- #
async def test_invalid_reply_is_retried_once_with_the_reason(authed: Any) -> None:
    fake.SCRIPT.extend(["Sure! Here are your foods: buckwheat", _reply([_ai_item()])])
    response = await authed.post("/api/v1/ai/parse-text", json={"text": "гречка"})
    assert response.status_code == 200, response.text
    assert len(fake.CALLS) == 2 and "previous reply was rejected" in fake.CALLS[1].user_text
    job = await _job(response.json()["job_id"])
    assert job.attempt == 2 and job.status == AiJobStatus.SUCCEEDED
    async with SessionLocal() as session:
        usage = (await session.execute(select(AiUsageDaily))).scalar_one()
    assert (usage.calls, usage.failed_calls) == (2, 1)


async def test_two_invalid_replies_fail_cleanly(authed: Any) -> None:
    fake.SCRIPT.extend(['{"items": "nope"}', _reply([_ai_item(estimated_grams=-5)])])
    response = await authed.post("/api/v1/ai/parse-text", json={"text": "гречка"})
    assert response.status_code == 502 and response.json()["error"]["code"] == "ai_invalid_response"
    async with SessionLocal() as session:
        job = (await session.execute(select(AiJob))).scalar_one()
    assert job.status == AiJobStatus.FAILED and job.attempt == 2 and job.error
    assert len(job.response_payload["replies"]) == 2


async def test_transient_error_retried_permanent_not(authed: Any) -> None:
    fake.SCRIPT.extend([AiProviderError("busy", transient=True, status=503), _reply([_ai_item()])])
    assert (await authed.post("/api/v1/ai/parse-text", json={"text": "гречка"})).status_code == 200
    fake.CALLS.clear()
    fake.SCRIPT.extend([AiProviderError("bad key", transient=False, status=401), _reply([_ai_item()])])
    failed = await authed.post("/api/v1/ai/parse-text", json={"text": "гречка"})
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "ai_unavailable"
    assert len(fake.CALLS) == 1  # a bad key is not hammered


async def test_daily_budget_and_burst_limit(authed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_daily_call_limit", 1)
    assert (await authed.post("/api/v1/ai/parse-text", json={"text": "банан"})).status_code == 200
    over = await authed.post("/api/v1/ai/parse-text", json={"text": "банан"})
    assert over.status_code == 429 and over.json()["error"]["code"] == "ai_daily_limit"
    usage = (await authed.get("/api/v1/ai/usage")).json()
    assert (usage["calls_today"], usage["remaining"]) == (1, 0)

    monkeypatch.setattr(get_settings(), "ai_daily_call_limit", 0)  # 0 = unlimited
    monkeypatch.setattr(get_settings(), "ai_rate_limit_per_minute", 2)
    codes = [(await authed.post("/api/v1/ai/parse-text", json={"text": "банан"})).status_code for _ in range(3)]
    assert codes[-1] == 429


async def test_ai_disabled_without_key(authed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_provider", "openai")
    monkeypatch.setattr(get_settings(), "ai_api_key", "")
    response = await authed.post("/api/v1/ai/parse-text", json={"text": "банан"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "ai_disabled"
    config = (await authed.get("/api/v1/meta/app-config")).json()
    assert config["features"]["ai_meal_text"] is False and config["ai"]["enabled"] is False


async def test_saving_requires_the_callers_successful_job(authed: Any) -> None:
    food = (await authed.post("/api/v1/foods", json={"name_ru": "Тест", "kcal_per_100g": 100})).json()
    item = {"food_id": food["id"], "grams": 100}
    unknown = await authed.post("/api/v1/nutrition/meals", json={"ai_job_id": str(uuid.uuid4()), "items": [item]})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "ai_job_not_found"

    fake.SCRIPT.extend(["x", "y"])
    failed = (await authed.post("/api/v1/ai/parse-text", json={"text": "банан"})).json()
    async with SessionLocal() as session:
        job_id = (await session.execute(select(AiJob.id))).scalar_one()
    assert failed["error"]["code"] == "ai_invalid_response"
    unusable = await authed.post("/api/v1/nutrition/meals", json={"ai_job_id": str(job_id), "items": [item]})
    assert unusable.status_code == 422 and unusable.json()["error"]["code"] == "ai_job_not_usable"
    assert await _count(MealLog) == 0

    # another user's analysis is invisible
    async with SessionLocal() as session:
        other = User(display_name="Other")
        session.add(other)
        await session.flush()
        foreign = AiJob(user_id=other.id, kind="parse_food_text", status=AiJobStatus.SUCCEEDED)
        session.add(foreign)
        await session.commit()
        foreign_id = foreign.id
    stolen = await authed.post("/api/v1/nutrition/meals", json={"ai_job_id": str(foreign_id), "items": [item]})
    assert stolen.status_code == 404


async def test_housekeeping_prunes_unsaved_thumbnails_and_old_payloads(authed: Any) -> None:
    await authed.patch("/api/v1/settings", json={"keep_photo_thumbnail": True})
    body = (await _analyze_photo(authed, thumbnail=True)).json()
    pending = Path(get_settings().media_dir) / "pending" / f"{body['job_id']}.jpg"
    old = time.time() - 48 * 3600
    os.utime(pending, (old, old))
    async with SessionLocal() as session:
        await session.execute(
            update(AiJob).values(created_at=func.now() - func.make_interval(0, 0, 0, 40))  # 40 days ago
        )
        await session.commit()
    stats = await ai_meal.ai_housekeeping()
    assert stats == {"pending_thumbs_removed": 1, "payloads_pruned": 1}
    job = await _job(body["job_id"])
    assert job.response_payload == {} and job.input_thumb_path is None and job.status == AiJobStatus.SUCCEEDED


# --------------------------------------------------------------------------- #
# provider adapters (request shape + response parsing, mocked HTTP)
# --------------------------------------------------------------------------- #
CANNED = {"items": [_ai_item()], "notes": None}


def _request(image: bool) -> AiRequest:
    return AiRequest(
        model="m-1",
        system="SYSTEM",
        user_text="USER",
        schema=MEAL_ITEMS_SCHEMA,
        schema_name="meal_items",
        image_jpeg=jpeg(64, 48, 10) if image else None,
    )


async def _call(provider_name: str, handler, monkeypatch: pytest.MonkeyPatch, *, image: bool = True):
    monkeypatch.setattr(get_settings(), "ai_provider", provider_name)
    monkeypatch.setattr(get_settings(), "ai_api_key", "sk-test")
    monkeypatch.setattr(get_settings(), "ai_base_url", "")
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    providers.set_transport(httpx.MockTransport(capture))
    response = await providers.get_provider().complete(_request(image))
    return seen[0], json.loads(seen[0].content), response


@pytest.mark.parametrize(
    ("name", "url", "token_key"),
    [
        ("openai", "https://api.openai.com/v1/chat/completions", "max_completion_tokens"),
        ("openrouter", "https://openrouter.ai/api/v1/chat/completions", "max_tokens"),
    ],
)
async def test_openai_compatible_adapter(name: str, url: str, token_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "id": "x",
        "choices": [{"message": {"content": json.dumps(CANNED)}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 120},
    }
    request, body, response = await _call(name, lambda _r: httpx.Response(200, json=reply), monkeypatch)
    assert str(request.url) == url and request.headers["authorization"] == "Bearer sk-test"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert body[token_key] == 1500
    image_part = body["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")
    assert AiMealResult.model_validate_json(response.text).items[0].name_ru == "Гречка отварная"
    assert (response.input_tokens, response.output_tokens) == (900, 120)


async def test_anthropic_adapter_forces_the_schema_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "id": "msg",
        "content": [{"type": "tool_use", "name": "meal_items", "input": CANNED}],
        "usage": {"input_tokens": 1000, "output_tokens": 80},
    }
    request, body, response = await _call("anthropic", lambda _r: httpx.Response(200, json=reply), monkeypatch)
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-test" and request.headers["anthropic-version"] == "2023-06-01"
    assert body["tool_choice"] == {"type": "tool", "name": "meal_items"}
    assert body["tools"][0]["input_schema"] == MEAL_ITEMS_SCHEMA
    assert body["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"
    assert json.loads(response.text) == CANNED and response.input_tokens == 1000


async def test_google_adapter_uses_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(CANNED)}]}}],
        "usageMetadata": {"promptTokenCount": 700, "candidatesTokenCount": 90},
    }
    request, body, response = await _call(
        "google", lambda _r: httpx.Response(200, json=reply), monkeypatch, image=False
    )
    assert str(request.url).endswith("/v1beta/models/m-1:generateContent")
    assert request.headers["x-goog-api-key"] == "sk-test"
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["responseSchema"]["type"] == "OBJECT"
    assert body["contents"][0]["parts"] == [{"text": "USER"}]
    assert json.loads(response.text) == CANNED and response.output_tokens == 90


@pytest.mark.parametrize(("status", "transient"), [(429, True), (503, True), (401, False), (400, False)])
async def test_adapter_error_classification(status: int, transient: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AiProviderError) as caught:
        await _call("openai", lambda _r: httpx.Response(status, json={"error": "x"}), monkeypatch)
    assert caught.value.transient is transient and caught.value.status == status


async def test_end_to_end_through_a_real_adapter(authed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner + matching with the OpenAI adapter, first reply broken, second fine."""
    await _seed()
    monkeypatch.setattr(get_settings(), "ai_provider", "openai")
    monkeypatch.setattr(get_settings(), "ai_api_key", "sk-test")
    replies = iter(
        [
            {"choices": [{"message": {"content": "{not json"}}], "usage": {}},
            {"choices": [{"message": {"content": json.dumps(CANNED)}}], "usage": {"prompt_tokens": 5}},
        ]
    )
    providers.set_transport(httpx.MockTransport(lambda _r: httpx.Response(200, json=next(replies))))
    body = (await authed.post("/api/v1/ai/parse-text", json={"text": "гречка 200 г"})).json()
    assert body["provider"] == "openai" and body["model"] == get_settings().ai_model_text
    assert body["items"][0]["match"]["food"]["name_ru"] == "Гречка отварная"
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Food).where(Food.user_id.is_not(None))) == 0
        assert await session.scalar(select(func.count()).select_from(MealItem)) == 0
    assert Decimal(str(body["items"][0]["kcal"])) == Decimal("220.0")


# --------------------------------------------------------------------------- #
# hardening after the stage-3 review
# --------------------------------------------------------------------------- #
_MULTIPART_HEAD = (
    b'--b\r\nContent-Disposition: form-data; name="photo"; filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
)


async def test_oversized_body_refused_before_parsing_and_auth(client: httpx.AsyncClient) -> None:
    limit = get_settings().max_request_bytes
    headers = {"content-type": "multipart/form-data; boundary=b"}
    declared = await client.post(
        "/api/v1/ai/analyze-meal", content=_MULTIPART_HEAD + b"x" * limit, headers=headers
    )  # no Authorization: the size check comes first
    assert declared.status_code == 413 and declared.json()["error"]["code"] == "payload_too_large"

    async def chunked():  # no Content-Length: counted while it streams
        yield _MULTIPART_HEAD
        for _ in range(limit // 65_536 + 2):
            yield b"x" * 65_536

    streamed = await client.post("/api/v1/ai/analyze-meal", content=chunked(), headers=headers)
    assert streamed.status_code == 413 and streamed.json()["error"]["code"] == "payload_too_large"


async def test_no_transaction_is_held_across_the_retry(authed: Any) -> None:
    async with SessionLocal() as session:
        user_id = (await session.execute(select(User.id))).scalars().first()
        in_transaction: list[bool] = []

        class Flaky:
            name = "fake"

            async def complete(self, request: AiRequest):
                in_transaction.append(session.in_transaction())
                if len(in_transaction) == 1:
                    raise AiProviderError("timeout", transient=True)
                return await fake.FakeProvider().complete(request)

        run = await ai_meal.runner.run_meal_ai(
            session,
            user_id=user_id,
            tz=None,
            kind=AiJobKind.PARSE_FOOD_TEXT,
            prompt=load_prompt("parse_food_text"),
            user_text="What I ate: банан",
            provider=Flaky(),
        )
    assert in_transaction == [False, False] and run.job.attempt == 2


async def test_one_analysis_saves_one_meal(authed: Any) -> None:
    await _seed()
    body = (await authed.post("/api/v1/ai/parse-text", json={"text": "банан"})).json()
    meal = {
        "id": str(uuid.uuid4()),
        "meal_type": "snack",
        "source": "text",
        "ai_job_id": body["job_id"],
        "items": [_item_payload(body["items"][0])],
    }
    first = await authed.post("/api/v1/nutrition/meals", json=meal)
    assert first.status_code == 201
    replay = await authed.post("/api/v1/nutrition/meals", json=meal)  # same client id: idempotent
    assert replay.status_code in (200, 201) and replay.json()["id"] == first.json()["id"]
    again = await authed.post("/api/v1/nutrition/meals", json={**meal, "id": str(uuid.uuid4())})
    assert again.status_code == 409 and again.json()["error"]["code"] == "ai_job_already_saved"
    assert await _count(MealLog) == 1


async def test_thumbnail_problems_never_fail_the_analysis(authed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await authed.patch("/api/v1/settings", json={"keep_photo_thumbnail": True})
    oversized = await authed.post(
        "/api/v1/ai/analyze-meal",
        files={
            "photo": ("meal.jpg", jpeg(), "image/jpeg"),
            "thumbnail": ("t.jpg", jpeg(400, 300, 400_000), "image/jpeg"),
        },
    )
    assert oversized.status_code == 200 and oversized.json()["thumbnail_kept"] is False

    def full_disk(relative: str, data: bytes) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(media, "write", full_disk)
    response = await _analyze_photo(authed, thumbnail=True)
    assert response.status_code == 200 and response.json()["thumbnail_kept"] is False
    assert (await _job(response.json()["job_id"])).input_thumb_path is None


async def test_usage_shows_my_calls_against_the_shared_budget(authed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_daily_call_limit", 10)
    assert (await authed.post("/api/v1/ai/parse-text", json={"text": "банан"})).status_code == 200
    async with SessionLocal() as session:
        mine = (await session.execute(select(AiUsageDaily))).scalar_one()
        other = User(display_name="Other")
        session.add(other)
        await session.flush()
        session.add(AiUsageDaily(user_id=other.id, local_day=mine.local_day, calls=4))
        await session.commit()
    usage = (await authed.get("/api/v1/ai/usage")).json()
    assert (usage["calls_today"], usage["daily_limit"], usage["remaining"]) == (1, 10, 5)


async def test_own_food_beats_a_fuller_library_name(authed: Any) -> None:
    await _seed()
    mine = await authed.post(
        "/api/v1/foods",
        json={"name_ru": "Гречка", "kcal_per_100g": 112, "protein_per_100g": 4, "carbs_per_100g": 21},
    )
    body = (await authed.post("/api/v1/ai/parse-text", json={"text": "гречка 200 г"})).json()
    assert body["items"][0]["match"]["food"]["id"] == mine.json()["id"]  # not the library «Гречка отварная»
