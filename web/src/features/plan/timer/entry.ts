/** What a time entry is about, as display text (task › description › project/area). */
import i18n from '@/i18n'
import { blockLabel } from '../format'
import type { TimeEntry } from '../types'

export function describeEntry(entry: Pick<TimeEntry, 'task' | 'description' | 'project' | 'area'>): {
  title: string
  subtitle: string
} {
  const container = entry.project?.name ?? entry.area?.name ?? null
  if (entry.task) {
    return { title: entry.task.title, subtitle: [entry.description, container].filter(Boolean).join(' · ') }
  }
  if (entry.description) return { title: entry.description, subtitle: container ?? '' }
  if (container) return { title: container, subtitle: '' }
  return { title: i18n.t('timer.untitled'), subtitle: '' }
}

/** "09:00–10:30", "23:00–01:00 +1" (local strings from the server). */
export function entryTimes(entry: Pick<TimeEntry, 'start' | 'end' | 'ends_next_day'>): string {
  if (!entry.start) return ''
  if (!entry.end) return `${entry.start}–…`
  return blockLabel({ start: entry.start, end: entry.end, ends_next_day: entry.ends_next_day })
}

/** Whole minutes of a finished entry (`duration_s` is the server's). */
export function entryMinutes(entry: Pick<TimeEntry, 'duration_s'>): number {
  return Math.round((entry.duration_s ?? 0) / 60)
}

/**
 * Milliseconds of an ISO instant from the server ("…:35.266843Z",
 * "…+07:00"). The six fraction digits Python writes are outside the format
 * every engine must parse, so they become milliseconds — rounded up, so that
 * start + 2 h 30 min is never a microsecond short of 2 h 30 min on the server
 * (which floors the seconds). NaN when unreadable.
 */
export function instantMs(iso: string): number {
  const fraction = /\.(\d{3})(\d+)/.exec(iso)
  if (!fraction) return Date.parse(iso)
  const ms = Date.parse(iso.replace(fraction[0], `.${fraction[1]}`))
  return /[1-9]/.test(fraction[2]) ? ms + 1 : ms
}

/**
 * The instant `seconds` after `startedAt`: when a timer is stopped (start +
 * the server's elapsed time) or how long it really ran (the duration sheet).
 * Plain instant arithmetic — no timezone, no device clock reading.
 */
export function instantAfter(startedAt: string, seconds: number): string | null {
  const start = instantMs(startedAt)
  return Number.isNaN(start) ? null : new Date(start + Math.max(0, Math.floor(seconds)) * 1000).toISOString()
}
