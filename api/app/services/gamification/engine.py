"""One gamification run for one user — the same code path for the scheduler jobs and
for the endpoints (``GET /gamification/today`` recomputes on read).

Order, inside one transaction holding a per-user advisory lock (overlapping runs —
two jobs, a job and a request — simply take turns; every write is idempotent):

1. **days** — daily scores and day XP for the open window (yesterday, today) and,
   once each, for older days whose score was never computed after their grace day
   ended (first run, downtime): those are then frozen for good. Score and XP of a
   day are always finalised together.
2. **streaks** — recomputed from the history; rows upserted (longest never
   decreases); overall-streak milestones paid once per run.
3. **achievements** — metrics, unlocks, permanent XP (repeated while a level-up
   unlocks more).
4. **quests** — expire past periods, rotate today/this week, refresh progress.

``persist=False`` computes the same snapshot without any write (the stage-7
streak reminder uses it).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.tz import iter_days, utcnow
from app.db import session_scope
from app.models import User, UserStatus
from app.services import day_metrics
from app.services.day_metrics import DayMetrics, WeekPlan, required_workouts, week_start_for
from app.services.gamification import achievements, quests, score, streaks, xp
from app.services.gamification.achievements import DayFlag
from app.services.gamification.context import GameContext, build_context
from app.services.gamification.levels import LevelProgress, level_for
from app.services.gamification.quests import QuestView
from app.services.gamification.score import DayScore
from app.services.gamification.streaks import StreakState
from app.services.planner.common import advisory_lock

log = get_logger(__name__)

STAGES: tuple[str, ...] = ("days", "streaks", "achievements", "quests")
HISTORY_DAYS = 1100  # how far back history is read and, on a first run, scored once
LOCK_SCOPE = "gamification"
MAX_LEVEL_ROUNDS = 5


@dataclass(slots=True)
class GameSnapshot:
    ctx: GameContext
    history: dict[date, DayMetrics]
    computed: dict[date, DayScore]  # days (re)computed in this run: the window + newly frozen
    flags: dict[date, DayFlag]  # every day with a score (stored or computed)
    plans: dict[date, WeekPlan]
    streaks: list[StreakState]
    xp_total: int = 0
    xp_today: int = 0
    level: LevelProgress = field(default_factory=lambda: level_for(0))
    metrics: dict[str, float] | None = None
    unlocked_now: list = field(default_factory=list)
    quests: list[QuestView] = field(default_factory=list)

    @property
    def today_score(self) -> DayScore:
        return self.computed[self.ctx.today]

    @property
    def week_required(self) -> int:
        plan = self.plans.get(self.ctx.week_start_day)
        return required_workouts(plan) if plan is not None else required_workouts(WeekPlan(self.ctx.today, 0, False))


def _pending(
    stored: dict[date, score.ScoreFlag], ctx: GameContext, first_data: date | None, oldest: date
) -> list[date]:
    """Days before yesterday that still need their final computation: never scored
    (from the first data day on), or last scored before their grace day ended
    (``computed_at`` < local start of day + 2) — then they are frozen for good."""
    last = ctx.today - timedelta(days=2)
    candidates = set(iter_days(max(first_data, oldest), last)) if first_data is not None else set()
    candidates |= {day for day in stored if day <= last}
    return sorted(
        day
        for day in candidates
        if day not in stored or stored[day].computed_at < ctx.local_start(day + timedelta(days=2))
    )


async def sync_user(
    db,
    user_id: uuid.UUID,
    now: datetime | None = None,
    *,
    stages: Iterable[str] = STAGES,
    persist: bool = True,
) -> GameSnapshot | None:
    """Run the requested stages for one active user; ``None`` for a missing/disabled one."""
    stages = set(stages)
    ctx = await build_context(db, user_id, now)
    if ctx is None:
        return None
    if persist:
        await advisory_lock(db, user_id, LOCK_SCOPE)
        if stages & {"achievements", "quests"}:
            from app.seeds.gamification import ensure_seeded

            await ensure_seeded(db)

    today, yesterday = ctx.today, ctx.yesterday
    oldest = today - timedelta(days=HISTORY_DAYS)
    first_data = await day_metrics.first_activity_day(db, user_id, ctx.tz)
    if first_data is not None and first_data > today:
        first_data = today
    start = week_start_for(max(oldest, min(first_data or yesterday, yesterday)), ctx.week_start)
    week_end = ctx.week_start_day + timedelta(days=6)
    plan = await day_metrics.plan_by_day(db, user_id, start, week_end)  # whole weeks, this one included
    history = await day_metrics.collect(db, user_id, start, today, ctx.tz, now=ctx.now, plan=plan)
    plans = day_metrics.weeks_from_plan(plan, start, today, ctx.week_start)
    stored = await score.load_flags(db, user_id, start, today)

    # yesterday stays in the window while it has data or a stored row (a deleted meal
    # must lose its XP); a brand-new account gets no empty "yesterday"
    with_yesterday = (first_data is not None and first_data <= yesterday) or yesterday in stored
    window = [yesterday, today] if with_yesterday else [today]
    pending = _pending(stored, ctx, first_data, oldest)
    computed = {day: score.compute_day_score(history[day], ctx, provisional=day == today) for day in pending + window}

    flags: dict[date, DayFlag] = {day: DayFlag(flag.score, flag.credit) for day, flag in stored.items()}
    flags.update({day: DayFlag(item.value, item.credit) for day, item in computed.items()})
    overall_days = {day for day, flag in flags.items() if flag.credit and flag.score >= score.OVERALL_THRESHOLD}
    stored_streaks = await streaks.stored_rows(db, user_id)
    longest = {kind: int(row["longest_count"] or 0) for kind, row in stored_streaks.items()}
    states = streaks.compute(history, overall_days, plans, ctx, longest, track=set(computed))
    snapshot = GameSnapshot(ctx=ctx, history=history, computed=computed, flags=flags, plans=plans, streaks=states)

    if persist and "days" in stages:
        days = sorted(computed)
        refs = await xp.load_refs(db, ctx, days[0], today)
        awards = [
            award
            for day in days
            for award in xp.day_awards(day, history[day], refs.get(day), ctx, finished=day < today)
        ]
        await xp.sync_days(db, ctx, days, awards)
    if persist and "streaks" in stages:
        await streaks.persist(db, ctx, states, stored_streaks)
        await streaks.award_milestones(db, ctx, states[0])

    snapshot.xp_total = await xp.total(db, user_id)
    snapshot.level = level_for(snapshot.xp_total)
    if persist and "achievements" in stages:
        snapshot.metrics = await achievements.compute_metrics(
            db, ctx, history, flags, states, snapshot.level.current.level
        )
        for _ in range(MAX_LEVEL_ROUNDS):
            fresh = await achievements.evaluate(db, ctx, snapshot.metrics)
            snapshot.unlocked_now += fresh
            if not fresh:
                break
            snapshot.xp_total = await xp.total(db, user_id)
            snapshot.level = level_for(snapshot.xp_total)
            if snapshot.level.current.level == int(snapshot.metrics["level"]):
                break
            snapshot.metrics["level"] = float(snapshot.level.current.level)
    if persist and "quests" in stages:
        f = await quests.facts(db, ctx, history[today].plan, snapshot.week_required)
        await quests.expire(db, ctx)
        await quests.rotate(db, ctx, f)
        snapshot.quests = await quests.refresh(db, ctx, history, snapshot.week_required)  # no XP until claimed
    if persist and "days" in stages:  # last, so xp_earned includes this run's permanent awards
        days = sorted(computed)
        xp_by_day = await xp.per_day(db, user_id, days[0], today)
        await score.store(db, ctx, computed.values(), xp_by_day, states[0].count_on)
        snapshot.xp_today = xp_by_day.get(today, 0)
    else:
        snapshot.xp_today = (await xp.per_day(db, user_id, today, today)).get(today, 0)
    return snapshot


# --------------------------------------------------------------------------- #
# Scheduler entry points (registered in app/services/hooks.py)
# --------------------------------------------------------------------------- #
async def run_for_all_users(stages: Iterable[str], now: datetime | None = None) -> int:
    """Every active user, each in their own transaction; one failure never stops a run."""
    stages = tuple(stages)
    now = now or utcnow()
    async with session_scope() as db:
        user_ids = (await db.scalars(select(User.id).where(User.status == UserStatus.ACTIVE))).all()
    done = 0
    for user_id in user_ids:
        try:
            async with session_scope() as db:
                if await sync_user(db, user_id, now, stages=stages) is not None:
                    done += 1
        except Exception as exc:  # pragma: no cover - defensive, logged
            log.exception("gamification_sync_failed", user_id=str(user_id), stages=stages, error=type(exc).__name__)
    return done


async def recompute_daily_scores_job() -> None:
    await run_for_all_users(("days",))


async def sync_xp_job() -> None:
    await run_for_all_users(("days", "streaks"))


async def evaluate_achievements_job() -> None:
    await run_for_all_users(("days", "streaks", "achievements"))


async def rotate_quests_job() -> None:
    await run_for_all_users(("quests",))
