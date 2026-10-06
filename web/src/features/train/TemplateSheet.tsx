/** Create or edit a workout template: exercises in order with their targets. */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, Select, Sheet } from '@/components/ui'
import { IconChevronLeft, IconPlus, IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { Exercise, LogMode, Template, TemplateItemIn } from '@/api/types'
import { errorMessage } from '@/lib/errors'
import { ExercisePicker } from './ExercisePicker'
import { exerciseName } from './format'
import { useDeleteTemplate, useSaveTemplate } from './useTraining'

interface Item {
  key: string
  exercise: Exercise
  sets: string
  reps: string
  weight: string
  seconds: string
  minutes: string
  km: string
  rest: number
  /** fields set elsewhere (rep ranges, supersets, notes) survive an edit here */
  kept: Pick<TemplateItemIn, 'target_reps_min' | 'target_reps_max' | 'superset_group' | 'notes'>
}

const REST_OPTIONS = [45, 60, 90, 120, 150, 180, 240]
const num = (value: string): number | undefined => {
  const parsed = Number(value.replace(',', '.'))
  return value.trim() && Number.isFinite(parsed) && parsed > 0 ? parsed : undefined
}

function fromTemplate(template: Template | null): Item[] {
  return (template?.items ?? []).map((item) => ({
    key: item.id,
    exercise: item.exercise,
    sets: String(item.target_sets),
    reps: item.target_reps ? String(item.target_reps) : '',
    weight: item.target_weight_kg != null ? String(item.target_weight_kg) : '',
    seconds: item.target_duration_s ? String(item.target_duration_s) : '',
    minutes: item.target_duration_s ? String(Math.round(item.target_duration_s / 60)) : '',
    km: item.target_distance_m ? String(item.target_distance_m / 1000) : '',
    rest: item.rest_seconds,
    kept: {
      target_reps_min: item.target_reps_min ?? undefined,
      target_reps_max: item.target_reps_max ?? undefined,
      superset_group: item.superset_group ?? undefined,
      notes: item.notes ?? undefined,
    },
  }))
}

function toPayload(item: Item): TemplateItemIn {
  const mode = (item.exercise.log_mode ?? 'weight_reps') as LogMode
  const sets = Math.min(20, Math.max(1, Math.round(num(item.sets) ?? 3)))
  const base: TemplateItemIn = { ...item.kept, exercise_id: item.exercise.id, target_sets: sets, rest_seconds: item.rest }
  if (mode === 'weight_reps') return { ...base, target_reps: num(item.reps), target_weight_kg: num(item.weight) }
  if (mode === 'reps') return { ...base, target_reps: num(item.reps) }
  if (mode === 'duration') return { ...base, target_duration_s: num(item.seconds) }
  const minutes = num(item.minutes)
  const kilometres = num(item.km)
  return {
    ...base,
    target_sets: 1,
    target_duration_s: minutes ? Math.round(minutes * 60) : undefined,
    target_distance_m: kilometres ? Math.round(kilometres * 1000) : undefined,
  }
}

export function TemplateSheet({
  open,
  template,
  onClose,
}: {
  open: boolean
  template: Template | null
  onClose: () => void
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const save = useSaveTemplate()
  const remove = useDeleteTemplate()
  const [name, setName] = useState('')
  const [items, setItems] = useState<Item[]>([])
  const [picking, setPicking] = useState(false)

  useEffect(() => {
    if (!open) return
    setName(template?.name ?? '')
    setItems(fromTemplate(template))
  }, [open, template])

  const patch = (key: string, value: Partial<Item>) =>
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...value } : item)))
  const move = (index: number, delta: number) =>
    setItems((current) => {
      const next = [...current]
      const [item] = next.splice(index, 1)
      next.splice(Math.max(0, Math.min(next.length, index + delta)), 0, item)
      return next
    })

  const submit = () => {
    save.mutate(
      {
        existing: !!template,
        payload: {
          id: template?.id ?? crypto.randomUUID(),
          name: name.trim(),
          kind: items.every((item) => item.exercise.log_mode === 'cardio') && items.length ? 'cardio' : 'strength',
          estimated_minutes: template?.estimated_minutes ?? undefined,
          items: items.map(toPayload),
        },
      },
      {
        onSuccess: (saved) => {
          toast.success(t('train.template_saved', { name: saved.name }))
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <>
      <Sheet
        open={open && !picking}
        onClose={onClose}
        size="tall"
        title={template ? t('train.edit_template') : t('train.new_template')}
        footer={
          <div className="flex gap-2">
            {template && (
              <Button
                variant="ghost"
                className="text-rose-400"
                loading={remove.isPending}
                onClick={() =>
                  remove.mutate(template.id, {
                    onSuccess: () => {
                      toast.success(t('train.template_deleted'))
                      onClose()
                    },
                    onError: (error) => toast.error(errorMessage(error)),
                  })
                }
              >
                {t('app.delete')}
              </Button>
            )}
            <Button className="flex-1" loading={save.isPending} disabled={!name.trim() || items.length === 0} onClick={submit}>
              {t('app.save')}
            </Button>
          </div>
        }
      >
        <div className="space-y-3 pb-2">
          <Field label={t('train.template_name')} htmlFor="template-title">
            <Input
              id="template-title"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t('train.template_name_placeholder')}
            />
          </Field>
          {items.map((item, index) => (
            <TemplateItemRow
              key={item.key}
              item={item}
              first={index === 0}
              last={index === items.length - 1}
              onChange={(value) => patch(item.key, value)}
              onUp={() => move(index, -1)}
              onDown={() => move(index, 1)}
              onRemove={() => setItems((current) => current.filter((row) => row.key !== item.key))}
            />
          ))}
          <Button variant="secondary" block icon={<IconPlus size={18} />} onClick={() => setPicking(true)}>
            {t('train.add_exercise')}
          </Button>
        </div>
      </Sheet>
      <ExercisePicker
        open={open && picking}
        onClose={() => setPicking(false)}
        excludeIds={items.map((item) => item.exercise.id)}
        onPick={(exercise) => {
          setItems((current) => [
            ...current,
            {
              key: crypto.randomUUID(),
              exercise,
              sets: exercise.log_mode === 'cardio' ? '1' : '3',
              reps: exercise.log_mode === 'weight_reps' || exercise.log_mode === 'reps' ? '10' : '',
              weight: '',
              seconds: exercise.log_mode === 'duration' ? '60' : '',
              minutes: exercise.log_mode === 'cardio' ? '30' : '',
              km: '',
              rest: 90,
              kept: {},
            },
          ])
          setPicking(false)
        }}
      />
    </>
  )
}

function TemplateItemRow({
  item,
  first,
  last,
  onChange,
  onUp,
  onDown,
  onRemove,
}: {
  item: Item
  first: boolean
  last: boolean
  onChange: (value: Partial<Item>) => void
  onUp: () => void
  onDown: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const mode = (item.exercise.log_mode ?? 'weight_reps') as LogMode
  const name = exerciseName(item.exercise)
  const box = 'tnum h-tap px-2 text-center'
  return (
    <div className="card space-y-2 p-3" data-testid="template-item">
      <div className="flex items-center gap-1">
        <p className="min-w-0 flex-1 truncate text-[15px] font-medium">{name}</p>
        <button
          type="button"
          onClick={onUp}
          disabled={first}
          aria-label={t('train.move_up', { name })}
          className="flex h-tap w-tap items-center justify-center muted disabled:opacity-30"
        >
          <IconChevronLeft size={18} className="rotate-90" />
        </button>
        <button
          type="button"
          onClick={onDown}
          disabled={last}
          aria-label={t('train.move_down', { name })}
          className="flex h-tap w-tap items-center justify-center muted disabled:opacity-30"
        >
          <IconChevronLeft size={18} className="-rotate-90" />
        </button>
        <button
          type="button"
          onClick={onRemove}
          aria-label={t('train.remove_exercise', { name })}
          className="-mr-2 flex h-tap w-tap items-center justify-center muted"
        >
          <IconTrash size={18} />
        </button>
      </div>
      <div className="flex items-end gap-2">
        {mode !== 'cardio' && (
          <label className="w-16 shrink-0 text-xs muted">
            {t('train.sets')}
            <Input inputMode="numeric" value={item.sets} onChange={(e) => onChange({ sets: e.target.value })} className={box} />
          </label>
        )}
        {(mode === 'weight_reps' || mode === 'reps') && (
          <label className="min-w-0 flex-1 text-xs muted">
            {t('train.reps')}
            <Input inputMode="numeric" value={item.reps} onChange={(e) => onChange({ reps: e.target.value })} className={box} />
          </label>
        )}
        {mode === 'weight_reps' && (
          <label className="min-w-0 flex-1 text-xs muted">
            {t('train.weight_kg')}
            <Input inputMode="decimal" value={item.weight} onChange={(e) => onChange({ weight: e.target.value })} className={box} />
          </label>
        )}
        {mode === 'duration' && (
          <label className="min-w-0 flex-1 text-xs muted">
            {t('train.unit.sec')}
            <Input inputMode="numeric" value={item.seconds} onChange={(e) => onChange({ seconds: e.target.value })} className={box} />
          </label>
        )}
        {mode === 'cardio' && (
          <>
            <label className="min-w-0 flex-1 text-xs muted">
              {t('common.min')}
              <Input inputMode="decimal" value={item.minutes} onChange={(e) => onChange({ minutes: e.target.value })} className={box} />
            </label>
            <label className="min-w-0 flex-1 text-xs muted">
              {t('train.unit.km')}
              <Input inputMode="decimal" value={item.km} onChange={(e) => onChange({ km: e.target.value })} className={box} />
            </label>
          </>
        )}
        {mode !== 'cardio' && (
          <label className="w-24 shrink-0 text-xs muted">
            {t('train.rest')}
            <Select value={item.rest} onChange={(e) => onChange({ rest: Number(e.target.value) })} className="h-tap px-2">
              {REST_OPTIONS.map((seconds) => (
                <option key={seconds} value={seconds}>
                  {seconds >= 60 ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : `0:${seconds}`}
                </option>
              ))}
            </Select>
          </label>
        )}
      </div>
    </div>
  )
}
