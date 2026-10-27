/**
 * Commit a calendar drop: optimistic in every cached copy of the task
 * (`patchTaskInCaches`), then `PATCH /tasks/{id}` through the offline queue.
 * On an error the caches roll back, the server truth is refetched and a toast
 * says why. Each drop awaits its own request (`mutateAsync`), so two quick
 * drops never lose each other's rollback.
 */
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { invalidatePlanner, patchTaskInCaches, useUpdateTask } from '../queries'
import type { Block, Task, TaskPatchIn } from '../types'
import { fromMinutes, MINUTES_PER_DAY } from './dates'

export type TaskMove =
  /** New block: local day + minutes after its midnight (`end` past 1440 ends the next day). */
  | { kind: 'block'; date: string; start: number; end: number }
  /** New due day for a task without a block (`due_time` is kept by the server). */
  | { kind: 'due'; date: string }

export function useMoveTask() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const { mutateAsync } = useUpdateTask()

  return useCallback(
    async (task: Task, move: TaskMove) => {
      let patch: TaskPatchIn
      let optimistic: Partial<Task>
      if (move.kind === 'block') {
        const start = fromMinutes(move.start)
        const end = fromMinutes(move.end)
        patch = { block: { date: move.date, start, end } }
        optimistic = {
          block: {
            ...(task.block ?? ({} as Block)),
            date: move.date,
            start,
            end,
            minutes: move.end - move.start,
            ends_next_day: move.end >= MINUTES_PER_DAY,
          },
        }
      } else {
        patch = { due_date: move.date }
        optimistic = { due_date: move.date }
      }

      // an in-flight refetch must not overwrite the optimistic copy
      await queryClient.cancelQueries({ queryKey: ['planner', 'calendar'] })
      const rollback = patchTaskInCaches(queryClient, task.id, optimistic)
      try {
        const result = await mutateAsync({ id: task.id, patch })
        if (result.queued) toast.info(t('calendar.moved_offline'))
      } catch (error) {
        rollback()
        void invalidatePlanner(queryClient)
        toast.error(t('calendar.move_failed', { reason: errorMessage(error) }))
      }
    },
    [mutateAsync, queryClient, t, toast],
  )
}
