/** Today: the latest weigh-in and last night's sleep — one tap to log either. */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Card, Skeleton } from '@/components/ui'
import { IconChevronRight } from '@/components/ui/icons'
import type { HealthToday } from '@/api/types'
import { timeShort } from '@/lib/format'
import { kg, signedKg, sleepDuration } from './format'

export function HealthTodayCard({
  data,
  loading,
  tz,
  onWeight,
  onSleep,
}: {
  data: HealthToday | undefined
  loading: boolean
  tz?: string
  onWeight: () => void
  onSleep: () => void
}) {
  const { t } = useTranslation()
  const weight = data?.weight
  const sleep = data?.sleep
  const change = signedKg(data?.weight_change_7d_kg)

  return (
    <Card className="space-y-3" data-testid="health-today">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold">{t('health.card_title')}</p>
        <Link to="/health" className="-my-3 -mr-2 flex min-h-tap items-center gap-0.5 px-2 text-xs muted">
          {t('health.open_all')}
          <IconChevronRight size={14} />
        </Link>
      </div>
      {loading && !data ? (
        <Skeleton className="h-[76px]" />
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={onWeight}
            className="surface-sunken flex min-h-tap flex-col items-start rounded-2xl px-3 py-2.5 text-left"
            data-testid="health-weight-tile"
          >
            <span className="text-xs muted">{t('health.weight_title')}</span>
            {weight?.weight_kg != null ? (
              <>
                <span className="tnum text-lg font-semibold leading-tight">
                  {kg(weight.weight_kg)}
                  <span className="ml-1 text-xs font-normal muted">{t('common.kg')}</span>
                </span>
                <span className="tnum text-xs muted">
                  {change ? t('health.weight_change_7d', { kg: change }) : t('health.weight_add_again')}
                </span>
              </>
            ) : (
              <span className="mt-1 text-sm text-[rgb(var(--accent))]">{t('health.weight_none')}</span>
            )}
          </button>
          <button
            type="button"
            onClick={onSleep}
            className="surface-sunken flex min-h-tap flex-col items-start rounded-2xl px-3 py-2.5 text-left"
            data-testid="health-sleep-tile"
          >
            <span className="text-xs muted">{t('health.sleep_last_night')}</span>
            {sleep ? (
              <>
                <span className="tnum text-lg font-semibold leading-tight">{sleepDuration(sleep.duration_min)}</span>
                <span className="tnum text-xs muted">
                  {timeShort(sleep.bed_at, tz)}–{timeShort(sleep.wake_at, tz)}
                </span>
              </>
            ) : (
              <span className="mt-1 text-sm text-[rgb(var(--accent))]">{t('health.sleep_none')}</span>
            )}
          </button>
        </div>
      )}
    </Card>
  )
}
