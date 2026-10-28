"""One gamification run for one user — the same code path for the scheduler jobs and
for the endpoints (``GET /gamification/today`` recomputes on read).

Order, inside one transaction holding a per-user advisory lock (overlapping runs —
two jobs, a job and a request — simply take turns; every write is idempotent):

1. **days** — daily scores and day XP for the open window (yesterday, today) and,
   once each, for older days without a *final* row (first run, downtime): those are
   then frozen for good — the row says so (``components.final``), and a final day is
   never recomputed, whatever the timezone does later (a day that is "yesterday"
   again after a move west keeps its frozen row). Score and XP of a day are always
   finalised together.
2. **streaks** — recomputed from the history; rows upserted (longest never
   decreases); overall-streak milestones paid once per run. A finished day is judged
   against the targets it was finalised with (its stored row), a finished week against
   the requirement frozen with its last day's row the first time the engine saw the
   week finished; the current targets apply to the open window only and the current
   plan to the current week only — so a weigh-in or a plan edit never re-judges the
   past (review 8, M1).
3. **achievements** — metrics, unlocks, permanent XP (repeated while a level-up
   unlocks more).
4. **quests** — expire past periods, rotate today/this week, refresh progress.

``persist=False`` computes the same snapshot without any write (the stage-7
streak reminder uses it).

**Bounded work per call** (review 8, L5): one call finalises at most
``PENDING_PER_RUN`` pending days, oldest first — a request (or the stage-7 reminder
check) never scores years of history while holding the per-user lock. The jobs and
``python -m app.cli backfill-scores`` finish a backlog (the first run after the
stage-8 deploy, a long downtime) with :func:`sync_fully`: chunks of
``BACKFILL_CHUNK`` days, each in its own short transaction, then the requested stages
once more on the complete history. Oldest first keeps the results identical to one
big run: every finalised day sees all earlier days final, and while days are still
pending the overall-streak milestones wait (a run may only look shorter then, never
start elsewhere).
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
HISTORY_DAYS = 1100  # how far back history is read and scored once
LOCK_SCOPE = "gamification"
MAX_LEVEL_ROUNDS = 5
PENDING_PER_RUN = 60  # pending days one call may finalise (requests, the reminder check)
BACKFILL_CHUNK = 60  # days per transaction when the jobs / the CLI finish a backlog
MAX_BACKFILL_ROUNDS = HISTORY_DAYS // BACKFILL_CHUNK + 3


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
    finalised: int = 0  # pending days computed for the last time in this run
    pending_left: int = 0  # pending days left for a later run (backlog beyond the bound)

    @property
    def today_score(self) -> DayScore:
        return self.computed[self.ctx.today]

    @property
    def week_required(self) -> int:
        plan = self.plans.get(self.ctx.week_start_day)
        return required_workouts(plan) if plan is not None else required_workouts(WeekPlan(self.ctx.today, 0, False))


def _pending(
    stored_days: Iterable[date], final: set[date], ctx: GameContext, first_data: date | None, oldest: date
) -> list[date]:
    """Days before yesterday without a final row, oldest first: never scored (from the
    first data day on) or scored only while they were in the open window. Each is
    computed once more and then frozen for good."""
    last = ctx.today - timedelta(days=2)
    candidates = set(iter_days(max(first_data, oldest), last)) if first_data is not None else set()
    candidates |= {day for day in stored_days if day <= last}
    return sorted(day for day in candidates if day not in final)


def _spans(days: list[date], most: int = 3) -> list[tuple[date, date]]:
    """Contiguous runs of the sorted ``days`` (the oldest pending days and the window are
    two), or one range over all of them when they are scattered — the rows behind the
    awards are read for those days, not for the years in between."""
    spans: list[tuple[date, date]] = []
    for day in days:
        if spans and day == spans[-1][1] + timedelta(days=1):
            spans[-1] = (spans[-1][0], day)
        else:
            spans.append((day, day))
    return spans if len(spans) <= most else [(days[0], days[-1])]


def _closed_week(ctx: GameContext, day: date) -> date | None:
    """The start of the finished week ``day`` is the last day of, if any."""
    week = week_start_for(day, ctx.week_start)
    return week if day == week + timedelta(days=6) and week < ctx.week_start_day else None


async def sync_user(
    db,
    user_id: uuid.UUID,
    now: datetime | None = None,
    *,
    stages: Iterable[str] = STAGES,
    persist: bool = True,
    pending_limit: int | None = PENDING_PER_RUN,
) -> GameSnapshot | None:
    """Run the requested stages for one active user; ``None`` for a missing/disabled one.
    ``pending_limit``: at most that many pending days are finalised, oldest first
    (``None``: all of them — only for tests and tools that compare)."""
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
    stored = await score.load_flags(db, user_id, start, today)
    score.freeze(ctx, stored)  # final days: their own targets from now on
    plans = day_metrics.weeks_from_plan(plan, start, today, ctx.week_start)  # the current plan …
    plans.update({week: frozen for week, frozen in score.frozen_weeks(ctx, stored).items() if week in plans})

    # a final row is never recomputed — even when a move west makes its day "yesterday"
    final = {day for day, flag in stored.items() if flag.is_final(today)}
    # yesterday stays in the window while it has data or a stored row (a deleted meal
    # must lose its XP); a brand-new account gets no empty "yesterday"
    with_yesterday = (first_data is not None and first_data <= yesterday) or yesterday in stored
    window = [day for day in ([yesterday, today] if with_yesterday else [today]) if day not in final or day == today]
    backlog = _pending(stored, final, ctx, first_data, oldest)
    pending = backlog if pending_limit is None else backlog[: max(0, pending_limit)]
    computed = {day: score.compute_day_score(history[day], ctx, provisional=day == today) for day in pending + window}
    writable = sorted(day for day in computed if day not in final)  # (today is final only after a ≥ 2-day jump west)

    flags: dict[date, DayFlag] = {day: DayFlag(flag.score, flag.credit) for day, flag in stored.items()}
    flags.update({day: DayFlag(computed[day].value, computed[day].credit) for day in writable})
    overall_days = {day for day, flag in flags.items() if flag.credit and flag.score >= score.OVERALL_THRESHOLD}
    stored_streaks = await streaks.stored_rows(db, user_id)
    longest = {kind: int(row["longest_count"] or 0) for kind, row in stored_streaks.items()}
    states = streaks.compute(history, overall_days, plans, ctx, longest, track=set(computed))
    snapshot = GameSnapshot(ctx=ctx, history=history, computed=computed, flags=flags, plans=plans, streaks=states)
    snapshot.finalised, snapshot.pending_left = len(pending), len(backlog) - len(pending)

    if persist and "days" in stages:
        # finished weeks whose last day is final but carries no requirement (rows from
        # before review 8, weeks re-bucketed by a new week start): frozen now, once
        unfrozen = {
            day: plans[week] for day in final if (week := _closed_week(ctx, day)) in plans and stored[day].week is None
        }
        await score.annotate_weeks(db, ctx, unfrozen)
    if persist and "days" in stages and writable:
        refs: dict[date, xp.DayRefs] = {}
        for first, last in _spans(writable):
            refs.update(await xp.load_refs(db, ctx, first, last))
        awards = [
            award
            for day in writable
            for award in xp.day_awards(day, history[day], refs.get(day), ctx, finished=day < today)
        ]
        await xp.sync_days(db, ctx, writable, awards)
    if persist and "streaks" in stages:
        await streaks.persist(db, ctx, states, stored_streaks)
        if not snapshot.pending_left:  # milestones from the complete history only (identical results)
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
    if persist and "days" in stages and writable:  # last, so xp_earned includes this run's permanent awards
        xp_by_day = await xp.per_day(db, user_id, writable[0], today)
        rows = [computed[day] for day in writable]
        weeks = {day: plans[week] for day in writable if (week := _closed_week(ctx, day)) in plans}
        await score.store(db, ctx, rows, xp_by_day, states[0].count_on, final=pending, weeks=weeks)
        snapshot.xp_today = xp_by_day.get(today, 0)
    else:
        snapshot.xp_today = (await xp.per_day(db, user_id, today, today)).get(today, 0)
    return snapshot


# --------------------------------------------------------------------------- #
# Jobs and tools: the whole backlog, in chunks
# --------------------------------------------------------------------------- #
async def sync_fully(
    user_id: uuid.UUID, now: datetime | None = None, *, stages: Iterable[str] = STAGES
) -> tuple[GameSnapshot | None, int]:
    """The requested stages for one user, finishing any backlog of pending days first:
    chunks of ``BACKFILL_CHUNK`` days (oldest first), each in its own transaction — the
    per-user lock is never held for long — then the stages once more on the complete
    history. Steady state: one run. Returns the last snapshot (``None``: missing or
    disabled account) and the number of days finalised."""
    stages = tuple(stages)
    now = now or utcnow()
    async with session_scope() as db:
        snapshot = await sync_user(db, user_id, now, stages=stages, pending_limit=BACKFILL_CHUNK)
    if snapshot is None or "days" not in stages:
        return snapshot, 0
    finalised = snapshot.finalised
    if not snapshot.pending_left:
        return snapshot, finalised
    for _ in range(MAX_BACKFILL_ROUNDS):
        async with session_scope() as db:
            chunk = await sync_user(db, user_id, now, stages=("days",), pending_limit=BACKFILL_CHUNK)
        if chunk is None:
            return None, finalised
        finalised += chunk.finalised
        if not chunk.pending_left or not chunk.finalised:
            break
    async with session_scope() as db:
        snapshot = await sync_user(db, user_id, now, stages=stages, pending_limit=BACKFILL_CHUNK)
    return snapshot, finalised + (snapshot.finalised if snapshot is not None else 0)


async def backfill(user_ids: Iterable[uuid.UUID] | None = None, now: datetime | None = None) -> dict[uuid.UUID, int]:
    """Score and freeze every pending day of these (default: all active) users, then
    streaks, achievements and quests — ``python -m app.cli backfill-scores``. Idempotent
    and safe next to the API and the jobs (per-user lock). Days finalised per user."""
    now = now or utcnow()
    if user_ids is None:
        async with session_scope() as db:
            user_ids = (await db.scalars(select(User.id).where(User.status == UserStatus.ACTIVE))).all()
    out: dict[uuid.UUID, int] = {}
    for user_id in user_ids:
        snapshot, finalised = await sync_fully(user_id, now)
        if snapshot is not None:
            out[user_id] = finalised
    return out


# --------------------------------------------------------------------------- #
# Scheduler entry points (registered in app/services/hooks.py)
# --------------------------------------------------------------------------- #
async def run_for_all_users(stages: Iterable[str], now: datetime | None = None) -> int:
    """Every active user (a backlog finished in chunks, see :func:`sync_fully`); one
    failure never stops a run."""
    stages = tuple(stages)
    now = now or utcnow()
    async with session_scope() as db:
        user_ids = (await db.scalars(select(User.id).where(User.status == UserStatus.ACTIVE))).all()
    done = 0
    for user_id in user_ids:
        try:
            if "days" in stages:
                snapshot, _ = await sync_fully(user_id, now, stages=stages)
            else:  # nothing to finalise in a quests-only run
                async with session_scope() as db:
                    snapshot = await sync_user(db, user_id, now, stages=stages, pending_limit=0)
            if snapshot is not None:
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
