/**
 * Статистика → the level plaque at the top (stage 10, every theme): the level and its
 * title, a big XP bar to the next level («осталось 350 XP»), XP in total, this week
 * and today, and the overall streak (no freezes: a missed day ends it).
 *
 * `GET /gamification/profile` (the ladder names the next level; the same read as the
 * achievements screen) and the current week's progress series (the request the
 * dashboards already make for the current week). Every number is the server's — the
 * week's XP is the sum of its days, as the «Прогресс» card counts it. XP comes from
 * logging and consistency, never from eating less.
 *
 * `sk-*` and `data-cat` are hooks for the themes (styles/skins/*.css).
 */
import { useTranslation } from 'react-i18next'

import { Card, ProgressBar, Skeleton } from '@/components/ui'
import { ProgressRing } from '@/components/ui/ProgressRing'
import { number } from '@/lib/format'
import { levelTitle, streakLength } from '@/features/game/format'
import { IconFlameLit } from '@/features/game/icons'
import { useGameProfile } from '@/features/game/queries'
import { progressTotals } from './derive'
import { CardError } from './parts'
import { useStats } from './queries'

export function LevelPlaque() {
  const { t } = useTranslation()
  const profile = useGameProfile()
  const week = useStats('progress', 'week', null)

  if (!profile.data) {
    if (profile.isError) return <CardError onRetry={() => void profile.refetch()} testId="level-plaque-error" />
    return (
      <Card className="space-y-3" aria-busy="true" data-testid="level-plaque-loading">
        <div className="flex items-center gap-4">
          <Skeleton className="h-[76px] w-[76px] rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
        <Skeleton className="h-3 w-full" />
        <div className="grid grid-cols-3 gap-2">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      </Card>
    )
  }

  const { xp, levels, streaks } = profile.data
  const { level } = xp
  const next = levels.find((row) => row.level === level.level + 1) ?? null
  const overall = streaks.find((streak) => streak.kind === 'overall')
  const weekXp = week.data && !week.isPlaceholderData ? progressTotals(week.data).xp : null
  const span = level.next_min_xp != null ? level.next_min_xp - level.min_xp : null
  const into = xp.total - level.min_xp
  const left = level.next_min_xp != null ? level.next_min_xp - xp.total : null

  return (
    <Card
      className="sk-tile sk-plaque space-y-3"
      data-cat="xp"
      aria-labelledby="level-plaque-title"
      data-testid="level-plaque"
      data-level={level.level}
      data-total={xp.total}
      role="region"
    >
      <div className="flex items-center gap-4">
        {/* the heading says «Уровень 12 · Профи»; the medal repeats it for the eye */}
        <span aria-hidden="true" className="shrink-0">
          <ProgressRing
            value={level.progress}
            max={1}
            size={76}
            thickness={7}
            tone="xp"
            className="sk-medal sk-tilt-l [&_svg]:overflow-visible"
            label={<span className="text-[1.75rem]">{level.level}</span>}
            sublabel={t('game.page.level_word')}
          />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="level-plaque-title" className="truncate text-xl font-semibold leading-tight">
            <span className="sr-only">{t('stats.plaque.level_sr', { level: level.level })} </span>
            {levelTitle(level)}
          </h2>
          <p className="tnum mt-0.5 text-sm font-semibold" data-testid="plaque-total">
            {t('game.page.total_xp', { xp: number(xp.total) })}
          </p>
        </div>
      </div>

      <div className="space-y-1.5">
        <ProgressBar
          value={level.progress}
          max={1}
          tone="xp"
          className="sk-bar-big h-3"
          label={next ? t('stats.plaque.bar_label', { level: next.level }) : t('stats.plaque.bar_label_top')}
          valueText={span != null ? t('stats.plaque.bar_value', { value: number(into), max: number(span) }) : undefined}
        />
        <div className="tnum flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs">
          {next && span != null && left != null ? (
            <>
              <span className="muted">
                {t('stats.plaque.to_next', { value: number(into), max: number(span), level: next.level, title: levelTitle(next) })}
              </span>
              <span className="font-semibold" data-testid="plaque-xp-left" data-xp={left}>
                {t('stats.plaque.left', { xp: number(left) })}
              </span>
            </>
          ) : (
            <span className="muted">{t('game.xp_max', { total: number(xp.total) })}</span>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-3 gap-2">
        <div className="sk-cell surface-sunken min-w-0 px-2.5 py-2" data-testid="plaque-week" data-xp={weekXp ?? undefined}>
          <dt className="truncate text-xs muted">{t('stats.plaque.week')}</dt>
          <dd className="tnum mt-0.5 truncate text-[17px] font-semibold leading-tight">
            {weekXp != null ? t('stats.plaque.plus_xp', { xp: number(weekXp) }) : '—'}
          </dd>
        </div>
        <div className="sk-cell surface-sunken min-w-0 px-2.5 py-2" data-testid="plaque-today" data-xp={xp.today}>
          <dt className="truncate text-xs muted">{t('stats.plaque.today')}</dt>
          <dd className="tnum mt-0.5 truncate text-[17px] font-semibold leading-tight">
            {t('stats.plaque.plus_xp', { xp: number(xp.today) })}
          </dd>
        </div>
        <div
          className="sk-cell surface-sunken min-w-0 px-2.5 py-2"
          data-testid="plaque-streak"
          data-current={overall?.current ?? 0}
        >
          <dt className="truncate text-xs muted">{t('game.status.streak')}</dt>
          <dd className="mt-0.5 flex min-w-0 items-center gap-1 leading-tight">
            <IconFlameLit lit={!!overall?.today_met} size={15} className="shrink-0 text-[rgb(var(--cat-streak))]" />
            <span className="tnum truncate text-[17px] font-semibold">
              {overall ? streakLength(overall, overall.current) : t('game.streak.days', { count: 0 })}
            </span>
          </dd>
        </div>
      </dl>
    </Card>
  )
}
