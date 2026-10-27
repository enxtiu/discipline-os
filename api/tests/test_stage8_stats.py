"""Stage 8: the /stats/* series — periods, every day present, null vs zero per field,
values on hand-made data (the calorie zone and floor exactly as the score, burned
calories kept out of nutrition), a DST change and timezones whose day differs from
UTC's, an empty account, scoping, auth and validation, and the cost of a year
(a constant number of statements; < 300 ms when the shared machine is not busy)."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.api.v1.presenters import progress_stats_out
from app.models import (
    Area,
    BodyMetric,
    DailyScore,
    Equipment,
    Exercise,
    HealthDaily,
    HealthSource,
    JournalEntry,
    MealLog,
    MuscleGroup,
    PersonalRecord,
    Project,
    ProjectStatus,
    SleepLog,
    Streak,
    StreakKind,
    Task,
    TaskStatus,
    TimeEntry,
    UserSettings,
    WaterLog,
    WorkoutLog,
    WorkoutSet,
    WorkoutStatus,
    XpEvent,
    XpKind,
)
from app.services import stats
from app.services.gamification import score
from app.services.gamification.context import context_for
from tests.stage6_helpers import other_user
from tests.stage8_helpers import (
    BANGKOK,
    NOON,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    done_task,
    eat_day,
    exercise,
    journal,
    local,
    meal,
    owner_id,
    plan,
    record,
    run,
    sleep,
    steps,
    tracked,
    water,
    weigh_in,
    workout,
)

pytestmark = pytest.mark.anyio
S = "/api/v1/stats"
SERIES = ("nutrition", "activity", "body", "planner", "progress")
WEEK = date(2026, 7, 13)  # a Monday, long past: every day of it is finished


@pytest.fixture
async def player(authed: httpx.AsyncClient, db) -> dict:
    user_id = await owner_id(db)
    await configure(db, user_id)
    return {"client": authed, "user_id": user_id}


async def get(client: httpx.AsyncClient, series: str, **params) -> dict:
    response = await client.get(f"{S}/{series}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_date(items: list[dict]) -> dict[str, dict]:
    return {item["date"]: item for item in items}


def iso(day: date) -> str:
    return day.isoformat()


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #
def test_periods_are_calendar_periods_around_the_anchor() -> None:
    today = date(2028, 2, 10)
    wed = date(2028, 2, 9)  # a Wednesday

    def bounds(period: str, anchor: date, week_start: int = 1) -> tuple[date, date]:
        span = stats.span_for(period, anchor, week_start, today)
        assert span.today == today and span.days[0] == span.first and span.days[-1] == span.last
        return span.first, span.last

    assert bounds("week", wed) == (date(2028, 2, 7), date(2028, 2, 13))  # Monday
    assert bounds("week", wed, 0) == (date(2028, 2, 6), date(2028, 2, 12))  # Sunday
    assert bounds("week", wed, 6) == (date(2028, 2, 5), date(2028, 2, 11))  # Saturday
    assert bounds("month", wed) == (date(2028, 2, 1), date(2028, 2, 29))  # a leap year
    assert bounds("quarter", date(2026, 11, 30)) == (date(2026, 10, 1), date(2026, 12, 31))
    assert bounds("quarter", date(2026, 1, 1)) == (date(2026, 1, 1), date(2026, 3, 31))
    assert bounds("year", wed) == (date(2028, 1, 1), date(2028, 12, 31))
    assert len(stats.span_for("year", wed, 1, today).days) == 366
    assert bounds("month", date(2100, 12, 31)) == (date(2100, 12, 1), date(2100, 12, 31))
    assert all(len(stats.span_for(period, wed, 1, today).days) <= 400 for period in stats.PERIODS)


async def test_every_series_has_every_day_and_the_span(player) -> None:
    client = player["client"]
    for series in SERIES:
        for period, size in (("week", 7), ("month", 31), ("quarter", 92), ("year", 365)):
            body = await get(client, series, period=period, date="2026-07-15")
            assert body["period"] == period and body["today"] == datetime.now(BANGKOK).date().isoformat()
            days = body.get("days") or body.get("weight") or body.get("scores")
            assert len(days) == size, (series, period)
            assert days[0]["date"] == body["from"] and days[-1]["date"] == body["to"]
            assert [item["date"] for item in days] == [
                iso(date.fromisoformat(body["from"]) + timedelta(days=offset)) for offset in range(size)
            ]
    week = await get(client, "nutrition", date="2026-07-15")  # default period: week
    assert (week["period"], week["from"], week["to"]) == ("week", "2026-07-13", "2026-07-19")


async def test_auth_validation_and_api_tokens(player, client) -> None:
    authed = player["client"]
    for params in ({"period": "decade"}, {"date": "9999-01-01"}, {"date": "nope"}):
        response = await authed.get(f"{S}/nutrition", params=params)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"]["code"] == "validation_error"
    created = await authed.post("/api/v1/tokens", json={"name": "Shortcut", "scopes": ["read:all"]})
    headers = {"Authorization": f"Bearer {created.json()['token']}"}
    from app.main import app

    anonymous = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    for series in SERIES:
        refused = await client.get(f"{S}/{series}", headers=headers)
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "device_only"), series
        assert (await anonymous.get(f"{S}/{series}")).status_code == 401
    await anonymous.aclose()


# --------------------------------------------------------------------------- #
# Nutrition
# --------------------------------------------------------------------------- #
async def test_nutrition_values_zone_floor_and_null_days(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    mon, tue, wed, thu, fri = (WEEK + timedelta(days=offset) for offset in range(5))
    await eat_day(db, user_id, mon, 2000, protein=150, fiber=30)  # inside [1840, 2120]
    await water(db, user_id, mon, 2500)
    await workout(db, user_id, mon, kcal_burned=600)  # burned: never nutrition
    await steps(db, user_id, mon, 12000, active_kcal=450)
    await meal(db, user_id, tue, 1000, protein=160, fiber=5)  # below the floor: protein never counts
    await water(db, user_id, wed, 500)  # water only — no meal
    await meal(db, user_id, thu, 900, deleted=True)  # a deleted meal is no meal
    await meal(db, user_id, fri, 1150, protein=40)
    await meal(db, user_id, fri, 1150, protein=40)  # 2300: above the zone

    body = await get(client, "nutrition", period="week", date=iso(WEEK + timedelta(days=2)))
    assert (body["from"], body["to"]) == ("2026-07-13", "2026-07-19")
    assert (body["target_kcal"], body["floor"], body["zone"]) == (2000, 1500, {"low": 1840, "high": 2120})
    assert (body["target_protein_g"], body["target_fiber_g"], body["target_water_ml"]) == (150, 30, 2500)
    days = by_date(body["days"])
    monday = days[iso(mon)]
    assert monday["kcal"] == pytest.approx(2000.0, abs=0.02)  # three thirds, stored to the cent
    assert {
        key: monday[key] for key in ("protein_g", "fat_g", "carbs_g", "fiber_g", "water_ml", "meals", "in_zone")
    } == {
        "protein_g": 150.0,
        "fat_g": 0.0,
        "carbs_g": 0.0,
        "fiber_g": 30.0,
        "water_ml": 2500,
        "meals": 3,
        "in_zone": True,
    }
    assert (days[iso(tue)]["kcal"], days[iso(tue)]["in_zone"], days[iso(tue)]["water_ml"]) == (1000.0, False, None)
    for empty in (wed, thu):  # no meal: nulls, not zeros
        assert {key: days[iso(empty)][key] for key in ("kcal", "protein_g", "fiber_g", "meals", "in_zone")} == {
            "kcal": None,
            "protein_g": None,
            "fiber_g": None,
            "meals": 0,
            "in_zone": None,
        }
    assert days[iso(wed)]["water_ml"] == 500 and days[iso(thu)]["water_ml"] is None
    assert (days[iso(fri)]["kcal"], days[iso(fri)]["meals"], days[iso(fri)]["in_zone"]) == (2300.0, 2, False)
    assert body["adherence"] == {"zone_days": 1, "protein_days": 1, "water_days": 1, "logged_days": 3}
    averages = body["averages"]
    assert averages["days"] == 3 and averages["kcal"] == pytest.approx((2000 + 1000 + 2300) / 3, abs=0.05)
    assert averages["meals"] == pytest.approx(2.0) and averages["protein_g"] == pytest.approx(
        (150 + 160 + 80) / 3, abs=0.05
    )
    assert (averages["water_days"], averages["water_ml"]) == (2, 1500.0)
    assert "burn" not in str(body) and "active" not in str(body)  # burned calories are not part of nutrition


async def test_the_zone_never_starts_below_the_floor_and_is_null_without_a_target(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    await configure(db, user_id, target_kcal=1300)  # 0.92 × 1300 = 1196 < the floor 1500
    await meal(db, user_id, WEEK, 1450)
    body = await get(client, "nutrition", date=iso(WEEK))
    assert body["zone"] == {"low": 1500, "high": 1500} and body["floor"] == 1500
    assert by_date(body["days"])[iso(WEEK)]["in_zone"] is False  # below the floor is never "in zone"
    await configure(db, user_id, safe_floor_kcal=1300, target_kcal=1600)
    body = await get(client, "nutrition", date=iso(WEEK))
    assert (body["floor"], body["zone"]) == (1300, {"low": 1472, "high": 1696})
    assert by_date(body["days"])[iso(WEEK)]["in_zone"] is False
    await configure(db, user_id, target_kcal=None, weight_kg=None)  # no target can be computed
    body = await get(client, "nutrition", date=iso(WEEK))
    assert body["target_kcal"] is None and body["zone"] is None
    assert by_date(body["days"])[iso(WEEK)]["in_zone"] is None
    assert body["adherence"]["zone_days"] == 0


async def test_today_is_in_progress_and_the_future_is_empty(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    today = datetime.now(BANGKOK).date()
    await eat_day(db, user_id, today, 2000)  # inside the zone, but the day is not over
    await eat_day(db, user_id, today - timedelta(days=1), 2000)
    body = await get(client, "nutrition", period="year")
    days = by_date(body["days"])
    assert days[iso(today)]["kcal"] == pytest.approx(2000.0, abs=0.02)  # three thirds, stored to the cent
    assert days[iso(today)]["in_zone"] is None
    assert body["adherence"]["logged_days"] >= 1
    if today.month != 1 or today.day != 1:
        yesterday = days[iso(today - timedelta(days=1))]
        assert yesterday["in_zone"] is True
        assert body["averages"]["days"] == 1  # today's partial day never averages in
    for item in body["days"]:
        if item["date"] > iso(today):
            assert (item["kcal"], item["meals"], item["in_zone"], item["water_ml"]) == (None, 0, None, None)


# --------------------------------------------------------------------------- #
# Activity
# --------------------------------------------------------------------------- #
async def _strength_exercise(db, name: str, muscle: MuscleGroup) -> uuid.UUID:
    row = Exercise(name_en=f"{name} {uuid.uuid4().hex[:6]}", muscle_group=muscle, equipment=Equipment.BARBELL)
    db.add(row)
    await db.commit()
    return row.id


async def _set(db, user_id, workout_id, exercise_id, reps, kg, *, warmup: bool = False) -> None:
    db.add(
        WorkoutSet(
            user_id=user_id,
            workout_log_id=workout_id,
            exercise_id=exercise_id,
            reps=reps,
            weight_kg=Decimal(str(kg)) if kg is not None else None,
            is_warmup=warmup,
        )
    )
    await db.commit()


async def test_activity_days_weeks_volume_and_records(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    mon, wed = WEEK, WEEK + timedelta(days=2)
    await steps(db, user_id, mon, 9000, active_kcal=420.25)
    await steps(db, user_id, wed, 3000)  # synced without active energy
    first = await workout(db, user_id, mon, kcal_burned=350.5)
    second = await workout(db, user_id, mon, hh=7, kcal_burned=None)
    await workout(db, user_id, wed, status=WorkoutStatus.IN_PROGRESS)  # not completed: not counted
    bench = await _strength_exercise(db, "Bench", MuscleGroup.CHEST)
    squat = await _strength_exercise(db, "Squat", MuscleGroup.QUADS)
    await _set(db, user_id, first, bench, 10, 50, warmup=True)  # warm-ups are no volume
    await _set(db, user_id, first, bench, 8, 80)
    await _set(db, user_id, first, bench, 6, 85)
    await _set(db, user_id, second, squat, 5, 120)
    await db.execute(sa.update(WorkoutLog).where(WorkoutLog.id == first).values(total_volume_kg=Decimal("1150")))
    await db.execute(sa.update(WorkoutLog).where(WorkoutLog.id == second).values(total_volume_kg=Decimal("600")))
    await db.commit()
    await record(db, user_id, first, bench, "weight", mon)
    await record(db, user_id, first, bench, "weight", mon)  # re-detected twice: one record
    await record(db, user_id, second, squat, "est_1rm", mon)
    old = await workout(db, user_id, WEEK - timedelta(days=30))
    await record(db, user_id, old, squat, "weight", WEEK - timedelta(days=30))  # another period

    body = await get(client, "activity", date=iso(wed))
    days = by_date(body["days"])
    assert body["target_steps"] == 8000
    assert days[iso(mon)] == {
        "date": iso(mon),
        "steps": 9000,
        "on_target": True,
        "workouts": 2,
        "workout_kcal": 350.5,
        "active_kcal": 420.2,
    }
    assert days[iso(wed)] == {
        "date": iso(wed),
        "steps": 3000,
        "on_target": False,
        "workouts": 0,
        "workout_kcal": 0.0,
        "active_kcal": None,
    }
    tue = days[iso(mon + timedelta(days=1))]
    assert (tue["steps"], tue["on_target"], tue["workouts"], tue["workout_kcal"], tue["active_kcal"]) == (
        None,
        None,
        0,
        0.0,
        None,
    )
    assert body["weeks"] == [{"week_start": iso(mon), "workouts": 2, "planned": 0, "required": 3, "volume_kg": 1750.0}]
    assert body["muscle_volume"] == [
        {"muscle": "chest", "sets": 2, "volume_kg": 1150.0},
        {"muscle": "quads", "sets": 1, "volume_kg": 600.0},
    ]
    assert body["records_total"] == 2 and len(body["records"]) == 2
    assert {(item["kind"], item["local_day"]) for item in body["records"]} == {
        ("weight", iso(mon)),
        ("est_1rm", iso(mon)),
    }
    assert body["records"][0]["exercise_name_en"].startswith(("Bench", "Squat"))

    # the plan: Mon/Wed/Fri training, Sunday rest → planned 3, required 3; a month shows whole weeks
    for weekday in (0, 2, 4):
        await plan(db, user_id, weekday)
    await plan(db, user_id, 6, rest=True)
    month = await get(client, "activity", period="month", date=iso(wed))
    weeks = month["weeks"]
    assert weeks[0]["week_start"] == "2026-06-29" and weeks[-1]["week_start"] == "2026-07-27"  # whole weeks
    assert all((week["planned"], week["required"]) == (3, 3) for week in weeks)
    assert by_date([{"date": w["week_start"], **w} for w in weeks])[iso(mon)]["workouts"] == 2


# --------------------------------------------------------------------------- #
# Body
# --------------------------------------------------------------------------- #
async def test_body_weight_mean_change_and_sleep(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    mon = WEEK
    await weigh_in(db, user_id, mon - timedelta(days=40), 84.0)  # 30–45 days before the newest
    await weigh_in(db, user_id, mon - timedelta(days=3), 82.0)
    await weigh_in(db, user_id, mon, 81.0)
    await weigh_in(db, user_id, mon + timedelta(days=2), 80.0)
    await sleep(db, user_id, mon, 450)
    await sleep(db, user_id, mon + timedelta(days=1), 401)
    body = await get(client, "body", date=iso(mon))
    weight = by_date(body["weight"])
    assert weight[iso(mon)] == {"date": iso(mon), "kg": 81.0, "avg7": 81.5}  # 82 (Fri before) and 81
    assert weight[iso(mon + timedelta(days=1))] == {"date": iso(mon + timedelta(days=1)), "kg": None, "avg7": 81.5}
    assert weight[iso(mon + timedelta(days=2))]["avg7"] == pytest.approx(81.0)
    assert weight[iso(mon + timedelta(days=6))]["avg7"] == pytest.approx(80.5)  # the 82 left the window
    assert body["change_30d"] == pytest.approx(80.0 - 84.0)  # the period's newest minus 42 days before
    sleep_days = by_date(body["sleep"])
    assert sleep_days[iso(mon)]["minutes"] == 450 and sleep_days[iso(mon + timedelta(days=2))]["minutes"] is None
    assert body["sleep_avg"] == 426  # (450 + 401) / 2, rounded

    previous = await get(client, "body", date=iso(mon - timedelta(days=60)))
    assert previous["change_30d"] is None and previous["sleep_avg"] is None
    assert all(point["kg"] is None and point["avg7"] is None for point in previous["weight"])


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
async def _task(db, user_id, *, created: datetime, done: datetime | None = None, **fields) -> uuid.UUID:
    row = Task(
        user_id=user_id,
        title=fields.pop("title", "задача"),
        status=TaskStatus.DONE if done else fields.pop("status", TaskStatus.OPEN),
        completed_at=done,
        **fields,
    )
    row.created_at = created
    db.add(row)
    await db.commit()
    return row.id


async def _entry(db, user_id, day: date, minutes: int, *, area_id=None, running: bool = False) -> None:
    start = local(day, 9) if not running else datetime.now(UTC) - timedelta(minutes=minutes)
    db.add(
        TimeEntry(
            user_id=user_id,
            area_id=area_id,
            started_at=start,
            ended_at=None if running else start + timedelta(minutes=minutes),
            duration_s=None if running else minutes * 60,
            local_day=day,
        )
    )
    await db.commit()


async def test_planner_days_areas_and_projects(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    mon, tue = WEEK, WEEK + timedelta(days=1)
    # names apart from the owner's default areas (Здоровье, Работа, Дом… are created with the account)
    health = Area(user_id=user_id, name="Спорт", weekly_target_minutes=120, sort_order=1)
    work = Area(user_id=user_id, name="Учёба", sort_order=2)
    idle = Area(user_id=user_id, name="Сад", weekly_target_minutes=60, sort_order=3)
    archived = Area(user_id=user_id, name="Архив", weekly_target_minutes=60, is_archived=True)
    db.add_all([health, work, idle, archived])
    await db.commit()
    await _entry(db, user_id, mon, 50, area_id=health.id)
    await _entry(db, user_id, tue, 25, area_id=work.id)
    await _entry(db, user_id, tue, 10)  # no area

    launch = Project(user_id=user_id, name="Запуск", status=ProjectStatus.ACTIVE, sort_order=1)
    shipped = Project(user_id=user_id, name="Готово", status=ProjectStatus.COMPLETED)
    dormant = Project(user_id=user_id, name="Старое", status=ProjectStatus.COMPLETED)
    db.add_all([launch, shipped, dormant])
    await db.commit()
    # 03:00 local on Monday = 20:00 UTC on Sunday: the user's day counts
    await _task(db, user_id, created=local(mon, 3), done=local(mon, 3), project_id=launch.id)
    await _task(db, user_id, created=local(mon, 10), project_id=launch.id)  # open
    await _task(db, user_id, created=local(mon, 11), status=TaskStatus.CANCELLED, project_id=launch.id)
    parent = await _task(db, user_id, created=local(tue, 8), done=local(tue, 23, 59), project_id=shipped.id)
    await _task(db, user_id, created=local(tue, 8), done=local(tue, 12), parent_id=parent, project_id=shipped.id)
    await _task(
        db,
        user_id,
        created=local(mon - timedelta(days=20), 8),
        done=local(mon - timedelta(days=20), 9),
        project_id=dormant.id,
    )

    body = await get(client, "planner", date=iso(mon))
    days = by_date(body["days"])
    assert days[iso(mon)] == {"date": iso(mon), "done": 1, "created": 3, "tracked_minutes": 50}
    assert days[iso(tue)] == {"date": iso(tue), "done": 2, "created": 2, "tracked_minutes": 35}  # subtasks count
    assert days[iso(mon + timedelta(days=2))] == {
        "date": iso(mon + timedelta(days=2)),
        "done": 0,
        "created": 0,
        "tracked_minutes": 0,
    }
    areas = [
        (item["area"]["name"] if item["area"] else None, item["minutes"], item["target_minutes"])
        for item in body["by_area"]
    ]
    assert areas == [("Спорт", 50, 120), ("Учёба", 25, None), (None, 10, None), ("Сад", 0, 60)]
    projects = [
        (item["project"]["name"], item["done"], item["total"], item["done_in_period"]) for item in body["projects"]
    ]
    assert projects == [("Готово", 1, 1, 1), ("Запуск", 1, 2, 1)]  # top-level tasks; the dormant one is gone

    month = await get(client, "planner", period="month", date=iso(mon))
    assert [item["target_minutes"] for item in month["by_area"] if item["area"]] == [522, None, 261]  # ×4.35


async def test_the_running_timer_counts_today(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    today = datetime.now(BANGKOK).date()
    await _entry(db, user_id, today, 30, running=True)
    await tracked(db, user_id, today, 15)
    body = await get(client, "planner")
    assert by_date(body["days"])[iso(today)]["tracked_minutes"] in (45, 46)
    assert body["by_area"][0]["area"] is None and body["by_area"][0]["minutes"] in (45, 46)


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #
async def test_progress_reads_scores_xp_and_the_streak_calendar(player, db) -> None:
    """The series as stored (a fixed clock: the service, without the on-read refresh)."""
    user_id = player["user_id"]
    days = [NOON.astimezone(BANGKOK).date() - timedelta(days=back) for back in (2, 1, 0)]  # Mon–Wed 13–15 July
    for day in days:
        await eat_day(db, user_id, day, 2000)
        await water(db, user_id, day, 2500)
    await journal(db, user_id, days[1])
    await run(db, user_id, NOON)
    await db.execute(
        sa.update(Streak)
        .where(Streak.user_id == user_id, Streak.kind == StreakKind.OVERALL)
        .values(freeze_used_dates=["2026-07-16", "garbage"])  # a malformed stored value degrades
    )
    await db.commit()

    settings = (await db.scalars(select(UserSettings).where(UserSettings.user_id == user_id))).one()
    ctx = context_for(settings, user_id, NOON)
    series = await stats.progress(db, ctx, stats.span_for("week", date(2026, 7, 15), ctx.week_start, ctx.today))
    body = progress_stats_out(series).model_dump(mode="json", by_alias=True)
    assert (body["from"], body["to"], body["today"]) == ("2026-07-13", "2026-07-19", "2026-07-15")
    stored = {
        row.local_day: row for row in (await db.scalars(select(DailyScore).where(DailyScore.user_id == user_id))).all()
    }
    xp_by_day: dict[date, int] = {}
    for row in (await db.scalars(select(XpEvent).where(XpEvent.user_id == user_id))).all():
        xp_by_day[row.local_day] = xp_by_day.get(row.local_day, 0) + row.points
    scores, xp, calendar = by_date(body["scores"]), by_date(body["xp"]), by_date(body["streak_calendar"])
    for offset in range(7):
        day = WEEK + timedelta(days=offset)
        row = stored.get(day)
        assert scores[iso(day)]["score"] == (round(float(row.score), 1) if row else None), day
        assert xp[iso(day)]["xp"] == xp_by_day.get(day, 0), day
        assert calendar[iso(day)]["overall"] == (row is not None and score.counts_for_overall(row)), day
        assert calendar[iso(day)]["freeze"] == (day == date(2026, 7, 16)), day
    assert scores["2026-07-13"]["score"] is not None and xp["2026-07-13"]["xp"] > 0
    assert scores["2026-07-19"]["score"] is None and xp["2026-07-19"]["xp"] == 0  # the future
    assert calendar["2026-07-13"]["overall"] is True  # a full, logged day in the zone


async def test_progress_refreshes_today_on_read(player, db) -> None:
    """Like /gamification/scores, the endpoint lets the engine refresh today and yesterday
    first: a meal logged a second ago is in today's score and XP without any job run."""
    client, user_id = player["client"], player["user_id"]
    today = datetime.now(BANGKOK).date()
    for back in (3, 2):
        await eat_day(db, user_id, today - timedelta(days=back), 2000)
        await water(db, user_id, today - timedelta(days=back), 2500)
    await eat_day(db, user_id, today, 2000)
    assert (await db.scalar(select(sa.func.count()).select_from(DailyScore))) == 0  # nothing computed yet

    body = await get(client, "progress", period="month")
    scores, xp, calendar = by_date(body["scores"]), by_date(body["xp"]), by_date(body["streak_calendar"])
    history = (await client.get("/api/v1/gamification/scores", params={"from": iso(today), "to": iso(today)})).json()
    assert scores[iso(today)]["score"] is not None
    assert scores[iso(today)]["score"] == pytest.approx(history["items"][0]["score"], abs=0.05)
    assert xp[iso(today)]["xp"] > 0  # three meals logged today
    db.expire_all()
    stored = {
        row.local_day: row for row in (await db.scalars(select(DailyScore).where(DailyScore.user_id == user_id))).all()
    }
    streak = (
        await db.scalars(select(Streak).where(Streak.user_id == user_id, Streak.kind == StreakKind.OVERALL))
    ).one()
    freeze = set(streak.freeze_used_dates or [])
    for item in body["streak_calendar"]:
        day = date.fromisoformat(item["date"])
        row = stored.get(day)
        assert item["overall"] == (row is not None and score.counts_for_overall(row)), day
        assert item["freeze"] == (item["date"] in freeze), day
        if day > today:
            assert (scores[item["date"]]["score"], xp[item["date"]]["xp"], item["overall"]) == (None, 0, False)
    assert calendar[iso(today)]["freeze"] is False


# --------------------------------------------------------------------------- #
# Timezones and DST
# --------------------------------------------------------------------------- #
async def test_days_follow_the_users_timezone_across_a_dst_change(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    await configure(db, user_id, timezone="Europe/Berlin")
    # Sunday 25 Oct 2026: clocks go back 03:00 CEST → 02:00 CET (a 25-hour day)
    for instant, title in (
        (datetime(2026, 10, 24, 21, 30, tzinfo=UTC), "23:30 Saturday"),
        (datetime(2026, 10, 24, 22, 30, tzinfo=UTC), "00:30 Sunday (CEST)"),
        (datetime(2026, 10, 25, 0, 30, tzinfo=UTC), "02:30 Sunday, first time"),
        (datetime(2026, 10, 25, 1, 30, tzinfo=UTC), "02:30 Sunday, second time"),
        (datetime(2026, 10, 25, 22, 30, tzinfo=UTC), "23:30 Sunday (CET)"),
        (datetime(2026, 10, 25, 23, 30, tzinfo=UTC), "00:30 Monday"),
    ):
        await _task(db, user_id, created=instant, done=instant, title=title)
    body = await get(client, "planner", date="2026-10-25")
    assert (body["from"], body["to"]) == ("2026-10-19", "2026-10-25")
    assert len(body["days"]) == 7  # the 25-hour day is one day
    days = by_date(body["days"])
    assert (days["2026-10-24"]["done"], days["2026-10-25"]["done"]) == (1, 4)
    assert (days["2026-10-24"]["created"], days["2026-10-25"]["created"]) == (1, 4)
    following = await get(client, "planner", date="2026-10-26")
    assert by_date(following["days"])["2026-10-26"]["done"] == 1

    # spring forward (29 March: 02:00 → 03:00, a 23-hour day) in a quarter view
    await _task(
        db, user_id, created=datetime(2026, 3, 28, 23, 30, tzinfo=UTC), done=datetime(2026, 3, 28, 23, 30, tzinfo=UTC)
    )
    await _task(
        db, user_id, created=datetime(2026, 3, 29, 21, 30, tzinfo=UTC), done=datetime(2026, 3, 29, 21, 30, tzinfo=UTC)
    )
    await _task(
        db, user_id, created=datetime(2026, 3, 29, 22, 30, tzinfo=UTC), done=datetime(2026, 3, 29, 22, 30, tzinfo=UTC)
    )
    quarter = await get(client, "planner", period="quarter", date="2026-03-29")
    assert len(quarter["days"]) == 90
    spring = by_date(quarter["days"])
    assert (spring["2026-03-29"]["done"], spring["2026-03-30"]["done"]) == (2, 1)
    nutrition = await get(client, "nutrition", period="quarter", date="2026-03-29")
    assert len(nutrition["days"]) == 90 and len({day["date"] for day in nutrition["days"]}) == 90


async def test_a_local_day_that_differs_from_the_utc_day(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    instant = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)  # 03:00 on the 15th in Bangkok, 13:00 on the 14th in LA
    await _task(db, user_id, created=instant, done=instant)
    db.add(MealLog(user_id=user_id, logged_at=instant, local_day=date(2026, 7, 15), kcal=Decimal(700), item_count=1))
    await db.commit()
    body = await get(client, "planner", date="2026-07-15")
    assert by_date(body["days"])["2026-07-15"]["done"] == 1 and by_date(body["days"])["2026-07-14"]["done"] == 0
    assert by_date((await get(client, "nutrition", date="2026-07-15"))["days"])["2026-07-15"]["meals"] == 1

    await configure(db, user_id, timezone="America/Los_Angeles", week_start=0)
    body = await get(client, "planner", date="2026-07-15")
    assert (body["from"], body["to"]) == ("2026-07-12", "2026-07-18")  # weeks start on Sunday
    assert by_date(body["days"])["2026-07-14"]["done"] == 1 and by_date(body["days"])["2026-07-15"]["done"] == 0
    assert body["today"] == datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


# --------------------------------------------------------------------------- #
# Empty account and scoping
# --------------------------------------------------------------------------- #
async def test_an_empty_account(player) -> None:
    client = player["client"]
    nutrition = await get(client, "nutrition", date=iso(WEEK))
    assert all(day["kcal"] is None and day["meals"] == 0 and day["water_ml"] is None for day in nutrition["days"])
    assert nutrition["averages"] == {
        "days": 0,
        "kcal": None,
        "protein_g": None,
        "fat_g": None,
        "carbs_g": None,
        "fiber_g": None,
        "meals": None,
        "water_days": 0,
        "water_ml": None,
    }
    assert nutrition["adherence"] == {"zone_days": 0, "protein_days": 0, "water_days": 0, "logged_days": 0}
    activity = await get(client, "activity", date=iso(WEEK))
    assert all(day["steps"] is None and day["workouts"] == 0 for day in activity["days"])
    assert activity["weeks"] == [
        {"week_start": iso(WEEK), "workouts": 0, "planned": 0, "required": 3, "volume_kg": 0.0}
    ]
    assert (activity["muscle_volume"], activity["records"], activity["records_total"]) == ([], [], 0)
    body = await get(client, "body", date=iso(WEEK))
    assert body["change_30d"] is None and body["sleep_avg"] is None
    assert all(point["kg"] is None for point in body["weight"]) and all(n["minutes"] is None for n in body["sleep"])
    planner = await get(client, "planner", date=iso(WEEK))
    assert all((day["done"], day["created"], day["tracked_minutes"]) == (0, 0, 0) for day in planner["days"])
    assert (planner["by_area"], planner["projects"]) == ([], [])
    progress = await get(client, "progress", date=iso(WEEK))
    assert all(point["score"] is None for point in progress["scores"]) and all(p["xp"] == 0 for p in progress["xp"])
    assert not any(day["overall"] or day["freeze"] for day in progress["streak_calendar"])


async def test_a_second_user_sees_nothing_of_the_first(player, db) -> None:
    client, user_id = player["client"], player["user_id"]
    await eat_day(db, user_id, WEEK)
    await water(db, user_id, WEEK)
    await steps(db, user_id, WEEK, 10000)
    await workout(db, user_id, WEEK)
    await weigh_in(db, user_id, WEEK)
    await sleep(db, user_id, WEEK)
    await done_task(db, user_id, local(WEEK, 10))
    await tracked(db, user_id, WEEK, 30)
    db.add(XpEvent(user_id=user_id, kind=XpKind.MANUAL, points=50, awarded_on=WEEK, local_day=WEEK, dedupe_key="x"))
    await db.commit()
    mine = await get(client, "nutrition", date=iso(WEEK))
    assert mine["adherence"]["logged_days"] == 1
    async with other_user() as stranger:
        for series in SERIES:
            body = await get(stranger, series, date=iso(WEEK))
            text = str(body)
            assert "2000.0" not in text and "10000" not in text, series
        assert by_date((await get(stranger, "planner", date=iso(WEEK)))["days"])[iso(WEEK)]["done"] == 0
        assert by_date((await get(stranger, "progress", date=iso(WEEK)))["xp"])[iso(WEEK)]["xp"] == 0
        assert by_date((await get(stranger, "body", date=iso(WEEK)))["weight"])[iso(WEEK)]["kg"] is None
        assert (await get(stranger, "activity", date=iso(WEEK)))["weeks"][0]["workouts"] == 0


# --------------------------------------------------------------------------- #
# A year of data
# --------------------------------------------------------------------------- #
async def _year_of_data(db, user_id: uuid.UUID, last: date) -> None:
    """A dense year, inserted in bulk: 3 meals, water, steps, sleep, a weigh-in, 5 tasks,
    2 time entries, a journal entry, XP and a score every day; 3 workouts with 10 sets a week."""
    bench = await exercise(db)
    rows: dict[type, list[dict]] = {
        model: []
        for model in (
            MealLog,
            WaterLog,
            HealthDaily,
            SleepLog,
            BodyMetric,
            Task,
            TimeEntry,
            JournalEntry,
            XpEvent,
            DailyScore,
            WorkoutLog,
            WorkoutSet,
            PersonalRecord,
        )
    }
    now = datetime.now(UTC)
    for back in range(366):
        day = last - timedelta(days=back)
        for hh in (8, 13, 19):
            rows[MealLog].append(
                {
                    "id": uuid.uuid4(),
                    "client_uuid": uuid.uuid4(),
                    "user_id": user_id,
                    "logged_at": local(day, hh),
                    "local_day": day,
                    "kcal": Decimal(650),
                    "protein_g": Decimal(45),
                    "fiber_g": Decimal(9),
                    "item_count": 1,
                }
            )
        rows[WaterLog].append(
            {
                "id": uuid.uuid4(),
                "client_uuid": uuid.uuid4(),
                "user_id": user_id,
                "logged_at": local(day, 10),
                "local_day": day,
                "amount_ml": 2400,
            }
        )
        rows[HealthDaily].append(
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "local_day": day,
                "steps": 7000 + back,
                "source": HealthSource.MANUAL,
                "ingested_at": now,
            }
        )
        rows[SleepLog].append(
            {
                "id": uuid.uuid4(),
                "client_uuid": uuid.uuid4(),
                "user_id": user_id,
                "local_day": day,
                "bed_at": local(day, 0),
                "wake_at": local(day, 7),
                "duration_min": 420,
            }
        )
        rows[BodyMetric].append(
            {
                "id": uuid.uuid4(),
                "client_uuid": uuid.uuid4(),
                "user_id": user_id,
                "measured_on": day,
                "weight_kg": Decimal("80.5"),
            }
        )
        for index in range(5):
            rows[Task].append(
                {
                    "id": uuid.uuid4(),
                    "client_uuid": uuid.uuid4(),
                    "user_id": user_id,
                    "title": f"t{index}",
                    "status": TaskStatus.DONE,
                    "priority": 3,
                    "completed_at": local(day, 9 + index),
                    "created_at": local(day, 8),
                    "updated_at": now,
                }
            )
        for hh in (9, 14):
            rows[TimeEntry].append(
                {
                    "id": uuid.uuid4(),
                    "client_uuid": uuid.uuid4(),
                    "user_id": user_id,
                    "started_at": local(day, hh),
                    "ended_at": local(day, hh) + timedelta(minutes=45),
                    "duration_s": 2700,
                    "local_day": day,
                }
            )
        rows[JournalEntry].append(
            {"id": uuid.uuid4(), "client_uuid": uuid.uuid4(), "user_id": user_id, "entry_date": day, "note_md": "ok"}
        )
        rows[XpEvent].append(
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "kind": XpKind.MANUAL,
                "points": 40,
                "awarded_on": day,
                "local_day": day,
                "dedupe_key": f"bulk:{day}",
            }
        )
        rows[DailyScore].append(
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "local_day": day,
                "score": Decimal(72),
                "components": {"version": 1, "provisional": False, "credit": True, "items": []},
                "weights": {},
                "computed_at": now,
            }
        )
        if day.weekday() in (0, 2, 4):
            workout_id = uuid.uuid4()
            rows[WorkoutLog].append(
                {
                    "id": workout_id,
                    "client_uuid": uuid.uuid4(),
                    "user_id": user_id,
                    "started_at": local(day, 18),
                    "local_day": day,
                    "status": WorkoutStatus.COMPLETED,
                    "kcal_burned": Decimal(300),
                    "total_volume_kg": Decimal(4000),
                    "pr_count": 1,
                }
            )
            for index in range(10):
                rows[WorkoutSet].append(
                    {
                        "id": uuid.uuid4(),
                        "client_uuid": uuid.uuid4(),
                        "user_id": user_id,
                        "workout_log_id": workout_id,
                        "exercise_id": bench,
                        "reps": 8,
                        "weight_kg": Decimal(50),
                        "set_index": index + 1,
                    }
                )
            rows[PersonalRecord].append(
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "exercise_id": bench,
                    "kind": "weight",
                    "value": Decimal(100),
                    "unit": "kg",
                    "achieved_at": local(day, 19),
                    "workout_log_id": workout_id,
                }
            )
    for model, values in rows.items():
        for start in range(0, len(values), 500):
            await db.execute(sa.insert(model.__table__), values[start : start + 500])
    await db.commit()


def _busy() -> bool:
    """The box is shared: above two runnable processes per CPU a wall clock proves nothing."""
    return os.getloadavg()[0] > 2 * (os.cpu_count() or 1)


async def test_a_year_costs_what_a_week_costs(player, db) -> None:
    """No per-day queries (the same statements for a week and a year) and < 300 ms per
    series for a dense year — the timing is only asserted when the shared box is idle."""
    from sqlalchemy import event

    from app.db import engine

    client, user_id = player["client"], player["user_id"]
    today = datetime.now(BANGKOK).date()
    await _year_of_data(db, user_id, today)
    statements: list[str] = []

    def count(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    async def measure(series: str, period: str) -> tuple[int, float]:
        statements.clear()
        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            began = time.perf_counter()
            response = await client.get(f"{S}/{series}", params={"period": period})
            elapsed = time.perf_counter() - began
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        assert response.status_code == 200, response.text
        return len(statements), elapsed

    await get(client, "nutrition")  # the device's last-seen touch happens once, outside the counts
    timings = {}
    for series in SERIES:
        await measure(series, "week")  # warm: the first progress read of a day writes the open window once
        week_statements, _ = await measure(series, "week")
        await measure(series, "year")  # warm
        year_statements, elapsed = await measure(series, "year")
        _, again = await measure(series, "year")
        timings[series] = round(min(elapsed, again) * 1000)
        assert year_statements == week_statements, series
    year = await get(client, "nutrition", period="year")
    assert year["adherence"]["logged_days"] == (today - date(today.year, 1, 1)).days + 1
    print(f"\n/stats year timings (ms): {timings}, load {os.getloadavg()[0]:.1f}")
    if _busy():
        pytest.skip(f"timing not asserted: load {os.getloadavg()[0]:.1f} on {os.cpu_count()} CPUs ({timings})")
    assert max(timings.values()) < 300, timings
