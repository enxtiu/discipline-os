/**
 * Gamification endpoints (stage 8). Reads only, plus two small writes:
 * marking celebrated achievements as seen and claiming a quest (both
 * idempotent on the server — a repeated claim returns the same quest, 0 XP).
 */
import { api } from '@/api/client'
import type { AchievementsSeenIn, GameProfile, GameToday, Ok, QuestClaim, ScoreHistory, XpHistory } from './types'

const G = '/v1/gamification'

export const gameApi = {
  /** Score, rings, XP and level, streaks, today's quests and unseen achievements (recomputed on read). */
  today: () => api.get<GameToday>(`${G}/today`),
  /** XP (30 days), the level ladder, streaks, every achievement with progress, totals. */
  profile: () => api.get<GameProfile>(`${G}/profile`),
  /** XP per day with its events, ≤ 366 days. */
  xp: (from: string, to: string) => api.get<XpHistory>(`${G}/xp`, { query: { from, to } }),
  /** Daily scores with their breakdown, ≤ 366 days. */
  scores: (from: string, to: string) => api.get<ScoreHistory>(`${G}/scores`, { query: { from, to } }),
  markSeen: (ids: string[]) => api.post<Ok>(`${G}/achievements/seen`, { ids } satisfies AchievementsSeenIn),
  claimQuest: (userQuestId: string) => api.post<QuestClaim>(`${G}/quests/${userQuestId}/claim`),
}
