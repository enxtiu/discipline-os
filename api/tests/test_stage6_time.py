"""Stage 6: time tracking — timer start/stop/switch, replays, 24 h rules, manual entries, summaries."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import TimeEntry
from tests.stage6_helpers import API, BANGKOK, code, get_task, make_task, set_timezone, today, uid

pytestmark = pytest.mark.anyio


async def _start(client: httpx.AsyncClient, **body) -> httpx.Response:
    return await client.post(f"{API}/timer/start", json={"id": uid(), **body})


async def _running_count() -> int:
    async with SessionLocal() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(TimeEntry)
                .where(TimeEntry.ended_at.is_(None), TimeEntry.is_deleted.is_(False))
            )
        )


async def test_start_switch_replay_and_stop(authed: httpx.AsyncClient) -> None:
    project = (await authed.post(f"{API}/projects", json={"name": "Работа"})).json()
    area = (await authed.get(f"{API}/areas")).json()["items"][1]
    task = await make_task(authed, "отчёт", project_id=project["id"], area_id=area["id"])
    first_id = uid()
    twenty_ago = datetime.now(UTC) - timedelta(minutes=20)
    first = await authed.post(
        f"{API}/timer/start", json={"id": first_id, "task_id": task["id"], "started_at": twenty_ago.isoformat()}
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["entry"]["id"] == first_id and body["stopped"] is None
    assert (body["entry"]["task"], body["entry"]["project"]["id"], body["entry"]["area"]["id"]) == (
        {"id": task["id"], "title": "отчёт"},
        project["id"],
        area["id"],
    )  # inherited from the task
    assert body["entry"]["running"] is True and body["entry"]["duration_s"] is None and body["entry"]["end"] is None
    assert body["running"]["entry"]["id"] == first_id and 1190 <= body["running"]["elapsed_s"] <= 1300
    assert body["entry"]["date"] == twenty_ago.astimezone(BANGKOK).date().isoformat()
    state = (await authed.get(f"{API}/timer")).json()
    assert state["running"]["entry"]["id"] == first_id and state["server_now"]

    replay = await authed.post(f"{API}/timer/start", json={"id": first_id, "task_id": task["id"]})
    assert replay.status_code == 200
    assert (replay.json()["entry"]["id"], replay.json()["running"]["entry"]["id"], replay.json()["stopped"]) == (
        first_id,
        first_id,
        None,
    )

    second_id = uid()
    switch_at = datetime.now(UTC) - timedelta(minutes=5)
    switched = (
        await authed.post(f"{API}/timer/start", json={"id": second_id, "started_at": switch_at.isoformat()})
    ).json()
    assert switched["stopped"]["id"] == first_id and switched["running"]["entry"]["id"] == second_id
    assert datetime.fromisoformat(switched["stopped"]["ended_at"]) == switch_at.replace(
        microsecond=switch_at.microsecond
    )
    assert switched["stopped"]["duration_s"] == 15 * 60 and switched["stopped"]["running"] is False
    assert (await get_task(authed, task["id"]))["actual_minutes"] == 15
    late_replay = (await authed.post(f"{API}/timer/start", json={"id": first_id})).json()
    assert late_replay["entry"]["running"] is False and late_replay["running"]["entry"]["id"] == second_id
    assert await _running_count() == 1

    stopped = (await authed.post(f"{API}/timer/stop", json={})).json()
    assert stopped["stopped"]["id"] == second_id and stopped["stopped"]["duration_s"] >= 300
    assert (await authed.post(f"{API}/timer/stop", json={})).json()["stopped"] is None  # nothing runs: no-op
    again = (await authed.post(f"{API}/timer/stop", json={"entry_id": second_id})).json()
    assert again["stopped"]["id"] == second_id and again["stopped"]["duration_s"] == stopped["stopped"]["duration_s"]
    assert (await authed.post(f"{API}/timer/stop")).status_code == 200  # an empty body is fine
    assert (await authed.post(f"{API}/timer/stop", json={"entry_id": uid()})).status_code == 404
    assert (await authed.get(f"{API}/timer")).json()["running"] is None


async def test_concurrent_starts_leave_one_running(authed: httpx.AsyncClient) -> None:
    responses = await asyncio.gather(*(_start(authed) for _ in range(4)))
    assert [response.status_code for response in responses] == [200] * 4
    assert await _running_count() == 1
    running = (await authed.get(f"{API}/timer")).json()["running"]["entry"]["id"]
    assert running in {response.json()["entry"]["id"] for response in responses}
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count()).select_from(TimeEntry))
    assert total == 4


async def test_start_bounds_and_long_timers(authed: httpx.AsyncClient, owner) -> None:
    for started in (datetime.now(UTC) - timedelta(hours=25), datetime.now(UTC) + timedelta(minutes=10)):
        response = await _start(authed, started_at=started.isoformat())
        assert response.status_code == 422 and code(response) == "invalid_time_entry"
    assert (await _start(authed, description="x" * 301)).status_code == 422
    assert (await _start(authed, started_at="9999-12-31T23:59:00Z")).status_code == 422
    assert (await authed.post(f"{API}/timer/start", json={})).status_code == 422  # the id is required

    started_at = datetime.now(UTC) - timedelta(hours=30)
    async with SessionLocal() as session:
        forgotten = TimeEntry(
            user_id=owner.id, started_at=started_at, local_day=started_at.astimezone(BANGKOK).date(), source="timer"
        )
        session.add(forgotten)
        await session.commit()
        forgotten_id = str(forgotten.id)
    too_long = await authed.post(f"{API}/timer/stop", json={})
    assert too_long.status_code == 409 and code(too_long) == "timer_too_long"
    assert datetime.fromisoformat(too_long.json()["error"]["details"]["started_at"]) == started_at
    switch = await _start(authed)
    assert switch.status_code == 409 and code(switch) == "timer_too_long"
    for ended in (
        started_at - timedelta(minutes=1),
        started_at + timedelta(hours=25),
        datetime.now(UTC) + timedelta(hours=1),
    ):
        response = await authed.post(f"{API}/timer/stop", json={"ended_at": ended.isoformat()})
        assert response.status_code == 422 and code(response) == "invalid_time_entry", ended
    fixed = await authed.post(
        f"{API}/timer/stop", json={"entry_id": forgotten_id, "ended_at": (started_at + timedelta(hours=2)).isoformat()}
    )
    assert fixed.status_code == 200 and fixed.json()["stopped"]["duration_s"] == 7200


async def test_manual_entries_patch_delete_and_actual_minutes(authed: httpx.AsyncClient) -> None:
    yesterday = today() - timedelta(days=1)
    night = yesterday - timedelta(days=1)  # across a midnight that is over even at 00:01 today
    task = await make_task(authed, "чтение")
    other = await make_task(authed, "письмо")
    entry_id = uid()
    body = {"id": entry_id, "task_id": task["id"], "date": night.isoformat(), "start": "23:30", "end": "00:30"}
    created = await authed.post(f"{API}/time-entries", json=body)
    assert created.status_code == 201, created.text
    entry = created.json()
    assert (
        entry["source"],
        entry["duration_s"],
        entry["ends_next_day"],
        entry["date"],
        entry["start"],
        entry["end"],
    ) == (
        "manual",
        3600,
        True,
        night.isoformat(),
        "23:30",
        "00:30",
    )
    replay = await authed.post(f"{API}/time-entries", json=body)
    assert replay.status_code == 201 and replay.json()["id"] == entry_id
    assert (await get_task(authed, task["id"]))["actual_minutes"] == 60
    whole_day = await authed.post(
        f"{API}/time-entries",
        json={"date": (yesterday - timedelta(days=1)).isoformat(), "start": "10:00", "end": "10:00"},
    )
    assert whole_day.status_code == 201 and whole_day.json()["duration_s"] == 86400
    future = datetime.now(BANGKOK) + timedelta(hours=2)
    bad_bodies = [
        {
            "date": future.date().isoformat(),
            "start": future.strftime("%H:%M"),
            "end": (future + timedelta(minutes=30)).strftime("%H:%M"),
        },
        {"date": yesterday.isoformat(), "start": "10:00"},
        {"date": yesterday.isoformat(), "start": "10:00", "end": "25:00"},
        {"date": "1999-01-01", "start": "10:00", "end": "11:00"},
        {"date": yesterday.isoformat(), "start": "10:00", "end": "11:00", "description": "x" * 301},
    ]
    for bad in bad_bodies:
        response = await authed.post(f"{API}/time-entries", json=bad)
        assert response.status_code == 422, (bad, response.text)
    assert code(await authed.post(f"{API}/time-entries", json=bad_bodies[0])) == "invalid_time_entry"

    moved = await authed.patch(
        f"{API}/time-entries/{entry_id}",
        json={"task_id": other["id"], "date": yesterday.isoformat(), "start": "08:00", "end": "08:45"},
    )
    assert moved.status_code == 200, moved.text
    assert (moved.json()["task"]["id"], moved.json()["duration_s"], moved.json()["ends_next_day"]) == (
        other["id"],
        2700,
        False,
    )
    assert (await get_task(authed, task["id"]))["actual_minutes"] == 0
    assert (await get_task(authed, other["id"]))["actual_minutes"] == 45
    detail = await get_task(authed, other["id"])
    assert detail["time"]["total_minutes"] == 45 and [item["id"] for item in detail["time"]["entries"]] == [entry_id]
    partial = await authed.patch(f"{API}/time-entries/{entry_id}", json={"start": "09:00"})
    assert partial.status_code == 422
    noted = (
        await authed.patch(f"{API}/time-entries/{entry_id}", json={"description": "глава 3", "note": "медленно"})
    ).json()
    assert (noted["description"], noted["note"]) == ("глава 3", "медленно")
    assert (await authed.delete(f"{API}/time-entries/{entry_id}")).status_code == 204
    assert (await get_task(authed, other["id"]))["actual_minutes"] == 0
    assert (await authed.delete(f"{API}/time-entries/{entry_id}")).status_code == 404
    assert (await authed.patch(f"{API}/time-entries/{entry_id}", json={"note": "x"})).status_code == 404
    async with SessionLocal() as session:
        kept = await session.get(TimeEntry, uuid.UUID(entry_id))
        assert kept is not None and kept.is_deleted is True  # soft delete


async def test_entry_list_and_summary(authed: httpx.AsyncClient) -> None:
    anchor = today() - timedelta(days=1)
    areas = (await authed.get(f"{API}/areas")).json()["items"]
    health = areas[0]
    await authed.patch(f"{API}/areas/{health['id']}", json={"weekly_target_minutes": 300})
    project = (await authed.post(f"{API}/projects", json={"name": "Диплом", "area_id": areas[3]["id"]})).json()
    for body in (
        {"area_id": health["id"], "start": "07:00", "end": "08:00"},
        {"area_id": health["id"], "start": "18:00", "end": "18:30"},
        {"project_id": project["id"], "start": "10:00", "end": "12:00"},  # the area comes from the project
        {"start": "13:00", "end": "13:20"},
    ):
        response = await authed.post(f"{API}/time-entries", json={"date": anchor.isoformat(), **body})
        assert response.status_code == 201, response.text
    assert response.json()["area"] is None
    listing = (
        await authed.get(
            f"{API}/time-entries", params={"from": (anchor - timedelta(days=2)).isoformat(), "to": anchor.isoformat()}
        )
    ).json()
    assert len(listing["items"]) == 4 and listing["total_minutes"] == 230
    assert [day["minutes"] for day in listing["by_day"]] == [0, 0, 230]
    assert listing["items"][0]["start"] == "18:00"  # newest first
    too_long = await authed.get(
        f"{API}/time-entries", params={"from": anchor.isoformat(), "to": (anchor + timedelta(days=62)).isoformat()}
    )
    assert too_long.status_code == 422 and code(too_long) == "range_too_large"

    week = (await authed.get(f"{API}/time/summary", params={"date": anchor.isoformat()})).json()
    first = anchor - timedelta(days=anchor.weekday())  # week starts on Monday by default
    assert (week["period"], week["from"], week["to"], week["total_minutes"]) == (
        "week",
        first.isoformat(),
        (first + timedelta(days=6)).isoformat(),
        230,
    )
    by_area = {(item["area"] or {}).get("name"): item for item in week["by_area"]}
    assert (by_area[health["name"]]["minutes"], by_area[health["name"]]["target_minutes"]) == (90, 300)
    assert by_area[areas[3]["name"]]["minutes"] == 120 and by_area[None]["minutes"] == 20
    assert {(item["project"] or {}).get("name"): item["minutes"] for item in week["by_project"]} == {
        "Диплом": 120,
        None: 110,
    }
    assert len(week["by_day"]) == 7 and sum(day["minutes"] for day in week["by_day"]) == 230

    await authed.patch("/api/v1/settings", json={"week_start": 0})
    sunday_week = (await authed.get(f"{API}/time/summary", params={"date": anchor.isoformat()})).json()
    sunday = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    assert sunday_week["from"] == sunday.isoformat()
    month = (await authed.get(f"{API}/time/summary", params={"period": "month", "date": anchor.isoformat()})).json()
    assert month["from"] == anchor.replace(day=1).isoformat() and month["total_minutes"] == 230
    assert (
        next(item for item in month["by_area"] if item["area"] and item["area"]["id"] == health["id"])["target_minutes"]
        == 1305
    )
    assert (await authed.get(f"{API}/time/summary", params={"period": "year"})).status_code == 422


async def test_dst_fall_back_day_is_one_whole_day(authed: httpx.AsyncClient, owner) -> None:
    """24 hours of wall clock are 25 real hours on the fall-back day: entries and timers take the whole day."""
    await set_timezone(authed, "Europe/Berlin")
    whole = await authed.post(f"{API}/time-entries", json={"date": "2026-10-25", "start": "00:00", "end": "00:00"})
    assert whole.status_code == 201, whole.text
    body = whole.json()
    assert (body["duration_s"], body["date"], body["start"], body["end"], body["ends_next_day"]) == (
        25 * 3600,
        "2026-10-25",
        "00:00",
        "00:00",
        True,
    )
    again = await authed.patch(
        f"{API}/time-entries/{body['id']}", json={"date": "2026-10-25", "start": "00:00", "end": "00:00"}
    )
    assert again.status_code == 200 and again.json()["duration_s"] == 25 * 3600
    gap = await authed.post(f"{API}/time-entries", json={"date": "2026-03-29", "start": "02:30", "end": "03:00"})
    assert gap.status_code == 422 and code(gap) == "invalid_time_entry"  # the clock skips 02:00-03:00

    started_at = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)  # 2026-10-25 00:00 in Berlin
    async with SessionLocal() as session:
        forgotten = TimeEntry(user_id=owner.id, started_at=started_at, local_day=date(2026, 10, 25), source="timer")
        session.add(forgotten)
        await session.commit()
        forgotten_id = str(forgotten.id)
    too_long = await authed.post(  # 00:30 the next day: 24 h 30 min of wall clock
        f"{API}/timer/stop", json={"entry_id": forgotten_id, "ended_at": "2026-10-25T23:30:00Z"}
    )
    assert too_long.status_code == 422 and code(too_long) == "invalid_time_entry"
    midnight = await authed.post(  # midnight to midnight: 24 h of wall clock, 25 real hours
        f"{API}/timer/stop", json={"entry_id": forgotten_id, "ended_at": "2026-10-25T23:00:00Z"}
    )
    assert midnight.status_code == 200, midnight.text
    assert (midnight.json()["stopped"]["duration_s"], midnight.json()["stopped"]["end"]) == (25 * 3600, "00:00")


def _ago(now: datetime, minutes: int) -> str:
    return (now - timedelta(minutes=minutes)).isoformat()


async def test_replayed_start_keeps_the_newer_running_timer(authed: httpx.AsyncClient) -> None:
    """Laptop timer runs since 20 min; the offline phone tapped start 30 min ago and stop 25 min ago.
    Replaying both used to stop the laptop timer at 0 s and leave nothing running (20+ min lost)."""
    now = datetime.now(UTC)
    task = await make_task(authed, "звонок")
    laptop_id, phone_id = uid(), uid()
    laptop = await authed.post(
        f"{API}/timer/start", json={"id": laptop_id, "description": "laptop", "started_at": _ago(now, 20)}
    )
    assert laptop.status_code == 200, laptop.text
    replay = await authed.post(
        f"{API}/timer/start",
        json={"id": phone_id, "task_id": task["id"], "description": "phone", "started_at": _ago(now, 30)},
    )
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert (body["entry"]["id"], body["entry"]["running"], body["entry"]["duration_s"]) == (phone_id, False, 600)
    assert body["entry"]["ended_at"] == laptop.json()["entry"]["started_at"]  # ended where the newer timer began
    assert body["running"]["entry"]["id"] == laptop_id and body["stopped"] is None

    stop = await authed.post(f"{API}/timer/stop", json={"entry_id": phone_id, "ended_at": _ago(now, 25)})
    assert stop.status_code == 200, stop.text
    assert (stop.json()["stopped"]["id"], stop.json()["stopped"]["duration_s"]) == (phone_id, 300)  # first stop wins
    running = (await authed.get(f"{API}/timer")).json()["running"]
    assert running["entry"]["id"] == laptop_id and running["entry"]["duration_s"] is None
    assert running["elapsed_s"] >= 20 * 60 - 5
    assert (await get_task(authed, task["id"]))["actual_minutes"] == 5

    for later in ({"entry_id": phone_id, "ended_at": _ago(now, 22)}, {"entry_id": phone_id}):
        again = await authed.post(f"{API}/timer/stop", json=later)  # a later or repeated stop changes nothing
        assert again.status_code == 200 and again.json()["stopped"]["duration_s"] == 300, later
    before_start = await authed.post(f"{API}/timer/stop", json={"entry_id": phone_id, "ended_at": _ago(now, 40)})
    assert before_start.status_code == 422 and code(before_start) == "invalid_time_entry"
    # an old client's stop without entry_id cannot reach back before the running timer either
    blind = await authed.post(f"{API}/timer/stop", json={"ended_at": _ago(now, 25)})
    assert blind.status_code == 422 and code(blind) == "invalid_time_entry"
    assert (await authed.get(f"{API}/timer")).json()["running"]["entry"]["id"] == laptop_id
    replayed_again = (
        await authed.post(f"{API}/timer/start", json={"id": phone_id, "started_at": _ago(now, 30)})
    ).json()
    assert (replayed_again["entry"]["duration_s"], replayed_again["running"]["entry"]["id"]) == (300, laptop_id)
    assert await _running_count() == 1


async def test_replayed_offline_switches_end_each_other(authed: httpx.AsyncClient) -> None:
    """Offline: start A 30 min ago, switch to C 28 min ago, stop 25 min ago — replayed onto a timer running since 20."""
    now = datetime.now(UTC)
    laptop_id, a_id, c_id = uid(), uid(), uid()
    await authed.post(f"{API}/timer/start", json={"id": laptop_id, "started_at": _ago(now, 20)})
    first = (await authed.post(f"{API}/timer/start", json={"id": a_id, "started_at": _ago(now, 30)})).json()
    assert (first["entry"]["running"], first["entry"]["duration_s"], first["stopped"]) == (False, 600, None)
    switch = (await authed.post(f"{API}/timer/start", json={"id": c_id, "started_at": _ago(now, 28)})).json()
    assert (switch["entry"]["id"], switch["entry"]["running"], switch["entry"]["duration_s"]) == (c_id, False, 480)
    assert (switch["stopped"]["id"], switch["stopped"]["duration_s"]) == (a_id, 120)  # C switched A off at its start
    assert switch["running"]["entry"]["id"] == laptop_id
    stop = (await authed.post(f"{API}/timer/stop", json={"entry_id": c_id, "ended_at": _ago(now, 25)})).json()
    assert stop["stopped"]["duration_s"] == 180
    day = (now - timedelta(minutes=30)).astimezone(BANGKOK).date().isoformat()
    listing = (await authed.get(f"{API}/time-entries", params={"from": day, "to": today().isoformat()})).json()
    durations = {item["id"]: item["duration_s"] for item in listing["items"]}
    assert (durations[a_id], durations[c_id], durations[laptop_id]) == (120, 180, None)
    assert await _running_count() == 1


async def test_start_now_switches_even_a_timer_stamped_ahead(authed: httpx.AsyncClient) -> None:
    """A device clock a few minutes fast stamps its start ahead of the server: a later "start now" still switches."""
    ahead_id, now_id = uid(), uid()
    ahead = await authed.post(
        f"{API}/timer/start",
        json={"id": ahead_id, "started_at": (datetime.now(UTC) + timedelta(minutes=3)).isoformat()},
    )
    assert ahead.status_code == 200, ahead.text
    switched = (await authed.post(f"{API}/timer/start", json={"id": now_id})).json()
    assert (switched["entry"]["running"], switched["running"]["entry"]["id"]) == (True, now_id)
    assert (switched["stopped"]["id"], switched["stopped"]["duration_s"]) == (ahead_id, 0)
