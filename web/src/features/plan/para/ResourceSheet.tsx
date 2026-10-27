/** Create / edit a resource: name, link, area, tags, Markdown notes; archive and delete. */
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, Select, Sheet } from '@/components/ui'
import { errorMessage } from '@/lib/errors'
import { IconArchive } from '../icons'
import { newId, useAreas, useCreateResource, useDeleteResource, useTags, useUpdateResource } from '../queries'
import type { Resource, ResourcePatchIn } from '../types'
import { TagEditor } from '../tasks/TagEditor'
import { planToast } from '../tasks/toast'
import { ConfirmButton, MarkdownField } from '../tasks/ui'

/** "example.com/x" → "https://example.com/x"; null when empty; undefined when not an http(s) link. */
export function normalizeUrl(text: string): string | null | undefined {
  const trimmed = text.trim()
  if (!trimmed) return null
  const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`
  try {
    const url = new URL(withScheme)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return undefined
    return withScheme.length <= 500 ? withScheme : undefined
  } catch {
    return undefined
  }
}

const sameList = (a: string[], b: string[]) => JSON.stringify(a) === JSON.stringify(b)

export function ResourceSheet({
  open,
  resource,
  defaultAreaId,
  onClose,
}: {
  open: boolean
  resource?: Resource | null
  defaultAreaId?: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const areas = useAreas()
  const tagList = useTags()
  const create = useCreateResource()
  const update = useUpdateResource()
  const remove = useDeleteResource()
  const ids = { name: useId(), url: useId(), area: useId(), notes: useId() }
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [areaId, setAreaId] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName(resource?.name ?? '')
    setUrl(resource?.url ?? '')
    setAreaId(resource?.area?.id ?? defaultAreaId ?? '')
    setTags(resource?.tags ?? [])
    setNotes(resource?.notes_md ?? '')
    setError(null)
    // re-initialise per opening / resource, not on every background refetch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, resource?.id, defaultAreaId])

  const normalized = normalizeUrl(url)
  const valid = name.trim().length > 0 && name.trim().length <= 200 && normalized !== undefined
  const pending = create.isPending || update.isPending
  const areaItems = areas.data?.items ?? []
  const areaOptions =
    resource?.area && !areaItems.some((area) => area.id === resource.area?.id)
      ? [{ id: resource.area.id, name: resource.area.name }, ...areaItems]
      : areaItems

  const done = (message: string) => (result: { queued: boolean }) => {
    planToast.success(result.queued ? t('task.queued') : message)
    onClose()
  }
  const onError = (failure: unknown) => setError(errorMessage(failure))

  const submit = () => {
    if (!valid || normalized === undefined) return
    setError(null)
    const notesValue = notes.trim() ? notes : null
    if (resource) {
      const patch: ResourcePatchIn = {}
      if (name.trim() !== resource.name) patch.name = name.trim()
      if (normalized !== resource.url) patch.url = normalized
      if ((areaId || null) !== (resource.area?.id ?? null)) patch.area_id = areaId || null
      if (!sameList(tags, resource.tags)) patch.tags = tags
      if (notesValue !== resource.notes_md) patch.notes_md = notesValue
      if (Object.keys(patch).length === 0) {
        onClose()
        return
      }
      update.mutate({ id: resource.id, patch }, { onSuccess: done(t('plan.resource_saved')), onError })
      return
    }
    create.mutate(
      {
        id: newId(),
        name: name.trim(),
        url: normalized ?? undefined,
        area_id: areaId || undefined,
        tags: tags.length ? tags : undefined,
        notes_md: notesValue ?? undefined,
      },
      { onSuccess: done(t('plan.resource_created', { name: name.trim() })), onError },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} size="tall" title={resource ? t('plan.resource_edit') : t('plan.resource_new')}>
      <div className="space-y-4 pb-2" data-testid="resource-sheet">
        <Field label={t('plan.resource_name')} htmlFor={ids.name}>
          <Input
            id={ids.name}
            value={name}
            maxLength={200}
            autoFocus={!resource}
            onChange={(event) => setName(event.target.value)}
            data-testid="resource-name"
          />
        </Field>
        <Field
          label={t('plan.resource_url')}
          htmlFor={ids.url}
          error={normalized === undefined ? t('plan.resource_url_invalid') : undefined}
        >
          <Input
            id={ids.url}
            type="url"
            inputMode="url"
            autoCapitalize="none"
            autoCorrect="off"
            placeholder="https://"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            data-testid="resource-url"
          />
        </Field>
        <Field label={t('task.area')} htmlFor={ids.area}>
          <Select id={ids.area} value={areaId} onChange={(event) => setAreaId(event.target.value)} data-testid="resource-area">
            <option value="">{t('task.no_area')}</option>
            {areaOptions.map((area) => (
              <option key={area.id} value={area.id}>
                {area.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t('task.tags')}>
          <TagEditor
            tags={tags}
            onChange={setTags}
            suggestions={(tagList.data?.items ?? []).map((tag) => tag.name)}
            testId="resource-tag-input"
          />
        </Field>
        <MarkdownField
          id={ids.notes}
          label={t('task.notes')}
          value={notes}
          onChange={setNotes}
          placeholder={t('plan.resource_notes_placeholder')}
          testId="resource-notes"
        />
        {error && <p className="text-sm text-rose-400">{error}</p>}
        <Button block disabled={!valid} loading={pending} onClick={submit} data-testid="resource-save">
          {resource ? t('app.save') : t('plan.resource_create')}
        </Button>
        {resource && (
          <div className="space-y-2 border-t border-[rgb(var(--border))] pt-3">
            <Button
              variant="ghost"
              block
              icon={<IconArchive size={18} />}
              loading={update.isPending}
              onClick={() =>
                update.mutate(
                  { id: resource.id, patch: { is_archived: !resource.is_archived } },
                  {
                    onSuccess: done(resource.is_archived ? t('plan.restored') : t('plan.resource_archived')),
                    onError,
                  },
                )
              }
              data-testid="resource-archive"
            >
              {resource.is_archived ? t('plan.restore') : t('plan.archive_action')}
            </Button>
            <ConfirmButton
              label={t('plan.resource_delete')}
              confirmLabel={t('plan.resource_delete_confirm')}
              loading={remove.isPending}
              onConfirm={() =>
                remove.mutate({ id: resource.id }, { onSuccess: done(t('plan.resource_deleted')), onError })
              }
              testId="resource-delete"
            />
          </div>
        )}
      </div>
    </Sheet>
  )
}
