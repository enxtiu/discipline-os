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
