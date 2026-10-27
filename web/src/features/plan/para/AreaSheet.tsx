/** Create / edit an area: name, colour, weekly target in hours, description. */
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, Sheet } from '@/components/ui'
import { errorMessage } from '@/lib/errors'
import { number } from '@/lib/format'
import { newId, useCreateArea, useUpdateArea } from '../queries'
import type { Area, AreaPatchIn } from '../types'
import { planToast } from '../tasks/toast'
import { ColorPicker } from './palette'

/** 300 → "5", 150 → "2,5" (the field is in hours, the API in minutes). */
export function hoursText(minutes: number | null | undefined): string {
  if (minutes == null) return ''
  const hours = minutes / 60
  if (Number.isInteger(hours)) return String(hours)
  return number(Math.round(hours * 100) / 100, 2).replace(/[,.]?0+$/, '')
}

/** "2,5" → 150; "" → null; out of range / not a number → undefined. */
export function parseHours(text: string): number | null | undefined {
  const trimmed = text.trim()
  if (!trimmed) return null
  const hours = Number(trimmed.replace(',', '.'))
  if (!Number.isFinite(hours) || hours < 0 || hours > 168) return undefined
  return Math.round(hours * 60)
}

export function AreaSheet({ open, area, onClose }: { open: boolean; area?: Area | null; onClose: () => void }) {
  const { t } = useTranslation()
  const create = useCreateArea()
  const update = useUpdateArea()
  const ids = { name: useId(), target: useId(), description: useId() }
  const [name, setName] = useState('')
  const [color, setColor] = useState<string | null>(null)
  const [target, setTarget] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName(area?.name ?? '')
    setColor(area?.color ?? null)
    setTarget(hoursText(area?.weekly_target_minutes))
    setDescription(area?.description ?? '')
    setError(null)
    // re-initialise per opening / area, not on every background refetch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, area?.id])

  const minutes = parseHours(target)
  const valid = name.trim().length > 0 && name.trim().length <= 120 && minutes !== undefined
  const pending = create.isPending || update.isPending

  const submit = () => {
    if (!valid || minutes === undefined) return
    setError(null)
    const onError = (failure: unknown) => setError(errorMessage(failure))
    if (area) {
      const patch: AreaPatchIn = {}
      if (name.trim() !== area.name) patch.name = name.trim()
      if ((color ?? null) !== (area.color ?? null)) patch.color = color
      if (minutes !== area.weekly_target_minutes) patch.weekly_target_minutes = minutes
      if ((description.trim() || null) !== area.description) patch.description = description.trim() || null
      if (Object.keys(patch).length === 0) {
        onClose()
        return
      }
      update.mutate(
        { id: area.id, patch },
        {
          onSuccess: (result) => {
            planToast.success(result.queued ? t('task.queued') : t('plan.area_saved'))
            onClose()
          },
          onError,
        },
      )
      return
    }
    create.mutate(
      {
        id: newId(),
        name: name.trim(),
        color: color ?? undefined,
        weekly_target_minutes: minutes ?? undefined,
        description: description.trim() || undefined,
      },
      {
        onSuccess: (result) => {
          planToast.success(result.queued ? t('task.queued') : t('plan.area_created', { name: name.trim() }))
          onClose()
        },
        onError,
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={area ? t('plan.area_edit') : t('plan.area_new')}>
      <div className="space-y-4 pb-2" data-testid="area-sheet">
        <Field label={t('plan.area_name')} htmlFor={ids.name}>
          <Input
            id={ids.name}
            value={name}
            maxLength={120}
            autoFocus={!area}
            onChange={(event) => setName(event.target.value)}
            data-testid="area-name"
          />
        </Field>
        <Field label={t('plan.color_label')}>
          <ColorPicker value={color} onChange={setColor} />
        </Field>
        <Field
          label={t('plan.weekly_target')}
          htmlFor={ids.target}
          hint={t('plan.weekly_target_hint')}
          error={minutes === undefined ? t('plan.weekly_target_invalid') : undefined}
        >
          <div className="flex items-center gap-2">
            <Input
              id={ids.target}
              inputMode="decimal"
              className="w-28 tnum"
              placeholder="5"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              data-testid="area-target-input"
            />
            <span className="text-sm muted">{t('plan.hours_per_week')}</span>
          </div>
        </Field>
        <Field label={t('plan.description')} htmlFor={ids.description}>
          <textarea
            id={ids.description}
            className="input min-h-[5rem] resize-y"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        {error && <p className="text-sm text-rose-400">{error}</p>}
        <Button block disabled={!valid} loading={pending} onClick={submit} data-testid="area-save">
          {area ? t('app.save') : t('plan.area_create')}
        </Button>
      </div>
    </Sheet>
  )
}
