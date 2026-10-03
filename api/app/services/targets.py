"""Energy & macro target math (pure functions — fully unit tested).

Order of operations
-------------------
1. ``BMR`` — Mifflin-St Jeor, or Katch-McArdle when body-fat % is known
   (``BmrFormula.AUTO`` picks Katch, because it is more accurate for lean and
   for higher-fat bodies alike as long as the estimate exists).
2. ``TDEE`` — ``BMR x activity factor``.
3. ``target kcal`` — TDEE - deficit (lose) / + surplus (gain), preset 10/15/20 %.
4. ``protein`` — g/kg bodyweight, ``fat`` — minimum g/kg, ``carbs`` — remainder.
5. ``fiber`` — 14 g per 1000 kcal; ``water`` — 30-35 ml/kg.

Safety rules that the rest of the app must respect:

* the computed intake is never pushed below the safe floor
  (1500 kcal male / 1200 kcal female by default, editable behind a warning);
* ``warnings`` carries machine-readable codes so the UI can nag in the user's
  language ("aggressive_deficit", "below_safe_floor", "missing_body_params");
* gamification never rewards an intake below the floor — the daily score peaks
  *inside* the target zone (see ``score_kcal``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from app.models.user import ACTIVITY_FACTORS, ActivityLevel, BmrFormula, Sex

__all__ = [
    "ABSOLUTE_FLOOR_KCAL",
    "FIBER_PER_1000_KCAL",
    "SAFE_FLOOR_FEMALE",
    "SAFE_FLOOR_MALE",
    "BodyParams",
    "TargetResult",
    "activity_factor",
    "age_from_birth_date",
    "compute_bmr",
    "compute_targets",
    "compute_tdee",
    "estimate_1rm_epley",
    "katch_mcardle",
    "kcal_from_met",
    "kcal_from_steps",
    "mifflin_st_jeor",
    "rdee_from_steps",
    "score_kcal",
]

SAFE_FLOOR_MALE = 1500
SAFE_FLOOR_FEMALE = 1200
SAFE_FLOOR_UNKNOWN = 1200
# The DB check constraint refuses anything below this; the API refuses below
# the user's floor unless it was explicitly confirmed.
ABSOLUTE_FLOOR_KCAL = 1000
FIBER_PER_1000_KCAL = 14.0
WATER_ML_PER_KG_MIN = 30.0
WATER_ML_PER_KG_MAX = 35.0
AGGRESSIVE_DEFICIT_PCT = 25
AGGRESSIVE_SURPLUS_PCT = 20

MIN_PROTEIN_G_PER_KG = 1.2
MAX_PROTEIN_G_PER_KG = 3.0
MIN_FAT_G_PER_KG = 0.5


def _round(value: float, step: int) -> int:
    return int(round(value / step) * step)


def age_from_birth_date(birth_date: date | None, as_of: date) -> int | None:
    if birth_date is None:
        return None
    years = as_of.year - birth_date.year - ((as_of.month, as_of.day) < (birth_date.month, birth_date.day))
    return years if years >= 0 else None


@dataclass(frozen=True)
class BodyParams:
    """Everything the formulas need; all fields optional so the UI can show a
    partial preview while the user is still filling in onboarding."""

    sex: Sex | None = None
    age_years: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    activity_level: ActivityLevel = ActivityLevel.LIGHT
    formula: BmrFormula = BmrFormula.AUTO

    # user overrides
    goal_mode: str = "maintain"  # lose | maintain | gain
    goal_rate_pct: int = 0  # deficit/surplus preset
    protein_g_per_kg: float = 2.0
    fat_min_g_per_kg: float = 0.8
    water_ml_per_kg: float = 33.0
    safe_floor_kcal: int | None = None  # None -> default by sex
    target_steps: int = 8000

    def with_updates(self, **changes) -> BodyParams:
        from dataclasses import replace

        return replace(self, **changes)


@dataclass(frozen=True)
class TargetResult:
    ok: bool
    formula_used: str
    bmr_kcal: int | None = None
    tdee_kcal: int | None = None
    target_kcal: int | None = None
    target_protein_g: int | None = None
    target_fat_g: int | None = None
    target_carbs_g: int | None = None
    target_fiber_g: int | None = None
    target_water_ml: int | None = None
    target_steps: int | None = None
    safe_floor_kcal: int = SAFE_FLOOR_UNKNOWN
    deficit_pct: float | None = None
    warnings: list[str] = field(default_factory=list)
    macros_kcal: dict[str, int] = field(default_factory=dict)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


# --------------------------------------------------------------------------- #
# BMR
# --------------------------------------------------------------------------- #
def mifflin_st_jeor(weight_kg: float, height_cm: float, age_years: int, sex: Sex | None) -> float:
    """Mifflin-St Jeor. ``Sex.OTHER``/unknown uses the midpoint of the two
    constants (+5 and -161 -> -78), which is the documented convention."""
    if weight_kg <= 0 or height_cm <= 0 or age_years < 0:
        raise ValueError("weight_kg, height_cm and age_years must be positive")
    if sex == Sex.MALE:
        offset = 5.0
    elif sex == Sex.FEMALE:
        offset = -161.0
    else:
        offset = -78.0
    return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years + offset


def katch_mcardle(weight_kg: float, body_fat_pct: float) -> float:
    """Katch-McArdle: 370 + 21.6 x lean body mass."""
    if not 0 < body_fat_pct < 75:
        raise ValueError("body_fat_pct must be between 0 and 75")
    lean_mass = weight_kg * (1.0 - body_fat_pct / 100.0)
    return 370.0 + 21.6 * lean_mass


def activity_factor(level: ActivityLevel | str) -> float:
    if isinstance(level, str):
        try:
            level = ActivityLevel(level)
        except ValueError:
            return ACTIVITY_FACTORS[ActivityLevel.LIGHT]
    return ACTIVITY_FACTORS.get(level, 1.375)


def compute_bmr(params: BodyParams) -> tuple[float | None, str, list[str]]:
    """Returns ``(bmr, formula_used, warnings)``."""
    warnings: list[str] = []
    if params.weight_kg is None or params.weight_kg <= 0:
        return None, "none", ["missing_body_params"]

    want_katch = params.formula in (BmrFormula.KATCH,) or (
        params.formula == BmrFormula.AUTO and params.body_fat_pct is not None
    )
    if want_katch:
        if params.body_fat_pct is None:
            warnings.append("katch_requires_body_fat")
        else:
            return katch_mcardle(params.weight_kg, params.body_fat_pct), "katch_mcardle", warnings

    if params.formula == BmrFormula.KATCH:
        warnings.append("katch_requires_body_fat")

    if params.height_cm is None or params.age_years is None:
        return None, "none", [*warnings, "missing_body_params"]
    return (
        mifflin_st_jeor(params.weight_kg, params.height_cm, params.age_years, params.sex),
        "mifflin_st_jeor",
        warnings,
    )


def compute_tdee(bmr: float, level: ActivityLevel | str) -> float:
    return bmr * activity_factor(level)


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
def _default_floor(sex: Sex | None) -> int:
    if sex == Sex.MALE:
        return SAFE_FLOOR_MALE
    if sex == Sex.FEMALE:
        return SAFE_FLOOR_FEMALE
    return SAFE_FLOOR_UNKNOWN


def compute_targets(params: BodyParams, *, as_of: date | None = None) -> TargetResult:
    """Full target computation. Returns ``ok=False`` (with warnings) when the
    body parameters are incomplete — the caller then keeps previous targets."""
    warnings: list[str] = []
    floor = params.safe_floor_kcal or _default_floor(params.sex)

    bmr, formula_used, bmr_warnings = compute_bmr(params)
    warnings.extend(bmr_warnings)
    if bmr is None or formula_used == "none":
        return TargetResult(ok=False, formula_used=formula_used, warnings=warnings, safe_floor_kcal=floor)

    tdee = compute_tdee(bmr, params.activity_level)

    mode = params.goal_mode if params.goal_mode in ("lose", "maintain", "gain") else "maintain"
    rate = max(0, int(params.goal_rate_pct or 0))
    if mode == "lose":
        raw_target = tdee * (1.0 - rate / 100.0)
    elif mode == "gain":
        raw_target = tdee * (1.0 + rate / 100.0)
    else:
        raw_target = tdee

    # 1) never go below the safe floor
    if raw_target < floor:
        warnings.append("below_safe_floor")
        target_kcal = float(floor)
    else:
        target_kcal = raw_target

    # 2) express the *effective* deficit after clamping (it shrinks when the
    #    floor bites — important for honest UI copy)
    effective_deficit_pct = round((1.0 - target_kcal / tdee) * 100.0, 1) if tdee > 0 else 0.0

    if mode == "lose" and rate > AGGRESSIVE_DEFICIT_PCT:
        warnings.append("aggressive_deficit")
    if mode == "lose" and rate >= 20:
        warnings.append("high_deficit")
    if mode == "gain" and rate > AGGRESSIVE_SURPLUS_PCT:
        warnings.append("aggressive_surplus")
    if mode == "lose" and params.body_fat_pct is not None:
        # very low body fat + deficit is a red flag
        risky_below = 8.0 if params.sex == Sex.MALE else 16.0
        if params.body_fat_pct < risky_below:
            warnings.append("low_body_fat_deficit")

    target_kcal_i = _round(target_kcal, 10)

    weight = float(params.weight_kg or 0.0)
    protein_per_kg = min(max(float(params.protein_g_per_kg or 2.0), MIN_PROTEIN_G_PER_KG), MAX_PROTEIN_G_PER_KG)
    protein_g = _round(protein_per_kg * weight, 1)

    fat_per_kg = max(float(params.fat_min_g_per_kg or 0.8), MIN_FAT_G_PER_KG)
    fat_g = _round(fat_per_kg * weight, 1)

    remaining = target_kcal_i - protein_g * 4 - fat_g * 9
    if remaining < 0:
        warnings.append("macros_exceed_kcal")
    carbs_g = max(0, _round(remaining / 4.0, 1))

    fiber_g = _round(FIBER_PER_1000_KCAL * target_kcal_i / 1000.0, 1)
    water_per_kg = float(params.water_ml_per_kg or WATER_ML_PER_KG_MIN)
    water_per_kg = min(max(water_per_kg, WATER_ML_PER_KG_MIN), WATER_ML_PER_KG_MAX)
    water_ml = _round(water_per_kg * weight, 10) if weight else 2000

    return TargetResult(
        ok=True,
        formula_used=formula_used,
        bmr_kcal=_round(bmr, 1),
        tdee_kcal=_round(tdee, 1),
        target_kcal=target_kcal_i,
        target_protein_g=protein_g,
        target_fat_g=fat_g,
        target_carbs_g=carbs_g,
        target_fiber_g=fiber_g,
        target_water_ml=water_ml,
        target_steps=params.target_steps,
        safe_floor_kcal=floor,
        deficit_pct=effective_deficit_pct,
        warnings=sorted(set(warnings)),
        macros_kcal={
            "protein": protein_g * 4,
            "fat": fat_g * 9,
            "carbs": carbs_g * 4,
        },
    )


# --------------------------------------------------------------------------- #
# Expenditure estimates (stages 4 & 5)
# --------------------------------------------------------------------------- #
def rdee_from_steps(steps: int, weight_kg: float, bmr_kcal: float | None = None) -> float:
    """Rough daily expenditure from step count.

    Uses the common walking approximation ``~0.0005 kcal per step per kg``,
    which reproduces the familiar "≈40 kcal / 1000 steps at 80 kg" figure.
    """
    return max(0.0, float(steps)) * 0.0005 * float(weight_kg)


def kcal_from_met(met: float, weight_kg: float, minutes: float) -> float:
    """MET x weight(kg) x hours (the standard ACSM formula)."""
    return max(0.0, float(met)) * float(weight_kg) * (float(minutes) / 60.0)


def kcal_from_steps(steps: int, weight_kg: float) -> float:
    """Step-only burn estimate, used to avoid double counting with workouts."""
    return rdee_from_steps(steps, weight_kg)


def estimate_1rm_epley(weight_kg: float, reps: int) -> float:
    """Epley: w x (1 + reps/30).  1 rep returns the weight itself."""
    if weight_kg <= 0 or reps <= 0:
        return 0.0
    if reps == 1:
        return float(weight_kg)
    return float(weight_kg) * (1.0 + reps / 30.0)


# --------------------------------------------------------------------------- #
# Scoring helpers used by gamification (stage 8) — kept here with the math
# --------------------------------------------------------------------------- #
def score_kcal(intake: int, target: int, floor: int, tolerance_pct: float = 10.0) -> float:
    """0..1 score for calorie intake that **peaks inside the target zone**.

    * inside target +/- tolerance  -> 1.0
    * below the safe floor         -> 0.0 (never rewarded)
    * outside the zone             -> linear falloff, reaching 0 at 2x the
      tolerance window or at the floor, whichever comes first.

    This makes "eating as little as possible" strictly worse than hitting the
    target, which is the whole point of the safety rule.
    """
    if target <= 0:
        return 0.0
    if intake <= 0:
        return 0.0
    if intake < floor:
        return 0.0
    window = max(1.0, target * tolerance_pct / 100.0)
    delta = abs(intake - target)
    if delta <= window:
        return 1.0
    if intake < target:
        # linear from the bottom edge of the zone down to the safe floor
        span = max(1.0, target - window - floor)
        return max(0.0, 1.0 - (target - window - intake) / span)
    # linear from the top edge of the zone down to 0 at target + 30 %
    # (an occasional big day is forgivable; a permanent +50 % is not)
    span = max(window, target * 0.30)
    return max(0.0, 1.0 - (delta - window) / span)


def clamp_score(value: float) -> float:
    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, value))
