/**
 * Start / stop with an optimistic timer cache: the bar answers the tap at once
 * — also offline, where the write waits in the queue carrying the tap
 * instant — and the server's answer (or the refetch once the queue is sent)
 * replaces the provisional state. A failed write restores the previous state
 * with its original receive time, so the ticking clock does not jump.
 *
 * The tap instant is read on the server's clock: the device clock plus the
 * offset seen in the last `GET /timer` answer (`server_now` against when it
 * arrived), so a phone whose clock is a few minutes off neither starts a timer
 * "in the future" (refused) nor shifts it.
 */
import { useCallback } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

import { newId, planKeys, useTimerStart, useTimerStop } from '../queries'
import type { Ref, TaskRef, TimeEntry, TimerState } from '../types'
import { instantMs } from './entry'

const DAY_MS = 86_400_000

/** "Now" as an ISO instant on the server's clock (the device clock when no answer is cached yet). */
export function serverNow(queryClient: QueryClient): string {
  const data = queryClient.getQueryData<TimerState>(planKeys.timer)
  const receivedAt = queryClient.getQueryState(planKeys.timer)?.dataUpdatedAt ?? 0
  const serverAt = data ? instantMs(data.server_now) : Number.NaN
  const offset = receivedAt > 0 && !Number.isNaN(serverAt) ? serverAt - receivedAt : 0
  return new Date(Date.now() + (Math.abs(offset) < DAY_MS ? offset : 0)).toISOString()
}

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
      const now = serverNow(queryClient)
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
      const endedAt = input.endedAt ?? serverNow(queryClient)
      const restore = snapshot(queryClient)
      await queryClient.cancelQueries({ queryKey: planKeys.timer })
      // keep the receive time: it still belongs to the cached server_now (see serverNow)
      const updatedAt = queryClient.getQueryState(planKeys.timer)?.dataUpdatedAt
      queryClient.setQueryData<TimerState>(planKeys.timer, (old) => (old ? { ...old, running: null } : old), { updatedAt })
      try {
        return await mutateAsync({ entry_id: input.entryId, ended_at: endedAt })
      } catch (error) {
        restore()
        throw error
      }
    },
    [mutateAsync, queryClient],
  )
}
