"""Stage 8 — gamification: daily score, rings, XP, levels, streaks, quests, achievements.

Every number here is computed by the server in the user's timezone and returned
ready to display; the client never computes a score, a target or a day boundary.
Bilingual reference data (level titles, quest and achievement texts) is returned
as ``*_ru`` / ``*_en`` pairs like the exercise library — the client picks one.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import ApiModel

# --------------------------------------------------------------------------- #
# Score weights and XP multipliers (settings.xp_weights) — shared with settings
# --------------------------------------------------------------------------- #
SCORE_KEYS: tuple[str, ...] = ("kcal", "protein", "water", "fiber", "activity", "tasks", "sleep", "review")
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "kcal": 25.0,
    "protein": 15.0,
    "water": 10.0,
    "fiber": 5.0,
    "activity": 15.0,
    "tasks": 15.0,
    "sleep": 10.0,
    "review": 5.0,
}
XP_CATEGORIES: tuple[str, ...] = ("nutrition", "training", "activity", "planner", "review", "bonus")
MAX_SCORE_WEIGHT = 100.0
MAX_XP_MULTIPLIER = 2.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_xp_weights(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """``settings.xp_weights`` on the way in: ``score_weights`` maps score components
    to 0..100 (at least one above 0), ``xp_multipliers`` maps XP categories to 0..2.
    Other (legacy) keys are kept untouched. Raises ``ValueError`` → 422."""
    if value is None:
        return None
    weights = value.get("score_weights")
    if weights is not None:
        if not isinstance(weights, dict):
            raise ValueError("score_weights must be an object")
        for key, raw in weights.items():
            if key not in DEFAULT_SCORE_WEIGHTS:
                raise ValueError(f"unknown score component: {key}")
            number = _number(raw)
            if number is None or not 0 <= number <= MAX_SCORE_WEIGHT:
                raise ValueError(f"score weight {key} must be a number between 0 and {MAX_SCORE_WEIGHT:g}")
        merged = {**DEFAULT_SCORE_WEIGHTS, **{key: float(raw) for key, raw in weights.items()}}
        if sum(merged.values()) <= 0:
            raise ValueError("at least one score weight must be above 0")
    multipliers = value.get("xp_multipliers")
    if multipliers is not None:
        if not isinstance(multipliers, dict):
            raise ValueError("xp_multipliers must be an object")
        for key, raw in multipliers.items():
            if key not in XP_CATEGORIES:
                raise ValueError(f"unknown XP category: {key}")
            number = _number(raw)
            if number is None or not 0 <= number <= MAX_XP_MULTIPLIER:
                raise ValueError(f"XP multiplier {key} must be a number between 0 and {MAX_XP_MULTIPLIER:g}")
    return value


def read_score_weights(xp_weights: Any) -> dict[str, float]:
    """Stored weights → the effective (validated) set; anything malformed falls back
    to the defaults key by key (never a 500)."""
    raw = xp_weights.get("score_weights") if isinstance(xp_weights, dict) else None
    merged = dict(DEFAULT_SCORE_WEIGHTS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            number = _number(value)
            if key in merged and number is not None and 0 <= number <= MAX_SCORE_WEIGHT:
                merged[key] = number
    return merged if sum(merged.values()) > 0 else dict(DEFAULT_SCORE_WEIGHTS)


def read_xp_multipliers(xp_weights: Any) -> dict[str, float]:
    raw = xp_weights.get("xp_multipliers") if isinstance(xp_weights, dict) else None
    out = dict.fromkeys(XP_CATEGORIES, 1.0)
    if isinstance(raw, dict):
        for key, value in raw.items():
            number = _number(value)
            if key in out and number is not None and 0 <= number <= MAX_XP_MULTIPLIER:
                out[key] = number
    return out


# --------------------------------------------------------------------------- #
# Literals
# --------------------------------------------------------------------------- #
ScoreKeyLiteral = Literal["kcal", "protein", "water", "fiber", "activity", "tasks", "sleep", "review"]
RingKeyLiteral = Literal["nutrition", "activity", "discipline"]
StreakKindLiteral = Literal["overall", "meal_logging", "water", "steps", "tasks", "review", "workout"]
StreakUnitLiteral = Literal["day", "week"]
QuestKindLiteral = Literal["daily", "weekly"]
QuestStatusLiteral = Literal["active", "completed", "claimed", "expired"]
RarityLiteral = Literal["common", "rare", "epic", "legendary"]
AchievementCategoryLiteral = Literal[
    "nutrition", "training", "activity", "planner", "review", "streaks", "levels", "special"
]
XpKindLiteral = Literal[
    "meal_logged",
    "protein_target",
    "fiber_target",
    "water_target",
    "kcal_in_zone",
    "steps_target",
    "workout_completed",
    "workout_volume",
    "task_completed",
    "project_completed",
    "timer_session",
    "daily_review",
    "sleep_logged",
    "weight_logged",
    "streak_bonus",
    "quest_completed",
    "achievement_unlocked",
    "manual",
]
ScoreStateLiteral = Literal[
    "no_target",
    "no_meals",
    "below_floor",
    "below_zone",
    "progress",
    "in_zone",
    "above_zone",
    "no_data",
    "no_tasks",
    "workout",
    "ok",
]


# --------------------------------------------------------------------------- #
# Score
# --------------------------------------------------------------------------- #
class ScoreDetailOut(ApiModel):
    """What a component was computed from (only the fields that apply are set)."""

    state: ScoreStateLiteral
    amount: float | None = Field(default=None, description="kcal, g, ml, steps or minutes — see unit")
    target: float | None = None
    unit: Literal["kcal", "g", "ml", "steps", "min"] | None = None
    zone_low: int | None = Field(default=None, description="kcal: lower edge of the target zone (≥ the floor)")
    zone_high: int | None = None
    floor: int | None = Field(default=None, description="kcal: the safe floor")
    meals: int | None = None
    workouts: int | None = None
    done: int | None = None
    open_due: int | None = None
    has_entry: bool | None = None


class ScoreComponentOut(ApiModel):
    key: ScoreKeyLiteral
    value: float = Field(description="0..1")
    weight: float = Field(description="effective weight in points (non-excluded weights sum to 100); 0 if excluded")
    excluded: bool
    detail: ScoreDetailOut


class DayScoreOut(ApiModel):
    value: float = Field(description="0..100")
    provisional: bool = Field(description="today: the day is in progress (the kcal part is progress, not a verdict)")
    components: list[ScoreComponentOut]


class RingOut(ApiModel):
    key: RingKeyLiteral
    value: float = Field(description="0..1")


# --------------------------------------------------------------------------- #
# XP and levels
# --------------------------------------------------------------------------- #
class LevelOut(ApiModel):
    level: int
    title_ru: str
    title_en: str
    min_xp: int


class LevelProgressOut(LevelOut):
    next_min_xp: int | None = Field(description="null at the top level")
    progress: float = Field(description="0..1 within the current level (1 at the top level)")


class XpTodayOut(ApiModel):
    today: int
    total: int
    level: LevelProgressOut


class XpDayOut(ApiModel):
    date: date
    xp: int


class ProfileXpOut(ApiModel):
    total: int
    today: int
    level: LevelProgressOut
    last_30_days: list[XpDayOut]


class XpEventOut(ApiModel):
    kind: XpKindLiteral
    points: int
    reason: str | None = None


class XpHistoryDayOut(ApiModel):
    date: date
    xp: int
    events: list[XpEventOut]


class XpHistoryOut(ApiModel):
    items: list[XpHistoryDayOut]


class ScoreHistoryDayOut(ApiModel):
    date: date
    score: float | None = Field(description="null when the day has no score (before your first data, or the future)")
    provisional: bool
    components: list[ScoreComponentOut]


class ScoreHistoryOut(ApiModel):
    items: list[ScoreHistoryDayOut]


# --------------------------------------------------------------------------- #
# Streaks
# --------------------------------------------------------------------------- #
class StreakPeriodOut(ApiModel):
    """Weekly streak: completed workouts this week vs the number that makes it count."""

    done: int
    required: int


class StreakOut(ApiModel):
    kind: StreakKindLiteral
    unit: StreakUnitLiteral
    current: int = Field(description="days (or weeks) in a row, today/this week included once met")
    longest: int
    today_met: bool = Field(description="today's (this week's) condition is met")
    at_risk: bool = Field(description="not met yet, a streak to lose, and it is after 18:00 local (weekly: last day)")
    freeze_tokens: int = Field(description="0..2; +1 every week, one is used automatically for a missed day/week")
    freeze_used_dates: list[date] = Field(description="days (weekly: week starts) saved by a freeze, newest last")
    last_met_date: date | None = None
    broken_on: date | None = None
    period: StreakPeriodOut | None = None
    next_milestone: int | None = Field(default=None, description="overall streak: the next XP milestone")


# --------------------------------------------------------------------------- #
# Quests
# --------------------------------------------------------------------------- #
class UserQuestOut(ApiModel):
    id: uuid.UUID
    quest_id: uuid.UUID
    code: str
    kind: QuestKindLiteral
    title_ru: str
    title_en: str
    description_ru: str | None = None
    description_en: str | None = None
    icon: str | None = None
    period_start: date
    period_end: date
    progress: float
    target: float
    unit: Literal["count", "ml", "steps", "min", "days"] = "count"
    status: QuestStatusLiteral
    claimable: bool
    xp_reward: int
    completed_at: datetime | None = None
    claimed_at: datetime | None = None


class QuestClaimOut(ApiModel):
    quest: UserQuestOut
    xp_awarded: int


# --------------------------------------------------------------------------- #
# Achievements
# --------------------------------------------------------------------------- #
class AchievementProgressOut(ApiModel):
    value: float
    target: float


class AchievementOut(ApiModel):
    id: uuid.UUID
    code: str = Field(description="'secret' while a secret achievement is locked")
    name_ru: str
    name_en: str
    description_ru: str | None = None
    description_en: str | None = None
    icon: str | None = None
    category: AchievementCategoryLiteral
    rarity: RarityLiteral
    xp_reward: int
    is_secret: bool
    masked: bool = Field(description="a locked secret achievement: name, description and progress are hidden")
    unlocked: bool
    unlocked_at: datetime | None = None
    seen: bool
    progress: AchievementProgressOut | None = None


class AchievementsSeenIn(ApiModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100, description="achievement ids from `celebrate`")


# --------------------------------------------------------------------------- #
# Screens
# --------------------------------------------------------------------------- #
class GamificationTodayOut(ApiModel):
    date: date
    score: DayScoreOut
    rings: list[RingOut]
    xp: XpTodayOut
    streaks: list[StreakOut]
    quests: list[UserQuestOut]
    celebrate: list[AchievementOut] = Field(description="unlocked and not yet seen (POST /achievements/seen)")


class ProfileStatsOut(ApiModel):
    meals_logged_total: int
    workouts_total: int
    tasks_completed_total: int
    tracked_minutes_total: int
    journal_entries_total: int


class GamificationProfileOut(ApiModel):
    xp: ProfileXpOut
    levels: list[LevelOut]
    streaks: list[StreakOut]
    achievements: list[AchievementOut] = Field(
        description="unlocked first (newest first), then locked with progress; locked secrets are masked"
    )
    stats: ProfileStatsOut
