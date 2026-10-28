/**
 * One-tap re-log of a recent food or recipe (stage 10, the quick add's «Недавнее»): the same
 * write the log sheet makes, with the grams last used — nothing the server decides is computed
 * here, the preview only mirrors the sheet's numbers while the write waits offline.
 */
import { useTranslation } from 'react-i18next'

import type { Food, Recent, Recipe } from '@/api/types'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { number } from '@/lib/format'
import { guessMealType } from './mealType'
import { macrosFor, useLogMeal } from './useNutrition'

export interface RecentChoice {
  recent: Recent
  name: string
  grams: number
  kcal: number
}

/** The recents that can be logged again, with the name, grams and kcal they would log. */
export function recentChoices(recents: Recent[] | undefined, language: string): RecentChoice[] {
  const rows: RecentChoice[] = []
  for (const recent of recents ?? []) {
    if (recent.food) {
      const food = recent.food as Food
      const grams = Math.round(recent.last_grams ?? food.default_serving_g ?? 100)
      const name = language === 'en' ? (food.name_en ?? food.name_ru ?? '') : (food.name_ru ?? food.name_en ?? '')
      rows.push({ recent, name, grams, kcal: Math.round(macrosFor({ kind: 'food', food }, grams).kcal) })
    } else if (recent.recipe) {
      const recipe = recent.recipe as unknown as Recipe
      const grams = Math.round(recent.last_grams ?? recipe.portion_size_g ?? 100)
      rows.push({ recent, name: recipe.name, grams, kcal: Math.round(macrosFor({ kind: 'recipe', recipe }, grams).kcal) })
    }
  }
  return rows
}

export function useRelog() {
  const { t } = useTranslation()
  const toast = useToast()
  const logMeal = useLogMeal()
  return (choice: RecentChoice, onDone?: () => void) => {
    const { recent, name, grams } = choice
    const target = recent.food
      ? ({ kind: 'food', food: recent.food as Food } as const)
      : ({ kind: 'recipe', recipe: recent.recipe as unknown as Recipe } as const)
    const shown = macrosFor(target, grams)
    logMeal.mutate(
      {
        id: crypto.randomUUID(),
        meal_type: guessMealType(),
        source: 'recent',
        items: [
          target.kind === 'food'
            ? { food_id: target.food.id, grams, match_method: 'manual' }
            : { recipe_id: target.recipe.id, grams, match_method: 'manual' },
        ],
        preview: {
          name,
          kcal: shown.kcal,
          protein_g: shown.protein,
          fat_g: shown.fat,
          carbs_g: shown.carbs,
          fiber_g: shown.fiber,
          items: [{ name, grams, kcal: shown.kcal, protein_g: shown.protein, fat_g: shown.fat, carbs_g: shown.carbs, fiber_g: shown.fiber }],
        },
      },
      {
        onSuccess: (result) => {
          if (result.queued) toast.info(t('sync.meal_queued', { name, kcal: number(shown.kcal) }))
          else toast.success(t('food.logged_toast', { name, kcal: number(shown.kcal) }))
          onDone?.()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }
}
