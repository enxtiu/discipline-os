"""Stage 10 — the one calm insight of «Сегодня» (``GET /insights/today``).

At most one observation, from the user's own history, in their timezone:

    «Белка больше, чем в среднем за 4 недели»      (today so far)
    «Вчера шагов было больше, чем в среднем за 4 недели»

Rules (binding — the insight is a kind word, so it follows the rules of every reward):

* **Only "more" of what the app encourages**: protein, fibre, water, steps. Never
  calories, fat or carbs, and never "less than" — it never praises eating less and
  never points at a shortfall (when nothing holds, there is simply no insight).
* **Nutrition credit**: a food observation (protein, fibre, water) needs that day's
  intake at or above the safe floor (``DayRules.credit`` — the predicate of the
  protein, fibre and water XP): eating below the floor is never met with a positive
  word. Steps are not food and need no credit (as the steps XP).
* **Enough history**: the average is the mean of the 28 days before the day, over the
  days that have data (a meal / water / steps), and needs at least 7 such days.
* **A real difference**: at least 5 % above the average, and still above it once both
  are rounded to the unit shown.
* **Fresh first**: today (the day so far, the current targets and floor) before
  yesterday (finished; the open window still uses the current ones); within a day the
  first of ``METRICS`` that holds.

Three grouped range queries (``app.services.day_metrics``), whatever the history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tz import iter_days
from app.services import day_metrics
from app.services.day_metrics import DayMetrics, HealthDay, NutritionDay
from app.services.gamification.context import DayRules, GameContext

WINDOW_DAYS = 28  # «за 4 недели» — the client's sentences say so
MIN_DAYS = 7
MARGIN = 1.05
# the order observations are offered in (the first that holds wins)
METRICS: tuple[str, ...] = ("protein", "fiber", "water", "steps")
UNITS: dict[str, str] = {"protein": "g", "fiber": "g", "water": "ml", "steps": "steps"}
FOOD_METRICS = frozenset({"protein", "fiber", "water"})


@dataclass(slots=True, frozen=True)
class Insight:
    metric: str
    day: str  # "today" | "yesterday"
    date: date
    value: float
    average: float
    unit: str
    window_days: int
    days_with_data: int


@dataclass(slots=True, frozen=True)
class TodayInsight:
    date: date
    insight: Insight | None


@dataclass(slots=True, frozen=True)
class History:
    """The per-day data of the range the insight looks at; days without data are absent."""

    food: dict[date, NutritionDay]
    water: dict[date, int]
    health: dict[date, HealthDay]


def _value(history: History, metric: str, day: date) -> float | None:
    """The day's amount of ``metric``, or ``None`` when the day has no data for it
    (missing is not zero: a day without a meal says nothing about protein)."""
    if metric in ("protein", "fiber"):
        food = history.food.get(day)
        if food is None or food.meals < 1:
            return None
        return food.protein_g if metric == "protein" else food.fiber_g
    if metric == "water":
        ml = history.water.get(day)
        return float(ml) if ml else None
    health = history.health.get(day)
    return float(health.steps) if health is not None and health.steps > 0 else None


def _credit(history: History, rules: DayRules, day: date) -> bool:
    """Nutrition credit of the day: a meal logged and intake at or above the safe floor."""
    food = history.food.get(day)
    metrics = DayMetrics(
        day=day,
        kcal=food.kcal if food is not None else 0.0,
        protein_g=food.protein_g if food is not None else 0.0,
        fiber_g=food.fiber_g if food is not None else 0.0,
        meals=food.meals if food is not None else 0,
        water_ml=history.water.get(day, 0),
    )
    return rules.credit(metrics)


def observe(history: History, rules: DayRules, metric: str, day: date, label: str) -> Insight | None:
    """``metric`` on ``day`` against the 28 days before it — or ``None`` when it is not
    clearly above that average (or must not be said, see the module rules)."""
    value = _value(history, metric, day)
    if value is None:
        return None
    if metric in FOOD_METRICS and not _credit(history, rules, day):
        return None
    first, last = day - timedelta(days=WINDOW_DAYS), day - timedelta(days=1)
    values = [item for item in (_value(history, metric, past) for past in iter_days(first, last)) if item is not None]
    if len(values) < MIN_DAYS:
        return None
    average = sum(values) / len(values)
    if average <= 0 or value < average * MARGIN or round(value) <= round(average):
        return None
    digits = 1 if UNITS[metric] == "g" else 0
    return Insight(
        metric=metric,
        day=label,
        date=day,
        value=round(value, digits),
        average=round(average, digits),
        unit=UNITS[metric],
        window_days=WINDOW_DAYS,
        days_with_data=len(values),
    )


def pick(history: History, rules: DayRules, today: date) -> Insight | None:
    """At most one insight: today first, then yesterday; within a day, ``METRICS`` in order."""
    for label, day in (("today", today), ("yesterday", today - timedelta(days=1))):
        for metric in METRICS:
            found = observe(history, rules, metric, day, label)
            if found is not None:
                return found
    return None


async def load_history(db: AsyncSession, ctx: GameContext, first: date, last: date) -> History:
    return History(
        food=await day_metrics.nutrition_by_day(db, ctx.user_id, first, last),
        water=await day_metrics.water_by_day(db, ctx.user_id, first, last),
        health=await day_metrics.health_by_day(db, ctx.user_id, first, last),
    )


async def today_insight(db: AsyncSession, ctx: GameContext) -> TodayInsight:
    """The insight for the user's today (``ctx.today``); yesterday's window reaches 29 days back."""
    today = ctx.today
    history = await load_history(db, ctx, today - timedelta(days=WINDOW_DAYS + 1), today)
    return TodayInsight(date=today, insight=pick(history, ctx.rules, today))
