"""FastAPI application factory.

Wiring order matters: logging -> error handlers -> middleware -> routers ->
lifespan (DB ping + scheduler).  The app is mounted at ``/api/v1``; Caddy
reverse-proxies ``/api/*`` to this process and serves the SPA for everything
else, so the browser only ever talks to one origin (no CORS in production).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import bind_request_context, configure_logging, get_logger
from app.db import dispose_engine, ping
from app.services.meta_i18n import as_dict as i18n_dict
from app.services.scheduler import start_scheduler, stop_scheduler

settings = get_settings()
configure_logging()
log = get_logger("app.main")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Correlate logs, time the request, and keep the event loop honest."""

    async def dispatch(self, request: Request, call_next):
        # A tiny bit of header hygiene: Caddy already strips Server.
        request_id = request.headers.get("x-request-id")
        rid = bind_request_context(request_id)
        request.state.request_id = rid
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time-ms"] = str(elapsed_ms)
        if not request.url.path.endswith("/health"):
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=elapsed_ms,
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "startup",
        env=settings.env,
        version=settings.api_version,
        scheduler_enabled=settings.scheduler_enabled,
    )
    db_ok = await ping()
    if not db_ok:
        log.error("database_unreachable_on_startup", hint="check DATABASE_URL / run alembic upgrade head")
    else:
        log.info("database_ready")
    await start_scheduler()
    try:
        yield
    finally:
        await stop_scheduler()
        await dispose_engine()
        log.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=(
            "Personal discipline tracker: nutrition, training, steps, PARA tasks, "
            "time tracking and gamification. Device-token auth, UTC storage, "
            "user-timezone day boundaries."
        ),
        openapi_tags=[
            {"name": "meta", "description": "Health, version, client bootstrap config"},
            {"name": "auth", "description": "Pairing, session, token refresh"},
            {"name": "devices", "description": "Paired devices and pairing codes"},
            {"name": "tokens", "description": "Scoped API tokens for automations"},
            {"name": "settings", "description": "Profile, goal, targets, onboarding"},
        ],
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        contact={"name": "DisciplineOS"},
        license_info={"name": "Private"},
    )

    register_exception_handlers(app)
    # innermost (added first): refuses an oversized body before FastAPI parses
    # it (and before auth); its 413 is logged and CORS-wrapped like any response
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "X-Response-Time-ms"],
        )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.api_title,
            "version": settings.api_version,
            "docs": "/docs",
            "api": settings.api_v1_prefix,
        }

    @app.get("/api/v1/i18n/export", include_in_schema=False)
    async def i18n_export() -> dict[str, dict[str, str]]:
        """Full label catalogue — the frontend build can snapshot this."""
        return i18n_dict()

    return app


app = create_app()
