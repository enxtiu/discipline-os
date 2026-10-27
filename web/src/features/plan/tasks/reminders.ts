/** Reminder labels: "В момент срока", "За 15 мин", "За день", "Завтра 09:00". */
import i18n from '@/i18n'
import { formatMinutes, relativeDay } from '../format'
import type { Reminder, ReminderIn } from '../types'

/** Relative reminders the editor offers in one tap (minutes before the due/block start). */
export const LEAD_PRESETS = [0, 15, 30, 60, 1440] as const

/** The server keeps at most this many pending reminders per task (`too_many_reminders`). */
export const MAX_REMINDERS = 5

/** «30 min before» — counted from the due time / block start, so it needs a date or a block. */
export function isRelativeReminder(reminder: ReminderIn): reminder is { lead_minutes: number } {
  return 'lead_minutes' in reminder
}

export function leadLabel(lead: number): string {
  if (lead === 0) return i18n.t('task.reminder_at_due')
  if (lead === 1440) return i18n.t('task.reminder_day_before')
  if (lead % 1440 === 0) return i18n.t('task.reminder_days_before', { n: lead / 1440 })
  return i18n.t('task.reminder_before', { duration: formatMinutes(lead) })
}

/** Short chip text for a preset lead: "В момент", "15 мин", "1 ч", "1 день". */
export function leadChip(lead: number): string {
  if (lead === 0) return i18n.t('task.reminder_chip_at_due')
  if (lead === 1440) return i18n.t('task.reminder_chip_day')
  return formatMinutes(lead)
}

export function reminderInLabel(reminder: ReminderIn, today: string | undefined): string {
  if ('lead_minutes' in reminder) return leadLabel(reminder.lead_minutes)
  return `${relativeDay(reminder.date, today)} ${reminder.time}`
}

export function reminderOutLabel(reminder: Reminder, today: string | undefined): string {
  if (reminder.lead_minutes != null) return leadLabel(reminder.lead_minutes)
  if (reminder.date && reminder.time) return `${relativeDay(reminder.date, today)} ${reminder.time}`
  return ''
}

/** Pending reminders as the API input (`reminders` replaces the pending ones). */
export function toReminderIn(reminders: Reminder[]): ReminderIn[] {
  const out: ReminderIn[] = []
  for (const reminder of reminders) {
    if (reminder.status !== 'pending') continue
    if (reminder.lead_minutes != null) out.push({ lead_minutes: reminder.lead_minutes })
    else if (reminder.date && reminder.time) out.push({ date: reminder.date, time: reminder.time })
  }
  return out
}

export function sameReminder(a: ReminderIn, b: ReminderIn): boolean {
  if ('lead_minutes' in a && 'lead_minutes' in b) return a.lead_minutes === b.lead_minutes
  if ('date' in a && 'date' in b) return a.date === b.date && a.time === b.time
  return false
}
