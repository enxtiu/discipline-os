/**
 * Formatting helpers.
 *
 * Everything the server sends is UTC; everything the user sees is local. Day
 * boundaries always use the *user's* timezone (from settings, falling back to
 * the device) so a "day" in Phuket is not a day in UTC.
 */
import i18n from '@/i18n'

export function timezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** `YYYY-MM-DD` in the given timezone (default: the device's). */
export function isoDay(value: Date = new Date(), tz: string = timezone()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(value)
  return parts // en-CA yields YYYY-MM-DD
}

export function today(tz?: string): string {
  return isoDay(new Date(), tz ?? timezone())
}

export function addDays(day: string, delta: number): string {
  const date = new Date(`${day}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + delta)
  return date.toISOString().slice(0, 10)
}

export function time(value: string | Date | null | undefined, tz?: string): string {
  const date = toDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: tz ?? timezone(),
  }).format(date)
}

export function timeShort(value: string | Date | null | undefined, tz?: string): string {
  const date = toDate(value)
  if (!date) return '—'
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: tz ?? timezone(),
  }).format(date)
}

export function dayLabel(day: string | Date, style: 'short' | 'long' = 'short'): string {
  const date = typeof day === 'string' ? new Date(`${day}T12:00:00Z`) : day
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', {
    weekday: style === 'long' ? 'long' : 'short',
    day: 'numeric',
    month: style === 'long' ? 'long' : 'short',
  }).format(date)
}

export function relativeTime(value: string | Date | null | undefined): string {
  const date = toDate(value)
  if (!date) return '—'
  const diffMs = Date.now() - date.getTime()
  const minutes = Math.round(diffMs / 60_000)
  if (Math.abs(minutes) < 1) return i18n.t('common.just_now')
  if (Math.abs(minutes) < 60) return i18n.t('common.minutes_ago', { count: minutes })
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return i18n.t('common.hours_ago', { count: hours })
  const days = Math.round(hours / 24)
  return i18n.t('common.days_ago', { count: days })
}

export function number(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const locale = i18n.language === 'en' ? 'en-GB' : 'ru-RU'
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

export function kcal(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${number(Math.round(value))} ${i18n.t('common.kcal')}`
}

export function grams(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? '—' : `${number(value, digits)} ${i18n.t('common.g')}`
}

export function millilitres(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${number(Math.round(value))} ${i18n.t('common.ml')}`
}

export function kilograms(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? '—' : `${number(value, digits)} ${i18n.t('common.kg')}`
}

/** 1 234 567 → «1,2 МБ» (bytes, KB, MB in the UI language). */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${number(bytes)} ${i18n.t('common.size_b')}`
  if (bytes < 1024 * 1024) return `${number(bytes / 1024, bytes < 10 * 1024 ? 1 : 0)} ${i18n.t('common.size_kb')}`
  return `${number(bytes / (1024 * 1024), 1)} ${i18n.t('common.size_mb')}`
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${number(value, digits)}%`
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** `"18:00"` -> `"18:00:00"` (the API expects a full time value). */
export function toApiTime(value: string): string {
  return value.length === 5 ? `${value}:00` : value
}

/** `"18:00:00"` -> `"18:00"` for `<input type="time">`. */
export function fromApiTime(value: string | null | undefined): string {
  if (!value) return ''
  return value.slice(0, 5)
}

/** Days until a date, in the user's timezone (negative = overdue). */
export function daysUntil(target: string | Date): number {
  const date = typeof target === 'string' ? new Date(`${target}T12:00:00Z`) : target
  const now = new Date(`${today()}T12:00:00Z`)
  return Math.round((date.getTime() - now.getTime()) / 86_400_000)
}
