/**
 * The default XP rules, shown in "How XP works" — a mirror of the constants in
 * `api/app/services/gamification/xp.py`, `streaks.py` and `score.py` (stage-8
 * contract §2–§3). Display only: every award is computed by the server, which
 * also applies the user's XP multipliers from their settings.
 */
export const XP_RULES = {
  meal: { points: 5, perDay: 5 },
  protein: 20,
  fiber: 10,
  water: 10,
  kcalZone: 30,
  steps: 15,
  workout: { points: 40, perDay: 2 },
  record: { points: 15, perDay: 3 },
  task: { p1: 12, p2: 9, p3: 6, p4: 4, subtask: 2, cap: 120 },
  project: 60,
  timer: { minutes: 10, cap: 48 },
  review: 15,
  sleep: 5,
  weight: 5,
} as const

/** Overall streak (daily score ≥ 60): days → XP, paid once per run. */
export const STREAK_MILESTONES: readonly (readonly [number, number])[] = [
  [3, 25],
  [7, 50],
  [14, 75],
  [30, 100],
  [60, 150],
  [100, 200],
  [200, 300],
  [365, 500],
]

export const FREEZE = { perWeek: 1, max: 2 } as const

/** XP of an overall-streak milestone (null when it is not one). */
export function milestoneXp(days: number | null | undefined): number | null {
  if (days == null) return null
  return STREAK_MILESTONES.find(([at]) => at === days)?.[1] ?? null
}
