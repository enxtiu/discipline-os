/**
 * Health data (stage 5): steps, weight and sleep.
 *
 * Reads are plain queries. Manual entries are PUT/DELETE by local day, so they
 * are naturally idempotent and go through the offline queue: typed on the
 * metro, sent when the network is back. A weigh-in also moves the profile
 * weight (and so the targets), hence the session refresh.
 */
import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { endpoints } from '@/api/client'
import type { HealthDay, SleepEntry, SleepIn, StepsIn, WeightEntry, WeightIn } from '@/api/types'
import { mutate, onQueueChange } from '@/lib/offlineQueue'
import { queryKeys } from '@/lib/queryClient'

export function useHealthToday() {
  return useQuery({ queryKey: queryKeys.healthToday, queryFn: () => endpoints.healthToday() })
}

export function useStepsSeries(days = 14) {
  return useQuery({ queryKey: queryKeys.steps(days), queryFn: () => endpoints.stepsSeries(days) })
}

export function useWeightSeries(days = 90) {
  return useQuery({ queryKey: queryKeys.weight(days), queryFn: () => endpoints.weightSeries(days) })
}

export function useSleepSeries(days = 14) {
  return useQuery({ queryKey: queryKeys.sleep(days), queryFn: () => endpoints.sleepSeries(days) })
}

function useInvalidateHealth(alsoSession = false) {
  const queryClient = useQueryClient()
  return async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.healthData })
    if (alsoSession) {
      await queryClient.invalidateQueries({ queryKey: queryKeys.session })
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings })
    }
  }
}

/** Refresh health numbers once queued entries have been sent. */
export function useHealthQueueRefresh() {
  const queryClient = useQueryClient()
  useEffect(() => {
    let last = Number.POSITIVE_INFINITY
    return onQueueChange((count) => {
      if (last > count) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.healthData })
        void queryClient.invalidateQueries({ queryKey: queryKeys.session })
      }
      last = count
    })
  }, [queryClient])
}

type Saved<T> = { queued: true } | { queued: false; data: T }

function useHealthWrite<TVars, TData>(
  request: (vars: TVars) => { path: string; method: 'PUT' | 'DELETE'; body?: unknown },
  alsoSession = false,
) {
  const invalidate = useInvalidateHealth(alsoSession)
  return useMutation({
    networkMode: 'always', // offline writes go to the queue instead of pausing
    mutationFn: async (vars: TVars): Promise<Saved<TData>> => {
      const { path, method, body } = request(vars)
      const result = await mutate<TData>(path, { method, body, kind: 'health' })
      return result.queued ? { queued: true } : { queued: false, data: result.data }
    },
    onSuccess: (result) => {
      if (!result.queued) void invalidate()
    },
  })
}

export function useSaveSteps() {
  return useHealthWrite<{ day: string; body: StepsIn }, HealthDay>(({ day, body }) => ({
    path: `/v1/health/steps/${day}`,
    method: 'PUT',
    body,
  }))
}

export function useDeleteSteps() {
  return useHealthWrite<string, unknown>((day) => ({ path: `/v1/health/steps/${day}`, method: 'DELETE' }))
}

export function useSaveWeight() {
  return useHealthWrite<{ day: string; body: WeightIn }, WeightEntry>(
    ({ day, body }) => ({ path: `/v1/health/weight/${day}`, method: 'PUT', body }),
    true,
  )
}

export function useDeleteWeight() {
  return useHealthWrite<string, unknown>((day) => ({ path: `/v1/health/weight/${day}`, method: 'DELETE' }), true)
}

export function useSaveSleep() {
  return useHealthWrite<{ day: string; body: SleepIn }, SleepEntry>(({ day, body }) => ({
    path: `/v1/health/sleep/${day}`,
    method: 'PUT',
    body,
  }))
}

export function useDeleteSleep() {
  return useHealthWrite<string, unknown>((day) => ({ path: `/v1/health/sleep/${day}`, method: 'DELETE' }))
}
