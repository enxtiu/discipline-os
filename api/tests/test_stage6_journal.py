"""Stage 6: the daily journal (upsert keeps unsent fields) and the cross-domain day summary."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import httpx
import pytest

from app.db import SessionLocal
from app.models import MealLog
from app.models.training import WorkoutLog
from tests.stage6_helpers import API, BANGKOK, code, complete, make_task, today, uid

pytestmark = pytest.mark.anyio


async def test_journal_upsert_list_and_delete(authed: httpx.AsyncClient) -> None:
    day = today().isoformat()
    entry_id = uid()
    first = await authed.put(
        f"{API}/journal/{day}",
        json={"id": entry_id, "note_md": "Хороший день", "rating": 4, "mood": 5, "tags": ["#спорт", "Спорт", "работа"]},
    )
    assert first.status_code == 200, first.text
    entry = first.json()
    assert (entry["id"], entry["date"], entry["note_md"], entry["rating"], entry["mood"], entry["energy"]) == (
        entry_id,
        day,
        "Хороший день",
        4,
        5,
        None,
    )
    assert entry["tags"] == ["спорт", "работа"]
    second = (await authed.put(f"{API}/journal/{day}", json={"energy": 3, "wins_md": "пробежал 5 км"})).json()
    assert (second["id"], second["note_md"], second["rating"], second["energy"], second["wins_md"]) == (
        entry_id,
        "Хороший день",
        4,
        3,
        "пробежал 5 км",
    )  # fields not sent keep their value
    cleared = (await authed.put(f"{API}/journal/{day}", json={"rating": None, "id": uid()})).json()
    assert cleared["rating"] is None and cleared["id"] == entry_id  # the client id only matters on create
    replay = (await authed.put(f"{API}/journal/{day}", json={"id": entry_id, "mood": 5})).json()
    assert replay["id"] == entry_id

    yesterday = (today() - timedelta(days=1)).isoformat()
    other_day = await authed.put(f"{API}/journal/{yesterday}", json={"id": entry_id, "note_md": "x"})
    assert other_day.status_code == 409 and code(other_day) == "id_conflict"
    await authed.put(f"{API}/journal/{yesterday}", json={"rating": 2})
    listing = (await authed.get(f"{API}/journal", params={"from": yesterday, "to": day})).json()["items"]
    assert listing == [
        {"date": yesterday, "rating": 2, "mood": None, "energy": None, "has_note": False},
        {"date": day, "rating": None, "mood": 5, "energy": 3, "has_note": True},
    ]
    too_long = await authed.get(
        f"{API}/journal", params={"from": (today() - timedelta(days=366)).isoformat(), "to": day}
    )
    assert too_long.status_code == 422 and code(too_long) == "range_too_large"
    assert (await authed.get(f"{API}/journal", params={"from": day, "to": yesterday})).status_code == 422

    tomorrow = (today() + timedelta(days=1)).isoformat()
    assert (await authed.put(f"{API}/journal/{tomorrow}", json={"note_md": "план"})).status_code == 200
    for bad_day in (
        (today() + timedelta(days=2)).isoformat(),
        (today() - timedelta(days=3651)).isoformat(),
        "9999-12-31",
    ):
        for method in ("get", "put", "delete"):
            kwargs = {"json": {"note_md": "x"}} if method == "put" else {}
            response = await getattr(authed, method)(f"{API}/journal/{bad_day}", **kwargs)
            assert response.status_code == 422 and code(response) == "validation_error", (method, bad_day)
    assert (await authed.get(f"{API}/journal/2026-13-01")).status_code == 422
    for body in ({"rating": 0}, {"mood": 6}, {"note_md": "x" * 20001}, {"tags": ["a b"]}, {"tags": [f"t{n}" for n in range(21)]},
                 {"unknown": 1}):  # fmt: skip
        assert (await authed.put(f"{API}/journal/{day}", json=body)).status_code == 422, body

    assert (await authed.delete(f"{API}/journal/{day}")).status_code == 204
    assert (await authed.get(f"{API}/journal/{day}")).json()["entry"] is None
    assert (await authed.delete(f"{API}/journal/{day}")).status_code == 404


async def test_day_summary_crosses_domains(authed: httpx.AsyncClient, owner) -> None:
    day = today() - timedelta(days=1)
    targets = await authed.put("/api/v1/settings/targets", json={"target_kcal": 2200})
    assert targets.status_code == 200, targets.text
    noon = datetime.combine(day, time(12), tzinfo=BANGKOK)
    async with SessionLocal() as session:
        session.add_all(
            [
                MealLog(
                    user_id=owner.id,
                    logged_at=noon,
                    local_day=day,
                    kcal=Decimal("650.4"),
                    meal_type="lunch",
                    source="search",
                ),
                MealLog(
                    user_id=owner.id,
                    logged_at=noon,
                    local_day=day,
                    kcal=Decimal("1200"),
                    meal_type="dinner",
                    source="search",
                ),
                MealLog(
                    user_id=owner.id,
                    logged_at=noon,
                    local_day=day,
                    kcal=Decimal("999"),
                    meal_type="snack",
                    source="search",
                    is_deleted=True,
                ),
                WorkoutLog(
                    user_id=owner.id,
                    title="ноги",
                    started_at=noon,
                    local_day=day,
                    status="completed",
                    kcal_burned=Decimal("320.4"),
                ),
                WorkoutLog(user_id=owner.id, title="брошена", started_at=noon, local_day=day, status="in_progress"),
            ]
        )
        await session.commit()
    assert (await authed.put(f"/api/v1/health/steps/{day.isoformat()}", json={"steps": 8342})).status_code == 200
    sleep = await authed.put(
        f"/api/v1/health/sleep/{day.isoformat()}", json={"bed_time": "23:30", "wake_time": "07:15"}
    )
    assert sleep.status_code == 200, sleep.text

    titles = [f"задача {n}" for n in range(6)]
    for title in titles:
        task = await make_task(authed, title)
        await complete(authed, task["id"], completed_at=datetime.combine(day, time(9), tzinfo=BANGKOK).isoformat())
    late = await make_task(authed, "после полуночи")
    await complete(
        authed,
        late["id"],
        completed_at=datetime.combine(day + timedelta(days=1), time(0, 5), tzinfo=BANGKOK).isoformat(),
    )
    await make_task(authed, "висит", due_date=day.isoformat())
    area = (await authed.get(f"{API}/areas")).json()["items"][0]
    for body in ({"area_id": area["id"], "start": "08:00", "end": "09:00"}, {"start": "10:00", "end": "10:25"}):
        response = await authed.post(f"{API}/time-entries", json={"date": day.isoformat(), **body})
        assert response.status_code == 201, response.text

    summary = (await authed.get(f"{API}/journal/{day.isoformat()}")).json()["day"]
    assert summary["date"] == day.isoformat()
    assert summary["tasks_done"] == 6 and summary["tasks_done_titles"] == titles[:5]
    assert summary["tasks_open_due"] == 1
    assert summary["time_minutes"] == 85
    assert summary["time_by_area"] == [
        {"area": {"id": area["id"], "name": area["name"], "color": area["color"]}, "minutes": 60},
        {"area": None, "minutes": 25},
    ]
    assert (summary["kcal"], summary["target_kcal"]) == (1850, 2200)  # the deleted snack is not counted
    assert (summary["workouts"], summary["workout_kcal"]) == (1, 320)  # burned: separate, never budget
    assert (summary["steps"], summary["sleep_minutes"]) == (8342, 465)

    empty = (await authed.get(f"{API}/journal/{(day - timedelta(days=30)).isoformat()}")).json()
    assert empty["entry"] is None
    assert empty["day"] == {
        "date": (day - timedelta(days=30)).isoformat(),
        "tasks_done": 0,
        "tasks_done_titles": [],
        "tasks_open_due": 0,
        "time_minutes": 0,
        "time_by_area": [],
        "kcal": 0,
        "target_kcal": 2200,
        "workouts": 0,
        "workout_kcal": None,
        "steps": None,
        "sleep_minutes": None,
    }
