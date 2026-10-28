/**
 * One task in any list: a round checkbox tinted by priority (the whole 48 px
 * square is the target), the title and a meta line built only from strings the
 * server sent (due label, block, project/area, tags, repeat, subtasks, estimate,
 * reminders). Tapping the row opens the editor. Virtual repeats render dimmed
 * and open their series task.
 */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { IconCheck, IconClock } from '@/components/ui/icons'
import { openTask } from '../events'
import { usePendingTaskIds } from '../pending'
import { blockLabel, dueLabel, formatMinutes, PRIORITY_STYLES, relativeDay } from '../format'
import { IconBellSmall, IconCalendar, IconRepeat, IconSubtasks, IconTimer } from '../icons'
import type { Occurrence, Priority, Ref, Task } from '../types'
import { ColorDot } from './ColorDot'
import { isClosed, useCompleteToggle } from './useTaskActions'

/** Faint fill behind the priority ring (literal classes, existing palette only). */
const PRIORITY_FILL: Record<Priority, string> = {
  1: 'bg-rose-400/15',
  2: 'bg-amber-400/15',
  3: 'bg-violet-300/15',
  4: '',
}

function Meta({
  icon,
  children,
  className,
  srLabel,
}: {
  icon?: ReactNode
  children?: ReactNode
  className?: string
  srLabel?: string
}) {
  return (
    <span className={clsx('inline-flex min-w-0 items-center gap-1', className)}>
      {icon}
      {children !== undefined && <span className="truncate">{children}</span>}
      {srLabel && <span className="sr-only">{srLabel}</span>}
    </span>
  )
}

function ContainerChip({ container }: { container: Ref }) {
  return (
    <Meta>
      <span className="inline-flex max-w-[11rem] items-center gap-1">
        <ColorDot color={container.color} />
        <span className="truncate">{container.name}</span>
      </span>
    </Meta>
  )
}

/** The round checkbox: `role="checkbox"`, 48 × 48 hit area, ring tinted by priority. */
export function TaskCheck({
  done,
  priority,
  title,
  onToggle,
  busy,
  testId = 'task-check',
}: {
  done: boolean
  priority: Priority
  title: string
  onToggle: () => void
  /** The last tap is still on its way to the server (further taps are ignored). */
  busy?: boolean
  testId?: string
}) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={done}
      aria-busy={busy || undefined}
      aria-label={done ? t('task.reopen_aria', { title }) : t('task.complete_aria', { title })}
      onClick={onToggle}
      className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl"
      data-testid={testId}
    >
      <span
        className={clsx(
          'flex h-[22px] w-[22px] items-center justify-center rounded-full border-2 transition-colors',
          done
            ? 'border-transparent bg-[rgb(var(--accent))] text-on-accent'
            : [PRIORITY_STYLES[priority].ring, PRIORITY_FILL[priority]],
        )}
      >
        {done && <IconCheck size={14} strokeWidth={3} />}
      </span>
    </button>
  )
}

export interface TaskRowProps {
  task: Task
  /** The server's today (labels "Сегодня"/"Завтра"). */
  today?: string
  /** The day this list is about: its date is implied, only times are shown. */
  day?: string
  /** Show the project/area chip (off inside a project or area). */
  showContainer?: boolean
  /** Extra actions under the row (inbox clarify). */
  children?: ReactNode
}

export function TaskRow({ task, today, day, showContainer = true, children }: TaskRowProps) {
  const { t } = useTranslation()
  const { toggle, busy } = useCompleteToggle(today)
  const waiting = usePendingTaskIds().has(task.id)
  const closed = isClosed(task)
  const meta: ReactNode[] = []
  if (waiting) {
    meta.push(
      <Meta key="waiting" className="text-violet-300">
        <span data-testid="task-pending">{t('sync.pending_mark')}</span>
      </Meta>,
    )
  }

  if (task.due_date) {
    const label = day && task.due_date === day ? (task.due_time ?? '') : dueLabel(task, today)
    if (label) {
      meta.push(
        <Meta
          key="due"
          icon={<IconCalendar size={12} />}
          className={clsx('tnum', task.overdue && !closed && 'font-medium text-rose-400')}
        >
          {label}
        </Meta>,
      )
    }
  }
  if (task.block) {
    const prefix = day && task.block.date === day ? '' : `${relativeDay(task.block.date, today)} `
    meta.push(
      <Meta key="block" icon={<IconClock size={12} />} className="tnum">
        {prefix}
        {blockLabel(task.block)}
      </Meta>,
    )
  }
  const container = task.project ?? task.area
  if (showContainer && container) meta.push(<ContainerChip key="container" container={container} />)
  for (const tag of task.tags.slice(0, 3)) meta.push(<Meta key={`tag-${tag.id}`}>#{tag.name}</Meta>)
  if (task.tags.length > 3) meta.push(<Meta key="tags-more">{t('task.more_tags', { n: task.tags.length - 3 })}</Meta>)
  if (task.context) meta.push(<Meta key="context">@{task.context}</Meta>)
  if (task.is_recurring) meta.push(<Meta key="repeat" icon={<IconRepeat size={12} />} srLabel={t('task.repeats')} />)
  if (task.subtask_counts.total > 0) {
    meta.push(
      <Meta key="subtasks" icon={<IconSubtasks size={12} />} className="tnum" srLabel={t('task.subtasks')}>
        {task.subtask_counts.done}/{task.subtask_counts.total}
      </Meta>,
    )
  }
  if (task.estimate_minutes) {
    meta.push(
      <Meta key="estimate" icon={<IconTimer size={12} />} className="tnum" srLabel={t('task.estimate')}>
        {formatMinutes(task.estimate_minutes)}
      </Meta>,
    )
  }
  if (task.reminders.some((reminder) => reminder.status === 'pending')) {
    meta.push(<Meta key="bell" icon={<IconBellSmall size={12} />} srLabel={t('task.has_reminders')} />)
  }

  return (
    <div data-testid="task-row" data-task-id={task.id} data-status={task.status} data-pending={waiting ? 'true' : 'false'}>
      <div className="flex items-start">
        <TaskCheck
          done={closed}
          priority={task.priority}
          title={task.title}
          onToggle={() => toggle(task)}
          busy={busy}
        />
        <button
          type="button"
          onClick={() => openTask(task.id)}
          className="min-h-tap min-w-0 flex-1 py-3 pr-4 text-left transition-colors active:bg-[rgb(var(--border))]/30"
        >
          {task.parent_title && (
            <span className="mb-0.5 block truncate text-xs muted">{t('task.parent_prefix', { title: task.parent_title })}</span>
          )}
          <span
            className={clsx(
              'block break-words text-[15px] leading-snug',
              closed && 'text-[rgb(var(--text-muted))] line-through',
            )}
            data-testid="task-title"
          >
            {task.title}
          </span>
          {meta.length > 0 && (
            <span className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs muted">
              {meta}
            </span>
          )}
        </button>
      </div>
      {children && <div className="-mt-1 pb-3 pl-12 pr-3">{children}</div>}
    </div>
  )
}

/** A future repeat that is not a row yet: dimmed, opens the series task. */
export function OccurrenceRow({
  occurrence,
  today,
  day,
  showContainer = true,
}: {
  occurrence: Occurrence
  today?: string
  day?: string
  showContainer?: boolean
}) {
  const { t } = useTranslation()
  const time = occurrence.block ? blockLabel(occurrence.block) : (occurrence.due_time ?? '')
  const container = occurrence.project ?? occurrence.area
  return (
    <div className="flex items-start" data-testid="occurrence-row" data-task-id={occurrence.task_id}>
      <span className="flex h-12 w-12 shrink-0 items-center justify-center opacity-60" aria-hidden="true">
        <span className={clsx('h-[22px] w-[22px] rounded-full border-2 border-dashed', PRIORITY_STYLES[occurrence.priority].ring)} />
      </span>
      <button
        type="button"
        onClick={() => openTask(occurrence.task_id)}
        className="min-h-tap min-w-0 flex-1 py-3 pr-4 text-left"
      >
        <span className="block break-words text-[15px] leading-snug text-[rgb(var(--text-muted))]">{occurrence.title}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs muted">
          <Meta icon={<IconRepeat size={12} />}>{t('task.virtual_repeat')}</Meta>
          {(!day || occurrence.date !== day) && <Meta className="tnum">{relativeDay(occurrence.date, today)}</Meta>}
          {time && (
            <Meta icon={<IconClock size={12} />} className="tnum">
              {time}
            </Meta>
          )}
          {showContainer && container && <ContainerChip container={container} />}
        </span>
      </button>
    </div>
  )
}
