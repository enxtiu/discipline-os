"""Daily score (0–100) and the three rings.

Components (each 0..1) with default weights summing to 100; excluded components
drop out and the rest are renormalised (user overrides in
``settings.xp_weights.score_weights``):

======== ====== ===========================================================
key      weight value
======== ====== ===========================================================
kcal     25     the calorie curve (below) — peaks *inside* the target zone
protein  15     min(1, protein / target)
water    10     min(1, water / target)
fiber    5      min(1, fiber / target)
activity 15     1 with a completed workout, else min(1, steps / target)
tasks    15     done / (done + still-open tasks due that day); none → excluded
sleep    10     min(1, minutes / 420); nothing logged → excluded
review   5      1 with a journal entry that has content
======== ====== ===========================================================

Safety, binding: a component without a target is excluded, but **nutrition
components with no meals logged are 0, never excluded** — not logging must not
raise the score. Burned calories never enter the kcal part (the target is the
target). Score-based *credit* (the overall streak, perfect days, strong weeks) also
requires ``credit``: at least one meal and intake at or above the safe floor —
whatever the user's weights are.

Stored rows (``daily_scores``) are the memory of finished days: ``components.final``
marks the last computation, the breakdown keeps the targets, floor and zone the day
was judged with (``detail.target``, ``detail.floor``) — read back by
:func:`load_flags` so streaks, achievements, quests and /stats judge a finished day
against *its* targets, never today's — and the row of a finished week's last day
keeps that week's workout requirement (``components.week``), frozen the first time the
engine sees the week finished (no migration: JSON keys of an existing column).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Float, bindparam, cast, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.tz import ensure_utc
from app.models import DailyScore
from app.schemas.gamification import DEFAULT_SCORE_WEIGHTS, SCORE_KEYS
from app.services.day_metrics import (
    DayMetrics,
    KcalZone,
    WeekPlan,
    kcal_zone,
    nutrition_credit,
    required_workouts,
    week_start_for,
)
from app.services.gamification.context import DayRules, GameContext, Targets

SCORE_VERSION = 1
OVERALL_THRESHOLD = 60.0  # a day counts for the overall streak
STRONG_THRESHOLD = 70.0  # "strong week" achievements
PERFECT_THRESHOLD = 95.0  # perfect days
SLEEP_TARGET_MIN = 420
CHUNK = 500  # rows per INSERT (PostgreSQL allows 65 535 bind parameters per statement)
RINGS: dict[str, tuple[str, ...]] = {
    "nutrition": ("kcal", "protein", "water", "fiber"),
    "activity": ("activity",),
    "discipline": ("tasks", "sleep", "review"),
}


# --------------------------------------------------------------------------- #
# The calorie curve
# --------------------------------------------------------------------------- #
def kcal_curve(kcal: float, zone: KcalZone, *, meals: int, provisional: bool) -> tuple[float, str]:
    """``(value 0..1, state)`` of the kcal component. ``zone`` must have a target.

    Finished day:
      no meals → 0 · kcal < F → 0 (never rewarded) · inside ``[max(F, 0.92T), 1.06T]`` → 1 ·
      ``F ≤ kcal < zone_low`` → 0.25 → 1 linearly · ``zone_high < kcal ≤ 1.35T`` → 1 → 0.25 ·
      then 0.25 → 0 at 1.6T.
    Today (provisional): below the zone it is plain progress ``kcal / zone_low`` (not a
    judgement); in and above the zone the finished-day curve applies.
    """
    assert zone.low is not None and zone.high is not None and zone.target is not None
    if meals <= 0:
        return 0.0, "no_meals"
    low, high, target, floor = zone.low, zone.high, float(zone.target), float(zone.floor)
    if low <= kcal <= high:
        return 1.0, "in_zone"
    if kcal < low:
        if provisional:
            return _clamp(kcal / low), "progress"
        if kcal < floor:
            return 0.0, "below_floor"
        return _clamp(0.25 + 0.75 * (kcal - floor) / (low - floor)), "below_zone"
    upper_1 = max(1.35 * target, high)  # 1.0 → 0.25
    upper_2 = max(1.6 * target, upper_1)  # 0.25 → 0
    if kcal <= upper_1:
        return _clamp(1.0 - 0.75 * (kcal - high) / (upper_1 - high)), "above_zone"
    if kcal < upper_2:
        return _clamp(0.25 * (1.0 - (kcal - upper_1) / (upper_2 - upper_1))), "above_zone"
    return 0.0, "above_zone"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ratio(amount: float, target: float) -> float:
    return _clamp(amount / target) if target > 0 else 0.0


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Component:
    key: str
    value: float
    excluded: bool
    detail: dict[str, Any]
    weight: float = 0.0  # effective, filled by the weighting step


@dataclass(slots=True)
class DayScore:
    day: date
    value: float
    provisional: bool
    credit: bool  # ≥ 1 meal and intake ≥ the safe floor
    components: list[Component]
    weights: dict[str, float] = field(default_factory=dict)
    computed_at: datetime | None = None

    @property
    def counts_for_overall(self) -> bool:
        return self.credit and self.value >= OVERALL_THRESHOLD

    def component(self, key: str) -> Component | None:
        return next((item for item in self.components if item.key == key), None)


def credit_ok(metrics: DayMetrics, floor: int) -> bool:
    """Score-based credit needs a logged day at or above the floor (never not-logging,
    never intake below the floor) — the shared ``day_metrics.nutrition_credit``. There is
    deliberately no upper bound (review 8, D2): above 1.6·T the kcal part is already 0,
    and credit stays "a meal at or above the floor"."""
    return nutrition_credit(metrics, floor)


def _components(m: DayMetrics, ctx: GameContext, provisional: bool) -> list[Component]:
    t = ctx.targets
    out: list[Component] = []

    # kcal — the target zone, never the burned calories
    kcal_detail: dict[str, Any] = {
        "amount": round(m.kcal),
        "unit": "kcal",
        "floor": ctx.floor,
        "meals": m.meals,
    }
    if ctx.zone.target is None:
        out.append(Component("kcal", 0.0, True, {**kcal_detail, "state": "no_target"}))
    else:
        value, state = kcal_curve(m.kcal, ctx.zone, meals=m.meals, provisional=provisional)
        kcal_detail.update(state=state, target=ctx.zone.target, zone_low=ctx.zone.low_int, zone_high=ctx.zone.high_int)
        out.append(Component("kcal", value, False, kcal_detail))

    for key, amount, target in (("protein", m.protein_g, t.protein_g), ("fiber", m.fiber_g, t.fiber_g)):
        detail = {"amount": round(amount, 1), "target": target, "unit": "g", "meals": m.meals}
        if not target:
            out.append(Component(key, 0.0, True, {**detail, "state": "no_target"}))
        elif m.meals <= 0:
            out.append(Component(key, 0.0, False, {**detail, "state": "no_meals"}))
        else:
            out.append(Component(key, _ratio(amount, target), False, {**detail, "state": "ok"}))

    water = {"amount": m.water_ml, "target": t.water_ml, "unit": "ml"}
    if not t.water_ml:
        out.append(Component("water", 0.0, True, {**water, "state": "no_target"}))
    else:
        out.append(Component("water", _ratio(m.water_ml, t.water_ml), False, {**water, "state": "ok"}))

    activity = {"amount": m.steps, "target": t.steps, "unit": "steps", "workouts": m.workouts}
    if m.workouts > 0:
        out.append(Component("activity", 1.0, False, {**activity, "state": "workout"}))
    elif not t.steps:
        out.append(Component("activity", 0.0, True, {**activity, "state": "no_target"}))
    else:
        state = "ok" if m.steps is not None else "no_data"
        out.append(Component("activity", _ratio(m.steps or 0, t.steps), False, {**activity, "state": state}))

    tasks = {"done": m.tasks_done, "open_due": m.tasks_open_due}
    if m.tasks_done + m.tasks_open_due == 0:
        out.append(Component("tasks", 0.0, True, {**tasks, "state": "no_tasks"}))
    else:
        value = m.tasks_done / (m.tasks_done + m.tasks_open_due)
        out.append(Component("tasks", value, False, {**tasks, "state": "ok"}))

    sleep = {"amount": m.sleep_minutes, "target": SLEEP_TARGET_MIN, "unit": "min"}
    if m.sleep_minutes is None:
        out.append(Component("sleep", 0.0, True, {**sleep, "state": "no_data"}))
    else:
        out.append(Component("sleep", _ratio(m.sleep_minutes, SLEEP_TARGET_MIN), False, {**sleep, "state": "ok"}))

    out.append(Component("review", 1.0 if m.journal else 0.0, False, {"has_entry": m.journal, "state": "ok"}))
    return out


def _apply_weights(components: list[Component], weights: dict[str, float]) -> dict[str, float]:
    present = [item for item in components if not item.excluded]
    total = sum(weights.get(item.key, 0.0) for item in present)
    if total <= 0:  # the user zeroed every present component: fall back to the defaults
        weights = DEFAULT_SCORE_WEIGHTS
        total = sum(weights[item.key] for item in present)
    effective: dict[str, float] = {}
    for item in components:
        item.weight = 0.0 if item.excluded or total <= 0 else weights.get(item.key, 0.0) * 100.0 / total
        effective[item.key] = round(item.weight, 2)
    return effective


def compute_day_score(metrics: DayMetrics, ctx: GameContext, *, provisional: bool) -> DayScore:
    """The score of one day from its metrics (pure)."""
    components = _components(metrics, ctx, provisional)
    effective = _apply_weights(components, ctx.score_weights)
    value = sum(item.value * item.weight for item in components if not item.excluded)
    return DayScore(
        day=metrics.day,
        value=round(max(0.0, min(100.0, value)), 1),
        provisional=provisional,
        credit=credit_ok(metrics, ctx.floor),
        components=components,
        weights=effective,
        computed_at=ctx.now,
    )


def rings(score: DayScore, weights: dict[str, float]) -> dict[str, float]:
    """Nutrition (kcal, protein, water, fiber), activity, discipline (tasks, sleep,
    review): the weighted mean of the ring's non-excluded components, 0..1."""
    out: dict[str, float] = {}
    for ring, keys in RINGS.items():
        members = [item for item in score.components if item.key in keys and not item.excluded]
        total = sum(weights.get(item.key, 0.0) for item in members)
        if total <= 0:
            total = sum(DEFAULT_SCORE_WEIGHTS[item.key] for item in members)
            share = {item.key: DEFAULT_SCORE_WEIGHTS[item.key] for item in members}
        else:
            share = {item.key: weights.get(item.key, 0.0) for item in members}
        out[ring] = round(sum(item.value * share[item.key] for item in members) / total, 4) if total > 0 else 0.0
    return out


# --------------------------------------------------------------------------- #
# Storage (daily_scores)
# --------------------------------------------------------------------------- #
def to_json(score: DayScore, *, final: bool = False, week: WeekPlan | None = None) -> dict[str, Any]:
    """The stored breakdown. ``final``: the day's last computation (after its grace day)
    — such a row is never recomputed again, whatever the timezone does later. ``week``:
    the requirement of the finished week this day closes (its last day)."""
    out: dict[str, Any] = {
        "version": SCORE_VERSION,
        "final": final,
        "provisional": score.provisional,
        "credit": score.credit,
        "items": [
            {
                "key": item.key,
                "value": round(item.value, 4),
                "weight": round(item.weight, 2),
                "excluded": item.excluded,
                "detail": item.detail,
            }
            for item in score.components
        ],
    }
    if week is not None:
        out["week"] = week_json(week)
    return out


def week_json(week: WeekPlan) -> dict[str, Any]:
    return {
        "start": week.week_start.isoformat(),
        "planned": week.planned,
        "has_plan": week.has_plan,
        "required": required_workouts(week),
    }


def finite_number(value: Any) -> float | None:
    """A finite JSON number (never a bool), else ``None``: stored JSON (a frozen day, a
    restored backup) degrades instead of raising — never a 500 from stored data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def from_row(row: DailyScore) -> DayScore:
    """A stored score (frozen days). Malformed JSON degrades — unknown items are skipped,
    non-numeric or out-of-range values count as 0 — never an exception."""
    payload = row.components if isinstance(row.components, dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    components = []
    for raw in items:
        if not isinstance(raw, dict) or raw.get("key") not in SCORE_KEYS:
            continue
        detail = raw.get("detail") if isinstance(raw.get("detail"), dict) else {}
        value = finite_number(raw.get("value"))
        weight = finite_number(raw.get("weight"))
        components.append(
            Component(
                key=str(raw["key"]),
                value=_clamp(value) if value is not None else 0.0,
                excluded=raw.get("excluded") is True,
                detail=dict(detail),
                weight=max(0.0, min(100.0, weight)) if weight is not None else 0.0,
            )
        )
    weights = row.weights if isinstance(row.weights, dict) else {}
    return DayScore(
        day=row.local_day,
        value=_clamp_score(row.score),
        provisional=payload.get("provisional") is True,
        credit=payload.get("credit") is True,
        components=components,
        weights={str(key): number for key, raw in weights.items() if (number := finite_number(raw)) is not None},
        computed_at=row.computed_at,
    )


def _clamp_score(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, number)) if math.isfinite(number) else 0.0


def counts_for_overall(row: DailyScore) -> bool:
    """Did this stored day count for the overall streak? (score ≥ 60 *and* credit:
    at least one meal logged and intake at or above the safe floor)."""
    payload = row.components if isinstance(row.components, dict) else {}
    return payload.get("credit") is True and _clamp_score(row.score) >= OVERALL_THRESHOLD


def legacy_final_at(day: date) -> datetime:
    """The earliest instant at which ``day``'s grace day is over anywhere on Earth: local
    midnight starting ``day + 2`` in UTC+14, i.e. 10:00 UTC on ``day + 1``."""
    return datetime.combine(day + timedelta(days=1), time(10, 0), tzinfo=UTC)


@dataclass(slots=True, frozen=True)
class FrozenTargets:
    """The targets and floor a stored day was computed with (``None`` = none then, or
    unreadable: that part cannot count)."""

    kcal: int | None
    protein_g: int | None
    fiber_g: int | None
    water_ml: int | None
    steps: int | None
    floor: int | None

    def rules(self, fallback_floor: int) -> DayRules:
        """The day's judge; a missing or damaged floor falls back to the current one."""
        floor = self.floor if self.floor is not None else fallback_floor
        targets = Targets(self.kcal, self.protein_g, self.fiber_g, self.water_ml, self.steps)
        return DayRules(targets, floor, kcal_zone(self.kcal, floor))


@dataclass(slots=True, frozen=True)
class ScoreFlag:
    """The light view of a stored day: enough for streaks and achievements."""

    day: date
    score: float
    credit: bool
    computed_at: datetime
    final_mark: bool | None = None  # the stored ``final`` key; None: stored before it existed
    targets: FrozenTargets | None = None  # what the day was judged with
    week: WeekPlan | None = None  # the finished week this row closes, as frozen

    @property
    def counts_for_overall(self) -> bool:
        return self.credit and self.score >= OVERALL_THRESHOLD

    def is_final(self, today: date) -> bool:
        """Is this row the day's last computation? Explicit since review 8 (``final``):
        a final row is never recomputed, whatever the timezone does later. A row stored
        before the key existed counts as final once its day is before yesterday and it was
        computed after the grace day ended *somewhere* (the old rule, in whichever
        timezone the user had then, always implies that) — so no move to another
        timezone can ever re-open it."""
        if self.final_mark is not None:
            return self.final_mark
        return self.day <= today - timedelta(days=2) and ensure_utc(self.computed_at) >= legacy_final_at(self.day)


def _item(key: str, field: str) -> Any:
    """``detail.<field>`` of the ``key`` component — a scalar JSON path, NULL when absent
    or when the stored JSON has another shape (lax mode never raises)."""
    path = f"'$.items[*] ? (@.key == \"{key}\").detail.{field}'::jsonpath"
    return func.jsonb_path_query_first(DailyScore.components, literal_column(path))


async def load_flags(db: AsyncSession, user_id, first: date, last: date) -> dict[date, ScoreFlag]:
    """Stored score, credit, finality, the targets the day was judged with and a frozen
    week requirement per day — without the breakdown (years of history stay a small
    read). Malformed JSON degrades (no credit, not final, no target)."""
    components = DailyScore.components
    rows = await db.execute(
        select(
            DailyScore.local_day,
            cast(DailyScore.score, Float),
            components["credit"].astext,
            DailyScore.computed_at,
            components["final"].astext,
            _item("kcal", "target"),
            _item("protein", "target"),
            _item("fiber", "target"),
            _item("water", "target"),
            _item("activity", "target"),
            _item("kcal", "floor"),
            components["week"],
        ).where(DailyScore.user_id == user_id, DailyScore.local_day.between(first, last))
    )
    return {
        row[0]: ScoreFlag(
            day=row[0],
            score=float(row[1] or 0),
            credit=row[2] == "true",
            computed_at=row[3],
            final_mark=_mark(row[4]),
            targets=FrozenTargets(*(_positive_int(value) for value in row[5:11])),
            week=_week(row[11], row[0]),
        )
        for row in rows
    }


def _positive_int(value: Any) -> int | None:
    number = finite_number(value)
    return int(number) if number is not None and number > 0 else None


def _week(value: Any, day: date) -> WeekPlan | None:
    """A stored week requirement, only when well-formed and closed by this very row."""
    if not isinstance(value, dict):
        return None
    try:
        start = date.fromisoformat(str(value.get("start")))
    except ValueError:
        return None
    planned, has_plan = value.get("planned"), value.get("has_plan")
    if start + timedelta(days=6) != day or not isinstance(has_plan, bool):
        return None
    if isinstance(planned, bool) or not isinstance(planned, int) or not 0 <= planned <= 7:
        return None
    return WeekPlan(start, planned, has_plan)


def _mark(value: str | None) -> bool | None:
    """The stored ``final`` key: true/false, None when absent (a row from before it
    existed); anything else (a damaged row) is "not final" — recomputed, never trusted."""
    if value is None:
        return None
    return value == "true"


def freeze(ctx: GameContext, flags: dict[date, ScoreFlag]) -> None:
    """Final days are judged against the targets they were finalised with from now on
    (``ctx.rules_for``); the open window keeps the current ones."""
    for day, flag in flags.items():
        if flag.targets is not None and flag.is_final(ctx.today):
            ctx.frozen[day] = flag.targets.rules(ctx.floor)


def frozen_weeks(ctx: GameContext, flags: dict[date, ScoreFlag]) -> dict[date, WeekPlan]:
    """Finished weeks (of the current week start) with the requirement frozen when they
    ended; the current week always follows the current plan."""
    out: dict[date, WeekPlan] = {}
    for flag in flags.values():
        week = flag.week
        if week is None or week.week_start >= ctx.week_start_day:
            continue
        if week_start_for(week.week_start, ctx.week_start) == week.week_start:  # same alignment
            out[week.week_start] = week
    return out


async def load_frozen(db: AsyncSession, ctx: GameContext, first: date, last: date) -> dict[date, WeekPlan]:
    """For readers outside the engine (quest claims, /stats): put the frozen targets of
    the final days of ``[first, last]`` into ``ctx`` and return the frozen weeks."""
    flags = await load_flags(db, ctx.user_id, first, last)
    freeze(ctx, flags)
    return frozen_weeks(ctx, flags)


async def annotate_weeks(db: AsyncSession, ctx: GameContext, weeks: dict[date, WeekPlan]) -> int:
    """Store the requirement of finished weeks whose last day's row is already final
    without one (rows from before review 8, or weeks re-bucketed by a new week start):
    the plan in force now — what they were judged with until now — from now on for
    good. Keys are the last days. One statement (executemany)."""
    if not weeks:
        return 0
    table = DailyScore.__table__
    stmt = (
        update(table)
        .where(table.c.user_id == bindparam("owner"), table.c.local_day == bindparam("last_day"))
        .values(components=table.c.components.op("||")(bindparam("patch", type_=JSONB)))
    )
    await db.execute(
        stmt,
        [
            {"owner": ctx.user_id, "last_day": day, "patch": {"week": week_json(week)}}
            for day, week in sorted(weeks.items())
        ],
    )
    return len(weeks)


async def load_rows(db: AsyncSession, user_id, first: date, last: date) -> dict[date, DailyScore]:
    rows = await db.scalars(
        select(DailyScore).where(DailyScore.user_id == user_id, DailyScore.local_day.between(first, last))
    )
    return {row.local_day: row for row in rows.all()}


async def store(
    db: AsyncSession,
    ctx: GameContext,
    scores: Iterable[DayScore],
    xp_by_day: dict[date, int],
    streak_days: dict[date, int] | None = None,
    *,
    final: Collection[date] = (),
    weeks: dict[date, WeekPlan] | None = None,
) -> int:
    """Upsert the given days (chunked statements). ``streak_days`` = the overall streak
    length reached on that day; ``final`` = the days computed for the last time;
    ``weeks`` = finished weeks' requirements keyed by their last day. A row that is
    already final is never overwritten. Returns the number of rows sent."""
    streak_days = streak_days or {}
    final = set(final)
    weeks = weeks or {}
    values = [
        {
            "id": new_id(),
            "user_id": ctx.user_id,
            "local_day": score.day,
            "score": score.value,
            "components": to_json(score, final=score.day in final, week=weeks.get(score.day)),
            "weights": score.weights,
            "xp_earned": int(xp_by_day.get(score.day, 0)),
            "streak_days": int(streak_days.get(score.day, 0)),
            "computed_at": ctx.now,
            "created_at": ctx.now,
            "updated_at": ctx.now,
        }
        for score in scores
    ]
    for start in range(0, len(values), CHUNK):
        stmt = pg_insert(DailyScore).values(values[start : start + CHUNK])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_daily_scores_user_day",
            set_={
                "score": stmt.excluded.score,
                "components": stmt.excluded.components,
                "weights": stmt.excluded.weights,
                "xp_earned": stmt.excluded.xp_earned,
                "streak_days": stmt.excluded.streak_days,
                "computed_at": stmt.excluded.computed_at,
                "updated_at": stmt.excluded.updated_at,
            },
            where=func.coalesce(DailyScore.components["final"].astext, "") != "true",
        )
        await db.execute(stmt)
    return len(values)
