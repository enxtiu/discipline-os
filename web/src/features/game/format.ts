/**
 * Display helpers for the gamification screens. Formatting only — every
 * number comes from the server as is (scores, XP, levels, progress, streaks).
 */
import i18n from '@/i18n'
import { number } from '@/lib/format'
import type { Achievement, Level, Rarity, RingKey, Streak, UserQuest } from './types'

/** The UI language decides which of the server's `*_ru` / `*_en` texts is shown. */
function english(): boolean {
  return (i18n.resolvedLanguage ?? i18n.language ?? 'ru').startsWith('en')
}

export function levelTitle(level: Pick<Level, 'title_ru' | 'title_en'>): string {
  return english() ? level.title_en : level.title_ru
}

export function questTitle(quest: Pick<UserQuest, 'title_ru' | 'title_en'>): string {
  return english() ? quest.title_en : quest.title_ru
}

export function questDescription(quest: Pick<UserQuest, 'description_ru' | 'description_en'>): string | null {
  return english() ? quest.description_en : quest.description_ru
}

export function achievementName(item: Pick<Achievement, 'name_ru' | 'name_en' | 'masked'>): string {
  if (item.masked) return i18n.t('game.secret_name')
  return english() ? item.name_en : item.name_ru
}

export function achievementDescription(item: Pick<Achievement, 'description_ru' | 'description_en'>): string | null {
  return english() ? item.description_en : item.description_ru
}

/** 0..1 from the server → "64%". */
export function percent01(value: number): string {
  return `${number(Math.round(Math.max(0, Math.min(1, value)) * 100))}%`
}

/** "1 / 3", "1 250 / 2 640 мл", "30 / 60 мин", "2 / 5 дн." */
export function questProgress(quest: Pick<UserQuest, 'progress' | 'target' | 'unit'>): string {
  const pair = `${number(Math.min(quest.progress, quest.target))} / ${number(quest.target)}`
  switch (quest.unit) {
    case 'ml':
      return `${pair} ${i18n.t('common.ml')}`
    case 'min':
      return `${pair} ${i18n.t('common.min')}`
    case 'days':
      return `${pair} ${i18n.t('game.unit_days')}`
    default:
      return pair
  }
}

/** "5 дн." / "2 нед." */
export function streakLength(streak: Pick<Streak, 'unit'>, count: number): string {
  return i18n.t(streak.unit === 'week' ? 'game.streak.weeks' : 'game.streak.days', { count })
}

/** Achievement progress "3 / 10" (large targets grouped: "4 000 / 10 000"). */
export function achievementProgress(item: Pick<Achievement, 'progress'>): string | null {
  if (!item.progress) return null
  return `${number(Math.min(item.progress.value, item.progress.target))} / ${number(item.progress.target)}`
}

/** Unlock date, short ("12 окт."). */
export function shortDate(value: string | null): string {
  if (!value) return ''
  return new Intl.DateTimeFormat(english() ? 'en-GB' : 'ru-RU', { day: 'numeric', month: 'short' }).format(new Date(value))
}

/** "28 сен." for a `YYYY-MM-DD` day (no timezone shift). */
export function shortDay(day: string): string {
  return new Intl.DateTimeFormat(english() ? 'en-GB' : 'ru-RU', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(
    new Date(`${day}T12:00:00Z`),
  )
}

/** Ring colours (the three small rings and their rows). */
export const RING_TONES: Record<RingKey, 'accent' | 'violet' | 'warn'> = {
  nutrition: 'accent',
  activity: 'violet',
  discipline: 'warn',
}

/**
 * Rarity styles — literal class strings (Tailwind purges anything built at
 * runtime), token colours only, so both themes stay readable.
 */
export const RARITY_STYLES: Record<Rarity, { text: string; border: string; bg: string; pill: string }> = {
  common: {
    text: 'text-[rgb(var(--text))]',
    border: 'border-[rgb(var(--text-muted))]/50',
    bg: 'bg-[rgb(var(--bg-sunken))]',
    pill: 'border-[rgb(var(--border))] text-[rgb(var(--text-muted))]',
  },
  rare: {
    text: 'text-[rgb(var(--accent))]',
    border: 'border-[rgb(var(--accent))]/60',
    bg: 'bg-[rgb(var(--accent))]/10',
    pill: 'border-[rgb(var(--accent))]/40 bg-[rgb(var(--accent))]/10 text-[rgb(var(--accent))]',
  },
  epic: {
    text: 'text-[rgb(var(--accent-2))]',
    border: 'border-[rgb(var(--accent-2))]/60',
    bg: 'bg-[rgb(var(--accent-2))]/10',
    pill: 'border-[rgb(var(--accent-2))]/40 bg-[rgb(var(--accent-2))]/10 text-[rgb(var(--accent-2))]',
  },
  legendary: {
    text: 'text-[rgb(var(--warn))]',
    border: 'border-[rgb(var(--warn))]/60',
    bg: 'bg-[rgb(var(--warn))]/10',
    pill: 'border-[rgb(var(--warn))]/40 bg-[rgb(var(--warn))]/10 text-[rgb(var(--warn))]',
  },
}

export const RARITY_ORDER: Rarity[] = ['common', 'rare', 'epic', 'legendary']
