/**
 * Subtasks inside the editor: tick, open, add inline. Depth is shown by
 * indentation (root → subtask → sub-subtask, the server's maximum). A new task
 * collects subtask titles locally; they are created right after it (same
 * order in the offline queue, so a task captured offline keeps its children).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { IconButton, Input } from '@/components/ui'
import { IconClose, IconPlus } from '@/components/ui/icons'
import { errorMessage } from '@/lib/errors'
import { useCreateTask } from '../queries'
import type { TaskDetail } from '../types'
import { planToast } from './toast'
import { TaskCheck } from './TaskRow'
import { isClosed, useCompleteToggle } from './useTaskActions'

const INDENT = ['', 'pl-6', 'pl-12']
const MAX_DEPTH = 2

function flatten(subtasks: TaskDetail[], level: number, out: { task: TaskDetail; level: number }[] = []) {
  for (const task of subtasks) {
    out.push({ task, level })
    if (task.subtasks?.length) flatten(task.subtasks, level + 1, out)
  }
  return out
}

function AddRow({ onAdd, disabled }: { onAdd: (title: string) => void; disabled?: boolean }) {
  const { t } = useTranslation()
  const [title, setTitle] = useState('')
  const submit = () => {
    const value = title.trim()
    if (!value) return
    onAdd(value)
    setTitle('')
  }
  return (
    <div className="flex gap-2">
      <Input
        value={title}
        maxLength={300}
        disabled={disabled}
        placeholder={t('task.subtask_placeholder')}
        aria-label={t('task.subtask_placeholder')}
        enterKeyHint="done"
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
            event.preventDefault()
            submit()
          }
        }}
        data-testid="subtask-input"
      />
      <IconButton
        label={t('task.subtask_add')}
        icon={<IconPlus size={18} />}
        variant="secondary"
        disabled={disabled || !title.trim()}
        onClick={submit}
        data-testid="subtask-add"
      />
    </div>
  )
}

export function SubtaskList({
  task,
  today,
  onOpen,
}: {
  task: TaskDetail
  today: string | undefined
  onOpen: (id: string) => void
}) {
  const { t } = useTranslation()
  const create = useCreateTask()
  const { toggle } = useCompleteToggle(today, 'top')
  const rows = flatten(task.subtasks ?? [], 0)
  const canAdd = task.depth < MAX_DEPTH

  const add = (title: string) =>
    create.mutate(
      { title, parent_id: task.id },
      {
        onSuccess: (result) => {
          if (result.queued) planToast.info(t('task.queued'), { placement: 'top' })
        },
        onError: (error) => planToast.error(errorMessage(error), { placement: 'top' }),
      },
    )

  return (
    <div className="space-y-2">
      {rows.length > 0 && (
        <div className="card divide-y divide-[rgb(var(--border))] overflow-hidden" data-testid="subtask-list">
          {rows.map(({ task: sub, level }) => {
            const closed = isClosed(sub)
            return (
              <div key={sub.id} className={clsx('flex items-start', INDENT[Math.min(level, 2)])} data-testid="subtask-row">
                <TaskCheck
                  done={closed}
                  priority={sub.priority}
                  title={sub.title}
                  onToggle={() => toggle(sub)}
                  testId="subtask-check"
                />
                <button
                  type="button"
                  onClick={() => onOpen(sub.id)}
                  className="min-w-0 flex-1 py-3 pr-4 text-left text-[15px] leading-snug"
                >
                  <span className={clsx('break-words', closed && 'text-[rgb(var(--text-muted))] line-through')}>
                    {sub.title}
                  </span>
                  {sub.subtask_counts.total > 0 && (
                    <span className="ml-2 tnum text-xs muted">
                      {sub.subtask_counts.done}/{sub.subtask_counts.total}
                    </span>
                  )}
                </button>
              </div>
            )
          })}
        </div>
      )}
      {canAdd ? (
        <AddRow onAdd={add} />
      ) : (
        <p className="text-xs muted">{t('task.subtask_max_depth')}</p>
      )}
    </div>
  )
}

/** For a task that does not exist yet: titles kept locally, created after the task. */
export function PendingSubtasks({ titles, onChange }: { titles: string[]; onChange: (next: string[]) => void }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      {titles.length > 0 && (
        <ul className="card divide-y divide-[rgb(var(--border))] overflow-hidden">
          {titles.map((title, index) => (
            <li key={`${title}-${index}`} className="flex min-h-tap items-center gap-3 pl-4">
              <span aria-hidden="true" className="h-[18px] w-[18px] shrink-0 rounded-full border-2 border-[rgb(var(--border))]" />
              <span className="min-w-0 flex-1 truncate text-[15px]">{title}</span>
              <IconButton
                label={t('task.subtask_remove', { title })}
                icon={<IconClose size={16} />}
                onClick={() => onChange(titles.filter((_, position) => position !== index))}
              />
            </li>
          ))}
        </ul>
      )}
      <AddRow onAdd={(title) => onChange([...titles, title])} disabled={titles.length >= 100} />
    </div>
  )
}
