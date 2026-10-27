/**
 * Calendar helpers: string arithmetic on the server's `YYYY-MM-DD` day keys and
 * `HH:MM` wall-clock strings. Nothing here turns an instant into a day or a
 * time — which day is "today" comes from the server (`useCounts().data.date`),
 * block times are the local strings the server sent. Labels go through `Intl`
 * with `timeZone: 'UTC'` on a noon-UTC date, so the device timezone can never
 * shift a key to the neighbouring day.
 */
import i18n from '@/i18n'
import { addDays } from '@/lib/format'

export const MINUTES_PER_DAY = 1440
/** Drag and resize snap to quarter hours. */
export const SNAP_MINUTES = 15

const DAY_KEY = /^\d{4}-\d{2}-\d{2}$/

/** A valid `YYYY-MM-DD` key, or null (URL params are user input). */
export function readDayKey(value: string | null | undefined): string | null {
  if (!value || !DAY_KEY.test(value)) return null
  const date = new Date(`${value}T12:00:00Z`)
  return Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value ? null : value
}

/** 0 = Sunday … 6 = Saturday (the convention of `settings.week_start`). */
export function weekday(day: string): number {
  return new Date(`${day}T12:00:00Z`).getUTCDay()
}

/** First day of the week that contains `day`; `weekStart` is `settings.week_start` (0 = Sunday, 1 = Monday). */
export function startOfWeek(day: string, weekStart: number): string {
  return addDays(day, -((weekday(day) - weekStart + 7) % 7))
}

export function startOfMonth(day: string): string {
  return `${day.slice(0, 7)}-01`
}

/** First day of the month `delta` months away from the month of `day`. */
export function addMonths(day: string, delta: number): string {
  const year = Number(day.slice(0, 4))
  const month = Number(day.slice(5, 7)) - 1
  const index = year * 12 + month + delta
  const nextYear = Math.floor(index / 12)
  const nextMonth = index - nextYear * 12 + 1
  return `${String(nextYear).padStart(4, '0')}-${String(nextMonth).padStart(2, '0')}-01`
}

export function endOfMonth(day: string): string {
  return addDays(addMonths(day, 1), -1)
}

/** `count` consecutive keys starting at `from`. */
export function dayRange(from: string, count: number): string[] {
  return Array.from({ length: count }, (_, index) => addDays(from, index))
}

/** Every key of the month of `day`. */
export function monthDays(day: string): string[] {
  const first = startOfMonth(day)
  const last = endOfMonth(day)
  return dayRange(first, Number(last.slice(8)))
}

/** The seven keys of the week of `day`. */
export function weekDays(day: string, weekStart: number): string[] {
  return dayRange(startOfWeek(day, weekStart), 7)
}

/** The 6×7 month grid (42 keys), starting on the week start on/before the 1st. */
export function monthGrid(day: string, weekStart: number): string[] {
  return dayRange(startOfWeek(startOfMonth(day), weekStart), 42)
}

export function sameMonth(a: string, b: string): boolean {
  return a.slice(0, 7) === b.slice(0, 7)
}

// --------------------------------------------------------------------------- //
// Wall-clock minutes
// --------------------------------------------------------------------------- //
/** `"09:30"` → 570 (`"24:00"` → 1440). */
export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':')
  return (Number(h) || 0) * 60 + (Number(m) || 0)
}

/** 570 → `"09:30"`; wraps past midnight (1440 → `"00:00"`, 1500 → `"01:00"`) — what the API expects. */
export function fromMinutes(total: number): string {
  const value = ((Math.round(total) % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}

/** For display while dragging: 1440 → `"24:00"`, later ends get a `+1`. */
export function endLabel(total: number): string {
  if (total === MINUTES_PER_DAY) return '24:00'
  return total > MINUTES_PER_DAY ? `${fromMinutes(total)} +1` : fromMinutes(total)
}

export function snapMinutes(minutes: number, step = SNAP_MINUTES): number {
  return Math.round(minutes / step) * step
}

// --------------------------------------------------------------------------- //
// Labels (the date key is formatted, never converted)
// --------------------------------------------------------------------------- //
function locale(): string {
  return i18n.language === 'en' ? 'en-GB' : 'ru-RU'
}

function at(day: string): Date {
  return new Date(`${day}T12:00:00Z`)
}

function format(day: string, options: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat(locale(), { ...options, timeZone: 'UTC' }).format(at(day))
}

function capitalise(value: string): string {
  return value ? value.charAt(0).toLocaleUpperCase(locale()) + value.slice(1) : value
}

/** "пн" / "Mon". */
export function weekdayShort(day: string): string {
  return format(day, { weekday: 'short' })
}

/** "Вторник, 27 октября". */
export function dayTitle(day: string): string {
  return capitalise(format(day, { weekday: 'long', day: 'numeric', month: 'long' }))
}

/** "27 октября" / "27 October". */
export function dayMonth(day: string): string {
  return format(day, { day: 'numeric', month: 'long' })
}

/** "Октябрь 2026" (the stand-alone month name, not "октября"). */
export function monthTitle(day: string): string {
  return `${capitalise(format(day, { month: 'long' }))} ${Number(day.slice(0, 4))}`
}

/** "27 окт. – 2 нояб." for a week (or any short range). */
export function rangeTitle(from: string, to: string): string {
  const formatter = new Intl.DateTimeFormat(locale(), { day: 'numeric', month: 'short', timeZone: 'UTC' })
  try {
    return formatter.formatRange(at(from), at(to))
  } catch {
    return `${formatter.format(at(from))} – ${formatter.format(at(to))}`
  }
}

/** Day number without a leading zero. */
export function dayNumber(day: string): number {
  return Number(day.slice(8, 10))
}
