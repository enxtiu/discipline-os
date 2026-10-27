/**
 * Day & week time grid: hours 00–24 (52 px per hour) in the page flow, the
 * toolbar, week header and all-day strip as one sticky stack under the app
 * header, blocks placed from the server's local `block.start` / `block.end`
 * strings (no timezone math), overlapping blocks side by side, a
 * current-time line, and drag — move (across days in the week) and resize
 * from the bottom edge, both snapped to 15 minutes, the snapped time shown
 * while dragging.
 *
 * The page is the only scroll container (no box scrolling inside the page):
 * on iOS a nested scroller traps swipes and shows a few hours at a time. On
 * mount the page scrolls to the current time (when today is shown) or 07:00;
 * dragging near the top or bottom edge scrolls the page.
 */
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { addDays, clamp } from '@/lib/format'
import { newTask, openTask } from '../events'
import { blockLabel, PRIORITY_STYLES } from '../format'
import type { Task } from '../types'
import {
  dayNumber,
  dayTitle,
  endLabel,
  fromMinutes,
  MINUTES_PER_DAY,
  SNAP_MINUTES,
  snapMinutes,
  weekdayShort,
} from './dates'
import {
  gridItems,
  isDone,
  layoutColumns,
  PRIORITY_CHIP,
  PRIORITY_TINT,
  visibleEnd,
  type DayBucket,
  type GridItem,
  type Placed,
} from './model'
import { shellInsets, useEdgeAutoScroll, useShellInsets } from './shell'
import { DRAG_SURFACE, useDrag, type DragBindings, type DragPoint } from './useDrag'

/** ≈ 52 px per hour: 15 minutes = 13 px, a day = 1248 px. */
const HOUR_PX = 52
const PX_PER_MIN = HOUR_PX / 60
const MIN_BLOCK_PX = 22
const MIN_VISUAL_MIN = Math.ceil(MIN_BLOCK_PX / PX_PER_MIN)
const GRID_PX = 24 * HOUR_PX

export interface BlockChange {
  date: string
  /** Minutes after local midnight of `date`. */
  start: number
  /** May pass 1440: the block then ends the next day. */
  end: number
}

interface DragItem {
  task: Task
  mode: 'move' | 'resize'
  dayIndex: number
  start: number
  end: number
}

/** Minutes since local midnight in the user's timezone (for the "now" line only). */
function nowMinutes(timeZone: string): number | null {
  try {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(new Date())
    const hour = Number(parts.find((part) => part.type === 'hour')?.value)
    const minute = Number(parts.find((part) => part.type === 'minute')?.value)
    return Number.isFinite(hour) && Number.isFinite(minute) ? (hour % 24) * 60 + minute : null
  } catch {
    return null
  }
}

function useNowMinutes(timeZone: string): number | null {
  const [value, setValue] = useState(() => nowMinutes(timeZone))
  useEffect(() => {
    setValue(nowMinutes(timeZone))
    const timer = window.setInterval(() => setValue(nowMinutes(timeZone)), 30_000)
    return () => window.clearInterval(timer)
  }, [timeZone])
  return value
}

export function TimeGrid({
  days,
  index,
  today,
  timeZone,
  toolbar,
  onChange,
  onPickDay,
}: {
  /** One day (day view) or seven (week view). */
  days: string[]
  index: Map<string, DayBucket>
  /** The server's today (the "now" line is drawn in that column only). */
  today: string | undefined
  timeZone: string
  /** View switcher and navigation: sticks with the week header. */
  toolbar?: ReactNode
  onChange: (task: Task, change: BlockChange) => void
  /** Week view: a day header or "+N" opens that day. */
  onPickDay?: (day: string) => void
}) {
  const { t } = useTranslation()
  const insets = useShellInsets()
  const stickyRef = useRef<HTMLDivElement>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  const layerRef = useRef<HTMLDivElement>(null)
  const count = days.length
  const multi = count > 1
  const now = useNowMinutes(timeZone)
  const todayIndex = today ? days.indexOf(today) : -1

  const columns = useMemo(
    () =>
      days.map((day) =>
        layoutColumns(gridItems(day, index.get(day), index.get(addDays(day, -1))), MIN_VISUAL_MIN),
      ),
    [days, index],
  )

  // Opens at the current time when today is shown, otherwise at 07:00: the
  // page scrolls so that hour sits right under the sticky headers. Only when
  // the grid mounts (switching day ↔ week remounts it); week-to-week
  // navigation keeps the hour the user scrolled to.
  useLayoutEffect(() => {
    const grid = gridRef.current
    if (!grid) return
    const target = todayIndex >= 0 && now !== null ? now - 90 : 7 * 60
    const headers = shellInsets().top + (stickyRef.current?.offsetHeight ?? 0)
    const top = grid.getBoundingClientRect().top + window.scrollY + Math.max(0, target) * PX_PER_MIN - headers
    window.scrollTo({ top: Math.max(0, top), behavior: 'auto' })
  }, [])

  // ---- drag ---------------------------------------------------------------------
  const [preview, setPreview] = useState<DragItem | null>(null)
  const grabOffset = useRef(0)
  const lastPoint = useRef<DragPoint | null>(null)
  const dragItem = useRef<DragItem | null>(null)
  const frame = useRef(0)

  const locate = useCallback(
    (point: DragPoint) => {
      const rect = layerRef.current?.getBoundingClientRect()
      if (!rect) return { minutes: 0, dayIndex: 0 }
      return {
        minutes: (point.y - rect.top) / PX_PER_MIN,
        dayIndex: clamp(Math.floor(((point.x - rect.left) / Math.max(1, rect.width)) * count), 0, count - 1),
      }
    },
    [count],
  )

  const compute = useCallback(
    (item: DragItem, point: DragPoint): DragItem => {
      const { minutes, dayIndex } = locate(point)
      const anchor = minutes - grabOffset.current
      if (item.mode === 'move') {
        const length = item.end - item.start
        const start = clamp(snapMinutes(anchor), 0, MINUTES_PER_DAY - SNAP_MINUTES)
        return { ...item, dayIndex: multi ? dayIndex : item.dayIndex, start, end: start + length }
      }
      return { ...item, end: clamp(snapMinutes(anchor), item.start + SNAP_MINUTES, MINUTES_PER_DAY) }
    },
    [locate, multi],
  )

  const stopDrag = () => {
    window.cancelAnimationFrame(frame.current)
    frame.current = 0
    dragItem.current = null
    lastPoint.current = null
    setPreview(null)
  }

  const drag = useDrag<DragItem>({
    onStart: (item, start) => {
      grabOffset.current = locate(start).minutes - (item.mode === 'move' ? item.start : item.end)
      dragItem.current = item
      lastPoint.current = start
      setPreview(item)
    },
    onMove: (item, point) => {
      dragItem.current = item
      lastPoint.current = point
      if (frame.current) return
      frame.current = window.requestAnimationFrame(() => {
        frame.current = 0
        const current = dragItem.current
        const last = lastPoint.current
        if (current && last) setPreview(compute(current, last))
      })
    },
    onDrop: (item, point) => {
      const next = compute(item, point)
      stopDrag()
      if (next.dayIndex === item.dayIndex && next.start === item.start && next.end === item.end) return
      onChange(item.task, { date: days[next.dayIndex], start: next.start, end: next.end })
    },
    onCancel: stopDrag,
  })
  const dragging = drag.dragging !== null

  // While dragging, the page scrolls near the edges (below the sticky headers,
  // above the tab bar) and the preview follows the content under the finger.
  useEdgeAutoScroll(
    dragging,
    lastPoint,
    () => insets.top + (stickyRef.current?.offsetHeight ?? 0),
    () => {
      const point = lastPoint.current
      const item = dragItem.current
      if (point && item) setPreview(compute(item, point))
    },
  )

  // ---- tap on an empty slot → a new task in that half hour ------------------------
  const onEmptyTap = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return
    const { minutes, dayIndex } = locate({ x: event.clientX, y: event.clientY })
    const start = clamp(Math.floor(minutes / 30) * 30, 0, MINUTES_PER_DAY - 30)
    newTask({ block: { date: days[dayIndex], start: fromMinutes(start), end: fromMinutes(start + 60) } })
  }

  const template = { gridTemplateColumns: `2.5rem repeat(${count}, minmax(0, 1fr))` }
  const hasAllDay = days.some((day) => {
    const bucket = index.get(day)
    return !!bucket && (bucket.allDay.length > 0 || bucket.occurrences.some((occurrence) => !occurrence.block))
  })
  const hasHeader = multi || hasAllDay
  const showNow = now !== null && todayIndex >= 0

  return (
    <div data-testid="cal-grid" data-view={multi ? 'week' : 'day'}>
      <div ref={stickyRef} className="sticky z-20 bg-[rgb(var(--bg))] pt-1" style={{ top: insets.top }}>
        {toolbar && <div className="pb-3">{toolbar}</div>}
        {hasHeader && (
          <div className="rounded-t-card border border-b-0 border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))]">
            {multi && (
              <div className="grid" style={template}>
                <span aria-hidden="true" />
                {days.map((day) => (
                  <button
                    key={day}
                    type="button"
                    onClick={() => onPickDay?.(day)}
                    aria-current={day === today ? 'date' : undefined}
                    className="flex min-h-tap flex-col items-center justify-center gap-0.5 py-1"
                    data-testid="cal-day-header"
                    data-date={day}
                  >
                    <span className="sr-only">{t('calendar.open_day_named', { day: dayTitle(day) })}</span>
                    <span
                      className={clsx('text-[10px] uppercase', day === today ? 'text-[rgb(var(--accent))]' : 'muted')}
                      aria-hidden="true"
                    >
                      {weekdayShort(day)}
                    </span>
                    <span
                      aria-hidden="true"
                      className={clsx(
                        'tnum flex h-6 min-w-6 items-center justify-center rounded-full px-1 text-[13px] font-semibold',
                        day === today && 'bg-[rgb(var(--accent))] text-on-accent',
                      )}
                    >
                      {dayNumber(day)}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {hasAllDay && (
              <div
                className={clsx('grid py-1', multi && 'border-t border-[rgb(var(--border))]/70')}
                style={template}
                data-testid="cal-allday"
              >
                <span className="self-center px-0.5 text-center text-[9px] leading-tight muted">
                  {t('calendar.all_day_short')}
                </span>
                {days.map((day) => (
                  <AllDayCell key={day} day={day} bucket={index.get(day)} compact={multi} onPickDay={onPickDay} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div
        ref={gridRef}
        className={clsx(
          'relative overflow-hidden rounded-b-card border border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))] shadow-card',
          !hasHeader && 'rounded-t-card',
        )}
        style={{ height: GRID_PX }}
        data-testid="cal-hours"
      >
        {/* hour lines and labels */}
        {Array.from({ length: 24 }, (_, hour) => (
          <div
            key={hour}
            className="pointer-events-none absolute inset-x-0 flex items-start"
            style={{ top: hour * HOUR_PX }}
            aria-hidden="true"
          >
            <span className="tnum w-10 shrink-0 -translate-y-1/2 pr-1.5 text-right text-[10px] leading-none muted">
              {hour === 0 || (showNow && Math.abs((now ?? 0) - hour * 60) < 14) ? '' : fromMinutes(hour * 60)}
            </span>
            <span className="h-px flex-1 bg-[rgb(var(--border))]/70" />
          </div>
        ))}
        {showNow && (
          <span
            className="tnum pointer-events-none absolute left-0 z-20 w-10 -translate-y-1/2 bg-[rgb(var(--bg-elevated))] pr-1.5 text-right text-[10px] font-semibold leading-none text-[rgb(var(--accent))]"
            style={{ top: now * PX_PER_MIN }}
            aria-hidden="true"
          >
            {fromMinutes(now)}
          </span>
        )}

        <div
          ref={layerRef}
          className={clsx('absolute inset-y-0 left-10 right-0', dragging && 'cursor-grabbing')}
          onClick={onEmptyTap}
          data-testid="cal-layer"
        >
          {multi &&
            days.map((day, dayIndex) =>
              dayIndex === 0 ? null : (
                <span
                  key={day}
                  className="pointer-events-none absolute inset-y-0 border-l border-[rgb(var(--border))]/70"
                  style={{ left: `${(dayIndex / count) * 100}%` }}
                  aria-hidden="true"
                />
              ),
            )}
          {multi && todayIndex >= 0 && (
            <span
              className="pointer-events-none absolute inset-y-0 bg-[rgb(var(--accent))]/[0.05]"
              style={{ left: `${(todayIndex / count) * 100}%`, width: `${100 / count}%` }}
              aria-hidden="true"
            />
          )}

          <BlocksLayer
            columns={columns}
            days={days}
            draggingId={drag.dragging?.task.id ?? null}
            bind={drag.bind}
            compact={multi}
          />

          {showNow && (
            <span
              className="pointer-events-none absolute z-20 h-0.5 rounded-full bg-[rgb(var(--accent))]"
              style={{
                top: now * PX_PER_MIN - 1,
                left: `${(todayIndex / count) * 100}%`,
                width: `${100 / count}%`,
              }}
              data-testid="cal-now"
              aria-hidden="true"
            >
              <span className="absolute -left-1 -top-[3px] h-2 w-2 rounded-full bg-[rgb(var(--accent))]" />
            </span>
          )}

          {preview && <DragPreview item={preview} count={count} compact={multi} />}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Blocks (memoised: a drag re-renders the preview, not every block)
// --------------------------------------------------------------------------- //
const BlocksLayer = memo(function BlocksLayer({
  columns,
  days,
  draggingId,
  bind,
  compact,
}: {
  columns: Placed<GridItem>[][]
  days: string[]
  draggingId: string | null
  bind: (item: DragItem) => DragBindings
  compact: boolean
}) {
  const { t } = useTranslation()
  const count = days.length
  return (
    <>
      {columns.map((placed, dayIndex) =>
        placed.map(({ item, column, columns: span }) => {
          const top = item.start * PX_PER_MIN
          const height = Math.max(MIN_BLOCK_PX, (visibleEnd(item.end) - item.start) * PX_PER_MIN)
          const style = {
            top,
            height,
            left: `calc(${((dayIndex + column / span) / count) * 100}% + 1px)`,
            width: `calc(${(1 / span / count) * 100}% - 2px)`,
          }
          // What fits: two title lines from 38 px, the time from 34 px (day) / 60 px (week),
          // read from the top (a flex column: a button centres its content).
          // Side by side in a week column a block is ~20 px wide: one clipped line,
          // never a word broken into a letter per line.
          const narrow = compact && span > 1
          const title = narrow
            ? 'overflow-hidden whitespace-nowrap'
            : height >= 38
              ? 'line-clamp-2 hyphens-auto break-words' // no `block`: it would override the clamp's display
              : 'block truncate'
          const showTime = !narrow && (compact ? height >= 60 : height >= 34)

          if (item.kind === 'occurrence') {
            const occurrence = item.occurrence
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => openTask(occurrence.task_id)}
                className={clsx(
                  'absolute z-10 flex flex-col overflow-hidden rounded-md border border-dashed border-[rgb(var(--text-muted))]/60 py-0.5 text-left leading-tight opacity-75',
                  compact ? 'text-[10px]' : 'text-xs',
                  narrow ? 'px-0.5' : 'px-1',
                )}
                style={style}
                aria-label={t('calendar.repeat_label', {
                  title: occurrence.title,
                  time: blockLabel(occurrence.block),
                })}
                data-testid="cal-occurrence"
              >
                <span className={clsx('font-medium', title)}>{occurrence.title}</span>
                {showTime && (
                  <span className="tnum block truncate muted">
                    {compact ? occurrence.block?.start : blockLabel(occurrence.block)}
                  </span>
                )}
              </button>
            )
          }

          const task = item.task
          const done = isDone(task)
          const movable = item.kind === 'task'
          const resizable = movable && !task.block?.ends_next_day && height >= 30
          const origin: DragItem = { task, mode: 'move', dayIndex, start: item.start, end: item.end }
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => openTask(task.id)}
              {...(movable ? bind(origin) : {})}
              className={clsx(
                'absolute z-10 flex flex-col overflow-hidden rounded-md border-l-[3px] py-0.5 text-left leading-tight',
                done ? 'text-[rgb(var(--text-muted))]' : 'text-[rgb(var(--text))]',
                compact ? 'text-[10px]' : 'text-xs',
                narrow ? 'px-0.5' : 'px-1',
                PRIORITY_TINT[task.priority],
                !task.project?.color && PRIORITY_STYLES[task.priority].ring,
                draggingId === task.id && 'opacity-30',
                movable && clsx(DRAG_SURFACE, 'cursor-grab'),
              )}
              style={{ ...style, borderLeftColor: task.project?.color ?? undefined }}
              aria-label={t('calendar.block_label', { title: task.title, time: blockLabel(task.block) })}
              data-testid={movable ? 'cal-block' : 'cal-continuation'}
              data-task-id={task.id}
              data-start={task.block?.start}
              data-end={task.block?.end}
              data-date={task.block?.date}
            >
              <span className={clsx('font-semibold', title, done && 'line-through')}>{task.title}</span>
              {showTime && (
                <span className="tnum block truncate opacity-75">{compact ? task.block?.start : blockLabel(task.block)}</span>
              )}
              {!compact && height >= 64 && (task.project || task.area) && (
                <span className="block truncate opacity-75">{task.project?.name ?? task.area?.name}</span>
              )}
              {resizable && (
                <span
                  {...bind({ ...origin, mode: 'resize' })}
                  className={clsx('absolute inset-x-0 bottom-0 flex h-3.5 cursor-ns-resize items-end justify-center pb-0.5', DRAG_SURFACE)}
                  data-testid="cal-resize"
                  aria-hidden="true"
                >
                  <span className="h-0.5 w-5 rounded-full bg-current opacity-40" />
                </span>
              )}
            </button>
          )
        }),
      )}
    </>
  )
})

function DragPreview({ item, count, compact }: { item: DragItem; count: number; compact: boolean }) {
  const top = item.start * PX_PER_MIN
  const height = Math.max(MIN_BLOCK_PX, (visibleEnd(item.end) - item.start) * PX_PER_MIN)
  return (
    <div
      className={clsx(
        'pointer-events-none absolute z-30 overflow-hidden rounded-md border-l-[3px] px-1 py-0.5 leading-tight shadow-card ring-2 ring-[rgb(var(--accent))]',
        'bg-[rgb(var(--bg-elevated))]',
        compact ? 'text-[10px]' : 'text-xs',
        PRIORITY_STYLES[item.task.priority].ring,
      )}
      style={{
        top,
        height,
        left: `calc(${(item.dayIndex / count) * 100}% + 1px)`,
        width: `calc(${100 / count}% - 2px)`,
        borderLeftColor: item.task.project?.color ?? undefined,
      }}
      data-testid="cal-drag-preview"
    >
      <span className="tnum block font-semibold text-[rgb(var(--accent))]" data-testid="cal-drag-time">
        {compact ? (
          <>
            <span className="block whitespace-nowrap">{fromMinutes(item.start)}</span>
            <span className="block whitespace-nowrap opacity-80">{endLabel(item.end)}</span>
          </>
        ) : (
          <span className="whitespace-nowrap">
            {fromMinutes(item.start)}–{endLabel(item.end)}
          </span>
        )}
      </span>
      <span className="block truncate">{item.task.title}</span>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// All-day strip
// --------------------------------------------------------------------------- //
function AllDayCell({
  day,
  bucket,
  compact,
  onPickDay,
}: {
  day: string
  bucket: DayBucket | undefined
  compact: boolean
  onPickDay?: (day: string) => void
}) {
  const { t } = useTranslation()
  const tasks = bucket?.allDay ?? []
  const repeats = (bucket?.occurrences ?? []).filter((occurrence) => !occurrence.block)
  const total = tasks.length + repeats.length
  const limit = compact ? 2 : Number.POSITIVE_INFINITY
  const shownTasks = tasks.slice(0, limit)
  const shownRepeats = repeats.slice(0, Math.max(0, limit - shownTasks.length))
  const hidden = total - shownTasks.length - shownRepeats.length

  return (
    <div
      className={clsx('min-w-0', compact ? 'space-y-0.5 px-0.5' : 'flex gap-1.5 overflow-x-auto px-1.5 py-0.5')}
      style={compact ? undefined : { scrollbarWidth: 'none' }}
    >
      {shownTasks.map((task) => {
        const label = task.due_time ? `${task.due_time} ${task.title}` : task.title
        return (
          <button
            key={task.id}
            type="button"
            onClick={() => openTask(task.id)}
            className={clsx(
              'block truncate rounded-md text-left',
              compact ? 'w-full max-w-full px-1 text-[10px] leading-5' : 'min-h-tap max-w-[15rem] shrink-0 px-3 text-sm',
              PRIORITY_CHIP[task.priority],
              isDone(task) && 'text-[rgb(var(--text-muted))] line-through',
            )}
            title={label}
            aria-label={label}
            data-testid="cal-allday-task"
            data-task-id={task.id}
          >
            {/* week columns are too narrow for the time: it is in the label, the day view and the editor */}
            {!compact && task.due_time && <span className="tnum mr-1.5 opacity-75">{task.due_time}</span>}
            {task.title}
          </button>
        )
      })}
      {shownRepeats.map((occurrence) => (
        <button
          key={`${occurrence.task_id}:${occurrence.date}`}
          type="button"
          onClick={() => openTask(occurrence.task_id)}
          className={clsx(
            'block truncate rounded-md border border-dashed border-[rgb(var(--text-muted))]/60 text-left opacity-75',
            compact ? 'w-full max-w-full px-1 text-[10px] leading-[18px]' : 'min-h-tap max-w-[15rem] shrink-0 px-3 text-sm',
          )}
          aria-label={t('calendar.repeat_label', { title: occurrence.title, time: occurrence.due_time ?? '' })}
          data-testid="cal-occurrence"
        >
          {!compact && occurrence.due_time && <span className="tnum mr-1.5">{occurrence.due_time}</span>}
          {occurrence.title}
        </button>
      ))}
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => onPickDay?.(day)}
          className="block w-full truncate px-1 text-left text-[10px] leading-5 muted"
          aria-label={t('calendar.more_label', { n: hidden, day: dayTitle(day) })}
        >
          {t('calendar.more', { n: hidden })}
        </button>
      )}
    </div>
  )
}
