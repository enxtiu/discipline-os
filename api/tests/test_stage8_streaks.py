"""Stage 8: streaks — daily kinds, the weekly plan-aware workout streak, freeze tokens,
longest kept, today counting once met, at_risk after 18:00 — and the stage-7 evening
reminder for streaks at risk (never about eating less)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Reminder, ReminderKind, ReminderStatus, Streak, StreakKind
from app.services.day_metrics import WeekPlan
from app.services.gamification.streaks import (
    MAX_FREEZE_TOKENS,
    StreakState,
    at_risk,
    simulate_daily,
    simulate_weekly,
)
from tests.stage8_helpers import (
    NOON,
    TODAY,
    YESTERDAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    eat_day,
    journal,
    local,
    meal,
    plan,
    run,
    sleep,
    steps,
    water,
    workout,
    xp_rows,
)
from tests.test_stage7 import (  # the stage-7 push doubles (fixtures included)
    FORBIDDEN,
    BrowserKeys,
    dispatch,
    endpoint,
    push_service,  # noqa: F401
    subscribe,
    vapid,  # noqa: F401
)

pytestmark = pytest.mark.anyio


def days(first: date, last: date) -> set[date]:
    return {first + timedelta(days=offset) for offset in range((last - first).days + 1)}


# --------------------------------------------------------------------------- #
# Daily streaks (pure)
# --------------------------------------------------------------------------- #
def test_today_counts_once_met_and_is_never_a_miss_while_it_lasts() -> None:
    met = simulate_daily(StreakKind.WATER, days(date(2026, 7, 11), TODAY), TODAY, 1, 12)
    assert (met.current, met.longest, met.today_met, met.at_risk) == (5, 5, True, False)
    pending = simulate_daily(StreakKind.WATER, days(date(2026, 7, 10), YESTERDAY), TODAY, 1, 12)
    assert (pending.current, pending.today_met, pending.at_risk, pending.broken_on) == (5, False, False, None)


@pytest.mark.parametrize(("hour", "risky"), [(9, False), (17, False), (18, True), (23, True)])
def test_at_risk_from_18_local(hour: int, risky: bool) -> None:
    state = simulate_daily(StreakKind.TASKS, days(date(2026, 7, 12), YESTERDAY), TODAY, 1, hour)
    assert state.at_risk is risky
    nothing = simulate_daily(StreakKind.TASKS, set(), TODAY, 1, hour)
    assert nothing.at_risk is False  # no streak, nothing to lose


def test_a_missed_day_uses_a_freeze_token_and_the_week_start_refills_it() -> None:
    met = days(date(2026, 7, 6), date(2026, 7, 8)) | days(date(2026, 7, 10), TODAY)  # Thursday 9 July missed
    state = simulate_daily(StreakKind.MEAL_LOGGING, met, TODAY, 1, 12)
    assert state.freeze_used == [date(2026, 7, 9)]
    assert state.current == 3 + 6  # the saved day keeps the streak but does not add to it
    assert state.freeze_tokens == 1  # 1 − 1 on Thursday, + 1 on Monday 13 July
    assert state.broken_on is None


def test_two_misses_with_one_token_break_the_streak_and_longest_is_kept() -> None:
    met = days(date(2026, 7, 6), date(2026, 7, 7)) | days(date(2026, 7, 10), TODAY)
    state = simulate_daily(StreakKind.MEAL_LOGGING, met, TODAY, 1, 12)
    assert state.freeze_used == [date(2026, 7, 8)]
    assert state.broken_on == date(2026, 7, 9)
    assert (state.current, state.longest) == (6, 6)
    assert state.run_start == date(2026, 7, 10)


def test_freeze_tokens_are_capped_at_two() -> None:
    state = simulate_daily(StreakKind.REVIEW, days(date(2026, 6, 1), TODAY), TODAY, 1, 12)
    assert state.freeze_tokens == MAX_FREEZE_TOKENS == 2
    sunday_weeks = simulate_daily(StreakKind.REVIEW, days(date(2026, 7, 5), TODAY), TODAY, 0, 12)
    assert sunday_weeks.freeze_tokens == 2  # +1 on Sunday 12 July (week_start = Sunday)


def test_milestones_are_tracked_for_the_current_run() -> None:
    state = simulate_daily(StreakKind.OVERALL, days(date(2026, 7, 1), TODAY), TODAY, 1, 12)
    assert state.current == 15
    assert state.reached == {3: date(2026, 7, 3), 7: date(2026, 7, 7), 14: date(2026, 7, 14)}
    assert state.next_milestone == 30


# --------------------------------------------------------------------------- #
# The weekly, plan-aware workout streak (pure)
# --------------------------------------------------------------------------- #
WEEKS = [date(2026, 6, 22), date(2026, 6, 29), date(2026, 7, 6), date(2026, 7, 13)]


def plans(planned: int, *, has_plan: bool = True) -> dict[date, WeekPlan]:
    return {week: WeekPlan(week, planned, has_plan) for week in WEEKS}


def test_planned_weeks_count_and_rest_days_never_break_anything() -> None:
    done = {WEEKS[0]: 3, WEEKS[1]: 3, WEEKS[2]: 3, WEEKS[3]: 1}  # Mon/Wed/Fri plan; this week Monday so far
    state = simulate_weekly(done, plans(3), TODAY, 1, 21)
    assert (state.unit, state.current, state.longest) == ("week", 3, 3)
    assert (state.today_met, state.period_done, state.period_required) == (False, 1, 3)
    assert state.at_risk is False  # Wednesday: the week is not over
    assert state.broken_on is None and state.freeze_used == []
    state = simulate_weekly({**done, WEEKS[3]: 3}, plans(3), TODAY, 1, 12)
    assert (state.current, state.today_met) == (4, True)


def test_a_short_week_uses_a_token_and_a_rest_week_is_neutral() -> None:
    weekly = plans(3)
    weekly[WEEKS[2]] = WeekPlan(WEEKS[2], 0, True)  # a deload week planned as rest
    done = {WEEKS[0]: 3, WEEKS[1]: 2, WEEKS[2]: 0, WEEKS[3]: 0}
    state = simulate_weekly(done, weekly, TODAY, 1, 12)
    assert state.freeze_used == [WEEKS[1]]
    assert state.current == 1 and state.broken_on is None  # the rest week neither counts nor breaks


def test_without_a_plan_three_workouts_make_a_week() -> None:
    done = {WEEKS[1]: 3, WEEKS[2]: 2, WEEKS[3]: 0}
    state = simulate_weekly(done, plans(0, has_plan=False), TODAY, 1, 12)
    assert state.period_required == 3
    assert state.freeze_used == [WEEKS[2]] and state.current == 1


def test_weekly_at_risk_only_on_the_last_evening_of_the_week() -> None:
    done = {WEEKS[2]: 3, WEEKS[3]: 2}
    sunday = date(2026, 7, 19)
    assert simulate_weekly(done, plans(3), sunday, 1, 18).at_risk is True
    assert simulate_weekly(done, plans(3), sunday, 1, 17).at_risk is False
    assert simulate_weekly(done, plans(3), date(2026, 7, 18), 1, 22).at_risk is False


# --------------------------------------------------------------------------- #
# Integration: rows, credit, milestones, longest kept
# --------------------------------------------------------------------------- #
@pytest.fixture
async def uid(owner, db):
    await configure(db, owner.id)
    return owner.id


def by_kind(snapshot) -> dict[str, StreakState]:
    return {state.kind.value: state for state in snapshot.streaks}


async def good_day(db, uid, day: date) -> None:
    await eat_day(db, uid, day, 2000)
    await water(db, uid, day, 2500)
    await steps(db, uid, day, 9000)
    await journal(db, uid, day)
    await sleep(db, uid, day, 450)


async def test_rows_follow_the_data_and_longest_never_decreases(uid, db) -> None:
    for back in range(1, 6):
        await meal(db, uid, TODAY - timedelta(days=back), 500)
    snapshot = await run(db, uid, NOON)
    meals = by_kind(snapshot)["meal_logging"]
    assert (meals.current, meals.today_met) == (5, False)
    rows = {row.kind: row for row in (await db.scalars(select(Streak).where(Streak.user_id == uid))).all()}
    assert set(rows) == set(StreakKind)
    assert (rows[StreakKind.MEAL_LOGGING].current_count, rows[StreakKind.MEAL_LOGGING].longest_count) == (5, 5)

    await meal(db, uid, TODAY, 500)  # today counts as soon as it is met
    assert by_kind(await run(db, uid, NOON + timedelta(minutes=5)))["meal_logging"].current == 6

    rows[StreakKind.MEAL_LOGGING].longest_count = 40  # an older, longer run (history beyond the window)
    await db.commit()
    snapshot = await run(db, uid, NOON + timedelta(minutes=10))
    assert by_kind(snapshot)["meal_logging"].longest == 40
    db.expire_all()
    stored = await db.scalar(select(Streak).where(Streak.user_id == uid, Streak.kind == StreakKind.MEAL_LOGGING))
    assert (stored.current_count, stored.longest_count) == (6, 40)


async def test_overall_streak_needs_score_and_credit_and_pays_milestones_once(uid, db) -> None:
    start = TODAY - timedelta(days=7)
    for back in range(7, 0, -1):
        await good_day(db, uid, TODAY - timedelta(days=back))
    snapshot = await run(db, uid, NOON)
    overall = by_kind(snapshot)["overall"]
    assert overall.current == 7 and overall.run_start == start
    rows = await xp_rows(db, uid)
    assert rows[f"streak:overall:{start}:3"].points == 25
    assert rows[f"streak:overall:{start}:7"].points == 50
    assert not any(key.endswith(":14") for key in rows)
    await run(db, uid, NOON + timedelta(minutes=15))
    assert len([key for key in await xp_rows(db, uid) if key.startswith("streak:")]) == 2  # paid once

    # a perfect day below the safe floor never keeps the overall streak alive
    await eat_day(db, uid, TODAY, 1400)
    await water(db, uid, TODAY, 2500)
    await steps(db, uid, TODAY, 9000)
    await journal(db, uid, TODAY)
    await sleep(db, uid, TODAY, 480)
    snapshot = await run(db, uid, NOON + timedelta(hours=1))
    assert by_kind(snapshot)["overall"].today_met is False
    assert snapshot.today_score.credit is False


async def test_rest_days_never_break_the_workout_streak(uid, db) -> None:
    for weekday in (0, 2, 4):  # Mon/Wed/Fri
        await plan(db, uid, weekday)
    for weekday in (1, 3, 5, 6):
        await plan(db, uid, weekday, rest=True)
    for week in (date(2026, 6, 29), date(2026, 7, 6)):
        for offset in (0, 2, 4):
            await workout(db, uid, week + timedelta(days=offset))
    await workout(db, uid, date(2026, 7, 13))
    snapshot = await run(db, uid, NOON)
    state = by_kind(snapshot)["workout"]
    assert (state.current, state.period_done, state.period_required, state.today_met) == (2, 1, 3, False)
    assert state.broken_on is None and state.freeze_used == []
    assert snapshot.week_required == 3
    assert snapshot.history[date(2026, 7, 14)].plan == "rest"


async def test_at_risk_reads_without_writing(uid, db) -> None:
    for back in range(1, 4):
        await meal(db, uid, TODAY - timedelta(days=back), 500)
    evening = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)  # 18:00 in Bangkok

    async def stored() -> set[tuple]:
        db.expire_all()
        rows = (await db.scalars(select(Streak).where(Streak.user_id == uid))).all()
        return {(row.kind, row.current_count, row.updated_at) for row in rows}

    before = await stored()  # the owner's empty rows from onboarding
    risky = await at_risk(db, uid, evening)
    assert [(state.kind, state.current) for state in risky] == [(StreakKind.MEAL_LOGGING, 3)]
    assert await at_risk(db, uid, evening - timedelta(minutes=1)) == []  # 17:59
    assert await stored() == before  # read-only


# --------------------------------------------------------------------------- #
# Stage 7: the evening streak reminder
# --------------------------------------------------------------------------- #
EVENING = datetime(2026, 7, 15, 13, 0, tzinfo=UTC)  # 20:00 in Bangkok


@pytest.fixture
async def pushing(authed, vapid, push_service, db):  # noqa: F811
    from tests.stage8_helpers import owner_id

    user_id = await owner_id(db)
    await configure(db, user_id, quiet_hours_enabled=False)
    keys = BrowserKeys.new()
    url = endpoint("streak")
    assert (await subscribe(authed, url, keys)).status_code == 201
    await db.execute(Reminder.__table__.delete())  # rows planned on subscribe (real clock) are not under test
    await db.commit()
    return {"user_id": user_id, "keys": keys, "url": url, "push": push_service}


async def streak_row(db, user_id, day: date = TODAY):
    row = Reminder(
        user_id=user_id,
        kind=ReminderKind.STREAK,
        title="Сохраните серию",
        payload={"date": day.isoformat(), "time": "20:00"},
        remind_at=local(day, 20),
        status=ReminderStatus.PENDING,
        dedupe_key=f"streak:{day.isoformat()}:{datetime.now(UTC).timestamp()}",
    )
    db.add(row)
    await db.commit()
    return row.id


async def test_streak_reminder_names_the_streak_and_respects_the_rules(pushing, db) -> None:
    user_id, keys, fake = pushing["user_id"], pushing["keys"], pushing["push"]
    for back in range(1, 6):
        await meal(db, user_id, TODAY - timedelta(days=back), 500)
    first = await streak_row(db, user_id)
    assert (await dispatch(EVENING)).get("sent") == 1
    payload = keys.decrypt(fake.to(pushing["url"])[0].data)
    assert (payload["title"], payload["body"], payload["url"], payload["tag"]) == (
        "Сохраните серию",
        "Дневник питания: 5 дней подряд — сегодня ещё не засчитано",
        "/food?action=search",
        "streak",
    )
    db.expire_all()
    assert (await db.get(Reminder, first)).status == ReminderStatus.SENT

    # once today's meal is logged the streak is safe: nothing is sent
    await meal(db, user_id, TODAY, 500)
    second = await streak_row(db, user_id)
    await dispatch(EVENING)
    db.expire_all()
    assert ((await db.get(Reminder, second)).last_error, len(fake.requests)) == ("not_needed", 1)


async def test_streak_reminder_only_for_streaks_of_three_or_more(pushing, db) -> None:
    user_id, fake = pushing["user_id"], pushing["push"]
    for back in (1, 2):
        await meal(db, user_id, TODAY - timedelta(days=back), 500)
    rid = await streak_row(db, user_id)
    await dispatch(EVENING)
    db.expire_all()
    assert ((await db.get(Reminder, rid)).last_error, fake.requests) == ("not_needed", [])


async def test_planner_adds_the_streak_slot_only_when_there_is_a_streak(pushing, db) -> None:
    from app.services.notifications import plan_daily_reminders

    user_id = pushing["user_id"]
    morning = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)  # 08:00 Bangkok
    await plan_daily_reminders(morning)
    keys = set((await db.scalars(select(Reminder.dedupe_key).where(Reminder.user_id == user_id))).all())
    assert "streak:2026-07-15" not in keys
    for back in range(1, 4):
        await meal(db, user_id, TODAY - timedelta(days=back), 500)
    await run(db, user_id, morning, ("days", "streaks"))  # the job stores the streak rows
    await plan_daily_reminders(morning)
    rows = {row.dedupe_key: row for row in (await db.scalars(select(Reminder).where(Reminder.user_id == user_id)))}
    assert rows["streak:2026-07-15"].remind_at == local(TODAY, 20)
    assert rows["streak:2026-07-15"].kind == ReminderKind.STREAK


def test_streak_messages_never_mention_food_amounts() -> None:
    from app.services import notifications as n

    assert set(n.MESSAGES["ru"]) == set(n.MESSAGES["en"])
    texts: dict[str, list[str]] = {"ru": [], "en": []}
    for lang in texts:
        for kind in StreakKind:
            for count in (1, 3, 5, 21, 22, 25, 111):
                unit = "week" if kind is StreakKind.WORKOUT else "day"
                single = n.streak_message(lang, [StreakState(kind=kind, unit=unit, current=count)])
                texts[lang] += [single.title, single.body]
        both = n.streak_message(
            lang,
            [
                StreakState(kind=StreakKind.WATER, unit="day", current=12),
                StreakState(kind=StreakKind.WORKOUT, unit="week", current=4),
            ],
        )
        texts[lang] += [both.title, both.body]
    for lang, rendered in texts.items():
        for text in rendered:
            hits = [word for word in FORBIDDEN[lang] if word in text.lower()]
            assert not hits, f"{lang}: {text!r} contains {hits}"
    ru = n.streak_message("ru", [StreakState(kind=StreakKind.WATER, unit="day", current=21)]).body
    assert ru == "Вода: 21 день подряд — сегодня ещё не засчитано"
    assert n.streak_message("ru", [StreakState(kind=StreakKind.WORKOUT, unit="week", current=3)]).body == (
        "Тренировки: 3 недели подряд — эта неделя ещё не засчитана"
    )
    assert n.streak_message("en", [StreakState(kind=StreakKind.STEPS, unit="day", current=1)]).body == (
        "Steps: 1 day in a row — today isn't counted yet"
    )
