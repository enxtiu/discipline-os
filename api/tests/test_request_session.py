"""The request's database session commits before the response leaves the server.

A ``yield`` dependency's exit code runs *after* the response is sent by default
(FastAPI's "request" scope), so a client that writes and reads right away — the
offline queue replaying a task and then its subtask, a list refetched after a
save — could miss its own write (a 404 for the parent it was just told exists).
``get_db`` therefore runs with ``scope="function"`` everywhere.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
from fastapi.routing import APIRoute
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db import get_db


def _session_dependants(dependant: Any) -> Iterator[Any]:
    for sub in dependant.dependencies:
        if sub.call is get_db:
            yield sub
        yield from _session_dependants(sub)


def test_every_endpoint_commits_its_session_before_responding() -> None:
    from app.main import app

    offenders = [
        f"{','.join(sorted(route.methods))} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute)
        for dependant in _session_dependants(route.dependant)
        if dependant.scope != "function"
    ]
    assert offenders == []


async def test_a_write_is_committed_when_its_response_starts(authed: httpx.AsyncClient) -> None:
    from app.main import app

    commits = 0
    commits_at_start: list[int] = []

    def count_commit(_session: Session) -> None:
        nonlocal commits
        commits += 1

    async def recording_app(scope: Any, receive: Any, send: Any) -> None:
        async def recording_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                commits_at_start.append(commits)
            await send(message)

        await app(scope, receive, recording_send)

    event.listen(Session, "after_commit", count_commit)
    try:
        transport = httpx.ASGITransport(app=recording_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=authed.headers) as http:
            created = await http.post("/api/v1/planner/tasks", json={"title": "сначала коммит"})
            # the very next request sees the row (a subtask needs its parent)
            child = await http.post(
                "/api/v1/planner/tasks", json={"title": "подзадача", "parent_id": created.json()["id"]}
            )
    finally:
        event.remove(Session, "after_commit", count_commit)

    assert created.status_code == 201, created.text
    assert child.status_code == 201, child.text
    assert commits_at_start[0] >= 1, "the task was committed only after its 201 had been sent"
