/**
 * Task editor fields: priority, status, due date/time, time block, estimate.
 * Dates are local `YYYY-MM-DD` keys and times `HH:MM` wall-clock strings —
 * the server converts them with the user's timezone. The only arithmetic here
 * is stepping a day key (quick chips) and adding minutes to a wall-clock time
 * (a block's default end), never a timezone conversion.
 */
import { useEffect, useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, Input } from '@/components/ui'
import { IconClock } from '@/components/ui/icons'
import { addDays } from '@/lib/format'
import { blockLabel, dueLabel, formatMinutes, priorityLabel } from '../format'
import type { BlockIn, Priority, TaskStatusIn } from '../types'
import { PRIORITY_TEXT } from './priority'
import { Chip } from './ui'

export function FieldSection({
  label,
  htmlFor,
  error,
  hint,
  testId,
  children,
}: {
  label: ReactNode
  htmlFor?: string
  error?: string
  hint?: ReactNode
  testId?: string
  children: ReactNode
}) {
  return (
    <section className="space-y-2" data-testid={testId}>
      {htmlFor ? (
        <label htmlFor={htmlFor} className="label block">
          {label}
        </label>
      ) : (
        <h3 className="label">{label}</h3>
      )}
      {children}
      {error ? <p className="text-xs text-rose-400">{error}</p> : hint ? <p className="text-xs muted">{hint}</p> : null}
    </section>
  )
}

/** `"18:00"` + 90 → `"19:30"` on the wall clock (wraps past midnight). */
export function addToTime(time: string, minutes: number): string {
  const [h, m] = time.split(':').map(Number)
  const total = (((h * 60 + m + minutes) % 1440) + 1440) % 1440
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

/** Minutes of a block from its wall-clock ends (end ≤ start = the next day, as the server reads it). */
export function blockMinutes(block: Pick<BlockIn, 'start' | 'end'>): number {
  const toMin = (value: string) => {
    const [h, m] = value.split(':').map(Number)
    return h * 60 + m
  }
  const diff = toMin(block.end) - toMin(block.start)
  return diff > 0 ? diff : diff + 1440
}

// --------------------------------------------------------------------------- //
// Priority
// --------------------------------------------------------------------------- //
const PRIORITY_ACTIVE: Record<Priority, string> = {
  1: 'border-rose-400 bg-rose-400/15',
  2: 'border-amber-400 bg-amber-400/15',
  3: 'border-violet-300 bg-violet-300/15',
  4: 'border-[rgb(var(--accent))]/50 bg-[rgb(var(--accent))]/10',
}

export function PriorityPicker({ value, onChange }: { value: Priority; onChange: (next: Priority) => void }) {
  const { t } = useTranslation()
  return (
    <div role="radiogroup" aria-label={t('task.priority')} className="grid grid-cols-4 gap-2">
      {([1, 2, 3, 4] as Priority[]).map((priority) => (
        <button
          key={priority}
          type="button"
          role="radio"
          aria-checked={value === priority}
          aria-label={t('task.priority_n', { label: priorityLabel(priority) })}
          onClick={() => onChange(priority)}
          className={clsx(
            'chip min-h-tap justify-center font-semibold',
            PRIORITY_TEXT[priority],
            value === priority && PRIORITY_ACTIVE[priority],
            value === priority && priority === 4 && 'text-[rgb(var(--text))]',
          )}
          data-testid={`priority-${priority}`}
        >
          {priorityLabel(priority)}
        </button>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Status
// --------------------------------------------------------------------------- //
const STATUSES: TaskStatusIn[] = ['open', 'in_progress', 'waiting', 'blocked']

export function StatusPicker({
  value,
  onChange,
}: {
  value: TaskStatusIn | null
  onChange: (next: TaskStatusIn) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t('task.status_label')}>
        {STATUSES.map((status) => (
          <Chip
            key={status}
            role="radio"
            aria-checked={value === status}
            aria-pressed={undefined}
            active={value === status}
            onClick={() => onChange(status)}
            data-testid={`status-${status}`}
          >
            {t(`task.status.${status}`)}
          </Chip>
        ))}
      </div>
      {value === null && <p className="text-xs muted">{t('task.status_auto_hint')}</p>}
      {value === 'inbox' && <p className="text-xs muted">{t('task.status_inbox_hint')}</p>}
      {(value === 'done' || value === 'cancelled') && <p className="text-xs muted">{t(`task.status.${value}`)}</p>}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Due date + time
// --------------------------------------------------------------------------- //
export function DueEditor({
  date,
  time,
  today,
  onChange,
}: {
  date: string | null
  time: string | null
  today: string | undefined
  onChange: (date: string | null, time: string | null) => void
}) {
  const { t } = useTranslation()
  const dateId = useId()
  const timeId = useId()
  const tomorrow = today ? addDays(today, 1) : undefined
  const weekFrom = date ?? today
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Chip active={!!today && date === today} disabled={!today} onClick={() => today && onChange(today, time)} data-testid="due-today">
          {t('plan.day.today')}
        </Chip>
        <Chip
          active={!!tomorrow && date === tomorrow}
          disabled={!tomorrow}
          onClick={() => tomorrow && onChange(tomorrow, time)}
          data-testid="due-tomorrow"
        >
          {t('plan.day.tomorrow')}
        </Chip>
        <Chip disabled={!weekFrom} onClick={() => weekFrom && onChange(addDays(weekFrom, 7), time)} data-testid="due-week">
          {t('task.due_plus_week')}
        </Chip>
        {date && (
          <Chip onClick={() => onChange(null, null)} data-testid="due-clear">
            {t('task.due_clear')}
          </Chip>
        )}
      </div>
      <div className="grid grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-2">
        <div className="min-w-0">
          <label htmlFor={dateId} className="sr-only">
            {t('task.due_date')}
          </label>
          <Input
            id={dateId}
            type="date"
            className="tnum"
            value={date ?? ''}
            onChange={(event) => onChange(event.target.value || null, event.target.value ? time : null)}
            data-testid="due-date"
          />
        </div>
        <div className="min-w-0">
          <label htmlFor={timeId} className="sr-only">
            {t('task.due_time')}
          </label>
          <Input
            id={timeId}
            type="time"
            className="tnum"
            value={time ?? ''}
            disabled={!date}
            onChange={(event) => onChange(date, event.target.value || null)}
            data-testid="due-time"
          />
        </div>
      </div>
      {date && (
        <p className="tnum text-xs muted" data-testid="due-label">
          {dueLabel({ due_date: date, due_time: time }, today)}
        </p>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Time block
// --------------------------------------------------------------------------- //
export function BlockEditor({
  block,
  fallbackDate,
  fallbackStart,
  defaultMinutes,
  onChange,
}: {
  block: BlockIn | null
  fallbackDate: string | null | undefined
  fallbackStart: string | null | undefined
  defaultMinutes: number
  onChange: (next: BlockIn | null) => void
}) {
  const { t } = useTranslation()
  const dateId = useId()
  const startId = useId()
  const endId = useId()
  if (!block) {
    return (
      <Button
        variant="secondary"
        size="sm"
        className="min-h-tap"
        icon={<IconClock size={16} />}
        disabled={!fallbackDate}
        onClick={() => {
          if (!fallbackDate) return
          const start = fallbackStart ?? '09:00'
          onChange({ date: fallbackDate, start, end: addToTime(start, defaultMinutes) })
        }}
        data-testid="block-add"
      >
        {t('task.block_add')}
      </Button>
    )
  }
  const minutes = blockMinutes(block)
  const endsNextDay = block.end <= block.start
  return (
    <div className="space-y-2">
      <div>
        <label htmlFor={dateId} className="sr-only">
          {t('task.block_date')}
        </label>
        <Input
          id={dateId}
          type="date"
          className="tnum"
          value={block.date}
          onChange={(event) => event.target.value && onChange({ ...block, date: event.target.value })}
          data-testid="block-date"
        />
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
        <div className="field min-w-0">
          <label htmlFor={startId} className="text-xs muted">
            {t('task.block_start')}
          </label>
          <Input
            id={startId}
            type="time"
            className="tnum"
            value={block.start}
            // moving the start keeps the duration
            onChange={(event) =>
              event.target.value &&
              onChange({ ...block, start: event.target.value, end: addToTime(event.target.value, minutes) })
            }
            data-testid="block-start"
          />
        </div>
        <div className="field min-w-0">
          <label htmlFor={endId} className="text-xs muted">
            {t('task.block_end')}
          </label>
          <Input
            id={endId}
            type="time"
            className="tnum"
            value={block.end}
            onChange={(event) => event.target.value && onChange({ ...block, end: event.target.value })}
            data-testid="block-end"
          />
        </div>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="tnum text-sm" data-testid="block-label">
          {blockLabel({ ...block, ends_next_day: endsNextDay })} · {formatMinutes(minutes)}
        </span>
        <Button variant="ghost" size="sm" className="min-h-tap" onClick={() => onChange(null)} data-testid="block-clear">
          {t('task.block_clear')}
        </Button>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Estimate
// --------------------------------------------------------------------------- //
const ESTIMATES = [15, 30, 45, 60, 90, 120]

export function EstimateEditor({ value, onChange }: { value: number | null; onChange: (next: number | null) => void }) {
  const { t } = useTranslation()
  const inputId = useId()
  const isPreset = value !== null && ESTIMATES.includes(value)
  const [custom, setCustom] = useState(value !== null && !isPreset)
  const [text, setText] = useState(value !== null && !isPreset ? String(value) : '')
  useEffect(() => {
    if (value !== null && !ESTIMATES.includes(value)) {
      setCustom(true)
      setText(String(value))
    }
  }, [value])
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {ESTIMATES.map((minutes) => (
          <Chip
            key={minutes}
            active={value === minutes}
            onClick={() => {
              setCustom(false)
              onChange(value === minutes ? null : minutes)
            }}
            data-testid={`estimate-${minutes}`}
          >
            <span className="tnum">{formatMinutes(minutes)}</span>
          </Chip>
        ))}
        <Chip active={custom} onClick={() => setCustom((current) => !current)} data-testid="estimate-custom">
          {t('task.estimate_custom')}
        </Chip>
      </div>
      {custom && (
        <div className="flex items-center gap-2">
          <label htmlFor={inputId} className="sr-only">
            {t('task.estimate_minutes')}
          </label>
          <Input
            id={inputId}
            type="number"
            inputMode="numeric"
            min={1}
            max={1440}
            className="w-28 tnum"
            value={text}
            placeholder={t('common.min')}
            onChange={(event) => {
              setText(event.target.value)
              const minutes = Math.round(Number(event.target.value))
              if (event.target.value === '') onChange(null)
              else if (Number.isFinite(minutes) && minutes >= 1 && minutes <= 1440) onChange(minutes)
            }}
            data-testid="estimate-input"
          />
          <span className="text-sm muted">{t('common.min')}</span>
        </div>
      )}
    </div>
  )
}
