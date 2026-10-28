"""Stage 10: the one calm insight of «Сегодня» (GET /insights/today).

The rules on hand-made history (no database): only "more" of protein, fibre, water or
steps against the 28 days before the day; never calories, fat or carbs, never "less";
no food observation for a day below the safe floor (steps need no credit); at least
7 days with data and 5 % above the average (also after rounding); today before
yesterday, then the metrics in order; missing days are not zeros. Then the endpoint:
the shape, an empty account, the user's own history (deleted meals excluded), the
user's timezone, three statements, scoping, auth."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import event, select

from app.models import UserSettings
from app.services import insights
from app.services.day_metrics import HealthDay, NutritionDay, kcal_zone
from app.services.gamification.context import DayRules, Targets, context_for
from tests.stage6_helpers import other_user
from tests.stage8_helpers import (
    BANGKOK,
    NOON,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    eat_day,
    meal,
    owner_id,
    steps,
    water,
)

pytestmark = pytest.mark.anyio
URL = "/api/v1/insights/today"
DAY = date(2026, 7, 15)  # the day under test in the rule tests
RULES = DayRules(targets=Targets(2000, 150, 30, 2500, 8000), floor=1500, zone=kcal_zone(2000, 1500))


def history(
    food: dict[date, tuple[float, float, float]] | None = None,
    water_ml: dict[date, int] | None = None,
    step_counts: dict[date, int] | None = None,
) -> insights.History:
    """``food`` maps a day to (kcal, protein g, fibre g) of one meal."""
    return insights.History(
        food={
            day: NutritionDay(kcal=kcal, protein_g=protein, fiber_g=fiber, meals=1)
            for day, (kcal, protein, fiber) in (food or {}).items()
        },
        water=dict(water_ml or {}),
        health={
            day: HealthDay(steps=count, active_kcal=None, distance_m=None) for day, count in (step_counts or {}).items()
        },
    )


def before(day: date, count: int, start: int = 1) -> list[date]:
    """``count`` days going back from ``day - start``."""
    return [day - timedelta(days=start + offset) for offset in range(count)]


def past_food(day: date = DAY, count: int = 10, *, kcal: float = 2000, protein: float = 100, fiber: float = 20):
    return dict.fromkeys(before(day, count), (kcal, protein, fiber))


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #
def test_protein_today_above_its_four_week_average() -> None:
    found = insights.pick(history({**past_food(), DAY: (1600, 120, 20)}), RULES, DAY)
    assert found == insights.Insight(
        metric="protein",
        day="today",
        date=DAY,
        value=120.0,
        average=100.0,
        unit="g",
        window_days=28,
        days_with_data=10,
    )


def test_only_more_of_what_the_app_encourages() -> None:
    # calories far above the average, fat and carbs never looked at: nothing to say about food
    assert insights.METRICS == ("protein", "fiber", "water", "steps")
    assert insights.pick(history({**past_food(), DAY: (3500, 90, 15)}), RULES, DAY) is None
    # below the average is never said (no "less protein than usual")
    assert insights.pick(history({**past_food(protein=140, fiber=30), DAY: (2100, 60, 8)}), RULES, DAY) is None


def test_no_food_insight_for_a_day_below_the_safe_floor() -> None:
    lean = {**past_food(), DAY: (1100, 150, 40), DAY - timedelta(days=1): (1200, 150, 40)}
    assert insights.pick(history(lean), RULES, DAY) is None
    # at the floor exactly it counts (the credit predicate of the protein XP)
    assert insights.pick(history({**lean, DAY: (1500, 150, 40)}), RULES, DAY).metric == "protein"
    # a lower personal floor (settings) is the one that applies
    low = DayRules(targets=RULES.targets, floor=1100, zone=kcal_zone(2000, 1100))
    assert insights.pick(history(lean), low, DAY).metric == "protein"
    # water needs the same credit; steps are not food and need none
    thirsty = history(lean, water_ml={**dict.fromkeys(before(DAY, 10), 1500), DAY: 2500})
    assert insights.pick(thirsty, RULES, DAY) is None
    walker = history(step_counts={**dict.fromkeys(before(DAY, 10), 6000), DAY: 9000})
    found = insights.pick(walker, RULES, DAY)
    assert (found.metric, found.day, found.value, found.average, found.unit) == ("steps", "today", 9000, 6000, "steps")


def test_enough_history_and_a_real_difference() -> None:
    assert insights.pick(history({**past_food(count=6), DAY: (2000, 130, 20)}), RULES, DAY) is None
    assert insights.pick(history({**past_food(count=7), DAY: (2000, 130, 20)}), RULES, DAY).days_with_data == 7
    assert insights.pick(history({**past_food(), DAY: (2000, 104.9, 20)}), RULES, DAY) is None  # < 5 % above
    assert insights.pick(history({**past_food(), DAY: (2000, 105, 20)}), RULES, DAY).value == 105.0
    # 5 % above, yet the same once rounded to whole grams: not said
    fibre = {**past_food(protein=100, fiber=9.6), DAY: (2000, 90, 10.1)}
    assert insights.pick(history(fibre), RULES, DAY) is None
    fibre[DAY] = (2000, 90, 10.6)
    found = insights.pick(history(fibre), RULES, DAY)
    assert (found.metric, found.value, found.average) == ("fiber", 10.6, 9.6)


def test_today_first_then_the_metrics_in_order() -> None:
    both = {**past_food(), DAY: (2000, 130, 30)}
    assert insights.pick(history(both), RULES, DAY).metric == "protein"
    fibre_only = {**past_food(), DAY: (2000, 100, 30)}
    assert insights.pick(history(fibre_only), RULES, DAY).metric == "fiber"
    # yesterday's protein would hold too, but today's water comes first
    yesterday = DAY - timedelta(days=1)
    mixed = history(
        {**past_food(count=12), yesterday: (2000, 140, 20), DAY: (1800, 100, 20)},
        water_ml={**dict.fromkeys(before(DAY, 10, start=2), 1500), DAY: 2400},
    )
    found = insights.pick(mixed, RULES, DAY)
    assert (found.metric, found.day) == ("water", "today")


def test_yesterday_when_today_has_nothing_to_say() -> None:
    yesterday = DAY - timedelta(days=1)
    data = {**past_food(yesterday), yesterday: (2100, 125, 20), DAY: (400, 20, 3)}
    found = insights.pick(history(data), RULES, DAY)
    assert (found.metric, found.day, found.date, found.value, found.average) == (
        "protein",
        "yesterday",
        yesterday,
        125,
        100,
    )
    # yesterday's window is the 28 days before yesterday: today is not part of it
    assert found.days_with_data == 10


def test_the_window_is_the_28_days_before_and_missing_is_not_zero() -> None:
    # 7 recent days at 100 g; older, richer days beyond 28 days never count
    data = {**past_food(count=7), **dict.fromkeys(before(DAY, 10, start=29), (2000, 400, 20)), DAY: (2000, 110, 20)}
    found = insights.pick(history(data), RULES, DAY)
    assert (found.average, found.days_with_data) == (100.0, 7)
    # 21 days without a meal in between do not pull the average down to 25 g
    sparse = {**dict.fromkeys(before(DAY, 7, start=22), (2000, 100, 20)), DAY: (2000, 101, 20)}
    assert insights.pick(history(sparse), RULES, DAY) is None
    # a day with 0 steps is no data either
    walker = history(
        step_counts={**dict.fromkeys(before(DAY, 7), 5000), **dict.fromkeys(before(DAY, 14, 8), 0), DAY: 5600}
    )
    assert insights.pick(walker, RULES, DAY).average == 5000


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #
@pytest.fixture
async def player(authed: httpx.AsyncClient, db) -> dict:
    user_id = await owner_id(db)
    await configure(db, user_id)
    return {"client": authed, "user_id": user_id, "today": datetime.now(BANGKOK).date()}


async def get(client: httpx.AsyncClient) -> dict:
    response = await client.get(URL)
    assert response.status_code == 200, response.text
    return response.json()


async def test_an_empty_account_has_nothing_to_say(player) -> None:
    assert await get(player["client"]) == {"date": player["today"].isoformat(), "insight": None}


async def test_the_users_history_decides(player, db) -> None:
    client, user_id, today = player["client"], player["user_id"], player["today"]
    for past in before(today, 9):
        await eat_day(db, user_id, past, 2000, protein=96, fiber=24)
    assert (await get(client))["insight"] is None  # nothing today, yesterday at the average
    await meal(db, user_id, today, 1000, protein=70, fiber=6, hh=8)
    assert (await get(client))["insight"] is None  # 1 000 kcal so far: below the floor, no food insight yet
    await meal(db, user_id, today, 800, protein=60, fiber=6, hh=13)
    assert await get(client) == {
        "date": today.isoformat(),
        "insight": {
            "metric": "protein",
            "day": "today",
            "date": today.isoformat(),
            "value": 130.0,
            "average": 96.0,
            "unit": "g",
            "window_days": 28,
            "days_with_data": 9,
        },
    }


async def test_a_deleted_meal_never_counts(player, db) -> None:
    client, user_id, today = player["client"], player["user_id"], player["today"]
    for past in before(today, 9):
        await eat_day(db, user_id, past, 2000, protein=96, fiber=24)
    await meal(db, user_id, today, 1600, protein=90, fiber=20, hh=9)
    await meal(db, user_id, today, 600, protein=80, fiber=10, hh=13, deleted=True)
    assert (await get(client))["insight"] is None


async def test_the_users_timezone_decides_the_day(player, db) -> None:
    user_id = player["user_id"]
    for past in before(DAY, 9):
        await eat_day(db, user_id, past, 2000, protein=100, fiber=20)
    await eat_day(db, user_id, DAY, 2000, protein=150, fiber=20)
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one()
    # 05:00 UTC on 15 July: noon in Bangkok (the 15th), 22:00 on the 14th in Los Angeles
    bangkok = await insights.today_insight(db, context_for(settings, user_id, NOON))
    assert (bangkok.date, bangkok.insight.metric, bangkok.insight.day, bangkok.insight.value) == (
        DAY,
        "protein",
        "today",
        150,
    )
    settings.timezone = "America/Los_Angeles"
    await db.commit()
    los_angeles = await insights.today_insight(db, context_for(settings, user_id, NOON))
    assert los_angeles.date == DAY - timedelta(days=1)
    assert los_angeles.insight is None  # the 14th sits at the average; the 15th is tomorrow there


async def test_three_statements_whatever_the_history(player, db) -> None:
    from app.db import engine

    client, user_id, today = player["client"], player["user_id"], player["today"]
    for past in before(today, 29):
        await eat_day(db, user_id, past, 2000, protein=100, fiber=20)
        await water(db, user_id, past, 2000)
        await steps(db, user_id, past, 7000)
    await get(client)  # the device's last-seen touch happens once, outside the count
    statements: list[str] = []

    def count(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        body = await get(client)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    reads = [
        statement
        for statement in statements
        if "meal_logs" in statement or "water_logs" in statement or "health_daily" in statement
    ]
    assert len(reads) == 3, statements
    assert body["insight"] is None


async def test_a_second_user_sees_nothing_of_the_first(player, db) -> None:
    client, user_id, today = player["client"], player["user_id"], player["today"]
    for past in before(today, 9):
        await steps(db, user_id, past, 6000)
    await steps(db, user_id, today, 9500)
    assert (await get(client))["insight"]["metric"] == "steps"
    async with other_user() as stranger:
        assert (await get(stranger))["insight"] is None


async def test_auth_and_api_tokens(player, client) -> None:
    from app.main import app

    created = await player["client"].post("/api/v1/tokens", json={"name": "Shortcut", "scopes": ["read:all"]})
    refused = await client.get(URL, headers={"Authorization": f"Bearer {created.json()['token']}"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "device_only")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as anonymous:
        assert (await anonymous.get(URL)).status_code == 401
