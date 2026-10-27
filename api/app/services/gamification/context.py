"""Per-user context of one gamification run: the clock, the day, targets, the floor,
the calorie zone, score weights and XP multipliers — captured once, so a run never
mixes two "nows", two days or two sets of targets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tz import ensure_utc, get_zone, utcnow
from app.models import User, UserSettings, UserStatus
from app.schemas.gamification import read_score_weights, read_xp_multipliers
from app.services.day_metrics import (
    DayMetrics,
    KcalZone,
    kcal_zone,
    nutrition_credit,
    safe_floor_kcal,
    sql_timezone,
    week_start_for,
)
from app.services.settings_service import current_targets


@dataclass(slots=True, frozen=True)
class Targets:
    """Daily targets; ``None`` = not set yet (the matching score part is excluded)."""

    kcal: int | None
    protein_g: int | None
    fiber_g: int | None
    water_ml: int | None
    steps: int | None


def _positive(value: int | float | None) -> int | None:
    return int(value) if value is not None and value > 0 else None


@dataclass(slots=True, frozen=True)
class DayRules:
    """What one day is judged against: its targets, the safe floor and the calorie zone.
    The open window (today, yesterday) uses the current ones; a finished day the ones it
    was finalised with (``GameContext.rules_for``). Every day-level verdict of stage 8 —
    XP, streaks, achievement day counts, quests, the /stats adherence — goes through these
    predicates, so they can never disagree."""

    targets: Targets
    floor: int
    zone: KcalZone

    def credit(self, m: DayMetrics) -> bool:
        """Nutrition credit: a meal logged and intake at or above the safe floor."""
        return nutrition_credit(m, self.floor)

    def protein_met(self, m: DayMetrics) -> bool:
        target = self.targets.protein_g
        return bool(target) and self.credit(m) and m.protein_g >= (target or 0)

    def fiber_met(self, m: DayMetrics) -> bool:
        target = self.targets.fiber_g
        return bool(target) and self.credit(m) and m.fiber_g >= (target or 0)

    def water_met(self, m: DayMetrics) -> bool:
        """Water at the target counts only with nutrition credit: a day of water and no
        food (or food below the floor) earns no water XP, streak, quest or badge."""
        target = self.targets.water_ml
        return bool(target) and self.credit(m) and m.water_ml >= (target or 0)

    def steps_met(self, m: DayMetrics) -> bool:
        target = self.targets.steps
        return bool(target) and m.steps is not None and m.steps >= (target or 0)

    def in_zone(self, m: DayMetrics) -> bool:
        """A logged day inside the calorie zone (the zone never starts below the floor)."""
        return m.meals >= 1 and self.zone.contains(m.kcal)


@dataclass(slots=True)
class GameContext:
    user_id: uuid.UUID
    settings: UserSettings | None
    tz: str
    now: datetime  # aware UTC
    week_start: int
    targets: Targets
    floor: int
    zone: KcalZone
    score_weights: dict[str, float]
    multipliers: dict[str, float]
    # finished days → the targets they were finalised with (loaded by the engine and the
    # readers that judge finished days); every other day uses the current targets
    frozen: dict[date, DayRules] = field(default_factory=dict)

    @property
    def rules(self) -> DayRules:
        """The current targets, floor and zone (today, yesterday, the current week)."""
        return DayRules(self.targets, self.floor, self.zone)

    def rules_for(self, day: date) -> DayRules:
        """What ``day`` is judged against: its frozen targets once it is final, else the
        current ones."""
        frozen = self.frozen.get(day)
        return frozen if frozen is not None else self.rules

    @property
    def local_now(self) -> datetime:
        return self.now.astimezone(get_zone(self.tz))

    @property
    def today(self) -> date:
        return self.local_now.date()

    @property
    def yesterday(self) -> date:
        return self.today - timedelta(days=1)

    @property
    def week_start_day(self) -> date:
        return week_start_for(self.today, self.week_start)

    def local_start(self, day: date) -> datetime:
        """UTC instant of local midnight starting ``day`` (DST-safe)."""
        return datetime.combine(day, time.min).replace(tzinfo=get_zone(self.tz)).astimezone(UTC)


def context_for(settings: UserSettings | None, user_id: uuid.UUID, now: datetime | None = None) -> GameContext:
    now = ensure_utc(now) if now is not None else utcnow()
    tz = sql_timezone((settings.timezone if settings else None) or get_settings().default_timezone)
    today = now.astimezone(get_zone(tz)).date()
    if settings is not None:
        result = current_targets(settings, as_of=today)
        targets = Targets(
            kcal=_positive(result.target_kcal),
            protein_g=_positive(result.target_protein_g),
            fiber_g=_positive(result.target_fiber_g),
            water_ml=_positive(result.target_water_ml),
            steps=_positive(settings.target_steps),
        )
    else:
        targets = Targets(None, None, None, None, None)
    floor = safe_floor_kcal(settings)
    return GameContext(
        user_id=user_id,
        settings=settings,
        tz=tz,
        now=now,
        week_start=int(settings.week_start) if settings is not None and settings.week_start is not None else 1,
        targets=targets,
        floor=floor,
        zone=kcal_zone(targets.kcal, floor),
        score_weights=read_score_weights(settings.xp_weights if settings else None),
        multipliers=read_xp_multipliers(settings.xp_weights if settings else None),
    )


async def build_context(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> GameContext | None:
    """The context of an active user, or ``None`` (missing or disabled account)."""
    row = (
        await db.execute(
            select(User.status, UserSettings)
            .outerjoin(UserSettings, UserSettings.user_id == User.id)
            .where(User.id == user_id)
        )
    ).first()
    if row is None or row[0] != UserStatus.ACTIVE:
        return None
    return context_for(row[1], user_id, now)
