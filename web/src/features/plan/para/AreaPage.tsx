/**
 * /plan/areas/:id — an area with its weekly target, its projects, the open
 * tasks directly in it (with an inline add field), its resources, and edit /
 * archive / delete.
 */
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { Badge, Button, Card, EmptyState, ErrorState, Rows, SkeletonCard } from '@/components/ui'
import { errorMessage } from '@/lib/errors'
import { IconArchive, IconLayers } from '../icons'
import { useArea, useCounts, useDeleteArea, useUpdateArea } from '../queries'
import type { Resource } from '../types'
import { InlineQuickAdd } from '../tasks/InlineQuickAdd'
import { TaskGroup } from '../tasks/TaskList'
import { TaskRow } from '../tasks/TaskRow'
import { planToast } from '../tasks/toast'
import { BackLink, ColorDot, ConfirmButton } from '../tasks/ui'
import { AreaSheet } from './AreaSheet'
import { AreaWeek } from './AreasPage'
import { ProjectRow } from './ProjectsPage'
import { ResourceSheet } from './ResourceSheet'
import { ResourceRow } from './ResourcesPage'

/** Keyed by id: sheets and drafts never carry over from one area to the next. */
export function AreaPage() {
  const { id } = useParams()
  return <AreaBody key={id} id={id} />
}

function AreaBody({ id }: { id: string | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const detail = useArea(id)
  const today = useCounts().data?.date
  const update = useUpdateArea()
  const remove = useDeleteArea()
  const [editing, setEditing] = useState(false)
  const [resource, setResource] = useState<Resource | null | undefined>(undefined)

  if (detail.isLoading) return <SkeletonCard lines={4} />
  if (detail.isError || !detail.data) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <div className="space-y-3">
        <BackLink to="/plan/areas" label={t('plan.areas')} />
        {missing ? (
          <EmptyState icon={<IconLayers size={30} />} title={t('plan.area_missing')} />
        ) : (
          <ErrorState message={errorMessage(detail.error)} onRetry={() => void detail.refetch()} retryLabel={t('app.retry')} />
        )}
      </div>
    )
  }

  const { area, projects, tasks, resources } = detail.data

  const setArchived = (value: boolean) =>
    update.mutate(
      { id: area.id, patch: { is_archived: value } },
      {
        onSuccess: (result) => {
          if (result.queued) planToast.info(t('task.queued'))
          else
            planToast.success(value ? t('plan.area_archived') : t('plan.restored'), {
              label: t('task.undo'),
              onClick: () => update.mutate({ id: area.id, patch: { is_archived: !value } }),
            })
        },
        onError: (error) => planToast.error(errorMessage(error)),
      },
    )

  return (
    <div className="space-y-5" data-testid="area-page" data-area-id={area.id}>
      <BackLink to="/plan/areas" label={t('plan.areas')} />

      <header className="flex items-start gap-3">
        <ColorDot color={area.color} className="mt-2.5 h-3 w-3" />
        <div className="min-w-0 flex-1 space-y-1">
          <h2 className="h2 break-words">{area.name}</h2>
          {area.is_archived && <Badge>{t('plan.archived_badge')}</Badge>}
        </div>
        <Button variant="secondary" size="sm" className="min-h-tap" onClick={() => setEditing(true)} data-testid="area-edit">
          {t('app.edit')}
        </Button>
      </header>

      <Card className="space-y-2">
        <p className="label">{t('plan.this_week')}</p>
        <AreaWeek area={area} />
        {area.description && <p className="whitespace-pre-wrap pt-1 text-sm">{area.description}</p>}
      </Card>

      {projects.length > 0 && (
        <section className="space-y-1.5">
          <h2 className="px-1 text-sm font-semibold">{t('plan.projects')}</h2>
          <Rows>
            {projects.map((project) => (
              <ProjectRow key={project.id} project={project} today={today} />
            ))}
          </Rows>
        </section>
      )}

      {tasks.length > 0 ? (
        <TaskGroup title={t('plan.open_tasks')} count={tasks.length} testId="area-tasks">
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} today={today} showContainer={false} />
          ))}
        </TaskGroup>
      ) : (
        <p className="px-1 text-sm muted">{t('plan.area_no_tasks')}</p>
      )}
      <InlineQuickAdd defaults={{ area_id: area.id }} placeholder={t('plan.add_to_area')} />

      {resources.length > 0 && (
        <section className="space-y-1.5">
          <h2 className="px-1 text-sm font-semibold">{t('plan.resources')}</h2>
          <Rows>
            {resources.map((item) => (
              <ResourceRow key={item.id} resource={item} onOpen={setResource} />
            ))}
          </Rows>
        </section>
      )}

      <div className="space-y-2 border-t border-[rgb(var(--border))] pt-4">
        <Button
          variant="ghost"
          block
          icon={<IconArchive size={18} />}
          onClick={() => setArchived(!area.is_archived)}
          data-testid="area-archive"
        >
          {area.is_archived ? t('plan.restore') : t('plan.archive_action')}
        </Button>
        <ConfirmButton
          label={t('plan.area_delete')}
          confirmLabel={t('plan.area_delete_confirm')}
          loading={remove.isPending}
          onConfirm={() =>
            remove.mutate(
              { id: area.id },
              {
                onSuccess: (result) => {
                  planToast.success(result.queued ? t('task.queued') : t('plan.area_deleted'))
                  navigate('/plan/areas')
                },
                onError: (error) => planToast.error(errorMessage(error)),
              },
            )
          }
          testId="area-delete"
        />
      </div>

      <AreaSheet open={editing} area={area} onClose={() => setEditing(false)} />
      <ResourceSheet
        open={resource !== undefined}
        resource={resource ?? null}
        defaultAreaId={area.id}
        onClose={() => setResource(undefined)}
      />
    </div>
  )
}
