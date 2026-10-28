/**
 * The meal type a new log starts with, from the device's clock — the log sheet's and the
 * AI review's default since stages 2–3 (the user can change it there before saving).
 * Stage 10: the one-tap «Повторить» on Сегодня uses the same rule, so a re-logged food
 * lands where the sheet would have put it. A UI default only: the server derives the day.
 */
import type { MealType } from '@/api/types'

export function guessMealType(): MealType {
  const hour = new Date().getHours()
  if (hour < 11) return 'breakfast'
  if (hour < 16) return 'lunch'
  if (hour < 22) return 'dinner'
  return 'snack'
}

const MEAL_TYPES: readonly MealType[] = ['breakfast', 'lunch', 'dinner', 'snack', 'other']

/** A `?meal=` value (a deep link) as a meal type, or undefined. */
export function asMealType(value: string | null | undefined): MealType | undefined {
  return MEAL_TYPES.find((type) => type === value)
}
