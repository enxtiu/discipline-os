/**
 * /plan/projects — projects grouped by area (in the areas' order), each with
 * its progress, counts and deadline (rose when overdue — the server decides),
 * a status filter and «Новый проект».
 */
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Badge, Button, EmptyState, ErrorState, IconButton, ProgressBar, Rows, SegmentedControl, SkeletonCard } from '@/components/ui'
import { IconPlus } from '@/components/ui/icons'
import { errorMessage } from '@/lib/errors'
import { relativeDay } from '../format'
import { IconFolder } from '../icons'
import { useAreas, useCounts, useProjects } from '../queries'
import type { Project, Ref } from '../types'
import { ColorDot } from '../tasks/ui'
import { ProjectSheet } from './ProjectSheet'

type Filter = 'open' | 'completed' | 'all'

export function ProjectRow({ project, today }: { project: Project; today: string | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <button
      type="button"
      onClick={() => navigate(`/plan/projects/${project.id}`)}
      className="block w-full px-4 py-3.5 text-left transition-colors active:bg-[rgb(var(--border))]/30"
      data-testid="project-row"
      data-project-id={project.id}
    >
      <span className="flex items-center gap-2">
        <ColorDot color={project.color} className="h-2.5 w-2.5" />
        <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{project.name}</span>
        {project.status !== 'active' && <Badge>{t(`plan.project_status.${project.status}`)}</Badge>}
      </span>
      <ProgressBar value={project.progress} max={1} className="mt-2.5" />
      <span className="mt-1.5 flex items-center justify-between gap-2 text-xs muted tnum">
        <span>{t('plan.project_counts', { done: project.counts.done, total: project.counts.total })}</span>
        {project.deadline && (
          <span className={project.overdue ? 'font-medium text-rose-400' : undefined}>
            {t('plan.deadline_label', { date: relativeDay(project.deadline, today) })}
          </span>
        )}
      </span>
    </button>
  )
}

export function ProjectsPage() {
  const { t } = useTranslation()
  const [filter, setFilter] = useState<Filter>('open')
  const [creating, setCreating] = useState(false)
  const projects = useProjects(filter)
  const areas = useAreas()
  const today = useCounts().data?.date

  const groups = useMemo(() => {
    const order = new Map((areas.data?.items ?? []).map((area, index) => [area.id, index]))
    const byArea = new Map<string, { area: Ref | null; items: Project[] }>()
    for (const project of projects.data?.items ?? []) {
      const key = project.area?.id ?? ''
      const group = byArea.get(key) ?? { area: project.area, items: [] }
      group.items.push(project)
      byArea.set(key, group)
    }
    return [...byArea.values()].sort((a, b) => {
      if (!a.area) return 1
      if (!b.area) return -1
      return (order.get(a.area.id) ?? 999) - (order.get(b.area.id) ?? 999) || a.area.name.localeCompare(b.area.name)
    })
  }, [projects.data, areas.data])

  return (
    <div className="space-y-4" data-testid="projects-page">
      <div className="flex items-center gap-2">
        <SegmentedControl<Filter>
          value={filter}
          onChange={setFilter}
          className="flex-1 [&>button]:min-h-tap [&>button]:whitespace-nowrap [&>button]:px-2"
          options={[
            { value: 'open', label: t('plan.filter_open') },
            { value: 'completed', label: t('plan.filter_completed') },
            { value: 'all', label: t('plan.filter_all') },
          ]}
        />
        <IconButton
          label={t('plan.project_new')}
          icon={<IconPlus size={22} />}
          variant="secondary"
          onClick={() => setCreating(true)}
          data-testid="project-new"
        />
      </div>

      {projects.isLoading && <SkeletonCard lines={3} />}
      {projects.isError && (
        <ErrorState message={errorMessage(projects.error)} onRetry={() => void projects.refetch()} retryLabel={t('app.retry')} />
      )}
      {projects.data && groups.length === 0 && (
        <EmptyState
          icon={<IconFolder size={30} />}
          title={filter === 'completed' ? t('plan.projects_none_completed') : t('plan.projects_empty')}
          description={filter === 'completed' ? undefined : t('plan.projects_empty_hint')}
          action={
            filter !== 'completed' ? (
              <Button size="sm" className="min-h-tap" icon={<IconPlus size={16} />} onClick={() => setCreating(true)}>
                {t('plan.project_new')}
              </Button>
            ) : undefined
          }
        />
      )}
      {groups.map((group) => (
        <section key={group.area?.id ?? 'none'} className="space-y-1.5">
          <h2 className="flex items-center gap-2 px-1 text-sm font-semibold">
            {group.area && <ColorDot color={group.area.color} />}
            {group.area?.name ?? t('plan.no_area_group')}
          </h2>
          <Rows>
            {group.items.map((project) => (
              <ProjectRow key={project.id} project={project} today={today} />
            ))}
          </Rows>
        </section>
      ))}

      <ProjectSheet open={creating} onClose={() => setCreating(false)} />
    </div>
  )
}
