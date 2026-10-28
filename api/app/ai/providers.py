"""Provider adapters: one request shape in, raw JSON text out.

``AI_PROVIDER`` picks the adapter; ``AI_MODEL_VISION`` (photos) and
``AI_MODEL_TEXT`` (typed/dictated text, the cheaper model) pick the models.

* ``openai`` / ``openrouter`` — Chat Completions with
  ``response_format: json_schema`` (strict). OpenRouter speaks the same API.
* ``anthropic`` — Messages API; the schema is enforced as a forced tool call.
* ``google`` — Gemini ``generateContent`` with ``responseSchema``.
* ``fake`` — deterministic, offline (``app/ai/fake.py``): tests and demos.

Adapters never validate meaning; ``app/ai/runner.py`` does that and owns the
retry. They only classify failures: transient (timeouts, 429, 5xx → worth
one retry) or permanent (bad key, bad request → not).

Error messages (stored in ``ai_jobs.error``) never carry a provider's response
body: a rejection keeps the HTTP status and the provider's machine-readable error
``type``/``code``/``status`` only — the body's message can echo request content or
a masked fragment of the server's API key — and a model's refusal text stays out
too. :func:`public_error` applies the same rule to rows stored before.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import Settings, get_settings

REJECTED = "provider rejected the request"
REFUSED = "model refused the request"
BLOCKED = "prompt blocked"
# a provider's error identifier ("invalid_api_key", "authentication_error", "INVALID_ARGUMENT"):
# letters, digits, "_" and "." — no "-", so nothing shaped like a key ("sk-proj-…") passes
_ERROR_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,47}")
_STORED_REJECTION = re.compile(r"provider rejected the request \((\d{3})(?:: ([A-Za-z0-9_./]*))?\)")
_BODY_CODE = re.compile(r'"(?:type|code|status)"\s*:\s*(?:"([A-Za-z][A-Za-z0-9_.]{0,47})"|(\d{1,6})\b)')


def _code_token(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if 0 <= value < 1_000_000 else None
    if isinstance(value, str) and _ERROR_CODE.fullmatch(value):
        return value
    return None


def error_codes(body: Any, status: int | None = None) -> list[str]:
    """``type``/``code``/``status`` of a provider's error body — OpenAI and OpenRouter
    (``{"error": {"type", "code"}}``), Anthropic (``{"error": {"type"}}``), Gemini
    (``{"error": {"code", "status"}}``); never the ``message``."""
    error = body.get("error") if isinstance(body, dict) else None
    candidates = [error.get("type"), error.get("code"), error.get("status")] if isinstance(error, dict) else [error]
    out: list[str] = []
    for value in candidates:
        token = _code_token(value)
        if token is not None and token not in out and token != str(status):
            out.append(token)
    return out


def rejected_message(status: int, codes: Iterable[str] = ()) -> str:
    """``provider rejected the request (401: invalid_request_error/invalid_api_key)``."""
    codes = list(codes)
    return f"{REJECTED} ({status}{': ' + '/'.join(codes) if codes else ''})"


def public_error(text: str | None) -> str | None:
    """A stored ``ai_jobs.error`` as it may leave the server (exports): a rejection
    stored before this rule (``… (401): <the provider's body>``) is cut down to its
    status and error codes, a refusal to ``model refused the request``; every other
    message is ours (timeouts, busy, unreadable replies, schema problems) and stays."""
    if text is None:
        return None
    if text.startswith(REJECTED):
        if _STORED_REJECTION.fullmatch(text):
            return text
        status = re.match(r"provider rejected the request \((\d{3})\)", text)
        if status is None:
            return REJECTED
        codes: list[str] = []
        for match in _BODY_CODE.finditer(text[status.end() :]):
            token = match.group(1) or match.group(2)
            if token not in codes and token != status.group(1):
                codes.append(token)
        return rejected_message(int(status.group(1)), codes)
    if text.startswith("model refused"):
        return REFUSED
    if text.startswith(BLOCKED):
        reason = text.removeprefix(BLOCKED).lstrip(": ")
        return f"{BLOCKED}: {reason}" if _ERROR_CODE.fullmatch(reason) else BLOCKED
    return text


@dataclass(slots=True)
class AiRequest:
    model: str
    system: str
    user_text: str
    schema: dict[str, Any]
    schema_name: str
    image_jpeg: bytes | None = None
    max_output_tokens: int = 1500


@dataclass(slots=True)
class AiResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class AiProviderError(Exception):
    def __init__(self, message: str, *, transient: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status = status


class AiProvider(Protocol):
    name: str

    async def complete(self, request: AiRequest) -> AiResponse: ...


# Tests replace this with an httpx.MockTransport; production uses the network.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def _post(
    url: str, *, headers: dict[str, str], body: dict[str, Any], timeout_s: float, params: dict | None = None
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(transport=_transport, timeout=timeout_s) as client:
            response = await client.post(url, headers=headers, json=body, params=params)
    except httpx.TimeoutException as error:
        raise AiProviderError("provider timed out", transient=True) from error
    except httpx.HTTPError as error:
        raise AiProviderError(f"provider unreachable: {type(error).__name__}", transient=True) from error
    if response.status_code == 429 or response.status_code >= 500:
        raise AiProviderError(f"provider busy ({response.status_code})", transient=True, status=response.status_code)
    if response.status_code >= 400:
        # 400/401/403/404: configuration or request problem — retrying will not help.
        # The status and the provider's error code only: the body never leaves this function.
        try:
            body = response.json()
        except ValueError:
            body = None
        raise AiProviderError(
            rejected_message(response.status_code, error_codes(body, response.status_code)),
            transient=False,
            status=response.status_code,
        )
    try:
        return response.json()
    except ValueError as error:
        raise AiProviderError("provider returned non-JSON", transient=True) from error


# --------------------------------------------------------------------------- #
# OpenAI-compatible (OpenAI, OpenRouter)
# --------------------------------------------------------------------------- #
class OpenAICompatibleProvider:
    def __init__(self, settings: Settings, *, name: str, default_base: str) -> None:
        self.name = name
        self._settings = settings
        self._base = (settings.ai_base_url or default_base).rstrip("/")

    async def complete(self, request: AiRequest) -> AiResponse:
        content: list[dict[str, Any]] = [{"type": "text", "text": request.user_text}]
        if request.image_jpeg is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{_b64(request.image_jpeg)}", "detail": "high"},
                }
            )
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
            },
        }
        # OpenAI's newer models only accept max_completion_tokens; OpenRouter normalises max_tokens
        body["max_completion_tokens" if self.name == "openai" else "max_tokens"] = request.max_output_tokens
        headers = {"Authorization": f"Bearer {self._settings.ai_api_key}"}
        if self.name == "openrouter":
            headers["X-Title"] = "DisciplineOS"
        payload = await _post(
            f"{self._base}/chat/completions", headers=headers, body=body, timeout_s=self._settings.ai_timeout_seconds
        )
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise AiProviderError("unexpected response shape", transient=True) from error
        if message.get("refusal"):
            raise AiProviderError(REFUSED, transient=False)  # the refusal text is model output: not stored
        usage = payload.get("usage") or {}
        return AiResponse(
            text=message.get("content") or "",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={"id": payload.get("id"), "model": payload.get("model"), "usage": usage},
        )


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = (settings.ai_base_url or "https://api.anthropic.com").rstrip("/")

    async def complete(self, request: AiRequest) -> AiResponse:
        content: list[dict[str, Any]] = []
        if request.image_jpeg is not None:
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(request.image_jpeg)},
                }
            )
        content.append({"type": "text", "text": request.user_text})
        body = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": content}],
            # the schema is enforced by forcing a single tool call with it as input_schema
            "tools": [
                {
                    "name": request.schema_name,
                    "description": "Record the recognised foods.",
                    "input_schema": request.schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": request.schema_name},
        }
        headers = {"x-api-key": self._settings.ai_api_key, "anthropic-version": "2023-06-01"}
        payload = await _post(
            f"{self._base}/v1/messages", headers=headers, body=body, timeout_s=self._settings.ai_timeout_seconds
        )
        tool_input = next(
            (block.get("input") for block in payload.get("content") or [] if block.get("type") == "tool_use"),
            None,
        )
        if tool_input is None:
            raise AiProviderError("no tool_use block in the response", transient=True)
        usage = payload.get("usage") or {}
        return AiResponse(
            text=json.dumps(tool_input, ensure_ascii=False),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw={"id": payload.get("id"), "model": payload.get("model"), "usage": usage},
        )


# --------------------------------------------------------------------------- #
# Google Gemini
# --------------------------------------------------------------------------- #
def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON schema → the OpenAPI subset Gemini accepts (no additionalProperties,
    upper-case types, nullable flag instead of a type union)."""
    node = copy.deepcopy(schema)
    out: dict[str, Any] = {}
    kind = node.get("type")
    if isinstance(kind, list):
        non_null = [k for k in kind if k != "null"]
        out["nullable"] = "null" in kind
        kind = non_null[0] if non_null else "string"
    if kind:
        out["type"] = str(kind).upper()
    for key in ("description", "enum", "maxItems", "minItems", "required"):
        if key in node:
            out[key] = node[key]
    if "properties" in node:
        out["properties"] = {name: to_gemini_schema(sub) for name, sub in node["properties"].items()}
        out["propertyOrdering"] = list(node["properties"])
    if "items" in node:
        out["items"] = to_gemini_schema(node["items"])
    return out


class GoogleProvider:
    name = "google"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = (settings.ai_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    async def complete(self, request: AiRequest) -> AiResponse:
        parts: list[dict[str, Any]] = []
        if request.image_jpeg is not None:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": _b64(request.image_jpeg)}})
        parts.append({"text": request.user_text})
        body = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(request.schema),
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        headers = {"x-goog-api-key": self._settings.ai_api_key}
        payload = await _post(
            f"{self._base}/models/{request.model}:generateContent",
            headers=headers,
            body=body,
            timeout_s=self._settings.ai_timeout_seconds,
        )
        try:
            candidate = payload["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (KeyError, IndexError, TypeError) as error:
            blocked = (payload.get("promptFeedback") or {}).get("blockReason")
            if blocked:
                raise AiProviderError(public_error(f"{BLOCKED}: {blocked}") or BLOCKED, transient=False) from error
            raise AiProviderError("unexpected response shape", transient=True) from error
        usage = payload.get("usageMetadata") or {}
        return AiResponse(
            text=text,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            raw={"model": payload.get("modelVersion"), "usage": usage},
        )


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def get_provider(settings: Settings | None = None) -> AiProvider:
    settings = settings or get_settings()
    kind = settings.ai_provider
    if kind == "fake":
        from app.ai.fake import FakeProvider

        return FakeProvider()
    if kind == "openai":
        return OpenAICompatibleProvider(settings, name="openai", default_base="https://api.openai.com/v1")
    if kind == "openrouter":
        return OpenAICompatibleProvider(settings, name="openrouter", default_base="https://openrouter.ai/api/v1")
    if kind == "anthropic":
        return AnthropicProvider(settings)
    if kind == "google":
        return GoogleProvider(settings)
    raise ValueError(f"unknown AI_PROVIDER: {kind}")
