"""Calorie & macro math — the rules the whole product depends on."""

from __future__ import annotations

from datetime import date

import pytest

from app.models.user import ActivityLevel, BmrFormula, Sex
from app.services.targets import (
    ABSOLUTE_FLOOR_KCAL,
    SAFE_FLOOR_FEMALE,
    SAFE_FLOOR_MALE,
    BodyParams,
    activity_factor,
    age_from_birth_date,
    compute_bmr,
    compute_targets,
    estimate_1rm_epley,
    katch_mcardle,
    kcal_from_met,
    kcal_from_steps,
    mifflin_st_jeor,
    score_kcal,
)


# --------------------------------------------------------------------------- #
# BMR
# --------------------------------------------------------------------------- #
def test_mifflin_male_matches_published_formula() -> None:
    # 10*80 + 6.25*180 - 5*30 + 5 = 800 + 1125 - 150 + 5 = 1780
    assert mifflin_st_jeor(80, 180, 30, Sex.MALE) == pytest.approx(1780.0)


def test_mifflin_female_matches_published_formula() -> None:
    # 10*60 + 6.25*165 - 5*28 - 161 = 600 + 1031.25 - 140 - 161 = 1330.25
    assert mifflin_st_jeor(60, 165, 28, Sex.FEMALE) == pytest.approx(1330.25)


def test_mifflin_unknown_sex_uses_midpoint() -> None:
    male = mifflin_st_jeor(70, 175, 35, Sex.MALE)
    female = mifflin_st_jeor(70, 175, 35, Sex.FEMALE)
    unknown = mifflin_st_jeor(70, 175, 35, None)
    assert unknown == pytest.approx((male + female) / 2)


def test_katch_mcardle_uses_lean_mass() -> None:
    # 370 + 21.6 * (80 * 0.85) = 370 + 21.6*68 = 1838.8
    assert katch_mcardle(80, 15) == pytest.approx(370 + 21.6 * 68)


def test_katch_rejects_impossible_body_fat() -> None:
    with pytest.raises(ValueError):
        katch_mcardle(80, 90)


def test_auto_formula_prefers_katch_when_body_fat_known() -> None:
    params = BodyParams(
        sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, body_fat_pct=15, formula=BmrFormula.AUTO
    )
    bmr, formula, warnings = compute_bmr(params)
    assert formula == "katch_mcardle"
    assert bmr == pytest.approx(1838.8)
    assert warnings == []


def test_auto_formula_falls_back_to_mifflin_without_body_fat() -> None:
    params = BodyParams(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, formula=BmrFormula.AUTO)
    _, formula, _ = compute_bmr(params)
    assert formula == "mifflin_st_jeor"


def test_katch_without_body_fat_warns_and_falls_back() -> None:
    params = BodyParams(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, formula=BmrFormula.KATCH)
    _, formula, warnings = compute_bmr(params)
    assert formula == "mifflin_st_jeor"
    assert "katch_requires_body_fat" in warnings


def test_missing_body_params_produce_no_bmr() -> None:
    bmr, formula, warnings = compute_bmr(BodyParams(weight_kg=80))
    assert bmr is None and formula == "none"
    assert "missing_body_params" in warnings


def test_active_levels_are_ordered() -> None:
    factors = [activity_factor(level) for level in ActivityLevel]
    assert factors == sorted(factors)
    assert activity_factor(ActivityLevel.SEDENTARY) == 1.2
    assert activity_factor("definitely-not-a-level") == 1.375  # safe default


def test_age_from_birth_date() -> None:
    today = date(2026, 10, 1)
    assert age_from_birth_date(date(1990, 10, 1), today) == 36
    assert age_from_birth_date(date(1990, 10, 2), today) == 35  # birthday tomorrow
    assert age_from_birth_date(None, today) is None


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #
def test_maintain_targets_equal_tdee() -> None:
    result = compute_targets(
        BodyParams(
            sex=Sex.MALE,
            age_years=30,
            height_cm=180,
            weight_kg=80,
            activity_level=ActivityLevel.MODERATE,
            goal_mode="maintain",
        )
    )
    assert result.ok
    assert result.bmr_kcal == 1780
    assert result.tdee_kcal == pytest.approx(1780 * 1.55, abs=1)
    assert result.target_kcal == pytest.approx(2759, abs=5)


def test_deficit_applies_and_reports_effective_percentage() -> None:
    result = compute_targets(
        BodyParams(
            sex=Sex.MALE,
            age_years=30,
            height_cm=180,
            weight_kg=80,
            activity_level=ActivityLevel.MODERATE,
            goal_mode="lose",
            goal_rate_pct=15,
        )
    )
    assert result.target_kcal == pytest.approx(2345, abs=10)
    assert result.deficit_pct == pytest.approx(15, abs=0.5)
    assert "aggressive_deficit" not in result.warnings


def test_aggressive_deficit_is_flagged() -> None:
    result = compute_targets(
        BodyParams(
            sex=Sex.MALE,
            age_years=30,
            height_cm=180,
            weight_kg=90,
            activity_level=ActivityLevel.LIGHT,
            goal_mode="lose",
            goal_rate_pct=30,
        )
    )
    assert "aggressive_deficit" in result.warnings
    assert "high_deficit" in result.warnings


def test_target_never_drops_below_the_safe_floor() -> None:
    """Small woman, sedentary, 30 % deficit -> clamped to 1200 kcal."""
    result = compute_targets(
        BodyParams(
            sex=Sex.FEMALE,
            age_years=55,
            height_cm=150,
            weight_kg=48,
            activity_level=ActivityLevel.SEDENTARY,
            goal_mode="lose",
            goal_rate_pct=30,
        )
    )
    assert result.safe_floor_kcal == SAFE_FLOOR_FEMALE
    assert result.target_kcal == SAFE_FLOOR_FEMALE
    assert "below_safe_floor" in result.warnings
    # the *effective* deficit shrinks because the floor bit
    assert result.deficit_pct is not None and result.deficit_pct < 30


def test_custom_safe_floor_is_respected() -> None:
    result = compute_targets(
        BodyParams(
            sex=Sex.MALE,
            age_years=40,
            height_cm=175,
            weight_kg=70,
            activity_level=ActivityLevel.SEDENTARY,
            goal_mode="lose",
            goal_rate_pct=25,
            safe_floor_kcal=1800,
        )
    )
    assert result.safe_floor_kcal == 1800
    assert result.target_kcal >= 1800


def test_default_floors_by_sex() -> None:
    male = compute_targets(BodyParams(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80))
    female = compute_targets(BodyParams(sex=Sex.FEMALE, age_years=30, height_cm=165, weight_kg=60))
    assert male.safe_floor_kcal == SAFE_FLOOR_MALE
    assert female.safe_floor_kcal == SAFE_FLOOR_FEMALE
    assert ABSOLUTE_FLOOR_KCAL < SAFE_FLOOR_FEMALE


def test_macros_are_consistent_with_the_calorie_target() -> None:
    result = compute_targets(
        BodyParams(
            sex=Sex.MALE,
            age_years=30,
            height_cm=180,
            weight_kg=80,
            activity_level=ActivityLevel.MODERATE,
            goal_mode="maintain",
            protein_g_per_kg=2.0,
            fat_min_g_per_kg=0.8,
        )
    )
    assert result.target_protein_g == 160  # 2 g/kg * 80 kg
    assert result.target_fat_g == 64  # 0.8 g/kg * 80 kg
    remaining_kcal = result.target_kcal - 160 * 4 - 64 * 9
    assert result.target_carbs_g == round(remaining_kcal / 4, 1)
    assert result.macros_kcal["protein"] == 160 * 4
    assert result.macros_kcal["fat"] == 64 * 9
    # sum of macros never exceeds the target in a way that starves carbs
    total = sum(result.macros_kcal.values())
    assert abs(total - result.target_kcal) <= 4  # rounding only


def test_protein_is_clamped_to_a_sane_range() -> None:
    low = compute_targets(BodyParams(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, protein_g_per_kg=0.1))
    high = compute_targets(BodyParams(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, protein_g_per_kg=9.0))
    assert low.target_protein_g == round(1.2 * 80)  # MIN_PROTEIN_G_PER_KG
    assert high.target_protein_g == round(3.0 * 80)  # MAX_PROTEIN_G_PER_KG


def test_fiber_uses_14g_per_1000_kcal() -> None:
    result = compute_targets(
        BodyParams(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
    )
    expected = round(14 * result.target_kcal / 1000)  # whole grams
    assert result.target_fiber_g == expected
    assert result.target_fiber_g >= 25  # sanity: a 2 700 kcal target wants ~38 g


def test_water_is_between_30_and_35_ml_per_kg() -> None:
    base = BodyParams(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.LIGHT)
    low = compute_targets(base.with_updates(water_ml_per_kg=10))
    high = compute_targets(base.with_updates(water_ml_per_kg=99))
    mid = compute_targets(base.with_updates(water_ml_per_kg=33))
    assert low.target_water_ml == 30 * 80  # clamped to the minimum
    assert high.target_water_ml == 35 * 80  # clamped to the maximum
    assert mid.target_water_ml == 33 * 80


def test_low_body_fat_plus_deficit_warns() -> None:
    result = compute_targets(
        BodyParams(
            sex=Sex.MALE,
            age_years=25,
            height_cm=180,
            weight_kg=72,
            body_fat_pct=7,
            activity_level=ActivityLevel.ACTIVE,
            goal_mode="lose",
            goal_rate_pct=15,
        )
    )
    assert "low_body_fat_deficit" in result.warnings


def test_compute_targets_without_weight_is_not_ok() -> None:
    result = compute_targets(BodyParams(sex=Sex.MALE, age_years=30, height_cm=180))
    assert result.ok is False
    assert result.target_kcal is None
    assert "missing_body_params" in result.warnings


# --------------------------------------------------------------------------- #
# Expenditure estimates
# --------------------------------------------------------------------------- #
def test_met_formula() -> None:
    # 8 MET, 80 kg, 45 min -> 8 * 80 * 0.75 = 480 kcal
    assert kcal_from_met(8, 80, 45) == pytest.approx(480.0)


def test_steps_formula_scales_with_weight() -> None:
    assert kcal_from_steps(10000, 70) == pytest.approx(350.0)
    assert kcal_from_steps(10000, 100) > kcal_from_steps(10000, 70)


def test_epley_1rm() -> None:
    assert estimate_1rm_epley(100, 1) == pytest.approx(100)
    assert estimate_1rm_epley(100, 5) == pytest.approx(116.67, abs=0.01)
    assert estimate_1rm_epley(0, 5) == 0


# --------------------------------------------------------------------------- #
# Scoring (gamification safety rule)
# --------------------------------------------------------------------------- #
def test_score_peaks_inside_the_target_zone() -> None:
    target, floor = 2400, 1500
    assert score_kcal(2400, target, floor) == 1.0
    assert score_kcal(2250, target, floor) == 1.0  # -6 %
    assert score_kcal(2600, target, floor) == 1.0  # +8 %


def test_score_is_zero_below_the_safe_floor() -> None:
    assert score_kcal(1499, 2400, 1500) == 0.0
    assert score_kcal(900, 2400, 1500) == 0.0


def test_eating_less_is_never_better() -> None:
    """The core anti-restriction rule: 1800 kcal must not score above 2400."""
    target, floor = 2400, 1500
    assert score_kcal(1800, target, floor) < score_kcal(2400, target, floor)
    assert score_kcal(1500, target, floor) == 0.0  # exactly at the floor -> no reward
    assert score_kcal(2000, target, floor) < score_kcal(2200, target, floor)


def test_score_falls_off_on_the_high_side_too() -> None:
    target, floor = 2400, 1500
    assert 0 < score_kcal(3000, target, floor) < 1
    assert score_kcal(2600, target, floor) == 1.0  # +8 % is inside the zone
    assert score_kcal(3400, target, floor) == 0.0  # +40 % is not


def test_score_handles_degenerate_input() -> None:
    assert score_kcal(0, 2400, 1500) == 0.0
    assert score_kcal(2400, 0, 1500) == 0.0
