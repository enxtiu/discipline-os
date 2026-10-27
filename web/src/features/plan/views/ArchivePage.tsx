/**
 * /plan/archive — finished and archived things, each with a way back:
 * completed / cancelled / archived projects (restore), archived areas and
 * resources (restore), done and cancelled tasks (the checkbox reopens them).
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Badge, Button, EmptyState, ErrorState, Rows, SkeletonCard } from '@/components/ui'
import { errorMessage } from '@/lib/errors'
import { IconArchive } from '../icons'
import { useArchive, useCounts, useUpdateArea, useUpdateProject, useUpdateResource } from '../queries'
import { TaskGroup } from '../tasks/TaskList'
import { TaskRow } from '../tasks/TaskRow'
import { planToast } from '../tasks/toast'
import { ColorDot } from '../tasks/ui'

function ArchiveRow({
  title,
  color,
  badge,
  onOpen,
  onRestore,
  restoring,
}: {
  title: string
  color?: string | null
  badge?: string
  onOpen?: () => void
  onRestore: () => void
  restoring?: boolean
}) {
  const { t } = useTranslation()
  const body = (
    <span className="flex min-w-0 items-center gap-2">
      <ColorDot color={color} className="h-2.5 w-2.5" />
      <span className="min-w-0 truncate text-[15px] font-medium">{title}</span>
      {badge && <Badge className="shrink-0">{badge}</Badge>}
    </span>
  )
  return (
    <div className="flex items-center gap-2 pr-2" data-testid="archive-row">
      {onOpen ? (
        <button type="button" onClick={onOpen} className="min-h-tap min-w-0 flex-1 px-4 py-3 text-left">
          {body}
        </button>
      ) : (
        <div className="min-w-0 flex-1 px-4 py-3">{body}</div>
      )}
      <Button
        variant="secondary"
        size="sm"
        className="min-h-tap shrink-0"
        loading={restoring}
        onClick={onRestore}
        data-testid="archive-restore"
      >
        {t('plan.restore')}
      </Button>
    </div>
  )
}

export function ArchivePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const archive = useArchive()
  const today = useCounts().data?.date
  const updateProject = useUpdateProject()
  const updateArea = useUpdateArea()
  const updateResource = useUpdateResource()

  const restored = (result: { queued: boolean }) => planToast.success(result.queued ? t('task.queued') : t('plan.restored'))
  const failed = (error: unknown) => planToast.error(errorMessage(error))

  if (archive.isLoading) return <SkeletonCard lines={4} />
  if (archive.isError || !archive.data) {
    return <ErrorState message={errorMessage(archive.error)} onRetry={() => void archive.refetch()} retryLabel={t('app.retry')} />
  }
  const { projects, areas, resources, tasks } = archive.data
  const empty = projects.length + areas.length + resources.length + tasks.length === 0

  return (
    <div className="space-y-5" data-testid="archive-page">
      {empty && <EmptyState icon={<IconArchive size={30} />} title={t('plan.archive_empty')} description={t('plan.archive_empty_hint')} />}

      {projects.length > 0 && (
        <section className="space-y-1.5" data-testid="archive-projects">
          <h2 className="px-1 text-sm font-semibold">{t('plan.projects')}</h2>
          <Rows>
            {projects.map((project) => (
              <ArchiveRow
                key={project.id}
                title={project.name}
                color={project.color}
                badge={t(`plan.project_status.${project.status}`)}
                onOpen={() => navigate(`/plan/projects/${project.id}`)}
                onRestore={() =>
                  updateProject.mutate(
                    { id: project.id, patch: { status: 'active', is_archived: false } },
                    { onSuccess: restored, onError: failed },
                  )
                }
              />
            ))}
          </Rows>
        </section>
      )}

      {areas.length > 0 && (
        <section className="space-y-1.5" data-testid="archive-areas">
          <h2 className="px-1 text-sm font-semibold">{t('plan.areas')}</h2>
          <Rows>
            {areas.map((area) => (
              <ArchiveRow
                key={area.id}
                title={area.name}
                color={area.color}
                onOpen={() => navigate(`/plan/areas/${area.id}`)}
                onRestore={() =>
                  updateArea.mutate({ id: area.id, patch: { is_archived: false } }, { onSuccess: restored, onError: failed })
                }
              />
            ))}
          </Rows>
        </section>
      )}

      {resources.length > 0 && (
        <section className="space-y-1.5" data-testid="archive-resources">
          <h2 className="px-1 text-sm font-semibold">{t('plan.resources')}</h2>
          <Rows>
            {resources.map((resource) => (
              <ArchiveRow
                key={resource.id}
                title={resource.name}
                onRestore={() =>
                  updateResource.mutate(
                    { id: resource.id, patch: { is_archived: false } },
                    { onSuccess: restored, onError: failed },
                  )
                }
              />
            ))}
          </Rows>
        </section>
      )}

      {tasks.length > 0 && (
        <TaskGroup title={t('plan.archive_tasks')} count={tasks.length} testId="archive-tasks">
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} today={today} />
          ))}
        </TaskGroup>
      )}
      {tasks.length > 0 && <p className="px-1 text-xs muted">{t('plan.archive_tasks_hint')}</p>}
    </div>
  )
}
