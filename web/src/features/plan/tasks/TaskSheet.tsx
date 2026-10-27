/**
 * The task editor — create and edit in one tall sheet.
 *
 * Edit: the server copy plus the fields you touched (see `draft.ts`);
 * «Сохранить» sends only what changed (`PATCH`). Create: the prefill (e.g. a
 * calendar slot's block) plus your input, with a client id, so a task created
 * offline is replayed once. Ticking the checkbox, subtasks, the timer and
 * delete act immediately; leaving with unsaved edits asks first. Server
 * validation errors are shown at the field they belong to.
 */
import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { Button, ErrorState, IconButton, Input, Select, Sheet, Skeleton } from '@/components/ui'
import { IconPin } from '@/components/ui/icons'
import { errorMessage, fieldErrors } from '@/lib/errors'
import { openTask } from '../events'
import { formatMinutes } from '../format'
import { IconPlay } from '../icons'
import {
  newId,
  useAreas,
  useCounts,
  useCreateTask,
  useDeleteTask,
  useProjects,
  useTags,
  useTask,
  useTimerStart,
  useUpdateTask,
} from '../queries'
import type { Ref, TaskIn } from '../types'
import { buildCreate, buildPatch, draftFromPrefill, draftFromTask, useTaskDraft, type Draft } from './draft'
import { BlockEditor, DueEditor, EstimateEditor, FieldSection, PriorityPicker, StatusPicker } from './fields'
import { ReminderEditor } from './ReminderEditor'
import { RepeatEditor } from './RepeatEditor'
import { PendingSubtasks, SubtaskList } from './SubtaskList'
import { TagEditor } from './TagEditor'
import { planToast } from './toast'
import { TaskCheck } from './TaskRow'
import { ConfirmButton, MarkdownField } from './ui'
import { isClosed, nextInstanceLabel, useCompleteToggle } from './useTaskActions'

type SectionKey =
  | 'title'
  | 'notes'
  | 'due'
  | 'block'
  | 'estimate'
  | 'repeat'
  | 'reminders'
  | 'tags'
  | 'context'
  | 'container'
  | 'subtasks'

/** Domain error codes → the field that caused them. */
const CODE_SECTION: Record<string, SectionKey> = {
  invalid_block: 'block',
  invalid_rrule: 'repeat',
  invalid_reminder: 'reminders',
  too_many_reminders: 'reminders',
  invalid_tag: 'tags',
  too_many_tags: 'tags',
  task_too_deep: 'subtasks',
  too_many_subtasks: 'subtasks',
}
/** Pydantic `loc` prefixes → field. */
const FIELD_SECTION: [string, SectionKey][] = [
  ['title', 'title'],
  ['notes_md', 'notes'],
  ['due_date', 'due'],
  ['due_time', 'due'],
  ['block', 'block'],
  ['estimate_minutes', 'estimate'],
  ['recurrence_rule', 'repeat'],
  ['recurrence_until', 'repeat'],
  ['reminders', 'reminders'],
  ['tags', 'tags'],
  ['context', 'context'],
  ['project_id', 'container'],
  ['area_id', 'container'],
  ['parent_id', 'subtasks'],
]

interface FormError {
  code?: string
  fields: Record<string, string>
}

function TitleInput({
  value,
  onChange,
  autoFocus,
  invalid,
}: {
  value: string
  onChange: (next: string) => void
  autoFocus?: boolean
  invalid?: boolean
}) {
  const { t } = useTranslation()
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [value])
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      maxLength={300}
      autoFocus={autoFocus}
      aria-label={t('task.title_label')}
      aria-invalid={invalid || undefined}
      placeholder={t('task.title_placeholder')}
      enterKeyHint="done"
      className="block w-full resize-none overflow-hidden border-b border-[rgb(var(--border))] bg-transparent py-2.5 text-lg font-semibold leading-snug placeholder:text-[rgb(var(--text-muted))]/70 focus:border-[rgb(var(--accent))] focus:outline-none"
      onChange={(event) => onChange(event.target.value.replace(/[\r\n]+/g, ' '))}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
          event.preventDefault()
          event.currentTarget.blur()
        }
      }}
      data-testid="task-title-input"
    />
  )
}

function EditorSkeleton() {
  return (
    <div className="space-y-4 pb-4" aria-hidden="true">
      <Skeleton className="h-8 w-3/4" />
      <Skeleton className="h-24" />
      <Skeleton className="h-12" />
      <Skeleton className="h-12" />
      <Skeleton className="h-12 w-2/3" />
    </div>
  )
}

/** Keep a container the task already points at selectable, even when it is no longer listed (completed, archived). */
function withCurrent<T extends { id: string; name: string }>(items: T[], current: Ref | null | undefined) {
  const list: { id: string; name: string }[] = items
  return current && !list.some((item) => item.id === current.id) ? [{ id: current.id, name: current.name }, ...list] : list
}

export function TaskSheet({
  open,
  taskId,
  prefill,
  onClose,
}: {
  open: boolean
  /** Edit an existing task… */
  taskId?: string
  /** …or create a new one with these fields prefilled. */
  prefill?: Partial<TaskIn>
  onClose: () => void
}) {
  const { t } = useTranslation()
  const mode: 'edit' | 'create' = taskId ? 'edit' : 'create'
  const query = useTask(open && taskId ? taskId : undefined)
  const task = taskId && query.data?.id === taskId ? query.data : undefined
  const today = useCounts().data?.date
  const projects = useProjects('open')
  const areas = useAreas()
  const tags = useTags()
  const create = useCreateTask()
  const createSubtask = useCreateTask()
  const update = useUpdateTask()
  const remove = useDeleteTask()
  const timerStart = useTimerStart()
  const { toggle } = useCompleteToggle(today)
  const contextId = useId()
  const projectId = useId()
  const areaId = useId()

  // every opening — and every other task opened from here — is a fresh session
  const [wasOpen, setWasOpen] = useState(open)
  const [session, setSession] = useState(0)
  if (wasOpen !== open) {
    setWasOpen(open)
    if (open) setSession((value) => value + 1)
  }
  const sessionKey = `${session}:${taskId ?? 'new'}`

  const base = useMemo(() => (task ? draftFromTask(task) : draftFromPrefill(prefill)), [task, prefill])
  const form = useTaskDraft(base, sessionKey)
  const draft = form.draft
  // one id per new-task session: a retried save can never create a duplicate
  const createId = useMemo(() => newId(), [sessionKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const [pendingSubtasks, setPendingSubtasks] = useState<string[]>([])
  const [formError, setFormError] = useState<FormError | null>(null)
  const [pendingAction, setPendingAction] = useState<null | (() => void)>(null)
  const [lastSession, setLastSession] = useState(sessionKey)
  if (lastSession !== sessionKey) {
    setLastSession(sessionKey)
    setPendingSubtasks([])
    setFormError(null)
    setPendingAction(null)
  }

  const errors = useMemo(() => {
    const out: Partial<Record<SectionKey, string>> = {}
    if (!formError) return out
    if (formError.code && CODE_SECTION[formError.code]) out[CODE_SECTION[formError.code]] = t(`errors.${formError.code}`)
    for (const key of Object.keys(formError.fields)) {
      const match = FIELD_SECTION.find(([prefix]) => key === prefix || key.startsWith(`${prefix}.`))
      if (match && !out[match[1]]) out[match[1]] = t('task.field_invalid')
    }
    return out
  }, [formError, t])

  const set = <K extends keyof Draft>(field: K, value: Draft[K]) => {
    form.set(field, value)
    if (formError) setFormError(null)
  }

  const hasEdits = mode === 'edit' ? form.dirty : draft.title.trim() !== '' || pendingSubtasks.length > 0
  const guard = (action: () => void) => {
    if (hasEdits) setPendingAction(() => action)
    else action()
  }

  const onSaveError = (error: unknown) => {
    planToast.error(errorMessage(error))
    setFormError({ code: error instanceof ApiError ? error.code : undefined, fields: fieldErrors(error) })
    setPendingAction(null)
  }

  const save = (after: () => void = onClose) => {
    setFormError(null)
    if (mode === 'create') {
      const body = buildCreate(draft, createId, prefill)
      if (!body.title) {
        setFormError({ fields: { title: 'required' } })
        setPendingAction(null)
        return
      }
      const subtasks = pendingSubtasks
      create.mutate(body, {
        onSuccess: (result) => {
          void (async () => {
            for (const title of subtasks) {
              try {
                await createSubtask.mutateAsync({ title, parent_id: createId })
              } catch (error) {
                planToast.error(errorMessage(error))
                break
              }
            }
          })()
          if (result.queued) planToast.info(t('task.queued'))
          else
            planToast.success(t('task.created', { title: body.title }), {
              label: t('task.open'),
              onClick: () => openTask(createId),
            })
          after()
        },
        onError: onSaveError,
      })
      return
    }
    if (!task) return
    const patch = buildPatch(base, draft, form.touched)
    if (Object.keys(patch).length === 0) {
      after()
      return
    }
    update.mutate(
      { id: task.id, patch },
      {
        onSuccess: (result) => {
          if (result.queued) planToast.info(t('task.queued'))
          else {
            const next = result.data.next_instance
            planToast.success(next ? t('task.saved_next', { when: nextInstanceLabel(next, today) }) : t('task.saved'))
          }
          form.reset()
          after()
        },
        onError: onSaveError,
      },
    )
  }

  const discard = () => {
    const action = pendingAction
    setPendingAction(null)
    form.reset()
    setPendingSubtasks([])
    action?.()
  }

  const startTimer = () => {
    if (!task) return
    timerStart.mutate(
      { task_id: task.id },
      {
        onSuccess: (result) =>
          result.queued ? planToast.info(t('task.queued')) : planToast.success(t('task.timer_started', { title: task.title })),
        onError: (error) => planToast.error(errorMessage(error)),
      },
    )
  }

  const deleteTask = () => {
    if (!task) return
    remove.mutate(
      { id: task.id },
      {
        onSuccess: (result) => {
          if (result.queued) planToast.info(t('task.queued'))
          else planToast.success(t('task.deleted'))
          form.reset()
          onClose()
        },
        onError: (error) => planToast.error(errorMessage(error)),
      },
    )
  }

  const projectOptions = withCurrent(projects.data?.items ?? [], task?.project)
  const areaOptions = withCurrent(areas.data?.items ?? [], task?.area)
  const tagNames = useMemo(
    () => [...(tags.data?.items ?? [])].sort((a, b) => b.open_tasks - a.open_tasks).map((tag) => tag.name),
    [tags.data],
  )
  const busy = create.isPending || update.isPending

  const footer = pendingAction ? (
    <div className="space-y-2" data-testid="task-unsaved">
      <p className="text-sm">{t('task.unsaved')}</p>
      <div className="flex gap-2">
        <Button
          className="flex-1"
          loading={busy}
          onClick={() => {
            const action = pendingAction
            save(() => {
              setPendingAction(null)
              action()
            })
          }}
          data-testid="task-unsaved-save"
        >
          {t('app.save')}
        </Button>
        <Button variant="secondary" className="flex-1" onClick={discard} data-testid="task-unsaved-discard">
          {t('task.discard')}
        </Button>
      </div>
    </div>
  ) : mode === 'create' ? (
    <Button block loading={create.isPending} disabled={!draft.title.trim()} onClick={() => save()} data-testid="task-save">
      {t('task.create')}
    </Button>
  ) : task && form.dirty ? (
    <Button block loading={update.isPending} onClick={() => save()} data-testid="task-save">
      {t('app.save')}
    </Button>
  ) : (
    <Button block variant="secondary" onClick={onClose} data-testid="task-close">
      {t('app.close')}
    </Button>
  )

  return (
    <Sheet
      open={open}
      onClose={() => guard(onClose)}
      size="tall"
      title={mode === 'edit' ? t('task.edit_title') : t('plan.add_task')}
      footer={footer}
    >
      {mode === 'edit' &&
        !task &&
        (query.isError ? (
          <ErrorState message={errorMessage(query.error)} onRetry={() => void query.refetch()} retryLabel={t('app.retry')} />
        ) : (
          <EditorSkeleton />
        ))}

      {(mode === 'create' || task) && (
        <div key={sessionKey} className="space-y-5 pb-4" data-testid="task-sheet" data-task-id={task?.id}>
          {task?.parent_id && task.parent_title && (
            <button
              type="button"
              className="-mb-3 flex min-h-tap max-w-full items-center text-left text-xs muted"
              onClick={() => {
                const parentId = task.parent_id
                if (parentId) guard(() => openTask(parentId))
              }}
            >
              <span className="truncate">{t('task.parent_prefix', { title: task.parent_title })}</span>
            </button>
          )}

          <div className="flex items-start gap-1">
            {task && (
              <TaskCheck
                done={isClosed(task)}
                priority={draft.priority}
                title={task.title}
                onToggle={() => toggle(task)}
                testId="task-sheet-check"
              />
            )}
            <div className="min-w-0 flex-1">
              <TitleInput
                value={draft.title}
                onChange={(value) => set('title', value)}
                autoFocus={mode === 'create' && !draft.title}
                invalid={!!errors.title}
              />
              {errors.title && <p className="mt-1 text-xs text-rose-400">{errors.title}</p>}
            </div>
            <IconButton
              label={draft.is_pinned ? t('task.unpin') : t('task.pin')}
              aria-pressed={draft.is_pinned}
              icon={<IconPin size={20} filled={draft.is_pinned} />}
              className={draft.is_pinned ? 'text-[rgb(var(--accent))]' : undefined}
              onClick={() => set('is_pinned', !draft.is_pinned)}
              data-testid="task-pin"
            />
          </div>

          <MarkdownField
            id={`${contextId}-notes`}
            label={t('task.notes')}
            value={draft.notes_md}
            onChange={(value) => set('notes_md', value)}
            placeholder={t('task.notes_placeholder')}
            error={errors.notes}
            testId="task-notes"
          />

          <FieldSection label={t('task.due')} error={errors.due} testId="task-due">
            <DueEditor
              date={draft.due_date}
              time={draft.due_time}
              today={today}
              onChange={(date, time) => {
                form.setMany({ due_date: date, due_time: time })
                if (formError) setFormError(null)
              }}
            />
          </FieldSection>

          <FieldSection label={t('task.block')} error={errors.block} hint={t('task.block_hint')} testId="task-block">
            <BlockEditor
              block={draft.block}
              fallbackDate={draft.due_date ?? today}
              fallbackStart={draft.due_time}
              defaultMinutes={draft.estimate_minutes ?? 60}
              onChange={(block) => set('block', block)}
            />
          </FieldSection>

          <FieldSection label={t('task.priority')} hint={t('task.priority_hint')} testId="task-priority">
            <PriorityPicker value={draft.priority} onChange={(priority) => set('priority', priority)} />
          </FieldSection>

          <div className="grid grid-cols-2 gap-3">
            <FieldSection label={t('task.project')} htmlFor={projectId} error={errors.container}>
              <Select
                id={projectId}
                value={draft.project_id ?? ''}
                onChange={(event) => set('project_id', event.target.value || null)}
                data-testid="task-project"
              >
                <option value="">{t('task.no_project')}</option>
                {projectOptions.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </Select>
            </FieldSection>
            <FieldSection label={t('task.area')} htmlFor={areaId}>
              <Select
                id={areaId}
                value={draft.area_id ?? ''}
                onChange={(event) => set('area_id', event.target.value || null)}
                data-testid="task-area"
              >
                <option value="">{t('task.no_area')}</option>
                {areaOptions.map((area) => (
                  <option key={area.id} value={area.id}>
                    {area.name}
                  </option>
                ))}
              </Select>
            </FieldSection>
          </div>

          <FieldSection
            label={t('task.estimate')}
            error={errors.estimate}
            hint={
              task && task.time.total_minutes > 0
                ? t('task.tracked', { time: formatMinutes(task.time.total_minutes) })
                : undefined
            }
            testId="task-estimate"
          >
            <EstimateEditor value={draft.estimate_minutes} onChange={(minutes) => set('estimate_minutes', minutes)} />
          </FieldSection>

          <FieldSection label={t('task.repeat')} error={errors.repeat} testId="task-repeat">
            <RepeatEditor
              rule={draft.recurrence_rule}
              until={draft.recurrence_until}
              onChange={(rule, until) => {
                form.setMany({ recurrence_rule: rule, recurrence_until: until })
                if (formError) setFormError(null)
              }}
            />
          </FieldSection>

          <FieldSection label={t('task.reminders')} error={errors.reminders} testId="task-reminders">
            <ReminderEditor
              reminders={draft.reminders}
              history={task?.reminders.filter((reminder) => reminder.status !== 'pending') ?? []}
              hasAnchor={!!(draft.due_date || draft.block)}
              today={today}
              onChange={(reminders) => set('reminders', reminders)}
            />
          </FieldSection>

          <FieldSection label={t('task.tags')} error={errors.tags} testId="task-tags">
            <TagEditor tags={draft.tags} suggestions={tagNames} onChange={(value) => set('tags', value)} />
          </FieldSection>

          <FieldSection label={t('task.context')} htmlFor={contextId} error={errors.context}>
            <div className="relative">
              <span aria-hidden="true" className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 muted">
                @
              </span>
              <Input
                id={contextId}
                className="pl-8"
                value={draft.context}
                maxLength={64}
                autoCapitalize="none"
                placeholder={t('task.context_placeholder')}
                onChange={(event) => set('context', event.target.value.replace(/^@+/, ''))}
                data-testid="task-context"
              />
            </div>
          </FieldSection>

          <FieldSection label={t('task.status_label')} testId="task-status">
            <StatusPicker value={draft.status} onChange={(status) => set('status', status)} />
          </FieldSection>

          <FieldSection
            label={
              task && task.subtask_counts.total > 0
                ? t('task.subtasks_count', { done: task.subtask_counts.done, total: task.subtask_counts.total })
                : t('task.subtasks')
            }
            error={errors.subtasks}
            testId="task-subtasks"
          >
            {task ? (
              <SubtaskList task={task} today={today} onOpen={(id) => guard(() => openTask(id))} />
            ) : (
              <PendingSubtasks titles={pendingSubtasks} onChange={setPendingSubtasks} />
            )}
          </FieldSection>

          {task && (
            <div className="space-y-2 border-t border-[rgb(var(--border))] pt-4">
              <Button
                variant="secondary"
                block
                icon={<IconPlay size={18} />}
                loading={timerStart.isPending}
                onClick={startTimer}
                data-testid="task-timer"
              >
                {t('task.start_timer')}
              </Button>
              <ConfirmButton
                label={t('task.delete')}
                confirmLabel={t('task.delete_confirm')}
                loading={remove.isPending}
                onConfirm={deleteTask}
                testId="task-delete"
              />
            </div>
          )}
        </div>
      )}
    </Sheet>
  )
}
