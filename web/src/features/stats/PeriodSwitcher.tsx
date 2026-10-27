/**
 * The one filter of the dashboard, above everything it scopes: the period
 * kind (Неделя / Месяц / Квартал / Год) and a sticky row to step back and
 * forth. Periods are anchored on the server: «previous» asks for the day
 * before the shown `from`, «next» for the day after `to` — the server decides
 * where that period starts and ends, the client never computes it.
 */
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { IconButton, SegmentedControl, Skeleton } from '@/components/ui'
import { IconChevronLeft, IconChevronRight } from '@/components/ui/icons'
import { useShellInsets } from '@/features/plan/calendar/shell'
import { periodLabel } from './format'
import type { Period, StatsRange } from './types'

export const PERIODS: Period[] = ['week', 'month', 'quarter', 'year']

export function PeriodSwitcher({
  period,
  range,
  stale,
  loading,
  onPeriod,
  onPrev,
  onNext,
  onCurrent,
}: {
  period: Period
  /** the shown range (null until the first answer) */
  range: StatsRange | null
  /** the shown range is the previous request's, the new one is on its way */
  stale: boolean
  loading: boolean
  onPeriod: (period: Period) => void
  onPrev: () => void
  onNext: () => void
  onCurrent: () => void
}) {
  const { t } = useTranslation()
  const insets = useShellInsets()
  const current = range != null && range.from <= range.today && range.today <= range.to
  const future = range != null && range.from > range.today

  return (
    <div className="space-y-2" data-testid="stats-period">
      <SegmentedControl
        value={period}
        onChange={onPeriod}
        options={PERIODS.map((value) => ({ value, label: t(`stats.period.name.${value}`) }))}
      />
      <div
        className="sticky z-20 -mx-4 flex items-center gap-1 border-b border-transparent bg-[rgb(var(--bg))]/90 px-4 py-1 backdrop-blur-xl"
        style={{ top: insets.top }}
      >
        <IconButton
          label={t('stats.period.prev')}
          icon={<IconChevronLeft size={20} />}
          onClick={onPrev}
          disabled={range == null || stale}
          data-testid="stats-prev"
        />
        <div className="min-w-0 flex-1 text-center" aria-live="polite">
          {range == null ? (
            <Skeleton className="mx-auto h-5 w-40" />
          ) : current || future ? (
            <div className="flex min-h-tap flex-col items-center justify-center">
              <p
                className={clsx('truncate text-[15px] font-semibold tnum transition-opacity', loading && 'opacity-60')}
                data-testid="stats-period-label"
                data-from={range.from}
                data-to={range.to}
              >
                {periodLabel(range)}
              </p>
              <p className="text-[11px] muted">{t(`stats.period.current.${period}`)}</p>
            </div>
          ) : (
            <button
              type="button"
              onClick={onCurrent}
              disabled={stale}
              className="flex min-h-tap w-full flex-col items-center justify-center rounded-2xl active:bg-[rgb(var(--border))]/40"
              data-testid="stats-current"
            >
              <span
                className={clsx('truncate text-[15px] font-semibold tnum transition-opacity', loading && 'opacity-60')}
                data-testid="stats-period-label"
                data-from={range.from}
                data-to={range.to}
              >
                {periodLabel(range)}
              </span>
              <span className="text-[11px] font-medium text-[rgb(var(--accent))]">{t(`stats.period.back.${period}`)}</span>
            </button>
          )}
        </div>
        <IconButton
          label={t('stats.period.next')}
          icon={<IconChevronRight size={20} />}
          onClick={onNext}
          disabled={range == null || stale || current || future}
          data-testid="stats-next"
        />
      </div>
    </div>
  )
}
