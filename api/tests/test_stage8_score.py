"""Stage 8: the daily score — the calorie curve, components, exclusion and
renormalisation, user weights, rings and the binding safety rules."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.schemas.gamification import (
    DEFAULT_SCORE_WEIGHTS,
    read_score_weights,
    read_xp_multipliers,
    validate_xp_weights,
)
from app.services.day_metrics import DayMetrics, kcal_zone, safe_floor_kcal
from app.services.gamification.context import GameContext, Targets
from app.services.gamification.score import compute_day_score, kcal_curve, rings
from tests.stage8_helpers import NOON, TODAY, YESTERDAY

T, F = 2000, 1500  # target and floor (a man)
ZONE = kcal_zone(T, F)  # [1840, 2120]


def ctx(**over) -> GameContext:
    targets = over.pop("targets", Targets(kcal=T, protein_g=150, fiber_g=30, water_ml=2500, steps=8000))
    floor = over.pop("floor", F)
    values = {
        "user_id": uuid.uuid4(),
        "settings": None,
        "tz": "Asia/Bangkok",
        "now": NOON,
        "week_start": 1,
        "targets": targets,
        "floor": floor,
        "zone": kcal_zone(targets.kcal, floor),
        "score_weights": dict(DEFAULT_SCORE_WEIGHTS),
        "multipliers": read_xp_multipliers(None),
    }
    values.update(over)
    return GameContext(**values)


def day(**fields) -> DayMetrics:
    return DayMetrics(day=fields.pop("day", YESTERDAY), **fields)


def component(score, key):
    return next(item for item in score.components if item.key == key)


# --------------------------------------------------------------------------- #
# The calorie curve
# --------------------------------------------------------------------------- #
def test_zone_never_starts_below_the_floor() -> None:
    assert (ZONE.low, ZONE.high) == (1840, 2120)
    woman = kcal_zone(1300, 1200)  # 0.92 · 1300 = 1196 < 1200
    assert (woman.low, woman.high) == (1200, 1378)
    assert kcal_curve(1199, woman, meals=3, provisional=False) == (0.0, "below_floor")
    assert kcal_curve(1200, woman, meals=3, provisional=False) == (1.0, "in_zone")
    # a floor above 1.06·T collapses the zone onto the floor (never below it)
    tight = kcal_zone(1300, 1500)
    assert (tight.low, tight.high) == (1500, 1500)
    assert kcal_curve(1499, tight, meals=2, provisional=False)[0] == 0.0
    assert kcal_curve(1500, tight, meals=2, provisional=False)[0] == 1.0


@pytest.mark.parametrize(
    ("kcal", "expected", "state"),
    [
        (F - 1, 0.0, "below_floor"),  # never rewarded
        (F, 0.25, "below_zone"),
        (1839, 0.25 + 0.75 * 339 / 340, "below_zone"),  # zone_low − 1
        (1840, 1.0, "in_zone"),  # zone_low
        (T, 1.0, "in_zone"),
        (2120, 1.0, "in_zone"),  # zone_high
        (2121, 1.0 - 0.75 * 1 / 580, "above_zone"),  # zone_high + 1
        (2700, 0.25, "above_zone"),  # 1.35·T
        (2950, 0.125, "above_zone"),
        (3200, 0.0, "above_zone"),  # 1.6·T
        (4000, 0.0, "above_zone"),
    ],
)
def test_calorie_curve_every_edge_of_a_finished_day(kcal: float, expected: float, state: str) -> None:
    value, got_state = kcal_curve(kcal, ZONE, meals=3, provisional=False)
    assert value == pytest.approx(expected, abs=1e-9)
    assert got_state == state


def test_calorie_curve_is_monotonic_towards_the_zone() -> None:
    below = [kcal_curve(kcal, ZONE, meals=3, provisional=False)[0] for kcal in range(1500, 1841, 20)]
    above = [kcal_curve(kcal, ZONE, meals=3, provisional=False)[0] for kcal in range(2120, 3300, 20)]
    assert below == sorted(below) and above == sorted(above, reverse=True)
    # eating less than the floor is strictly worse than eating at the floor
    assert kcal_curve(1400, ZONE, meals=3, provisional=False)[0] < kcal_curve(1500, ZONE, meals=3, provisional=False)[0]


def test_today_is_provisional_progress_below_the_zone() -> None:
    assert kcal_curve(920, ZONE, meals=1, provisional=True) == (0.5, "progress")  # 920 / 1840
    assert kcal_curve(1000, ZONE, meals=1, provisional=True)[1] == "progress"  # below the floor: not a verdict
    assert kcal_curve(1840, ZONE, meals=3, provisional=True) == (1.0, "in_zone")
    assert kcal_curve(2700, ZONE, meals=3, provisional=True) == pytest.approx((0.25, "above_zone"))
    assert kcal_curve(0, ZONE, meals=0, provisional=True) == (0.0, "no_meals")


def test_today_score_is_labelled_provisional() -> None:
    score = compute_day_score(day(day=TODAY, kcal=900, meals=1), ctx(), provisional=True)
    assert score.provisional is True
    assert component(score, "kcal").detail["state"] == "progress"
    finished = compute_day_score(day(kcal=900, meals=1), ctx(), provisional=False)
    assert finished.provisional is False and component(finished, "kcal").detail["state"] == "below_floor"


# --------------------------------------------------------------------------- #
# Components, exclusion, renormalisation
# --------------------------------------------------------------------------- #
def test_every_component_of_a_full_day() -> None:
    m = day(
        kcal=2000,
        protein_g=120,
        fiber_g=15,
        meals=3,
        water_ml=2000,
        steps=4000,
        tasks_done=3,
        tasks_open_due=1,
        sleep_minutes=315,
        journal=True,
    )
    score = compute_day_score(m, ctx(), provisional=False)
    values = {item.key: round(item.value, 4) for item in score.components}
    assert values == {
        "kcal": 1.0,
        "protein": 0.8,
        "fiber": 0.5,
        "water": 0.8,
        "activity": 0.5,
        "tasks": 0.75,
        "sleep": 0.75,
        "review": 1.0,
    }
    assert {item.key: item.weight for item in score.components} == DEFAULT_SCORE_WEIGHTS
    expected = 25 * 1 + 15 * 0.8 + 5 * 0.5 + 10 * 0.8 + 15 * 0.5 + 15 * 0.75 + 10 * 0.75 + 5 * 1  # 78.75
    assert score.value == pytest.approx(expected, abs=0.051)
    assert score.credit is True and score.counts_for_overall is True
    detail = component(score, "kcal").detail
    assert (detail["zone_low"], detail["zone_high"], detail["floor"], detail["target"]) == (1840, 2120, 1500, 2000)


def test_a_workout_fills_activity_and_rest_days_still_count_steps() -> None:
    with_workout = compute_day_score(day(workouts=1, steps=None), ctx(), provisional=False)
    assert component(with_workout, "activity").value == 1.0
    rest_day = compute_day_score(day(steps=8000, plan="rest"), ctx(), provisional=False)
    assert component(rest_day, "activity").value == 1.0  # a planned rest day never empties the ring


def test_excluded_components_drop_out_and_the_rest_is_renormalised() -> None:
    m = day(kcal=2000, protein_g=150, fiber_g=30, meals=3, water_ml=2500, steps=8000, journal=True)
    score = compute_day_score(m, ctx(), provisional=False)  # no tasks due/done, no sleep logged
    excluded = {item.key for item in score.components if item.excluded}
    assert excluded == {"tasks", "sleep"}
    weights = {item.key: item.weight for item in score.components if not item.excluded}
    assert sum(weights.values()) == pytest.approx(100)
    assert weights["kcal"] == pytest.approx(25 * 100 / 75)
    assert score.value == 100.0

    no_targets = ctx(targets=Targets(None, None, None, None, None))
    bare = compute_day_score(m, no_targets, provisional=False)
    assert {item.key for item in bare.components if item.excluded} == {
        "kcal",
        "protein",
        "fiber",
        "water",
        "activity",
        "tasks",
        "sleep",
    }
    assert bare.value == 100.0  # only the review is left, and it is done


def test_no_meals_means_zero_nutrition_never_exclusion() -> None:
    """Not logging food must not raise the score."""
    empty = day(water_ml=2500, steps=8000, journal=True, sleep_minutes=420)
    score = compute_day_score(empty, ctx(), provisional=False)
    for key in ("kcal", "protein", "fiber"):
        item = component(score, key)
        assert (item.value, item.excluded, item.detail["state"]) == (0.0, False, "no_meals")
    logged = compute_day_score(
        day(water_ml=2500, steps=8000, journal=True, sleep_minutes=420, kcal=2000, meals=3, protein_g=150, fiber_g=30),
        ctx(),
        provisional=False,
    )
    assert score.value < logged.value
    assert score.credit is False and score.counts_for_overall is False


def test_below_the_floor_gives_no_kcal_value_and_no_credit_whatever_the_weights() -> None:
    m = day(kcal=1400, protein_g=150, fiber_g=30, meals=3, water_ml=2500, steps=9000, journal=True, sleep_minutes=480)
    score = compute_day_score(m, ctx(), provisional=False)
    assert component(score, "kcal").value == 0.0 and component(score, "kcal").detail["state"] == "below_floor"
    assert score.credit is False and score.counts_for_overall is False
    # a user who zeroes the nutrition weights still gets no streak credit for that day
    zeroed = ctx(score_weights={**DEFAULT_SCORE_WEIGHTS, "kcal": 0, "protein": 0, "fiber": 0})
    tuned = compute_day_score(m, zeroed, provisional=False)
    assert tuned.value == 100.0
    assert tuned.counts_for_overall is False
    # today: the progress below the floor is shown but never credited
    today = compute_day_score(day(day=TODAY, kcal=1400, meals=2), ctx(), provisional=True)
    assert component(today, "kcal").value > 0 and today.credit is False


def test_burned_calories_never_change_the_kcal_part() -> None:
    plain = day(kcal=1700, protein_g=150, fiber_g=30, meals=3)
    burned = day(kcal=1700, protein_g=150, fiber_g=30, meals=3, workout_kcal=900.0, active_kcal=700.0, workouts=1)
    a, b = compute_day_score(plain, ctx(), provisional=False), compute_day_score(burned, ctx(), provisional=False)
    assert component(a, "kcal").value == component(b, "kcal").value
    assert component(a, "kcal").detail == component(b, "kcal").detail  # same target, zone and floor


def test_user_weight_overrides_are_validated_and_renormalised() -> None:
    weights = read_score_weights({"score_weights": {"kcal": 50, "review": 0, "sleep": "lots", "bogus": 9}})
    assert weights == {**DEFAULT_SCORE_WEIGHTS, "kcal": 50.0, "review": 0.0}  # junk ignored on read
    m = day(kcal=2000, protein_g=150, fiber_g=30, meals=3, water_ml=0, steps=0, journal=False)
    score = compute_day_score(m, ctx(score_weights=weights), provisional=False)
    present = {item.key: item.weight for item in score.components if not item.excluded}
    assert present["review"] == 0.0
    assert present["kcal"] == pytest.approx(50 * 100 / (50 + 15 + 5 + 10 + 15))
    assert sum(present.values()) == pytest.approx(100)
    # every present component zeroed → the defaults apply
    zero = read_score_weights({"score_weights": dict.fromkeys(DEFAULT_SCORE_WEIGHTS, 0)})
    assert zero == DEFAULT_SCORE_WEIGHTS
    # legacy stage-1 keys are ignored
    assert read_score_weights({"nutrition": 0.3, "xp": {"meal_logged": 10}}) == DEFAULT_SCORE_WEIGHTS


@pytest.mark.parametrize(
    "value",
    [
        {"score_weights": {"kcal": -1}},
        {"score_weights": {"kcal": 101}},
        {"score_weights": {"calories": 10}},
        {"score_weights": {"kcal": True}},
        {"score_weights": dict.fromkeys(DEFAULT_SCORE_WEIGHTS, 0)},
        {"score_weights": [1, 2]},
        {"xp_multipliers": {"nutrition": 2.5}},
        {"xp_multipliers": {"nutrition": -0.5}},
        {"xp_multipliers": {"fun": 1}},
    ],
)
def test_bad_weight_overrides_are_rejected(value) -> None:
    with pytest.raises(ValueError):
        validate_xp_weights(value)


def test_rings_group_the_components() -> None:
    m = day(kcal=2000, protein_g=75, fiber_g=30, meals=3, water_ml=2500, steps=4000, tasks_done=1, journal=False)
    score = compute_day_score(m, ctx(), provisional=False)
    values = rings(score, DEFAULT_SCORE_WEIGHTS)
    assert values["nutrition"] == pytest.approx((25 + 15 * 0.5 + 10 + 5) / 55, abs=1e-4)
    assert values["activity"] == 0.5
    assert values["discipline"] == pytest.approx((15 * 1 + 5 * 0) / 20)  # sleep excluded
    assert set(values) == {"nutrition", "activity", "discipline"}
    assert all(0 <= value <= 1 for value in values.values())


def test_safe_floor_defaults() -> None:
    from app.models import UserSettings

    assert safe_floor_kcal(UserSettings(sex="male")) == 1500
    assert safe_floor_kcal(UserSettings(sex="female")) == 1200
    # the same floor the target engine and Settings use (stage-1 default: 1200 for other/unknown)
    assert safe_floor_kcal(UserSettings(sex="other")) == 1200
    assert safe_floor_kcal(UserSettings(sex=None)) == 1200
    assert safe_floor_kcal(UserSettings(sex="female", safe_floor_kcal=1400)) == 1400
    assert safe_floor_kcal(None) == 1200


def test_day_metrics_tracked_minutes_round_half_up() -> None:
    assert DayMetrics(day=date(2026, 1, 1), tracked_seconds=89, running_seconds=0).tracked_minutes == 1
    assert DayMetrics(day=date(2026, 1, 1), tracked_seconds=60, running_seconds=30).tracked_minutes == 2


def test_achievement_day_counts_never_include_low_intake_or_unfinished_days() -> None:
    from app.services.gamification.achievements import DayFlag
    from app.services.gamification.achievements import day_metrics as achievement_days

    low, ok, unlogged = date(2026, 7, 10), date(2026, 7, 11), date(2026, 7, 12)
    history = {
        low: DayMetrics(day=low, kcal=1400, meals=3, protein_g=200, water_ml=3000, steps=9000),
        ok: DayMetrics(day=ok, kcal=1900, meals=3, protein_g=200),
        unlogged: DayMetrics(day=unlogged, water_ml=3000),
    }
    flags = {
        low: DayFlag(99.0, False),
        ok: DayFlag(99.0, True),
        unlogged: DayFlag(97.0, False),
        TODAY: DayFlag(100.0, True),  # today is not finished
    }
    metrics = achievement_days(history, flags, ctx())
    assert metrics["days_protein_target"] == 1  # the below-floor day never counts
    # water needs nutrition credit too: neither the below-floor day nor the water-only day (H1)
    assert metrics["days_water_target"] == 0 and metrics["days_steps_target"] == 1
    assert metrics["perfect_days"] == 1
    assert metrics["days_score_70_in_week"] == 1
