/**
 * Planner formatting helpers (stage 6). Pure display: every date, time and
 * duration here was computed by the server; this only turns it into text.
 */
import i18n from '@/i18n'
import { addDays, dayLabel } from '@/lib/format'
import type { Priority, RecurrencePreset, Task } from './types'

/** 42 → "0:42", 754 → "12:34", 3723 → "1:02:03" (a ticking timer). */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  return h > 0 ? `${h}:${mm}:${String(s).padStart(2, '0')}` : `${mm}:${String(s).padStart(2, '0')}`
}

/** 45 → "45 мин", 90 → "1 ч 30 мин", 120 → "2 ч" (estimates, tracked time). */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes == null) return '—'
  const total = Math.max(0, Math.round(minutes))
  const h = Math.floor(total / 60)
  const m = total % 60
  const hs = i18n.t('common.hours_short')
  const ms = i18n.t('common.min')
  if (h === 0) return `${m} ${ms}`
  return m === 0 ? `${h} ${hs}` : `${h} ${hs} ${m} ${ms}`
}

/**
 * "Сегодня" / "Завтра" / "Вчера" / "пн, 3 нояб." for `date`, relative to the
 * server's `today` (from `useCounts().data.date` or a view's `date`).
 */
export function relativeDay(date: string, today: string | undefined): string {
  if (today) {
    if (date === today) return i18n.t('plan.day.today')
    if (date === addDays(today, 1)) return i18n.t('plan.day.tomorrow')
    if (date === addDays(today, -1)) return i18n.t('plan.day.yesterday')
  }
  return dayLabel(date)
}

/** Due label of a task: "Завтра 18:00", "пн, 3 нояб.", "" when undated. */
export function dueLabel(task: Pick<Task, 'due_date' | 'due_time'>, today: string | undefined): string {
  if (!task.due_date) return ''
  const day = relativeDay(task.due_date, today)
  return task.due_time ? `${day} ${task.due_time}` : day
}

/** "18:00–19:30" for a time block (`+1` marks a block ending the next day). */
export function blockLabel(block: { start: string; end: string; ends_next_day?: boolean } | null | undefined): string {
  if (!block) return ''
  return `${block.start}–${block.end}${block.ends_next_day ? ' +1' : ''}`
}

/**
 * Priority styles. Literal class strings (Tailwind purges anything built at
 * runtime) and only palette colours the design system already uses.
 */
export const PRIORITY_STYLES: Record<Priority, { text: string; ring: string; dot: string }> = {
  1: { text: 'text-rose-400', ring: 'border-rose-400', dot: 'bg-rose-400' },
  // amber/violet text is unreadable on white: the light theme takes the darker tokens
  2: { text: 'text-[rgb(var(--warn))]', ring: 'border-amber-400', dot: 'bg-amber-400' },
  3: {
    text: 'text-violet-300 [[data-theme=light]_&]:text-[rgb(var(--accent-2))]',
    ring: 'border-violet-300',
    dot: 'bg-violet-300',
  },
  4: { text: 'text-[rgb(var(--text-muted))]', ring: 'border-[rgb(var(--border))]', dot: 'bg-[rgb(var(--border))]' },
}

/** "P1" … "P4". */
export function priorityLabel(priority: Priority): string {
  return `P${priority}`
}

/** Localised name of a recurrence preset; custom rules show "custom". */
export function recurrenceLabel(preset: RecurrencePreset | null | undefined): string {
  if (!preset) return ''
  return i18n.t(`plan.recurrence.${preset}`)
}
