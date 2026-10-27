"""Stage 9: field-level compare-and-set (`base` → 409 `edit_conflict`) on the planner's PATCH/PUT
endpoints, and deleting a meal or a water log by the client id it was logged with."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest

from tests.stage6_helpers import API, code, get_task, make_task, other_user, patch_task, today, uid

pytestmark = pytest.mark.anyio


def conflict(response: httpx.Response) -> dict[str, Any]:
    """The 409 body's details (asserting the shape)."""
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "edit_conflict"
    assert set(error["details"]) == {"fields", "current"}
    return error["details"]


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
async def test_task_base_match_writes(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "старое", priority=3)
    response = await authed.patch(f"{API}/tasks/{task['id']}", json={"title": "новое", "base": {"title": "старое"}})
    assert response.status_code == 200, response.text
    assert response.json()["task"]["title"] == "новое"


async def test_task_base_mismatch_is_409_with_current_and_writes_nothing(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "старое", priority=3)
    await patch_task(authed, task["id"], title="с телефона")  # the other device
    before = await get_task(authed, task["id"])
    response = await authed.patch(
        f"{API}/tasks/{task['id']}",
        json={"title": "с ноутбука", "priority": 1, "base": {"title": "старое", "priority": 3}},
    )
    details = conflict(response)
    assert details["fields"] == ["title"]  # priority still matched: only the field that moved
    # `current` is the task exactly as GET /tasks/{id} shows it
    assert details["current"] == before
    after = await get_task(authed, task["id"])
    assert after == before  # nothing written — not even the priority that did not conflict


async def test_task_without_base_is_last_write_wins(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "старое")
    await patch_task(authed, task["id"], title="с телефона")
    response = await authed.patch(f"{API}/tasks/{task['id']}", json={"title": "с ноутбука"})
    assert response.status_code == 200
    assert response.json()["task"]["title"] == "с ноутбука"
    # null base = no base
    response = await authed.patch(f"{API}/tasks/{task['id']}", json={"title": "ещё", "base": None})
    assert response.status_code == 200


async def test_task_fields_not_in_base_merge(authed: httpx.AsyncClient) -> None:
    """Two devices editing different fields of the same task both win."""
    task = await make_task(authed, "задача", notes_md="план")
    await patch_task(authed, task["id"], notes_md="план с телефона")  # device B: notes
    response = await authed.patch(  # device A: title, started from the old notes but does not change them
        f"{API}/tasks/{task['id']}", json={"title": "задача с ноутбука", "base": {"title": "задача"}}
    )
    assert response.status_code == 200, response.text
    merged = response.json()["task"]
    assert (merged["title"], merged["notes_md"]) == ("задача с ноутбука", "план с телефона")


async def test_task_base_lists_a_precondition_field_it_does_not_change(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "задача", due_date=today().isoformat())
    await patch_task(authed, task["id"], due_date=(today() + timedelta(days=2)).isoformat())
    response = await authed.patch(
        f"{API}/tasks/{task['id']}", json={"priority": 1, "base": {"due_date": today().isoformat()}}
    )
    assert conflict(response)["fields"] == ["due_date"]


async def test_task_replay_with_base_is_idempotent(authed: httpx.AsyncClient) -> None:
    """A queued edit replayed after its first send went through (response lost) is not a conflict."""
    task = await make_task(authed, "старое", tags=["дом"])
    body = {"title": "новое", "tags": ["работа", "дом"], "base": {"title": "старое", "tags": ["дом"]}}
    first = await authed.patch(f"{API}/tasks/{task['id']}", json=body)
    assert first.status_code == 200, first.text
    replay = await authed.patch(f"{API}/tasks/{task['id']}", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["task"]["title"] == "новое"
    assert sorted(tag["name"] for tag in replay.json()["task"]["tags"]) == ["дом", "работа"]
    # the same change made on another device in the meantime is not a conflict either
    other = await make_task(authed, "x")
    await patch_task(authed, other["id"], title="y")
    same = await authed.patch(f"{API}/tasks/{other['id']}", json={"title": "y", "base": {"title": "x"}})
    assert same.status_code == 200


async def test_task_base_normalisation(authed: httpx.AsyncClient) -> None:
    """Values compare the way the response shows them."""
    day = (today() + timedelta(days=1)).isoformat()
    task = await make_task(
        authed,
        "  Пробежка  ",
        notes_md="- [ ] шаг 1\n- [ ] шаг 2",
        tags=["Здоровье", "спорт"],
        context="@дом",
        due_date=day,
        due_time="09:00",
        recurrence_rule="weekly",
        reminders=[{"lead_minutes": 15}, {"lead_minutes": 60}],
    )
    assert task["recurrence_rule"] != "weekly"  # stored as RRULE text, shown with its preset
    base = {
        "title": "Пробежка  ",  # whitespace around text does not count
        "notes_md": "- [ ] шаг 1\r\n- [ ] шаг 2\r\n",  # CRLF = LF, a trailing newline does not count
        "tags": ["#спорт", "здоровье"],  # a set: order, case and '#' do not count
        "context": "дом",  # '@' does not count
        "due_date": day,
        "due_time": "9:00",  # 9:00 = 09:00 = 09:00:00, your wall clock (the device is in Bangkok)
        "recurrence_rule": "weekly",  # a preset key = its RRULE
        "reminders": [{"lead_minutes": 60}, {"lead_minutes": 15}],
        "estimate_minutes": None,
        "status": "open",
    }
    response = await authed.patch(f"{API}/tasks/{task['id']}", json={"priority": 2, "base": base})
    assert response.status_code == 200, response.text
    seconds = await authed.patch(
        f"{API}/tasks/{task['id']}", json={"priority": 1, "base": {"due_time": "09:00:00", "notes_md": None}}
    )
    assert conflict(seconds)["fields"] == ["notes_md"]  # null ≠ text (but "" = null, below)
    empty = await make_task(authed, "без заметки")
    response = await authed.patch(
        f"{API}/tasks/{empty['id']}", json={"notes_md": "заметка", "base": {"notes_md": "", "context": ""}}
    )
    assert response.status_code == 200, response.text
    # the stored UTC instant is 02:00Z — the wall clock is what counts
    utc_clock = await authed.patch(f"{API}/tasks/{task['id']}", json={"priority": 3, "base": {"due_time": "02:00"}})
    assert conflict(utc_clock)["fields"] == ["due_time"]


async def test_task_base_block_status_and_reminders(authed: httpx.AsyncClient) -> None:
    day = (today() + timedelta(days=1)).isoformat()
    task = await make_task(authed, "созвон", block={"date": day, "start": "23:30", "end": "00:30"})
    assert task["status"] == "scheduled" and task["block"]["ends_next_day"] is True
    ok = await authed.patch(
        f"{API}/tasks/{task['id']}",
        json={
            "title": "созвон 2",
            # overnight block as sent; `scheduled` (derived) compares as `open`
            "base": {"block": {"date": day, "start": "23:30", "end": "0:30"}, "status": "open", "reminders": []},
        },
    )
    assert ok.status_code == 200, ok.text
    moved = await authed.patch(
        f"{API}/tasks/{task['id']}",
        json={
            "block": {"date": day, "start": "10:00", "end": "11:00"},
            "base": {"block": {"date": day, "start": "23:00", "end": "00:30"}},
        },
    )
    assert conflict(moved)["fields"] == ["block"]
    assert conflict(moved)["current"]["block"]["start"] == "23:30"
    reminders = await authed.patch(
        f"{API}/tasks/{task['id']}",
        json={"reminders": [{"lead_minutes": 5}], "base": {"reminders": [{"lead_minutes": 5}]}},
    )
    assert conflict(reminders)["fields"] == ["reminders"]


async def test_task_base_tags_differ(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "задача", tags=["дом"])
    await patch_task(authed, task["id"], tags=["дом", "срочно"])
    response = await authed.patch(
        f"{API}/tasks/{task['id']}", json={"tags": ["дом", "работа"], "base": {"tags": ["Дом"]}}
    )
    details = conflict(response)
    assert details["fields"] == ["tags"]
    assert sorted(tag["name"] for tag in details["current"]["tags"]) == ["дом", "срочно"]


async def test_task_conflict_writes_no_status_change(authed: httpx.AsyncClient) -> None:
    """A conflicting completion creates no next repeat and keeps the task open."""
    task = await make_task(authed, "зарядка", due_date=today().isoformat(), recurrence_rule="daily")
    await patch_task(authed, task["id"], title="зарядка 15 мин")
    response = await authed.patch(f"{API}/tasks/{task['id']}", json={"status": "done", "base": {"title": "зарядка"}})
    conflict(response)
    after = await get_task(authed, task["id"])
    assert after["status"] == "open" and after["completed_at"] is None
    upcoming = (await authed.get(f"{API}/views/upcoming", params={"days": 3})).json()
    assert sum(1 for bucket in upcoming["days"] for item in bucket["tasks"] if item["series_id"] == task["id"]) == 1


async def test_task_base_validation_and_scoping(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "задача")
    for base in ({"titel": "x"}, {"due_time": "25:00"}, {"priority": 9}, {"due_time": "утро"}, {"tags": "дом"}):
        response = await authed.patch(f"{API}/tasks/{task['id']}", json={"title": "y", "base": base})
        assert response.status_code == 422, (base, response.text)
        assert code(response) == "validation_error"
    assert (await get_task(authed, task["id"]))["title"] == "задача"
    missing = await authed.patch(f"{API}/tasks/{uid()}", json={"title": "y", "base": {"title": "x"}})
    assert missing.status_code == 404
    async with other_user() as stranger:
        response = await stranger.patch(f"{API}/tasks/{task['id']}", json={"title": "y", "base": {"title": "чужая"}})
        assert response.status_code == 404  # never a 409 that would show another user's row
        assert "current" not in response.text


async def test_task_base_with_a_move_under_a_parent(authed: httpx.AsyncClient) -> None:
    parent = await make_task(authed, "родитель")
    child = await make_task(authed, "шаг")
    response = await authed.patch(
        f"{API}/tasks/{child['id']}", json={"parent_id": parent["id"], "base": {"parent_id": None, "title": "шаг"}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["task"]["parent_id"] == parent["id"]
    stale = await authed.patch(f"{API}/tasks/{child['id']}", json={"parent_id": None, "base": {"parent_id": None}})
    assert conflict(stale)["fields"] == ["parent_id"]


# --------------------------------------------------------------------------- #
# Journal
# --------------------------------------------------------------------------- #
async def test_journal_base(authed: httpx.AsyncClient) -> None:
    day = today().isoformat()
    url = f"{API}/journal/{day}"
    # no entry yet: every field is empty, so a base of empty values creates it
    created = await authed.put(url, json={"id": uid(), "wins_md": "пробежка", "base": {"wins_md": "", "rating": None}})
    assert created.status_code == 200, created.text
    entry = created.json()
    # match (Markdown: CRLF and surrounding whitespace do not count)
    ok = await authed.put(url, json={"wins_md": "пробежка\n5 км", "base": {"wins_md": "пробежка\r\n"}})
    assert ok.status_code == 200, ok.text
    # the other device rated the day; this one still edits the wins from the old text
    await authed.put(url, json={"rating": 4})
    merged = await authed.put(url, json={"note_md": "заметка", "base": {"note_md": None}})
    assert merged.status_code == 200 and merged.json()["rating"] == 4
    stale = await authed.put(
        url, json={"rating": 2, "wins_md": "новое", "base": {"rating": None, "wins_md": "пробежка\n5 км"}}
    )
    details = conflict(stale)
    assert details["fields"] == ["rating"]
    assert details["current"]["id"] == entry["id"] and details["current"]["rating"] == 4
    assert details["current"] == (await authed.get(url)).json()["entry"]  # the entry as GET shows it
    assert (await authed.get(url)).json()["entry"]["wins_md"] == "пробежка\n5 км"  # nothing written
    # replay of a write that went through
    body = {"mood": 5, "tags": ["Спорт", "отдых"], "base": {"mood": None, "tags": []}}
    assert (await authed.put(url, json=body)).status_code == 200
    replay = await authed.put(url, json={**body, "base": {"mood": None, "tags": ["отдых", "спорт"]}})
    assert replay.status_code == 200


async def test_journal_base_after_delete_elsewhere(authed: httpx.AsyncClient) -> None:
    url = f"{API}/journal/{today().isoformat()}"
    await authed.put(url, json={"wins_md": "был текст"})
    assert (await authed.delete(url)).status_code == 204  # the other device deleted the day
    response = await authed.put(url, json={"wins_md": "правка", "base": {"wins_md": "был текст"}})
    details = conflict(response)
    assert details == {"fields": ["wins_md"], "current": None}
    assert (await authed.get(url)).json()["entry"] is None


async def test_journal_base_validation(authed: httpx.AsyncClient) -> None:
    url = f"{API}/journal/{today().isoformat()}"
    for base in ({"id": uid()}, {"rating": 7}, {"day": "x"}):
        response = await authed.put(url, json={"rating": 3, "base": base})
        assert response.status_code == 422, (base, response.text)
    far = (today() + timedelta(days=5)).isoformat()
    assert (await authed.put(f"{API}/journal/{far}", json={"rating": 3, "base": {"rating": None}})).status_code == 422


# --------------------------------------------------------------------------- #
# Projects, areas, resources
# --------------------------------------------------------------------------- #
async def test_project_base(authed: httpx.AsyncClient) -> None:
    area = (await authed.get(f"{API}/areas")).json()["items"][0]
    project = (
        await authed.post(f"{API}/projects", json={"name": "Ремонт", "area_id": area["id"], "color": "#22d3ee"})
    ).json()
    url = f"{API}/projects/{project['id']}"
    ok = await authed.patch(
        url,
        json={
            "name": "Ремонт кухни",
            "base": {"name": "Ремонт", "color": "#22D3EE", "area_id": area["id"], "deadline": None},
        },
    )
    assert ok.status_code == 200, ok.text
    await authed.patch(url, json={"status": "on_hold"})
    stale = await authed.patch(url, json={"status": "completed", "base": {"status": "active", "name": "Ремонт кухни"}})
    details = conflict(stale)
    assert details["fields"] == ["status"]
    assert details["current"]["status"] == "on_hold" and details["current"]["area"]["id"] == area["id"]
    assert details["current"]["counts"] == {"open": 0, "done": 0, "total": 0}  # the ProjectOut the PATCH answers
    assert (await authed.get(url)).json()["project"]["status"] == "on_hold"


async def test_area_base(authed: httpx.AsyncClient) -> None:
    area = (await authed.post(f"{API}/areas", json={"name": "Музыка", "weekly_target_minutes": 120})).json()
    url = f"{API}/areas/{area['id']}"
    ok = await authed.patch(
        url, json={"weekly_target_minutes": 180, "base": {"weekly_target_minutes": 120, "color": None}}
    )
    assert ok.status_code == 200, ok.text
    stale = await authed.patch(url, json={"weekly_target_minutes": 60, "base": {"weekly_target_minutes": 120}})
    details = conflict(stale)
    assert details["fields"] == ["weekly_target_minutes"] and details["current"]["weekly_target_minutes"] == 180
    assert "week_minutes" in details["current"]  # the AreaOut the PATCH answers


async def test_resource_base(authed: httpx.AsyncClient) -> None:
    resource = (
        await authed.post(
            f"{API}/resources", json={"name": "Книга", "url": "https://example.com/a", "tags": ["чтение", "Учёба"]}
        )
    ).json()
    url = f"{API}/resources/{resource['id']}"
    ok = await authed.patch(url, json={"notes_md": "глава 1", "base": {"tags": ["учёба", "#чтение"], "notes_md": ""}})
    assert ok.status_code == 200, ok.text
    await authed.patch(url, json={"url": "https://example.com/b"})
    stale = await authed.patch(url, json={"name": "Книга 2", "base": {"url": "https://example.com/a", "name": "Книга"}})
    details = conflict(stale)
    assert details["fields"] == ["url"] and details["current"]["url"] == "https://example.com/b"


async def test_container_base_scoping(authed: httpx.AsyncClient) -> None:
    project = (await authed.post(f"{API}/projects", json={"name": "Секрет"})).json()
    area = (await authed.post(f"{API}/areas", json={"name": "Секретная"})).json()
    resource = (await authed.post(f"{API}/resources", json={"name": "Секретный"})).json()
    async with other_user() as stranger:
        for path in (f"projects/{project['id']}", f"areas/{area['id']}", f"resources/{resource['id']}"):
            response = await stranger.patch(f"{API}/{path}", json={"name": "x", "base": {"name": "y"}})
            assert response.status_code == 404, path


# --------------------------------------------------------------------------- #
# Time entries
# --------------------------------------------------------------------------- #
async def test_time_entry_base(authed: httpx.AsyncClient) -> None:
    day = (today() - timedelta(days=1)).isoformat()
    task = await make_task(authed, "отчёт")
    entry = (
        await authed.post(
            f"{API}/time-entries",
            json={"date": day, "start": "22:30", "end": "01:15", "task_id": task["id"], "description": "черновик"},
        )
    ).json()
    url = f"{API}/time-entries/{entry['id']}"
    # local wall-clock strings (Bangkok), an overnight end, 9:05 = 09:05
    ok = await authed.patch(
        url,
        json={
            "description": "отчёт за квартал",
            "base": {"date": day, "start": "22:30:00", "end": "1:15", "task_id": task["id"], "description": "черновик"},
        },
    )
    assert ok.status_code == 200, ok.text
    await authed.patch(url, json={"date": day, "start": "21:00", "end": "23:00"})  # the other device
    stale = await authed.patch(
        url,
        json={"date": day, "start": "22:00", "end": "23:30", "base": {"date": day, "start": "22:30", "end": "01:15"}},
    )
    details = conflict(stale)
    assert details["fields"] == ["start", "end"]
    assert (details["current"]["start"], details["current"]["end"]) == ("21:00", "23:00")
    replay = await authed.patch(
        url, json={"date": day, "start": "21:00", "end": "23:00", "base": {"start": "22:30", "end": "01:15"}}
    )
    assert replay.status_code == 200  # already the values this request sets


async def test_running_entry_base_end_is_null(authed: httpx.AsyncClient) -> None:
    started = await authed.post(f"{API}/timer/start", json={"id": uid(), "description": "фокус"})
    entry = started.json()["entry"]
    url = f"{API}/time-entries/{entry['id']}"
    ok = await authed.patch(url, json={"note": "заметка", "base": {"end": None, "note": None, "description": "фокус"}})
    assert ok.status_code == 200, ok.text
    stale = await authed.patch(url, json={"note": "x", "base": {"end": "10:00"}})
    assert conflict(stale)["fields"] == ["end"]
    await authed.post(f"{API}/timer/stop", json={})


# --------------------------------------------------------------------------- #
# Nutrition: a meal / water logged offline is deleted by its client id
# --------------------------------------------------------------------------- #
MEAL_ITEM = {"name": "Каша", "kcal_per_100g": 120, "protein_per_100g": 4, "grams": 150}


async def test_meal_and_water_delete_by_client_id(authed: httpx.AsyncClient) -> None:
    meal_id = uid()
    meal = (await authed.post("/api/v1/nutrition/meals", json={"id": meal_id, "items": [MEAL_ITEM]})).json()
    assert meal["id"] != meal_id and meal["client_uuid"] == meal_id
    assert (await authed.delete(f"/api/v1/nutrition/meals/{meal_id}")).status_code == 200
    assert (await authed.get("/api/v1/nutrition/day")).json()["logged_meals"] == 0
    assert (await authed.delete(f"/api/v1/nutrition/meals/{meal_id}")).status_code == 404  # a replay: already gone
    by_server_id = (await authed.post("/api/v1/nutrition/meals", json={"id": uid(), "items": [MEAL_ITEM]})).json()
    assert (await authed.delete(f"/api/v1/nutrition/meals/{by_server_id['id']}")).status_code == 200

    water_id = uid()
    water = (await authed.post("/api/v1/nutrition/water", json={"id": water_id, "amount_ml": 250})).json()
    assert water["id"] != water_id
    assert (await authed.delete(f"/api/v1/nutrition/water/{water_id}")).status_code == 200
    assert (await authed.get("/api/v1/nutrition/day")).json()["water_ml"] == 0
    assert (await authed.delete(f"/api/v1/nutrition/water/{water_id}")).status_code == 404


async def test_meal_delete_by_client_id_is_scoped(authed: httpx.AsyncClient) -> None:
    meal_id, water_id = uid(), uid()
    await authed.post("/api/v1/nutrition/meals", json={"id": meal_id, "items": [MEAL_ITEM]})
    await authed.post("/api/v1/nutrition/water", json={"id": water_id, "amount_ml": 300})
    async with other_user() as stranger:
        assert (await stranger.delete(f"/api/v1/nutrition/meals/{meal_id}")).status_code == 404
        assert (await stranger.delete(f"/api/v1/nutrition/water/{water_id}")).status_code == 404
    day = (await authed.get("/api/v1/nutrition/day")).json()
    assert day["logged_meals"] == 1 and day["water_ml"] == 300


async def test_offline_meal_keeps_the_tap_time(authed: httpx.AsyncClient) -> None:
    """A meal queued at 23:50 and replayed after midnight belongs to the day it was eaten (logged_at)."""
    yesterday = today() - timedelta(days=1)
    logged_at = f"{yesterday.isoformat()}T23:50:00+07:00"
    meal_id = uid()
    body = {"id": meal_id, "logged_at": logged_at, "meal_type": "snack", "items": [MEAL_ITEM]}
    first = (await authed.post("/api/v1/nutrition/meals", json=body)).json()
    replay = (await authed.post("/api/v1/nutrition/meals", json=body)).json()
    assert first["local_day"] == yesterday.isoformat() and replay["id"] == first["id"]
