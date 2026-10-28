/**
 * Calories burned in workouts, last 7 days — its own card, never food budget.
 *
 * Single series: one validated amber step per theme (`--chart-burn`), 4 px
 * rounded tops anchored to the baseline, a direct label on today and on the
 * best day only, and a tap target per day that reveals its value.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Card, Skeleton } from '@/components/ui'
import { IconFlame } from '@/components/ui/icons'
import type { Burned } from '@/api/types'
import { dayLabel, number } from '@/lib/format'
import i18n from '@/i18n'

const HEIGHT = 96

function weekday(day: string): string {
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', { weekday: 'short', timeZone: 'UTC' }).format(
    new Date(`${day}T12:00:00Z`),
  )
}

export function BurnedChart({ data, loading }: { data: Burned | undefined; loading?: boolean }) {
  const { t } = useTranslation()
  const [active, setActive] = useState<string | null>(null)
  const days = data?.days ?? []
  const max = Math.max(1, ...days.map((day) => day.kcal))
  const best = days.reduce<string | null>((top, day) => (day.kcal > 0 && (!top || day.kcal > (days.find((d) => d.date === top)?.kcal ?? 0)) ? day.date : top), null)
  const todayDate = days[days.length - 1]?.date

  return (
    <Card className="space-y-3" data-testid="burned-chart">
      <div className="sk-burn-head flex items-start justify-between gap-3">
        <div className="sk-burn-left min-w-0">
          <p className="sk-burn-title flex items-center gap-1.5 text-sm font-semibold">
            <IconFlame size={16} className="text-amber-400" />
            {t('train.burned_week')}
          </p>
          <p className="sk-burn-note mt-0.5 text-xs muted">{t('train.burned_not_budget')}</p>
        </div>
        <div className="sk-burn-total shrink-0 text-right">
          <p className="tnum text-lg font-semibold">{number(data?.total_kcal ?? 0)}</p>
          <p className="text-xs muted">
            {t('common.kcal')}, {t('train.avg_per_day', { kcal: number(data?.average_kcal ?? 0) })}
          </p>
        </div>
      </div>
      {loading && !data ? (
        <Skeleton className="h-28" />
      ) : (
        <div className="relative">
          {(data?.total_kcal ?? 0) === 0 && (
            <p className="pointer-events-none absolute inset-x-0 top-8 text-center text-sm muted">{t('train.burned_empty')}</p>
          )}
          <ul className="grid grid-cols-7 gap-0.5" role="list" aria-label={t('train.burned_week')}>
            {days.map((day) => {
              const height = day.kcal > 0 ? Math.max(4, Math.round((day.kcal / max) * HEIGHT)) : 0
              const labelled = day.date === todayDate || day.date === best || day.date === active
              return (
                <li key={day.date} className="flex">
                  <button
                    type="button"
                    onClick={() => setActive(active === day.date ? null : day.date)}
                    aria-pressed={active === day.date}
                    className="flex min-h-tap w-full flex-col items-center justify-end gap-1 rounded-lg pt-1"
                  >
                    <span className="sr-only">
                      {t('train.burned_day', { day: dayLabel(day.date, 'long'), kcal: number(day.kcal), n: day.workouts })}
                    </span>
                    <span
                      className={clsx('tnum text-[11px] leading-none', labelled && day.kcal > 0 ? 'muted' : 'invisible')}
                      aria-hidden="true"
                    >
                      {number(day.kcal)}
                    </span>
                    <span className="flex w-full justify-center" style={{ height: HEIGHT }} aria-hidden="true">
                      <span className="mt-auto w-3/5 max-w-[22px] rounded-t-[4px] chart-burn" style={{ height }} />
                    </span>
                    <span className="h-px w-full bg-[rgb(var(--border))]" aria-hidden="true" />
                    <span
                      className={clsx('text-[11px] leading-none', day.date === todayDate ? 'font-semibold' : 'muted')}
                      aria-hidden="true"
                    >
                      {weekday(day.date)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </Card>
  )
}
