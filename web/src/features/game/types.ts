/**
 * Gamification types (stage 8).
 *
 * Every number here is computed by the server in the user's timezone and
 * returned ready to display (`api/app/schemas/gamification.py`); the client
 * never computes a score, a target, a level or a day boundary. Bilingual
 * reference data (level titles, quest and achievement texts) comes as
 * `*_ru` / `*_en` pairs — the UI picks one by its language.
 */

// --------------------------------------------------------------------------- //
// Literals
// --------------------------------------------------------------------------- //
export type ScoreKey = 'kcal' | 'protein' | 'water' | 'fiber' | 'activity' | 'tasks' | 'sleep' | 'review'
export type RingKey = 'nutrition' | 'activity' | 'discipline'
export type StreakKind = 'overall' | 'meal_logging' | 'water' | 'steps' | 'tasks' | 'review' | 'workout'
export type StreakUnit = 'day' | 'week'
export type QuestKind = 'daily' | 'weekly'
export type QuestStatus = 'active' | 'completed' | 'claimed' | 'expired'
export type QuestUnit = 'count' | 'ml' | 'steps' | 'min' | 'days'
export type Rarity = 'common' | 'rare' | 'epic' | 'legendary'
export type AchievementCategory =
  | 'nutrition'
  | 'training'
  | 'activity'
  | 'planner'
  | 'review'
  | 'streaks'
  | 'levels'
  | 'special'
export type ScoreState =
  | 'no_target'
  | 'no_meals'
  | 'below_floor'
  | 'below_zone'
  | 'progress'
  | 'in_zone'
  | 'above_zone'
  | 'no_data'
  | 'no_tasks'
  | 'workout'
  | 'ok'
export type DetailUnit = 'kcal' | 'g' | 'ml' | 'steps' | 'min'
export type XpKind =
  | 'meal_logged'
  | 'protein_target'
  | 'fiber_target'
  | 'water_target'
  | 'kcal_in_zone'
  | 'steps_target'
  | 'workout_completed'
  | 'workout_volume'
  | 'task_completed'
  | 'project_completed'
  | 'timer_session'
  | 'daily_review'
  | 'sleep_logged'
  | 'weight_logged'
  | 'streak_bonus'
  | 'quest_completed'
  | 'achievement_unlocked'
  | 'manual'

// --------------------------------------------------------------------------- //
// Score and rings
// --------------------------------------------------------------------------- //
/** What a score component was computed from (only the fields that apply are set). */
export interface ScoreDetail {
  state: ScoreState
  amount: number | null
  target: number | null
  unit: DetailUnit | null
  /** kcal: the target zone (lower edge ≥ the safe floor) */
  zone_low: number | null
  zone_high: number | null
  /** kcal: the safe floor */
  floor: number | null
  meals: number | null
  workouts: number | null
  done: number | null
  open_due: number | null
  has_entry: boolean | null
}

export interface ScoreComponent {
  key: ScoreKey
  /** 0..1 */
  value: number
  /** effective weight in points (non-excluded weights sum to 100); 0 if excluded */
  weight: number
  excluded: boolean
  detail: ScoreDetail
}

export interface DayScore {
  /** 0..100 */
  value: number
  /** today: the day is in progress (the kcal part is progress, not a verdict) */
  provisional: boolean
  components: ScoreComponent[]
}

export interface Ring {
  key: RingKey
  /** 0..1 */
  value: number
}

// --------------------------------------------------------------------------- //
// XP and levels
// --------------------------------------------------------------------------- //
export interface Level {
  level: number
  title_ru: string
  title_en: string
  min_xp: number
}

export interface LevelProgress extends Level {
  /** null at the top level */
  next_min_xp: number | null
  /** 0..1 within the current level (1 at the top level) */
  progress: number
}

export interface XpToday {
  today: number
  total: number
  level: LevelProgress
}

export interface XpDay {
  date: string
  xp: number
}

export interface ProfileXp {
  total: number
  today: number
  level: LevelProgress
  last_30_days: XpDay[]
}

export interface XpEvent {
  kind: XpKind
  points: number
  reason: string | null
}

export interface XpHistoryDay {
  date: string
  xp: number
  events: XpEvent[]
}

export interface XpHistory {
  items: XpHistoryDay[]
}

export interface ScoreHistoryDay {
  date: string
  /** null when the day has no score (before the first data, or the future) */
  score: number | null
  provisional: boolean
  components: ScoreComponent[]
}

export interface ScoreHistory {
  items: ScoreHistoryDay[]
}

// --------------------------------------------------------------------------- //
// Streaks
// --------------------------------------------------------------------------- //
/** Weekly streak: completed workouts this week vs the number that makes it count. */
export interface StreakPeriod {
  done: number
  required: number
}

export interface Streak {
  kind: StreakKind
  unit: StreakUnit
  /** days (or weeks) in a row, today/this week included once met */
  current: number
  longest: number
  /** today's (this week's) condition is met */
  today_met: boolean
  /** not met yet, a streak to lose, and it is after 18:00 local (weekly: the last day) */
  at_risk: boolean
  /** 0..2; +1 every week, one is used automatically for a missed day/week */
  freeze_tokens: number
  freeze_used_dates: string[]
  last_met_date: string | null
  broken_on: string | null
  period: StreakPeriod | null
  /** overall streak: the next XP milestone (days) */
  next_milestone: number | null
}

// --------------------------------------------------------------------------- //
// Quests
// --------------------------------------------------------------------------- //
export interface UserQuest {
  id: string
  quest_id: string
  code: string
  kind: QuestKind
  title_ru: string
  title_en: string
  description_ru: string | null
  description_en: string | null
  icon: string | null
  period_start: string
  period_end: string
  progress: number
  target: number
  unit: QuestUnit
  status: QuestStatus
  claimable: boolean
  xp_reward: number
  completed_at: string | null
  claimed_at: string | null
}

export interface QuestClaim {
  quest: UserQuest
  /** 0 on a repeated claim */
  xp_awarded: number
}

// --------------------------------------------------------------------------- //
// Achievements
// --------------------------------------------------------------------------- //
export interface AchievementProgress {
  value: number
  target: number
}

export interface Achievement {
  id: string
  /** 'secret' while a secret achievement is locked */
  code: string
  name_ru: string
  name_en: string
  description_ru: string | null
  description_en: string | null
  icon: string | null
  category: AchievementCategory
  rarity: Rarity
  xp_reward: number
  is_secret: boolean
  /** a locked secret achievement: name, description and progress are hidden */
  masked: boolean
  unlocked: boolean
  unlocked_at: string | null
  seen: boolean
  progress: AchievementProgress | null
}

export interface AchievementsSeenIn {
  ids: string[]
}

export interface Ok {
  ok: boolean
}

// --------------------------------------------------------------------------- //
// Screens
// --------------------------------------------------------------------------- //
export interface GameToday {
  date: string
  score: DayScore
  rings: Ring[]
  xp: XpToday
  streaks: Streak[]
  quests: UserQuest[]
  /** unlocked and not yet seen (POST /achievements/seen) */
  celebrate: Achievement[]
}

export interface ProfileStats {
  meals_logged_total: number
  workouts_total: number
  tasks_completed_total: number
  tracked_minutes_total: number
  journal_entries_total: number
}

export interface GameProfile {
  xp: ProfileXp
  levels: Level[]
  streaks: Streak[]
  /** unlocked first (newest first), then locked with progress; locked secrets are masked */
  achievements: Achievement[]
  stats: ProfileStats
}

// --------------------------------------------------------------------------- //
// Drift guard: every read model above must be a subtype of the server's
// OpenAPI schema (`npm run gen:api`) and must not invent fields. The generator
// marks defaulted response fields optional (`description_ru?`, `reason?`, …);
// the server always sends them (null when unset), so the hand-written types
// make them required. A schema change that breaks them fails `tsc`.
// --------------------------------------------------------------------------- //
import type { components } from '@/api/generated/schema'

type S = components['schemas']
type NoExtraKeys<T, U> = Exclude<keyof T, keyof U> extends never ? true : false
type Matches<T, U> = [T] extends [U] ? NoExtraKeys<T, U> : false
type Check<T extends true> = T

/** Compile-time only (exported so `noUnusedLocals` keeps it): one entry per read model. */
export type GameSchemaGuard = [
  Check<Matches<ScoreDetail, S['ScoreDetailOut']>>,
  Check<Matches<ScoreComponent, S['ScoreComponentOut']>>,
  Check<Matches<DayScore, S['DayScoreOut']>>,
  Check<Matches<Ring, S['RingOut']>>,
  Check<Matches<Level, S['LevelOut']>>,
  Check<Matches<LevelProgress, S['LevelProgressOut']>>,
  Check<Matches<XpToday, S['XpTodayOut']>>,
  Check<Matches<XpDay, S['XpDayOut']>>,
  Check<Matches<ProfileXp, S['ProfileXpOut']>>,
  Check<Matches<XpEvent, S['XpEventOut']>>,
  Check<Matches<XpHistoryDay, S['XpHistoryDayOut']>>,
  Check<Matches<XpHistory, S['XpHistoryOut']>>,
  Check<Matches<ScoreHistoryDay, S['ScoreHistoryDayOut']>>,
  Check<Matches<ScoreHistory, S['ScoreHistoryOut']>>,
  Check<Matches<StreakPeriod, S['StreakPeriodOut']>>,
  Check<Matches<Streak, S['StreakOut']>>,
  Check<Matches<UserQuest, S['UserQuestOut']>>,
  Check<Matches<QuestClaim, S['QuestClaimOut']>>,
  Check<Matches<AchievementProgress, S['AchievementProgressOut']>>,
  Check<Matches<Achievement, S['AchievementOut']>>,
  Check<Matches<GameToday, S['GamificationTodayOut']>>,
  Check<Matches<ProfileStats, S['ProfileStatsOut']>>,
  Check<Matches<GameProfile, S['GamificationProfileOut']>>,
  Check<Matches<Ok, S['OkOut']>>,
]

/** Request bodies: every key must exist on the server model (it forbids unknown fields). */
type InMatches<T, U> = [T] extends [Partial<U>] ? NoExtraKeys<T, U> : false
export type GameInputGuard = [Check<InMatches<AchievementsSeenIn, S['AchievementsSeenIn']>>]
