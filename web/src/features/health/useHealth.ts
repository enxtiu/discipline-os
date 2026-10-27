/**
 * Health data (stage 5): steps, weight and sleep.
 *
 * Reads are plain queries. Manual entries are PUT/DELETE by local day, so they
 * are naturally idempotent and go through the offline queue: typed on the
 * metro, sent when the network is back. A weigh-in also moves the profile
 * weight (and so the targets), hence the session refresh.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { endpoints } from '@/api/client'
import type { HealthDay, SleepEntry, SleepIn, StepsIn, WeightEntry, WeightIn } from '@/api/types'
import { mutate } from '@/lib/offlineQueue'
import { queryKeys } from '@/lib/queryClient'

export function useHealthToday() {
  // back from the Shortcuts app: show its numbers without a reload
  return useQuery({
    queryKey: queryKeys.healthToday,
    queryFn: () => endpoints.healthToday(),
    refetchOnWindowFocus: true,
  })
}

/** `enabled`: a sheet asks only while it is open (stage 9: Today opened without three series reads). */
export function useStepsSeries(days = 14, enabled = true) {
  return useQuery({ queryKey: queryKeys.steps(days), queryFn: () => endpoints.stepsSeries(days), enabled })
}

export function useWeightSeries(days = 90, enabled = true) {
  return useQuery({ queryKey: queryKeys.weight(days), queryFn: () => endpoints.weightSeries(days), enabled })
}

export function useSleepSeries(days = 14, enabled = true) {
  return useQuery({ queryKey: queryKeys.sleep(days), queryFn: () => endpoints.sleepSeries(days), enabled })
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

/**
 * Health numbers after queued entries have been sent: since stage 9 the app-wide
 * refresh does it (lib/queueRefresh.ts, health data and the session included) —
 * this hook no longer refetches health and the session on every mount.
 */
export function useHealthQueueRefresh(): void {}

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
    path: endpoints.healthPaths.steps(day),
    method: 'PUT',
    body,
  }))
}

export function useDeleteSteps() {
  return useHealthWrite<string, unknown>((day) => ({ path: endpoints.healthPaths.steps(day), method: 'DELETE' }))
}

export function useSaveWeight() {
  return useHealthWrite<{ day: string; body: WeightIn }, WeightEntry>(
    ({ day, body }) => ({ path: endpoints.healthPaths.weight(day), method: 'PUT', body }),
    true,
  )
}

export function useDeleteWeight() {
  return useHealthWrite<string, unknown>((day) => ({ path: endpoints.healthPaths.weight(day), method: 'DELETE' }), true)
}

export function useSaveSleep() {
  return useHealthWrite<{ day: string; body: SleepIn }, SleepEntry>(({ day, body }) => ({
    path: endpoints.healthPaths.sleep(day),
    method: 'PUT',
    body,
  }))
}

export function useDeleteSleep() {
  return useHealthWrite<string, unknown>((day) => ({ path: endpoints.healthPaths.sleep(day), method: 'DELETE' }))
}
