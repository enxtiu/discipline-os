/** Training display helpers: names in the UI language, durations, set summaries. */
import i18n from '@/i18n'
import type { Exercise, PersonalRecord, PreviousSet, WorkoutSet } from '@/api/types'
import { number } from '@/lib/format'

export function exerciseName(exercise: Pick<Exercise, 'name_ru' | 'name_en'> | null | undefined): string {
  if (!exercise) return ''
  return i18n.language === 'en'
    ? (exercise.name_en ?? exercise.name_ru ?? '')
    : (exercise.name_ru ?? exercise.name_en ?? '')
}

export function recordExerciseName(record: PersonalRecord): string {
  return exerciseName({ name_ru: record.exercise_name_ru ?? null, name_en: record.exercise_name_en ?? null })
}

/** 1:05:09 / 23:41 — a running clock. */
export function clock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const mm = String(m).padStart(h ? 2 : 1, '0')
  const ss = String(s).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

/** "52 мин" / "1 ч 05 мин" — a finished duration. */
export function minutesLabel(totalSeconds: number | null | undefined): string {
  if (!totalSeconds) return '—'
  const minutes = Math.round(totalSeconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (!h) return `${m} ${i18n.t('common.min')}`
  return `${h} ${i18n.t('common.hours_short')} ${String(m).padStart(2, '0')} ${i18n.t('common.min')}`
}

/** 80 / 82,5 / 1,25 — weights keep the decimals people load. */
export function weight(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const digits = Number.isInteger(value) ? 0 : Number.isInteger(value * 10) ? 1 : 2
  return number(value, digits)
}

export function km(meters: number | null | undefined): string {
  if (!meters) return '—'
  return `${number(meters / 1000, meters % 1000 ? 2 : 0)} ${i18n.t('train.unit.km')}`
}

/** One set as people say it: "80 × 8", "×12", "1:30", "5 км · 28:10". */
export function setSummary(row: Pick<WorkoutSet | PreviousSet, 'weight_kg' | 'reps' | 'duration_s' | 'distance_m'>): string {
  if (row.distance_m) return `${km(row.distance_m)}${row.duration_s ? ` ${clock(row.duration_s)}` : ''}`
  if (row.duration_s && !row.reps) return clock(row.duration_s)
  if (row.weight_kg) return `${weight(row.weight_kg)} × ${row.reps ?? 0}`
  if (row.reps) return `× ${row.reps}`
  return '—'
}

export function recordValue(record: Pick<PersonalRecord, 'kind' | 'value' | 'unit' | 'reps'>): string {
  switch (record.kind) {
    case 'weight':
      return `${weight(record.value)} ${i18n.t('common.kg')}${record.reps ? ` × ${record.reps}` : ''}`
    case 'est_1rm':
      return `${weight(Math.round(record.value * 2) / 2)} ${i18n.t('common.kg')}`
    case 'volume':
      return `${number(record.value)} ${i18n.t('common.kg')}`
    case 'reps':
      return `${number(record.value)} ${i18n.t('train.unit.reps')}`
    case 'duration':
      return clock(record.value)
    case 'distance':
      return km(record.value)
    default:
      return number(record.value)
  }
}

export function weekdayShort(weekday: number): string {
  // 2024-01-01 was a Monday
  const date = new Date(Date.UTC(2024, 0, 1 + weekday, 12))
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', { weekday: 'short', timeZone: 'UTC' }).format(date)
}
