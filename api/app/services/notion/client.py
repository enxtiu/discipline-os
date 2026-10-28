"""Notion API client (stage 9).

Facts this module relies on, checked against the official reference (Oct 2026):

* **Version** — every request carries ``Notion-Version``; the latest is ``2026-03-11``
  (https://developers.notion.com/reference/versioning). That version renamed
  ``archived`` → ``in_trash`` in request and response bodies
  (https://developers.notion.com/guides/get-started/upgrade-guide-2026-03-11). Since
  ``2025-09-03`` a database is a container of *data sources*: pages are created under
  ``{"type": "data_source_id", ...}`` and relations point at a ``data_source_id``
  (https://developers.notion.com/reference/changes-by-version).
* **Auth** — ``Authorization: Bearer <secret>`` of an internal connection
  (https://developers.notion.com/reference/authentication).
* **Limits** — 180 requests per minute per connection (600 on Business/Enterprise),
  enforced over a fixed 60-second window, plus a per-workspace limit; 429
  ``rate_limited`` with ``Retry-After`` in whole seconds (at most 60 for the
  per-connection limit); 529 ``service_overload`` is retried like a 429
  (https://developers.notion.com/reference/request-limits).
* **Retries** — 429/529 for every method; 500/502/503/504 only for idempotent requests,
  because a write that returns 503 may have been saved — for a create,
  ``additional_data.committed_resource_id`` then names the new object
  (https://developers.notion.com/reference/request-limits,
  https://developers.notion.com/reference/status-codes).
* **Errors** — ``{"object": "error", "status", "code", "message"}`` with the codes
  ``unauthorized`` (401), ``restricted_resource`` (403), ``object_not_found`` (404: missing
  *or not shared with the connection*), ``validation_error`` (400), ``conflict_error``
  (409: retry with fresh parameters), ``rate_limited`` (429)
  (https://developers.notion.com/reference/status-codes).

Safety: the base URL is fixed (``https://api.notion.com/v1``) and every path is built
from ids normalised to UUIDs here — nothing a user types becomes a URL (no SSRF); no
redirect is followed. The secret lives only in the ``Authorization`` header of the
underlying ``httpx`` client: it is never logged, never part of an exception message
and not shown by ``repr``.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

__all__ = [
    "BASE_URL",
    "NOTION_VERSION",
    "BudgetExhausted",
    "NotionClient",
    "NotionConflict",
    "NotionError",
    "NotionForbidden",
    "NotionNotFound",
    "NotionRateLimited",
    "NotionUnauthorized",
    "NotionUnavailable",
    "NotionUncertain",
    "NotionValidation",
    "RequestBudget",
    "norm_id",
]

log = get_logger(__name__)

# https://developers.notion.com/reference/versioning — pinned in this one place
NOTION_VERSION = "2026-03-11"
BASE_URL = "https://api.notion.com/v1"
USER_AGENT = "DisciplineOS/0.1 (self-hosted; one-way Notion mirror)"

READ_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Notion answers 503 (with ``committed_resource_id`` for creates) when it cannot build a
# response within 60 s: wait longer than that for writes, so their outcome is known
WRITE_TIMEOUT = httpx.Timeout(75.0, connect=10.0)
MAX_ATTEMPTS = 4
MAX_RETRY_AFTER = 60.0  # longer waits (the workspace limit) end the run; the next one resumes
BACKOFF_SECONDS = (0.5, 1.0, 2.0, 4.0)

# tests install an ``httpx.MockTransport`` (a fake Notion) and a fake clock here;
# nothing in the test suite ever reaches the real API
_transport_override: httpx.AsyncBaseTransport | None = None


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> Awaitable[None]: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


_clock_override: Clock | None = None


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class NotionError(Exception):
    """A request Notion refused, or that could not be completed. ``code`` is the API
    envelope code the app shows (``notion_*``); ``notion_code`` is Notion's own code.
    Messages are fixed strings: Notion's message text is never copied into them."""

    code = "notion_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        status: int | None = None,
        notion_code: str | None = None,
        retry_after: float | None = None,
        committed_id: str | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.status = status
        self.notion_code = notion_code
        self.retry_after = retry_after
        self.committed_id = committed_id


class NotionUnauthorized(NotionError):
    """401: the secret is wrong, revoked or expired."""

    code = "notion_unauthorized"


class NotionForbidden(NotionError):
    """403 restricted_resource: the connection lacks a capability (read/update/insert)."""

    code = "notion_forbidden"


class NotionNotFound(NotionError):
    """404 object_not_found: missing, trashed for good, or not shared with the connection."""

    code = "notion_not_found"


class NotionValidation(NotionError):
    """400: Notion rejected the request body (validation_error, invalid_request…)."""

    code = "notion_rejected"


class NotionConflict(NotionError):
    """409 conflict_error that persisted through the retries."""

    code = "notion_conflict"


class NotionRateLimited(NotionError):
    """429/529 whose wait is longer than a run may sleep, or that kept coming."""

    code = "notion_rate_limited"


class NotionUnavailable(NotionError):
    """5xx, timeouts, network trouble — after the retries an idempotent request gets."""

    code = "notion_unavailable"


class NotionUncertain(NotionUnavailable):
    """A create (not idempotent) whose outcome is unknown: it may have been saved.
    ``committed_id`` is the new object's id when Notion said so (503 with
    ``additional_data.committed_resource_id``)."""


class BudgetExhausted(NotionError):
    """The run spent its request budget; the next run continues where this one stopped."""

    code = "notion_budget"


def _error_for(status: int, notion_code: str | None) -> NotionError:
    kind: type[NotionError]
    if status == 401:
        kind = NotionUnauthorized
    elif status == 403:
        kind = NotionForbidden
    elif status == 404:
        kind = NotionNotFound
    elif status == 409:
        kind = NotionConflict
    elif status in (429, 529):
        kind = NotionRateLimited
    elif 400 <= status < 500:
        kind = NotionValidation
    else:
        kind = NotionUnavailable
    return kind(f"Notion answered {status} {notion_code or ''}".strip(), status=status, notion_code=notion_code)


# --------------------------------------------------------------------------- #
# Budget
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RequestBudget:
    """Requests one run may send (every attempt counts, retries included)."""

    limit: int | None
    spent: int = 0

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.spent >= self.limit

    def take(self) -> None:
        if self.exhausted:
            raise BudgetExhausted("request budget of this run spent", status=None)
        self.spent += 1


def norm_id(value: str) -> str:
    """A Notion id (32 hex digits, dashed or not) → the dashed lowercase form.
    Raises ``ValueError`` for anything else, so no other text ever reaches a path."""
    return str(uuid.UUID(str(value).strip()))


def _retry_after(response: httpx.Response, body: dict[str, Any]) -> float | None:
    """``Retry-After`` (integer seconds), else ``additional_data.retry_after`` (a string)."""
    for raw in (response.headers.get("retry-after"), (body.get("additional_data") or {}).get("retry_after")):
        if raw is None:
            continue
        try:
            value = float(str(raw).strip())
        except ValueError:
            continue
        if value >= 0:
            return value
    return None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class NotionClient:
    """One connection's client for one run: paced (≤ ``max_rps``), budgeted, retrying."""

    def __init__(
        self,
        secret: str,
        *,
        budget: RequestBudget | None = None,
        max_rps: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        settings = get_settings()
        self.budget = budget or RequestBudget(None)
        self._interval = 1.0 / float(max_rps or settings.notion_max_rps)
        self._clock = clock or _clock_override or RealClock()
        self._next_at = 0.0
        self.requests = 0
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            transport=transport or _transport_override,
            headers={
                "Authorization": f"Bearer {secret}",
                "Notion-Version": NOTION_VERSION,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=READ_TIMEOUT,
            follow_redirects=False,
        )

    def __repr__(self) -> str:  # never the secret
        return f"<NotionClient version={NOTION_VERSION} requests={self.requests}>"

    async def __aenter__(self) -> NotionClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ core
    async def _pace(self) -> None:
        now = self._clock.monotonic()
        if self._next_at > now:
            await self._clock.sleep(self._next_at - now)
            now = self._clock.monotonic()
        self._next_at = max(now, self._next_at) + self._interval

    async def _backoff(self, attempt: int) -> None:
        base = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
        await self._clock.sleep(base * (1.0 + 0.25 * random.random()))  # noqa: S311 - jitter, not security

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, Any] | None:
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, idempotent: bool
    ) -> dict[str, Any]:
        """Send one request (with its retries). ``idempotent`` decides whether a 5xx or a
        timeout may be repeated; a create that cannot be repeated raises
        :class:`NotionUncertain` instead."""
        timeout = READ_TIMEOUT if method == "GET" else WRITE_TIMEOUT
        attempt = 0
        while True:
            attempt += 1
            self.budget.take()
            await self._pace()
            self.requests += 1
            try:
                response = await self._http.request(method, path, json=json, timeout=timeout)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                # the request never reached Notion: safe to repeat for any method
                if attempt < MAX_ATTEMPTS:
                    await self._backoff(attempt)
                    continue
                raise NotionUnavailable("Notion cannot be reached") from None
            except httpx.HTTPError:
                # sent, but no (complete) answer: only idempotent requests are repeated
                if idempotent and attempt < MAX_ATTEMPTS:
                    await self._backoff(attempt)
                    continue
                if idempotent:
                    raise NotionUnavailable("Notion did not answer") from None
                raise NotionUncertain("Notion did not answer a create") from None

            status = response.status_code
            body = self._body(response)
            if 200 <= status < 300:
                if body is None:
                    if idempotent:
                        raise NotionUnavailable("Notion sent an unreadable answer", status=status)
                    raise NotionUncertain("Notion sent an unreadable answer to a create", status=status)
                return body

            body = body or {}
            notion_code = body.get("code") if isinstance(body.get("code"), str) else None
            log.info("notion_http_error", method=method, status=status, code=notion_code, attempt=attempt)

            if status in (429, 529) and notion_code != "public_api_request_blocked":
                wait = _retry_after(response, body)
                if wait is None:
                    wait = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
                if wait > MAX_RETRY_AFTER or attempt >= MAX_ATTEMPTS:
                    raise NotionRateLimited(
                        "Notion asks to slow down", status=status, notion_code=notion_code, retry_after=wait
                    )
                await self._clock.sleep(wait)  # nothing is sent meanwhile (one request at a time)
                continue
            if status == 409 and notion_code == "conflict_error" and attempt < MAX_ATTEMPTS:
                await self._backoff(attempt)  # a failed transaction: nothing was saved
                continue
            if status >= 500:
                if idempotent and attempt < MAX_ATTEMPTS:
                    await self._backoff(attempt)
                    continue
                if not idempotent:
                    extra = body.get("additional_data") if isinstance(body.get("additional_data"), dict) else {}
                    committed = extra.get("committed_resource_id")
                    raise NotionUncertain(
                        "Notion failed a create; it may have been saved",
                        status=status,
                        notion_code=notion_code,
                        committed_id=str(committed) if committed else None,
                    )
            raise _error_for(status, notion_code)

    # ------------------------------------------------------------- endpoints
    async def me(self) -> dict[str, Any]:
        """The connection's bot user (https://developers.notion.com/reference/get-self)."""
        return await self.request("GET", "/users/me", idempotent=True)

    async def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/pages/{norm_id(page_id)}", idempotent=True)

    async def create_database(
        self,
        *,
        page_id: str,
        title: str,
        icon: str | None,
        properties: dict[str, Any],
        description: str | None = None,
    ) -> dict[str, Any]:
        """A database under a page with its initial data source schema
        (https://developers.notion.com/reference/database-create). Not idempotent."""
        body: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": norm_id(page_id)},
            "title": [{"type": "text", "text": {"content": title}}],
            "initial_data_source": {"properties": properties},
        }
        if description:
            body["description"] = [{"type": "text", "text": {"content": description}}]
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        return await self.request("POST", "/databases", json=body, idempotent=False)

    async def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/databases/{norm_id(database_id)}", idempotent=True)

    async def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/data_sources/{norm_id(data_source_id)}", idempotent=True)

    async def update_data_source(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        """Add (name → config), rename or remove (id → ``None``) properties
        (https://developers.notion.com/reference/update-a-data-source). Setting a
        schema to the same value again changes nothing, so it may be repeated."""
        return await self.request(
            "PATCH", f"/data_sources/{norm_id(data_source_id)}", json={"properties": properties}, idempotent=True
        )

    async def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        """A row (https://developers.notion.com/reference/post-page). Not idempotent."""
        parent = {"type": "data_source_id", "data_source_id": norm_id(data_source_id)}
        body = {"parent": parent, "properties": properties}
        return await self.request("POST", "/pages", json=body, idempotent=False)

    async def update_page(
        self, page_id: str, *, properties: dict[str, Any] | None = None, in_trash: bool | None = None
    ) -> dict[str, Any]:
        """Absolute property values and/or the trash flag
        (https://developers.notion.com/reference/patch-page,
        https://developers.notion.com/reference/archive-a-page) — repeating it changes
        nothing, so it is retried like a read."""
        body: dict[str, Any] = {}
        if properties is not None:
            body["properties"] = properties
        if in_trash is not None:
            body["in_trash"] = in_trash
        return await self.request("PATCH", f"/pages/{norm_id(page_id)}", json=body, idempotent=True)

    async def query_data_source(
        self, data_source_id: str, *, filter: dict[str, Any], page_size: int = 10
    ) -> dict[str, Any]:
        """Rows matching ``filter`` (https://developers.notion.com/reference/query-a-data-source)."""
        return await self.request(
            "POST",
            f"/data_sources/{norm_id(data_source_id)}/query",
            json={"filter": filter, "page_size": page_size},
            idempotent=True,
        )
