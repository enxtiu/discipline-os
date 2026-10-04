"""Reject oversized request bodies before anything parses them.

FastAPI reads (and spools) a multipart body *before* dependencies such as
authentication run, so without this an anonymous client could make the API
buffer arbitrarily large uploads. Two checks:

* a declared ``Content-Length`` above the limit → 413 at once, body unread;
* a body without one (chunked) is counted while it streams and aborted with
  413 as soon as it crosses the limit.

Caddy's ``request_body max_size`` is the outer backstop in production.
"""

from __future__ import annotations

import json

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=f"Request body is larger than {limit} bytes")


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        declared = next((value for key, value in scope["headers"] if key == b"content-length"), None)
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = False  # let the server/parser reject a malformed header
            if too_big:
                body = json.dumps(
                    {
                        "error": {
                            "code": "payload_too_large",
                            "message": f"Request body is larger than {self.max_bytes} bytes",
                        }
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        received = 0
        limit = self.max_bytes

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # fastapi re-raises HTTPException from body parsing unchanged,
                    # so the normal error envelope (413 payload_too_large) applies
                    raise _too_large(limit)
            return message

        await self.app(scope, counting_receive, send)
