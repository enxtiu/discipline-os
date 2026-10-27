/**
 * /plan/inbox — captured, not clarified yet (newest first). Each row can be
 * clarified in one tap — Сегодня / Завтра / Проект… — without opening the
 * editor; the server moves a task with a date or a container out of the inbox.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, EmptyState, ErrorState } from '@/components/ui'
import { IconPlus } from '@/components/ui/icons'
import { addDays } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { quickAdd } from '../events'
import { IconCalendar, IconFolder, IconInbox } from '../icons'
import { useCounts, useInbox } from '../queries'
import type { Task, TaskPatchIn } from '../types'
import { ContainerPicker, type ContainerPick } from '../tasks/ContainerPicker'
import { TaskGroup, TaskListSkeleton } from '../tasks/TaskList'
import { TaskRow } from '../tasks/TaskRow'
import { Chip } from '../tasks/ui'
import { usePatchWithUndo } from '../tasks/useTaskActions'

/** A row hidden optimistically until the server answers; a new `updated_at` (undo) shows it again. */
const rowKey = (task: Task) => `${task.id}:${task.updated_at}`

export function InboxPage() {
  const { t } = useTranslation()
  const inbox = useInbox()
  const today = useCounts().data?.date
  const patch = usePatchWithUndo()
  const [hidden, setHidden] = useState<Set<string>>(() => new Set())
  const [picking, setPicking] = useState<Task | null>(null)

  const hide = (task: Task, value: boolean) =>
    setHidden((current) => {
      const next = new Set(current)
      if (value) next.add(rowKey(task))
      else next.delete(rowKey(task))
      return next
    })

  const clarify = (task: Task, change: TaskPatchIn, undo: TaskPatchIn, message: string) => {
    hide(task, true)
    patch.run(task.id, change, { message, undo: { ...undo, status: 'inbox' }, onError: () => hide(task, false) })
  }

  const pickContainer = (pick: ContainerPick) => {
    const task = picking
    setPicking(null)
    if (!task) return
    if (pick.kind === 'project') {
      clarify(task, { project_id: pick.id }, { project_id: null }, t('plan.clarified_container', { name: pick.name }))
    } else {
      clarify(task, { area_id: pick.id }, { area_id: null }, t('plan.clarified_container', { name: pick.name }))
    }
  }

  if (inbox.isLoading) return <TaskListSkeleton rows={4} />
  if (inbox.isError || !inbox.data) {
    return <ErrorState message={errorMessage(inbox.error)} onRetry={() => void inbox.refetch()} retryLabel={t('app.retry')} />
  }

  const items = inbox.data.items.filter((task) => !hidden.has(rowKey(task)))

  return (
    <div className="space-y-4" data-testid="inbox">
      {items.length === 0 ? (
        <EmptyState
          icon={<IconInbox size={30} />}
          title={t('plan.inbox_empty')}
          description={t('plan.inbox_empty_hint')}
          action={
            <Button size="sm" className="min-h-tap" icon={<IconPlus size={16} />} onClick={() => quickAdd()}>
              {t('plan.add_task')}
            </Button>
          }
        />
      ) : (
        <>
          <p className="px-1 text-sm muted">{t('plan.inbox_hint')}</p>
          <TaskGroup title={t('plan.inbox')} count={items.length} testId="inbox-list">
            {items.map((task) => (
              <TaskRow key={task.id} task={task} today={today}>
                <div className="flex flex-wrap gap-2">
                  <Chip
                    disabled={!today}
                    onClick={() =>
                      today && clarify(task, { due_date: today }, { due_date: null }, t('plan.clarified_today'))
                    }
                    data-testid="clarify-today"
                  >
                    <IconCalendar size={14} />
                    {t('plan.day.today')}
                  </Chip>
                  <Chip
                    disabled={!today}
                    onClick={() =>
                      today &&
                      clarify(task, { due_date: addDays(today, 1) }, { due_date: null }, t('plan.clarified_tomorrow'))
                    }
                    data-testid="clarify-tomorrow"
                  >
                    {t('plan.day.tomorrow')}
                  </Chip>
                  <Chip onClick={() => setPicking(task)} data-testid="clarify-project">
                    <IconFolder size={14} />
                    {t('plan.clarify_container')}
                  </Chip>
                </div>
              </TaskRow>
            ))}
          </TaskGroup>
        </>
      )}
      <ContainerPicker open={!!picking} onClose={() => setPicking(null)} onPick={pickContainer} />
    </div>
  )
}
