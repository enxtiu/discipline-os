"""Stage 4: exercise library, templates, weekly plan, live workouts, PRs, burned kcal."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import User
from app.models.training import BodyMetric, Exercise, WorkoutLog
from app.seeds.exercises import seed_exercises
from app.services.training import epley, week_start_for

pytestmark = pytest.mark.anyio
API = "/api/v1/training"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
async def gym(authed: httpx.AsyncClient) -> httpx.AsyncClient:
    async with SessionLocal() as session:
        await seed_exercises(session)
        await session.commit()
    assert (await authed.patch("/api/v1/settings", json={"weight_kg": 80})).status_code == 200
    return authed


async def _ex(client: httpx.AsyncClient, name_en: str) -> dict[str, Any]:
    items = (await client.get(f"{API}/exercises", params={"q": name_en})).json()["items"]
    found = next((item for item in items if item["name_en"] == name_en), None)
    assert found, f"{name_en} not found in {[item['name_en'] for item in items]}"
    return found


async def _start(client: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    response = await client.post(f"{API}/workouts", json={"id": str(uuid.uuid4()), **body})
    assert response.status_code == 201, response.text
    return response.json()


async def _set(client, workout_id, exercise_id, set_id=None, **values) -> httpx.Response:
    return await client.put(
        f"{API}/workouts/{workout_id}/sets/{set_id or uuid.uuid4()}",
        json={"exercise_id": exercise_id, **values},
    )


async def _finish(client, workout_id, **body) -> dict[str, Any]:
    response = await client.post(f"{API}/workouts/{workout_id}/finish", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_epley_and_week_start() -> None:
    assert float(epley(100, 1)) == 100.0
    assert float(epley(100, 5)) == pytest.approx(116.67, abs=0.01)
    assert epley(100, 15) is None and epley(None, 5) is None and epley(0, 5) is None
    wednesday = date(2026, 10, 7)
    assert week_start_for(wednesday, 1) == date(2026, 10, 5)  # Monday
    assert week_start_for(wednesday, 0) == date(2026, 10, 4)  # Sunday
    assert week_start_for(date(2026, 10, 4), 0) == date(2026, 10, 4)


# --------------------------------------------------------------------------- #
# exercise library
# --------------------------------------------------------------------------- #
async def test_exercise_search_ru_en_aliases_and_filters(gym: httpx.AsyncClient) -> None:
    def names(response):
        return [item["name_en"] for item in response.json()["items"]]

    assert names(await gym.get(f"{API}/exercises", params={"q": "жим лёжа"}))[0] == "Barbell bench press"
    assert names(await gym.get(f"{API}/exercises", params={"q": "жим лежа"}))[0] == "Barbell bench press"  # ё/е
    assert "Barbell bench press" in names(await gym.get(f"{API}/exercises", params={"q": "bench"}))
    assert names(await gym.get(f"{API}/exercises", params={"q": "присед"}))[0] in ("Back squat", "Front squat")
    chest = (await gym.get(f"{API}/exercises", params={"muscle": "chest"})).json()["items"]
    assert chest and all(item["muscle_group"] == "chest" or "chest" in item["secondary_muscles"] for item in chest)
    run = await _ex(gym, "Outdoor running")
    assert (run["log_mode"], run["exercise_type"]) == ("cardio", "cardio")
    assert (await _ex(gym, "Pull-up"))["log_mode"] == "reps"
    assert (await _ex(gym, "Plank"))["log_mode"] == "duration"
    assert (await _ex(gym, "Deadlift"))["log_mode"] == "weight_reps"
    full = (await gym.get(f"{API}/exercises", params={"limit": 200})).json()["items"]
    assert len(full) == 52


async def test_custom_exercise_is_mine_first_and_private(gym: httpx.AsyncClient) -> None:
    exercise_id = str(uuid.uuid4())
    body = {"id": exercise_id, "name_ru": "Жим в Смите", "muscle_group": "chest", "equipment": "machine"}
    created = await gym.post(f"{API}/exercises", json=body)
    assert created.status_code == 201 and created.json()["is_custom"] is True
    assert created.json()["met"] == 5.0  # default for strength
    assert (await gym.post(f"{API}/exercises", json=body)).json()["id"] == exercise_id  # replay
    first = (await gym.get(f"{API}/exercises", params={"q": "жим"})).json()["items"][0]
    assert first["id"] == exercise_id

    library = await _ex(gym, "Deadlift")
    assert (await gym.patch(f"{API}/exercises/{library['id']}", json={"met": 1})).status_code == 404
    renamed = await gym.patch(f"{API}/exercises/{exercise_id}", json={"name_en": "Smith press"})
    assert renamed.json()["name_en"] == "Smith press"
    assert (await gym.post(f"{API}/exercises", json={"muscle_group": "chest"})).status_code == 422

    async with SessionLocal() as session:
        other = User(display_name="Other")
        session.add(other)
        await session.flush()
        session.add(Exercise(user_id=other.id, is_global=False, name_ru="Чужое упражнение", source="user"))
        await session.commit()
    assert not (await gym.get(f"{API}/exercises", params={"q": "чужое"})).json()["items"]


# --------------------------------------------------------------------------- #
# templates & plan
# --------------------------------------------------------------------------- #
async def _template(client, name="Грудь + спина", items=None) -> dict[str, Any]:
    bench = await _ex(client, "Barbell bench press")
    row = await _ex(client, "Bent-over barbell row")
    body = {
        "id": str(uuid.uuid4()),
        "name": name,
        "estimated_minutes": 60,
        "items": items
        or [
            {
                "exercise_id": bench["id"],
                "target_sets": 3,
                "target_reps": 8,
                "target_weight_kg": 80,
                "rest_seconds": 120,
            },
            {"exercise_id": row["id"], "target_sets": 3, "target_reps": 10, "target_weight_kg": 60},
        ],
    }
    response = await client.post(f"{API}/templates", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_templates_crud(gym: httpx.AsyncClient) -> None:
    template = await _template(gym)
    assert [item["exercise"]["name_en"] for item in template["items"]] == [
        "Barbell bench press",
        "Bent-over barbell row",
    ]
    assert template["items"][0]["target_weight_kg"] == 80 and template["items"][0]["rest_seconds"] == 120
    replay = await gym.post(f"{API}/templates", json={"id": template["id"], "name": "x", "items": []})
    assert replay.json()["name"] == "Грудь + спина"  # idempotent create

    squat = await _ex(gym, "Back squat")
    replaced = await gym.put(
        f"{API}/templates/{template['id']}",
        json={"name": "Ноги", "kind": "strength", "items": [{"exercise_id": squat["id"], "target_sets": 5}]},
    )
    assert replaced.status_code == 200 and [item["exercise"]["name_en"] for item in replaced.json()["items"]] == [
        "Back squat"
    ]
    unknown = await gym.post(f"{API}/templates", json={"name": "bad", "items": [{"exercise_id": str(uuid.uuid4())}]})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "exercise_not_found"
    assert len((await gym.get(f"{API}/templates")).json()) == 1
    assert (await gym.delete(f"{API}/templates/{template['id']}")).status_code == 200
    assert (await gym.get(f"{API}/templates")).json() == []


async def test_weekly_plan_recurring_override_and_week_start(gym: httpx.AsyncClient) -> None:
    template = await _template(gym)
    plan = (await gym.put(f"{API}/plan/weekdays/0", json={"template_id": template["id"]})).json()
    assert plan["recurring"][0]["template"]["name"] == "Грудь + спина"
    await gym.put(f"{API}/plan/weekdays/0", json={"template_id": template["id"], "planned_time": "07:30"})
    plan = (await gym.put(f"{API}/plan/weekdays/2", json={"is_rest_day": True})).json()
    assert plan["recurring"][0]["planned_time"] == "07:30:00" and plan["recurring"][2]["is_rest_day"] is True
    assert plan["recurring"][1] is None

    monday = date.fromisoformat(plan["start"])
    assert monday.weekday() == 0  # default week starts on Monday
    override = (await gym.put(f"{API}/plan/dates/{monday.isoformat()}", json={"is_rest_day": True})).json()
    first = override["days"][0]
    assert (first["source"], first["is_rest_day"], first["template"]) == ("date", True, None)
    assert override["days"][7 - 7 + 0]["date"] == monday.isoformat()
    cleared = (await gym.delete(f"{API}/plan/dates/{monday.isoformat()}")).json()
    assert cleared["days"][0]["source"] == "weekday" and cleared["days"][0]["template"]["id"] == template["id"]

    bad = await gym.put(f"{API}/plan/weekdays/1", json={"is_rest_day": True, "template_id": template["id"]})
    assert bad.status_code == 422
    assert (await gym.put(f"{API}/plan/weekdays/7", json={"is_rest_day": True})).status_code == 422
    assert (await gym.put(f"{API}/plan/weekdays/1", json={"template_id": str(uuid.uuid4())})).status_code == 404

    await gym.patch("/api/v1/settings", json={"week_start": 0})
    sunday_week = (await gym.get(f"{API}/plan")).json()
    assert date.fromisoformat(sunday_week["start"]).weekday() == 6
    assert sum(1 for day in sunday_week["days"] if day["is_today"]) == 1


# --------------------------------------------------------------------------- #
# live workout → finish → records
# --------------------------------------------------------------------------- #
async def test_workout_from_template_live_logging_and_finish(gym: httpx.AsyncClient) -> None:
    template = await _template(gym)
    workout = await _start(gym, template_id=template["id"])
    assert (workout["status"], workout["title"]) == ("in_progress", "Грудь + спина")
    # the plan shows up before anything is logged, with targets
    assert [item["exercise"]["name_en"] for item in workout["exercises"]] == [
        "Barbell bench press",
        "Bent-over barbell row",
    ]
    assert workout["exercises"][0]["target"]["target_reps"] == 8 and workout["exercises"][0]["sets"] == []
    replay = await gym.post(f"{API}/workouts", json={"id": workout["id"]})
    assert replay.status_code == 201 and replay.json()["id"] == workout["id"]
    second = await gym.post(f"{API}/workouts", json={"id": str(uuid.uuid4())})
    assert second.status_code == 409 and second.json()["error"]["code"] == "workout_in_progress"
    assert second.json()["error"]["details"]["workout_id"] == workout["id"]

    bench = workout["exercises"][0]["exercise"]["id"]
    warmup = await _set(gym, workout["id"], bench, set_index=1, is_warmup=True, reps=10, weight_kg=40)
    assert warmup.status_code == 200
    set_id = str(uuid.uuid4())
    await _set(gym, workout["id"], bench, set_id, set_index=2, reps=8, weight_kg=75)
    corrected = await _set(gym, workout["id"], bench, set_id, set_index=2, reps=8, weight_kg=80)  # same id → update
    body = corrected.json()
    assert body["total_sets"] == 1 and body["total_volume_kg"] == 640  # warm-up excluded
    sets = body["exercises"][0]["sets"]
    assert len(sets) == 2 and sets[1]["weight_kg"] == 80 and sets[1]["est_1rm_kg"] == pytest.approx(101.33, abs=0.01)
    assert sets[0]["est_1rm_kg"] is None  # warm-ups never count
    await _set(gym, workout["id"], bench, set_index=3, reps=7, weight_kg=80)
    assert (await _set(gym, workout["id"], bench, reps=0)).status_code == 422  # nothing measured

    done = await _finish(gym, workout["id"], duration_s=1800, rating=4)
    assert done["status"] == "completed" and done["duration_s"] == 1800 and done["rating"] == 4
    # 2 working + 1 warm-up set of bench (MET 6) → 3 sets, capped at 6 min each: (6-1)*80*1080/3600 = 120
    assert (done["kcal_burned"], done["kcal_method"]) == (120, "met")
    assert done["pr_count"] == 0 and done["records"] == []  # the first time is the baseline
    assert [item["exercise"]["name_en"] for item in done["exercises"]] == ["Barbell bench press"]  # done only
    assert (await gym.post(f"{API}/workouts/{workout['id']}/finish", json={})).json()["duration_s"] == 1800
    templates = (await gym.get(f"{API}/templates")).json()
    assert templates[0]["use_count"] == 1


async def test_records_previous_and_bests(gym: httpx.AsyncClient) -> None:
    bench = (await _ex(gym, "Barbell bench press"))["id"]
    pullup = (await _ex(gym, "Pull-up"))["id"]
    first = await _start(gym)
    await _set(gym, first["id"], bench, set_index=1, reps=8, weight_kg=80)
    await _set(gym, first["id"], bench, set_index=2, reps=8, weight_kg=80)
    await _set(gym, first["id"], pullup, sort_order=1, reps=8)
    await _finish(gym, first["id"])

    second = await _start(gym)
    view = (await _set(gym, second["id"], bench, set_index=1, reps=5, weight_kg=85)).json()
    entry = view["exercises"][0]
    assert entry["previous"] == [
        {"reps": 8, "weight_kg": 80.0, "duration_s": None, "distance_m": None, "is_warmup": False},
        {"reps": 8, "weight_kg": 80.0, "duration_s": None, "distance_m": None, "is_warmup": False},
    ]
    assert entry["bests"]["weight_kg"] == 80 and entry["bests"]["volume_kg"] == 1280
    await _set(gym, second["id"], pullup, sort_order=1, reps=10)
    done = await _finish(gym, second["id"])
    kinds = sorted((record["kind"], record["value"], record["previous_value"]) for record in done["records"])
    # 85×5 → heavier (weight) and e1RM 99.17 > 101.33? no: 85*(1+5/30) = 99.17 < 101.33 → no e1RM record
    assert kinds == [("reps", 10.0, 8.0), ("weight", 85.0, 80.0)]
    assert done["pr_count"] == 2
    pr_set = next(s for s in done["exercises"][0]["sets"] if s["weight_kg"] == 85)
    assert pr_set["is_pr"] is True
    assert (await _finish(gym, second["id"]))["pr_count"] == 2  # replayed finish: no duplicates

    history = (await gym.get(f"{API}/exercises/{bench}/history")).json()
    assert history["bests"]["weight_kg"] == 85 and history["bests"]["est_1rm_kg"] == pytest.approx(101.33, abs=0.01)
    assert [session["top_weight_kg"] for session in history["sessions"]] == [85, 80]
    assert [record["kind"] for record in history["records"]] == ["weight"]
    recent = (await gym.get(f"{API}/records")).json()
    assert {record["exercise_name_en"] for record in recent} == {"Barbell bench press", "Pull-up"}

    # deleting the record workout removes its records; bests come back from the sets
    assert (await gym.delete(f"{API}/workouts/{second['id']}")).status_code == 200
    assert (await gym.get(f"{API}/records")).json() == []
    history = (await gym.get(f"{API}/exercises/{bench}/history")).json()
    assert history["bests"]["weight_kg"] == 80 and len(history["sessions"]) == 1
    assert (await gym.get(f"{API}/workouts/{second['id']}")).status_code == 404


async def test_editing_a_finished_workout_recomputes(gym: httpx.AsyncClient) -> None:
    bench = (await _ex(gym, "Barbell bench press"))["id"]
    base = await _start(gym)
    await _set(gym, base["id"], bench, reps=5, weight_kg=100)
    await _finish(gym, base["id"], duration_s=600)
    later = await _start(gym)
    set_id = str(uuid.uuid4())
    await _set(gym, later["id"], bench, set_id, reps=5, weight_kg=90)
    done = await _finish(gym, later["id"], duration_s=600)
    assert done["pr_count"] == 0
    fixed = (await _set(gym, later["id"], bench, set_id, reps=5, weight_kg=105)).json()  # typo fixed afterwards
    assert fixed["pr_count"] >= 1 and {r["kind"] for r in fixed["records"]} >= {"weight"}

    manual = (await gym.patch(f"{API}/workouts/{later['id']}", json={"kcal_burned": 333})).json()
    assert (manual["kcal_burned"], manual["kcal_method"]) == (333, "manual")
    edited = (await _set(gym, later["id"], bench, set_id, reps=6, weight_kg=105)).json()
    assert edited["kcal_burned"] == 333  # a manual value survives edits
    auto = (await gym.patch(f"{API}/workouts/{later['id']}", json={"kcal_auto": True})).json()
    assert auto["kcal_method"] == "met" and auto["kcal_burned"] != 333


async def test_empty_finish_discard_and_validation(gym: httpx.AsyncClient) -> None:
    workout = await _start(gym, title="Пустая")
    empty = await gym.post(f"{API}/workouts/{workout['id']}/finish", json={})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "workout_empty"
    assert (await gym.delete(f"{API}/workouts/{workout['id']}")).status_code == 200  # discard
    assert (await gym.get(f"{API}/today")).json()["active"] is None
    unknown = await gym.put(
        f"{API}/workouts/{uuid.uuid4()}/sets/{uuid.uuid4()}", json={"exercise_id": str(uuid.uuid4()), "reps": 5}
    )
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "workout_not_found"
    started = await _start(gym)
    bad_exercise = await _set(gym, started["id"], str(uuid.uuid4()), reps=5)
    assert bad_exercise.status_code == 404 and bad_exercise.json()["error"]["code"] == "exercise_not_found"
    early = await gym.post(f"{API}/workouts/{started['id']}/finish", json={"ended_at": "2000-01-01T00:00:00+00:00"})
    assert early.status_code in (422,)


async def test_quick_cardio_kcal_distance_record_and_budget(gym: httpx.AsyncClient) -> None:
    run = await _ex(gym, "Outdoor running")
    day_before = (await gym.get("/api/v1/nutrition/day")).json()
    first = await gym.post(
        f"{API}/workouts/quick",
        json={"id": str(uuid.uuid4()), "exercise_id": run["id"], "duration_s": 1800, "distance_m": 5000},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    # net MET: (9.8 - 1) × 80 kg × 0.5 h = 352
    assert (body["status"], body["kcal_burned"], body["title"]) == ("completed", 352, "Бег на улице")
    second = await gym.post(
        f"{API}/workouts/quick",
        json={"id": str(uuid.uuid4()), "exercise_id": run["id"], "duration_s": 1500, "distance_m": 5500},
    )
    assert [(r["kind"], r["value"]) for r in second.json()["records"]] == [("distance", 5500.0)]

    today = (await gym.get(f"{API}/today")).json()
    assert today["burned_kcal"] == 352 + second.json()["kcal_burned"] and len(today["done"]) == 2
    burned = (await gym.get(f"{API}/burned", params={"days": 7})).json()
    assert len(burned["days"]) == 7 and burned["days"][-1]["kcal"] == today["burned_kcal"]
    assert burned["days"][-1]["workouts"] == 2 and burned["total_kcal"] == today["burned_kcal"]
    # burned calories are never food budget: the nutrition day is unchanged
    assert (await gym.get("/api/v1/nutrition/day")).json() == day_before


async def test_no_body_weight_means_no_kcal(gym: httpx.AsyncClient) -> None:
    async with SessionLocal() as session:
        from app.models import UserSettings

        row = (await session.execute(select(UserSettings))).scalar_one()
        row.weight_kg = None
        await session.execute(delete(BodyMetric))  # the settings change also logged a weigh-in
        await session.commit()
    run = await _ex(gym, "Walking")
    body = (
        await gym.post(
            f"{API}/workouts/quick", json={"id": str(uuid.uuid4()), "exercise_id": run["id"], "duration_s": 600}
        )
    ).json()
    assert (body["kcal_burned"], body["kcal_method"]) == (None, "none")


async def test_scoping_and_id_conflicts(gym: httpx.AsyncClient) -> None:
    bench = (await _ex(gym, "Barbell bench press"))["id"]
    mine = await _start(gym)
    set_id = str(uuid.uuid4())
    await _set(gym, mine["id"], bench, set_id, reps=5, weight_kg=60)
    await _finish(gym, mine["id"])
    async with SessionLocal() as session:
        other = User(display_name="Other")
        session.add(other)
        await session.flush()
        foreign = WorkoutLog(
            user_id=other.id,
            title="чужая",
            started_at=datetime.now(UTC),
            local_day=date.today(),
            status="in_progress",
        )
        session.add(foreign)
        await session.commit()
        foreign_id = str(foreign.id)
    assert (await gym.get(f"{API}/workouts/{foreign_id}")).status_code == 404
    assert (await gym.delete(f"{API}/workouts/{foreign_id}")).status_code == 404
    taken = await gym.post(f"{API}/workouts", json={"id": foreign_id})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "id_conflict"
    second = await _start(gym)
    moved = await _set(gym, second["id"], bench, set_id, reps=5, weight_kg=60)  # a set id of another workout
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "id_conflict"
    listing = (await gym.get(f"{API}/workouts")).json()["items"]
    assert {item["id"] for item in listing} == {mine["id"], second["id"]}


async def test_local_day_and_history_paging(gym: httpx.AsyncClient) -> None:
    bench = (await _ex(gym, "Barbell bench press"))["id"]
    # 23:30 in Bangkok (UTC+7) is 16:30 UTC — it belongs to the local day
    late = await _start(gym, started_at="2026-03-10T23:30:00+07:00")
    assert late["local_day"] == "2026-03-10"
    await _set(gym, late["id"], bench, reps=5, weight_kg=50)
    await _finish(gym, late["id"], duration_s=900)
    ids = [late["id"]]
    for offset in range(3):
        workout = await _start(gym, started_at=(datetime(2026, 3, 11, tzinfo=UTC) + timedelta(days=offset)).isoformat())
        await _set(gym, workout["id"], bench, reps=5, weight_kg=50)
        await _finish(gym, workout["id"], duration_s=900)
        ids.append(workout["id"])
    page = (await gym.get(f"{API}/workouts", params={"limit": 2})).json()
    assert page["has_more"] is True and [item["id"] for item in page["items"]] == ids[::-1][:2]
    rest = (await gym.get(f"{API}/workouts", params={"limit": 2, "before": page["items"][-1]["started_at"]})).json()
    assert [item["id"] for item in rest["items"]] == ids[::-1][2:] and rest["has_more"] is False
    assert page["items"][0]["exercise_names"] == ["Жим штанги лёжа"]


async def test_save_workout_as_template_and_today_plan(gym: httpx.AsyncClient) -> None:
    bench = (await _ex(gym, "Barbell bench press"))["id"]
    curl = (await _ex(gym, "Barbell curl"))["id"]
    workout = await _start(gym)
    await _set(gym, workout["id"], bench, set_index=1, reps=8, weight_kg=70)
    await _set(gym, workout["id"], bench, set_index=2, reps=6, weight_kg=75)
    await _set(gym, workout["id"], curl, sort_order=1, reps=12, weight_kg=25)
    await _finish(gym, workout["id"], duration_s=2400)
    template_id = str(uuid.uuid4())
    saved = await gym.post(f"{API}/workouts/{workout['id']}/save-as-template/{template_id}", params={"name": "Верх"})
    assert saved.status_code == 201, saved.text
    items = saved.json()["items"]
    assert [(i["exercise"]["name_en"], i["target_sets"], i["target_weight_kg"]) for i in items] == [
        ("Barbell bench press", 2, 75.0),
        ("Barbell curl", 1, 25.0),
    ]
    assert saved.json()["estimated_minutes"] == 40

    weekday = (await gym.get(f"{API}/today")).json()["plan"]["weekday"]
    await gym.put(f"{API}/plan/weekdays/{weekday}", json={"template_id": template_id})
    today = (await gym.get(f"{API}/today")).json()
    assert today["plan"]["template"]["name"] == "Верх" and today["plan"]["is_today"] is True
    assert len(today["done"]) == 1 and today["burned_kcal"] > 0
