"""Stage 10: no streak freezes — the owner's rule, "всё по харду".

``STREAK_FREEZES_ENABLED`` is off by default: a missed day/week breaks the streak, no
freeze token is granted, refilled or used, a new account starts with 0 tokens, and the API
keeps its response shapes while it reports ``freeze_tokens = 0`` and no freeze dates. The
opt-in mechanics of stage 8 stay tested in ``test_stage8_streaks.py`` (``freezes_on``);
here are the default, the switch itself and the contrast between the two.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models import Streak, StreakKind, UserSettings
from app.services import stats
from app.services.bootstrap import create_owner
from app.services.day_metrics import WeekPlan
from app.services.gamification.context import context_for
from app.services.gamification.streaks import StreakState, simulate_daily, simulate_weekly
from tests.stage8_helpers import (
    BANGKOK,
    NOON,
    TODAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    meal,
    owner_id,
    run,
)

pytestmark = pytest.mark.anyio

G = "/api/v1/gamification"
FLAGS = pytest.mark.parametrize("freezes", [False, True], ids=["hard", "freezes"])
KINDS = ["overall", "meal_logging", "water", "steps", "tasks", "review", "workout"]
STREAK_KEYS = {
    "kind",
    "unit",
    "current",
    "longest",
    "today_met",
    "at_risk",
    "freeze_tokens",
    "freeze_used_dates",
    "last_met_date",
    "broken_on",
    "period",
    "next_milestone",
}


def days(first: date, last: date) -> set[date]:
    return {first + timedelta(days=offset) for offset in range((last - first).days + 1)}


def switch(monkeypatch: pytest.MonkeyPatch, freezes: bool) -> None:
    monkeypatch.setattr(get_settings(), "streak_freezes_enabled", freezes)


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #
def test_freezes_are_off_unless_the_env_var_turns_them_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.model_fields["streak_freezes_enabled"].default is False
    monkeypatch.delenv("STREAK_FREEZES_ENABLED", raising=False)
    assert Settings(_env_file=None).streak_freezes_enabled is False
    monkeypatch.setenv("STREAK_FREEZES_ENABLED", "true")
    assert Settings(_env_file=None).streak_freezes_enabled is True


def test_the_suite_runs_under_the_owners_rule() -> None:
    """conftest pins the variable: a stray ``.env`` cannot turn the freezes on for a run."""
    assert get_settings().streak_freezes_enabled is False


# --------------------------------------------------------------------------- #
# Daily streaks (pure): a miss breaks, nothing is granted or refilled
# --------------------------------------------------------------------------- #
def test_a_missed_day_breaks_the_streak() -> None:
    met = days(date(2026, 7, 6), date(2026, 7, 8)) | days(date(2026, 7, 10), TODAY)  # Thursday 9 July missed
    state = simulate_daily(StreakKind.MEAL_LOGGING, met, TODAY, 1, 12)
    assert state.broken_on == date(2026, 7, 9)
    assert (state.current, state.longest) == (6, 6)  # 10–15 July; the run before it was 3
    assert state.run_start == date(2026, 7, 10)
    assert (state.freeze_used, state.freeze_tokens) == ([], 0)


def test_one_miss_after_a_long_run_breaks_it_and_longest_is_kept() -> None:
    met = days(date(2026, 6, 1), date(2026, 7, 12)) | days(date(2026, 7, 14), TODAY)  # 13 July missed after 42 days
    state = simulate_daily(StreakKind.REVIEW, met, TODAY, 1, 12)
    assert (state.current, state.longest, state.broken_on) == (2, 42, date(2026, 7, 13))
    assert (state.freeze_used, state.freeze_tokens) == ([], 0)


@FLAGS
def test_tokens_are_neither_granted_nor_refilled_unless_opted_in(
    monkeypatch: pytest.MonkeyPatch, freezes: bool
) -> None:
    switch(monkeypatch, freezes)
    expected = 2 if freezes else 0  # the weekly refills fill the cap, or never start
    monday_weeks = simulate_daily(StreakKind.REVIEW, days(date(2026, 6, 1), TODAY), TODAY, 1, 12)
    assert (monday_weeks.current, monday_weeks.freeze_used, monday_weeks.freeze_tokens) == (45, [], expected)
    sunday_weeks = simulate_daily(StreakKind.REVIEW, days(date(2026, 7, 5), TODAY), TODAY, 0, 12)
    assert (sunday_weeks.current, sunday_weeks.freeze_used, sunday_weeks.freeze_tokens) == (11, [], expected)


@FLAGS
def test_a_streak_without_data_holds_no_token_unless_opted_in(monkeypatch: pytest.MonkeyPatch, freezes: bool) -> None:
    switch(monkeypatch, freezes)
    expected = 1 if freezes else 0  # the starting token
    assert simulate_daily(StreakKind.WATER, set(), TODAY, 1, 12).freeze_tokens == expected
    assert simulate_weekly({}, {}, TODAY, 1, 12).freeze_tokens == expected
    assert StreakState(kind=StreakKind.WATER, unit="day").freeze_tokens == expected


# --------------------------------------------------------------------------- #
# The weekly workout streak (pure)
# --------------------------------------------------------------------------- #
WEEKS = [date(2026, 6, 22), date(2026, 6, 29), date(2026, 7, 6), date(2026, 7, 13)]


def plans(planned: int, *, has_plan: bool = True) -> dict[date, WeekPlan]:
    return {week: WeekPlan(week, planned, has_plan) for week in WEEKS}


def test_a_short_week_breaks_the_workout_streak_and_a_rest_week_stays_neutral() -> None:
    weekly = plans(3)
    weekly[WEEKS[2]] = WeekPlan(WEEKS[2], 0, True)  # a deload week planned as rest
    done = {WEEKS[0]: 3, WEEKS[1]: 2, WEEKS[2]: 0, WEEKS[3]: 0}
    state = simulate_weekly(done, weekly, TODAY, 1, 12)
    assert (state.current, state.longest, state.broken_on) == (0, 1, WEEKS[1])  # the short week broke it
    assert (state.freeze_used, state.freeze_tokens) == ([], 0)


def test_without_a_plan_a_week_of_two_workouts_breaks_the_streak() -> None:
    done = {WEEKS[1]: 3, WEEKS[2]: 2, WEEKS[3]: 0}
    state = simulate_weekly(done, plans(0, has_plan=False), TODAY, 1, 12)
    assert state.period_required == 3
    assert (state.current, state.longest, state.broken_on) == (0, 1, WEEKS[2])
    assert (state.freeze_used, state.freeze_tokens) == ([], 0)


@FLAGS
def test_weekly_tokens_are_not_refilled_unless_opted_in(monkeypatch: pytest.MonkeyPatch, freezes: bool) -> None:
    switch(monkeypatch, freezes)
    state = simulate_weekly(dict.fromkeys(WEEKS, 3), plans(3), TODAY, 1, 12)
    assert (state.current, state.freeze_used) == (4, [])
    assert state.freeze_tokens == (2 if freezes else 0)


# --------------------------------------------------------------------------- #
# The engine and the stored rows
# --------------------------------------------------------------------------- #
@pytest.fixture
async def uid(owner, db):
    await configure(db, owner.id)
    return owner.id


async def log_meals(db, user_id, first_back: int, *, skip: tuple[int, ...] = (), today: date = TODAY) -> None:
    """A meal on every day from ``first_back`` days ago up to the day before ``today``, except ``skip``."""
    for back in range(first_back, 0, -1):
        if back not in skip:
            await meal(db, user_id, today - timedelta(days=back), 500)


async def stored(db, user_id) -> dict[StreakKind, Streak]:
    db.expire_all()
    return {row.kind: row for row in (await db.scalars(select(Streak).where(Streak.user_id == user_id))).all()}


async def test_the_engine_breaks_the_streak_on_a_missed_day_and_stores_no_tokens(uid, db) -> None:
    await log_meals(db, uid, 6, skip=(3,))  # 9–11 and 13–14 July; Sunday 12 July is missed
    snapshot = await run(db, uid, NOON)
    state = next(item for item in snapshot.streaks if item.kind is StreakKind.MEAL_LOGGING)
    assert (state.current, state.longest, state.broken_on, state.today_met) == (2, 3, date(2026, 7, 12), False)
    assert (state.freeze_tokens, state.freeze_used) == (0, [])

    rows = await stored(db, uid)
    assert set(rows) == set(StreakKind)
    assert all((row.freeze_tokens, row.freeze_used_dates) == (0, []) for row in rows.values())
    meals = rows[StreakKind.MEAL_LOGGING]
    assert (meals.current_count, meals.longest_count, meals.broken_on) == (2, 3, date(2026, 7, 12))


async def test_turning_the_rule_off_breaks_what_a_token_saved_and_keeps_longest(uid, db, monkeypatch) -> None:
    await log_meals(db, uid, 6, skip=(3,))
    switch(monkeypatch, True)
    saved = await run(db, uid, NOON)
    state = next(item for item in saved.streaks if item.kind is StreakKind.MEAL_LOGGING)
    assert (state.current, state.freeze_used, state.broken_on) == (5, [date(2026, 7, 12)], None)
    row = (await stored(db, uid))[StreakKind.MEAL_LOGGING]
    assert (row.current_count, row.longest_count, row.freeze_used_dates) == (5, 5, ["2026-07-12"])

    switch(monkeypatch, False)  # the owner's decision: from the next sync the gap is a miss
    await run(db, uid, NOON + timedelta(minutes=5))
    rows = await stored(db, uid)
    meals = rows[StreakKind.MEAL_LOGGING]
    assert (meals.current_count, meals.longest_count, meals.broken_on) == (2, 5, date(2026, 7, 12))  # longest stays
    assert all((row.freeze_tokens, row.freeze_used_dates) == (0, []) for row in rows.values())  # old values rewritten


# --------------------------------------------------------------------------- #
# Accounts: nobody starts with a token
# --------------------------------------------------------------------------- #
@FLAGS
async def test_a_new_account_starts_with_no_freeze_tokens(db, monkeypatch, freezes: bool) -> None:
    switch(monkeypatch, freezes)
    created = await create_owner(db, display_name="Новый", with_pairing_code=False)
    await db.commit()
    rows = await stored(db, created.user.id)
    assert set(rows) == set(StreakKind)
    assert {kind: row.freeze_tokens for kind, row in rows.items()} == {
        kind: 1 if freezes and kind is StreakKind.OVERALL else 0 for kind in StreakKind
    }
    assert all(row.freeze_used_dates == [] for row in rows.values())


# --------------------------------------------------------------------------- #
# The API: the same shapes, no tokens, no freeze dates
# --------------------------------------------------------------------------- #
def now_today() -> date:
    return datetime.now(BANGKOK).date()


@FLAGS
async def test_a_new_account_reports_its_tokens_through_the_api(
    authed: httpx.AsyncClient, db, monkeypatch, freezes: bool
) -> None:
    switch(monkeypatch, freezes)
    user_id = await owner_id(db)
    await configure(db, user_id)
    for path in ("today", "profile"):
        response = await authed.get(f"{G}/{path}")
        assert response.status_code == 200, response.text
        streaks = response.json()["streaks"]
        assert [item["kind"] for item in streaks] == KINDS
        for item in streaks:
            assert set(item) == STREAK_KEYS  # the shape is the same either way
            assert (item["freeze_tokens"], item["freeze_used_dates"]) == (1 if freezes else 0, [])
    assert all(row.freeze_tokens == (1 if freezes else 0) for row in (await stored(db, user_id)).values())


@FLAGS
async def test_the_api_after_a_missed_day(authed: httpx.AsyncClient, db, monkeypatch, freezes: bool) -> None:
    """Meals five, four, two and one days ago and today; three days ago is missed. A token
    would have saved it (a run of 5, the date listed); by the owner's rule it is a break."""
    switch(monkeypatch, freezes)
    user_id = await owner_id(db)
    await configure(db, user_id)
    today = now_today()
    gap = today - timedelta(days=3)
    await log_meals(db, user_id, 5, skip=(3,), today=today)
    await meal(db, user_id, today, 500)

    for path in ("today", "profile"):
        response = await authed.get(f"{G}/{path}")
        assert response.status_code == 200, response.text
        streaks = response.json()["streaks"]
        assert [item["kind"] for item in streaks] == KINDS
        assert all(set(item) == STREAK_KEYS for item in streaks)
        meals = next(item for item in streaks if item["kind"] == "meal_logging")
        if freezes:
            assert (meals["current"], meals["broken_on"], meals["freeze_used_dates"]) == (5, None, [gap.isoformat()])
            assert 0 <= meals["freeze_tokens"] <= 2
        else:
            assert (meals["current"], meals["longest"], meals["broken_on"]) == (3, 3, gap.isoformat())
            assert all((item["freeze_tokens"], item["freeze_used_dates"]) == (0, []) for item in streaks)


async def test_the_progress_calendar_flags_no_freeze_day(authed: httpx.AsyncClient, db) -> None:
    user_id = await owner_id(db)
    await configure(db, user_id)
    await log_meals(db, user_id, 5, skip=(3,), today=now_today())
    response = await authed.get("/api/v1/stats/progress", params={"period": "month"})
    assert response.status_code == 200, response.text
    calendar = response.json()["streak_calendar"]
    assert calendar and all(set(item) == {"date", "overall", "freeze"} for item in calendar)
    assert not any(item["freeze"] for item in calendar)


async def test_the_calendar_ignores_dates_a_row_still_holds_while_freezes_are_off(uid, db, monkeypatch) -> None:
    """Rows written before the switch (or restored from a backup) may still list dates; the
    next sync rewrites them, and until then they are not reported while the rule is off."""
    await run(db, uid, NOON)
    await db.execute(
        sa.update(Streak)
        .where(Streak.user_id == uid, Streak.kind == StreakKind.OVERALL)
        .values(freeze_used_dates=["2026-07-14", "garbage"])
    )
    await db.commit()
    settings = (await db.scalars(select(UserSettings).where(UserSettings.user_id == uid))).one()
    ctx = context_for(settings, uid, NOON)
    span = stats.span_for("week", TODAY, ctx.week_start, ctx.today)

    off = await stats.progress(db, ctx, span)
    assert not any(day.freeze for day in off.days)
    switch(monkeypatch, True)  # the same row is believed once the rule is on
    on = await stats.progress(db, ctx, span)
    assert [day.day for day in on.days if day.freeze] == [date(2026, 7, 14)]


def test_the_openapi_shape_keeps_the_freeze_fields() -> None:
    """The generated web client reads these: the rule changes their values, not the contract."""
    from app.main import app

    schemas = app.openapi()["components"]["schemas"]
    streak = schemas["StreakOut"]
    assert {"freeze_tokens", "freeze_used_dates"} <= set(streak["properties"]) & set(streak["required"])
    day = schemas["StreakDayOut"]
    assert "freeze" in set(day["properties"]) & set(day["required"])


# --------------------------------------------------------------------------- #
# Backend texts: nothing promises a freeze
# --------------------------------------------------------------------------- #
FREEZE_WORDS = ("freez", "frozen", "заморо")  # not "token": the API-token labels ("Token revoked") are about auth


def test_no_backend_text_mentions_a_freeze() -> None:
    from app.seeds.gamification import ACHIEVEMENTS, QUESTS
    from app.services import notifications as n
    from app.services.meta_i18n import I18N_ENUM_LABELS

    sources: dict[str, list[str]] = {
        "notification texts": [text for messages in n.MESSAGES.values() for text in messages.values()],
        "enum labels": [value for label in I18N_ENUM_LABELS for key, value in label.items() if key != "code"],
        "achievements": [
            text
            for item in ACHIEVEMENTS
            for text in (item.name_ru, item.name_en, item.description_ru, item.description_en)
        ],
        "quests": [
            text for item in QUESTS for text in (item.title_ru, item.title_en, item.description_ru, item.description_en)
        ],
        "streak reminders": [],
    }
    for lang in ("ru", "en"):
        for kind in StreakKind:
            unit = "week" if kind is StreakKind.WORKOUT else "day"
            for count in (3, 21):
                message = n.streak_message(lang, [StreakState(kind=kind, unit=unit, current=count)])
                sources["streak reminders"] += [message.title, message.body]
        several = n.streak_message(
            lang,
            [
                StreakState(kind=StreakKind.WATER, unit="day", current=12),
                StreakState(kind=StreakKind.WORKOUT, unit="week", current=4),
            ],
        )
        sources["streak reminders"] += [several.title, several.body]
    for name, texts in sources.items():
        assert texts, f"{name}: nothing to scan"
        hits = [text for text in texts if any(word in text.lower() for word in FREEZE_WORDS)]
        assert not hits, f"{name}: {hits}"
