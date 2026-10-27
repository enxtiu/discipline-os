/**
 * Stats display helpers: period labels, axis ticks, durations. Dates are the
 * server's local `YYYY-MM-DD` days; they are only *formatted* here (at noon
 * UTC, so no timezone can move them to another day) — never re-bucketed.
 */
import i18n from '@/i18n'
import { addDays, number } from '@/lib/format'
import type { Period, StatsRange } from './types'

const ROMAN = ['I', 'II', 'III', 'IV'] as const

function locale(): string {
  return i18n.language === 'en' ? 'en-GB' : 'ru-RU'
}

/** `YYYY-MM-DD` → a Date at noon UTC (a pure calendar day, formatted with `timeZone: 'UTC'`). */
export function calendarDate(day: string): Date {
  return new Date(`${day}T12:00:00Z`)
}

function format(day: string, options: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat(locale(), { ...options, timeZone: 'UTC' }).format(calendarDate(day))
}

/** "октябрь" → "Октябрь" */
function capitalised(value: string): string {
  return value.charAt(0).toLocaleUpperCase(locale()) + value.slice(1)
}

/** 0 = Sunday … 6 = Saturday (as the server's `week_start`). */
export function weekday(day: string): number {
  return calendarDate(day).getUTCDay()
}

/** The headline of a period: «27 окт. – 2 нояб.», «Октябрь 2026», «IV квартал 2026», «2026». */
export function periodLabel(range: Pick<StatsRange, 'period' | 'from' | 'to'>): string {
  const year = range.from.slice(0, 4)
  switch (range.period) {
    case 'week': {
      const sameYear = range.from.slice(0, 4) === range.to.slice(0, 4)
      const first = format(range.from, { day: 'numeric', month: 'short', ...(sameYear ? {} : { year: 'numeric' }) })
      const last = format(range.to, { day: 'numeric', month: 'short', year: 'numeric' })
      return `${first} – ${last}`
    }
    case 'month':
      return capitalised(format(range.from, { month: 'long', year: 'numeric' }))
    case 'quarter': {
      const quarter = Math.floor((Number(range.from.slice(5, 7)) - 1) / 3)
      return i18n.t('stats.period.quarter_label', { q: ROMAN[quarter], n: quarter + 1, year })
    }
    case 'year':
      return year
  }
}

/** Short day for tooltips and tables: «пн, 27 окт.» */
export function shortDay(day: string): string {
  return format(day, { weekday: 'short', day: 'numeric', month: 'short' })
}

/** Day and month: «27 окт.» */
export function dayMonth(day: string): string {
  return format(day, { day: 'numeric', month: 'short' })
}

/** A week as a range: «27 окт. – 2 нояб.» */
export function weekRange(start: string): string {
  return `${dayMonth(start)} – ${dayMonth(addDays(start, 6))}`
}

/** «пн» — a weekday letter group for the week axis. */
export function weekdayShort(day: string): string {
  return format(day, { weekday: 'short' }).replace('.', '')
}

/** «окт» — a month for the quarter/year axis (no trailing dot). */
export function monthShort(day: string): string {
  return format(day, { month: 'short' }).replace('.', '')
}

/** The x-axis label of one day for the given period. */
export function tickLabel(day: string, period: Period): string {
  switch (period) {
    case 'week':
      return weekdayShort(day)
    case 'month':
      return String(Number(day.slice(8, 10)))
    default:
      return monthShort(day)
  }
}

/** Which days carry an x-axis label: every weekday, every few days of a month, the first of each month. */
export function tickDays(days: string[], period: Period): string[] {
  if (period === 'week') return days
  if (period === 'month') return days.filter((day) => [1, 8, 15, 22, 29].includes(Number(day.slice(8, 10))))
  return days.filter((day) => day.endsWith('-01'))
}

/** 465 → «7 ч 45 мин» · 60 → «1 ч» · 40 → «40 мин». */
export function hoursMinutes(minutes: number | null | undefined): string {
  if (minutes == null) return '—'
  const total = Math.round(minutes)
  const hours = Math.floor(total / 60)
  const rest = total % 60
  const h = i18n.t('common.hours_short')
  const m = i18n.t('common.min')
  if (hours === 0) return `${rest} ${m}`
  return rest === 0 ? `${number(hours)} ${h}` : `${number(hours)} ${h} ${rest} ${m}`
}

/** Axis ticks for hours: 420 → «7 ч». */
export function hoursTick(minutes: number): string {
  return `${number(Math.round((minutes / 60) * 10) / 10, Number.isInteger(minutes / 60) ? 0 : 1)} ${i18n.t('common.hours_short')}`
}

/** Signed kilograms without judgement: «−0,6» / «+1,2» / «0». */
export function signed(value: number, digits = 1): string {
  const rounded = Number(value.toFixed(digits))
  if (rounded === 0) return number(0, digits)
  return `${rounded > 0 ? '+' : '−'}${number(Math.abs(rounded), digits)}`
}

/** Compact axis numbers: 12 500 → «12,5 тыс.» / «12.5K»; small values stay plain. */
export function compact(value: number): string {
  if (Math.abs(value) < 10_000) return number(value)
  return new Intl.NumberFormat(locale(), { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}
