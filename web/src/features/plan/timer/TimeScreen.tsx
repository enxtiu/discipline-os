/**
 * /plan/time — tracked time for a week or a month (`?period=week|month&date=`).
 *
 * The server decides the period (`week` follows `settings.week_start`) and
 * every total; prev/next step one day past the period it returned, so the
 * client never computes a week. Summary: total, by day, by area against the
 * area's target, by project. Below: the running entry pinned (in the period
 * it started in), then the entries of the period grouped by their local day,
 * tap to edit; «Добавить вручную» opens the manual entry sheet.
 */
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, Card, EmptyState, ErrorState, IconButton, Row, Rows, SegmentedControl, SkeletonCard } from '@/components/ui'
import { IconChevronLeft, IconChevronRight, IconPlus } from '@/components/ui/icons'
import { addDays } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { dayNumber, dayTitle, monthTitle, rangeTitle, readDayKey, weekdayShort } from '../calendar/dates'
import { formatDuration, formatMinutes, relativeDay } from '../format'
import { IconTimer } from '../icons'
import { useCounts, useElapsed, useTimeEntries, useTimer, useTimeSummary } from '../queries'
import type { Ref, RunningTimer, TimeEntry, TimeSummary } from '../types'
import { describeEntry, entryMinutes, entryTimes } from './entry'
import { TimeEntrySheet } from './TimeEntrySheet'

type Period = 'week' | 'month'

export default function TimeScreen() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const period: Period = params.get('period') === 'month' ? 'month' : 'week'
  const date = readDayKey(params.get('date')) ?? undefined
  const today = useCounts().data?.date
  const summary = useTimeSummary(period, date)
  const from = summary.data?.from ?? ''
  const to = summary.data?.to ?? ''
  const entries = useTimeEntries(from, to)
  const timer = useTimer()
  const running = timer.data?.running ?? null
  const [sheet, setSheet] = useState<{ entry: TimeEntry | null } | null>(null)
  const [sheetUsed, setSheetUsed] = useState(false)

  const go = (next: { period?: Period; date?: string | null }) => {
    const query = new URLSearchParams(params)
    if (next.period) query.set('period', next.period)
    if (next.date !== undefined) {
      if (next.date) query.set('date', next.date)
      else query.delete('date')
    }
    setParams(query, { replace: true })
  }

  const openSheet = (entry: TimeEntry | null) => {
    setSheetUsed(true)
    setSheet({ entry })
  }

  const groups = useMemo(() => {
    const items = (entries.data?.items ?? []).filter((entry) => !entry.running)
    const byDay = new Map<string, TimeEntry[]>()
    for (const entry of items) byDay.set(entry.date, [...(byDay.get(entry.date) ?? []), entry])
    const totals = new Map((entries.data?.by_day ?? []).map((day) => [day.date, day.minutes]))
    return [...byDay.entries()]
      .sort(([a], [b]) => b.localeCompare(a))
      .map(([day, list]) => ({
        day,
        minutes: totals.get(day) ?? list.reduce((sum, entry) => sum + entryMinutes(entry), 0),
        entries: [...list].sort((a, b) => b.started_at.localeCompare(a.started_at)),
      }))
  }, [entries.data])

  const isCurrent = !!today && !!summary.data && summary.data.from <= today && today <= summary.data.to
  const title = summary.data ? (period === 'month' ? monthTitle(summary.data.from) : rangeTitle(summary.data.from, summary.data.to)) : ''
  // pinned only in the period it belongs to (its start day; a start still queued offline has none yet: today)
  const runningDay = running ? running.entry.date || today : undefined
  const pinned = running && !!runningDay && !!summary.data && summary.data.from <= runningDay && runningDay <= summary.data.to ? running : null

  return (
    <div className="space-y-4" data-testid="time-page">
      <SegmentedControl
        value={period}
        onChange={(next) => go({ period: next })}
        options={[
          { value: 'week', label: t('timer.period_week') },
          { value: 'month', label: t('timer.period_month') },
        ]}
      />
      <div className="flex items-center gap-1">
        <IconButton
          label={t(period === 'week' ? 'timer.prev_week' : 'timer.prev_month')}
          icon={<IconChevronLeft size={22} />}
          disabled={!summary.data}
          onClick={() => go({ date: addDays(from, -1) })}
        />
        <h2 className="min-w-0 flex-1 truncate text-center text-[15px] font-semibold" data-testid="time-title">
          {title}
        </h2>
        <IconButton
          label={t(period === 'week' ? 'timer.next_week' : 'timer.next_month')}
          icon={<IconChevronRight size={22} />}
          disabled={!summary.data || isCurrent || (!!today && to >= today)}
          onClick={() => go({ date: addDays(to, 1) })}
        />
        <Button variant="secondary" size="sm" className="min-h-tap shrink-0" disabled={!date || isCurrent} onClick={() => go({ date: null })}>
          {t('timer.this_period')}
        </Button>
      </div>

      {summary.isError && !summary.data ? (
        <ErrorState message={errorMessage(summary.error)} onRetry={() => void summary.refetch()} retryLabel={t('app.retry')} />
      ) : !summary.data ? (
        <>
          <SkeletonCard lines={4} />
          <SkeletonCard lines={3} />
        </>
      ) : (
        <SummaryCards summary={summary.data} today={today} />
      )}

      <section className="space-y-3">
        <header className="flex items-center justify-between gap-3">
          <h2 className="h2">{t('timer.entries')}</h2>
          <Button
            variant="secondary"
            size="sm"
            className="min-h-tap"
            icon={<IconPlus size={18} />}
            onClick={() => openSheet(null)}
            data-testid="time-add-manual"
          >
            {t('timer.add_manual')}
          </Button>
        </header>

        {pinned && <RunningEntry running={pinned} receivedAt={timer.dataUpdatedAt} />}

        {entries.isError && !entries.data ? (
          <ErrorState message={errorMessage(entries.error)} onRetry={() => void entries.refetch()} retryLabel={t('app.retry')} />
        ) : !entries.data ? (
          <SkeletonCard lines={3} />
        ) : groups.length === 0 ? (
          <EmptyState icon={<IconTimer size={28} />} title={t('timer.entries_empty')} description={t('timer.entries_empty_hint')} />
        ) : (
          groups.map((group) => (
            <div key={group.day} className="space-y-1.5" data-testid="time-day" data-date={group.day}>
              <div className="flex items-baseline justify-between gap-3 px-1">
                <span className="label">{relativeDay(group.day, today)}</span>
                <span className="tnum text-xs muted">{formatMinutes(group.minutes)}</span>
              </div>
              <Rows>
                {group.entries.map((entry) => {
                  const { title: entryTitle, subtitle } = describeEntry(entry)
                  return (
                    <Row
                      key={entry.id}
                      onClick={() => openSheet(entry)}
                      label={entryTitle}
                      hint={[entryTimes(entry), subtitle].filter(Boolean).join(' · ')}
                      value={formatMinutes(entryMinutes(entry))}
                      className="min-h-tap"
                    />
                  )
                })}
              </Rows>
            </div>
          ))
        )}
      </section>

      {sheetUsed && (
        <TimeEntrySheet open={sheet !== null} onClose={() => setSheet(null)} entry={sheet?.entry ?? null} today={today} />
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// The running entry, pinned on top of the log
// --------------------------------------------------------------------------- //
function RunningEntry({ running, receivedAt }: { running: RunningTimer; receivedAt: number }) {
  const { t } = useTranslation()
  const elapsed = useElapsed(running, receivedAt)
  const { title, subtitle } = describeEntry(running.entry)
  return (
    <Card className="glow-accent flex items-center gap-3" data-testid="time-running">
      <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden="true">
        <span className="absolute inset-0 animate-pulse-soft rounded-full bg-[rgb(var(--accent))]" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-medium">{title}</p>
        <p className="truncate text-xs muted">
          {running.entry.start ? t('timer.running_since', { time: running.entry.start }) : t('timer.running')}
          {subtitle ? ` · ${subtitle}` : ''}
        </p>
      </div>
      <span className="tnum shrink-0 text-lg font-semibold text-[rgb(var(--accent))]">{formatDuration(elapsed)}</span>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Summary: total + by day, by area vs target, by project
// --------------------------------------------------------------------------- //
const BAR_PX = 88

function Dot({ color }: { color: string | null | undefined }) {
  return (
    <span
      className={clsx('block h-2.5 w-2.5 shrink-0 rounded-full', !color && 'bg-[rgb(var(--border))]')}
      style={color ? { backgroundColor: color } : undefined}
      aria-hidden="true"
    />
  )
}

function SummaryCards({ summary, today }: { summary: TimeSummary; today: string | undefined }) {
  const { t } = useTranslation()
  const [active, setActive] = useState<string | null>(null)
  const days = summary.by_day
  const max = Math.max(60, ...days.map((day) => day.minutes))
  const focus = days.find((day) => day.date === active) ?? days.find((day) => day.date === today) ?? null
  const areaMax = Math.max(1, ...summary.by_area.map((row) => row.minutes))
  const projectMax = Math.max(1, ...summary.by_project.map((row) => row.minutes))
  const week = summary.period === 'week'

  return (
    <>
      <Card className="space-y-3" data-testid="time-summary">
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="label">{week ? t('timer.total_week') : t('timer.total_month')}</p>
            <p className="stat-value" data-testid="time-total">
              {formatMinutes(summary.total_minutes)}
            </p>
          </div>
          {focus && (
            <p className="tnum shrink-0 text-right text-xs muted" aria-live="polite">
              {t('timer.day_value', { day: relativeDay(focus.date, today), duration: formatMinutes(focus.minutes) })}
            </p>
          )}
        </div>
        <ul
          className="grid gap-0.5"
          style={{ gridTemplateColumns: `repeat(${Math.max(1, days.length)}, minmax(0, 1fr))` }}
          aria-label={t('timer.by_day')}
        >
          {days.map((day) => {
            const height = day.minutes > 0 ? Math.max(3, Math.round((day.minutes / max) * BAR_PX)) : 0
            const n = dayNumber(day.date)
            const label = week ? weekdayShort(day.date) : n === 1 || n % 5 === 0 || day.date === today ? String(n) : ''
            return (
              <li key={day.date}>
                <button
                  type="button"
                  onClick={() => setActive(active === day.date ? null : day.date)}
                  aria-pressed={active === day.date}
                  aria-label={t('timer.day_bar', { day: dayTitle(day.date), duration: formatMinutes(day.minutes) })}
                  className="flex min-h-tap w-full flex-col items-center justify-end gap-1"
                >
                  <span className="flex w-full justify-center" style={{ height: BAR_PX }} aria-hidden="true">
                    <span
                      className={clsx(
                        'mt-auto w-3/5 max-w-[16px] rounded-t-[3px] bg-[rgb(var(--accent))]',
                        focus?.date !== day.date && 'opacity-70',
                      )}
                      style={{ height }}
                    />
                  </span>
                  <span className="h-px w-full bg-[rgb(var(--border))]" aria-hidden="true" />
                  <span
                    className={clsx(
                      'tnum h-3 text-[10px] leading-none',
                      day.date === today ? 'font-semibold text-[rgb(var(--accent))]' : 'muted',
                    )}
                  >
                    {label}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      </Card>

      <Card className="space-y-3" data-testid="time-by-area">
        <p className="text-sm font-semibold">{t('timer.by_area')}</p>
        {summary.by_area.length === 0 ? (
          <p className="text-sm muted">{t('timer.nothing_tracked')}</p>
        ) : (
          summary.by_area.map((row) => (
            <AreaRow key={row.area?.id ?? 'none'} area={row.area} minutes={row.minutes} target={row.target_minutes} max={areaMax} />
          ))
        )}
      </Card>

      {summary.by_project.length > 0 && (
        <Card className="space-y-3" data-testid="time-by-project">
          <p className="text-sm font-semibold">{t('timer.by_project')}</p>
          {summary.by_project.map((row) => (
            <div key={row.project?.id ?? 'none'} className="space-y-1">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="flex min-w-0 items-center gap-2">
                  <Dot color={row.project?.color} />
                  <span className="truncate">{row.project?.name ?? t('timer.no_project')}</span>
                </span>
                <span className="tnum shrink-0 muted">{formatMinutes(row.minutes)}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-pill bg-[rgb(var(--bg-sunken))]" aria-hidden="true">
                <div
                  className="h-full rounded-pill bg-[rgb(var(--accent-2))]"
                  style={{ width: `${(row.minutes / projectMax) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </Card>
      )}
    </>
  )
}

function AreaRow({
  area,
  minutes,
  target,
  max,
}: {
  area: Ref | null
  minutes: number
  target: number | null
  max: number
}) {
  const { t } = useTranslation()
  const scale = target ? Math.max(target, minutes) : max
  const width = scale > 0 ? (minutes / scale) * 100 : 0
  const targetAt = target ? (target / scale) * 100 : null
  const reached = target != null && target > 0 && minutes >= target
  return (
    <div className="space-y-1.5" data-testid="time-area-row">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="flex min-w-0 items-center gap-2">
          <Dot color={area?.color} />
          <span className="truncate">{area?.name ?? t('timer.no_area')}</span>
        </span>
        <span className={clsx('tnum shrink-0', reached ? 'text-[rgb(var(--accent))]' : 'muted')}>
          {target
            ? t('timer.of_target', { value: formatMinutes(minutes), target: formatMinutes(target) })
            : formatMinutes(minutes)}
        </span>
      </div>
      <div
        className="relative h-2 rounded-pill bg-[rgb(var(--bg-sunken))]"
        role="img"
        aria-label={
          target
            ? t('timer.area_target_label', { area: area?.name ?? t('timer.no_area'), value: formatMinutes(minutes), target: formatMinutes(target) })
            : t('timer.area_label', { area: area?.name ?? t('timer.no_area'), value: formatMinutes(minutes) })
        }
      >
        <div
          className={clsx('h-full rounded-pill bg-[rgb(var(--accent))]', !reached && 'opacity-75')}
          style={{ width: `${width}%` }}
        />
        {targetAt !== null && (
          <span
            className="absolute -top-1 h-4 w-0.5 rounded-full bg-[rgb(var(--text))]/70"
            style={{ left: `calc(${targetAt}% - 1px)` }}
            aria-hidden="true"
          />
        )}
      </div>
    </div>
  )
}
