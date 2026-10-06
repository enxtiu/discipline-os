/** Formatting and input parsing for the health screens. */
import i18n from '@/i18n'
import { number } from '@/lib/format'

/** "80,4" / "80.4" / " 80 " → 80.4; "10,000" (en grouping) → 10000; empty or junk → null. */
export function decimal(value: string): number | null {
  const compact = value.replace(/[\s\u00a0\u202f]/g, '')
  const cleaned = /^-?\d{1,3}(,\d{3})+(\.\d+)?$/.test(compact) ? compact.replace(/,/g, '') : compact.replace(',', '.')
  if (cleaned === '') return null
  const parsed = Number(cleaned)
  return Number.isFinite(parsed) ? parsed : null
}

/** "8 342" → 8342 (whole numbers only). */
export function wholeNumber(value: string): number | null {
  const parsed = decimal(value)
  return parsed === null ? null : Math.round(parsed)
}

export function kg(value: number | null | undefined): string {
  return value == null ? '—' : number(value, 1)
}

/** Signed change, e.g. "−0,6" / "+1,2"; null when there is nothing to compare. */
export function signedKg(value: number | null | undefined): string | null {
  if (value == null) return null
  const rounded = Math.round(value * 10) / 10
  if (rounded === 0) return '0'
  return `${rounded > 0 ? '+' : '−'}${number(Math.abs(rounded), 1)}`
}

/** 465 → "7 ч 45 мин" / "7 h 45 min". */
export function sleepDuration(minutes: number | null | undefined): string {
  if (minutes == null) return '—'
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  const h = i18n.t('common.hours_short')
  const m = i18n.t('common.min')
  if (hours === 0) return `${rest} ${m}`
  return rest === 0 ? `${hours} ${h}` : `${hours} ${h} ${rest} ${m}`
}

/** 465 → "7:45" (compact rows). */
export function sleepClock(minutes: number): string {
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`
}

export function km(meters: number | null | undefined): string | null {
  if (!meters) return null
  return `${number(meters / 1000, 1)} ${i18n.t('common.km')}`
}

/** Minutes between two "HH:MM" wall-clock times, crossing midnight if needed (preview only). */
export function previewMinutes(bed: string, wake: string): number | null {
  const parse = (value: string) => {
    const [hh, mm] = value.split(':').map(Number)
    return Number.isFinite(hh) && Number.isFinite(mm) ? hh * 60 + mm : null
  }
  const start = parse(bed)
  const end = parse(wake)
  if (start === null || end === null || start === end) return null
  return end > start ? end - start : 24 * 60 - start + end
}
