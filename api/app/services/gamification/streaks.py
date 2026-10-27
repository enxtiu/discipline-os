"""Streaks: one row per user and kind, recomputed from the data.

======== ================================================================ ======
kind     a day (week) counts when                                         unit
======== ================================================================ ======
overall  daily score ≥ 60 *and* credit (≥ 1 meal, intake ≥ the floor)     day
meal_logging ≥ 1 meal logged                                              day
water    water ≥ target                                                   day
steps    steps ≥ target                                                   day
tasks    ≥ 1 task completed                                               day
review   journal entry with content                                       day
workout  completed workouts ≥ planned that week (no plan → 3)             week
======== ================================================================ ======

Rest is part of a plan: the only training streak is weekly and plan-aware, and a
week planned entirely as rest is neutral (it neither counts nor breaks).

Freeze tokens: +1 every week (on the user's week start), at most 2; a missed past
day/week uses one automatically (``freeze_used_dates``) instead of breaking the
streak — a saved day does not add to the count. Today (this week) counts as soon as
its condition is met and is never a miss while it lasts. ``at_risk`` = not met
yet, there is a streak to lose and it is 18:00 or later locally (weekly: on the
last day of the week). The ``longest`` value never decreases.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models import Streak, StreakKind, XpKind
from app.services.day_metrics import DayMetrics, WeekPlan, required_workouts, week_start_for
from app.services.gamification import xp
from app.services.gamification.context import GameContext

INITIAL_FREEZE_TOKENS = 1
MAX_FREEZE_TOKENS = 2
AT_RISK_HOUR = 18
NOTIFY_MIN_STREAK = 3  # the stage-7 evening reminder only protects streaks of ≥ 3
FREEZE_DATES_KEPT = 120
OVERALL_MILESTONES: tuple[tuple[int, int], ...] = (
    (3, 25),
    (7, 50),
    (14, 75),
    (30, 100),
    (60, 150),
    (100, 200),
    (200, 300),
    (365, 500),
)
DISPLAY_ORDER: tuple[StreakKind, ...] = (
    StreakKind.OVERALL,
    StreakKind.MEAL_LOGGING,
    StreakKind.WATER,
    StreakKind.STEPS,
    StreakKind.TASKS,
    StreakKind.REVIEW,
    StreakKind.WORKOUT,
)
DAILY_KINDS: tuple[StreakKind, ...] = tuple(kind for kind in DISPLAY_ORDER if kind is not StreakKind.WORKOUT)


@dataclass(slots=True)
class StreakState:
    kind: StreakKind
    unit: str  # "day" | "week"
    current: int = 0
    longest: int = 0
    today_met: bool = False
    at_risk: bool = False
    freeze_tokens: int = INITIAL_FREEZE_TOKENS
    freeze_used: list[date] = field(default_factory=list)
    last_met: date | None = None
    broken_on: date | None = None
    run_start: date | None = None
    total_events: int = 0
    period_done: int | None = None
    period_required: int | None = None
    reached: dict[int, date] = field(default_factory=dict)  # milestone → day reached (current run)
    count_on: dict[date, int] = field(default_factory=dict)  # streak length at the end of a tracked day

    @property
    def next_milestone(self) -> int | None:
        if self.kind is not StreakKind.OVERALL:
            return None
        return next((days for days, _ in OVERALL_MILESTONES if days > self.current), None)


# --------------------------------------------------------------------------- #
# Simulations (pure)
# --------------------------------------------------------------------------- #
def simulate_daily(
    kind: StreakKind,
    met: set[date],
    today: date,
    week_start: int,
    local_hour: int,
    *,
    track: set[date] | frozenset[date] = frozenset(),
) -> StreakState:
    state = StreakState(kind=kind, unit="day")
    past_or_today = [day for day in met if day <= today]
    if not past_or_today:
        return state
    start = min(past_or_today)
    tokens = INITIAL_FREEZE_TOKENS
    day = start
    while day <= today:
        if day != start and week_start_for(day, week_start) == day:
            tokens = min(MAX_FREEZE_TOKENS, tokens + 1)
        if day in met:
            if state.current == 0:
                state.run_start, state.reached = day, {}
            state.current += 1
            state.total_events += 1
            state.last_met = day
            state.longest = max(state.longest, state.current)
            for days, _ in OVERALL_MILESTONES:
                if state.current == days:
                    state.reached[days] = day
        elif day < today and state.current > 0:
            if tokens > 0:
                tokens -= 1
                state.freeze_used.append(day)
            else:
                state.current, state.broken_on, state.run_start, state.reached = 0, day, None, {}
        if day in track:
            state.count_on[day] = state.current
        day += timedelta(days=1)
    state.freeze_tokens = tokens
    state.today_met = today in met
    state.at_risk = not state.today_met and state.current > 0 and local_hour >= AT_RISK_HOUR
    return state


def simulate_weekly(
    done_by_week: dict[date, int],
    plans: dict[date, WeekPlan],
    today: date,
    week_start: int,
    local_hour: int,
) -> StreakState:
    state = StreakState(kind=StreakKind.WORKOUT, unit="week")
    this_week = week_start_for(today, week_start)

    def required(week: date) -> int:
        plan = plans.get(week)
        return required_workouts(plan) if plan is not None else required_workouts(WeekPlan(week, 0, False))

    weeks = [week for week, done in done_by_week.items() if done > 0 and week <= this_week]
    if weeks:
        start = min(weeks)
        tokens = INITIAL_FREEZE_TOKENS
        week = start
        while week <= this_week:
            if week != start:
                tokens = min(MAX_FREEZE_TOKENS, tokens + 1)
            need, done = required(week), done_by_week.get(week, 0)
            if need == 0:
                pass  # a week planned as rest: neutral, never a miss
            elif done >= need:
                if state.current == 0:
                    state.run_start = week
                state.current += 1
                state.total_events += 1
                state.last_met = week
                state.longest = max(state.longest, state.current)
            elif week < this_week and state.current > 0:
                if tokens > 0:
                    tokens -= 1
                    state.freeze_used.append(week)
                else:
                    state.current, state.broken_on, state.run_start = 0, week, None
            week += timedelta(days=7)
        state.freeze_tokens = tokens
    need, done = required(this_week), done_by_week.get(this_week, 0)
    state.period_done, state.period_required = done, need
    state.today_met = need == 0 or done >= need
    last_day = this_week + timedelta(days=6)
    state.at_risk = not state.today_met and state.current > 0 and today == last_day and local_hour >= AT_RISK_HOUR
    return state


def daily_conditions(history: dict[date, DayMetrics], overall_days: set[date], ctx: GameContext) -> dict:
    """Days that count, per daily kind (current targets apply to the whole history)."""
    t = ctx.targets
    return {
        StreakKind.OVERALL: set(overall_days),
        StreakKind.MEAL_LOGGING: {day for day, m in history.items() if m.meals >= 1},
        StreakKind.WATER: {day for day, m in history.items() if t.water_ml and m.water_ml >= t.water_ml},
        StreakKind.STEPS: {day for day, m in history.items() if t.steps and m.steps is not None and m.steps >= t.steps},
        StreakKind.TASKS: {day for day, m in history.items() if m.tasks_done >= 1},
        StreakKind.REVIEW: {day for day, m in history.items() if m.journal},
    }


def compute(
    history: dict[date, DayMetrics],
    overall_days: set[date],
    plans: dict[date, WeekPlan],
    ctx: GameContext,
    stored_longest: dict[StreakKind, int] | None = None,
    *,
    track: set[date] | frozenset[date] = frozenset(),
) -> list[StreakState]:
    """Every streak of the user in display order (pure). ``plans``: week start → plan
    for every week of the history (whole weeks, the current one included)."""
    hour = ctx.local_now.hour
    conditions = daily_conditions(history, overall_days, ctx)
    states: dict[StreakKind, StreakState] = {
        kind: simulate_daily(
            kind, conditions[kind], ctx.today, ctx.week_start, hour, track=track if kind is StreakKind.OVERALL else ()
        )
        for kind in DAILY_KINDS
    }
    done_by_week: dict[date, int] = defaultdict(int)
    for day, m in history.items():
        done_by_week[week_start_for(day, ctx.week_start)] += m.workouts
    states[StreakKind.WORKOUT] = simulate_weekly(done_by_week, plans, ctx.today, ctx.week_start, hour)
    for kind, longest in (stored_longest or {}).items():
        if kind in states:
            states[kind].longest = max(states[kind].longest, int(longest or 0))
    return [states[kind] for kind in DISPLAY_ORDER]


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
STORED_COLUMNS = (
    "current_count",
    "longest_count",
    "last_event_date",
    "broken_on",
    "freeze_tokens",
    "freeze_used_dates",
    "total_events",
)


async def stored_rows(db: AsyncSession, user_id: uuid.UUID) -> dict[StreakKind, dict[str, Any]]:
    """The stored streak rows (the columns ``persist`` writes), by kind."""
    rows = await db.execute(
        select(Streak.kind, *(getattr(Streak, column) for column in STORED_COLUMNS)).where(Streak.user_id == user_id)
    )
    return {row[0]: dict(zip(STORED_COLUMNS, row[1:], strict=True)) for row in rows}


async def stored_longest(db: AsyncSession, user_id: uuid.UUID) -> dict[StreakKind, int]:
    return {kind: int(row["longest_count"] or 0) for kind, row in (await stored_rows(db, user_id)).items()}


async def persist(
    db: AsyncSession,
    ctx: GameContext,
    states: list[StreakState],
    stored: dict[StreakKind, dict[str, Any]] | None = None,
) -> None:
    """Upsert the seven rows in one statement; ``longest_count`` never decreases.
    ``stored`` (``stored_rows``, read under the same lock): nothing is written when every
    row already holds these values — the usual case for a read of the day (stage 9 perf)."""
    values = [
        {
            "id": new_id(),
            "user_id": ctx.user_id,
            "kind": state.kind,
            "current_count": state.current,
            "longest_count": state.longest,
            "last_event_date": state.last_met,
            "broken_on": state.broken_on,
            "freeze_tokens": state.freeze_tokens,
            "freeze_used_dates": [day.isoformat() for day in state.freeze_used[-FREEZE_DATES_KEPT:]],
            "total_events": state.total_events,
            "created_at": ctx.now,
            "updated_at": ctx.now,
        }
        for state in states
    ]
    if stored is not None and all(
        (row := stored.get(value["kind"])) is not None
        and all(row[column] == value[column] for column in STORED_COLUMNS if column != "longest_count")
        and int(row["longest_count"] or 0) >= value["longest_count"]
        for value in values
    ):
        return
    stmt = pg_insert(Streak).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_streaks_user_kind",
        set_={
            "current_count": stmt.excluded.current_count,
            "longest_count": func.greatest(Streak.longest_count, stmt.excluded.longest_count),
            "last_event_date": stmt.excluded.last_event_date,
            "broken_on": stmt.excluded.broken_on,
            "freeze_tokens": stmt.excluded.freeze_tokens,
            "freeze_used_dates": stmt.excluded.freeze_used_dates,
            "total_events": stmt.excluded.total_events,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await db.execute(stmt)


async def award_milestones(db: AsyncSession, ctx: GameContext, overall: StreakState) -> int:
    """Permanent XP for each milestone the current overall run has reached (once per run).
    One statement for all of them: this runs on every read of the day (stage 9 perf)."""
    if overall.kind is not StreakKind.OVERALL or overall.run_start is None:
        return 0
    awards = []
    for days, points in OVERALL_MILESTONES:
        if overall.current < days:
            break
        reached = overall.reached.get(days)
        awards.append(
            xp.Award(
                key=f"streak:overall:{overall.run_start.isoformat()}:{days}",
                kind=XpKind.STREAK_BONUS,
                points=points,
                day=ctx.today,
                reason=f"streak_overall_{days}",
                meta={"streak": "overall", "days": days, "reached_on": reached.isoformat() if reached else None},
            )
        )
    return await xp.award_permanent_many(db, ctx, awards)


async def has_streak(db: AsyncSession, user_id: uuid.UUID, min_count: int = NOTIFY_MIN_STREAK) -> bool:
    """Is any stored streak at least ``min_count`` long? (cheap; the planner slot)."""
    return bool(await db.scalar(select(exists().where(Streak.user_id == user_id, Streak.current_count >= min_count))))


async def at_risk(
    db: AsyncSession, user_id: uuid.UUID, now: datetime, *, min_count: int = NOTIFY_MIN_STREAK
) -> list[StreakState]:
    """Streaks of ≥ ``min_count`` whose today's (this week's) condition is not met and
    that are at risk at ``now`` (after 18:00 local), longest first. Read-only."""
    from app.services.gamification.engine import sync_user

    snapshot = await sync_user(db, user_id, now, stages=("streaks",), persist=False)
    if snapshot is None:
        return []
    risky = [state for state in snapshot.streaks if state.at_risk and state.current >= min_count]
    return sorted(risky, key=lambda state: (-state.current, DISPLAY_ORDER.index(state.kind)))
