"""Stage 8: quests — deterministic selection of what makes sense, rotation, progress,
the idempotent claim, 409s and expiry."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.core.errors import Conflict
from app.models import Quest, QuestKind, QuestStatus, UserQuest
from app.services.day_metrics import DayMetrics
from app.services.gamification.context import context_for
from app.services.gamification.quests import Facts, claim, eligible, pick, progress_of, target_of
from tests.stage6_helpers import other_user
from tests.stage8_helpers import (
    BANGKOK,
    NOON,
    TODAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    journal,
    meal,
    owner_id,
    run,
    xp_total,
)

pytestmark = pytest.mark.anyio
G = "/api/v1/gamification"


@pytest.fixture
async def uid(owner, db):
    await configure(db, owner.id)
    return owner.id


async def seeded(db) -> dict[str, Quest]:
    from app.seeds.gamification import seed_gamification

    await seed_gamification(db)
    await db.commit()
    return {quest.code: quest for quest in (await db.scalars(select(Quest))).all()}


async def only(db, *codes: str) -> None:
    """Leave only these daily quests active (so the selection is known)."""
    await db.execute(update(Quest).where(Quest.kind == QuestKind.DAILY).values(is_active=False))
    await db.execute(update(Quest).where(Quest.code.in_(codes)).values(is_active=True))
    await db.commit()


async def mine(db, user_id) -> list[UserQuest]:
    db.expire_all()
    return list((await db.scalars(select(UserQuest).where(UserQuest.user_id == user_id))).all())


def test_selection_is_deterministic_per_user_and_period() -> None:
    pool = [Quest(code=f"daily_{index}", kind=QuestKind.DAILY) for index in range(10)]
    user = uuid.UUID("01a17e00-0000-7000-8000-000000000001")
    first = [quest.code for quest in pick(pool, user, TODAY, 3)]
    assert first == [quest.code for quest in pick(list(reversed(pool)), user, TODAY, 3)]  # order-independent
    assert len(first) == 3 == len(set(first))
    week = {tuple(quest.code for quest in pick(pool, user, TODAY + timedelta(days=n), 3)) for n in range(14)}
    assert len(week) > 1  # days differ
    other = uuid.UUID("01a17e00-0000-7000-8000-000000000002")
    users = {tuple(quest.code for quest in pick(pool, u, TODAY, 3)) for u in (user, other)}
    assert len(users) == 2


async def test_only_quests_that_make_sense_are_eligible(uid, db) -> None:
    pool = await seeded(db)
    settings = await configure(db, uid)
    ctx = context_for(settings, uid, NOON)
    daily = [quest for quest in pool.values() if quest.kind == QuestKind.DAILY]
    weekly = [quest for quest in pool.values() if quest.kind == QuestKind.WEEKLY]

    rest_day = Facts(open_tasks=0, open_p1=0, uses_timer=False, recent_done=0, today_plan="rest", week_required=0)
    assert {quest.code for quest in daily if eligible(quest, ctx, rest_day)} == {
        "daily_meals_3",
        "daily_water",
        "daily_protein",
        "daily_steps",
        "daily_review",
        "daily_sleep",
    }  # no workout quest on a planned rest day, no task/timer quests without tasks/timer
    assert "weekly_workouts" not in {quest.code for quest in weekly if eligible(quest, ctx, rest_day)}

    busy = Facts(open_tasks=6, open_p1=1, uses_timer=True, recent_done=9, today_plan="workout", week_required=3)
    assert all(eligible(quest, ctx, busy) for quest in [*daily, *weekly])

    bare = context_for(await configure(db, uid, target_water_ml=None, target_protein_g=None, target_steps=0), uid, NOON)
    assert not {"daily_water", "daily_protein", "daily_steps"} & {
        quest.code for quest in daily if eligible(quest, bare, busy)
    }


async def test_rotation_is_idempotent_and_past_periods_expire(uid, db) -> None:
    await run(db, uid, NOON)
    rows = await mine(db, uid)
    daily = [row for row in rows if row.period_start == row.period_end == TODAY]
    weekly = [row for row in rows if row.period_start == date(2026, 7, 13)]
    assert (len(daily), len(weekly)) == (3, 2)
    assert all(row.period_end == date(2026, 7, 19) for row in weekly)

    await run(db, uid, NOON + timedelta(minutes=15))
    assert {row.id for row in await mine(db, uid)} == {row.id for row in rows}  # idempotent

    await run(db, uid, NOON + timedelta(days=1))
    rows = await mine(db, uid)
    assert {row.status for row in rows if row.period_end == TODAY} == {QuestStatus.EXPIRED}
    assert len([row for row in rows if row.period_start == TODAY + timedelta(days=1)]) == 3
    assert len([row for row in rows if row.period_start == date(2026, 7, 13)]) == 2  # same week, same quests

    await run(db, uid, NOON + timedelta(days=5))  # Monday 20 July: a new week
    rows = await mine(db, uid)
    assert {row.status for row in rows if row.period_start == date(2026, 7, 13)} == {QuestStatus.EXPIRED}
    assert (
        len([row for row in rows if row.period_start == date(2026, 7, 20) and row.period_end > row.period_start]) == 2
    )


async def test_progress_completion_claim_and_expiry(uid, db) -> None:
    await seeded(db)
    await only(db, "daily_review", "daily_sleep", "daily_meals_3")
    snapshot = await run(db, uid, NOON)
    by_code = {view.quest.code: view for view in snapshot.quests}
    assert {"daily_review", "daily_sleep", "daily_meals_3"} <= set(by_code)
    assert by_code["daily_review"].row.status == QuestStatus.ACTIVE

    await journal(db, uid, TODAY)
    await meal(db, uid, TODAY, 500)
    snapshot = await run(db, uid, NOON + timedelta(minutes=5))
    by_code = {view.quest.code: view for view in snapshot.quests}
    review = by_code["daily_review"]
    assert (review.progress, review.target, review.row.status, review.claimable) == (1, 1, QuestStatus.COMPLETED, True)
    assert (by_code["daily_meals_3"].progress, by_code["daily_meals_3"].target) == (1, 3)

    # completed but never claimed → expires with the day; claiming then is a 409
    await run(db, uid, NOON + timedelta(days=1))
    settings = await configure(db, uid)
    with pytest.raises(Conflict) as expired:
        await claim(db, context_for(settings, uid, NOON + timedelta(days=1)), review.row.id)
    assert expired.value.code == "quest_expired"
    await db.rollback()


async def test_claim_over_http_is_idempotent_and_needs_completion(authed: httpx.AsyncClient, db) -> None:
    from datetime import datetime

    user_id = await owner_id(db)
    await configure(db, user_id)
    await seeded(db)
    await only(db, "daily_review", "daily_sleep", "daily_meals_3")
    quests = (await authed.get(f"{G}/today")).json()["quests"]
    by_code = {quest["code"]: quest for quest in quests}
    assert by_code["daily_review"]["status"] == "active" and by_code["daily_review"]["claimable"] is False

    not_done = await authed.post(f"{G}/quests/{by_code['daily_sleep']['id']}/claim")
    assert (not_done.status_code, not_done.json()["error"]["code"]) == (409, "quest_not_completed")

    await journal(db, user_id, datetime.now(BANGKOK).date())
    before = await xp_total(db, user_id)
    first = await authed.post(f"{G}/quests/{by_code['daily_review']['id']}/claim")
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["xp_awarded"], body["quest"]["status"], body["quest"]["claimable"]) == (15, "claimed", False)
    assert body["quest"]["claimed_at"] is not None
    again = await authed.post(f"{G}/quests/{by_code['daily_review']['id']}/claim")
    assert again.status_code == 200 and again.json()["xp_awarded"] == 0
    assert again.json()["quest"]["id"] == body["quest"]["id"]
    assert await xp_total(db, user_id) - before == 15  # paid once

    async with other_user() as stranger:
        theirs = await stranger.post(f"{G}/quests/{by_code['daily_review']['id']}/claim")
        assert (theirs.status_code, theirs.json()["error"]["code"]) == (404, "quest_not_found")
    assert (await authed.post(f"{G}/quests/{uuid.uuid4()}/claim")).status_code == 404
    assert (await authed.post(f"{G}/quests/not-a-uuid/claim")).status_code == 422


async def test_targets_and_floor_gated_progress(uid, db) -> None:
    pool = await seeded(db)
    ctx = context_for(await configure(db, uid), uid, NOON)
    assert target_of(pool["weekly_workouts"], ctx, 2) == 2  # the plan says two this week
    assert target_of(pool["weekly_workouts"], ctx, 3) == 3  # no plan → 3
    assert target_of(pool["daily_water"], ctx, 3) == 2500 and target_of(pool["daily_steps"], ctx, 3) == 8000

    low = {TODAY: DayMetrics(day=TODAY, kcal=1300, meals=3, protein_g=160)}
    enough = {TODAY: DayMetrics(day=TODAY, kcal=1900, meals=3, protein_g=160)}
    protein = pool["daily_protein"]
    assert progress_of(protein, TODAY, TODAY, low, ctx) == 0  # below the floor: no intake-tied credit
    assert progress_of(protein, TODAY, TODAY, enough, ctx) == 1
    week = {
        TODAY - timedelta(days=offset): DayMetrics(
            day=TODAY - timedelta(days=offset), tracked_seconds=3600, running_seconds=600 if offset == 0 else 0
        )
        for offset in range(3)
    }
    assert progress_of(pool["weekly_focus_10h"], date(2026, 7, 13), date(2026, 7, 19), week, ctx) == 190
