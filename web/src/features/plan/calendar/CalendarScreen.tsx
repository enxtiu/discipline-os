/**
 * /plan/calendar — Day · Week · Month over `GET /views/calendar`.
 *
 * State lives in the URL (`?view=day|week|month&date=YYYY-MM-DD`) so a reload
 * or a shared link lands on the same range; the last view is remembered on
 * the device. "Today" is the server's day (`useCounts`), weeks start on
 * `settings.week_start`; the client only adds days to day keys. Day and week
 * read the same week range (switching between them is instant), month reads
 * its 6-week grid (≤ 42 days, under the 62-day limit).
 */
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, ErrorState, IconButton, Rows, Row, SegmentedControl, Spinner } from '@/components/ui'
import { IconChevronLeft, IconChevronRight, IconPlus } from '@/components/ui/icons'
import { useAuth } from '@/features/auth/AuthProvider'
import { errorMessage } from '@/lib/errors'
import { addDays, timezone } from '@/lib/format'
import { newTask, openTask } from '../events'
import { blockLabel, PRIORITY_STYLES, relativeDay } from '../format'
import { useCalendar, useCounts } from '../queries'
import type { Task } from '../types'
import {
  addMonths,
  dayMonth,
  dayTitle,
  monthGrid,
  monthTitle,
  rangeTitle,
  readDayKey,
  sameMonth,
  toMinutes,
  weekDays,
} from './dates'
import { indexCalendar, isDone, type DayBucket } from './model'
import { MonthGrid } from './MonthGrid'
import { shellInsets } from './shell'
import { CalendarSkeleton } from './CalendarSkeleton'
import { TimeGrid, type BlockChange } from './TimeGrid'
import { useMoveTask } from './useMoveTask'

type View = 'day' | 'week' | 'month'
const VIEWS: View[] = ['day', 'week', 'month']
const VIEW_KEY = 'dsos.calendar.view'

function readView(value: string | null): View | null {
  return VIEWS.includes(value as View) ? (value as View) : null
}

function storedView(): View {
  try {
    return readView(window.localStorage.getItem(VIEW_KEY)) ?? 'week'
  } catch {
    return 'week'
  }
}

export default function CalendarScreen() {
  const { t } = useTranslation()
  const { session } = useAuth()
  const weekStart = session?.settings.week_start ?? 1
  const timeZone = session?.settings.timezone || timezone()
  const counts = useCounts()
  const today = counts.data?.date
  const [params, setParams] = useSearchParams()
  const view = readView(params.get('view')) ?? storedView()
  const anchor = readDayKey(params.get('date')) ?? today ?? null
  const [picked, setPicked] = useState<string | null>(null)
  const move = useMoveTask()
  const rootRef = useRef<HTMLDivElement>(null)
  const shownView = useRef(view)

  // Day and week scroll the page to an hour; switching to the month brings its
  // toolbar back into view if that left it above the screen.
  useLayoutEffect(() => {
    if (shownView.current === view) return
    shownView.current = view
    const root = rootRef.current
    if (view !== 'month' || !root) return
    const top = shellInsets().top
    const rect = root.getBoundingClientRect()
    if (rect.top < top) window.scrollTo({ top: Math.max(0, rect.top + window.scrollY - top - 8), behavior: 'auto' })
  }, [view])

  const go = (next: { view?: View; date?: string | null }) => {
    const query = new URLSearchParams(params)
    if (next.view) {
      query.set('view', next.view)
      try {
        window.localStorage.setItem(VIEW_KEY, next.view)
      } catch {
        /* private mode: the URL still carries it */
      }
    }
    if (next.date !== undefined) {
      if (next.date && next.date !== today) query.set('date', next.date)
      else query.delete('date')
    }
    setParams(query, { replace: true })
  }

  const visible = useMemo(() => {
    if (!anchor) return null
    if (view === 'month') {
      const grid = monthGrid(anchor, weekStart)
      return { from: grid[0], to: grid[grid.length - 1], days: grid }
    }
    const week = weekDays(anchor, weekStart)
    return { from: week[0], to: week[6], days: view === 'day' ? [anchor] : week }
  }, [anchor, view, weekStart])

  const calendar = useCalendar(visible?.from ?? '', visible?.to ?? '', !!visible)
  const index = useMemo(() => indexCalendar(calendar.data), [calendar.data])
  const ready = !!calendar.data && calendar.data.from === visible?.from && calendar.data.to === visible?.to

  // month view: the tapped day (leading/trailing days of the grid included), else the anchor
  const selected = anchor && view === 'month' ? (picked && visible?.days.includes(picked) ? picked : anchor) : null

  const step = (direction: 1 | -1) => {
    if (!anchor) return
    setPicked(null)
    if (view === 'day') go({ date: addDays(anchor, direction) })
    else if (view === 'week') go({ date: addDays(anchor, 7 * direction) })
    else {
      const first = addMonths(anchor, direction)
      go({ date: today && sameMonth(today, first) ? today : first })
    }
  }

  const showsToday =
    !!today && !!visible && (view === 'month' ? sameMonth(today, anchor ?? '') && selected === today : visible.days.includes(today))
  const title = !anchor
    ? ''
    : view === 'day'
      ? dayTitle(anchor)
      : view === 'week' && visible
        ? rangeTitle(visible.from, visible.to)
        : monthTitle(anchor)

  const onBlockChange = (task: Task, change: BlockChange) => void move(task, { kind: 'block', ...change })
  const onMonthMove = (task: Task, day: string) => {
    if (task.block) {
      const start = toMinutes(task.block.start)
      void move(task, { kind: 'block', date: day, start, end: start + task.block.minutes })
    } else {
      void move(task, { kind: 'due', date: day })
    }
  }
  const openDay = (day: string) => {
    setPicked(null)
    go({ view: 'day', date: day })
  }

  const toolbar = (
    <div className="space-y-2" data-testid="cal-toolbar">
      <SegmentedControl
        value={view}
        onChange={(next) => go({ view: next })}
        options={VIEWS.map((value) => ({ value, label: t(`calendar.view.${value}`) }))}
      />
      <div className="flex items-center gap-1">
        <IconButton
          label={t(`calendar.prev.${view}`)}
          icon={<IconChevronLeft size={22} />}
          onClick={() => step(-1)}
          disabled={!anchor}
        />
        <div className="flex min-w-0 flex-1 items-center justify-center gap-2">
          <h2 className="truncate text-center text-[15px] font-semibold" data-testid="cal-title">
            {title}
          </h2>
          {calendar.isFetching && !ready && <Spinner size={14} className="shrink-0 muted" />}
        </div>
        <IconButton
          label={t(`calendar.next.${view}`)}
          icon={<IconChevronRight size={22} />}
          onClick={() => step(1)}
          disabled={!anchor}
        />
        <Button
          variant="secondary"
          size="sm"
          className="min-h-tap shrink-0"
          disabled={!today || showsToday}
          onClick={() => {
            setPicked(null)
            go({ date: null })
          }}
        >
          {t('calendar.today')}
        </Button>
      </div>
    </div>
  )

  // Day and week: the toolbar sticks with the week header inside the grid.
  if (anchor && calendar.data && view !== 'month' && visible) {
    return (
      <div ref={rootRef} data-testid="calendar">
        <TimeGrid
          key={view}
          days={visible.days}
          index={index}
          today={today}
          timeZone={timeZone}
          toolbar={toolbar}
          onChange={onBlockChange}
          onPickDay={openDay}
        />
      </div>
    )
  }

  let content: React.ReactNode = null
  if (!anchor) {
    content = counts.isError ? (
      <ErrorState message={errorMessage(counts.error)} onRetry={() => void counts.refetch()} retryLabel={t('app.retry')} />
    ) : (
      <CalendarSkeleton view={view} />
    )
  } else if (calendar.isError && !calendar.data) {
    content = (
      <ErrorState message={errorMessage(calendar.error)} onRetry={() => void calendar.refetch()} retryLabel={t('app.retry')} />
    )
  } else if (!calendar.data) {
    content = <CalendarSkeleton view={view} />
  } else if (visible && selected) {
    content = (
      <>
        <MonthGrid
          days={visible.days}
          month={anchor}
          index={index}
          today={today}
          selected={selected}
          onSelect={setPicked}
          onMove={onMonthMove}
        />
        <DayAgenda day={selected} bucket={index.get(selected)} today={today} loading={!ready} onOpenDay={openDay} />
      </>
    )
  }

  return (
    <div ref={rootRef} className="space-y-3" data-testid="calendar">
      {toolbar}
      {content}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// The selected day under the month grid
// --------------------------------------------------------------------------- //
function DayAgenda({
  day,
  bucket,
  today,
  loading,
  onOpenDay,
}: {
  day: string
  bucket: DayBucket | undefined
  today: string | undefined
  loading: boolean
  onOpenDay: (day: string) => void
}) {
  const { t } = useTranslation()
  const tasks = bucket ? [...bucket.blocks, ...bucket.allDay] : []
  const repeats = bucket?.occurrences ?? []
  const near = !!today && (day === today || day === addDays(today, 1) || day === addDays(today, -1))
  const heading = near ? t('calendar.day_heading', { relative: relativeDay(day, today), date: dayMonth(day) }) : dayTitle(day)

  const timeOf = (task: Task) =>
    task.block ? blockLabel(task.block) : task.due_time ? task.due_time : t('calendar.all_day')

  return (
    <section className="space-y-2" data-testid="cal-agenda" data-date={day}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="min-w-0 truncate text-[15px] font-semibold">{heading}</h3>
        <Button variant="ghost" size="sm" className="min-h-tap shrink-0" onClick={() => onOpenDay(day)}>
          {t('calendar.open_day')}
        </Button>
      </div>
      {tasks.length === 0 && repeats.length === 0 ? (
        <p className={clsx('rounded-2xl border border-dashed border-[rgb(var(--border))] px-4 py-5 text-center text-sm muted', loading && 'opacity-60')}>
          {loading ? t('app.loading') : t('calendar.day_empty')}
        </p>
      ) : (
        <Rows>
          {tasks.map((task) => (
            <Row
              key={task.id}
              onClick={() => openTask(task.id)}
              className={clsx(isDone(task) && 'opacity-60')}
              icon={<span className={clsx('block h-2.5 w-2.5 rounded-full', PRIORITY_STYLES[task.priority].dot)} />}
              label={<span className={clsx(isDone(task) && 'line-through')}>{task.title}</span>}
              hint={task.project?.name ?? task.area?.name ?? undefined}
              value={timeOf(task)}
            />
          ))}
          {repeats.map((occurrence) => (
            <Row
              key={`occ:${occurrence.task_id}`}
              onClick={() => openTask(occurrence.task_id)}
              className="opacity-70"
              icon={<span className="block h-2.5 w-2.5 rounded-full border border-dashed border-[rgb(var(--text-muted))]" />}
              label={occurrence.title}
              hint={t('calendar.repeat')}
              value={occurrence.block ? blockLabel(occurrence.block) : occurrence.due_time ?? t('calendar.all_day')}
            />
          ))}
        </Rows>
      )}
      <Button
        variant="secondary"
        block
        icon={<IconPlus size={18} />}
        onClick={() => newTask({ due_date: day })}
        data-testid="cal-add-on-day"
      >
        {t('calendar.add_on_day')}
      </Button>
    </section>
  )
}
