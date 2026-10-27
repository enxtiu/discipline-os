/**
 * /plan/areas — areas of responsibility with this week's tracked time against
 * the weekly target (both from the server, week = your `week_start`), open
 * task / project counts, create, and reorder with up / down buttons.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { Button, EmptyState, ErrorState, IconButton, ProgressBar, Rows, SkeletonCard } from '@/components/ui'
import { IconChevronRight, IconPlus } from '@/components/ui/icons'
import { errorMessage } from '@/lib/errors'
import { formatMinutes } from '../format'
import { IconLayers } from '../icons'
import { planKeys, useAreas, useReorderAreas } from '../queries'
import type { Area } from '../types'
import { planToast } from '../tasks/toast'
import { Chip, ColorDot } from '../tasks/ui'
import { AreaSheet } from './AreaSheet'

export function AreaWeek({ area }: { area: Area }) {
  const { t } = useTranslation()
  const target = area.weekly_target_minutes
  if (!target) {
    return (
      <span className="block text-xs muted tnum">
        {t('plan.area_week_no_target', { time: formatMinutes(area.week_minutes) })}
      </span>
    )
  }
  return (
    <span className="block space-y-1" data-testid="area-target">
      <ProgressBar value={area.week_minutes} max={target} tone={area.week_minutes >= target ? 'accent' : 'violet'} />
      <span className="block text-xs muted tnum">
        {t('plan.area_week', { time: formatMinutes(area.week_minutes), target: formatMinutes(target) })}
      </span>
    </span>
  )
}

export function AreasPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const areas = useAreas()
  const reorder = useReorderAreas()
  const [creating, setCreating] = useState(false)
  const [ordering, setOrdering] = useState(false)
  const items = areas.data?.items ?? []

  const move = (index: number, delta: -1 | 1) => {
    const target = index + delta
    if (target < 0 || target >= items.length) return
    const next = [...items]
    ;[next[index], next[target]] = [next[target], next[index]]
    const key = planKeys.areas(false)
    const previous = queryClient.getQueryData(key)
    queryClient.setQueryData(key, { items: next })
    reorder.mutate(
      next.map((area) => area.id),
      {
        onError: (error) => {
          queryClient.setQueryData(key, previous)
          planToast.error(errorMessage(error))
        },
      },
    )
  }

  return (
    <div className="space-y-4" data-testid="areas-page">
      <div className="flex items-center justify-between gap-2">
        <p className="min-w-0 flex-1 px-1 text-sm muted">{t('plan.areas_hint')}</p>
        {items.length > 1 && (
          <Chip active={ordering} onClick={() => setOrdering((value) => !value)} data-testid="areas-reorder">
            {ordering ? t('app.done') : t('plan.reorder')}
          </Chip>
        )}
        <IconButton
          label={t('plan.area_new')}
          icon={<IconPlus size={22} />}
          variant="secondary"
          onClick={() => setCreating(true)}
          data-testid="area-new"
        />
      </div>

      {areas.isLoading && <SkeletonCard lines={3} />}
      {areas.isError && (
        <ErrorState message={errorMessage(areas.error)} onRetry={() => void areas.refetch()} retryLabel={t('app.retry')} />
      )}
      {areas.data && items.length === 0 && (
        <EmptyState
          icon={<IconLayers size={30} />}
          title={t('plan.areas_empty')}
          description={t('plan.areas_empty_hint')}
          action={
            <Button size="sm" className="min-h-tap" icon={<IconPlus size={16} />} onClick={() => setCreating(true)}>
              {t('plan.area_new')}
            </Button>
          }
        />
      )}
      {items.length > 0 && (
        <Rows>
          {items.map((area, index) => (
            <div key={area.id} className="flex items-center" data-testid="area-row" data-area-id={area.id}>
              <button
                type="button"
                onClick={() => navigate(`/plan/areas/${area.id}`)}
                className="min-w-0 flex-1 space-y-2 px-4 py-3.5 text-left transition-colors active:bg-[rgb(var(--border))]/30"
              >
                <span className="flex items-center gap-2">
                  <ColorDot color={area.color} className="h-2.5 w-2.5" />
                  <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{area.name}</span>
                </span>
                <AreaWeek area={area} />
                <span className="block text-xs muted tnum">
                  {t('plan.area_counts', { tasks: area.counts.open_tasks, projects: area.counts.active_projects })}
                </span>
              </button>
              {ordering && (
                <div className="flex shrink-0 pr-1">
                  <IconButton
                    label={t('plan.move_up', { name: area.name })}
                    icon={<IconChevronRight size={20} className="-rotate-90" />}
                    disabled={index === 0 || reorder.isPending}
                    onClick={() => move(index, -1)}
                    data-testid="area-up"
                  />
                  <IconButton
                    label={t('plan.move_down', { name: area.name })}
                    icon={<IconChevronRight size={20} className="rotate-90" />}
                    disabled={index === items.length - 1 || reorder.isPending}
                    onClick={() => move(index, 1)}
                    data-testid="area-down"
                  />
                </div>
              )}
            </div>
          ))}
        </Rows>
      )}

      <AreaSheet open={creating} onClose={() => setCreating(false)} />
    </div>
  )
}
