/**
 * /plan/resources — reference material (links, notes) filtered by area; tap to
 * edit, the link button opens it in a new tab.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, EmptyState, ErrorState, IconButton, Rows, SkeletonCard } from '@/components/ui'
import { IconPlus } from '@/components/ui/icons'
import { errorMessage } from '@/lib/errors'
import { IconBook, IconLink } from '../icons'
import { useAreas, useResources } from '../queries'
import type { Resource } from '../types'
import { Chip, ColorDot } from '../tasks/ui'
import { ResourceSheet } from './ResourceSheet'

function host(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function ResourceRow({ resource, onOpen }: { resource: Resource; onOpen: (resource: Resource) => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center" data-testid="resource-row" data-resource-id={resource.id}>
      <button
        type="button"
        onClick={() => onOpen(resource)}
        className="min-w-0 flex-1 px-4 py-3 text-left transition-colors active:bg-[rgb(var(--border))]/30"
      >
        <span className="block truncate text-[15px] font-medium">{resource.name}</span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs muted">
          {resource.url && <span className="max-w-[12rem] truncate">{host(resource.url)}</span>}
          {resource.area && (
            <span className="inline-flex items-center gap-1">
              <ColorDot color={resource.area.color} />
              {resource.area.name}
            </span>
          )}
          {resource.tags.slice(0, 3).map((tag) => (
            <span key={tag}>#{tag}</span>
          ))}
        </span>
      </button>
      {resource.url && (
        <a
          href={resource.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl text-[rgb(var(--accent))]"
          aria-label={t('plan.open_link', { name: resource.name })}
          data-testid="resource-link"
        >
          <IconLink size={20} />
        </a>
      )}
    </div>
  )
}

export function ResourcesPage() {
  const { t } = useTranslation()
  const [areaId, setAreaId] = useState('')
  const [editing, setEditing] = useState<Resource | null | undefined>(undefined)
  const resources = useResources(areaId || undefined)
  const areas = useAreas()
  const areaItems = areas.data?.items ?? []
  const items = resources.data?.items ?? []

  // the area filter only matters once there is something to filter
  const showFilter = areaItems.length > 0 && (!!areaId || items.length > 0)

  return (
    <div className="space-y-4" data-testid="resources-page">
      <div className="flex items-center justify-between gap-2">
        <p className="min-w-0 flex-1 px-1 text-sm muted">{t('plan.resources_hint')}</p>
        <IconButton
          label={t('plan.resource_new')}
          icon={<IconPlus size={22} />}
          variant="secondary"
          onClick={() => setEditing(null)}
          data-testid="resource-new"
        />
      </div>
      {showFilter && (
        <div className="scroll-x" role="group" aria-label={t('plan.filter_by_area')} data-testid="resource-filter">
          <Chip active={!areaId} onClick={() => setAreaId('')}>
            {t('plan.filter_all')}
          </Chip>
          {areaItems.map((area) => (
            <Chip key={area.id} active={areaId === area.id} onClick={() => setAreaId(area.id)}>
              <ColorDot color={area.color} />
              {area.name}
            </Chip>
          ))}
        </div>
      )}

      {resources.isLoading && <SkeletonCard lines={3} />}
      {resources.isError && (
        <ErrorState message={errorMessage(resources.error)} onRetry={() => void resources.refetch()} retryLabel={t('app.retry')} />
      )}
      {resources.data && items.length === 0 && (
        <EmptyState
          icon={<IconBook size={30} />}
          title={t('plan.resources_empty')}
          description={t('plan.resources_empty_hint')}
          action={
            <Button size="sm" className="min-h-tap" icon={<IconPlus size={16} />} onClick={() => setEditing(null)}>
              {t('plan.resource_new')}
            </Button>
          }
        />
      )}
      {items.length > 0 && (
        <Rows>
          {items.map((resource) => (
            <ResourceRow key={resource.id} resource={resource} onOpen={setEditing} />
          ))}
        </Rows>
      )}

      <ResourceSheet
        open={editing !== undefined}
        resource={editing ?? null}
        defaultAreaId={areaId || undefined}
        onClose={() => setEditing(undefined)}
      />
    </div>
  )
}
