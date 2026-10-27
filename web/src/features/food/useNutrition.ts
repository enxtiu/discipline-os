/**
 * Nutrition queries & mutations (stage 2; offline since stage 9).
 *
 * Every write invalidates the day dashboard, recents and favorites — the three
 * views that move when something is logged — so the UI never shows stale math.
 *
 * Logging and deleting meals and water go through the offline queue (`mutate`)
 * with the client ids the API keys them by and the tap instant as `logged_at`,
 * so a meal eaten at 23:50 in the metro lands on the right day when it is sent
 * after midnight. Until then `useDayView` shows it (see `pendingDay.ts`).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import i18n from '@/i18n'
import { endpoints } from '@/api/client'
import type { FoodIn, MealLog, MealLogIn, MealType, Recipe, RecipeIn, RecipePatch, WaterLog } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { number, today } from '@/lib/format'
import { createdIdOf, mutate, withdraw, type QueuedRequest } from '@/lib/offlineQueue'
import { MEALS_PATH, WATER_PATH, usePendingDay, type MealPreview } from './pendingDay'

export type { MealPreview } from './pendingDay'

export function useDay(date?: string) {
  const day = date ?? today()
  return useQuery({
    queryKey: queryKeys.day(day),
    queryFn: () => endpoints.day(day),
  })
}

/**
 * The day as Today and Food show it: the server's day plus meals and water still
 * waiting in the offline queue (`pendingIds` marks those rows «ждёт отправки»).
 */
export function useDayView(date?: string) {
  const day = date ?? today()
  const query = useDay(day)
  const view = usePendingDay(query.data, query.dataUpdatedAt, day)
  return {
    data: view.data,
    pendingIds: view.pendingIds,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  }
}

export type Saved<T> = { queued: true; clientUuid: string } | { queued: false; data: T }

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

/** A create still in the queue (not on its way): the one with this client id. */
function queuedCreate(path: string, id: string) {
  return (entry: QueuedRequest) => entry.method === 'POST' && entry.path === path && createdIdOf(entry) === id.toLowerCase()
}

/**
 * Log a meal: sent now, or queued with what the log sheet showed (`preview`) so the
 * day shows it at once. `logged_at` is the tap instant (the server derives the day).
 */
export function useLogMeal() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    networkMode: 'always', // offline writes go to the queue instead of pausing
    mutationFn: async ({ preview, ...payload }: MealLogIn & { id: string; preview?: MealPreview }): Promise<Saved<MealLog>> => {
      const body: MealLogIn = { ...payload, logged_at: payload.logged_at ?? new Date().toISOString() }
      return mutate<MealLog>(MEALS_PATH, {
        method: 'POST',
        body,
        clientUuid: payload.id,
        kind: 'meal',
        meta: {
          day: today(),
          meal: preview,
          title: preview
            ? i18n.t('sync.meal_title', { name: preview.name, kcal: number(preview.kcal) })
            : undefined,
        },
      })
    },
    onSuccess: (result) => {
      if (!result.queued) invalidate()
    },
  })
}

/**
 * Delete a meal by its id — or, for a meal still waiting to be sent, by its client
 * id: the queued log is withdrawn and nothing reaches the server (`withdrawn`).
 */
export function useDeleteMeal() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    networkMode: 'always',
    mutationFn: async (id: string): Promise<Saved<unknown> & { withdrawn?: boolean }> => {
      if (await withdraw(queuedCreate(MEALS_PATH, id))) return { queued: false, data: null, withdrawn: true }
      return mutate(`${MEALS_PATH}/${id}`, { method: 'DELETE', kind: 'meal', clientUuid: crypto.randomUUID() })
    },
    onSuccess: (result) => {
      if (!result.queued) invalidate()
    },
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

/** Add water (queued offline; the client id makes a replay a no-op). */
export function useAddWater() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    networkMode: 'always',
    mutationFn: async ({ amountMl, id }: { amountMl: number; id: string }): Promise<Saved<WaterLog>> =>
      mutate<WaterLog>(WATER_PATH, {
        method: 'POST',
        body: { amount_ml: amountMl, id, logged_at: new Date().toISOString() },
        clientUuid: id,
        kind: 'water',
        meta: { day: today(), title: i18n.t('sync.water_title', { ml: number(amountMl) }) },
      }),
    onSuccess: (result) => {
      if (!result.queued) invalidate()
    },
  })
}

/** Remove a water log (by id or client id); one still waiting is withdrawn. */
export function useDeleteWater() {
  const invalidate = useInvalidateNutrition()
  return useMutation({
    networkMode: 'always',
    mutationFn: async (id: string): Promise<Saved<unknown> & { withdrawn?: boolean }> => {
      if (await withdraw(queuedCreate(WATER_PATH, id))) return { queued: false, data: null, withdrawn: true }
      return mutate(`${WATER_PATH}/${id}`, { method: 'DELETE', kind: 'water', clientUuid: crypto.randomUUID() })
    },
    onSuccess: (result) => {
      if (!result.queued) invalidate()
    },
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
