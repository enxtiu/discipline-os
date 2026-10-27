/**
 * Stats types (stage 8): the `/stats/*` series behind the dashboards.
 *
 * Every number is computed by the server for one calendar period in the
 * user's timezone (`api/app/schemas/stats.py`): targets, the calorie zone, the
 * safe floor, day boundaries, averages and adherence counts arrive ready to
 * display. Each field says whether a day without data is `null` ("nothing
 * logged") or `0` ("nothing happened") — the charts keep that distinction.
 * Burned calories are never part of nutrition: they are their own series in
 * activity.
 */
import type { PersonalRecord } from '@/api/types'

export type Period = 'week' | 'month' | 'quarter' | 'year'
export type StatsSection = 'nutrition' | 'activity' | 'body' | 'planner' | 'progress'
export type Muscle =
  | 'chest'
  | 'back'
  | 'lats'
  | 'traps'
  | 'shoulders'
  | 'biceps'
  | 'triceps'
  | 'forearms'
  | 'abs'
  | 'obliques'
  | 'lower_back'
  | 'glutes'
  | 'quads'
  | 'hamstrings'
  | 'calves'
  | 'full_body'
  | 'cardio'
  | 'other'

/** The span every series carries (`from`/`to` inclusive; days after `today` are the future). */
export interface StatsRange {
  period: Period
  from: string
  to: string
  today: string
}

// --------------------------------------------------------------------------- //
// Nutrition
// --------------------------------------------------------------------------- //
export interface Zone {
  /** max(floor, 0.92 × target) — never below the safe floor */
  low: number
  /** 1.06 × target */
  high: number
}

export interface NutritionDay {
  date: string
  /** eaten (meals only, never burned calories); null = no meal logged */
  kcal: number | null
  protein_g: number | null
  fat_g: number | null
  carbs_g: number | null
  fiber_g: number | null
  /** null = no water logged */
  water_ml: number | null
  meals: number
  /** finished days with meals only; null for no meals, today, the future, no target */
  in_zone: boolean | null
}

/** Means over finished days with at least one meal (a partial or unlogged day never pulls them down). */
export interface NutritionAverages {
  days: number
  kcal: number | null
  protein_g: number | null
  fat_g: number | null
  carbs_g: number | null
  fiber_g: number | null
  meals: number | null
  water_days: number
  water_ml: number | null
}

/** Day counts up to today (future days never count). */
export interface NutritionAdherence {
  zone_days: number
  /** protein ≥ target on a day at or above the safe floor */
  protein_days: number
  water_days: number
  /** days with at least one meal, today included */
  logged_days: number
}

export interface NutritionStats extends StatsRange {
  target_kcal: number | null
  target_protein_g: number | null
  target_fiber_g: number | null
  target_water_ml: number | null
  zone: Zone | null
  floor: number
  days: NutritionDay[]
  averages: NutritionAverages
  adherence: NutritionAdherence
}

// --------------------------------------------------------------------------- //
// Activity (burned calories live here, on their own)
// --------------------------------------------------------------------------- //
export interface ActivityDay {
  date: string
  /** null = nothing synced or entered */
  steps: number | null
  on_target: boolean | null
  /** completed workouts (0 = none) */
  workouts: number
  /** burned in workouts — never food budget */
  workout_kcal: number
  /** Health's active energy — never food budget; null = not reported */
  active_kcal: number | null
}

/** A whole week (week start … +6 days) that overlaps the period. */
export interface ActivityWeek {
  week_start: string
  workouts: number
  /** workout days in the weekly plan (0 = none planned) */
  planned: number
  /** what makes the week count for the workout streak: planned, or 3 without a plan */
  required: number
  volume_kg: number
}

export interface MuscleVolume {
  muscle: Muscle
  sets: number
  volume_kg: number
}

export interface ActivityStats extends StatsRange {
  target_steps: number | null
  days: ActivityDay[]
  weeks: ActivityWeek[]
  /** most volume first */
  muscle_volume: MuscleVolume[]
  /** newest first, at most 100 */
  records: PersonalRecord[]
  records_total: number
}

// --------------------------------------------------------------------------- //
// Body
// --------------------------------------------------------------------------- //
export interface WeightPoint {
  date: string
  /** the weigh-in of that day; null = none */
  kg: number | null
  /** mean of the weigh-ins of the 7 days ending that day; null = none or future */
  avg7: number | null
}

export interface SleepPoint {
  date: string
  /** the night ending that morning; null = no entry */
  minutes: number | null
}

export interface BodyStats extends StatsRange {
  weight: WeightPoint[]
  /** the period's newest weigh-in minus the newest one 30–45 days before it */
  change_30d: number | null
  sleep: SleepPoint[]
  sleep_avg: number | null
}

// --------------------------------------------------------------------------- //
// Planner
// --------------------------------------------------------------------------- //
export interface Ref {
  id: string
  name: string
  color: string | null
}

export interface PlannerDay {
  date: string
  done: number
  created: number
  /** the running timer counts up to now */
  tracked_minutes: number
}

export interface AreaTime {
  /** null = time without an area */
  area: Ref | null
  minutes: number
  /** the weekly target scaled to the period; null without a target */
  target_minutes: number | null
}

export interface ProjectProgress {
  project: Ref
  /** top-level tasks done (all time) */
  done: number
  /** top-level tasks, cancelled ones excluded (all time) */
  total: number
  done_in_period: number
}

export interface PlannerStats extends StatsRange {
  days: PlannerDay[]
  /** most time first */
  by_area: AreaTime[]
  projects: ProjectProgress[]
}

// --------------------------------------------------------------------------- //
// Progress (gamification history)
// --------------------------------------------------------------------------- //
export interface ScorePoint {
  date: string
  /** the stored daily score 0–100; null = never scored (no data yet, the future) */
  score: number | null
}

export interface XpPoint {
  date: string
  xp: number
}

export interface StreakDay {
  date: string
  /** counted for the overall streak (score ≥ 60 with credit) */
  overall: boolean
  /** a freeze token saved the overall streak that day */
  freeze: boolean
}

export interface ProgressStats extends StatsRange {
  scores: ScorePoint[]
  xp: XpPoint[]
  streak_calendar: StreakDay[]
}

export interface StatsBySection {
  nutrition: NutritionStats
  activity: ActivityStats
  body: BodyStats
  planner: PlannerStats
  progress: ProgressStats
}

// --------------------------------------------------------------------------- //
// Drift guard: every read model above must be a subtype of the server's
// OpenAPI schema (`npm run gen:api`) and must not invent fields. The generator
// marks defaulted response fields optional (`RefOut.color?`) and widens the
// muscle literal list to the schema's enum; the server always sends them. A
// schema change that breaks these types fails `tsc`.
// --------------------------------------------------------------------------- //
import type { components } from '@/api/generated/schema'

type S = components['schemas']
type NoExtraKeys<T, U> = Exclude<keyof T, keyof U> extends never ? true : false
type Matches<T, U> = [T] extends [U] ? NoExtraKeys<T, U> : false
type Check<T extends true> = T

/** Compile-time only (exported so `noUnusedLocals` keeps it): one entry per read model. */
export type StatsSchemaGuard = [
  Check<Matches<Zone, S['ZoneOut']>>,
  Check<Matches<NutritionDay, S['NutritionDayOut']>>,
  Check<Matches<NutritionAverages, S['NutritionAveragesOut']>>,
  Check<Matches<NutritionAdherence, S['NutritionAdherenceOut']>>,
  Check<Matches<NutritionStats, S['NutritionStatsOut']>>,
  Check<Matches<ActivityDay, S['ActivityDayOut']>>,
  Check<Matches<ActivityWeek, S['ActivityWeekOut']>>,
  Check<Matches<MuscleVolume, S['MuscleVolumeOut']>>,
  Check<Matches<ActivityStats, S['ActivityStatsOut']>>,
  Check<Matches<WeightPoint, S['WeightPointOut']>>,
  Check<Matches<SleepPoint, S['SleepPointOut']>>,
  Check<Matches<BodyStats, S['BodyStatsOut']>>,
  Check<Matches<Ref, S['RefOut']>>,
  Check<Matches<PlannerDay, S['PlannerDayOut']>>,
  Check<Matches<AreaTime, S['AreaTimeOut']>>,
  Check<Matches<ProjectProgress, S['ProjectProgressOut']>>,
  Check<Matches<PlannerStats, S['PlannerStatsOut']>>,
  Check<Matches<ScorePoint, S['ScorePointOut']>>,
  Check<Matches<XpPoint, S['XpPointOut']>>,
  Check<Matches<StreakDay, S['StreakDayOut']>>,
  Check<Matches<ProgressStats, S['ProgressStatsOut']>>,
  // the generator's muscle enum and ours are the same set, both ways
  Check<[Muscle] extends [S['MuscleVolumeOut']['muscle']] ? ([S['MuscleVolumeOut']['muscle']] extends [Muscle] ? true : false) : false>,
  Check<[Period] extends [S['NutritionStatsOut']['period']] ? ([S['NutritionStatsOut']['period']] extends [Period] ? true : false) : false>,
]
