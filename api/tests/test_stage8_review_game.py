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

import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import select, update

from app.core.errors import Conflict
from app.models import (
    Achievement,
    DailyScore,
    MealLog,
    Quest,
    QuestStatus,
    StreakKind,
    UserAchievement,
    UserQuest,
    UserSettings,
    WeeklyPlan,
    XpEvent,
    XpKind,
)
from app.services.day_metrics import week_start_for
from app.services.gamification import quests, score
from app.services.gamification.context import build_context
from app.services.planner.containers import week_start_for as planner_week_start_for
from tests.stage8_helpers import (
    NOON,
    TODAY,
    YESTERDAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    eat_day,
    meal,
    owner_id,
    plan,
    run,
    water,
    workout,
    xp_rows,
)

pytestmark = pytest.mark.anyio


async def _owner(db) -> uuid.UUID:
    """The owner account without a device (engine-level tests)."""
    from app.db import SessionLocal
    from app.services.bootstrap import create_owner

    async with SessionLocal() as session:
        result = await create_owner(session, display_name="Owner")
        await session.commit()
        return result.user.id


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


# --------------------------------------------------------------------------- #
# L3 — a final day is never recomputed, whatever the timezone does later
# --------------------------------------------------------------------------- #
async def day_state(db, uid, day: date) -> tuple:
    db.expire_all()
    row = (await db.scalars(select(DailyScore).where(DailyScore.user_id == uid, DailyScore.local_day == day))).one()
    stored = (row.score, row.computed_at, row.components)
    xp = {key: event.points for key, event in (await xp_rows(db, uid)).items() if event.local_day == day}
    return (*stored, xp)


async def test_moving_west_never_reopens_a_final_day(owner, db) -> None:
    """Adapted from probe T1: Bangkok → Los Angeles after the day was finalised."""
    uid = owner.id
    d, d1 = date(2026, 7, 10), date(2026, 7, 11)
    await configure(db, uid)  # Asia/Bangkok
    await eat_day(db, uid, d)
    await eat_day(db, uid, d1)
    await run(db, uid, datetime(2026, 7, 11, 17, 30, tzinfo=UTC))  # 12 July 00:30 Bangkok: d is final
    frozen_d = await day_state(db, uid, d)
    assert frozen_d[2]["final"] is True and frozen_d[0] > 0 and frozen_d[3]
    await run(db, uid, datetime(2026, 7, 13, 5, 0, tzinfo=UTC))  # 13 July 12:00 Bangkok: d1 is final too
    frozen_d1 = await day_state(db, uid, d1)
    assert frozen_d1[2]["final"] is True and await day_state(db, uid, d) == frozen_d

    # both days' meals are deleted, then the phone moves to Los Angeles (13 July 06:00 UTC
    # = 12 July 23:00 there): d is "today − 2" again and d1 is "yesterday" — the open window
    await db.execute(update(MealLog).where(MealLog.user_id == uid).values(is_deleted=True))
    await db.execute(update(UserSettings).where(UserSettings.user_id == uid).values(timezone="America/Los_Angeles"))
    await db.commit()
    snapshot = await run(db, uid, datetime(2026, 7, 13, 6, 0, tzinfo=UTC))
    assert (snapshot.ctx.today, snapshot.ctx.yesterday) == (date(2026, 7, 12), d1)
    assert await day_state(db, uid, d) == frozen_d
    assert await day_state(db, uid, d1) == frozen_d1  # a final "yesterday" stays as it was
    assert d1 not in snapshot.computed and snapshot.flags[d1].credit is True


async def test_rows_from_before_the_final_mark_stay_frozen_after_a_move_west(owner, db) -> None:
    """Rows written before finality was explicit (no ``final`` key) are final once their
    day is before yesterday and they were computed after the grace day ended anywhere
    (10:00 UTC the next day) — whatever the timezone does now. A legacy row of the open
    window is recomputed once (it was last computed while in the window)."""
    uid = owner.id
    d = date(2026, 7, 10)
    await configure(db, uid)
    for day in (d, date(2026, 7, 14)):
        await eat_day(db, uid, day)
    await run(db, uid, datetime(2026, 7, 11, 17, 30, tzinfo=UTC))  # d finalised in Bangkok
    await run(db, uid, NOON)  # 11–13 July finalised, 14–15 July in the window
    await db.execute(
        update(DailyScore).where(DailyScore.user_id == uid).values(components=DailyScore.components.op("-")("final"))
    )
    await db.commit()
    legacy = await day_state(db, uid, d)
    assert "final" not in legacy[2]
    await db.execute(update(MealLog).where(MealLog.user_id == uid).values(is_deleted=True))
    await db.execute(update(UserSettings).where(UserSettings.user_id == uid).values(timezone="America/Los_Angeles"))
    await db.commit()
    snapshot = await run(db, uid, NOON + timedelta(hours=1))  # 14 July 23:00 in Los Angeles
    assert snapshot.ctx.today == date(2026, 7, 14)
    assert await day_state(db, uid, d) == legacy  # frozen for good
    window = await day_state(db, uid, date(2026, 7, 14))  # the open window there: recomputed
    assert (window[2]["final"], window[0], window[3]) == (False, 0, {})


async def test_store_never_overwrites_a_final_row(owner, db) -> None:
    uid = owner.id
    d = date(2026, 7, 10)
    await configure(db, uid)
    await eat_day(db, uid, d)
    snapshot = await run(db, uid, NOON)
    before = await day_state(db, uid, d)
    assert before[2]["final"] is True
    from app.db import SessionLocal
    from app.services.day_metrics import DayMetrics

    async with SessionLocal() as session:
        empty = score.compute_day_score(DayMetrics(day=d), snapshot.ctx, provisional=False)
        await score.store(session, snapshot.ctx, [empty], {d: 0})
        await session.commit()
    assert await day_state(db, uid, d) == before


# --------------------------------------------------------------------------- #
# M1 — finished days/weeks keep the targets/plan of their time
# --------------------------------------------------------------------------- #
async def test_a_weigh_in_never_rejudges_finished_days(authed: httpx.AsyncClient, owner, db) -> None:
    """Adapted from probe R1. Auto targets follow the weight: 80 kg → 2640 ml, 85 kg →
    2800 ml. Eleven days of 2700 ml (with food: water counts with nutrition credit, H1).
    After the weigh-in the nine finished days keep their verdict (the frozen score says
    2700/2640); only the open window (yesterday, today) is judged with the new target —
    yesterday falls short and a freeze token saves it, today counts once topped up."""
    from app.services.settings_service import recompute_targets

    uid = owner.id
    row = await configure(db, uid, targets_manual=False, weight_kg=80, goal_water_ml_per_kg=33)
    await recompute_targets(db, row)
    await db.commit()
    for back in range(0, 11):
        await eat_day(db, uid, TODAY - timedelta(days=back))
        await water(db, uid, TODAY - timedelta(days=back), 2700)
    before = await run(db, uid, NOON)
    assert before.ctx.targets.water_ml == 2640
    assert streak_of(before, StreakKind.WATER).current == 11

    response = await authed.put(f"/api/v1/health/weight/{TODAY}", json={"weight_kg": 85})
    assert response.status_code == 200, response.text
    after = await run(db, uid, NOON + timedelta(minutes=5))
    assert after.ctx.targets.water_ml == 2800  # 85 kg × 33 ml, rounded by the target engine
    state = streak_of(after, StreakKind.WATER)
    assert (state.current, state.longest, state.freeze_used, state.today_met) == (9, 11, [YESTERDAY], False)
    assert after.metrics is not None and after.metrics["days_water_target"] == 9  # the finished days
    frozen = (
        await db.scalars(
            select(DailyScore).where(DailyScore.user_id == uid, DailyScore.local_day == TODAY - timedelta(days=5))
        )
    ).one()
    water_part = next(item for item in frozen.components["items"] if item["key"] == "water")
    assert (water_part["detail"]["amount"], water_part["detail"]["target"]) == (2700, 2640)

    body = (await authed.get("/api/v1/stats/nutrition", params={"date": "2026-07-08"})).json()  # 6–12 July
    assert body["target_water_ml"] == 2800 and body["adherence"]["water_days"] == 7  # as the streak counts them

    await water(db, uid, TODAY, 100)  # today meets the new target
    assert streak_of(await run(db, uid, NOON + timedelta(minutes=10)), StreakKind.WATER).current == 10


async def test_a_plan_added_today_never_rejudges_finished_weeks(authed: httpx.AsyncClient, db) -> None:
    """Adapted from probe R2: eight past weeks with one workout each and no plan (3
    required) never become an 8-week streak because a plan is added today; the current
    week follows the new plan; removing it again changes nothing either."""
    uid = await owner_id(db)
    await configure(db, uid)
    this_monday = date(2026, 7, 13)  # TODAY is Wednesday 15 July
    for weeks in range(1, 9):
        await workout(db, uid, this_monday - timedelta(weeks=weeks))
    before = streak_of(await run(db, uid, NOON), StreakKind.WORKOUT)
    assert (before.current, before.longest) == (0, 0)
    xp_before = sum(row.points for row in (await xp_rows(db, uid)).values())
    last_day = (
        await db.scalars(select(DailyScore).where(DailyScore.user_id == uid, DailyScore.local_day == date(2026, 7, 12)))
    ).one()
    assert last_day.components["week"] == {"start": "2026-07-06", "planned": 0, "has_plan": False, "required": 3}

    await plan(db, uid, 0)  # today: "Monday — workout"
    after = await run(db, uid, NOON + timedelta(minutes=5))
    state = streak_of(after, StreakKind.WORKOUT)
    assert (state.current, state.longest, state.period_required) == (0, 0, 1)  # the current week: the new plan
    assert "streak_workout_8" not in await unlocked_codes(db, uid)
    assert sum(row.points for row in (await xp_rows(db, uid)).values()) == xp_before
    weeks = (await authed.get("/api/v1/stats/activity", params={"period": "month", "date": "2026-07-01"})).json()
    by_week = {week["week_start"]: week for week in weeks["weeks"]}
    assert (by_week["2026-07-06"]["planned"], by_week["2026-07-06"]["required"]) == (0, 3)  # as it was judged
    current = (await authed.get("/api/v1/stats/activity", params={"date": "2026-07-15"})).json()["weeks"]
    assert (current[0]["planned"], current[0]["required"]) == (1, 1)  # the current week: the current plan

    await db.execute(sa.delete(WeeklyPlan).where(WeeklyPlan.user_id == uid))
    await db.commit()
    again = streak_of(await run(db, uid, NOON + timedelta(minutes=10)), StreakKind.WORKOUT)
    assert (again.current, again.longest, again.period_required) == (0, 0, 3)


async def test_a_week_is_frozen_the_first_time_it_is_seen_finished(db) -> None:
    """The week 13–19 July ends on Sunday night; the first run on Monday stores its
    requirement with Sunday's row (still in the open window) — a plan edited on Monday
    applies to the new week only, and the row keeps the requirement when it turns final."""
    uid = await _owner(db)
    await configure(db, uid)
    for day in (date(2026, 7, 13), date(2026, 7, 15), date(2026, 7, 17)):
        await workout(db, uid, day)  # 3 workouts: enough without a plan
    monday_night = datetime(2026, 7, 19, 17, 30, tzinfo=UTC)  # Monday 20 July 00:30 in Bangkok
    first = await run(db, uid, monday_night)
    assert streak_of(first, StreakKind.WORKOUT).current == 1
    sunday = await day_state(db, uid, date(2026, 7, 19))
    assert sunday[2]["final"] is False and sunday[2]["week"]["required"] == 3

    for weekday in range(5):  # Monday: "five workouts a week" from now on
        await plan(db, uid, weekday)
    later = await run(db, uid, monday_night + timedelta(hours=10))
    state = streak_of(later, StreakKind.WORKOUT)
    assert (state.current, state.period_required) == (1, 5)  # 13–19 July still counts with its 3
    tuesday = await run(db, uid, monday_night + timedelta(days=1))  # Sunday's row turns final
    assert streak_of(tuesday, StreakKind.WORKOUT).current == 1
    sunday = await day_state(db, uid, date(2026, 7, 19))
    assert sunday[2]["final"] is True and sunday[2]["week"]["required"] == 3


async def test_finished_weeks_from_before_the_fix_are_frozen_once(db) -> None:
    """Final rows without a stored requirement (written before review 8) get the plan
    in force at the first run that sees them — what they were judged with until then —
    and keep it."""
    uid = await _owner(db)
    await configure(db, uid)
    for weeks in range(1, 4):
        await workout(db, uid, date(2026, 7, 13) - timedelta(weeks=weeks))
    await run(db, uid, NOON)
    await db.execute(
        sa.update(DailyScore).where(DailyScore.user_id == uid).values(components=DailyScore.components.op("-")("week"))
    )
    await db.commit()
    await run(db, uid, NOON + timedelta(minutes=5))  # annotates the three finished weeks
    db.expire_all()
    rows = (
        await db.scalars(select(DailyScore).where(DailyScore.user_id == uid, DailyScore.components.has_key("week")))
    ).all()
    assert sorted(row.local_day for row in rows) == [date(2026, 6, 28), date(2026, 7, 5), date(2026, 7, 12)]
    await plan(db, uid, 0)
    state = streak_of(await run(db, uid, NOON + timedelta(minutes=10)), StreakKind.WORKOUT)
    assert (state.current, state.longest) == (0, 0)


async def test_a_quest_claim_judges_finished_days_like_the_engine(db) -> None:
    """Sunday 19 July: water at 2600 ml on Monday–Friday (target 2500) completes «Норма
    воды: 5 дней»; raising the target to 3000 ml afterwards does not un-complete it — on
    read or on claim (both judge the finished days against their own target)."""
    uid = await _owner(db)
    await configure(db, uid)
    week = date(2026, 7, 13)
    for offset in range(5):
        await eat_day(db, uid, week + timedelta(days=offset))
        await water(db, uid, week + timedelta(days=offset), 2600)
    sunday = datetime(2026, 7, 19, 5, 0, tzinfo=UTC)  # 12:00 in Bangkok
    await run(db, uid, sunday)
    row_id = await _quest_row(db, uid, "weekly_water_5", week, week + timedelta(days=6))
    await configure(db, uid, target_water_ml=3000)
    snapshot = await run(db, uid, sunday + timedelta(minutes=5))
    view = next(view for view in snapshot.quests if view.quest.code == "weekly_water_5")
    assert (view.progress, view.row.status) == (5, QuestStatus.COMPLETED)

    from app.db import SessionLocal

    async with SessionLocal() as session:
        ctx = await build_context(session, uid, sunday + timedelta(minutes=10))
        claimed, awarded = await quests.claim(session, ctx, row_id)
        await session.commit()
    assert (claimed.progress, awarded) == (5, 60)


# --------------------------------------------------------------------------- #
# L5 — bounded work per request; the jobs / the CLI finish a backlog in chunks
# --------------------------------------------------------------------------- #
async def populate(db, user_id: uuid.UUID, last: date, days: int) -> None:
    """``days`` dense days up to ``last`` (bulk inserts): food in the zone, water, steps,
    sleep, a journal entry, three tasks, 90 tracked minutes, workouts Mon/Wed/Fri — with
    a few gaps, so streaks break, freeze tokens are spent and milestones are reached."""
    from app.models import HealthDaily, JournalEntry, SleepLog, Task, TimeEntry, WaterLog, WorkoutLog
    from tests.stage8_helpers import local

    rows: dict[Any, list[dict[str, Any]]] = {
        model: [] for model in (MealLog, Task, WaterLog, HealthDaily, SleepLog, JournalEntry, TimeEntry, WorkoutLog)
    }
    for back in range(days):
        day = last - timedelta(days=back)
        if back % 23 in (5, 6, 7):  # a three-day gap now and then
            continue
        for index in range(3):
            rows[MealLog].append(
                {"user_id": user_id, "logged_at": local(day, 8 + 5 * index), "local_day": day, "kcal": 670}
                | {"protein_g": 50, "fiber_g": 10}
            )
            rows[Task].append(
                {"user_id": user_id, "title": f"t {day} {index}", "status": "done", "priority": 2}
                | {"completed_at": local(day, 10 + index)}
            )
        rows[WaterLog].append({"user_id": user_id, "logged_at": local(day, 10), "local_day": day, "amount_ml": 2600})
        rows[HealthDaily].append(
            {"user_id": user_id, "local_day": day, "steps": 9000, "ingested_at": local(day, 21), "source": "manual"}
        )
        rows[SleepLog].append(
            {"user_id": user_id, "local_day": day, "bed_at": local(day, 0), "wake_at": local(day, 7, 30)}
            | {"duration_min": 450}
        )
        rows[JournalEntry].append({"user_id": user_id, "entry_date": day, "note_md": "ok"})
        start = local(day, 9)
        rows[TimeEntry].append(
            {"user_id": user_id, "started_at": start, "ended_at": start + timedelta(minutes=90)}
            | {"duration_s": 5400, "local_day": day}
        )
        if day.weekday() in (0, 2, 4):
            rows[WorkoutLog].append(
                {"user_id": user_id, "started_at": local(day, 18), "ended_at": local(day, 19)}
                | {"local_day": day, "status": "completed"}
            )
    for model, values in rows.items():
        for offset in range(0, len(values), 1000):
            await db.execute(sa.insert(model), values[offset : offset + 1000])
    await db.commit()


async def derived_state(db, user_id: uuid.UUID) -> dict[str, Any]:
    """Everything the engine writes, without ids and wall-clock timestamps."""
    from app.models import Streak

    db.expire_all()
    scores = {
        row.local_day: (row.score, row.components, row.weights, row.xp_earned, row.streak_days, row.computed_at)
        for row in (await db.scalars(select(DailyScore).where(DailyScore.user_id == user_id))).all()
    }
    xp = {
        row.dedupe_key: (row.kind, row.points, row.local_day, row.awarded_on, row.reason, row.meta)
        for row in (await db.scalars(select(XpEvent).where(XpEvent.user_id == user_id))).all()
    }
    streaks = {
        row.kind: (
            row.current_count,
            row.longest_count,
            row.last_event_date,
            row.broken_on,
            row.freeze_tokens,
            row.freeze_used_dates,
            row.total_events,
        )
        for row in (await db.scalars(select(Streak).where(Streak.user_id == user_id))).all()
    }
    badges = await unlocked_codes(db, user_id)
    quest_rows = (
        await db.execute(
            select(Quest.code, UserQuest.period_start, UserQuest.status, UserQuest.progress)
            .join(Quest, Quest.id == UserQuest.quest_id)
            .where(UserQuest.user_id == user_id)
        )
    ).all()
    return {"scores": scores, "xp": xp, "streaks": streaks, "badges": badges, "quests": sorted(map(tuple, quest_rows))}


async def wipe_derived(db, user_id: uuid.UUID) -> None:
    from app.models import Streak

    for model in (XpEvent, DailyScore, Streak, UserAchievement, UserQuest):
        await db.execute(sa.delete(model).where(model.user_id == user_id))
    await db.commit()


async def test_a_request_finalises_at_most_sixty_pending_days_oldest_first(owner, db) -> None:
    """Adapted from probe P1: the first engine run of a long history is bounded."""
    uid = owner.id
    await configure(db, uid)
    await populate(db, uid, TODAY, 200)
    first = await run(db, uid, NOON)
    assert (first.finalised, first.pending_left) == (60, 198 - 60)  # 200 days, today and yesterday open
    db.expire_all()
    final_days = sorted(
        (
            await db.scalars(
                select(DailyScore.local_day).where(
                    DailyScore.user_id == uid, DailyScore.components["final"].astext == "true"
                )
            )
        ).all()
    )
    assert final_days == [TODAY - timedelta(days=199) + timedelta(days=offset) for offset in range(60)]
    assert not [row for row in (await xp_rows(db, uid)).values() if row.kind == XpKind.STREAK_BONUS]  # waits

    calls: list[date] = []
    original = score.compute_day_score

    def counting(metrics, ctx, *, provisional):
        calls.append(metrics.day)
        return original(metrics, ctx, provisional=provisional)

    from app.services.gamification import engine, streaks

    engine.score.compute_day_score = counting  # the stage-7 reminder check is bounded too
    try:
        await streaks.at_risk(db, uid, NOON + timedelta(hours=8))
    finally:
        engine.score.compute_day_score = original
    assert len(calls) <= engine.PENDING_PER_RUN + 2


async def test_chunked_backfill_gives_exactly_the_rows_of_one_big_run(owner, db) -> None:
    """The jobs finish a backlog in chunks of 60 days (oldest first, each its own
    transaction): scores, XP, streaks, badges and quests come out identical to finalising
    everything at once (the pre-review behaviour)."""
    from app.db import SessionLocal
    from app.services.gamification import engine

    uid = owner.id
    await configure(db, uid)
    await populate(db, uid, TODAY, 200)
    async with SessionLocal() as session:
        whole = await engine.sync_user(session, uid, NOON, pending_limit=None)
        await session.commit()
    assert whole is not None and (whole.finalised, whole.pending_left) == (198, 0)
    at_once = await derived_state(db, uid)
    assert any(key.startswith("streak:overall:") for key in at_once["xp"])  # milestones were reached
    assert any(state[5] for state in at_once["streaks"].values())  # freeze tokens were spent

    await wipe_derived(db, uid)
    snapshot, finalised = await engine.sync_fully(uid, NOON)
    assert snapshot is not None and (finalised, snapshot.pending_left) == (198, 0)
    assert await derived_state(db, uid) == at_once


async def test_the_jobs_finish_a_backlog(owner, db) -> None:
    from app.services.gamification import engine

    uid = owner.id
    await configure(db, uid)
    await populate(db, uid, TODAY, 150)
    assert await engine.run_for_all_users(("days",), NOON) == 1
    db.expire_all()
    stored = await score.load_flags(db, uid, TODAY - timedelta(days=200), TODAY)
    assert len(stored) == 150 and sum(1 for flag in stored.values() if flag.is_final(TODAY)) == 148
    again = await run(db, uid, NOON + timedelta(minutes=15))
    assert (again.finalised, again.pending_left) == (0, 0)


def backfill_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own interpreter and module
        [sys.executable, "-m", "app.cli", "backfill-scores", *args],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "NO_COLOR": "1"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


async def test_cli_backfill_scores(owner, db) -> None:
    """``python -m app.cli backfill-scores`` as a real process (the real clock)."""
    from tests.stage8_helpers import BANGKOK

    uid = owner.id
    await configure(db, uid)
    today = datetime.now(BANGKOK).date()
    await populate(db, uid, today, 130)
    bad = backfill_cli("--user-id", "nope")
    assert bad.returncode == 1 and "--user-id must be a UUID" in bad.stdout + bad.stderr
    unknown = backfill_cli("--user-id", str(uuid.uuid4()))
    assert unknown.returncode == 1 and "no such active account" in unknown.stdout + unknown.stderr
    done = backfill_cli("--user-id", str(uid))
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{uid}: 128 day(s) finalised" in done.stdout and "1 account(s) up to date" in done.stdout
    db.expire_all()
    final = await db.scalar(
        select(sa.func.count())
        .select_from(DailyScore)
        .where(DailyScore.user_id == uid, DailyScore.components["final"].astext == "true")
    )
    assert final == 128
    again = backfill_cli()
    assert again.returncode == 0 and f"{uid}: 0 day(s) finalised" in again.stdout
