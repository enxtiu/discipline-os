/**
 * The running-timer bar — above the Plan views and on Today.
 *
 * Running: what is timed, the elapsed time ticking from the server's
 * `elapsed_s` (never the device clock, see `useElapsed`), a 48 px stop button;
 * the bar opens the time log. Stopping sends `ended_at` = start + that
 * elapsed time, so a phone with a wrong clock still records what it showed.
 * After 12 h it warns; an entry is at most 24 h, so a timer over a day cannot
 * be stopped without saying how long it ran: the stop button asks first, and
 * when the server refuses anyway (`422 invalid_time_entry` for the `ended_at`
 * we send, `409 timer_too_long` without one) the same question opens.
 * Idle: `variant="plan"` offers «Запустить таймер», `variant="today"` renders
 * nothing. Start/stop work offline (queued with the tap instant). The sheets
 * load on first use, so Today stays light.
 */
import { lazy, Suspense, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { ApiError } from '@/api/client'
import { IconButton } from '@/components/ui'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { formatDuration, formatMinutes } from '../format'
import { IconPlay, IconStop } from '../icons'
import { useElapsed, useTimer } from '../queries'
import { describeEntry, instantAfter } from './entry'
import type { LongTimer } from './TimerSheets'
import { useStopTimer } from './useTimerActions'

const TimerStartSheet = lazy(() => import('./TimerSheets').then((module) => ({ default: module.TimerStartSheet })))
const TimerDurationSheet = lazy(() => import('./TimerSheets').then((module) => ({ default: module.TimerDurationSheet })))

const WARN_AFTER_S = 12 * 3600
const ASK_AFTER_S = 24 * 3600

export function TimerBar({ variant = 'plan' }: { variant?: 'plan' | 'today' }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const toast = useToast()
  const timer = useTimer()
  const running = timer.data?.running ?? null
  const elapsed = useElapsed(running, timer.dataUpdatedAt)
  const stop = useStopTimer()
  const [sheet, setSheet] = useState<'start' | 'duration' | null>(null)
  // a sheet stays mounted after its first use so its close animation can play
  const [used, setUsed] = useState({ start: false, duration: false })
  const [longTimer, setLongTimer] = useState<LongTimer | null>(null)
  const [stopping, setStopping] = useState(false)

  const show = (which: 'start' | 'duration') => {
    setUsed((value) => ({ ...value, [which]: true }))
    setSheet(which)
  }

  const askDuration = (startedAt: string) => {
    if (!running) return
    const entry = running.entry
    setLongTimer({ entryId: entry.id, startedAt, title: describeEntry(entry).title, date: entry.date, start: entry.start })
    show('duration')
  }

  const onStop = async () => {
    if (!running || stopping) return
    const entry = running.entry
    if (elapsed >= ASK_AFTER_S) {
      askDuration(entry.started_at)
      return
    }
    setStopping(true)
    try {
      const result = await stop({ entryId: entry.id, endedAt: instantAfter(entry.started_at, elapsed) ?? undefined })
      if (result.queued) toast.info(t('timer.stop_queued'))
      else toast.success(t('timer.stopped', { duration: formatMinutes((result.data.stopped?.duration_s ?? elapsed) / 60) }))
    } catch (error) {
      if (error instanceof ApiError && (error.code === 'timer_too_long' || error.code === 'invalid_time_entry')) {
        const startedAt = error.details?.started_at
        askDuration(typeof startedAt === 'string' ? startedAt : entry.started_at)
      } else {
        toast.error(errorMessage(error))
      }
    } finally {
      setStopping(false)
    }
  }

  const sheets = (
    <Suspense fallback={null}>
      {used.start && <TimerStartSheet open={sheet === 'start'} onClose={() => setSheet(null)} />}
      {used.duration && longTimer && (
        <TimerDurationSheet open={sheet === 'duration'} onClose={() => setSheet(null)} timer={longTimer} />
      )}
    </Suspense>
  )

  if (!running) {
    if (variant === 'today') return sheets
    return (
      <>
        <button
          type="button"
          onClick={() => show('start')}
          className="flex min-h-tap w-full items-center gap-2 rounded-2xl border border-dashed border-[rgb(var(--border))] px-4 text-sm font-medium text-[rgb(var(--text-muted))] transition-colors active:bg-[rgb(var(--border))]/40"
          data-testid="timer-start"
        >
          <IconPlay size={18} className="text-[rgb(var(--accent))]" />
          {t('timer.start')}
        </button>
        {sheets}
      </>
    )
  }

  const { title, subtitle } = describeEntry(running.entry)
  const long = elapsed >= WARN_AFTER_S

  return (
    <div className="space-y-1.5" data-testid="timer-bar" data-entry-id={running.entry.id}>
      <div className={clsx('card flex items-center gap-1 py-1 pl-1 pr-1.5', long ? 'shadow-glow-amber' : 'glow-accent')}>
        <button
          type="button"
          onClick={() => navigate('/plan/time')}
          className="flex min-h-tap min-w-0 flex-1 items-center gap-3 rounded-2xl px-3 text-left"
        >
          <span className="sr-only">{t('a11y.open_time_log')}: </span>
          <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden="true">
            <span
              className={clsx(
                'absolute inset-0 animate-pulse-soft rounded-full',
                long ? 'bg-[rgb(var(--warn))]' : 'bg-[rgb(var(--accent))]',
              )}
            />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[15px] font-medium" data-testid="timer-title">
              {title}
            </span>
            {subtitle && <span className="block truncate text-xs muted">{subtitle}</span>}
          </span>
          <span
            className={clsx(
              'tnum shrink-0 font-semibold',
              variant === 'today' ? 'text-base' : 'text-lg',
              long ? 'text-[rgb(var(--warn))]' : 'text-[rgb(var(--accent))]',
            )}
            data-testid="timer-elapsed"
          >
            {formatDuration(elapsed)}
          </span>
        </button>
        <IconButton
          label={t('timer.stop')}
          icon={<IconStop size={22} />}
          variant="secondary"
          className="shrink-0"
          disabled={stopping}
          onClick={() => void onStop()}
          data-testid="timer-stop"
        />
      </div>
      {long && (
        <p className="px-1 text-xs text-[rgb(var(--warn))]" role="status">
          {elapsed >= ASK_AFTER_S ? t('timer.over_day') : t('timer.over_12h')}
        </p>
      )}
      {sheets}
    </div>
  )
}
