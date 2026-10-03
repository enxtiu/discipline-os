"""Structured logging (structlog) with request correlation.

Development: human-readable colourised console output.
Production (``LOG_JSON=true``): one JSON object per line, ready for journald /
Loki / CloudWatch.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import get_settings

__all__ = ["bind_request_context", "configure_logging", "get_logger", "request_id_var"]

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def _add_context(_logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]:
    rid = request_id_var.get()
    if rid:
        event_dict.setdefault("request_id", rid)
    uid = user_id_var.get()
    if uid:
        event_dict.setdefault("user_id", uid)
    return event_dict


def configure_logging(level: str | None = None, json_logs: bool | None = None) -> None:
    settings = get_settings()
    log_level = (level or settings.log_level).upper()
    use_json = settings.log_json if json_logs is None else json_logs

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Any
    if use_json:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level, logging.INFO)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)
    for noisy in ("uvicorn.access", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel("WARNING")
    # uvicorn error logs keep going through stdlib; make them structlog-aware
    logging.getLogger("uvicorn.error").setLevel(log_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(request_id: str | None = None, user_id: str | None = None) -> str:
    rid = request_id or uuid.uuid4().hex[:16]
    request_id_var.set(rid)
    if user_id:
        user_id_var.set(user_id)
    return rid
