/**
 * /plan/tags/:id — every task with a tag (optionally the done ones too), and
 * rename / colour / delete for the tag itself.
 */
import { useEffect, useId, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { Button, EmptyState, ErrorState, Field, Input, Sheet, Toggle } from '@/components/ui'
import { errorMessage } from '@/lib/errors'
import { IconTag } from '../icons'
import { useCounts, useDeleteTag, useTagView, useUpdateTag } from '../queries'
import type { Tag, TagView } from '../types'
import { ColorPicker } from '../para/palette'
import { TaskGroup, TaskListSkeleton } from '../tasks/TaskList'
import { TaskRow } from '../tasks/TaskRow'
import { planToast } from '../tasks/toast'
import { BackLink, ColorDot, ConfirmButton } from '../tasks/ui'

const TAG_RE = /^[\p{L}\p{N}_-]{1,64}$/u

function TagSheet({ open, tag, onClose }: { open: boolean; tag: Tag; onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const update = useUpdateTag()
  const remove = useDeleteTag()
  const nameId = useId()
  const [name, setName] = useState(tag.name)
  const [color, setColor] = useState<string | null>(tag.color)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName(tag.name)
    setColor(tag.color)
    setError(null)
    // re-initialise per opening, not on every background refetch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tag.id])

  const clean = name.trim().replace(/^#+/, '')
  const valid = TAG_RE.test(clean)

  const save = () => {
    if (!valid) return
    const patch: { name?: string; color?: string | null } = {}
    if (clean !== tag.name) patch.name = clean
    if ((color ?? null) !== (tag.color ?? null)) patch.color = color
    if (Object.keys(patch).length === 0) {
      onClose()
      return
    }
    update.mutate(
      { id: tag.id, patch },
      {
        onSuccess: (result) => {
          planToast.success(result.queued ? t('task.queued') : t('plan.tag_saved'))
          onClose()
        },
        onError: (failure) => setError(errorMessage(failure)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('plan.tag_edit')}>
      <div className="space-y-4 pb-2" data-testid="tag-sheet">
        <Field
          label={t('plan.tag_name')}
          htmlFor={nameId}
          error={!valid && name.trim() ? t('errors.invalid_tag') : undefined}
        >
          <Input
            id={nameId}
            value={name}
            maxLength={65}
            autoCapitalize="none"
            onChange={(event) => setName(event.target.value)}
            data-testid="tag-name"
          />
        </Field>
        <Field label={t('plan.color_label')}>
          <ColorPicker value={color} onChange={setColor} />
        </Field>
        {error && <p className="text-sm text-rose-400">{error}</p>}
        <Button block disabled={!valid} loading={update.isPending} onClick={save} data-testid="tag-save">
          {t('app.save')}
        </Button>
        <div className="border-t border-[rgb(var(--border))] pt-3">
          <ConfirmButton
            label={t('plan.tag_delete')}
            confirmLabel={t('plan.tag_delete_confirm')}
            loading={remove.isPending}
            onConfirm={() =>
              remove.mutate(
                { id: tag.id },
                {
                  onSuccess: (result) => {
                    planToast.success(result.queued ? t('task.queued') : t('plan.tag_deleted'))
                    onClose()
                    navigate('/plan/search')
                  },
                  onError: (failure) => setError(errorMessage(failure)),
                },
              )
            }
            testId="tag-delete"
          />
        </div>
      </div>
    </Sheet>
  )
}

export function TagPage() {
  const { t } = useTranslation()
  const { id } = useParams()
  const [includeDone, setIncludeDone] = useState(false)
  const [editing, setEditing] = useState(false)
  const view = useTagView(id, includeDone)
  const today = useCounts().data?.date
  // keep the header and the switch on screen while the other list loads
  const [last, setLast] = useState<TagView | undefined>(undefined)
  if (view.data && view.data !== last) setLast(view.data)
  const data = view.data ?? (last && last.tag.id === id ? last : undefined)

  const back = <BackLink to="/plan/search" label={t('plan.search')} />
  if (!data && view.isLoading) {
    return (
      <div className="space-y-3">
        {back}
        <TaskListSkeleton rows={3} />
      </div>
    )
  }
  if (!data) {
    const missing = view.error instanceof ApiError && view.error.status === 404
    return (
      <div className="space-y-3">
        {back}
        {missing ? (
          <EmptyState icon={<IconTag size={28} />} title={t('plan.tag_missing')} />
        ) : (
          <ErrorState message={errorMessage(view.error)} onRetry={() => void view.refetch()} retryLabel={t('app.retry')} />
        )}
      </div>
    )
  }

  const { tag } = data
  const items = view.data?.items
  return (
    <div className="space-y-4" data-testid="tag-page" data-tag-id={tag.id}>
      {back}
      <header className="flex items-center gap-3">
        <ColorDot color={tag.color} className="h-3 w-3" />
        <div className="min-w-0 flex-1">
          <h2 className="h2 truncate">#{tag.name}</h2>
          <p className="text-xs muted tnum">{t('plan.tag_open_count', { n: tag.open_tasks })}</p>
        </div>
        <Button variant="secondary" size="sm" className="min-h-tap" onClick={() => setEditing(true)} data-testid="tag-edit">
          {t('app.edit')}
        </Button>
      </header>
      <label className="flex min-h-tap cursor-pointer items-center justify-between gap-3 px-1">
        <span className="text-sm">{t('plan.include_done')}</span>
        <Toggle checked={includeDone} onChange={setIncludeDone} label={t('plan.include_done')} />
      </label>
      {!items ? (
        view.isError ? (
          <ErrorState message={errorMessage(view.error)} onRetry={() => void view.refetch()} retryLabel={t('app.retry')} />
        ) : (
          <TaskListSkeleton rows={3} />
        )
      ) : items.length === 0 ? (
        <EmptyState icon={<IconTag size={28} />} title={t('plan.tag_empty')} />
      ) : (
        <TaskGroup title={t('plan.tag_tasks')} count={items.length} testId="tag-tasks">
          {items.map((task) => (
            <TaskRow key={task.id} task={task} today={today} />
          ))}
        </TaskGroup>
      )}
      <TagSheet open={editing} tag={tag} onClose={() => setEditing(false)} />
    </div>
  )
}
