/**
 * Task actions shared by every list: complete / reopen with an optimistic
 * update and an undo toast, "patch with undo" (inbox clarify, move to today)
 * and bulk moves. Light on purpose — the Today card imports it.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { errorMessage } from '@/lib/errors'
import { blockLabel, dueLabel, relativeDay } from '../format'
import { patchTaskInCaches, planKeys, useCompleteTask, useReopenTask, useUpdateTask } from '../queries'
import type { Counts, Task, TaskPatchIn } from '../types'
import { planToast } from './toast'

type TaskLike = Pick<Task, 'id' | 'title' | 'status'>

export function isClosed(task: Pick<Task, 'status'>): boolean {
  return task.status === 'done' || task.status === 'cancelled'
}

/** "Завтра 18:00" / "пт, 31 окт. 18:00–19:30" — when a recurring task comes back. */
export function nextInstanceLabel(next: Task, today: string | undefined): string {
  if (next.block) return `${relativeDay(next.block.date, today)} ${blockLabel(next.block)}`
  return dueLabel(next, today)
}

/** The server's today, read from the counts cache at call time (no extra request). */
function useTodayReader(hint?: string) {
  const queryClient = useQueryClient()
  return () => hint ?? queryClient.getQueryData<Counts>(planKeys.counts)?.date
}

/**
 * Checkbox behaviour: completing answers instantly (every cached copy of the
 * task is patched), the toast offers «Отменить» and names the next repeat.
 */
export function useCompleteToggle(todayHint?: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const complete = useCompleteTask()
  const reopen = useReopenTask()
  const readToday = useTodayReader(todayHint)

  const reopenTask = (task: TaskLike) => {
    const rollback = patchTaskInCaches(queryClient, task.id, {
      status: 'open',
      completed_at: null,
      completed_day: null,
    })
    reopen.mutate(
      { id: task.id },
      {
        onSuccess: (result) => {
          if (result.queued) planToast.info(t('task.queued'))
        },
        onError: (error) => {
          rollback()
          planToast.error(errorMessage(error))
        },
      },
    )
  }

  const completeTask = (task: TaskLike) => {
    const rollback = patchTaskInCaches(queryClient, task.id, {
      status: 'done',
      completed_at: new Date().toISOString(),
      overdue: false,
    })
    complete.mutate(
      { id: task.id },
      {
        onSuccess: (result) => {
          const undo = { label: t('task.undo'), onClick: () => reopenTask(task) }
          if (result.queued) {
            planToast.info(t('task.done_queued'), undo)
            return
          }
          const next = result.data.next_instance
          const when = next ? nextInstanceLabel(next, readToday()) : ''
          planToast.success(
            next && when ? t('task.done_next', { when }) : t('task.done_toast', { title: task.title }),
            undo,
          )
        },
        onError: (error) => {
          rollback()
          planToast.error(errorMessage(error))
        },
      },
    )
  }

  return {
    complete: completeTask,
    reopen: reopenTask,
    toggle: (task: TaskLike) => (isClosed(task) ? reopenTask(task) : completeTask(task)),
  }
}

/** A one-tap change with an undo (inbox clarify, «Перенести на сегодня»). */
export function usePatchWithUndo() {
  const { t } = useTranslation()
  const update = useUpdateTask()

  const undoWith = (id: string, patch: TaskPatchIn) => ({
    label: t('task.undo'),
    onClick: () =>
      update.mutate(
        { id, patch },
        {
          onSuccess: (result) => {
            if (result.queued) planToast.info(t('task.queued'))
          },
          onError: (error) => planToast.error(errorMessage(error)),
        },
      ),
  })

  return {
    pending: update.isPending,
    run: (
      id: string,
      patch: TaskPatchIn,
      options: { message: string; undo?: TaskPatchIn; onDone?: () => void; onError?: () => void },
    ) =>
      update.mutate(
        { id, patch },
        {
          onSuccess: (result) => {
            const undo = options.undo ? undoWith(id, options.undo) : undefined
            if (result.queued) planToast.info(t('task.queued'), undo)
            else planToast.success(options.message, undo)
            options.onDone?.()
          },
          onError: (error) => {
            planToast.error(errorMessage(error))
            options.onError?.()
          },
        },
      ),
    /** Several tasks at once; one toast, one undo restoring every previous value. */
    runMany: async (
      changes: { id: string; patch: TaskPatchIn; undo: TaskPatchIn }[],
      message: string,
    ): Promise<boolean> => {
      const results = await Promise.allSettled(
        changes.map((change) => update.mutateAsync({ id: change.id, patch: change.patch })),
      )
      const failed = results.find((result) => result.status === 'rejected') as PromiseRejectedResult | undefined
      if (failed) {
        planToast.error(errorMessage(failed.reason))
        return false
      }
      const queued = results.some((result) => result.status === 'fulfilled' && result.value.queued)
      const undo = {
        label: t('task.undo'),
        onClick: () => {
          void Promise.allSettled(changes.map((change) => update.mutateAsync({ id: change.id, patch: change.undo })))
        },
      }
      if (queued) planToast.info(t('task.queued'), undo)
      else planToast.success(message, undo)
      return true
    },
  }
}
