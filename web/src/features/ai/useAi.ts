/**
 * AI logging queries & mutations (stage 3).
 *
 * The analysis calls never write the diary: they return suggestions for the
 * review sheet. Saving goes through `useLogMeal` with `ai_job_id`.
 */
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, endpoints } from '@/api/client'
import type { MealType } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { preparePhoto } from '@/lib/image'

/** AI features need the network: fail fast with a clear code instead of a hung spinner. */
export function assertOnline(): void {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    throw new ApiError(0, { code: 'offline_ai', message: 'AI features need a connection' })
  }
}

export function useAppConfig() {
  return useQuery({ queryKey: queryKeys.appConfig, queryFn: () => endpoints.appConfig(), staleTime: 5 * 60_000 })
}

export function useAnalyzePhoto() {
  return useMutation({
    // 'online' would silently *pause* the mutation offline (spinner forever);
    // AI needs the network, so run it and fail fast with offline_ai instead
    networkMode: 'always',
    mutationFn: async ({ file, keepThumbnail, mealType }: { file: File; keepThumbnail: boolean; mealType?: MealType }) => {
      assertOnline()
      const prepared = await preparePhoto(file, keepThumbnail)
      const form = new FormData()
      form.append('photo', prepared.photo, 'photo.jpg')
      if (prepared.thumbnail) form.append('thumbnail', prepared.thumbnail, 'thumb.jpg')
      if (mealType) form.append('meal_type', mealType)
      return endpoints.analyzeMealPhoto(form)
    },
  })
}

export function useParseText() {
  return useMutation({
    networkMode: 'always',
    mutationFn: async ({ text, mealType }: { text: string; mealType?: MealType }) => {
      assertOnline()
      return endpoints.parseFoodText({ text, meal_type: mealType })
    },
  })
}

/** Object URL of a meal's opted-in thumbnail; revoked by the browser on reload. */
export function useMealPhoto(mealId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['nutrition', 'photo', mealId],
    queryFn: () => endpoints.mealPhotoUrl(mealId),
    enabled,
    staleTime: Infinity,
    retry: false,
  })
}
