/**
 * /plan — today: overdue (with «Перенести всё на сегодня»), due today, time
 * blocks by start, and what is already done (collapsed). Every bucket comes
 * from the server (`GET /views/today`), in the user's timezone.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, EmptyState, ErrorState } from '@/components/ui'
import { IconPlus } from '@/components/ui/icons'
import { dayLabel } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { quickAdd } from '../events'
import { IconCalendar } from '../icons'
import { useTodayView } from '../queries'
import type { Task } from '../types'
import { TaskGroup, TaskListSkeleton } from '../tasks/TaskList'
import { TaskRow } from '../tasks/TaskRow'
import { usePatchWithUndo } from '../tasks/useTaskActions'

function byBlockStart(a: Task, b: Task): number {
  return Date.parse(a.block?.start_at ?? '') - Date.parse(b.block?.start_at ?? '')
}

export function PlanTodayPage() {
  const { t } = useTranslation()
  const view = useTodayView()
  const patch = usePatchWithUndo()
  const [moving, setMoving] = useState(false)
  const data = view.data

  if (view.isLoading) {
    return (
      <div className="space-y-4">
        <TaskListSkeleton rows={4} />
        <TaskListSkeleton rows={2} />
      </div>
    )
  }
  if (view.isError || !data) {
    return <ErrorState message={errorMessage(view.error)} onRetry={() => void view.refetch()} retryLabel={t('app.retry')} />
  }

  const today = data.date
  const scheduled = [...data.scheduled].sort(byBlockStart)
  const nothingOpen = data.overdue.length === 0 && data.today.length === 0 && scheduled.length === 0

  const moveAllToToday = async () => {
    setMoving(true)
    await patch.runMany(
      data.overdue.map((task) => ({ id: task.id, patch: { due_date: today }, undo: { due_date: task.due_date } })),
      t('plan.moved_to_today', { n: data.overdue.length }),
    )
    setMoving(false)
  }

  return (
    <div className="space-y-5" data-testid="plan-today">
      <p className="sk-datehead px-1 text-sm muted first-letter:uppercase">{dayLabel(today, 'long')}</p>

      {data.overdue.length > 0 && (
        <TaskGroup
          title={t('plan.overdue')}
          count={data.overdue.length}
          tone="danger"
          testId="plan-overdue"
          action={
            <Button
              size="sm"
              variant="secondary"
              className="min-h-tap"
              loading={moving}
              onClick={() => void moveAllToToday()}
              data-testid="move-all-today"
            >
              {t('plan.move_all_today')}
            </Button>
          }
        >
          {data.overdue.map((task) => (
            <TaskRow key={task.id} task={task} today={today} />
          ))}
        </TaskGroup>
      )}

      {data.today.length > 0 && (
        <TaskGroup title={t('plan.due_today')} count={data.today.length} testId="plan-due-today">
          {data.today.map((task) => (
            <TaskRow key={task.id} task={task} today={today} day={today} />
          ))}
        </TaskGroup>
      )}

      {scheduled.length > 0 && (
        <TaskGroup title={t('plan.time_blocks')} count={scheduled.length} tone="accent" testId="plan-blocks">
          {scheduled.map((task) => (
            <TaskRow key={task.id} task={task} today={today} day={today} />
          ))}
        </TaskGroup>
      )}

      {nothingOpen && (
        <EmptyState
          icon={<IconCalendar size={30} />}
          title={data.done.length > 0 ? t('plan.today_all_done') : t('plan.today_empty')}
          description={t('plan.today_empty_hint')}
          action={
            <Button
              size="sm"
              className="min-h-tap"
              icon={<IconPlus size={16} />}
              onClick={() => quickAdd({ defaults: { due_date: today } })}
            >
              {t('plan.add_for_today')}
            </Button>
          }
        />
      )}

      {data.done.length > 0 && (
        <TaskGroup title={t('plan.done_today')} count={data.done.length} collapsible defaultOpen={false} testId="plan-done">
          {data.done.map((task) => (
            <TaskRow key={task.id} task={task} today={today} day={today} />
          ))}
        </TaskGroup>
      )}
    </div>
  )
}
