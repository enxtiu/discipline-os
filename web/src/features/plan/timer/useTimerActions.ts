/**
 * Start / stop with an optimistic timer cache: the bar answers the tap at once
 * — also offline, where the write waits in the queue carrying the tap
 * instant — and the server's answer (or the refetch once the queue is sent)
 * replaces the provisional state. A failed write restores the previous state
 * with its original receive time, so the ticking clock does not jump.
 */
import { useCallback } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

import { newId, planKeys, useTimerStart, useTimerStop } from '../queries'
import type { Ref, TaskRef, TimeEntry, TimerState } from '../types'

export interface StartTimerInput {
  task?: TaskRef | null
  project?: Ref | null
  area?: Ref | null
  description?: string | null
}

function snapshot(queryClient: QueryClient) {
  const data = queryClient.getQueryData<TimerState>(planKeys.timer)
  const updatedAt = queryClient.getQueryState(planKeys.timer)?.dataUpdatedAt
  return () => {
    if (data) queryClient.setQueryData(planKeys.timer, data, { updatedAt })
    void queryClient.invalidateQueries({ queryKey: planKeys.timer })
  }
}

export function useStartTimer() {
  const queryClient = useQueryClient()
  const { mutateAsync } = useTimerStart()
  return useCallback(
    async (input: StartTimerInput) => {
      const id = newId()
      const now = new Date().toISOString()
      const description = input.description?.trim() || null
      const restore = snapshot(queryClient)
      const entry: TimeEntry = {
        id,
        task: input.task ?? null,
        project: input.project ?? null,
        area: input.area ?? null,
        description,
        note: null,
        started_at: now,
        ended_at: null,
        duration_s: null,
        date: '', // local day/time are the server's to say; shown once it answers
        start: '',
        end: null,
        ends_next_day: false,
        source: 'timer',
        running: true,
      }
      await queryClient.cancelQueries({ queryKey: planKeys.timer })
      queryClient.setQueryData<TimerState>(planKeys.timer, {
        running: { entry, elapsed_s: 0, server_now: now },
        server_now: now,
      })
      try {
        return await mutateAsync({
          id,
          task_id: input.task?.id,
          project_id: input.project?.id,
          area_id: input.area?.id,
          description: description ?? undefined,
          started_at: now,
        })
      } catch (error) {
        restore()
        throw error
      }
    },
    [mutateAsync, queryClient],
  )
}

export function useStopTimer() {
  const queryClient = useQueryClient()
  const { mutateAsync } = useTimerStop()
  return useCallback(
    async (input: { entryId: string; endedAt?: string }) => {
      const restore = snapshot(queryClient)
      await queryClient.cancelQueries({ queryKey: planKeys.timer })
      queryClient.setQueryData<TimerState>(planKeys.timer, (old) => (old ? { ...old, running: null } : old))
      try {
        return await mutateAsync({ entry_id: input.entryId, ended_at: input.endedAt })
      } catch (error) {
        restore()
        throw error
      }
    },
    [mutateAsync, queryClient],
  )
}
