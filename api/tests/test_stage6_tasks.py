"""Stage 6: tasks — create/edit, status rules, subtasks, due & blocks (DST), reminders, tags, bounds."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg.errors
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models import Reminder, Task, TimeEntry
from tests.stage6_helpers import (
    API,
    code,
    complete,
    get_task,
    make_task,
    other_user,
    patch_task,
    set_timezone,
    today,
    uid,
)

pytestmark = pytest.mark.anyio


async def _reminders(task_id: str) -> list[Reminder]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Reminder).where(Reminder.task_id == uuid.UUID(task_id)).order_by(Reminder.remind_at)
        )
        return list(rows.all())


# --------------------------------------------------------------------------- #
# create, read, status
# --------------------------------------------------------------------------- #
async def test_create_defaults_and_shape(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "  купить молоко  ", notes_md="2 литра")
    assert task["title"] == "купить молоко"  # trimmed
    assert (task["status"], task["priority"], task["depth"], task["source"]) == ("inbox", 4, 0, "manual")
    assert task["due_date"] is None and task["due_time"] is None and task["block"] is None
    assert task["is_recurring"] is False and task["series_id"] is None and task["series_index"] == 0
    assert task["subtask_counts"] == {"done": 0, "total": 0} and task["tags"] == [] and task["reminders"] == []
    assert task["subtasks"] == [] and task["time"] == {"total_minutes": 0, "entries": []}
    assert task["overdue"] is False and task["actual_minutes"] == 0
    assert task["created_at"].endswith("+00:00") or task["created_at"].endswith("Z")
    assert (await get_task(authed, task["id"]))["notes_md"] == "2 литра"
    inbox = (await authed.get(f"{API}/views/inbox")).json()["items"]
    assert [item["id"] for item in inbox] == [task["id"]]


async def test_status_derivation(authed: httpx.AsyncClient) -> None:
    day = (today() + timedelta(days=3)).isoformat()
    project = (await authed.post(f"{API}/projects", json={"name": "Ремонт"})).json()
    area_id = (await authed.get(f"{API}/areas")).json()["items"][0]["id"]
    assert (await make_task(authed, "p", project_id=project["id"]))["status"] == "open"
    assert (await make_task(authed, "a", area_id=area_id))["status"] == "open"
    assert (await make_task(authed, "d", due_date=day))["status"] == "open"
    blocked = await make_task(authed, "b", block={"date": day, "start": "10:00", "end": "11:00"})
    assert blocked["status"] == "scheduled"
    assert (await make_task(authed, "i", status="inbox", project_id=project["id"]))["status"] == "inbox"
    assert (await make_task(authed, "w", status="waiting", block={"date": day, "start": "10:00", "end": "11:00"}))[
        "status"
    ] == "waiting"

    inbox = await make_task(authed, "из входящих")
    assert (await patch_task(authed, inbox["id"], title="переименовал"))["task"]["status"] == "inbox"
    assert (await patch_task(authed, inbox["id"], due_date=day))["task"]["status"] == "open"
    scheduled = await patch_task(authed, inbox["id"], block={"date": day, "start": "09:00", "end": "09:30"})
    assert scheduled["task"]["status"] == "scheduled"
    assert (await patch_task(authed, inbox["id"], status="open"))["task"]["status"] == "scheduled"  # block wins
    assert (await patch_task(authed, inbox["id"], block=None))["task"]["status"] == "open"
    other = await make_task(authed, "ещё во входящих")
    explicit = await patch_task(authed, other["id"], status="inbox", project_id=project["id"])
    assert explicit["task"]["status"] == "inbox"  # the same PATCH sets status: it wins
    refused = await authed.patch(f"{API}/tasks/{inbox['id']}", json={"status": "scheduled"})
    assert refused.status_code == 422 and code(refused) == "validation_error"
    progress = await patch_task(authed, inbox["id"], status="in_progress")
    assert progress["task"]["status"] == "in_progress"
    assert (await patch_task(authed, inbox["id"], block={"date": day, "start": "09:00", "end": "09:30"}))["task"][
        "status"
    ] == "in_progress"


async def test_create_is_idempotent_and_scoped(authed: httpx.AsyncClient) -> None:
    task_id = uid()
    first = await authed.post(f"{API}/tasks", json={"id": task_id, "title": "один раз"})
    again = await authed.post(f"{API}/tasks", json={"id": task_id, "title": "один раз"})
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == task_id and again.json()["created_at"] == first.json()["created_at"]
    async with SessionLocal() as session:
        stored = await session.get(Task, uuid.UUID(task_id))
        assert stored is not None and stored.client_uuid == uuid.UUID(task_id)
        assert await session.scalar(select(func.count()).select_from(Task)) == 1
    parent = await make_task(authed, "родитель")
    child_id = uid()
    await make_task(authed, "подзадача", id=child_id, parent_id=parent["id"])
    moved = await authed.post(f"{API}/tasks", json={"id": child_id, "title": "подзадача"})  # another parent
    assert moved.status_code == 409 and code(moved) == "id_conflict"
    async with other_user() as stranger:
        taken = await stranger.post(f"{API}/tasks", json={"id": task_id, "title": "чужой"})
        assert taken.status_code == 409 and code(taken) == "id_conflict"
        assert (await stranger.get(f"{API}/tasks/{task_id}")).status_code == 404
        assert (await stranger.patch(f"{API}/tasks/{task_id}", json={"title": "x"})).status_code == 404
        assert (await stranger.post(f"{API}/tasks/{task_id}/complete", json={})).status_code == 404
        assert (await stranger.post(f"{API}/tasks/{task_id}/reopen")).status_code == 404
        assert (await stranger.delete(f"{API}/tasks/{task_id}")).status_code == 404
        sub = await stranger.post(f"{API}/tasks", json={"title": "x", "parent_id": task_id})
        assert sub.status_code == 404
        assert (await stranger.get(f"{API}/views/inbox")).json()["items"] == []
    assert (await authed.delete(f"{API}/tasks/{task_id}")).status_code == 204
    gone = await authed.delete(f"{API}/tasks/{task_id}")
    assert gone.status_code == 404 and code(gone) == "not_found"


# --------------------------------------------------------------------------- #
# due date / time, blocks, DST
# --------------------------------------------------------------------------- #
async def test_due_and_block_conversion(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "встреча", due_date="2026-11-03", due_time="18:00")
    assert (task["due_date"], task["due_time"]) == ("2026-11-03", "18:00")
    assert datetime.fromisoformat(task["due_at"]) == datetime(2026, 11, 3, 11, 0, tzinfo=UTC)  # Bangkok = UTC+7
    moved = await patch_task(authed, task["id"], due_date="2026-11-05")
    assert (moved["task"]["due_date"], moved["task"]["due_time"]) == ("2026-11-05", "18:00")  # time kept
    cleared = await patch_task(authed, task["id"], due_date=None)
    assert cleared["task"]["due_time"] is None and cleared["task"]["due_at"] is None  # the time goes with the date
    no_date = await authed.patch(f"{API}/tasks/{task['id']}", json={"due_time": "10:00"})
    assert no_date.status_code == 422 and code(no_date) == "validation_error"
    created = await authed.post(f"{API}/tasks", json={"title": "x", "due_time": "10:00"})
    assert created.status_code == 422 and code(created) == "validation_error"
    for bad in ("24:00", "7:05", "18:00:00", "18-00"):
        response = await authed.post(f"{API}/tasks", json={"title": "x", "due_date": "2026-11-03", "due_time": bad})
        assert response.status_code == 422, bad

    night = await make_task(authed, "ночная смена", block={"date": "2026-11-03", "start": "23:30", "end": "01:00"})
    block = night["block"]
    assert (block["date"], block["start"], block["end"], block["minutes"], block["ends_next_day"]) == (
        "2026-11-03",
        "23:30",
        "01:00",
        90,
        True,
    )
    assert datetime.fromisoformat(block["start_at"]) == datetime(2026, 11, 3, 16, 30, tzinfo=UTC)
    assert datetime.fromisoformat(block["end_at"]) == datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
    whole_day = await make_task(authed, "сутки", block={"date": "2026-11-03", "start": "08:00", "end": "08:00"})
    assert whole_day["block"]["minutes"] == 1440 and whole_day["block"]["ends_next_day"] is True
    midnight = await make_task(authed, "до полуночи", block={"date": "2026-11-03", "start": "23:00", "end": "00:00"})
    assert midnight["block"]["minutes"] == 60


async def test_dst_days_in_berlin(authed: httpx.AsyncClient) -> None:
    await set_timezone(authed, "Europe/Berlin")
    fall = await make_task(authed, "осень", block={"date": "2026-10-25", "start": "01:00", "end": "04:00"})
    assert fall["block"]["minutes"] == 240  # 03:00 CEST → 02:00 CET: the night has an extra hour
    assert datetime.fromisoformat(fall["block"]["start_at"]) == datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
    assert datetime.fromisoformat(fall["block"]["end_at"]) == datetime(2026, 10, 25, 3, 0, tzinfo=UTC)
    assert (fall["block"]["start"], fall["block"]["end"]) == ("01:00", "04:00")
    spring = await make_task(authed, "весна", block={"date": "2026-03-29", "start": "01:00", "end": "04:00"})
    assert spring["block"]["minutes"] == 120  # 02:00 → 03:00 is skipped
    gap = await make_task(authed, "в несуществующий час", due_date="2026-03-29", due_time="02:30")
    assert datetime.fromisoformat(gap["due_at"]) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert gap["due_time"] == "03:30"  # 02:30 does not exist that night
    twice = await make_task(authed, "дважды", due_date="2026-10-25", due_time="02:30")
    assert datetime.fromisoformat(twice["due_at"]) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # first 02:30
    assert twice["due_time"] == "02:30"
    # the fall-back day is one whole day of wall clock: 24 h on the clock, 25 real hours
    long_day = await make_task(authed, "длинный день", block={"date": "2026-10-25", "start": "00:00", "end": "00:00"})
    block = long_day["block"]
    assert (block["date"], block["start"], block["end"], block["minutes"], block["ends_next_day"]) == (
        "2026-10-25",
        "00:00",
        "00:00",
        1500,
        True,
    )
    assert datetime.fromisoformat(block["start_at"]) == datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    assert datetime.fromisoformat(block["end_at"]) == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    echoed = await patch_task(authed, long_day["id"], block={"date": "2026-10-25", "start": "00:00", "end": "00:00"})
    assert echoed["task"]["block"]["minutes"] == 1500
    short_day = await make_task(authed, "короткий день", block={"date": "2026-03-29", "start": "00:00", "end": "00:00"})
    assert short_day["block"]["minutes"] == 1380
    skipped = await authed.post(
        f"{API}/tasks", json={"title": "x", "block": {"date": "2026-03-29", "start": "02:30", "end": "03:00"}}
    )
    assert skipped.status_code == 422 and code(skipped) == "invalid_block"  # the clock skips 02:00-03:00
    summer = await make_task(authed, "лето", due_date="2026-07-01", due_time="09:00")
    assert datetime.fromisoformat(summer["due_at"]) == datetime(2026, 7, 1, 7, 0, tzinfo=UTC)
    winter = await make_task(authed, "зима", due_date="2026-12-01", due_time="09:00")
    assert datetime.fromisoformat(winter["due_at"]) == datetime(2026, 12, 1, 8, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# subtasks
# --------------------------------------------------------------------------- #
async def test_subtasks_depth_inheritance_cascade(authed: httpx.AsyncClient) -> None:
    project = (await authed.post(f"{API}/projects", json={"name": "Переезд"})).json()
    root = await make_task(authed, "переезд", project_id=project["id"])
    child = await make_task(authed, "упаковать", parent_id=root["id"], sort_order=2)
    first = await make_task(authed, "коробки", parent_id=root["id"], sort_order=1)
    grand = await make_task(authed, "скотч", parent_id=child["id"])
    assert child["project"]["id"] == project["id"] and grand["project"]["id"] == project["id"]  # inherited
    assert (child["depth"], child["parent_title"], grand["depth"], grand["parent_title"]) == (
        1,
        "переезд",
        2,
        "упаковать",
    )
    assert child["status"] == "open"
    orphan = await make_task(authed, "без проекта", parent_id=root["id"], project_id=None)
    assert orphan["project"] is None  # explicitly no project
    too_deep = await authed.post(f"{API}/tasks", json={"title": "x", "parent_id": grand["id"]})
    assert too_deep.status_code == 422 and code(too_deep) == "task_too_deep"

    detail = await get_task(authed, root["id"])
    assert [sub["title"] for sub in detail["subtasks"]] == ["без проекта", "коробки", "упаковать"]
    assert detail["subtasks"][2]["subtasks"][0]["title"] == "скотч"
    assert detail["subtask_counts"] == {"done": 0, "total": 3}

    cycle = await authed.patch(f"{API}/tasks/{root['id']}", json={"parent_id": grand["id"]})
    assert cycle.status_code == 422 and code(cycle) == "task_too_deep"
    itself = await authed.patch(f"{API}/tasks/{root['id']}", json={"parent_id": root["id"]})
    assert itself.status_code == 422 and code(itself) == "task_too_deep"
    other_root = await make_task(authed, "другое")
    other_child = await make_task(authed, "другое: шаг", parent_id=other_root["id"])
    deep_move = await authed.patch(f"{API}/tasks/{child['id']}", json={"parent_id": other_child["id"]})
    assert deep_move.status_code == 422 and code(deep_move) == "task_too_deep"  # its own child would be depth 3
    flat_move = await patch_task(authed, first["id"], parent_id=other_child["id"])
    assert flat_move["task"]["depth"] == 2 and flat_move["task"]["project"] is None  # new parent's project

    entry = await authed.post(
        f"{API}/time-entries",
        json={
            "task_id": grand["id"],
            "date": (today() - timedelta(days=1)).isoformat(),
            "start": "10:00",
            "end": "10:30",
        },
    )
    assert entry.status_code == 201, entry.text
    done = await complete(authed, root["id"])
    assert done["task"]["status"] == "done" and done["next_instance"] is None
    detail = await get_task(authed, root["id"])
    assert {sub["status"] for sub in detail["subtasks"]} == {"done"}  # cascade
    assert detail["subtasks"][1]["subtasks"][0]["status"] == "done"
    reopened = (await authed.post(f"{API}/tasks/{root['id']}/reopen")).json()
    assert reopened["task"]["status"] == "open" and reopened["removed_next_instance_id"] is None
    detail = await get_task(authed, root["id"])
    assert {sub["status"] for sub in detail["subtasks"]} == {"done"}  # cascade-completed children stay done

    assert (await authed.delete(f"{API}/tasks/{root['id']}")).status_code == 204
    for gone in (child, grand, orphan):
        assert (await authed.get(f"{API}/tasks/{gone['id']}")).status_code == 404
    async with SessionLocal() as session:
        kept = await session.get(TimeEntry, uuid.UUID(entry.json()["id"]))
        assert kept is not None and kept.task_id is None and kept.is_deleted is False


async def test_crossing_moves_neither_deadlock_nor_form_a_cycle(authed: httpx.AsyncClient) -> None:
    """PATCH A {parent_id: B} ‖ PATCH B {parent_id: A}: used to deadlock on each other's rows (500)."""
    day = today().isoformat()
    for _ in range(10):
        a = await make_task(authed, "A", due_date=day)
        b = await make_task(authed, "B", due_date=day)
        first, second = await asyncio.gather(
            authed.patch(f"{API}/tasks/{a['id']}", json={"parent_id": b["id"]}),
            authed.patch(f"{API}/tasks/{b['id']}", json={"parent_id": a["id"]}),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 422], (first.text, second.text)
        refused = first if first.status_code == 422 else second
        assert code(refused) == "task_too_deep"
        parents = {(await get_task(authed, a["id"]))["parent_id"], (await get_task(authed, b["id"]))["parent_id"]}
        assert None in parents  # one of them stays a root: no cycle


@pytest.fixture
async def lenient(device_token: str):
    """A client that returns 500 responses instead of raising the app's exception."""
    from app.main import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        http.headers["Authorization"] = f"Bearer {device_token}"
        yield http


async def test_deadlocks_answer_a_retryable_conflict(lenient: httpx.AsyncClient, monkeypatch) -> None:
    """A transaction PostgreSQL aborts to break a deadlock (or a serialization failure) is worth a retry."""
    from app.services.planner import tasks

    task = await make_task(lenient, "x")
    failure: dict[str, Exception] = {}

    async def broken(*_args, **_kwargs):
        raise failure["exc"]

    monkeypatch.setattr(tasks, "update_task", broken)
    for orig in (psycopg.errors.DeadlockDetected("deadlock detected"), psycopg.errors.SerializationFailure("x")):
        failure["exc"] = OperationalError("UPDATE tasks", {}, orig)
        response = await lenient.patch(f"{API}/tasks/{task['id']}", json={"parent_id": uid()})
        assert response.status_code == 409 and code(response) == "conflict", response.text
        assert response.json()["error"]["details"] == {"retryable": True}
    failure["exc"] = OperationalError("UPDATE tasks", {}, psycopg.errors.DiskFull("no space"))
    other = await lenient.patch(f"{API}/tasks/{task['id']}", json={"title": "y"})
    assert other.status_code == 500  # anything else is still a server error


async def test_too_many_subtasks(authed: httpx.AsyncClient, owner) -> None:
    parent = await make_task(authed, "много шагов")
    async with SessionLocal() as session:
        now = datetime.now(UTC)
        session.add_all(
            [
                Task(
                    user_id=owner.id,
                    title=f"шаг {n}",
                    parent_id=uuid.UUID(parent["id"]),
                    status="open",
                    priority=4,
                    created_at=now,
                    updated_at=now,
                )
                for n in range(100)
            ]
        )
        await session.commit()
    extra = await authed.post(f"{API}/tasks", json={"title": "101", "parent_id": parent["id"]})
    assert extra.status_code == 422 and code(extra) == "too_many_subtasks"
    assert (await get_task(authed, parent["id"]))["subtask_counts"]["total"] == 100


# --------------------------------------------------------------------------- #
# reminders
# --------------------------------------------------------------------------- #
async def test_reminders_store_recompute_and_cancel(authed: httpx.AsyncClient) -> None:
    day = today() + timedelta(days=5)
    task = await make_task(
        authed,
        "позвонить врачу",
        due_date=day.isoformat(),
        reminders=[{"lead_minutes": 15}, {"lead_minutes": 15}, {"date": day.isoformat(), "time": "08:00"}],
    )
    relative, absolute = sorted(task["reminders"], key=lambda item: item["lead_minutes"] is None)
    assert (relative["lead_minutes"], relative["date"], relative["time"], relative["status"]) == (
        15,
        None,
        None,
        "pending",
    )
    # date only: the anchor is 09:00 of the due day (Bangkok) → 08:45 local = 01:45 UTC
    expected = datetime(day.year, day.month, day.day, 1, 45, tzinfo=UTC)
    assert datetime.fromisoformat(relative["remind_at"]) == expected
    assert (absolute["lead_minutes"], absolute["date"], absolute["time"]) == (None, day.isoformat(), "08:00")
    assert datetime.fromisoformat(absolute["remind_at"]) == datetime(day.year, day.month, day.day, 1, 0, tzinfo=UTC)
    rows = await _reminders(task["id"])
    assert len(rows) == 2 and {row.kind.value for row in rows} == {"task"}
    assert all(row.dedupe_key == f"task:{task['id']}:{row.id}" for row in rows)
    assert {tuple(sorted(row.payload)) for row in rows} == {("lead_minutes",), ("date", "time")}
    assert {row.title for row in rows} == {"позвонить врачу"}

    timed = await patch_task(authed, task["id"], due_time="18:00", title="позвонить в клинику")
    relative = next(item for item in timed["task"]["reminders"] if item["lead_minutes"] == 15)
    assert datetime.fromisoformat(relative["remind_at"]) == datetime(day.year, day.month, day.day, 10, 45, tzinfo=UTC)
    assert relative["id"] == next(item["id"] for item in task["reminders"] if item["lead_minutes"] == 15)
    assert {row.title for row in await _reminders(task["id"])} == {"позвонить в клинику"}

    replaced = await patch_task(authed, task["id"], reminders=[{"lead_minutes": 15}, {"lead_minutes": 60}])
    assert sorted(item["lead_minutes"] for item in replaced["task"]["reminders"]) == [15, 60]
    assert relative["id"] in {item["id"] for item in replaced["task"]["reminders"]}  # kept, not recreated
    no_anchor = await authed.patch(f"{API}/tasks/{task['id']}", json={"due_date": None})
    assert no_anchor.status_code == 422 and code(no_anchor) == "invalid_reminder"

    done = await complete(authed, task["id"])
    assert {item["status"] for item in done["task"]["reminders"]} == {"cancelled"}
    reopened = (await authed.post(f"{API}/tasks/{task['id']}/reopen")).json()
    assert {item["status"] for item in reopened["task"]["reminders"]} == {"pending"}
    assert (await authed.delete(f"{API}/tasks/{task['id']}")).status_code == 204
    assert await _reminders(task["id"]) == []

    block_only = await make_task(
        authed,
        "тренировка",
        block={"date": day.isoformat(), "start": "19:00", "end": "20:00"},
        reminders=[{"lead_minutes": 30}],
    )
    remind_at = datetime.fromisoformat(block_only["reminders"][0]["remind_at"])
    assert remind_at == datetime(day.year, day.month, day.day, 11, 30, tzinfo=UTC)  # 18:30 Bangkok


async def test_reminder_validation(authed: httpx.AsyncClient) -> None:
    day = (today() + timedelta(days=2)).isoformat()
    dateless = await authed.post(f"{API}/tasks", json={"title": "x", "reminders": [{"lead_minutes": 5}]})
    assert dateless.status_code == 422 and code(dateless) == "invalid_reminder"
    absolute = await make_task(authed, "без даты", reminders=[{"date": day, "time": "07:30"}])
    assert absolute["reminders"][0]["date"] == day  # an absolute reminder needs no due date
    six = [{"lead_minutes": n} for n in range(6)]
    too_many = await authed.post(f"{API}/tasks", json={"title": "x", "due_date": day, "reminders": six})
    assert too_many.status_code == 422 and code(too_many) == "too_many_reminders"
    for bad in (
        [{"lead_minutes": 10081}],
        [{"lead_minutes": -1}],
        [{"lead_minutes": 5, "date": day}],
        [{"date": day}],
        [{"date": day, "time": "25:00"}],
        [{}],
    ):
        response = await authed.post(f"{API}/tasks", json={"title": "x", "due_date": day, "reminders": bad})
        assert response.status_code == 422 and code(response) == "validation_error", bad


# --------------------------------------------------------------------------- #
# tags, small fields, bounds
# --------------------------------------------------------------------------- #
async def test_tags_on_tasks(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "зал", tags=["#Здоровье", "здоровье", "deep-work", "ИДЕЯ"])
    assert [tag["name"] for tag in task["tags"]] == ["deep-work", "здоровье", "идея"]  # defaults matched, any case
    tags = {tag["name"]: tag for tag in (await authed.get(f"{API}/tags")).json()["items"]}
    assert tags["здоровье"]["open_tasks"] == 1 and tags["deep-work"]["open_tasks"] == 1
    replaced = await patch_task(authed, task["id"], tags=["Здоровье"])
    assert [tag["name"] for tag in replaced["task"]["tags"]] == ["здоровье"]
    cleared = await patch_task(authed, task["id"], tags=None)
    assert cleared["task"]["tags"] == []
    for bad in (["a b"], ["#"], ["x" * 65], ["x!"], ["tag", "#?"]):
        response = await authed.post(f"{API}/tasks", json={"title": "x", "tags": bad})
        assert response.status_code == 422 and code(response) == "invalid_tag", bad
    eleven = [f"t{n}" for n in range(11)]
    response = await authed.post(f"{API}/tasks", json={"title": "x", "tags": eleven})
    assert response.status_code == 422 and code(response) == "too_many_tags"


async def test_fields_and_bounds(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "x", context="@дом", energy=2, estimate_minutes=45, is_pinned=True, sort_order=-5)
    assert (task["context"], task["energy"], task["estimate_minutes"], task["is_pinned"], task["sort_order"]) == (
        "дом",
        2,
        45,
        True,
        -5,
    )
    bad_bodies = [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 301},
        {"title": "x", "notes_md": "x" * 20001},
        {"title": "x", "priority": 0},
        {"title": "x", "priority": 5},
        {"title": "x", "energy": 4},
        {"title": "x", "estimate_minutes": 0},
        {"title": "x", "estimate_minutes": 1441},
        {"title": "x", "sort_order": 2**31},
        {"title": "x", "context": "x" * 70},
        {"title": "x", "due_date": "1999-12-31"},
        {"title": "x", "due_date": "9999-12-31"},
        {"title": "x", "block": {"date": "9999-12-31", "start": "10:00", "end": "11:00"}},
        {"title": "x", "status": "scheduled"},
        {"title": "x", "unknown": 1},
        {"title": "nul\u0000byte"},
        {"title": "x", "tags": ["ok\u0000"]},
        {"title": "x", "recurrence_rule": "x" * 256},
    ]
    for body in bad_bodies:
        response = await authed.post(f"{API}/tasks", json=body)
        assert response.status_code == 422, (body, response.text)
    nan = await authed.post(
        f"{API}/tasks", content=b'{"title": "x", "priority": NaN}', headers={"content-type": "application/json"}
    )
    assert nan.status_code == 422
    for body in ({"title": None}, {"priority": None}, {"status": None}, {"is_pinned": None}, {"sort_order": None}):
        response = await authed.patch(f"{API}/tasks/{task['id']}", json=body)
        assert response.status_code == 422, body
    missing = await authed.post(f"{API}/tasks", json={"title": "x", "project_id": uid()})
    assert missing.status_code == 404 and code(missing) == "not_found"
    missing_area = await authed.patch(f"{API}/tasks/{task['id']}", json={"area_id": uid()})
    assert missing_area.status_code == 404
    assert (await authed.get(f"{API}/tasks/not-a-uuid")).status_code == 422
    assert (await authed.get(f"{API}/tasks/{uid()}")).status_code == 404
    long_context = await authed.post(f"{API}/tasks", json={"title": "x", "context": "@" + "к" * 64})
    assert long_context.status_code == 201 and long_context.json()["context"] == "к" * 64


async def test_api_tokens_cannot_use_the_planner(authed: httpx.AsyncClient, client: httpx.AsyncClient) -> None:
    issued = await authed.post("/api/v1/tokens", json={"name": "Shortcut", "scopes": ["ingest:health"]})
    token = issued.json()["token"]
    response = await client.get(f"{API}/views/inbox", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert (await client.post(f"{API}/tasks", json={"title": "x"}, headers={"Authorization": ""})).status_code == 401
