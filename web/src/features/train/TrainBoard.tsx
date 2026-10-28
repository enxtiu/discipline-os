/**
 * Тренировки, the pieces a theme may add (`SkinTraits.boardContent`, Ffin-Train): the planned
 * workout as a card with its numbered exercises («4×8 · 80 кг»), and the week header's date range.
 * The numbers are the template's own targets; «Эволюция» keeps its one-line list.
 */
import { useTranslation } from 'react-i18next'

import type { Template } from '@/api/types'
import i18n from '@/i18n'
import { number } from '@/lib/format'
import { exerciseName } from './format'

export function PlanExercises({ template }: { template: Template }) {
  const { t } = useTranslation()
  return (
    <ol aria-label={t('board.exercises')} className="sk-plan-list" data-testid="plan-exercises">
      {template.items.map((item, index) => {
        const reps = item.target_reps ?? item.target_reps_max ?? item.target_reps_min
        const sets =
          reps != null
            ? item.target_weight_kg
              ? t('board.sets_reps_kg', { sets: item.target_sets, reps, kg: number(item.target_weight_kg, item.target_weight_kg % 1 ? 1 : 0) })
              : t('board.sets_reps', { sets: item.target_sets, reps })
            : ''
        return (
          <li key={item.id} className="flex items-center gap-2 text-sm leading-[18px]">
            <span aria-hidden="true" className="sk-plan-num">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1 truncate font-semibold">{exerciseName(item.exercise)}</span>
            {sets && <span className="tnum whitespace-nowrap font-bold">{sets}</span>}
          </li>
        )
      })}
    </ol>
  )
}

/** «26 окт.» — a day of the week range, without the weekday. */
export function dayRange(day: string): string {
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(
    new Date(`${day}T12:00:00Z`),
  )
}
