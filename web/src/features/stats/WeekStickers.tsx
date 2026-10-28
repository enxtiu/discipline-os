/**
 * Статистика → «Наклейки недели» (stage 10, a theme trait: `SkinTraits.weekStickers`):
 * the achievements unlocked during the current week as a strip of stickers, with
 * empty places to fill (five places; with more, the newest five). Only when there is
 * at least one. Stickers that do not fit one row wrap to the next (never wider than
 * the screen).
 *
 * `GET /gamification/profile` (achievements with `unlocked_at`) and the current week's
 * range from the server (the progress series' `from` / `to`, the request the dashboards
 * already make); an unlock time is placed on a day in the user's timezone from the
 * session — formatting, not a day boundary of the client's own.
 */
import { useTranslation } from 'react-i18next'

import { useAuth } from '@/features/auth/AuthProvider'
import { achievementName } from '@/features/game/format'
import { useGameProfile } from '@/features/game/queries'
import type { AchievementCategory } from '@/features/game/types'
import { useStats } from './queries'

const SLOTS = 5

/** The sticker colour of each achievement category (the theme's category tokens). */
const CATEGORY_TONE: Record<AchievementCategory, string> = {
  nutrition: 'kcal',
  training: 'steps',
  activity: 'steps',
  planner: 'water',
  review: 'score',
  streaks: 'streak',
  levels: 'xp',
  special: 'kcal',
}

/**
 * A name on two lines of about the same length («Первая\nтренировка», «10 000\nшагов»), as
 * the boards set them: the sticker is then as narrow as its longer line. Presentation only —
 * the text stays the same words (a line break is white space to a screen reader).
 */
function twoLines(name: string): string {
  const words = name.split(' ')
  let best = name
  let longest = Infinity
  for (let cut = 1; cut < words.length; cut += 1) {
    const first = words.slice(0, cut).join(' ')
    const second = words.slice(cut).join(' ')
    const length = Math.max(first.length, second.length)
    if (length < longest) {
      longest = length
      best = `${first}\n${second}`
    }
  }
  return best
}

function localDay(iso: string, timeZone: string | undefined): string | null {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso))
  } catch {
    return null
  }
}

export function WeekStickers() {
  const { t } = useTranslation()
  const { session } = useAuth()
  const profile = useGameProfile()
  const week = useStats('progress', 'week', null)
  if (!profile.data || !week.data || week.isPlaceholderData) return null

  const { from, to } = week.data
  const tz = session?.settings.timezone
  const earned = profile.data.achievements
    .filter((item) => {
      if (!item.unlocked || !item.unlocked_at) return false
      const day = localDay(item.unlocked_at, tz)
      return day != null && day >= from && day <= to
    })
    // in the order they were earned (a sticker book fills from the left); the newest five
    .sort((a, b) => (a.unlocked_at ?? '').localeCompare(b.unlocked_at ?? ''))
  if (!earned.length) return null
  const shown = earned.slice(-SLOTS)

  return (
    <section className="space-y-2" aria-labelledby="week-stickers-title" data-testid="week-stickers" data-count={earned.length}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="week-stickers-title" className="text-base font-semibold">
          {t('stats.week_stickers.title')}
        </h2>
        <span className="tnum text-sm muted">{t('stats.week_stickers.count', { count: earned.length })}</span>
      </div>
      <ul className="sk-sticker-book flex items-center gap-2">
        {shown.map((item, index) => (
          <li
            key={item.id}
            className={`sk-sticker sk-badge ${index % 2 ? 'sk-tilt-r' : 'sk-tilt-l'}`}
            data-cat={CATEGORY_TONE[item.category]}
            data-testid="week-sticker"
          >
            {twoLines(achievementName(item))}
          </li>
        ))}
        {Array.from({ length: SLOTS - shown.length }).map((_, index) => (
          <li key={`empty-${index}`} aria-hidden="true" className="sk-sticker-slot" />
        ))}
      </ul>
    </section>
  )
}
