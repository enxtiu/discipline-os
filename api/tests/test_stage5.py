"""Stage 5: health ingest (iOS Shortcut), manual steps, weight and sleep."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

pytestmark = pytest.mark.anyio
TZ = ZoneInfo("Asia/Bangkok")  # the device fixture pairs with this timezone


def today() -> date:
    return datetime.now(TZ).date()


async def _token(authed: httpx.AsyncClient, *scopes: str) -> str:
    response = await authed.post("/api/v1/tokens", json={"name": "Shortcut", "scopes": list(scopes)})
    assert response.status_code in (200, 201), response.text
    return response.json()["token"]


async def _ingest(client: httpx.AsyncClient, token: str, body: Any, path: str = "/api/v1/ingest/health"):
    return await client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})


async def test_shortcut_ingest_upserts_the_day(authed: httpx.AsyncClient, client: httpx.AsyncClient) -> None:
    token = await _token(authed, "ingest:health")
    device = authed.headers.pop("Authorization")  # the Shortcut has only its token
    day = today().isoformat()
    first = await _ingest(client, token, {"date": day, "steps": 8342, "active_kcal": 512, "distance_m": 6210})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True
    assert (body["day"]["steps"], body["day"]["active_kcal"], body["day"]["source"]) == (8342, 512.0, "shortcut")
    assert body["day"]["steps_kcal"] == 512.0 and body["day"]["steps_kcal_estimated"] is False

    again = await _ingest(client, token, {"date": day, "steps": 9100})  # 22:00 run
    assert again.json()["created"] is False
    assert (again.json()["day"]["steps"], again.json()["day"]["active_kcal"]) == (9100, 512.0)  # unsent fields stay

    # Shortcut numbers arrive as text, with spaces or a decimal comma
    lenient = await _ingest(
        client, token, {"date": day, "steps": "9 400", "active_kcal": "530,5", "distance_m": 7012.6}
    )
    assert lenient.status_code == 200, lenient.text
    assert (
        lenient.json()["day"]["steps"],
        lenient.json()["day"]["active_kcal"],
        lenient.json()["day"]["distance_m"],
    ) == (
        9400,
        530.5,
        7013,
    )

    # the token can do nothing else
    assert (await client.get("/api/v1/health/today", headers={"Authorization": f"Bearer {token}"})).status_code == 403
    weight = await _ingest(client, token, {"weight_kg": 80}, "/api/v1/ingest/weight")
    assert weight.status_code == 403
    authed.headers["Authorization"] = device
    assert (await authed.get("/api/v1/health/today")).json()["last_sync_at"] is not None


async def test_ingest_days_and_validation(authed: httpx.AsyncClient) -> None:
    token = await _token(authed, "ingest:health")
    headers = {"Authorization": f"Bearer {token}"}
    no_date = await authed.post("/api/v1/ingest/health", json={"steps": 100}, headers=headers)
    assert no_date.json()["day"]["date"] == today().isoformat()  # default: the user's today
    late_utc = datetime.combine(today() - timedelta(days=1), datetime.min.time()).replace(
        hour=20, tzinfo=ZoneInfo("UTC")
    )
    converted = await authed.post(
        "/api/v1/ingest/health", json={"date": late_utc.isoformat(), "steps": 200}, headers=headers
    )
    assert converted.json()["day"]["date"] == today().isoformat()  # 20:00 UTC = 03:00 next day in Bangkok
    old = await authed.post("/api/v1/ingest/health", json={"date": "2020-01-01", "steps": 1}, headers=headers)
    assert old.status_code == 422 and old.json()["error"]["code"] == "health_day_out_of_range"
    empty = await authed.post("/api/v1/ingest/health", json={"date": today().isoformat()}, headers=headers)
    assert empty.status_code == 422
    junk = await authed.post("/api/v1/ingest/health", json={"steps": "many"}, headers=headers)
    assert junk.status_code == 422
    too_many = await authed.post("/api/v1/ingest/health", json={"steps": 250_000}, headers=headers)
    assert too_many.status_code == 422

    batch = {"days": [{"date": (today() - timedelta(days=n)).isoformat(), "steps": 1000 * n} for n in range(1, 4)]}
    first = await authed.post("/api/v1/ingest/health/batch", json=batch, headers=headers)
    assert first.status_code == 200 and (first.json()["created"], first.json()["updated"]) == (3, 0)
    second = await authed.post("/api/v1/ingest/health/batch", json=batch, headers=headers)
    assert (second.json()["created"], second.json()["updated"]) == (0, 3)


async def test_manual_steps_estimate_and_series(authed: httpx.AsyncClient) -> None:
    await authed.patch("/api/v1/settings", json={"weight_kg": 80, "height_cm": 180, "target_steps": 8000})
    nutrition_before = (await authed.get("/api/v1/nutrition/day")).json()
    day = today().isoformat()
    put = await authed.put(f"/api/v1/health/steps/{day}", json={"steps": 10_000})
    assert put.status_code == 200, put.text
    body = put.json()
    # stride 180 cm × 0.415 = 0.747 m → 7.47 km × 0.5 kcal/kg/km × 80 kg ≈ 299
    assert (body["source"], body["steps_kcal"], body["steps_kcal_estimated"]) == ("manual", 299, True)
    await authed.put(f"/api/v1/health/steps/{(today() - timedelta(days=2)).isoformat()}", json={"steps": 4000})
    series = (await authed.get("/api/v1/health/steps", params={"days": 7})).json()
    assert len(series["days"]) == 7 and series["days"][-1]["steps"] == 10_000 and series["days"][0]["steps"] == 0
    assert (series["target_steps"], series["days_on_target"], series["average_steps"]) == (8000, 1, 2000)
    today_view = (await authed.get("/api/v1/health/today")).json()
    assert today_view["day"]["steps"] == 10_000 and today_view["last_sync_at"] is None  # manual is not a sync
    # steps and their calories never touch the food budget
    assert (await authed.get("/api/v1/nutrition/day")).json() == nutrition_before


async def test_weight_updates_profile_and_trend(authed: httpx.AsyncClient) -> None:
    d0 = today()
    await authed.put(f"/api/v1/health/weight/{(d0 - timedelta(days=31)).isoformat()}", json={"weight_kg": 84})
    await authed.put(f"/api/v1/health/weight/{(d0 - timedelta(days=2)).isoformat()}", json={"weight_kg": 81.4})
    latest = await authed.put(f"/api/v1/health/weight/{d0.isoformat()}", json={"weight_kg": 80.6, "body_fat_pct": 18})
    assert latest.status_code == 200 and latest.json()["weight_kg"] == 80.6
    settings = (await authed.get("/api/v1/settings")).json()["settings"]
    assert settings["weight_kg"] == 80.6  # the newest weigh-in is the profile weight
    series = (await authed.get("/api/v1/health/weight")).json()
    assert [row["weight_kg"] for row in series["items"]] == [84.0, 81.4, 80.6]
    assert series["trend_kg"] == 81.0 and series["change_30d_kg"] == pytest.approx(-3.4)
    today_view = (await authed.get("/api/v1/health/today")).json()
    assert today_view["weight"]["weight_kg"] == 80.6 and today_view["weight_change_7d_kg"] is None
    again = await authed.put(f"/api/v1/health/weight/{d0.isoformat()}", json={"weight_kg": 80.2})  # one per day
    assert again.json()["weight_kg"] == 80.2 and len((await authed.get("/api/v1/health/weight")).json()["items"]) == 3
    await authed.put(f"/api/v1/health/weight/{(d0 - timedelta(days=9)).isoformat()}", json={"weight_kg": 82})
    assert (await authed.get("/api/v1/health/today")).json()["weight_change_7d_kg"] == pytest.approx(-1.8)
    assert (await authed.delete(f"/api/v1/health/weight/{d0.isoformat()}")).status_code == 200
    assert (await authed.get("/api/v1/settings")).json()["settings"]["weight_kg"] == 81.4
    missing = await authed.delete(f"/api/v1/health/weight/{d0.isoformat()}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "weight_not_found"
    assert (await authed.put(f"/api/v1/health/weight/{d0.isoformat()}", json={"weight_kg": 10})).status_code == 422


async def test_sleep_manual_and_ingest(authed: httpx.AsyncClient) -> None:
    d0 = today()
    night = await authed.put(
        f"/api/v1/health/sleep/{d0.isoformat()}", json={"bed_time": "23:30", "wake_time": "07:15", "quality": 4}
    )
    assert night.status_code == 200, night.text
    body = night.json()
    assert body["duration_min"] == 465 and body["date"] == d0.isoformat()
    bed_local = datetime.fromisoformat(body["bed_at"]).astimezone(TZ)
    assert (bed_local.date(), bed_local.hour, bed_local.minute) == (d0 - timedelta(days=1), 23, 30)
    after_midnight = await authed.put(
        f"/api/v1/health/sleep/{(d0 - timedelta(days=1)).isoformat()}", json={"bed_time": "01:00", "wake_time": "08:00"}
    )
    assert after_midnight.json()["duration_min"] == 420
    zero = await authed.put(f"/api/v1/health/sleep/{d0.isoformat()}", json={"bed_time": "07:00", "wake_time": "07:00"})
    assert zero.status_code == 422
    series = (await authed.get("/api/v1/health/sleep", params={"days": 7})).json()
    assert [row["duration_min"] for row in series["items"]] == [465, 420] and series["average_min"] == 442
    assert (await authed.get("/api/v1/health/today")).json()["sleep"]["duration_min"] == 465

    token = await _token(authed, "ingest:sleep", "ingest:weight")
    headers = {"Authorization": f"Bearer {token}"}
    wake = datetime.combine(d0 - timedelta(days=2), datetime.min.time()).replace(hour=6, tzinfo=TZ)
    pushed = await authed.post(
        "/api/v1/ingest/sleep",
        json={"bed_at": (wake - timedelta(hours=7, minutes=30)).isoformat(), "wake_at": wake.isoformat()},
        headers=headers,
    )
    assert pushed.status_code == 200 and pushed.json()["date"] == (d0 - timedelta(days=2)).isoformat()
    assert pushed.json()["duration_min"] == 450
    scale = await authed.post("/api/v1/ingest/weight", json={"weight_kg": "79,9"}, headers=headers)
    assert scale.status_code == 200 and scale.json()["weight_kg"] == 79.9 and scale.json()["source"] == "shortcut"
    assert (await authed.delete(f"/api/v1/health/sleep/{d0.isoformat()}")).status_code == 200
    assert (await authed.delete(f"/api/v1/health/sleep/{d0.isoformat()}")).status_code == 404
