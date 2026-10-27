"""Stage-8 review fixes on the export side (review 8, L6 and the notes).

L6 — an AI provider's error body never leaves the server: a rejection is stored as
its HTTP status plus the provider's machine-readable error ``type``/``code``, a
model's refusal without its text, and rows stored before that rule are cut down the
same way on the way out (JSON, CSV, backup). The key fragment an OpenAI 401 echoes
(``sk-proj-Ab12****…WxYz``) is the canary.
"""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import select

from app.ai import providers
from app.core.config import get_settings
from app.models import AiJob, AiJobKind, AiJobStatus
from tests.stage8_helpers import _fresh_seed_cache  # noqa: F401  (autouse fixture)

pytestmark = pytest.mark.anyio
KEY_FRAGMENT = "sk-proj-Ab12"
OPENAI_401 = {
    "error": {
        "message": f"Incorrect API key provided: {KEY_FRAGMENT}****************************WxYz. "
        "You can find your API key at https://platform.openai.com/account/api-keys.",
        "type": "invalid_request_error",
        "param": None,
        "code": "invalid_api_key",
    }
}
# what providers.py stored before the fix: up to 300 characters of the body
OLD_STORED = f"provider rejected the request (401): {orjson.dumps(OPENAI_401).decode()}"[:330]


async def _rejection(status: int, **response: Any) -> providers.AiProviderError:
    providers.set_transport(httpx.MockTransport(lambda _request: httpx.Response(status, **response)))
    try:
        with pytest.raises(providers.AiProviderError) as caught:
            await providers._post("https://api.example.com/v1/x", headers={}, body={}, timeout_s=5)
    finally:
        providers.set_transport(None)
    return caught.value


@pytest.mark.parametrize(
    ("status", "response", "expected"),
    [
        (401, {"json": OPENAI_401}, "provider rejected the request (401: invalid_request_error/invalid_api_key)"),
        (  # Anthropic
            401,
            {"json": {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}},
            "provider rejected the request (401: authentication_error)",
        ),
        (  # Gemini: the numeric code repeats the status
            400,
            {"json": {"error": {"code": 400, "message": "API key not valid: AIza…", "status": "INVALID_ARGUMENT"}}},
            "provider rejected the request (400: INVALID_ARGUMENT)",
        ),
        (  # OpenRouter
            402,
            {"json": {"error": {"code": 402, "message": "Insufficient credits for sk-or-v1-abc"}}},
            "provider rejected the request (402)",
        ),
        (403, {"text": f"<html>forbidden {KEY_FRAGMENT}</html>"}, "provider rejected the request (403)"),
        (  # a "code" shaped like a key never passes (no "-")
            401,
            {"json": {"error": {"type": "invalid_request_error", "code": f"{KEY_FRAGMENT}xx"}}},
            "provider rejected the request (401: invalid_request_error)",
        ),
    ],
)
async def test_a_rejection_keeps_the_status_and_the_error_code_never_the_body(
    status: int, response: dict[str, Any], expected: str
) -> None:
    error = await _rejection(status, **response)
    assert str(error) == expected
    assert (error.status, error.transient) == (status, False)
    assert KEY_FRAGMENT not in str(error) and "AIza" not in str(error) and "credits" not in str(error)


async def test_a_refusal_is_stored_without_the_models_text(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_api_key", "sk-test")
    reply = {"choices": [{"message": {"content": None, "refusal": "I can't describe this person's photo"}}]}
    providers.set_transport(httpx.MockTransport(lambda _request: httpx.Response(200, json=reply)))
    try:
        provider = providers.OpenAICompatibleProvider(settings, name="openai", default_base="https://api.openai.com/v1")
        request = providers.AiRequest(model="m", system="s", user_text="u", schema={}, schema_name="x")
        with pytest.raises(providers.AiProviderError) as caught:
            await provider.complete(request)
    finally:
        providers.set_transport(None)
    assert str(caught.value) == "model refused the request" and caught.value.transient is False


@pytest.mark.parametrize(
    ("stored", "public"),
    [
        (None, None),
        (OLD_STORED, "provider rejected the request (401: invalid_request_error/invalid_api_key)"),
        ("provider rejected the request (500): <html>oops</html>", "provider rejected the request (500)"),
        (
            "provider rejected the request (401: invalid_request_error/invalid_api_key)",  # already public: kept
            "provider rejected the request (401: invalid_request_error/invalid_api_key)",
        ),
        ("provider rejected the request", "provider rejected the request"),
        ("model refused: I cannot help with this photo of a person", "model refused the request"),
        ("prompt blocked: SAFETY", "prompt blocked: SAFETY"),
        ("prompt blocked: please do not show " + KEY_FRAGMENT, "prompt blocked"),
        ("provider timed out", "provider timed out"),
        ("provider busy (503)", "provider busy (503)"),
        ("provider unreachable: ConnectError", "provider unreachable: ConnectError"),
        ("items.0.grams: Input should be greater than 0", "items.0.grams: Input should be greater than 0"),
    ],
)
def test_public_error(stored: str | None, public: str | None) -> None:
    assert providers.public_error(stored) == public
    assert providers.public_error(public) == public  # idempotent


async def test_a_failed_ai_call_stores_no_provider_body(authed: httpx.AsyncClient, db, monkeypatch) -> None:
    """Through the runner and the API: the stored job error is the public one."""
    monkeypatch.setattr(get_settings(), "ai_provider", "openai")
    monkeypatch.setattr(get_settings(), "ai_api_key", "sk-test")
    providers.set_transport(httpx.MockTransport(lambda _request: httpx.Response(401, json=OPENAI_401)))
    try:
        response = await authed.post("/api/v1/ai/parse-text", json={"text": "гречка 200 г"})
    finally:
        providers.set_transport(None)
    assert response.status_code >= 500 and KEY_FRAGMENT not in response.text
    errors = (await db.scalars(select(AiJob.error))).all()
    assert errors == ["provider rejected the request (401: invalid_request_error/invalid_api_key)"]


async def test_rows_stored_before_leave_without_the_body_in_every_export(authed: httpx.AsyncClient, owner, db) -> None:
    db.add_all(
        [
            AiJob(user_id=owner.id, kind=AiJobKind.PARSE_FOOD_TEXT, status=AiJobStatus.FAILED, error=OLD_STORED),
            AiJob(
                user_id=owner.id,
                kind=AiJobKind.ANALYZE_MEAL_PHOTO,
                status=AiJobStatus.FAILED,
                error="model refused: a refusal that quotes the photo",
            ),
            AiJob(
                user_id=owner.id, kind=AiJobKind.PARSE_FOOD_TEXT, status=AiJobStatus.FAILED, error="provider busy (503)"
            ),
        ]
    )
    await db.commit()
    expected = {
        "provider rejected the request (401: invalid_request_error/invalid_api_key)",
        "model refused the request",
        "provider busy (503)",
    }

    exported = await authed.get("/api/v1/export/json")
    assert KEY_FRAGMENT not in exported.text and "quotes the photo" not in exported.text
    assert {row["error"] for row in orjson.loads(exported.content)["tables"]["ai_jobs"]} == expected

    archive = zipfile.ZipFile(io.BytesIO((await authed.get("/api/v1/export/csv")).content))
    text = archive.read("ai_jobs.csv").decode("utf-8-sig")
    assert KEY_FRAGMENT not in text and {row["error"] for row in csv.DictReader(io.StringIO(text))} == expected

    backup = zipfile.ZipFile(io.BytesIO((await authed.get("/api/v1/export/backup")).content))
    data = backup.read("data.json")
    assert KEY_FRAGMENT.encode() not in data
    assert {row["error"] for row in orjson.loads(data)["tables"]["ai_jobs"]} == expected

    # the stored rows are left as they are (no data rewrite): only what leaves is cut down
    assert OLD_STORED in (await db.scalars(select(AiJob.error))).all()
