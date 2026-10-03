/**
 * Nutrition queries & mutations (stage 2).
 *
 * Every write invalidates the day dashboard, recents and favorites — the three
 * views that move when something is logged — so the UI never shows stale math.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { endpoints } from '@/api/client'
import type { FoodIn, MealLogIn, MealType, Recipe, RecipeIn, RecipePatch } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { today } from '@/lib/format'

export function useDay(date?: string) {
  const day = date ?? today()
  return useQuery({
    queryKey: queryKeys.day(day),
    queryFn: () => endpoints.day(day),
  })
}

export function useFoodSearch(query: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.foodSearch(query),
    queryFn: () => endpoints.searchFoods(query),
    enabled,
    placeholderData: (previous) => previous,
  })
}

export function useFavorites() {
  return useQuery({ queryKey: queryKeys.favorites, queryFn: () => endpoints.favorites() })
}

export function useRecents() {
  return useQuery({ queryKey: queryKeys.recents, queryFn: () => endpoints.recents() })
}

export function useRecipes() {
  return useQuery({ queryKey: queryKeys.recipes, queryFn: () => endpoints.recipes() })
}

function useInvalidateNutrition() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: ['nutrition'] })
    queryClient.invalidateQueries({ queryKey: ['foods'] })
    queryClient.invalidateQueries({ queryKey: ['recipes'] })
    queryClient.invalidateQueries({ queryKey: ['session'] })
  }
}

export function useLogMeal() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: (payload: MealLogIn & { id: string }) => endpoints.logMeal(payload),
    onSuccess: invalidate,
  })
}

export function useDeleteMeal() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: (id: string) => endpoints.deleteMeal(id),
    onSuccess: invalidate,
  })
}

export function useCopyMeals() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: (payload: { from_day: string; meal_type?: MealType; to_meal_type?: MealType }) =>
      endpoints.copyMeals(payload),
    onSuccess: invalidate,
  })
}

export function useAddWater() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: ({ amountMl, id }: { amountMl: number; id?: string }) => endpoints.addWater(amountMl, id),
    onSuccess: invalidate,
  })
}

export function useToggleFavorite() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: async ({ foodId, favorite, grams }: { foodId: string; favorite: boolean; grams?: number }) => {
      if (favorite) await endpoints.removeFavorite(foodId)
      else await endpoints.addFavorite(foodId, grams)
    },
    onSuccess: invalidate,
  })
}

export function useCreateRecipe() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: (payload: RecipeIn) => endpoints.createRecipe(payload),
    onSuccess: invalidate,
  })
}

export function useUpdateRecipe() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: RecipePatch }) => endpoints.updateRecipe(id, patch),
    onSuccess: invalidate,
  })
}

/** One-tap "the numbers are right" for an unverified library food. */
export function useVerifyFood() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: (id: string) => endpoints.verifyFood(id, true),
    onSuccess: invalidate,
  })
}

export function useDeleteRecipe() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: (id: string) => endpoints.deleteRecipe(id),
    onSuccess: invalidate,
  })
}

export function useCreateFood() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    mutationFn: (payload: FoodIn) => endpoints.createFood(payload),
    onSuccess: invalidate,
  })
}

/** Per-100 g macros of whatever the log sheet is pointing at. */
export function per100(target: { kind: 'food'; food: import('@/api/types').Food } | { kind: 'recipe'; recipe: Recipe }) {
  return target.kind === 'food'
    ? {
        kcal: target.food.kcal_per_100g,
        protein: target.food.protein_per_100g,
        fat: target.food.fat_per_100g,
        carbs: target.food.carbs_per_100g,
        fiber: target.food.fiber_per_100g,
      }
    : {
        kcal: target.recipe.kcal_per_100g,
        protein: target.recipe.protein_per_100g,
        fat: target.recipe.fat_per_100g,
        carbs: target.recipe.carbs_per_100g,
        fiber: target.recipe.fiber_per_100g,
      }
}

export function macrosFor(target: Parameters<typeof per100>[0], grams: number) {
  const base = per100(target)
  const factor = grams / 100
  return {
    kcal: round(base.kcal * factor),
    protein: round(base.protein * factor),
    fat: round(base.fat * factor),
    carbs: round(base.carbs * factor),
    fiber: round(base.fiber * factor),
  }
}

function round(value: number): number {
  return Math.round(value * 100) / 100
}
