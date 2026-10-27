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
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Float, cast, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models import DailyScore
from app.schemas.gamification import DEFAULT_SCORE_WEIGHTS, SCORE_KEYS
from app.services.day_metrics import DayMetrics, KcalZone
from app.services.gamification.context import GameContext

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
    never intake below the floor)."""
    return metrics.meals >= 1 and metrics.kcal >= floor


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
def to_json(score: DayScore) -> dict[str, Any]:
    return {
        "version": SCORE_VERSION,
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


@dataclass(slots=True, frozen=True)
class ScoreFlag:
    """The light view of a stored day: enough for streaks and achievements."""

    day: date
    score: float
    credit: bool
    computed_at: datetime

    @property
    def counts_for_overall(self) -> bool:
        return self.credit and self.score >= OVERALL_THRESHOLD


async def load_flags(db: AsyncSession, user_id, first: date, last: date) -> dict[date, ScoreFlag]:
    """Stored score, credit flag and ``computed_at`` per day — without the breakdown
    (years of history stay a small read)."""
    credit = DailyScore.components["credit"].astext
    rows = await db.execute(
        select(DailyScore.local_day, cast(DailyScore.score, Float), credit, DailyScore.computed_at).where(
            DailyScore.user_id == user_id, DailyScore.local_day.between(first, last)
        )
    )
    return {row[0]: ScoreFlag(row[0], float(row[1] or 0), row[2] == "true", row[3]) for row in rows}


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
) -> int:
    """Upsert the given days (chunked statements). ``streak_days`` = the overall streak
    length reached on that day. Returns the number of rows written."""
    streak_days = streak_days or {}
    values = [
        {
            "id": new_id(),
            "user_id": ctx.user_id,
            "local_day": score.day,
            "score": score.value,
            "components": to_json(score),
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
        )
        await db.execute(stmt)
    return len(values)
