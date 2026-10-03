"""Typed application errors and their JSON envelope.

Every error leaving the API looks like::

    {"error": {"code": "not_found", "message": "...", "details": {...}}}

The frontend maps ``code`` to an i18n key, so messages are developer-facing
English and the UI never has to parse prose.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.__class__.__doc__ or self.code
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(AppError):
    """Resource does not exist (or belongs to another user)."""

    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    """Missing, malformed, expired or revoked credentials."""

    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    """Authenticated but not allowed (e.g. token lacks the scope)."""

    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    """Write conflicts with existing state (idempotency / uniqueness)."""

    status_code = 409
    code = "conflict"


class Unprocessable(AppError):
    """Syntactically fine, semantically rejected (business rule)."""

    status_code = 422
    code = "unprocessable"


class RateLimited(AppError):
    """Too many requests; ``retry_after`` seconds in details."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after: int, message: str | None = None) -> None:
        super().__init__(message or "Too many requests", details={"retry_after": retry_after})


class ServiceUnavailable(AppError):
    """Dependency down or feature disabled (e.g. AI not configured)."""

    status_code = 503
    code = "service_unavailable"


def _safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Pydantic errors can carry non-JSON-serialisable objects in ``ctx``."""
    out: list[dict[str, Any]] = []
    for error in exc.errors():
        item: dict[str, Any] = {
            "type": error.get("type", "value_error"),
            "loc": [str(part) for part in error.get("loc", ())],
            "msg": str(error.get("msg", "invalid value")),
        }
        context = error.get("ctx")
        if isinstance(context, dict):
            item["ctx"] = {str(key): str(value) for key, value in context.items()}
        out.append(item)
    return out


def _envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details:
        payload["details"] = details
    return {"error": payload}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("app_error", code=exc.code, message=exc.message, details=exc.details)
        else:
            log.info("app_error", code=exc.code, message=exc.message)
        headers = {}
        if exc.status_code == 429:
            headers["Retry-After"] = str(exc.details.get("retry_after", 60))
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope("validation_error", "Request payload is invalid", _safe_errors(exc)),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail)),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "Internal server error"),
        )
