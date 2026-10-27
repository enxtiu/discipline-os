"""Quests: global templates (``quests``) → per-user instances per period (``user_quests``).

* **Daily**: 3 per day, chosen deterministically per user and date from the active
  daily pool — only quests that make sense for the user (no workout quest unless
  today is a planned workout day, no water quest without a water target, no
  "3 tasks" quest with fewer than 3 open tasks …).
* **Weekly**: 2 per week (the user's week start), same rules.
* Progress is computed on read; a quest is ``completed`` while progress ≥ target;
  the user **claims** it for the XP (idempotent; ``409 quest_not_completed``
  otherwise); unclaimed quests expire with their period (``expired``).
* Rotation is idempotent: a period that already has its quests gets no new ones,
  even when the eligible set changed during the day.
* Nutrition quests need nutrition credit (a meal and intake at or above the safe
  floor, ``day_metrics.nutrition_credit``): protein and water day counts count only
  such days, and the daily water quest *completes* only on such a day — its progress
  still shows the millilitres drunk (review 8, H1). Finished days are judged against
  the targets they were finalised with (``GameContext.rules_for``).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.ids import new_id
from app.core.tz import iter_days
from app.models import Quest, QuestKind, QuestStatus, Task, TaskStatus, TimeEntry, UserQuest, XpKind
from app.services import day_metrics
from app.services.day_metrics import DayMetrics
from app.services.gamification import xp
from app.services.gamification.context import GameContext

DAILY_COUNT = 3
WEEKLY_COUNT = 2
TIMER_LOOKBACK_DAYS = 30
PLANNER_LOOKBACK_DAYS = 28
OPEN = (QuestStatus.ACTIVE, QuestStatus.COMPLETED)


@dataclass(slots=True, frozen=True)
class Facts:
    """What decides whether a quest makes sense for the user right now."""

    open_tasks: int
    open_p1: int
    uses_timer: bool
    recent_done: int
    today_plan: str | None  # "workout" | "rest" | None
    week_required: int  # workouts that make this week count (plan, else 3; 0 = rest week)


async def facts(db: AsyncSession, ctx: GameContext, today_plan: str | None, week_required: int) -> Facts:
    open_task = (
        (Task.user_id == ctx.user_id)
        & Task.status.not_in((TaskStatus.DONE, TaskStatus.CANCELLED))
        & Task.is_archived.is_(False)
    )
    row = (
        await db.execute(
            select(
                select(func.count()).select_from(Task).where(open_task).scalar_subquery(),
                select(func.count()).select_from(Task).where(open_task, Task.priority == 1).scalar_subquery(),
                exists()
                .where(
                    TimeEntry.user_id == ctx.user_id,
                    TimeEntry.is_deleted.is_(False),
                    TimeEntry.local_day >= ctx.today - timedelta(days=TIMER_LOOKBACK_DAYS),
                )
                .correlate(None),
                select(func.count())
                .select_from(Task)
                .where(
                    Task.user_id == ctx.user_id,
                    Task.status == TaskStatus.DONE,
                    Task.completed_at >= ctx.now - timedelta(days=PLANNER_LOOKBACK_DAYS),
                )
                .scalar_subquery(),
            )
        )
    ).one()
    return Facts(int(row[0] or 0), int(row[1] or 0), bool(row[2]), int(row[3] or 0), today_plan, week_required)


def requirement_met(name: str, ctx: GameContext, f: Facts) -> bool:
    t = ctx.targets
    checks = {
        "water_target": bool(t.water_ml),
        "protein_target": bool(t.protein_g),
        "steps_target": bool(t.steps),
        "open_tasks_3": f.open_tasks >= 3,
        "open_p1": f.open_p1 >= 1,
        "uses_timer": f.uses_timer,
        "uses_planner": f.open_tasks >= 5 or f.recent_done >= 5,
        "planned_workout_day": f.today_plan == "workout",
        "planned_workouts_week": f.week_required >= 1,
    }
    return checks.get(name, False)


def _criteria(quest: Quest) -> dict:
    return quest.criteria if isinstance(quest.criteria, dict) else {}


def eligible(quest: Quest, ctx: GameContext, f: Facts) -> bool:
    requires = _criteria(quest).get("requires") or []
    return isinstance(requires, list) and all(requirement_met(str(name), ctx, f) for name in requires)


def pick(pool: list[Quest], user_id: uuid.UUID, period_start: date, count: int) -> list[Quest]:
    """Deterministic per user and period: order by a hash of (user, period, code)."""

    def rank(quest: Quest) -> str:
        return hashlib.sha256(f"{user_id}:{period_start.isoformat()}:{quest.code}".encode()).hexdigest()

    return sorted(pool, key=rank)[:count]


def target_of(quest: Quest, ctx: GameContext, week_required: int) -> float:
    criteria = _criteria(quest)
    dynamic = criteria.get("target")
    value: float | None
    if dynamic == "water_ml":
        value = ctx.targets.water_ml
    elif dynamic == "steps":
        value = ctx.targets.steps
    elif dynamic == "planned_workouts":
        value = week_required
    else:
        try:
            value = float(criteria.get("gte", quest.target_value or 1))
        except (TypeError, ValueError):
            value = None
    return float(value) if value and value > 0 else float(quest.target_value or 1)


def unit_of(quest: Quest) -> str:
    unit = _criteria(quest).get("unit")
    return unit if unit in ("count", "ml", "steps", "min", "days") else "count"


def _days(first: date, last: date, history: dict[date, DayMetrics], ctx: GameContext) -> list[DayMetrics]:
    return [history[day] for day in iter_days(first, min(last, ctx.today)) if day in history]


def progress_of(quest: Quest, first: date, last: date, history: dict[date, DayMetrics], ctx: GameContext) -> float:
    """Progress over ``[first, min(last, today)]`` from the per-day metrics (what the
    quest shows; :func:`counted_progress` is what completes it)."""
    days = _days(first, last, history, ctx)
    metric = _criteria(quest).get("metric")
    if metric == "meals_logged":
        return float(sum(m.meals for m in days))  # logging, whatever the amount (D1)
    if metric == "water_ml":
        return float(sum(m.water_ml for m in days))
    if metric == "protein_target" or metric == "days_protein_target":
        return float(sum(1 for m in days if ctx.rules_for(m.day).protein_met(m)))
    if metric == "steps":
        return float(sum(m.steps or 0 for m in days))
    if metric == "tasks_completed":
        return float(sum(m.tasks_done for m in days))
    if metric == "p1_tasks_completed":
        return float(sum(m.tasks_done_p1 for m in days))
    if metric == "tracked_minutes":
        return float(int(sum(m.tracked_seconds + m.running_seconds for m in days) / 60))
    if metric in ("journal", "days_journal"):
        return float(sum(1 for m in days if m.journal))
    if metric == "sleep_logged":
        return float(sum(1 for m in days if m.sleep_minutes is not None))
    if metric == "workouts":
        return float(sum(m.workouts for m in days))
    if metric == "days_steps_target":
        return float(sum(1 for m in days if ctx.rules_for(m.day).steps_met(m)))
    if metric == "days_water_target":
        return float(sum(1 for m in days if ctx.rules_for(m.day).water_met(m)))
    return 0.0


def counted_progress(quest: Quest, first: date, last: date, history: dict[date, DayMetrics], ctx: GameContext) -> float:
    """The progress that completes a quest: :func:`progress_of`, except that water drunk
    on a day without nutrition credit (no meal at or above the safe floor) never
    completes a water quest — a day of water and no food earns nothing (H1)."""
    if _criteria(quest).get("metric") != "water_ml":
        return progress_of(quest, first, last, history, ctx)
    days = _days(first, last, history, ctx)
    return float(sum(m.water_ml for m in days if ctx.rules_for(m.day).credit(m)))


@dataclass(slots=True)
class QuestView:
    row: UserQuest
    quest: Quest
    progress: float
    target: float
    unit: str

    @property
    def claimable(self) -> bool:
        return self.row.status == QuestStatus.COMPLETED


async def _pool(db: AsyncSession, kind: QuestKind) -> list[Quest]:
    return list(
        (
            await db.scalars(
                select(Quest)
                .where(Quest.kind == kind, Quest.is_active.is_(True), Quest.user_id.is_(None))
                .order_by(Quest.code)
            )
        ).all()
    )


async def _insert(db: AsyncSession, ctx: GameContext, quests: list[Quest], start: date, end: date) -> int:
    if not quests:
        return 0
    rows = [
        {
            "id": new_id(),
            "user_id": ctx.user_id,
            "quest_id": quest.id,
            "period_start": start,
            "period_end": end,
            "progress": Decimal(0),
            "status": QuestStatus.ACTIVE,
            "created_at": ctx.now,
            "updated_at": ctx.now,
        }
        for quest in quests
    ]
    stmt = (
        pg_insert(UserQuest)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_user_quests_period")
        .returning(UserQuest.id)
    )
    return len((await db.execute(stmt)).all())


async def rotate(db: AsyncSession, ctx: GameContext, f: Facts) -> int:
    """Create today's daily quests and this week's weekly quests if missing."""
    created = 0
    has_daily = await db.scalar(
        select(
            exists().where(
                UserQuest.user_id == ctx.user_id,
                UserQuest.period_start == ctx.today,
                UserQuest.period_end == ctx.today,
                UserQuest.quest_id.in_(select(Quest.id).where(Quest.kind == QuestKind.DAILY)),
            )
        )
    )
    if not has_daily:
        pool = [quest for quest in await _pool(db, QuestKind.DAILY) if eligible(quest, ctx, f)]
        created += await _insert(db, ctx, pick(pool, ctx.user_id, ctx.today, DAILY_COUNT), ctx.today, ctx.today)
    has_weekly = await db.scalar(
        select(
            exists().where(
                UserQuest.user_id == ctx.user_id,
                UserQuest.period_start <= ctx.today,
                UserQuest.period_end >= ctx.today,
                UserQuest.quest_id.in_(select(Quest.id).where(Quest.kind == QuestKind.WEEKLY)),
            )
        )
    )
    if not has_weekly:
        start = ctx.week_start_day
        pool = [quest for quest in await _pool(db, QuestKind.WEEKLY) if eligible(quest, ctx, f)]
        created += await _insert(
            db, ctx, pick(pool, ctx.user_id, start, WEEKLY_COUNT), start, start + timedelta(days=6)
        )
    return created


async def expire(db: AsyncSession, ctx: GameContext) -> int:
    """Unclaimed quests of past periods expire (completed-but-unclaimed included)."""
    result = await db.execute(
        update(UserQuest)
        .where(UserQuest.user_id == ctx.user_id, UserQuest.period_end < ctx.today, UserQuest.status.in_(OPEN))
        .values(status=QuestStatus.EXPIRED, updated_at=ctx.now)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def _status_for(progress: float, target: float) -> QuestStatus:
    return QuestStatus.COMPLETED if progress >= target else QuestStatus.ACTIVE


async def refresh(
    db: AsyncSession, ctx: GameContext, history: dict[date, DayMetrics], week_required: int
) -> list[QuestView]:
    """The current period's quests with live progress; statuses are kept in step
    (never touching a claimed one)."""
    rows = (
        await db.execute(
            select(UserQuest, Quest)
            .join(Quest, Quest.id == UserQuest.quest_id)
            .where(
                UserQuest.user_id == ctx.user_id,
                UserQuest.period_start <= ctx.today,
                UserQuest.period_end >= ctx.today,
            )
        )
    ).all()
    views: list[QuestView] = []
    for row, quest in rows:
        target = target_of(quest, ctx, week_required)
        if row.status in OPEN:
            progress = progress_of(quest, row.period_start, row.period_end, history, ctx)
            status = _status_for(counted_progress(quest, row.period_start, row.period_end, history, ctx), target)
            completed_at = row.completed_at if status == QuestStatus.COMPLETED else None
            if status == QuestStatus.COMPLETED and completed_at is None:
                completed_at = ctx.now
            if (row.status, Decimal(str(progress)), row.completed_at) != (
                status,
                Decimal(str(row.progress or 0)),
                completed_at,
            ):
                await db.execute(
                    update(UserQuest)
                    .where(UserQuest.id == row.id, UserQuest.status.in_(OPEN))
                    .values(
                        status=status, progress=Decimal(str(progress)), completed_at=completed_at, updated_at=ctx.now
                    )
                    .execution_options(synchronize_session=False)
                )
                row.status, row.progress, row.completed_at = status, Decimal(str(progress)), completed_at
        else:
            progress = float(row.progress or 0)
        views.append(QuestView(row=row, quest=quest, progress=progress, target=target, unit=unit_of(quest)))
    order = {QuestKind.DAILY: 0, QuestKind.WEEKLY: 1}
    views.sort(key=lambda view: (order.get(view.quest.kind, 2), view.quest.code))
    return views


async def claim(db: AsyncSession, ctx: GameContext, user_quest_id: uuid.UUID) -> tuple[QuestView, int]:
    """Claim a completed quest: permanent XP once. A second claim returns the same quest
    and 0 XP; an unfinished quest → 409 ``quest_not_completed``; a past period → 409
    ``quest_expired``."""
    found = (
        await db.execute(
            select(UserQuest, Quest)
            .join(Quest, Quest.id == UserQuest.quest_id)
            .where(UserQuest.id == user_quest_id, UserQuest.user_id == ctx.user_id)
            .with_for_update(of=UserQuest)
        )
    ).first()
    if found is None:
        raise NotFound("Quest not found", code="quest_not_found", details={"id": str(user_quest_id)})
    row, quest = found
    plans = await day_metrics.week_plans(db, ctx.user_id, row.period_start, row.period_start, ctx.week_start)
    week_plan = next(iter(plans.values()), None)
    week_required = day_metrics.required_workouts(week_plan) if week_plan is not None else 0
    target = target_of(quest, ctx, week_required)
    if row.status == QuestStatus.CLAIMED:
        return QuestView(row, quest, float(row.progress or 0), target, unit_of(quest)), 0
    if row.status == QuestStatus.EXPIRED or row.period_end < ctx.today:
        raise Conflict(
            "This quest's period is over", code="quest_expired", details={"period_end": row.period_end.isoformat()}
        )
    last = min(row.period_end, ctx.today)
    history = await day_metrics.collect(db, ctx.user_id, row.period_start, last, ctx.tz, now=ctx.now)
    progress = progress_of(quest, row.period_start, row.period_end, history, ctx)
    if counted_progress(quest, row.period_start, row.period_end, history, ctx) < target:
        raise Conflict(
            "This quest is not completed yet",
            code="quest_not_completed",
            details={"progress": progress, "target": target},
        )
    row.status = QuestStatus.CLAIMED
    row.progress = Decimal(str(progress))
    row.completed_at = row.completed_at or ctx.now
    row.claimed_at = ctx.now
    row.updated_at = ctx.now
    await db.flush()
    awarded = await xp.award_permanent(
        db,
        ctx,
        key=f"quest:{row.id}",
        kind=XpKind.QUEST_COMPLETED,
        points=int(quest.xp_reward or 0),
        reason=quest.code,
        ref_type="quest",
        ref_id=row.id,
    )
    return QuestView(row, quest, progress, target, unit_of(quest)), awarded
