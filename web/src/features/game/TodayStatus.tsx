/**
 * Today → the status of the day (stage 10, every theme): the day score, the
 * overall streak (no freezes: a missed day ends it, and the tile says so), and
 * the level («Ур. 12 · Профи») with its XP bar to the next level.
 *
 * `GET /gamification/today` as the server computed it — the same read as the
 * score card further down, so no extra request; while it loads the tiles keep
 * their height, and on an error the status steps aside (the score card shows the
 * error with «Повторить»). Static on purpose: the score card opens the breakdown,
 * the streaks and the achievements.
 *
 * The `sk-*` classes and `data-cat` are hooks for the themes (styles/skins/*.css);
 * «Эволюция» draws these as plain cards.
 */
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'

import { ProgressBar, Skeleton } from '@/components/ui'
import { number } from '@/lib/format'
import { levelTitle, streakLength } from './format'
import { IconFlameLit } from './icons'
import { ProvisionalPill } from './parts'
import { useGameToday } from './queries'

export function TodayStatus() {
  const { t } = useTranslation()
  const query = useGameToday()
  const data = query.data

  if (!data) {
    if (query.isError) return null
    return (
      <div className="grid grid-cols-2 gap-3" aria-hidden="true" data-testid="today-status-loading">
        <Skeleton className="h-[5.5rem] rounded-card" />
        <Skeleton className="h-[5.5rem] rounded-card" />
        <Skeleton className="col-span-2 h-[5.25rem] rounded-card" />
      </div>
    )
  }

  const { score, xp } = data
  const { level } = xp
  const overall = data.streaks.find((streak) => streak.kind === 'overall')
  const days = overall?.current ?? 0

  return (
    <section className="sk-status grid grid-cols-2 gap-3" aria-label={t('game.status.title')} data-testid="today-status">
      <div className="card sk-tile min-w-0 px-3.5 py-3" data-cat="score" data-testid="status-score" data-value={score.value}>
        <p className="truncate text-xs font-medium muted">{t('game.score_title')}</p>
        <p className="mt-0.5 flex items-baseline gap-1.5">
          <span className="sk-badge sk-tilt-l stat-value text-[1.75rem] leading-none">{number(Math.round(score.value))}</span>
          <span className="text-xs muted">{t('game.score_of')}</span>
        </p>
        {score.provisional && <ProvisionalPill className="mt-1.5" />}
      </div>

      <div
        className="card sk-tile min-w-0 px-3.5 py-3"
        data-cat="streak"
        data-testid="status-streak"
        data-current={days}
      >
        <p className="truncate text-xs font-medium muted">{t('game.status.streak')}</p>
        <p className="mt-0.5 flex items-center gap-1.5">
          <IconFlameLit
            lit={!!overall?.today_met}
            size={20}
            className="shrink-0 text-[rgb(var(--cat-streak))]"
          />
          <span className="sk-badge sk-tilt-r stat-value text-[1.75rem] leading-none">
            {overall ? streakLength(overall, days) : t('game.streak.days', { count: 0 })}
          </span>
        </p>
        <p className="mt-1.5 truncate text-[11px] font-medium muted" data-testid="status-streak-note">
          {t('game.status.reset_note')}
        </p>
      </div>

      <div
        className="card sk-tile col-span-2 min-w-0 space-y-2 px-3.5 py-3"
        data-cat="xp"
        data-testid="status-level"
        data-level={level.level}
        data-total={xp.total}
      >
        <div className="flex items-center gap-2">
          <span
            className={clsx(
              'sk-badge sk-level-badge tnum shrink-0 rounded-lg px-2 py-0.5 text-xs font-semibold',
              'bg-[rgb(var(--accent-2))]/15 text-[rgb(var(--accent-2))]',
            )}
          >
            {t('game.level_short', { level: level.level })}
          </span>
          <span className="min-w-0 flex-1 truncate font-semibold" data-testid="status-level-title">
            {levelTitle(level)}
          </span>
          <span className="tnum shrink-0 text-sm font-semibold">{t('game.status.xp_total', { xp: number(xp.total) })}</span>
        </div>
        <ProgressBar value={level.progress} max={1} tone="xp" />
        <div className="tnum flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs">
          <span className="muted" data-testid="status-xp-next">
            {level.next_min_xp != null
              ? t('game.xp_to_next', { xp: number(level.next_min_xp - xp.total), level: level.level + 1 })
              : t('game.xp_max', { total: number(xp.total) })}
          </span>
          <span className="font-semibold">{t('game.xp_today', { xp: number(xp.today) })}</span>
        </div>
      </div>
    </section>
  )
}
