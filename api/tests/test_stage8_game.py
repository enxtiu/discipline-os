"""Stage 8: the /gamification endpoints (shapes, ranges, auth, scoping), achievements
(unlock once, XP once, progress, secret masking, celebrate → seen), timezone day
boundaries, jobs (idempotent, safe to overlap), the seed and settings validation."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import func, select

from app.models import Achievement, DailyScore, Level, Quest, UserAchievement, UserQuest, XpEvent
from tests.stage6_helpers import other_user
from tests.stage8_helpers import (
    BANGKOK,
    NOON,
    TODAY,
    YESTERDAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    done_task,
    eat_day,
    journal,
    meal,
    owner_id,
    run,
    xp_rows,
)

pytestmark = pytest.mark.anyio
G = "/api/v1/gamification"


def now_today() -> date:
    return datetime.now(BANGKOK).date()


@pytest.fixture
async def player(authed: httpx.AsyncClient, db) -> dict:
    user_id = await owner_id(db)
    await configure(db, user_id)
    return {"client": authed, "user_id": user_id}


# --------------------------------------------------------------------------- #
# GET /gamification/today and /profile
# --------------------------------------------------------------------------- #
async def test_today_shape(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    await meal(db, user_id, now_today(), 700)
    response = await client.get(f"{G}/today")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"date", "score", "rings", "xp", "streaks", "quests", "celebrate"}
    assert body["date"] == now_today().isoformat()

    score = body["score"]
    assert score["provisional"] is True and 0 <= score["value"] <= 100
    assert [item["key"] for item in score["components"]] == [
        "kcal",
        "protein",
        "fiber",
        "water",
        "activity",
        "tasks",
        "sleep",
        "review",
    ]
    kcal = score["components"][0]
    assert kcal["detail"]["state"] == "progress" and kcal["detail"]["zone_low"] == 1840
    assert kcal["detail"]["floor"] == 1500 and kcal["detail"]["meals"] == 1
    assert sum(item["weight"] for item in score["components"] if not item["excluded"]) == pytest.approx(100, abs=0.5)

    assert [ring["key"] for ring in body["rings"]] == ["nutrition", "activity", "discipline"]
    xp = body["xp"]
    assert set(xp) == {"today", "total", "level"}
    assert set(xp["level"]) == {"level", "title_ru", "title_en", "min_xp", "next_min_xp", "progress"}
    assert xp["today"] >= 5 and xp["total"] >= xp["today"]

    kinds = [streak["kind"] for streak in body["streaks"]]
    assert kinds == ["overall", "meal_logging", "water", "steps", "tasks", "review", "workout"]
    meal_streak = body["streaks"][1]
    assert (meal_streak["current"], meal_streak["today_met"], meal_streak["unit"]) == (1, True, "day")
    workout = body["streaks"][-1]
    assert workout["unit"] == "week" and workout["period"] == {"done": 0, "required": 3}
    assert body["streaks"][0]["next_milestone"] == 3

    assert len(body["quests"]) == 5
    assert [quest["kind"] for quest in body["quests"]] == ["daily", "daily", "daily", "weekly", "weekly"]
    quest = body["quests"][0]
    assert {"id", "code", "title_ru", "title_en", "progress", "target", "unit", "status", "claimable"} <= set(quest)

    celebrate = body["celebrate"]
    assert [item["code"] for item in celebrate] == ["meals_1"]
    assert celebrate[0]["unlocked"] is True and celebrate[0]["seen"] is False
    seen = await client.post(f"{G}/achievements/seen", json={"ids": [celebrate[0]["id"]]})
    assert seen.status_code == 200 and seen.json() == {"ok": True}
    assert (await client.get(f"{G}/today")).json()["celebrate"] == []
    assert (await client.post(f"{G}/achievements/seen", json={"ids": []})).status_code == 422


async def test_profile_shape_progress_and_secret_masking(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    await meal(db, user_id, now_today(), 700)
    await done_task(db, user_id, datetime.now(UTC) - timedelta(minutes=1), priority=1)
    response = await client.get(f"{G}/profile")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"xp", "levels", "streaks", "achievements", "stats"}
    assert len(body["xp"]["last_30_days"]) == 30 and body["xp"]["last_30_days"][-1]["date"] == now_today().isoformat()
    assert body["xp"]["total"] == sum(day["xp"] for day in body["xp"]["last_30_days"])
    levels = body["levels"]
    assert [row["level"] for row in levels] == list(range(1, 31))
    assert (levels[0]["min_xp"], levels[1]["min_xp"], levels[-1]["min_xp"]) == (0, 100, 21870)
    assert body["stats"] == {
        "meals_logged_total": 1,
        "workouts_total": 0,
        "tasks_completed_total": 1,
        "tracked_minutes_total": 0,
        "journal_entries_total": 0,
    }

    achievements = body["achievements"]
    assert len(achievements) >= 30
    unlocked = [item for item in achievements if item["unlocked"]]
    assert {item["code"] for item in unlocked} == {"meals_1", "task_1"}
    assert achievements[: len(unlocked)] == unlocked  # unlocked first
    by_code = {item["code"]: item for item in achievements}
    assert by_code["meals_50"]["progress"] == {"value": 1.0, "target": 50.0}
    assert by_code["meals_1"]["progress"] == {"value": 1.0, "target": 1.0}
    masked = [item for item in achievements if item["masked"]]
    assert len(masked) == 4 and all(item["is_secret"] and not item["unlocked"] for item in masked)
    for item in masked:
        assert item["code"] == "secret" and item["name_en"] == "Secret achievement" and item["progress"] is None
        assert item["name_ru"] == "Секретное достижение" and item["icon"] == "lock"
    names = " ".join(item["name_en"] + " " + (item["description_en"] or "") for item in achievements)
    assert "perfect" not in names.lower()  # secret names never leak while locked


async def test_achievements_unlock_once_and_pay_once(player, db) -> None:
    user_id = player["user_id"]
    await meal(db, user_id, YESTERDAY, 700)
    for _ in range(3):
        await run(db, user_id, NOON)
    unlocked = (await db.scalars(select(UserAchievement).where(UserAchievement.user_id == user_id))).all()
    assert len(unlocked) == 1
    rows = await xp_rows(db, user_id)
    assert [key for key in rows if key.startswith("achievement:")] == ["achievement:meals_1"]
    assert rows["achievement:meals_1"].points == 10 and rows["achievement:meals_1"].local_day == TODAY


async def test_level_achievements_follow_xp(player, db) -> None:
    user_id = player["user_id"]
    for back in range(1, 15):  # two weeks of full days: plenty of XP
        await eat_day(db, user_id, TODAY - timedelta(days=back))
        await journal(db, user_id, TODAY - timedelta(days=back))
    snapshot = await run(db, user_id, NOON)
    assert snapshot.level.current.level >= 5
    codes = {row.code for row in snapshot.unlocked_now}
    assert "level_5" in codes and {"protein_7", "review_1", "streak_overall_7"} <= codes
    total = sum(row.points for row in (await xp_rows(db, user_id)).values())
    assert snapshot.xp_total == total


# --------------------------------------------------------------------------- #
# /xp and /scores
# --------------------------------------------------------------------------- #
async def test_xp_and_score_history(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    today = now_today()
    await meal(db, user_id, today - timedelta(days=3), 700)
    await meal(db, user_id, today, 700)
    first = (today - timedelta(days=5)).isoformat()
    xp = await client.get(f"{G}/xp", params={"from": first, "to": today.isoformat()})
    assert xp.status_code == 200, xp.text
    items = xp.json()["items"]
    assert [item["date"] for item in items] == [(today - timedelta(days=n)).isoformat() for n in range(5, -1, -1)]
    assert items[2]["xp"] == 5 and items[2]["events"] == [{"kind": "meal_logged", "points": 5, "reason": "meal_logged"}]
    assert items[0] == {"date": first, "xp": 0, "events": []}

    scores = await client.get(f"{G}/scores", params={"from": first, "to": today.isoformat()})
    assert scores.status_code == 200, scores.text
    days = scores.json()["items"]
    assert days[0]["score"] is None and days[0]["components"] == []  # before the first data
    assert days[2]["score"] is not None and days[2]["provisional"] is False  # frozen on first sight
    assert days[-1]["provisional"] is True and len(days[-1]["components"]) == 8

    for params, code in (
        ({"from": "2026-01-01", "to": "2027-01-02"}, "range_too_large"),
        ({"from": "2026-02-01", "to": "2026-01-01"}, "validation_error"),
        ({"from": "1990-01-01", "to": "1990-01-02"}, "validation_error"),
    ):
        for path in ("xp", "scores"):
            bad = await client.get(f"{G}/{path}", params=params)
            assert (bad.status_code, bad.json()["error"]["code"]) == (422, code), (path, params)
    assert (await client.get(f"{G}/xp", params={"from": "nope", "to": "2026-01-01"})).status_code == 422


async def test_scoped_api_tokens_are_refused(player, client) -> None:
    created = await player["client"].post("/api/v1/tokens", json={"name": "Shortcut", "scopes": ["read:all"]})
    assert created.status_code == 200, created.text
    headers = {"Authorization": f"Bearer {created.json()['token']}"}
    for method, path in (("get", "/today"), ("get", "/profile"), ("post", "/achievements/seen")):
        kwargs = {"json": {"ids": [str(player["user_id"])]}} if method == "post" else {}
        response = await getattr(client, method)(f"{G}{path}", headers=headers, **kwargs)
        assert response.status_code == 403, path


async def test_a_second_user_sees_nothing_of_the_first(player, db) -> None:
    user_id = player["user_id"]
    for back in range(0, 5):
        await eat_day(db, user_id, now_today() - timedelta(days=back))
    mine = (await player["client"].get(f"{G}/today")).json()
    assert mine["xp"]["total"] > 0 and mine["streaks"][1]["current"] == 5
    async with other_user() as stranger:
        theirs = (await stranger.get(f"{G}/today")).json()
        assert theirs["xp"] == {"today": 0, "total": 0, "level": theirs["xp"]["level"]}
        assert theirs["xp"]["level"]["level"] == 1
        assert all(streak["current"] == 0 for streak in theirs["streaks"])
        assert theirs["celebrate"] == []
        assert {quest["id"] for quest in theirs["quests"]}.isdisjoint({quest["id"] for quest in mine["quests"]})
        profile = (await stranger.get(f"{G}/profile")).json()
        assert not any(item["unlocked"] for item in profile["achievements"])
        assert profile["stats"]["meals_logged_total"] == 0
        assert all(
            item["xp"] == 0
            for item in (
                await stranger.get(
                    f"{G}/xp",
                    params={"from": (now_today() - timedelta(days=5)).isoformat(), "to": now_today().isoformat()},
                )
            ).json()["items"]
        )
        seen = await stranger.post(f"{G}/achievements/seen", json={"ids": [mine["celebrate"][0]["id"]]})
        assert seen.status_code == 200
    assert (await player["client"].get(f"{G}/today")).json()["celebrate"] != []  # untouched by the stranger


# --------------------------------------------------------------------------- #
# Day boundaries in the user's timezone
# --------------------------------------------------------------------------- #
async def test_day_boundaries_follow_the_users_timezone(owner, db) -> None:
    await configure(db, owner.id, timezone="Asia/Bangkok")
    late = datetime(2026, 7, 14, 21, 30, tzinfo=UTC)  # 04:30 on the 15th in Bangkok, 23:30 on the 14th in Berlin
    task = await done_task(db, owner.id, late, priority=1)
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    await run(db, owner.id, now, ("days",))
    assert (await xp_rows(db, owner.id))[f"task:{task}"].local_day == date(2026, 7, 15)
    await configure(db, owner.id, timezone="Europe/Berlin")
    await run(db, owner.id, now + timedelta(minutes=1), ("days",))
    assert (await xp_rows(db, owner.id))[f"task:{task}"].local_day == date(2026, 7, 14)


async def test_dst_day_boundaries_in_berlin(owner, db) -> None:
    """Clocks jump forward on 29 March 2026 at 01:00 UTC (CET +1 → CEST +2)."""
    await configure(db, owner.id, timezone="Europe/Berlin")
    before = await done_task(db, owner.id, datetime(2026, 3, 28, 23, 30, tzinfo=UTC), priority=2)  # 00:30 CET, 29th
    after = await done_task(db, owner.id, datetime(2026, 3, 29, 22, 30, tzinfo=UTC), priority=3)  # 00:30 CEST, 30th
    await run(db, owner.id, datetime(2026, 3, 30, 10, 0, tzinfo=UTC), ("days",))
    rows = await xp_rows(db, owner.id)
    assert rows[f"task:{before}"].local_day == date(2026, 3, 29)
    assert rows[f"task:{after}"].local_day == date(2026, 3, 30)
    scores = {row.local_day for row in (await db.scalars(select(DailyScore).where(DailyScore.user_id == owner.id)))}
    assert scores == {date(2026, 3, 29), date(2026, 3, 30)}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
JOBS = ("recompute_daily_scores", "sync_xp", "evaluate_achievements", "rotate_quests")


async def counts(db) -> dict[str, int]:
    out = {}
    for model in (XpEvent, DailyScore, UserQuest, UserAchievement):
        out[model.__tablename__] = int(await db.scalar(select(func.count()).select_from(model)) or 0)
    return out


async def test_jobs_are_registered_idempotent_and_safe_to_overlap(owner, db) -> None:
    from app.services.gamification import engine
    from app.services.hooks import registered_job_ids
    from app.services.scheduler import build_scheduler, run_job_now

    assert set(JOBS) <= set(registered_job_ids())
    jobs = {job.id: job for job in build_scheduler().get_jobs()}
    assert all(str(jobs[name].trigger) == "interval[0:15:00]" for name in JOBS)

    await configure(db, owner.id)
    for back in range(0, 4):
        await eat_day(db, owner.id, now_today() - timedelta(days=back))
        await journal(db, owner.id, now_today() - timedelta(days=back))
    for name in JOBS:
        assert await run_job_now(name) is True
    first = await counts(db)
    assert all(first.values())
    for name in reversed(JOBS):
        await run_job_now(name)
    assert await counts(db) == first

    from app.db import SessionLocal

    async def one() -> None:
        async with SessionLocal() as session:
            await engine.sync_user(session, owner.id)
            await session.commit()

    await asyncio.gather(one(), one(), one())  # overlapping runs take turns on the per-user lock
    assert await counts(db) == first


# --------------------------------------------------------------------------- #
# Seed and settings
# --------------------------------------------------------------------------- #
async def test_seed_is_registered_idempotent_and_complete(db) -> None:
    from app.seeds import get_seed
    from app.seeds.gamification import ACHIEVEMENTS, QUESTS, seed_gamification
    from app.services.gamification.achievements import TOTALS

    assert get_seed("gamification").run is seed_gamification
    first = await seed_gamification(db)
    await db.commit()
    second = await seed_gamification(db)
    await db.commit()
    assert first["created"] == 30 + len(ACHIEVEMENTS) + len(QUESTS) and second["created"] == 0
    assert await db.scalar(select(func.count()).select_from(Level)) == 30
    assert await db.scalar(select(func.count()).select_from(Achievement)) == len(ACHIEVEMENTS) >= 30
    assert await db.scalar(select(func.count()).select_from(Quest)) == len(QUESTS)

    categories = {spec.category for spec in ACHIEVEMENTS}
    assert categories == {"nutrition", "training", "activity", "planner", "review", "streaks", "levels", "special"}
    assert {spec.rarity for spec in ACHIEVEMENTS} == {"common", "rare", "epic", "legendary"}
    assert any(spec.secret for spec in ACHIEVEMENTS)
    known = set(TOTALS) | {
        "days_protein_target",
        "days_water_target",
        "days_steps_target",
        "perfect_days",
        "days_score_70_in_week",
        "level",
    }
    known |= {f"streak_longest_{kind}" for kind in ("overall", "meal_logging", "water", "steps", "tasks", "review")}
    known.add("streak_longest_workout")
    assert all(spec.metric in known and spec.gte > 0 for spec in ACHIEVEMENTS)
    text = " ".join(f"{spec.name_en} {spec.description_en} {spec.metric}" for spec in ACHIEVEMENTS).lower()
    for word in ("fast", "deficit", "lose", "burn", "skip", "restrict", "lighter"):
        assert word not in text  # no reward for low intake, fasting, loss speed or burned calories
    daily = [spec for spec in QUESTS if spec.kind == "daily"]
    weekly = [spec for spec in QUESTS if spec.kind == "weekly"]
    assert len(daily) >= 8 and len(weekly) >= 6


async def test_settings_validate_score_weights_and_the_score_uses_them(player, db) -> None:
    client = player["client"]
    bad = await client.patch("/api/v1/settings", json={"xp_weights": {"score_weights": {"kcal": 500}}})
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "validation_error")
    bad = await client.patch("/api/v1/settings", json={"xp_weights": {"xp_multipliers": {"nutrition": -1}}})
    assert bad.status_code == 422
    good = await client.patch(
        "/api/v1/settings",
        json={"xp_weights": {"score_weights": {"kcal": 50, "review": 0}, "xp_multipliers": {"bonus": 2}}},
    )
    assert good.status_code == 200, good.text
    components = {item["key"]: item for item in (await client.get(f"{G}/today")).json()["score"]["components"]}
    assert components["review"]["weight"] == 0
    present = [item["weight"] for item in components.values() if not item["excluded"]]
    assert components["kcal"]["weight"] == pytest.approx(50 * 100 / (50 + 15 + 5 + 10 + 15), abs=0.1)
    assert sum(present) == pytest.approx(100, abs=0.5)


async def test_frozen_scores_survive_a_change_of_weights(player, db) -> None:
    user_id = player["user_id"]
    old = TODAY - timedelta(days=4)
    await meal(db, user_id, old, 700)
    await run(db, user_id, NOON, ("days",))
    frozen = await db.scalar(select(DailyScore.score).where(DailyScore.user_id == user_id, DailyScore.local_day == old))
    await configure(db, user_id, xp_weights={"score_weights": {"kcal": 0}})
    await run(db, user_id, NOON + timedelta(hours=2), ("days",))
    db.expire_all()
    again = await db.scalar(select(DailyScore.score).where(DailyScore.user_id == user_id, DailyScore.local_day == old))
    assert again == frozen
    assert await db.scalar(sa.select(func.count()).select_from(DailyScore)) >= 2


async def test_today_costs_the_same_statements_for_any_history_length(player, db) -> None:
    """No per-day queries: a year of history costs what two days cost."""
    from sqlalchemy import event

    from app.db import engine

    client, user_id = player["client"], player["user_id"]
    statements: list[str] = []

    def count(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    async def measure() -> int:
        statements.clear()
        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            response = await client.get(f"{G}/today")
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        assert response.status_code == 200, response.text
        return len(statements)

    today = now_today()
    for back in range(0, 2):
        await eat_day(db, user_id, today - timedelta(days=back))
    await measure()  # first sight: seeds, rows, quests
    await measure()  # quest progress catches up
    small = await measure()
    for back in range(2, 120):
        await meal(db, user_id, today - timedelta(days=back), 700)
        await journal(db, user_id, today - timedelta(days=back))
    backfill = await measure()  # 118 older days scored and frozen at once
    large = await measure()
    print(f"\ngamification/today statements: steady {small} → {large}, backfill of 118 days {backfill}")
    assert large == small
    assert backfill < 100  # extra statements follow the achievements unlocked, never the days
