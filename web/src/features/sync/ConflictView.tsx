/**
 * «Изменено на другом устройстве»: the fields that moved, the other device's
 * value next to yours, and the two ways out — «Оставить их версию» (your
 * change of those fields is dropped) or «Сохранить мою» (sent again without
 * the check, it overwrites). Used inline by the task editor and the journal
 * (an online 409) and by the sync sheet (a queued one).
 */
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { Button } from '@/components/ui'
import { IconAlert } from '@/components/ui/icons'
import { currentValue, describeValue, fieldLabel } from './labels'

export interface ConflictViewProps {
  fields: string[]
  /** Your values (as the write sends them). */
  mine: Record<string, unknown>
  /** The row as the server shows it now (null: it has no entry any more). */
  current: unknown
  busy?: boolean
  onKeepTheirs: () => void
  onKeepMine: () => void
  /** Extra line under the title (e.g. what the write was). */
  subtitle?: string
}

function findName(value: unknown, id: string, depth = 0): string | null {
  if (!value || typeof value !== 'object' || depth > 6) return null
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findName(item, id, depth + 1)
      if (found) return found
    }
    return null
  }
  const record = value as Record<string, unknown>
  if (record.id === id) {
    if (typeof record.name === 'string') return record.name
    if (typeof record.title === 'string') return record.title
  }
  for (const child of Object.values(record)) {
    const found = findName(child, id, depth + 1)
    if (found) return found
  }
  return null
}

/** A project / area / task name from whatever the planner already has in its caches. */
export function useNameResolver(): (id: string) => string | null {
  const queryClient = useQueryClient()
  return useCallback(
    (id: string) => {
      for (const [, data] of queryClient.getQueriesData({ queryKey: ['planner'] })) {
        const found = findName(data, id)
        if (found) return found
      }
      return null
    },
    [queryClient],
  )
}

export function ConflictView({ fields, mine, current, busy, onKeepTheirs, onKeepMine, subtitle }: ConflictViewProps) {
  const { t } = useTranslation()
  const resolve = useNameResolver()
  return (
    <div
      className="space-y-3 rounded-2xl border border-amber-400/35 bg-amber-400/10 px-4 py-3"
      role="alert"
      data-testid="conflict-view"
    >
      <div className="space-y-1">
        <p className="flex items-center gap-2 text-sm font-semibold text-amber-200">
          <IconAlert size={16} />
          {t('sync.conflict_title')}
        </p>
        {subtitle && <p className="text-xs text-amber-100/80">{subtitle}</p>}
        <p className="text-xs leading-relaxed text-amber-100/80">
          {current === null ? t('sync.conflict_gone') : t('sync.conflict_hint')}
        </p>
      </div>
      <dl className="space-y-2.5">
        {fields.map((field) => (
          <div key={field} className="space-y-1 rounded-xl bg-[rgb(var(--bg-sunken))] px-3 py-2" data-testid="conflict-field" data-field={field}>
            <dt className="label">{fieldLabel(field)}</dt>
            <dd className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 text-sm">
              <span className="muted">{t('sync.conflict_theirs')}</span>
              <span className="whitespace-pre-wrap break-words" data-testid="conflict-theirs">
                {describeValue(field, currentValue(field, current), resolve)}
              </span>
              <span className="muted">{t('sync.conflict_mine')}</span>
              <span className="whitespace-pre-wrap break-words font-medium" data-testid="conflict-mine">
                {describeValue(field, mine[field], resolve)}
              </span>
            </dd>
          </div>
        ))}
      </dl>
      <div className="grid gap-2">
        <Button variant="secondary" block className="min-h-tap" disabled={busy} onClick={onKeepTheirs} data-testid="conflict-keep-theirs">
          {t('sync.keep_theirs')}
        </Button>
        <Button block className="min-h-tap" loading={busy} onClick={onKeepMine} data-testid="conflict-keep-mine">
          {t('sync.keep_mine')}
        </Button>
      </div>
    </div>
  )
}
