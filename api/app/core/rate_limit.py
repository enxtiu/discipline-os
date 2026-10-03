"""Tiny in-process rate limiter.

Good enough for this single-instance deployment: a fixed window counter kept in
memory (the API runs from one container; a restart clears it, which is fine for
brute-force protection of pairing codes because the codes themselves are short
lived and the number of guesses is also bounded by ``attempts`` in the DB).

If the API is ever scaled horizontally, swap the store for Redis — the
interface (``hit`` / ``check``) stays the same.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import RateLimited

__all__ = ["check_rate_limit", "hit", "rate_limit_dependency", "reset"]


class _Bucket:
    __slots__ = ("count", "window_start")

    def __init__(self, window_start: float) -> None:
        self.count = 0
        self.window_start = window_start


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(time.time()))
        self._lock = asyncio.Lock()

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        """Register one request. Returns ``(allowed, retry_after_seconds)``."""
        if limit <= 0:
            return True, 0
        now = time.time()
        async with self._lock:
            bucket = self._buckets[key]
            if now - bucket.window_start >= window_seconds:
                bucket.count = 0
                bucket.window_start = now
            bucket.count += 1
            if bucket.count > limit:
                retry_after = int(window_seconds - (now - bucket.window_start)) + 1
                return False, max(retry_after, 1)
        self._maybe_gc(now)
        return True, 0

    def _maybe_gc(self, now: float) -> None:
        if len(self._buckets) < 5000:
            return
        for key in [k for k, b in self._buckets.items() if now - b.window_start > 3600]:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        self._buckets.clear()


limiter = InMemoryRateLimiter()


def client_ip(request: Request) -> str:
    """Real client IP behind Caddy (``X-Forwarded-For`` first hop)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def hit(request: Request, scope: str, limit: int | None = None, window_seconds: int = 60) -> None:
    settings = get_settings()
    effective = settings.auth_rate_limit_per_minute if limit is None else limit
    allowed, retry_after = await limiter.hit(f"{scope}:{client_ip(request)}", effective, window_seconds)
    if not allowed:
        raise RateLimited(retry_after, "Too many attempts, please wait a moment")


async def check_rate_limit(request: Request) -> None:
    """Dependency: applies the global per-IP limit to auth-ish endpoints."""
    await hit(request, "auth")


def rate_limit_dependency(scope: str, limit: int | None = None, window_seconds: int = 60):
    async def _dep(request: Request) -> None:
        await hit(request, scope, limit, window_seconds)

    return _dep


def reset() -> None:
    limiter.reset()
