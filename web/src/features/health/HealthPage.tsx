/**
 * /health — steps (14 days), weight (90 days) and sleep (14 nights).
 *
 * Every number is computed by the server (days in the user's timezone,
 * averages, the step-kcal estimate); the charts only draw them.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import clsx from 'clsx'

import { Button, Card, Skeleton } from '@/components/ui'
import { IconChevronLeft, IconFootprints, IconRefresh } from '@/components/ui/icons'
import type { SleepEntry, StepsSeries, WeightSeries } from '@/api/types'
import { useAuth } from '@/features/auth/AuthProvider'
import { dayLabel, number, relativeTime, timeShort } from '@/lib/format'
import { kg, signedKg, sleepClock, sleepDuration } from './format'
import i18n from '@/i18n'
import { SleepSheet, StepsSheet, WeightSheet } from './sheets'
import {
  useHealthQueueRefresh,
  useHealthToday,
  useSleepSeries,
  useStepsSeries,
  useWeightSeries,
} from './useHealth'

export function HealthPage() {
  const { t } = useTranslation()
  const { session } = useAuth()
  const tz = session?.settings.timezone
  const today = useHealthToday()
  const steps = useStepsSeries(14)
  const weight = useWeightSeries(90)
  const sleep = useSleepSeries(14)
  const [sheet, setSheet] = useState<'steps' | 'weight' | 'sleep' | null>(null)
  useHealthQueueRefresh()
  const lastSync = today.data?.last_sync_at

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1">
        <Link to="/today" className="-ml-2 flex h-12 w-12 items-center justify-center" aria-label={t('app.back')}>
          <IconChevronLeft size={22} />
        </Link>
        <h1 className="h1">{t('health.title')}</h1>
      </div>

      <Link
        to="/settings/shortcut"
        className="surface-sunken flex min-h-tap items-center gap-3 rounded-2xl px-4 py-2.5 text-sm"
        data-testid="health-sync"
      >
        <IconRefresh size={18} className={lastSync ? 'text-[rgb(var(--accent))]' : 'muted'} />
        <span className="min-w-0 flex-1">
          {lastSync ? t('health.last_sync', { time: relativeTime(lastSync) }) : t('health.never_synced')}
        </span>
        <span className="shrink-0 text-xs text-[rgb(var(--accent))]">
          {lastSync ? t('health.shortcut_settings') : t('health.setup_shortcut')}
        </span>
      </Link>

      <StepsChart data={steps.data} loading={steps.isLoading} onEnter={() => setSheet('steps')} />
      <WeightChart data={weight.data} loading={weight.isLoading} onEnter={() => setSheet('weight')} />
      <SleepChart
        items={sleep.data?.items}
        average={sleep.data?.average_min ?? null}
        loading={sleep.isLoading}
        tz={tz}
        onEnter={() => setSheet('sleep')}
      />

      <StepsSheet open={sheet === 'steps'} onClose={() => setSheet(null)} />
      <WeightSheet open={sheet === 'weight'} onClose={() => setSheet(null)} />
      <SleepSheet open={sheet === 'sleep'} onClose={() => setSheet(null)} />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Steps: 14 bars against the target
// --------------------------------------------------------------------------- //
const BAR_HEIGHT = 96

function StepsChart({ data, loading, onEnter }: { data?: StepsSeries; loading: boolean; onEnter: () => void }) {
  const { t } = useTranslation()
  const [active, setActive] = useState<string | null>(null)
  const days = data?.days ?? []
  const target = data?.target_steps ?? 0
  const max = Math.max(1, target, ...days.map((day) => day.steps))
  const todayDate = days[days.length - 1]?.date
  const targetTop = target > 0 ? BAR_HEIGHT - Math.round((target / max) * BAR_HEIGHT) : null

  return (
    <Card className="space-y-3" data-testid="steps-chart">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-semibold">
            <IconFootprints size={16} className="text-violet-300" />
            {t('health.steps_chart')}
          </p>
          <p className="mt-0.5 text-xs muted">
            {data ? t('health.steps_on_target', { n: data.days_on_target, total: days.length }) : ' '}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tnum text-lg font-semibold">{number(data?.average_steps ?? 0)}</p>
          <p className="text-xs muted">{t('health.average_per_day')}</p>
        </div>
      </div>
      {loading && !data ? (
        <Skeleton className="h-32" />
      ) : (
        <div className="relative -mx-2">
          {targetTop !== null && (
            <div
              className="pointer-events-none absolute inset-x-0 border-t border-dashed border-[rgb(var(--text-muted))]/50"
              style={{ top: 16 + targetTop }}
              aria-hidden="true"
            />
          )}
          <div className="grid grid-cols-[repeat(14,minmax(0,1fr))]" role="list" aria-label={t('health.steps_chart')}>
            {days.map((day) => {
              const height = day.steps > 0 ? Math.max(3, Math.round((day.steps / max) * BAR_HEIGHT)) : 0
              const labelled = day.date === active || (active === null && day.date === todayDate)
              return (
                <div key={day.date} role="listitem">
                  <button
                    type="button"
                    onClick={() => setActive(active === day.date ? null : day.date)}
                    aria-pressed={active === day.date}
                    className="relative flex min-h-tap w-full flex-col items-center justify-end gap-1"
                  >
                    <span className="sr-only">
                      {t('health.steps_day', { day: dayLabel(day.date, 'long'), steps: number(day.steps) })}
                    </span>
                    <span
                      aria-hidden="true"
                      className={clsx(
                        'tnum absolute top-0 whitespace-nowrap text-[11px] leading-none',
                        labelled && day.steps > 0 ? 'muted' : 'invisible',
                        day.date === todayDate && 'right-0',
                      )}
                    >
                      {number(day.steps)}
                    </span>
                    <span className="mt-4 flex w-full justify-center" style={{ height: BAR_HEIGHT }} aria-hidden="true">
                      <span
                        className={clsx(
                          'mt-auto w-3/5 max-w-[14px] rounded-t-[3px] chart-steps',
                          !day.on_target && 'opacity-60',
                        )}
                        style={{ height }}
                      />
                    </span>
                    <span className="h-px w-full bg-[rgb(var(--border))]" aria-hidden="true" />
                    <span
                      className={clsx('tnum text-[10px] leading-none', day.date === todayDate ? 'font-semibold' : 'muted')}
                      aria-hidden="true"
                    >
                      {Number(day.date.slice(8))}
                    </span>
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs muted">{t('health.steps_legend', { target: number(target) })}</p>
        <Button size="sm" variant="secondary" className="min-h-tap" onClick={onEnter}>
          {t('health.steps_enter')}
        </Button>
      </div>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Weight: weigh-ins over 90 days
// --------------------------------------------------------------------------- //
const W = 320
const H = 120
const PAD_Y = 12

function dayNumber(day: string): number {
  return Math.round(Date.parse(`${day}T00:00:00Z`) / 86_400_000)
}

function WeightChart({ data, loading, onEnter }: { data?: WeightSeries; loading: boolean; onEnter: () => void }) {
  const { t } = useTranslation()
  const items = (data?.items ?? []).filter((row) => row.weight_kg != null)
  const values = items.map((row) => row.weight_kg as number)
  const low = Math.floor(Math.min(...values, Infinity) - 0.5)
  const high = Math.ceil(Math.max(...values, -Infinity) + 0.5)
  const first = items.length ? dayNumber(items[0].date) : 0
  const last = items.length ? dayNumber(items[items.length - 1].date) : 0
  const span = Math.max(1, last - first)
  const x = (day: string) => (items.length === 1 ? W / 2 : ((dayNumber(day) - first) / span) * (W - 8) + 4)
  const y = (value: number) => PAD_Y + (1 - (value - low) / Math.max(0.1, high - low)) * (H - 2 * PAD_Y)
  const path = items.map((row, index) => `${index ? 'L' : 'M'}${x(row.date).toFixed(1)},${y(row.weight_kg as number).toFixed(1)}`).join(' ')
  const latest = data?.latest

  return (
    <Card className="space-y-3" data-testid="weight-chart">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">{t('health.weight_chart')}</p>
          <p className="mt-0.5 text-xs muted">
            {data?.trend_kg != null ? t('health.weight_trend', { kg: kg(data.trend_kg) }) : t('health.weight_chart_hint')}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tnum text-lg font-semibold" data-testid="weight-latest">
            {kg(latest?.weight_kg)} <span className="text-xs font-normal muted">{t('common.kg')}</span>
          </p>
          <p className="tnum text-xs muted">
            {data?.change_30d_kg != null ? t('health.weight_change_30d', { kg: signedKg(data.change_30d_kg) }) : ' '}
          </p>
        </div>
      </div>
      {loading && !data ? (
        <Skeleton className="h-32" />
      ) : items.length === 0 ? (
        <p className="py-6 text-center text-sm muted">{t('health.weight_empty')}</p>
      ) : (
        <div className="flex gap-2">
          <div className="tnum flex flex-col justify-between py-1 text-[10px] leading-none muted" aria-hidden="true">
            <span>{high}</span>
            <span>{low}</span>
          </div>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="h-auto w-full text-[rgb(var(--accent))]"
            role="img"
            aria-label={t('health.weight_chart_label', {
              n: items.length,
              from: kg(values[0]),
              to: kg(values[values.length - 1]),
            })}
          >
            <line x1="0" x2={W} y1={H - 1} y2={H - 1} className="stroke-[rgb(var(--border))]" strokeWidth="1" />
            {items.length > 1 && (
              <path d={path} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            )}
            {items.map((row, index) => (
              <circle
                key={row.date}
                cx={x(row.date)}
                cy={y(row.weight_kg as number)}
                r={index === items.length - 1 ? 4.5 : 2.5}
                fill="currentColor"
                className={index === items.length - 1 ? undefined : 'opacity-70'}
              />
            ))}
          </svg>
        </div>
      )}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs muted">
          {items.length > 1
            ? t('health.range', { from: dayLabel(items[0].date), to: dayLabel(items[items.length - 1].date) })
            : items.length === 1
              ? dayLabel(items[0].date)
              : ''}
        </p>
        <Button size="sm" variant="secondary" className="min-h-tap" onClick={onEnter}>
          {t('health.weight_enter')}
        </Button>
      </div>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Sleep: each night on a 20:00 → 12:00 timeline
// --------------------------------------------------------------------------- //
const WINDOW_START = 20 * 60 // 20:00
const WINDOW = 16 * 60 // until 12:00
const TICKS = ['22:00', '02:00', '06:00', '10:00']

function shortDay(day: string): string {
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', {
    weekday: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${day}T12:00:00Z`))
}

/** Minutes from 20:00 on the timeline: 12:00–19:59 bedtimes are negative (before the window). */
function offset(clock: string): number {
  const [hh, mm] = clock.split(':').map(Number)
  const minutes = (hh * 60 + mm - WINDOW_START + 24 * 60) % (24 * 60)
  return minutes >= WINDOW ? minutes - 24 * 60 : minutes
}

/** The visible part of a night on the 20:00 → 12:00 window, at least a sliver wide. */
function span(bed: string, duration: number): { left: number; width: number } {
  const start = offset(bed)
  const left = Math.min(Math.max(0, start), WINDOW - 4)
  const right = Math.min(WINDOW, start + duration)
  return { left, width: Math.max(4, right - left) }
}

function SleepChart({
  items,
  average,
  loading,
  tz,
  onEnter,
}: {
  items?: SleepEntry[]
  average: number | null
  loading: boolean
  tz?: string
  onEnter: () => void
}) {
  const { t } = useTranslation()
  const nights = items ?? []
  return (
    <Card className="space-y-3" data-testid="sleep-chart">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">{t('health.sleep_chart')}</p>
          <p className="mt-0.5 text-xs muted">{t('health.sleep_chart_hint')}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tnum text-lg font-semibold">{sleepDuration(average)}</p>
          <p className="text-xs muted">{t('health.average_per_night')}</p>
        </div>
      </div>
      {loading && !items ? (
        <Skeleton className="h-32" />
      ) : nights.length === 0 ? (
        <p className="py-6 text-center text-sm muted">{t('health.sleep_empty')}</p>
      ) : (
        <div className="space-y-1.5" role="list" aria-label={t('health.sleep_chart')}>
          <div className="relative ml-14 mr-12 h-3 text-[10px] leading-none muted" aria-hidden="true">
            {TICKS.map((tick) => (
              <span
                key={tick}
                className="tnum absolute -translate-x-1/2"
                style={{ left: `${(offset(tick) / WINDOW) * 100}%` }}
              >
                {tick.slice(0, 2)}
              </span>
            ))}
          </div>
          {nights.map((night) => {
            const bed = timeShort(night.bed_at, tz)
            const wake = timeShort(night.wake_at, tz)
            const { left, width } = span(bed, night.duration_min)
            return (
              <div key={night.date} role="listitem" className="flex items-center gap-2 text-xs">
                <span className="sr-only">
                  {t('health.sleep_night', {
                    day: dayLabel(night.date, 'long'),
                    bed,
                    wake,
                    duration: sleepDuration(night.duration_min),
                  })}
                </span>
                <span className="w-12 shrink-0 muted" aria-hidden="true">
                  {shortDay(night.date)}
                </span>
                <span className="relative h-3 flex-1 rounded-full bg-[rgb(var(--bg-sunken))]" aria-hidden="true">
                  <span
                    className="absolute inset-y-0 rounded-full chart-sleep"
                    style={{ left: `${(left / WINDOW) * 100}%`, width: `${(Math.min(width, WINDOW - left) / WINDOW) * 100}%` }}
                  />
                </span>
                <span className="tnum w-10 shrink-0 text-right" aria-hidden="true">
                  {sleepClock(night.duration_min)}
                </span>
              </div>
            )
          })}
        </div>
      )}
      <div className="flex justify-end">
        <Button size="sm" variant="secondary" className="min-h-tap" onClick={onEnter}>
          {t('health.sleep_enter')}
        </Button>
      </div>
    </Card>
  )
}
