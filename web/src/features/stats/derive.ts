/**
 * Presentation totals over the server's per-day values: sums, counts and means
 * for the numbers rows. Nothing here computes a target, a zone, a score or a
 * day boundary — the server sends those (and says which day is `today`).
 *
 * Conventions, the same as the server's own averages (api/app/services/stats.py):
 * * sums include today (what happened so far is real);
 * * means are taken over **finished** days (before today) **with data** — a day
 *   in progress or a day without logging never pulls a mean down;
 * * the future (after today) never counts.
 */
import type { ActivityStats, BodyStats, NutritionStats, PlannerStats, ProgressStats } from './types'

export function sum(values: (number | null | undefined)[]): number {
  return values.reduce<number>((total, value) => total + (value ?? 0), 0)
}

export function mean(values: (number | null | undefined)[]): number | null {
  const present = values.filter((value): value is number => value != null)
  return present.length ? sum(present) / present.length : null
}

/** Days of the period up to and including today. */
export function elapsedDays(days: { date: string }[], today: string): number {
  return days.filter((day) => day.date <= today).length
}

// --------------------------------------------------------------------------- //
// Nutrition
// --------------------------------------------------------------------------- //
/**
 * Days with fiber ≥ its target. The API counts protein days (with the safe-floor
 * rule) but not fiber days, so this mirrors the protein rule exactly: a day with
 * meals, up to today, intake at or above the safe floor — a fiber "success" is
 * never shown for a day eaten below the floor.
 */
export function fiberDays(stats: NutritionStats): number {
  const target = stats.target_fiber_g
  if (!target) return 0
  return stats.days.filter(
    (day) =>
      day.date <= stats.today &&
      day.meals > 0 &&
      day.kcal != null &&
      day.kcal >= stats.floor &&
      day.fiber_g != null &&
      day.fiber_g >= target,
  ).length
}

/** Finished days with meals (the denominator of the zone count and of the averages). */
export function finishedLoggedDays(stats: NutritionStats): number {
  return stats.averages.days
}

export function hasNutrition(stats: NutritionStats): boolean {
  return stats.days.some((day) => day.meals > 0 || day.water_ml != null)
}

// --------------------------------------------------------------------------- //
// Activity
// --------------------------------------------------------------------------- //
export function activityTotals(stats: ActivityStats) {
  const finished = stats.days.filter((day) => day.date < stats.today)
  return {
    stepsMean: mean(finished.map((day) => day.steps)),
    stepsDays: stats.days.filter((day) => day.steps != null).length,
    onTargetDays: stats.days.filter((day) => day.on_target === true).length,
    workouts: sum(stats.days.map((day) => day.workouts)),
    workoutKcal: sum(stats.days.map((day) => day.workout_kcal)),
    activeKcal: sum(stats.days.map((day) => day.active_kcal)),
    activeDays: stats.days.filter((day) => day.active_kcal != null).length,
  }
}

export function hasSteps(stats: ActivityStats): boolean {
  return stats.days.some((day) => day.steps != null)
}

export function hasWorkouts(stats: ActivityStats): boolean {
  return stats.days.some((day) => day.workouts > 0) || stats.weeks.some((week) => week.workouts > 0)
}

export function hasBurned(stats: ActivityStats): boolean {
  return stats.days.some((day) => day.workout_kcal > 0 || day.active_kcal != null)
}

// --------------------------------------------------------------------------- //
// Body
// --------------------------------------------------------------------------- //
export function bodyTotals(stats: BodyStats) {
  const weighIns = stats.weight.filter((point) => point.kg != null)
  const nights = stats.sleep.filter((point) => point.minutes != null)
  return {
    latest: weighIns.length ? weighIns[weighIns.length - 1] : null,
    weighIns: weighIns.length,
    nights: nights.length,
  }
}

// --------------------------------------------------------------------------- //
// Planner
// --------------------------------------------------------------------------- //
export function plannerTotals(stats: PlannerStats) {
  return {
    done: sum(stats.days.map((day) => day.done)),
    created: sum(stats.days.map((day) => day.created)),
    tracked: sum(stats.days.map((day) => day.tracked_minutes)),
  }
}

export function hasPlanner(stats: PlannerStats): boolean {
  return (
    stats.days.some((day) => day.done > 0 || day.created > 0 || day.tracked_minutes > 0) ||
    stats.by_area.some((area) => area.minutes > 0) ||
    stats.projects.length > 0
  )
}

// --------------------------------------------------------------------------- //
// Progress
// --------------------------------------------------------------------------- //
export function progressTotals(stats: ProgressStats) {
  return {
    xp: sum(stats.xp.map((day) => day.xp)),
    scoreMean: mean(stats.scores.filter((day) => day.date < stats.today).map((day) => day.score)),
    scoredDays: stats.scores.filter((day) => day.date < stats.today && day.score != null).length,
    overallDays: stats.streak_calendar.filter((day) => day.overall).length,
    freezeDays: stats.streak_calendar.filter((day) => day.freeze).length,
  }
}

export function hasProgress(stats: ProgressStats): boolean {
  return stats.scores.some((day) => day.score != null) || stats.xp.some((day) => day.xp > 0)
}
