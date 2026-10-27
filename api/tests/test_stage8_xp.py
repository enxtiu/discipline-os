"""Stage 8: the XP ledger — exact points, caps, the open window, frozen days,
permanent awards, multipliers, levels and the migration's new kinds."""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.models import DailyScore, MealLog, Task, UserAchievement, XpEvent, XpKind
from app.services.gamification.levels import LEVELS, MAX_LEVEL, level_for, min_total_xp
from app.services.gamification.xp import DAY_KINDS
from tests.stage8_helpers import (
    NOON,
    TODAY,
    YESTERDAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    done_task,
    eat_day,
    exercise,
    journal,
    local,
    meal,
    project_done,
    record,
    run,
    sleep,
    steps,
    tracked,
    water,
    weigh_in,
    workout,
    xp_rows,
)

pytestmark = pytest.mark.anyio
DAYS = ("days",)


@pytest.fixture
async def uid(owner, db):
    await configure(db, owner.id)
    return owner.id


def day_xp(rows: dict[str, XpEvent], day) -> dict[str, int]:
    return {key: row.points for key, row in rows.items() if row.local_day == day and row.kind in DAY_KINDS}


async def test_every_day_award_with_its_exact_points(uid, db) -> None:
    meals = [await meal(db, uid, YESTERDAY, 2000 / 3, protein=50, fiber=10, hh=8 + 4 * i) for i in range(3)]
    await water(db, uid, YESTERDAY, 2500)
    await steps(db, uid, YESTERDAY, 9000)
    lift = await workout(db, uid, YESTERDAY)
    press = await exercise(db)
    await record(db, uid, lift, press, "weight", YESTERDAY)
    tasks = {p: await done_task(db, uid, local(YESTERDAY, 9 + p), priority=p) for p in (1, 2, 3, 4)}
    sub = await done_task(db, uid, local(YESTERDAY, 15), priority=1, parent_id=tasks[1])
    project = await project_done(db, uid, local(YESTERDAY, 16))
    await tracked(db, uid, YESTERDAY, 125)
    await journal(db, uid, YESTERDAY)
    await sleep(db, uid, YESTERDAY, 450)
    await weigh_in(db, uid, YESTERDAY)

    await run(db, uid, NOON, DAYS)
    got = day_xp(await xp_rows(db, uid), YESTERDAY)
    iso = YESTERDAY.isoformat()
    assert got == {
        **{f"meal:{meal_id}": 5 for meal_id in meals},
        f"protein:{iso}": 20,
        f"fiber:{iso}": 10,
        f"water:{iso}": 10,
        f"kcal_zone:{iso}": 30,
        f"steps:{iso}": 15,
        f"workout:{lift}": 40,
        f"pr:{lift}:{press}:weight": 15,
        f"task:{tasks[1]}": 12,
        f"task:{tasks[2]}": 9,
        f"task:{tasks[3]}": 6,
        f"task:{tasks[4]}": 4,
        f"task:{sub}": 2,
        f"project:{project}": 60,
        f"timer:{iso}": 12,  # 125 finished minutes → 12
        f"review:{iso}": 15,
        f"sleep:{iso}": 5,
        f"weight:{iso}": 5,
    }
    rows = await xp_rows(db, uid)
    assert rows[f"pr:{lift}:{press}:weight"].reason == "personal_record"
    assert rows[f"steps:{iso}"].kind == XpKind.STEPS_TARGET
    assert all(row.points >= 0 for row in rows.values())


async def test_kcal_in_zone_only_for_finished_days(uid, db) -> None:
    await eat_day(db, uid, TODAY, 2000)
    await run(db, uid, NOON, DAYS)
    today = day_xp(await xp_rows(db, uid), TODAY)
    assert f"kcal_zone:{TODAY}" not in today  # the day is not over
    assert today[f"protein:{TODAY}"] == 20 and today[f"fiber:{TODAY}"] == 10

    await run(db, uid, NOON + timedelta(days=1), DAYS)  # tomorrow: that day is finished
    assert day_xp(await xp_rows(db, uid), TODAY)[f"kcal_zone:{TODAY}"] == 30


async def test_nothing_rewards_intake_below_the_floor(uid, db) -> None:
    await eat_day(db, uid, YESTERDAY, 1400, protein=150, fiber=30)  # floor 1500 (a man)
    await water(db, uid, YESTERDAY, 2500)
    await run(db, uid, NOON, DAYS)
    got = day_xp(await xp_rows(db, uid), YESTERDAY)
    iso = YESTERDAY.isoformat()
    assert f"kcal_zone:{iso}" not in got
    assert f"protein:{iso}" not in got and f"fiber:{iso}" not in got  # intake-tied, below the floor
    assert sum(1 for key in got if key.startswith("meal:")) == 3  # logging is always rewarded
    assert got[f"water:{iso}"] == 10
    stored = await db.scalar(select(DailyScore).where(DailyScore.user_id == uid, DailyScore.local_day == YESTERDAY))
    kcal = next(item for item in stored.components["items"] if item["key"] == "kcal")
    assert (kcal["value"], kcal["detail"]["state"], stored.components["credit"]) == (0.0, "below_floor", False)


async def test_sync_twice_gives_the_same_rows(uid, db) -> None:
    await eat_day(db, uid, YESTERDAY)
    await eat_day(db, uid, TODAY)
    await done_task(db, uid, local(TODAY, 9), priority=1)
    await run(db, uid, NOON)
    first = {key: (row.id, row.points, row.local_day) for key, row in (await xp_rows(db, uid)).items()}
    await run(db, uid, NOON + timedelta(minutes=15))
    second = {key: (row.id, row.points, row.local_day) for key, row in (await xp_rows(db, uid)).items()}
    assert first == second and len(first) > 10


async def test_deletes_drop_xp_in_the_open_window_but_frozen_days_never_change(uid, db) -> None:
    old = TODAY - timedelta(days=3)
    old_meal = await meal(db, uid, old, 700)
    old_task = await done_task(db, uid, local(old, 10), priority=1)
    new_meal = await meal(db, uid, YESTERDAY, 700)
    new_task = await done_task(db, uid, local(YESTERDAY, 10), priority=1)
    await run(db, uid, NOON, DAYS)
    rows = await xp_rows(db, uid)
    assert {f"meal:{old_meal}", f"task:{old_task}", f"meal:{new_meal}", f"task:{new_task}"} <= set(rows)
    frozen = await db.scalar(select(DailyScore).where(DailyScore.user_id == uid, DailyScore.local_day == old))
    frozen_state = (frozen.score, frozen.computed_at, frozen.components)

    await db.execute(update(MealLog).where(MealLog.id.in_([old_meal, new_meal])).values(is_deleted=True))
    await db.execute(delete(Task).where(Task.id.in_([old_task, new_task])))
    await db.commit()
    await run(db, uid, NOON + timedelta(hours=1), DAYS)
    rows = await xp_rows(db, uid)
    assert f"meal:{new_meal}" not in rows and f"task:{new_task}" not in rows  # the open window follows the data
    assert f"meal:{old_meal}" in rows and f"task:{old_task}" in rows  # a frozen day keeps its XP
    db.expire_all()
    again = await db.scalar(select(DailyScore).where(DailyScore.user_id == uid, DailyScore.local_day == old))
    assert (again.score, again.computed_at, again.components) == frozen_state


async def test_a_day_is_finalised_once_after_its_grace_day(uid, db) -> None:
    """Yesterday's late meal still counts; once the day leaves the window it is frozen."""
    await run(db, uid, NOON, DAYS)  # creates the rows for today
    late = await meal(db, uid, TODAY, 650, hh=23, mm=50)
    await run(db, uid, NOON + timedelta(days=2), DAYS)  # two days later: TODAY is finalised now
    rows = await xp_rows(db, uid)
    assert f"meal:{late}" in rows
    row = await db.scalar(select(DailyScore).where(DailyScore.user_id == uid, DailyScore.local_day == TODAY))
    assert row.components["provisional"] is False  # recomputed as a finished day
    await db.execute(update(MealLog).where(MealLog.id == late).values(is_deleted=True))
    await db.commit()
    await run(db, uid, NOON + timedelta(days=3), DAYS)
    assert f"meal:{late}" in await xp_rows(db, uid)  # frozen


async def test_a_meal_moved_between_window_days_moves_its_award(uid, db) -> None:
    moved = await meal(db, uid, YESTERDAY, 700)
    await run(db, uid, NOON, DAYS)
    first = (await xp_rows(db, uid))[f"meal:{moved}"]
    await db.execute(update(MealLog).where(MealLog.id == moved).values(local_day=TODAY, logged_at=local(TODAY, 9)))
    await db.commit()
    await run(db, uid, NOON + timedelta(minutes=30), DAYS)
    second = (await xp_rows(db, uid))[f"meal:{moved}"]
    assert (second.id, second.local_day, second.points) == (first.id, TODAY, 5)


async def test_daily_caps(uid, db) -> None:
    for index in range(7):
        await meal(db, uid, YESTERDAY, 300, hh=7 + index)
    workouts = [await workout(db, uid, YESTERDAY, hh=hh) for hh in (7, 12, 18)]
    press = await exercise(db)
    for kind in ("weight", "est_1rm", "volume", "reps"):
        await record(db, uid, workouts[0], press, kind, YESTERDAY)
    await record(db, uid, workouts[1], press, "weight", YESTERDAY)
    p1 = [await done_task(db, uid, local(YESTERDAY, 8, index), priority=1) for index in range(9)]  # 108
    p2 = await done_task(db, uid, local(YESTERDAY, 9), priority=2)  # 117
    p3 = await done_task(db, uid, local(YESTERDAY, 10), priority=3)  # only 3 left
    p4 = await done_task(db, uid, local(YESTERDAY, 11), priority=4)  # nothing left
    await tracked(db, uid, YESTERDAY, 600)

    await run(db, uid, NOON, DAYS)
    got = day_xp(await xp_rows(db, uid), YESTERDAY)
    assert sum(1 for key in got if key.startswith("meal:")) == 5
    assert sorted(key for key in got if key.startswith("workout:")) == sorted(f"workout:{w}" for w in workouts[:2])
    assert sum(1 for key in got if key.startswith("pr:")) == 3
    task_points = {key: points for key, points in got.items() if key.startswith("task:")}
    assert sum(task_points.values()) == 120
    assert all(task_points[f"task:{task}"] == 12 for task in p1)
    assert (task_points[f"task:{p2}"], task_points[f"task:{p3}"]) == (9, 3)
    assert f"task:{p4}" not in task_points
    assert got[f"timer:{YESTERDAY}"] == 48


async def test_multipliers_scale_categories_and_zero_stores_nothing(uid, db) -> None:
    await configure(db, uid, xp_weights={"xp_multipliers": {"nutrition": 2, "planner": 0, "review": 1.5}})
    meal_id = await meal(db, uid, YESTERDAY, 700)
    task = await done_task(db, uid, local(YESTERDAY, 9), priority=1)
    await journal(db, uid, YESTERDAY)
    await run(db, uid, NOON, DAYS)
    rows = await xp_rows(db, uid)
    assert rows[f"meal:{meal_id}"].points == 10
    assert f"task:{task}" not in rows  # 0 × 12: no row
    assert rows[f"review:{YESTERDAY}"].points == 23  # 15 × 1.5 = 22.5 → 23 (half up)


async def test_xp_is_never_negative(uid, db) -> None:
    with pytest.raises(IntegrityError):
        db.add(
            XpEvent(
                user_id=uid,
                kind=XpKind.MANUAL,
                points=-5,
                awarded_on=TODAY,
                local_day=TODAY,
                dedupe_key="manual:negative",
            )
        )
        await db.commit()
    await db.rollback()


async def test_permanent_awards_survive_their_source(uid, db) -> None:
    meal_id = await meal(db, uid, YESTERDAY, 700)
    await run(db, uid, NOON)
    rows = await xp_rows(db, uid)
    assert rows["achievement:meals_1"].points == 10 and rows["achievement:meals_1"].kind == XpKind.ACHIEVEMENT_UNLOCKED
    await db.execute(update(MealLog).where(MealLog.id == meal_id).values(is_deleted=True))
    await db.commit()
    await run(db, uid, NOON + timedelta(minutes=15))
    rows = await xp_rows(db, uid)
    assert f"meal:{meal_id}" not in rows
    assert "achievement:meals_1" in rows  # never revoked
    assert await db.scalar(select(sa.func.count()).select_from(UserAchievement)) >= 1


async def test_the_migration_admits_the_two_new_kinds_only(uid, db) -> None:
    for kind in ("steps_target", "achievement_unlocked"):
        await db.execute(
            sa.text(
                "INSERT INTO xp_events (id, user_id, kind, points, awarded_on, local_day, dedupe_key, created_at, "
                "updated_at) VALUES (gen_random_uuid(), :u, :k, 1, current_date, current_date, :key, now(), now())"
            ),
            {"u": uid, "k": kind, "key": f"raw:{kind}"},
        )
    await db.commit()
    with pytest.raises(IntegrityError):
        await db.execute(
            sa.text(
                "INSERT INTO xp_events (id, user_id, kind, points, awarded_on, local_day, dedupe_key, created_at, "
                "updated_at) VALUES (gen_random_uuid(), :u, 'bogus', 1, current_date, current_date, 'raw:x', now(), now())"
            ),
            {"u": uid},
        )
    await db.rollback()


def test_level_curve_and_boundaries() -> None:
    assert len(LEVELS) == MAX_LEVEL == 30
    assert [row.min_xp for row in LEVELS[:6]] == [0, 100, 300, 580, 920, 1310]
    assert LEVELS[-1].min_xp == 21870 == min_total_xp(30)
    assert all(b.min_xp > a.min_xp for a, b in pairwise(LEVELS))
    assert all(min_total_xp(level) == round(100 * (level - 1) ** 1.6 / 10) * 10 for level in range(2, 31))
    assert len({row.title_ru for row in LEVELS}) == 30 and len({row.title_en for row in LEVELS}) == 30
    assert (LEVELS[0].title_ru, LEVELS[-1].title_ru) == ("Новичок", "Легенда")
    for total, level in ((0, 1), (99, 1), (100, 2), (299, 2), (300, 3), (21869, 29), (21870, 30), (10**7, 30)):
        assert level_for(total).current.level == level, total
    assert level_for(200).progress == 0.5
    top = level_for(30000)
    assert top.next is None and top.progress == 1.0
    assert level_for(-50).current.level == 1
