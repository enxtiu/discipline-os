/**
 * /plan/projects/:id — outcome, progress, tracked time, the description in
 * Markdown (editable), open tasks with an inline natural-language add field
 * (the project is the default), done tasks collapsed, and complete / archive /
 * delete.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { Badge, Button, Callout, Card, EmptyState, ErrorState, ProgressBar, SkeletonCard } from '@/components/ui'
import { errorMessage } from '@/lib/errors'
import { percent } from '@/lib/format'
import { IconCheck } from '@/components/ui/icons'
import { formatMinutes, relativeDay } from '../format'
import { IconArchive, IconFolder } from '../icons'
import { useCounts, useDeleteProject, useProject, useUpdateProject } from '../queries'
import type { ProjectPatchIn } from '../types'
import { InlineQuickAdd } from '../tasks/InlineQuickAdd'
import { TaskGroup } from '../tasks/TaskList'
import { TaskRow } from '../tasks/TaskRow'
import { planToast } from '../tasks/toast'
import { BackLink, ColorDot, ConfirmButton, MarkdownField } from '../tasks/ui'
import { ProjectSheet } from './ProjectSheet'

/** Keyed by id: local drafts never carry over from one project to the next. */
export function ProjectPage() {
  const { id } = useParams()
  return <ProjectBody key={id} id={id} />
}

function ProjectBody({ id }: { id: string | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const detail = useProject(id)
  const today = useCounts().data?.date
  const update = useUpdateProject()
  const remove = useDeleteProject()
  const [editing, setEditing] = useState(false)
  const [description, setDescription] = useState<string | null>(null)

  if (detail.isLoading) return <SkeletonCard lines={4} />
  if (detail.isError || !detail.data) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <div className="space-y-3">
        <BackLink to="/plan/projects" label={t('plan.projects')} />
        {missing ? (
          <EmptyState icon={<IconFolder size={30} />} title={t('plan.project_missing')} />
        ) : (
          <ErrorState message={errorMessage(detail.error)} onRetry={() => void detail.refetch()} retryLabel={t('app.retry')} />
        )}
      </div>
    )
  }

  const { project, tasks, done } = detail.data
  const descriptionValue = description ?? project.description_md ?? ''
  const descriptionDirty = description !== null && description !== (project.description_md ?? '')

  const patch = (change: ProjectPatchIn, message: string, undo?: ProjectPatchIn) =>
    update.mutate(
      { id: project.id, patch: change },
      {
        onSuccess: (result) => {
          if (result.queued) {
            planToast.info(t('task.queued'))
            return
          }
          planToast.success(
            message,
            undo
              ? {
                  label: t('task.undo'),
                  onClick: () =>
                    update.mutate({ id: project.id, patch: undo }, { onError: (error) => planToast.error(errorMessage(error)) }),
                }
              : undefined,
          )
        },
        onError: (error) => planToast.error(errorMessage(error)),
      },
    )

  const saveDescription = () =>
    update.mutate(
      { id: project.id, patch: { description_md: descriptionValue.trim() ? descriptionValue : null } },
      {
        onSuccess: (result) => {
          planToast.success(result.queued ? t('task.queued') : t('plan.description_saved'))
          setDescription(null)
        },
        onError: (error) => planToast.error(errorMessage(error)),
      },
    )

  const isOpen = project.status === 'active' || project.status === 'on_hold'

  return (
    <div className="space-y-5" data-testid="project-page" data-project-id={project.id}>
      <BackLink to="/plan/projects" label={t('plan.projects')} />

      <header className="flex items-start gap-3">
        <ColorDot color={project.color} className="mt-2.5 h-3 w-3" />
        <div className="min-w-0 flex-1 space-y-1">
          <h2 className="h2 break-words">{project.name}</h2>
          <div className="flex flex-wrap items-center gap-2 text-sm muted">
            {project.area && (
              <Link to={`/plan/areas/${project.area.id}`} className="inline-flex min-h-tap items-center gap-1.5">
                <ColorDot color={project.area.color} />
                {project.area.name}
              </Link>
            )}
            {project.status !== 'active' && <Badge>{t(`plan.project_status.${project.status}`)}</Badge>}
          </div>
        </div>
        <Button variant="secondary" size="sm" className="min-h-tap" onClick={() => setEditing(true)} data-testid="project-edit">
          {t('app.edit')}
        </Button>
      </header>

      {project.outcome && (
        <Callout title={t('plan.outcome')}>
          <span className="text-[rgb(var(--text))]">{project.outcome}</span>
        </Callout>
      )}

      <Card className="space-y-2" data-testid="project-progress">
        <div className="flex items-baseline justify-between gap-2 text-sm">
          <span className="tnum">{t('plan.project_counts', { done: project.counts.done, total: project.counts.total })}</span>
          <span className="tnum font-semibold">{percent(project.progress * 100)}</span>
        </div>
        <ProgressBar value={project.progress} max={1} />
        <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs muted tnum">
          <span>{t('plan.tracked', { time: formatMinutes(project.tracked_minutes) })}</span>
          {project.deadline && (
            <span className={project.overdue ? 'font-medium text-rose-400' : undefined}>
              {t('plan.deadline_label', { date: relativeDay(project.deadline, today) })}
            </span>
          )}
        </div>
      </Card>

      <div className="space-y-2">
        <MarkdownField
          id={`project-${project.id}-description`}
          label={t('plan.description')}
          value={descriptionValue}
          onChange={setDescription}
          placeholder={t('plan.description_placeholder')}
          testId="project-description"
          collapsible
        />
        {descriptionDirty && (
          <div className="flex gap-2">
            <Button className="flex-1" loading={update.isPending} onClick={saveDescription} data-testid="project-description-save">
              {t('app.save')}
            </Button>
            <Button variant="secondary" onClick={() => setDescription(null)}>
              {t('app.cancel')}
            </Button>
          </div>
        )}
      </div>

      {tasks.length > 0 ? (
        <TaskGroup title={t('plan.open_tasks')} count={tasks.length} testId="project-tasks">
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} today={today} showContainer={false} />
          ))}
        </TaskGroup>
      ) : (
        <p className="px-1 text-sm muted">{t('plan.project_no_tasks')}</p>
      )}
      <InlineQuickAdd defaults={{ project_id: project.id }} placeholder={t('plan.add_to_project')} />

      {done.length > 0 && (
        <TaskGroup title={t('plan.done_tasks')} count={done.length} collapsible defaultOpen={false} testId="project-done">
          {done.map((task) => (
            <TaskRow key={task.id} task={task} today={today} showContainer={false} />
          ))}
        </TaskGroup>
      )}

      <div className="space-y-2 border-t border-[rgb(var(--border))] pt-4">
        {isOpen ? (
          <Button
            variant="secondary"
            block
            icon={<IconCheck size={18} />}
            onClick={() => patch({ status: 'completed' }, t('plan.project_completed'), { status: project.status })}
            data-testid="project-complete"
          >
            {t('plan.project_complete')}
          </Button>
        ) : (
          <Button
            variant="secondary"
            block
            onClick={() => patch({ status: 'active', is_archived: false }, t('plan.project_reopened'))}
            data-testid="project-reopen"
          >
            {t('plan.project_reopen')}
          </Button>
        )}
        {project.status !== 'archived' && (
          <Button
            variant="ghost"
            block
            icon={<IconArchive size={18} />}
            onClick={() =>
              patch({ status: 'archived' }, t('plan.project_archived'), { status: project.status, is_archived: false })
            }
            data-testid="project-archive"
          >
            {t('plan.archive_action')}
          </Button>
        )}
        <ConfirmButton
          label={t('plan.project_delete')}
          confirmLabel={t('plan.project_delete_confirm')}
          loading={remove.isPending}
          onConfirm={() =>
            remove.mutate(
              { id: project.id },
              {
                onSuccess: (result) => {
                  planToast.success(result.queued ? t('task.queued') : t('plan.project_deleted'))
                  navigate('/plan/projects')
                },
                onError: (error) => planToast.error(errorMessage(error)),
              },
            )
          }
          testId="project-delete"
        />
      </div>

      <ProjectSheet open={editing} project={project} onClose={() => setEditing(false)} />
    </div>
  )
}
