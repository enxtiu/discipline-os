/**
 * Training queries & mutations (stage 4).
 *
 * Set writes go through the offline queue (`mutate`): the set id is generated
 * here, the server upserts by it, so a set logged with no signal in the gym is
 * replayed later without duplicates. Everything else needs the network.
 */
import { useEffect, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { endpoints } from '@/api/client'
import type {
  ExerciseIn,
  PlanDayIn,
  TemplateIn,
  Workout,
  WorkoutFinishIn,
  WorkoutPatchIn,
  WorkoutQuickIn,
  WorkoutSetIn,
  WorkoutStartIn,
} from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { mutate, onQueueChange, pendingFor } from '@/lib/offlineQueue'

export function useTrainingToday() {
  return useQuery({ queryKey: queryKeys.trainingToday, queryFn: () => endpoints.trainingToday() })
}

export function useWeekPlan(start?: string) {
  return useQuery({
    queryKey: queryKeys.weekPlan(start ?? 'current'),
    queryFn: () => endpoints.weekPlan(start),
  })
}

export function useTemplates() {
  return useQuery({ queryKey: queryKeys.templates, queryFn: () => endpoints.templates() })
}

export function useExercises(q: string, muscle: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.exercises(q, muscle),
    queryFn: () => endpoints.exercises({ q: q || undefined, muscle: muscle || undefined, limit: 200 }),
    enabled,
    staleTime: 5 * 60_000,
    placeholderData: (previous) => previous,
  })
}

export function useExerciseHistory(id: string | null) {
  return useQuery({
    queryKey: queryKeys.exerciseHistory(id ?? ''),
    queryFn: () => endpoints.exerciseHistory(id as string),
    enabled: !!id,
  })
}

export function useWorkout(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.workout(id ?? ''),
    queryFn: () => endpoints.workout(id as string),
    enabled: !!id,
    staleTime: 0,
  })
}

export function useWorkoutHistory() {
  return useInfiniteQuery({
    queryKey: queryKeys.workouts,
    queryFn: ({ pageParam }) => endpoints.workouts({ before: pageParam, limit: 10 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => (last.has_more ? last.items[last.items.length - 1]?.started_at : undefined),
  })
}

export function useRecords() {
  return useQuery({ queryKey: queryKeys.records, queryFn: () => endpoints.records(8) })
}

export function useBurned(days = 7) {
  return useQuery({ queryKey: queryKeys.burned(days), queryFn: () => endpoints.burned(days) })
}

/** Everything a finished/changed workout moves: today, plan, history, records, burned. */
function useInvalidateTraining() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['training'] })
}

function useStoreWorkout() {
  const queryClient = useQueryClient()
  return (workout: Workout) => queryClient.setQueryData(queryKeys.workout(workout.id), workout)
}

export function useStartWorkout() {
  const invalidate = useInvalidateTraining()
  const store = useStoreWorkout()
  return useMutation({
    mutationFn: (payload: WorkoutStartIn) => endpoints.startWorkout(payload),
    onSuccess: (workout) => {
      store(workout)
      void invalidate()
    },
  })
}

export function useQuickWorkout() {
  const invalidate = useInvalidateTraining()
  return useMutation({
    mutationFn: (payload: WorkoutQuickIn) => endpoints.quickWorkout(payload),
    onSuccess: () => void invalidate(),
  })
}

export type SetWrite =
  | { kind: 'put'; setId: string; body: WorkoutSetIn }
  | { kind: 'delete'; setId: string }

/**
 * Log, correct or remove a set — queued when offline, idempotent by set id.
 * Callers use `mutateAsync` (one observer, many rows). Responses can arrive out
 * of order, so none of them is written to the cache: when the last write of a
 * burst settles, the workout (and what it moves: today, records, burned) is
 * refetched once.
 */
export function useSetWrite(workoutId: string) {
  const queryClient = useQueryClient()
  const key = ['set-write', workoutId]
  return useMutation({
    mutationKey: key,
    networkMode: 'always',
    mutationFn: async (write: SetWrite) => {
      const path = `/v1/training/workouts/${workoutId}/sets/${write.setId}`
      return write.kind === 'put'
        ? mutate<Workout>(path, { method: 'PUT', body: write.body, kind: 'workout', clientUuid: write.setId })
        : mutate<Workout>(path, { method: 'DELETE', kind: 'workout', clientUuid: crypto.randomUUID() })
    },
    onSettled: (result) => {
      if (result?.queued) return
      if (queryClient.isMutating({ mutationKey: key }) <= 1) {
        void queryClient.invalidateQueries({ queryKey: ['training'] })
      }
    },
  })
}

/**
 * Set writes of this workout still waiting in the offline queue, by set id.
 * When the queue shrinks (it was flushed), the training views are refetched.
 */
export function usePendingSets(workoutId: string | undefined): Map<string, WorkoutSetIn> {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<Map<string, WorkoutSetIn>>(() => new Map())
  useEffect(() => {
    if (!workoutId) return
    const prefix = `/v1/training/workouts/${workoutId}/sets/`
    let alive = true
    let last = -1
    const read = async () => {
      const entries = await pendingFor(prefix)
      if (!alive) return
      const next = new Map<string, WorkoutSetIn>()
      for (const entry of entries) {
        const setId = entry.path.slice(prefix.length)
        if (entry.method === 'PUT') next.set(setId, entry.body as WorkoutSetIn)
        else next.delete(setId)
      }
      setPending(next)
    }
    const stop = onQueueChange((count) => {
      if (last > count) void queryClient.invalidateQueries({ queryKey: ['training'] })
      last = count
      void read()
    })
    return () => {
      alive = false
      stop()
    }
  }, [workoutId, queryClient])
  return pending
}

export function useFinishWorkout() {
  const invalidate = useInvalidateTraining()
  const store = useStoreWorkout()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: WorkoutFinishIn }) => endpoints.finishWorkout(id, payload),
    onSuccess: (workout) => {
      store(workout)
      void invalidate()
    },
  })
}

export function usePatchWorkout() {
  const invalidate = useInvalidateTraining()
  const store = useStoreWorkout()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: WorkoutPatchIn }) => endpoints.patchWorkout(id, payload),
    onSuccess: (workout) => {
      store(workout)
      void invalidate()
    },
  })
}

export function useDeleteWorkout() {
  const invalidate = useInvalidateTraining()
  return useMutation({
    mutationFn: (id: string) => endpoints.deleteWorkout(id),
    onSuccess: () => void invalidate(),
  })
}

export function useSaveTemplate() {
  const invalidate = useInvalidateTraining()
  return useMutation({
    mutationFn: ({ payload, existing }: { payload: TemplateIn; existing: boolean }) =>
      existing && payload.id ? endpoints.replaceTemplate(payload.id, payload) : endpoints.createTemplate(payload),
    onSuccess: () => void invalidate(),
  })
}

export function useDeleteTemplate() {
  const invalidate = useInvalidateTraining()
  return useMutation({
    mutationFn: (id: string) => endpoints.deleteTemplate(id),
    onSuccess: () => void invalidate(),
  })
}

export function useSaveAsTemplate() {
  const invalidate = useInvalidateTraining()
  return useMutation({
    mutationFn: ({ workoutId, templateId, name }: { workoutId: string; templateId: string; name: string }) =>
      endpoints.saveAsTemplate(workoutId, templateId, name),
    onSuccess: () => void invalidate(),
  })
}

export type PlanWrite =
  | { scope: 'weekday'; weekday: number; payload: PlanDayIn | null }
  | { scope: 'date'; date: string; payload: PlanDayIn | null }

export function useSetPlan() {
  const invalidate = useInvalidateTraining()
  return useMutation({
    mutationFn: (write: PlanWrite) => {
      if (write.scope === 'weekday') {
        return write.payload
          ? endpoints.setWeekdayPlan(write.weekday, write.payload)
          : endpoints.clearWeekdayPlan(write.weekday)
      }
      return write.payload ? endpoints.setDatePlan(write.date, write.payload) : endpoints.clearDatePlan(write.date)
    },
    onSuccess: () => void invalidate(),
  })
}

export function useArchiveExercise() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => endpoints.patchExercise(id, { is_archived: true }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['training'] }),
  })
}

export function useCreateExercise() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ExerciseIn) => endpoints.createExercise(payload),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['training', 'exercises'] }),
  })
}
