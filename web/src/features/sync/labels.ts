/**
 * Words for the sync sheet: what a queued write is («Приём пищи · добавление»),
 * why the server refused it, and the two sides of a conflict in plain words.
 */
import i18n from '@/i18n'
import { ApiError } from '@/api/client'
import { errorMessage } from '@/lib/errors'
import { entityId, type QueuedRequest } from '@/lib/offlineQueue'
import { describeRepeat } from '@/features/plan/tasks/rrule'
import { reminderInLabel } from '@/features/plan/tasks/reminders'
import type { RecurrencePreset, ReminderIn } from '@/features/plan/types'

export type QueueKind =
  | 'meal'
  | 'water'
  | 'task'
  | 'project'
  | 'area'
  | 'resource'
  | 'tag'
  | 'timer'
  | 'time_entry'
  | 'journal'
  | 'set'
  | 'workout'
  | 'steps'
  | 'weight'
  | 'sleep'
  | 'other'

const KIND_RULES: [RegExp, QueueKind][] = [
  [/^\/v1\/nutrition\/meals/, 'meal'],
  [/^\/v1\/nutrition\/water/, 'water'],
  [/^\/v1\/planner\/tasks/, 'task'],
  [/^\/v1\/planner\/projects/, 'project'],
  [/^\/v1\/planner\/areas/, 'area'],
  [/^\/v1\/planner\/resources/, 'resource'],
  [/^\/v1\/planner\/tags/, 'tag'],
  [/^\/v1\/planner\/timer/, 'timer'],
  [/^\/v1\/planner\/time-entries/, 'time_entry'],
  [/^\/v1\/planner\/journal/, 'journal'],
  [/^\/v1\/training\/workouts\/[^/]+\/sets/, 'set'],
  [/^\/v1\/training/, 'workout'],
  [/^\/v1\/health\/steps/, 'steps'],
  [/^\/v1\/health\/weight/, 'weight'],
  [/^\/v1\/health\/sleep/, 'sleep'],
]

/** What the write is about, from its path (rows queued by older builds carry a coarse `kind`). */
export function queueKind(entry: Pick<QueuedRequest, 'path'>): QueueKind {
  return KIND_RULES.find(([pattern]) => pattern.test(entry.path))?.[1] ?? 'other'
}

export type QueueAction = 'create' | 'edit' | 'delete' | 'complete' | 'reopen' | 'start' | 'stop' | 'copy'

export function queueAction(entry: Pick<QueuedRequest, 'method' | 'path'>): QueueAction {
  if (entry.method === 'DELETE') return 'delete'
  if (/\/complete$/.test(entry.path)) return 'complete'
  if (/\/reopen$/.test(entry.path)) return 'reopen'
  if (/\/timer\/start$/.test(entry.path)) return 'start'
  if (/\/timer\/stop$/.test(entry.path)) return 'stop'
  if (/\/meals\/copy$/.test(entry.path)) return 'copy'
  return entry.method === 'POST' ? 'create' : 'edit'
}

export function kindLabel(kind: QueueKind): string {
  return i18n.t(`sync.kind.${kind}`)
}

export function actionLabel(action: QueueAction): string {
  return i18n.t(`sync.action.${action}`)
}

/** «сегодня, 14:05», «вчера, 23:40», «12 окт., 09:15» — when the write was made (device clock: it is the tap). */
export function madeAt(timestamp: number, now = Date.now()): string {
  const at = new Date(timestamp)
  const minutes = Math.floor((now - timestamp) / 60_000)
  if (minutes < 1) return i18n.t('sync.made_now')
  if (minutes < 60) return i18n.t('sync.made_minutes', { n: minutes })
  const language = i18n.resolvedLanguage ?? 'ru'
  const time = at.toLocaleTimeString(language, { hour: '2-digit', minute: '2-digit' })
  const day = new Date(now)
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (sameDay(at, day)) return i18n.t('sync.made_today', { time })
  const yesterday = new Date(now - 86_400_000)
  if (sameDay(at, yesterday)) return i18n.t('sync.made_yesterday', { time })
  return `${at.toLocaleDateString(language, { day: 'numeric', month: 'short' })}, ${time}`
}

/** «30 с», «2 мин» — until the next automatic retry. */
export function countdown(ms: number): string {
  const seconds = Math.max(1, Math.ceil(ms / 1000))
  if (seconds < 60) return i18n.t('sync.seconds', { n: seconds })
  return i18n.t('sync.minutes', { n: Math.ceil(seconds / 60) })
}

/** The server's reason in plain words. */
export function failureReason(entry: QueuedRequest): string {
  const error = entry.error
  if (!error) return ''
  if (error.status === 404) {
    const details = (error.details ?? {}) as { id?: unknown }
    const own = entityId(entry)
    const missing = typeof details.id === 'string' ? details.id.toLowerCase() : null
    // the row this write changes is gone (another device deleted it)
    if ((own && missing === own) || (entry.method !== 'POST' && !missing)) return i18n.t('sync.reason.deleted_elsewhere')
    return i18n.t('sync.reason.missing')
  }
  if (error.status === 403) return i18n.t('sync.reason.forbidden')
  if (i18n.exists(`errors.${error.code}`)) {
    const text = errorMessage(new ApiError(error.status, { code: error.code, message: error.message }))
    return error.status === 422 || error.status === 400 ? i18n.t('sync.reason.invalid', { reason: text }) : text
  }
  return i18n.t('sync.reason.refused', { status: error.status })
}

// --------------------------------------------------------------------------- //
// Conflicts: the field names and both values in plain words
// --------------------------------------------------------------------------- //
export function fieldLabel(field: string): string {
  const key = `sync.field.${field}`
  return i18n.exists(key) ? i18n.t(key) : field
}

const PRESETS = ['daily', 'weekdays', 'weekly', 'biweekly', 'monthly', 'yearly']

type Resolve = (id: string) => string | null

function day(value: string): string {
  const date = new Date(`${value.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(i18n.resolvedLanguage ?? 'ru', { day: 'numeric', month: 'short', year: 'numeric' })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

/** One side of a conflicting field, as words. `value` is the request's or the response's representation. */
export function describeValue(field: string, value: unknown, resolve?: Resolve): string {
  const empty = i18n.t('sync.value_empty')
  if (value === null || value === undefined || value === '') return empty
  if (typeof value === 'boolean') return value ? i18n.t('sync.value_yes') : i18n.t('sync.value_no')
  if (Array.isArray(value)) {
    if (value.length === 0) return empty
    if (field === 'tags') return value.map((tag) => `#${isRecord(tag) ? String(tag.name) : String(tag)}`).join(' ')
    if (field === 'reminders') {
      return value
        .filter((item) => isRecord(item) && (item.status === undefined || item.status === 'pending'))
        .map((item) => {
          const reminder = item as Record<string, unknown>
          const input: ReminderIn =
            reminder.lead_minutes != null
              ? { lead_minutes: Number(reminder.lead_minutes) }
              : { date: String(reminder.date), time: String(reminder.time) }
          return reminderInLabel(input, undefined)
        })
        .join(', ') || empty
    }
    return value.map(String).join(', ')
  }
  if (isRecord(value)) {
    if (typeof value.name === 'string') return value.name // a project/area reference
    if (typeof value.start === 'string' && typeof value.end === 'string') {
      return `${typeof value.date === 'string' ? `${day(value.date)}, ` : ''}${value.start}–${value.end}`
    }
    return JSON.stringify(value)
  }
  const text = String(value)
  switch (field) {
    case 'priority':
      return `P${text}`
    case 'status':
      return i18n.exists(`task.status.${text}`) ? i18n.t(`task.status.${text}`) : i18n.t(`plan.project_status.${text}`, text)
    case 'due_date':
    case 'recurrence_until':
    case 'deadline':
    case 'start_date':
    case 'date':
      return day(text)
    case 'recurrence_rule':
      return PRESETS.includes(text) ? describeRepeat(text, text as RecurrencePreset) : describeRepeat(text)
    case 'estimate_minutes':
    case 'weekly_target_minutes':
      return i18n.t('sync.value_minutes', { n: text })
    case 'project_id':
    case 'area_id':
    case 'parent_id':
    case 'task_id':
      return resolve?.(text) ?? i18n.t('sync.value_other')
    default:
      return text.length > 280 ? `${text.slice(0, 280)}…` : text
  }
}

/** The response's value of an input field (`project_id` → the project reference, …). */
export function currentValue(field: string, current: unknown): unknown {
  if (!isRecord(current)) return null
  switch (field) {
    case 'project_id':
      return current.project ?? null
    case 'area_id':
      return current.area ?? null
    case 'task_id':
      return isRecord(current.task) ? { name: current.task.title } : null
    case 'parent_id':
      return current.parent_title ? { name: current.parent_title } : (current.parent_id ?? null)
    default:
      return current[field] ?? null
  }
}
