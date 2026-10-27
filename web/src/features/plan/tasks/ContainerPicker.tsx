/** Pick a project or an area for a task (inbox clarify) — one tap per choice. */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { EmptyState, Row, Rows, Sheet, Skeleton } from '@/components/ui'
import { IconFolder } from '../icons'
import { useAreas, useProjects } from '../queries'
import { ColorDot } from './ui'

export interface ContainerPick {
  kind: 'project' | 'area'
  id: string
  name: string
}

export function ContainerPicker({
  open,
  onClose,
  onPick,
}: {
  open: boolean
  onClose: () => void
  onPick: (pick: ContainerPick) => void
}) {
  const { t } = useTranslation()
  const projects = useProjects('open')
  const areas = useAreas()
  const projectItems = projects.data?.items ?? []
  const areaItems = areas.data?.items ?? []
  const loading = projects.isLoading || areas.isLoading

  return (
    <Sheet open={open} onClose={onClose} title={t('task.pick_container')}>
      <div className="space-y-4 pb-2" data-testid="container-picker">
        {loading && <Skeleton className="h-24 rounded-card" />}
        {!loading && projectItems.length === 0 && areaItems.length === 0 && (
          <EmptyState
            icon={<IconFolder size={28} />}
            title={t('task.pick_container_empty')}
            action={
              <Link to="/plan/projects" className="btn-secondary btn-sm min-h-tap" onClick={onClose}>
                {t('plan.projects')}
              </Link>
            }
          />
        )}
        {projectItems.length > 0 && (
          <div className="space-y-1.5">
            <p className="label px-1">{t('plan.projects')}</p>
            <Rows>
              {projectItems.map((project) => (
                <Row
                  key={project.id}
                  icon={<ColorDot color={project.color} className="h-2.5 w-2.5" />}
                  label={project.name}
                  hint={project.area?.name}
                  onClick={() => onPick({ kind: 'project', id: project.id, name: project.name })}
                />
              ))}
            </Rows>
          </div>
        )}
        {areaItems.length > 0 && (
          <div className="space-y-1.5">
            <p className="label px-1">{t('plan.areas')}</p>
            <Rows>
              {areaItems.map((area) => (
                <Row
                  key={area.id}
                  icon={<ColorDot color={area.color} className="h-2.5 w-2.5" />}
                  label={area.name}
                  onClick={() => onPick({ kind: 'area', id: area.id, name: area.name })}
                />
              ))}
            </Rows>
          </div>
        )}
      </div>
    </Sheet>
  )
}
