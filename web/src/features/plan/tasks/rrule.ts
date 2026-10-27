/**
 * The small RRULE subset the custom-repeat builder can express:
 * `FREQ=DAILY|WEEKLY|MONTHLY|YEARLY`, `INTERVAL`, `BYDAY` (plain weekdays) and
 * `BYMONTHDAY` (1–31). Anything else (COUNT, BYSETPOS, "1MO"…) is shown as the
 * raw rule and left untouched unless the user builds a new one. The server
 * validates every rule (`invalid_rrule`) and owns the expansion.
 */
import i18n from '@/i18n'
import { recurrenceLabel } from '../format'
import type { RecurrencePreset } from '../types'

export type Freq = 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'YEARLY'
export const WEEKDAYS = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'] as const
export type Weekday = (typeof WEEKDAYS)[number]

/** Preset keys the API accepts in place of an RRULE. */
export const PRESETS = ['daily', 'weekdays', 'weekly', 'biweekly', 'monthly', 'yearly'] as const
export type PresetKey = (typeof PRESETS)[number]

export function isPresetKey(value: string | null | undefined): value is PresetKey {
  return !!value && (PRESETS as readonly string[]).includes(value)
}

export interface RuleParts {
  freq: Freq
  interval: number
  byday: Weekday[]
  bymonthday: number | null
}

export const DEFAULT_PARTS: RuleParts = { freq: 'WEEKLY', interval: 1, byday: [], bymonthday: null }

const FREQS: Freq[] = ['DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY']
const ALLOWED_KEYS = new Set(['FREQ', 'INTERVAL', 'BYDAY', 'BYMONTHDAY', 'WKST'])

export function buildRule(parts: RuleParts): string {
  const out = [`FREQ=${parts.freq}`]
  if (parts.interval > 1) out.push(`INTERVAL=${Math.floor(parts.interval)}`)
  if (parts.freq === 'WEEKLY' && parts.byday.length > 0) {
    out.push(`BYDAY=${WEEKDAYS.filter((day) => parts.byday.includes(day)).join(',')}`)
  }
  if (parts.freq === 'MONTHLY' && parts.bymonthday) out.push(`BYMONTHDAY=${parts.bymonthday}`)
  return out.join(';')
}

/** Parts of a rule the builder can show, or null when it cannot express it. */
export function parseRule(rule: string | null | undefined): RuleParts | null {
  if (!rule) return null
  const text = rule.trim().replace(/^RRULE:/i, '')
  const map = new Map<string, string>()
  for (const part of text.split(';')) {
    const [key, value] = part.split('=')
    if (!key || !value) return null
    map.set(key.trim().toUpperCase(), value.trim().toUpperCase())
  }
  for (const key of map.keys()) if (!ALLOWED_KEYS.has(key)) return null
  const freq = map.get('FREQ') as Freq | undefined
  if (!freq || !FREQS.includes(freq)) return null
  const interval = map.has('INTERVAL') ? Number(map.get('INTERVAL')) : 1
  if (!Number.isInteger(interval) || interval < 1 || interval > 365) return null
  const byday = (map.get('BYDAY')?.split(',') ?? []).map((day) => day.trim())
  if (byday.some((day) => !(WEEKDAYS as readonly string[]).includes(day))) return null
  const monthday = map.has('BYMONTHDAY') ? Number(map.get('BYMONTHDAY')) : null
  if (monthday !== null && (!Number.isInteger(monthday) || monthday < 1 || monthday > 31)) return null
  return { freq, interval, byday: byday as Weekday[], bymonthday: monthday }
}

/** "пн" … "вс" / "Mon" … "Sun" from the platform's locale data (2024-01-01 was a Monday). */
export function weekdayShort(day: Weekday): string {
  const index = WEEKDAYS.indexOf(day)
  const locale = i18n.language === 'en' ? 'en-GB' : 'ru-RU'
  return new Intl.DateTimeFormat(locale, { weekday: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, 1 + index)))
}

/** Human summary of a repeat: the preset name, or e.g. "Каждые 2 нед.: пн, чт". */
export function describeRepeat(rule: string | null | undefined, preset?: RecurrencePreset | null): string {
  if (!rule) return ''
  if (preset && preset !== 'custom') return recurrenceLabel(preset)
  if (isPresetKey(rule)) return recurrenceLabel(rule)
  const parts = parseRule(rule)
  if (!parts) return i18n.t('task.repeat_raw', { rule })
  const n = parts.interval
  const base =
    parts.freq === 'DAILY'
      ? n === 1
        ? i18n.t('plan.recurrence.daily')
        : i18n.t('task.repeat_every_days', { n })
      : parts.freq === 'WEEKLY'
        ? n === 1
          ? i18n.t('plan.recurrence.weekly')
          : i18n.t('task.repeat_every_weeks', { n })
        : parts.freq === 'MONTHLY'
          ? n === 1
            ? i18n.t('plan.recurrence.monthly')
            : i18n.t('task.repeat_every_months', { n })
          : n === 1
            ? i18n.t('plan.recurrence.yearly')
            : i18n.t('task.repeat_every_years', { n })
  if (parts.freq === 'WEEKLY' && parts.byday.length > 0) {
    return i18n.t('task.repeat_on', { base, days: parts.byday.map(weekdayShort).join(', ') })
  }
  if (parts.freq === 'MONTHLY' && parts.bymonthday) {
    return i18n.t('task.repeat_monthday', { base, day: parts.bymonthday })
  }
  return base
}
