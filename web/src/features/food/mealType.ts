/** The meal a log made now most likely belongs to (by the device's hour; the user can change it). */
import type { MealType } from '@/api/types'

export function guessMealType(): MealType {
  const hour = new Date().getHours()
  if (hour < 11) return 'breakfast'
  if (hour < 16) return 'lunch'
  if (hour < 22) return 'dinner'
  return 'snack'
}
