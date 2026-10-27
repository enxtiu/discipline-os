/**
 * Month view: the 6×7 grid of the month (weeks start on `settings.week_start`).
 * Each day shows up to three chips (priority tint, done dimmed, repeats
 * dashed), «+N» and the server's done count. Tap a day to list it below the
 * grid; long-press (touch) or drag (mouse) a chip onto another day to move it.
 */
import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { clamp } from '@/lib/format'
import type { Occurrence, Task } from '../types'
import { dayNumber, dayTitle, monthTitle, sameMonth, weekdayShort } from './dates'
import { isDone, PRIORITY_CHIP, type DayBucket } from './model'
import { DRAG_SURFACE, useDrag, type DragPoint } from './useDrag'

const CHIPS_PER_DAY = 3

type Chip = { kind: 'task'; task: Task } | { kind: 'repeat'; occurrence: Occurrence }

interface ChipDrag {
  task: Task
  from: string
}

/** Open tasks by time, then repeats, then done tasks (all dimmed after the open ones). */
function chipsOf(bucket: DayBucket | undefined): Chip[] {
  if (!bucket) return []
  const tasks = [...bucket.blocks, ...bucket.allDay]
  const open = tasks.filter((task) => !isDone(task))
  const done = tasks.filter((task) => isDone(task))
  return [
    ...open.map((task): Chip => ({ kind: 'task', task })),
    ...bucket.occurrences.map((occurrence): Chip => ({ kind: 'repeat', occurrence })),
    ...done.map((task): Chip => ({ kind: 'task', task })),
  ]
}

export function MonthGrid({
  days,
  month,
  index,
  today,
  selected,
  onSelect,
  onMove,
}: {
  /** The 42 keys of the grid. */
  days: string[]
  /** Any key inside the displayed month. */
  month: string
  index: Map<string, DayBucket>
  today: string | undefined
  selected: string
  onSelect: (day: string) => void
  onMove: (task: Task, day: string) => void
}) {
  const { t } = useTranslation()
  const gridRef = useRef<HTMLDivElement>(null)
  const [pointer, setPointer] = useState<DragPoint | null>(null)
  const [target, setTarget] = useState<string | null>(null)

  const cellAt = useCallback(
    (point: DragPoint): string | null => {
      const rect = gridRef.current?.getBoundingClientRect()
      if (!rect) return null
      const margin = 24
      if (
        point.x < rect.left - margin ||
        point.x > rect.right + margin ||
        point.y < rect.top - margin ||
        point.y > rect.bottom + margin
      ) {
        return null
      }
      const column = clamp(Math.floor(((point.x - rect.left) / Math.max(1, rect.width)) * 7), 0, 6)
      const row = clamp(Math.floor(((point.y - rect.top) / Math.max(1, rect.height)) * 6), 0, 5)
      return days[row * 7 + column] ?? null
    },
    [days],
  )

  const drag = useDrag<ChipDrag>({
    onStart: (item, start) => {
      setPointer(start)
      setTarget(item.from)
    },
    onMove: (_item, point) => {
      setPointer(point)
      setTarget(cellAt(point))
    },
    onDrop: (item, point) => {
      const day = cellAt(point)
      setPointer(null)
      setTarget(null)
      if (day && day !== item.from) onMove(item.task, day)
    },
    onCancel: () => {
      setPointer(null)
      setTarget(null)
    },
  })

  const header = days.slice(0, 7)

  return (
    <div className="card overflow-hidden" data-testid="cal-month">
      <div className="grid grid-cols-7 border-b border-[rgb(var(--border))]" aria-hidden="true">
        {header.map((day) => (
          <span key={day} className="py-2 text-center text-[10px] uppercase muted">
            {weekdayShort(day)}
          </span>
        ))}
      </div>
      <div
        ref={gridRef}
        className="grid grid-cols-7 grid-rows-6"
        role="group"
        aria-label={monthTitle(month)}
      >
        {days.map((day, position) => {
          const bucket = index.get(day)
          const chips = chipsOf(bucket)
          const shown = chips.slice(0, CHIPS_PER_DAY)
          const hidden = chips.length - shown.length
          const inMonth = sameMonth(day, month)
          const isToday = day === today
          const isSelected = day === selected
          const doneCount = bucket?.doneCount ?? 0
          const openCount = chips.filter((chip) => chip.kind === 'task' && !isDone(chip.task)).length
          return (
            <div
              key={day}
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              aria-current={isToday ? 'date' : undefined}
              aria-label={t('calendar.month_day_label', { day: dayTitle(day), open: openCount, done: doneCount })}
              onClick={() => onSelect(day)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onSelect(day)
                }
              }}
              className={clsx(
                'relative flex min-h-[4.75rem] min-w-0 cursor-pointer flex-col gap-0.5 border-[rgb(var(--border))]/70 p-0.5 outline-none transition-colors',
                position % 7 !== 6 && 'border-r',
                position < 35 && 'border-b',
                !inMonth && 'bg-[rgb(var(--bg-sunken))]/50',
                isSelected && 'bg-[rgb(var(--accent))]/10',
                target === day && 'ring-2 ring-inset ring-[rgb(var(--accent))]',
                'focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--accent))]',
              )}
              data-testid="cal-month-day"
              data-date={day}
            >
              <div className="flex items-center justify-between gap-0.5 px-0.5">
                <span
                  className={clsx(
                    'tnum flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px] font-semibold leading-none',
                    isToday && 'bg-[rgb(var(--accent))] text-ink-950',
                    !isToday && !inMonth && 'muted opacity-60',
                  )}
                >
                  {dayNumber(day)}
                </span>
                {doneCount > 0 && (
                  <span className="tnum text-[9px] leading-none text-[rgb(var(--accent))]" aria-hidden="true">
                    {t('calendar.done_count', { n: doneCount })}
                  </span>
                )}
              </div>
              {shown.map((chip) => {
                if (chip.kind === 'repeat') {
                  return (
                    <span
                      key={`occ:${chip.occurrence.task_id}`}
                      className="block truncate rounded-[4px] border border-dashed border-[rgb(var(--text-muted))]/50 px-0.5 text-[9.5px] leading-[13px] opacity-70"
                      aria-hidden="true"
                      data-testid="cal-month-repeat"
                    >
                      {chip.occurrence.title}
                    </span>
                  )
                }
                const task = chip.task
                const done = isDone(task)
                return (
                  <span
                    key={task.id}
                    {...drag.bind({ task, from: day })}
                    className={clsx(
                      'block cursor-grab truncate rounded-[4px] px-0.5 text-[9.5px] leading-[15px] text-[rgb(var(--text))]',
                      DRAG_SURFACE,
                      PRIORITY_CHIP[task.priority],
                      done && 'line-through opacity-50',
                      drag.dragging?.task.id === task.id && 'opacity-30',
                    )}
                    aria-hidden="true"
                    data-testid="cal-month-chip"
                    data-task-id={task.id}
                  >
                    {task.title}
                  </span>
                )
              })}
              {hidden > 0 && (
                <span className="px-0.5 text-[9.5px] leading-[13px] muted" aria-hidden="true">
                  {t('calendar.more', { n: hidden })}
                </span>
              )}
            </div>
          )
        })}
      </div>

      {drag.dragging && pointer && (
        <div
          className={clsx(
            'pointer-events-none fixed z-[70] max-w-[11rem] -translate-x-1/2 truncate rounded-xl px-3 py-1.5 text-xs font-semibold shadow-card ring-2 ring-[rgb(var(--accent))]',
            'bg-[rgb(var(--bg-elevated))]',
          )}
          style={{ left: pointer.x, top: pointer.y - 44 }}
          data-testid="cal-month-preview"
        >
          {drag.dragging.task.title}
          {target && <span className="tnum ml-1.5 font-normal muted">{dayNumber(target)}</span>}
        </div>
      )}
    </div>
  )
}
