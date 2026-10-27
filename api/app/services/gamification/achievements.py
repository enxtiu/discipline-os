"""Achievements: global definitions (seeded, ``{"metric": name, "gte": N}``) unlocked
once per user (``user_achievements``), with permanent XP on unlock and progress for
locked ones computed on read.

Metrics (all server-side, all-time unless noted):

* totals: ``meals_logged_total``, ``workouts_total``, ``prs_total``, ``steps_best_day``,
  ``tasks_completed_total``, ``p1_tasks_completed_total``, ``projects_completed_total``,
  ``tracked_minutes_total``, ``journal_entries_total`` (with content), ``weigh_ins_total``,
  ``nights_7h_total`` (sleep ≥ 420 min), ``quests_claimed_total``;
* days: ``days_protein_target``, ``days_water_target`` (target met *and* nutrition
  credit: a meal and intake ≥ the safe floor), ``days_steps_target`` — finished days
  judged against the targets they were finalised with;
* scores (finished days with credit — a meal logged and intake ≥ the floor):
  ``perfect_days`` (score ≥ 95), ``days_score_70_in_week`` (best week);
* ``streak_longest_<kind>`` and ``level``.

Nothing rewards low intake, fasting, weight-loss speed or burned calories.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models import (
    Achievement,
    BodyMetric,
    HealthDaily,
    JournalEntry,
    MealLog,
    PersonalRecord,
    Project,
    ProjectStatus,
    QuestStatus,
    SleepLog,
    Task,
    TaskStatus,
    TimeEntry,
    UserAchievement,
    UserQuest,
    WorkoutLog,
    WorkoutStatus,
    XpKind,
)
from app.services.day_metrics import DayMetrics, journal_has_content, week_start_for
from app.services.gamification import xp
from app.services.gamification.context import GameContext
from app.services.gamification.score import PERFECT_THRESHOLD, STRONG_THRESHOLD
from app.services.gamification.streaks import StreakState

TOTALS = (
    "meals_logged_total",
    "workouts_total",
    "prs_total",
    "steps_best_day",
    "tasks_completed_total",
    "p1_tasks_completed_total",
    "projects_completed_total",
    "tracked_minutes_total",
    "journal_entries_total",
    "weigh_ins_total",
    "nights_7h_total",
    "quests_claimed_total",
)
NIGHT_MINUTES = 420


@dataclass(slots=True, frozen=True)
class DayFlag:
    """Score of a day as achievements see it: the value and the credit flag."""

    score: float
    credit: bool


async def totals(db: AsyncSession, user_id: uuid.UUID) -> dict[str, float]:
    """All-time totals in one round trip (scalar subqueries)."""
    done = (Task.user_id == user_id) & (Task.status == TaskStatus.DONE)
    workout_ok = (
        (WorkoutLog.user_id == user_id)
        & WorkoutLog.is_deleted.is_(False)
        & (WorkoutLog.status == WorkoutStatus.COMPLETED)
    )
    queries = {
        "meals_logged_total": select(func.count())
        .select_from(MealLog)
        .where(MealLog.user_id == user_id, MealLog.is_deleted.is_(False)),
        "workouts_total": select(func.count()).select_from(WorkoutLog).where(workout_ok),
        "prs_total": select(func.count())
        .select_from(PersonalRecord)
        .join(WorkoutLog, WorkoutLog.id == PersonalRecord.workout_log_id)
        .where(PersonalRecord.user_id == user_id, workout_ok),
        "steps_best_day": select(func.coalesce(func.max(HealthDaily.steps), 0)).where(HealthDaily.user_id == user_id),
        "tasks_completed_total": select(func.count()).select_from(Task).where(done),
        "p1_tasks_completed_total": select(func.count()).select_from(Task).where(done, Task.priority == 1),
        "projects_completed_total": select(func.count())
        .select_from(Project)
        .where(Project.user_id == user_id, Project.status == ProjectStatus.COMPLETED),
        "tracked_minutes_total": select(func.coalesce(func.sum(TimeEntry.duration_s), 0) / 60).where(
            TimeEntry.user_id == user_id, TimeEntry.is_deleted.is_(False), TimeEntry.duration_s.is_not(None)
        ),
        "journal_entries_total": select(func.count())
        .select_from(JournalEntry)
        .where(JournalEntry.user_id == user_id, journal_has_content()),
        "weigh_ins_total": select(func.count())
        .select_from(BodyMetric)
        .where(BodyMetric.user_id == user_id, BodyMetric.weight_kg.is_not(None)),
        "nights_7h_total": select(func.count())
        .select_from(SleepLog)
        .where(SleepLog.user_id == user_id, SleepLog.duration_min >= NIGHT_MINUTES),
        "quests_claimed_total": select(func.count())
        .select_from(UserQuest)
        .where(UserQuest.user_id == user_id, UserQuest.status == QuestStatus.CLAIMED),
    }
    row = (await db.execute(select(*(query.scalar_subquery() for query in queries.values())))).one()
    return {name: float(value or 0) for name, value in zip(queries, row, strict=True)}


def day_metrics(history: dict[date, DayMetrics], flags: dict[date, DayFlag], ctx: GameContext) -> dict[str, float]:
    protein = sum(1 for day, m in history.items() if ctx.rules_for(day).protein_met(m))
    water = sum(1 for day, m in history.items() if ctx.rules_for(day).water_met(m))
    steps = sum(1 for day, m in history.items() if ctx.rules_for(day).steps_met(m))
    finished = {day: flag for day, flag in flags.items() if day < ctx.today and flag.credit}
    perfect = sum(1 for flag in finished.values() if flag.score >= PERFECT_THRESHOLD)
    strong_by_week: dict[date, int] = defaultdict(int)
    for day, flag in finished.items():
        if flag.score >= STRONG_THRESHOLD:
            strong_by_week[week_start_for(day, ctx.week_start)] += 1
    return {
        "days_protein_target": float(protein),
        "days_water_target": float(water),
        "days_steps_target": float(steps),
        "perfect_days": float(perfect),
        "days_score_70_in_week": float(max(strong_by_week.values(), default=0)),
    }


async def compute_metrics(
    db: AsyncSession,
    ctx: GameContext,
    history: dict[date, DayMetrics],
    flags: dict[date, DayFlag],
    streaks: Iterable[StreakState],
    level: int,
) -> dict[str, float]:
    metrics = await totals(db, ctx.user_id)
    metrics.update(day_metrics(history, flags, ctx))
    for state in streaks:
        metrics[f"streak_longest_{state.kind.value}"] = float(state.longest)
    metrics["level"] = float(level)
    return metrics


def criterion(row: Achievement) -> tuple[str | None, float]:
    criteria = row.criteria if isinstance(row.criteria, dict) else {}
    metric = criteria.get("metric")
    try:
        gte = float(criteria.get("gte", row.target_value or 0))
    except (TypeError, ValueError):
        gte = float(row.target_value or 0)
    return (metric if isinstance(metric, str) else None), gte


async def evaluate(db: AsyncSession, ctx: GameContext, metrics: dict[str, float]) -> list[Achievement]:
    """Unlock what the metrics now satisfy (idempotent; XP once per achievement)."""
    rows = (await db.scalars(select(Achievement).where(Achievement.is_active.is_(True)))).all()
    unlocked = set(
        (await db.scalars(select(UserAchievement.achievement_id).where(UserAchievement.user_id == ctx.user_id))).all()
    )
    fresh: list[Achievement] = []
    for row in rows:
        if row.id in unlocked:
            continue
        metric, gte = criterion(row)
        if metric is None or gte <= 0 or metrics.get(metric, 0.0) < gte:
            continue
        stmt = (
            pg_insert(UserAchievement)
            .values(
                id=new_id(),
                user_id=ctx.user_id,
                achievement_id=row.id,
                unlocked_at=ctx.now,
                progress=Decimal(str(gte)),
                seen=False,
                celebrated=False,
                created_at=ctx.now,
                updated_at=ctx.now,
            )
            .on_conflict_do_nothing(constraint="uq_user_achievements")
            .returning(UserAchievement.id)
        )
        if (await db.execute(stmt)).first() is None:
            continue  # unlocked meanwhile by an overlapping run
        await xp.award_permanent(
            db,
            ctx,
            key=f"achievement:{row.code}",
            kind=XpKind.ACHIEVEMENT_UNLOCKED,
            points=int(row.xp_reward or 0),
            reason=row.code,
            ref_type="achievement",
            ref_id=row.id,
        )
        fresh.append(row)
    return fresh


@dataclass(slots=True)
class AchievementView:
    row: Achievement
    unlocked: UserAchievement | None
    value: float | None  # the metric value now (None when unknown)

    @property
    def target(self) -> float:
        return criterion(self.row)[1]

    @property
    def masked(self) -> bool:
        return bool(self.row.is_secret) and self.unlocked is None


async def listing(db: AsyncSession, user_id: uuid.UUID, metrics: dict[str, float] | None) -> list[AchievementView]:
    """Unlocked first (newest first), then locked in catalogue order."""
    rows = (
        await db.scalars(
            select(Achievement)
            .where(Achievement.is_active.is_(True))
            .order_by(Achievement.sort_order, Achievement.code)
        )
    ).all()
    mine = {
        row.achievement_id: row
        for row in (await db.scalars(select(UserAchievement).where(UserAchievement.user_id == user_id))).all()
    }
    views = []
    for row in rows:
        metric, _ = criterion(row)
        value = metrics.get(metric) if metrics is not None and metric is not None else None
        views.append(AchievementView(row=row, unlocked=mine.get(row.id), value=value))
    unlocked = sorted(
        (view for view in views if view.unlocked is not None),
        key=lambda view: (view.unlocked.unlocked_at, view.row.sort_order),  # type: ignore[union-attr]
        reverse=True,
    )
    return unlocked + [view for view in views if view.unlocked is None]


async def unseen(db: AsyncSession, user_id: uuid.UUID) -> list[AchievementView]:
    rows = (
        await db.execute(
            select(Achievement, UserAchievement)
            .join(UserAchievement, UserAchievement.achievement_id == Achievement.id)
            .where(UserAchievement.user_id == user_id, UserAchievement.seen.is_(False))
            .order_by(UserAchievement.unlocked_at, Achievement.sort_order)
        )
    ).all()
    return [AchievementView(row=row[0], unlocked=row[1], value=None) for row in rows]


async def mark_seen(db: AsyncSession, user_id: uuid.UUID, ids: Iterable[uuid.UUID]) -> int:
    """Mark the caller's unlocked achievements as seen (unknown ids are ignored)."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return 0
    result = await db.execute(
        update(UserAchievement)
        .where(UserAchievement.user_id == user_id, UserAchievement.achievement_id.in_(wanted))
        .values(seen=True, celebrated=True)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)
