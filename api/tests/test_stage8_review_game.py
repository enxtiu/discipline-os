"""Stage-8 review fixes (gamification): regression tests adapted from the reviewer's probes.

H1  water is nutrition: no water XP, water streak, water quest or water badge on a day
    without a meal at or above the safe floor (one shared predicate)
M1  finished days are judged against the targets they were finalised with, finished weeks
    against the plan frozen when they ended; current targets/plan only for the open window
    and the current week — streaks, achievements, quests and /stats agree
L2  malformed stored JSON degrades, never a 500
L3  a final day is never recomputed, whatever the timezone does later
L4  ``week_start`` 0..6 everywhere (the planner's helper)
L5  the request path finalises at most a bounded number of pending days; the jobs (and
    ``backfill-scores``) finish the rest in chunks with identical results
D1  meal-logging rewards never depend on the amount eaten
D2  overall-streak credit is "a meal at or above the floor"; the kcal part is 0 above 1.6·T
Notes: the streak reminder slot avoids quiet hours; the engine GETs are rate limited.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.errors import Conflict
from app.models import (
    Achievement,
    DailyScore,
    Quest,
    QuestStatus,
    StreakKind,
    UserAchievement,
    UserQuest,
    XpKind,
)
from app.services.day_metrics import week_start_for
from app.services.gamification import quests, score
from app.services.gamification.context import build_context
from app.services.planner.containers import week_start_for as planner_week_start_for
from tests.stage8_helpers import (
    NOON,
    TODAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    eat_day,
    meal,
    owner_id,
    run,
    water,
    workout,
    xp_rows,
)

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# L4 — week_start 0..6 everywhere
# --------------------------------------------------------------------------- #
def test_one_week_helper_for_every_week_start() -> None:
    for week_start in range(7):
        for offset in range(14):
            day = TODAY + timedelta(days=offset)
            first = week_start_for(day, week_start)
            assert first == planner_week_start_for(day, week_start)
            assert first <= day < first + timedelta(days=7)
            assert (first.weekday() + 1) % 7 == week_start  # 0 = Sunday … 6 = Saturday


async def test_saturday_weeks_for_quests_streaks_and_stats(authed: httpx.AsyncClient, db) -> None:
    user_id = await owner_id(db)
    response = await authed.patch("/api/v1/settings", json={"week_start": 6})  # Saturday
    assert response.status_code == 200, response.text
    stats = await authed.get("/api/v1/stats/activity", params={"period": "week"})
    assert stats.status_code == 200, stats.text
    today = await authed.get("/api/v1/gamification/today")
    assert today.status_code == 200, today.text
    weekly = [quest for quest in today.json()["quests"] if quest["kind"] == "weekly"]
    assert weekly and {quest["period_start"] for quest in weekly} == {stats.json()["from"]}
    assert date.fromisoformat(stats.json()["from"]).weekday() == 5  # a Saturday

    # the engine on a fixed clock: Wednesday 15 July → the week started on Saturday 11 July
    await configure(db, user_id, week_start=6)
    for day in (date(2026, 7, 11), date(2026, 7, 12), date(2026, 7, 13)):  # Sat, Sun, Mon
        await workout(db, user_id, day)
    await workout(db, user_id, date(2026, 7, 10))  # Friday: the week before
    snapshot = await run(db, user_id, NOON)
    assert snapshot.ctx.week_start_day == date(2026, 7, 11)
    state = next(state for state in snapshot.streaks if state.kind.value == "workout")
    assert (state.period_done, state.period_required, state.today_met) == (3, 3, True)
    assert set(snapshot.plans) >= {date(2026, 7, 4), date(2026, 7, 11)}  # Saturday weeks


# --------------------------------------------------------------------------- #
# L2 — stored JSON degrades, never a 500
# --------------------------------------------------------------------------- #
async def test_a_malformed_frozen_breakdown_degrades_instead_of_a_500(authed: httpx.AsyncClient, db) -> None:
    """Adapted from probe x4: whatever a frozen ``daily_scores`` row holds (a restored or
    damaged backup), /gamification/scores and /stats/progress answer 200."""
    user_id = await owner_id(db)
    day = date(2026, 7, 10)
    junk = [
        {"key": "kcal", "value": "n/a", "weight": "25", "excluded": "no", "detail": {"amount": "x", "state": 7}},
        {"key": "water", "value": 7, "weight": -3, "detail": ["not", "a", "dict"]},
        {"key": "protein", "value": True, "weight": None},
        "garbage",
        {"key": "bogus", "value": 1},
        {"value": 1},
    ]
    db.add(
        DailyScore(
            user_id=user_id,
            local_day=day,
            score=Decimal("61.5"),
            components={"final": True, "credit": "true", "provisional": 1, "items": junk},
            weights={"kcal": "x", "water": 10, "sleep": float("1e300")},
            computed_at=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
        )
    )
    db.add(
        DailyScore(
            user_id=user_id,
            local_day=day - timedelta(days=1),
            score=Decimal("40"),
            components=["not", "an", "object"],
            weights=[],
            computed_at=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
        )
    )
    await db.commit()

    response = await authed.get("/api/v1/gamification/scores", params={"from": "2026-07-08", "to": "2026-07-10"})
    assert response.status_code == 200, response.text
    items = {item["date"]: item for item in response.json()["items"]}
    stored = items["2026-07-10"]
    assert stored["score"] == 61.5 and stored["provisional"] is False
    parts = {item["key"]: item for item in stored["components"]}
    assert set(parts) == {"kcal", "water", "protein"}
    assert (parts["kcal"]["value"], parts["kcal"]["weight"], parts["kcal"]["excluded"]) == (0.0, 0.0, False)
    assert parts["kcal"]["detail"]["amount"] is None and parts["kcal"]["detail"]["state"] == "ok"
    assert (parts["water"]["value"], parts["water"]["weight"]) == (1.0, 0.0)  # clamped
    assert (parts["protein"]["value"], parts["protein"]["weight"]) == (0.0, 0.0)  # a bool is no number
    assert items["2026-07-09"]["score"] == 40.0 and items["2026-07-09"]["components"] == []

    rows = await score.load_rows(db, user_id, day, day)
    parsed = score.from_row(rows[day])
    assert parsed.credit is False and parsed.weights == {"water": 10.0, "sleep": 1e300}
    assert not score.counts_for_overall(rows[day])  # the string "true" is no credit
    progress = await authed.get("/api/v1/stats/progress", params={"period": "week", "date": "2026-07-10"})
    assert progress.status_code == 200, progress.text


# --------------------------------------------------------------------------- #
# H1 — water is nutrition: one shared credit predicate
# --------------------------------------------------------------------------- #
async def unlocked_codes(db, user_id) -> set[str]:
    db.expire_all()
    rows = await db.execute(
        select(Achievement.code)
        .join(UserAchievement, UserAchievement.achievement_id == Achievement.id)
        .where(UserAchievement.user_id == user_id)
    )
    return set(rows.scalars().all())


def streak_of(snapshot, kind: StreakKind):
    return next(state for state in snapshot.streaks if state.kind is kind)


async def test_water_only_days_earn_no_xp_streak_or_achievement(owner, db) -> None:
    """Adapted from the probe: 8 days of water at the target and not a single meal."""
    uid = owner.id
    await configure(db, uid)  # a man: floor 1500, water target 2500 ml
    for back in range(0, 8):
        await water(db, uid, TODAY - timedelta(days=back), 2500)
    snapshot = await run(db, uid, NOON)
    rows = await xp_rows(db, uid)
    assert not [row for row in rows.values() if row.kind == XpKind.WATER_TARGET]
    assert streak_of(snapshot, StreakKind.WATER).current == 0
    assert "water_7" not in await unlocked_codes(db, uid)
    assert snapshot.metrics is not None and snapshot.metrics["days_water_target"] == 0

    # food below the floor does not make water count either (1400 kcal, floor 1500)
    for back in range(0, 8):
        await eat_day(db, uid, TODAY - timedelta(days=back), 1400)
    snapshot = await run(db, uid, NOON + timedelta(minutes=5))
    assert streak_of(snapshot, StreakKind.WATER).current == 0
    assert not [row for row in (await xp_rows(db, uid)).values() if row.kind == XpKind.WATER_TARGET]


async def test_water_counts_on_days_with_a_meal_at_or_above_the_floor(owner, db) -> None:
    uid = owner.id
    await configure(db, uid)
    for back in range(0, 8):
        await water(db, uid, TODAY - timedelta(days=back), 2500)
        await meal(db, uid, TODAY - timedelta(days=back), 1500)  # exactly at the floor counts
    snapshot = await run(db, uid, NOON)
    water_xp = {row.local_day for row in (await xp_rows(db, uid)).values() if row.kind == XpKind.WATER_TARGET}
    assert water_xp == {TODAY - timedelta(days=back) for back in range(0, 8)}
    assert streak_of(snapshot, StreakKind.WATER).current == 8
    assert "water_7" in await unlocked_codes(db, uid)


async def _quest_row(db, uid, code: str, first: date, last: date) -> uuid.UUID:
    """The id of today's (this week's) instance of ``code`` — as if the rotation had picked it."""
    quest_id = (await db.scalars(select(Quest.id).where(Quest.code == code))).one()
    found = (
        await db.scalars(
            select(UserQuest).where(
                UserQuest.quest_id == quest_id, UserQuest.user_id == uid, UserQuest.period_start == first
            )
        )
    ).first()
    if found is None:
        db.add(UserQuest(user_id=uid, quest_id=quest_id, period_start=first, period_end=last))
        await db.commit()
    db.expire_all()
    return (
        await db.scalars(
            select(UserQuest.id).where(
                UserQuest.quest_id == quest_id, UserQuest.user_id == uid, UserQuest.period_start == first
            )
        )
    ).one()


async def _claim(uid, row_id) -> int | str:
    from app.db import SessionLocal

    async with SessionLocal() as session:
        ctx = await build_context(session, uid, NOON)
        try:
            _, awarded = await quests.claim(session, ctx, row_id)
        except Conflict as exc:
            await session.rollback()
            return exc.code
        await session.commit()
    return awarded


async def test_the_water_quest_shows_millilitres_but_completes_only_with_food(owner, db) -> None:
    """Adapted from the probe: «Выпейте норму воды» on a day without a meal."""
    uid = owner.id
    await configure(db, uid)
    await water(db, uid, TODAY, 2500)  # no meal today
    await run(db, uid, NOON)  # seeds the quest pool
    row_id = await _quest_row(db, uid, "daily_water", TODAY, TODAY)
    snapshot = await run(db, uid, NOON)
    view = next(view for view in snapshot.quests if view.quest.code == "daily_water")
    assert (view.progress, view.target, view.row.status, view.claimable) == (2500, 2500, QuestStatus.ACTIVE, False)
    assert await _claim(uid, row_id) == "quest_not_completed"

    await eat_day(db, uid, TODAY, 2000)  # food at or above the floor: now the water counts
    snapshot = await run(db, uid, NOON + timedelta(minutes=5))
    view = next(view for view in snapshot.quests if view.quest.code == "daily_water")
    assert (view.progress, view.row.status, view.claimable) == (2500, QuestStatus.COMPLETED, True)
    assert await _claim(uid, row_id) == 20


async def test_the_weekly_water_quest_counts_only_days_with_credit(owner, db) -> None:
    uid = owner.id
    await configure(db, uid)
    week = date(2026, 7, 13)  # Monday; TODAY is Wednesday 15 July
    for offset in range(3):
        await water(db, uid, week + timedelta(days=offset), 3000)
    await eat_day(db, uid, week, 2000)  # credit
    await eat_day(db, uid, week + timedelta(days=1), 900)  # below the floor: no credit
    await run(db, uid, NOON)
    await _quest_row(db, uid, "weekly_water_5", week, week + timedelta(days=6))
    snapshot = await run(db, uid, NOON)
    view = next(view for view in snapshot.quests if view.quest.code == "weekly_water_5")
    assert (view.progress, view.target) == (1, 5)


async def test_stats_water_days_follow_the_same_credit(authed: httpx.AsyncClient, db) -> None:
    user_id = await owner_id(db)
    await configure(db, user_id)
    week = date(2026, 7, 13)
    for offset in range(4):
        await water(db, user_id, week + timedelta(days=offset), 2600)
    await eat_day(db, user_id, week, 2000)  # credit → counts
    await eat_day(db, user_id, week + timedelta(days=1), 1000)  # below the floor
    await eat_day(db, user_id, week + timedelta(days=2), 1500)  # exactly the floor → counts
    body = (await authed.get("/api/v1/stats/nutrition", params={"date": week.isoformat()})).json()
    assert body["adherence"]["water_days"] == 2  # the water-only Thursday never counts
    assert [day["water_ml"] for day in body["days"][:4]] == [2600] * 4  # the series itself is unchanged


# --------------------------------------------------------------------------- #
# D1 (decided: keep) — meal-logging rewards are for logging, never for the amount
# --------------------------------------------------------------------------- #
async def second_account(db) -> uuid.UUID:
    """Another active account with its own settings row (no device needed)."""
    from app.db import SessionLocal
    from app.models import User, UserStatus
    from app.services.settings_service import get_or_create_settings

    async with SessionLocal() as session:
        user = User(display_name="Second", status=UserStatus.ACTIVE, is_owner=False)
        session.add(user)
        await session.flush()
        await get_or_create_settings(session, user)
        await session.commit()
        return user.id


LOGGING_BADGES = {"meals_1", "meals_50", "streak_meals_30"}


async def test_policy_meal_logging_rewards_never_depend_on_the_amount(owner, db) -> None:
    """Replaces the probe's expectation (review decision D1): 30 days of 3 × 200 kcal
    (far below the 1500 floor) earn exactly the meal-logging rewards that 30 days of
    3 × 667 kcal earn — XP per meal, the meal-logging streak, the logging badges and
    «Запишите 3 приёма пищи» — so logging a low day is never punished (no incentive to
    stop logging), and nothing pays more for eating less: every day and the total of the
    low account earn less XP, and score less, than the account eating at its target."""
    low, normal = owner.id, await second_account(db)
    for uid in (low, normal):
        await configure(db, uid)
    days = [TODAY - timedelta(days=back) for back in range(30)]
    for day in days:
        for index in range(3):
            await meal(db, low, day, 200, protein=10, fiber=2, hh=8 + 5 * index)
            await meal(db, normal, day, 2000 / 3, protein=50, fiber=10, hh=8 + 5 * index)
    await run(db, low, NOON)  # seeds the quest pool
    for uid in (low, normal):
        await _quest_row(db, uid, "daily_meals_3", TODAY, TODAY)
    snapshots = {uid: await run(db, uid, NOON) for uid in (low, normal)}

    def meal_xp(rows) -> int:
        return sum(points for kind, points, _ in rows if kind == XpKind.MEAL_LOGGED)

    rows = {}
    for uid in (low, normal):
        rows[uid] = [(row.kind, row.points, row.local_day) for row in (await xp_rows(db, uid)).values()]
    assert meal_xp(rows[low]) == meal_xp(rows[normal]) == 30 * 3 * 5
    logging = {uid: streak_of(snapshots[uid], StreakKind.MEAL_LOGGING) for uid in (low, normal)}
    assert (
        (logging[low].current, logging[low].longest) == (logging[normal].current, logging[normal].longest) == (30, 30)
    )
    assert await unlocked_codes(db, low) & LOGGING_BADGES == LOGGING_BADGES
    assert await unlocked_codes(db, normal) & LOGGING_BADGES == LOGGING_BADGES
    quest = {
        uid: next(view for view in snapshots[uid].quests if view.quest.code == "daily_meals_3") for uid in (low, normal)
    }
    assert (quest[low].progress, quest[low].row.status) == (quest[normal].progress, quest[normal].row.status)
    assert quest[low].row.status is QuestStatus.COMPLETED

    # nothing pays more for eating less
    def by_day(rows) -> dict[date, int]:
        out: dict[date, int] = {}
        for _, points, day in rows:
            out[day] = out.get(day, 0) + points
        return out

    low_days, normal_days = by_day(rows[low]), by_day(rows[normal])
    assert all(low_days.get(day, 0) <= normal_days.get(day, 0) for day in set(low_days) | set(normal_days))
    assert sum(low_days.values()) < sum(normal_days.values())
    for day in days[1:]:
        assert snapshots[low].flags[day].score <= snapshots[normal].flags[day].score
        assert snapshots[low].flags[day].credit is False and snapshots[normal].flags[day].credit is True
    assert streak_of(snapshots[low], StreakKind.OVERALL).current == 0


# --------------------------------------------------------------------------- #
# D2 (decided: keep) — overall-streak credit is "a meal at or above the floor"
# --------------------------------------------------------------------------- #
def test_policy_overall_credit_is_a_meal_at_or_above_the_floor_and_the_kcal_part_is_zero_above_1_6_t() -> None:
    """Review decision D2: credit has no upper bound — above 1.6·T the kcal part is
    already 0, so a binge day with every other part perfect scores 75 and counts for
    the overall streak (≥ 60) like any logged day at or above the floor; below the
    floor nothing counts, whatever the score."""
    from app.services.day_metrics import DayMetrics
    from tests.test_stage8_score import ctx as score_ctx

    context = score_ctx()  # T = 2000, F = 1500

    def day_with(kcal: float, meals: int = 3):
        return score.compute_day_score(
            DayMetrics(
                day=date(2026, 7, 10),
                kcal=kcal,
                meals=meals,
                protein_g=150,
                fiber_g=30,
                water_ml=2500,
                steps=9000,
                sleep_minutes=450,
                tasks_done=1,
                journal=True,
            ),
            context,
            provisional=False,
        )

    for kcal in (3200, 5000, 10000):  # 1.6·T and above
        binge = day_with(kcal)
        part = binge.component("kcal")
        assert (part.value, part.detail["state"]) == (0.0, "above_zone")
        assert (binge.value, binge.credit, binge.counts_for_overall) == (75.0, True, True)
    assert day_with(2700).component("kcal").value == pytest.approx(0.25)  # 1.35·T
    assert day_with(2000).value == 100.0
    exactly = day_with(1500)
    assert exactly.credit is True and exactly.counts_for_overall  # the floor itself counts
    low = day_with(900)
    assert (low.value, low.credit, low.counts_for_overall) == (75.0, False, False)
    assert day_with(0, meals=0).credit is False  # not logging is never credit
