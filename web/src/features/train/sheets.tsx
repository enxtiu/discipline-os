/** Small training sheets: start a workout, log cardio, plan a day. */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, Field, Input, SegmentedControl, Select, Sheet } from '@/components/ui'
import { IconCheck, IconFlame, IconPlus, IconTrain } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { PlanDay, Template } from '@/api/types'
import { dayLabel, number } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { exerciseName, weekdayShort } from './format'
import { useExercises, useQuickWorkout, useSetPlan } from './useTraining'
import { useStart } from './useStart'

export function StartWorkoutSheet({
  open,
  templates,
  onClose,
  onCardio,
}: {
  open: boolean
  templates: Template[]
  onClose: () => void
  onCardio: () => void
}) {
  const { t } = useTranslation()
  const start = useStart()
  return (
    <Sheet open={open} onClose={onClose} title={t('train.start')}>
      <div className="space-y-2 pb-2" data-testid="start-sheet">
        {templates.map((template) => (
          <button
            key={template.id}
            type="button"
            onClick={() => start.run(template.id)}
            disabled={start.pending}
            className="surface-sunken flex min-h-tap w-full items-center justify-between gap-3 rounded-2xl px-4 py-3 text-left"
          >
            <span className="min-w-0">
              <span className="block truncate font-medium">{template.name}</span>
              <span className="block truncate text-xs muted">
                {template.items.map((item) => exerciseName(item.exercise)).join(', ')}
              </span>
            </span>
            <IconTrain size={20} className="shrink-0 text-[rgb(var(--accent))]" />
          </button>
        ))}
        <div className="grid grid-cols-2 gap-2 pt-1">
          <Button variant="secondary" icon={<IconPlus size={18} />} loading={start.pending} onClick={() => start.run()}>
            {t('train.empty_workout')}
          </Button>
          <Button variant="secondary" icon={<IconFlame size={18} />} onClick={onCardio}>
            {t('train.cardio')}
          </Button>
        </div>
      </div>
    </Sheet>
  )
}

export function QuickCardioSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const quick = useQuickWorkout()
  const list = useExercises('', 'cardio', open)
  const options = useMemo(
    () => (list.data?.items ?? []).filter((exercise) => exercise.log_mode === 'cardio'),
    [list.data],
  )
  const [exerciseId, setExerciseId] = useState('')
  const [minutes, setMinutes] = useState('30')
  const [distance, setDistance] = useState('')

  useEffect(() => {
    if (open && !exerciseId && options.length) {
      const recent = new Set(list.data?.recent_ids ?? [])
      setExerciseId((options.find((option) => recent.has(option.id)) ?? options[0]).id)
    }
  }, [open, options, exerciseId, list.data])

  const save = () => {
    const mins = Number(minutes.replace(',', '.'))
    const kilometres = Number(distance.replace(',', '.'))
    if (!exerciseId || !(mins > 0)) return
    quick.mutate(
      {
        id: crypto.randomUUID(),
        exercise_id: exerciseId,
        duration_s: Math.max(60, Math.round(mins * 60)),
        distance_m: kilometres > 0 ? Math.round(kilometres * 1000) : undefined,
      },
      {
        onSuccess: (workout) => {
          toast.success(
            workout.kcal_burned != null
              ? t('train.cardio_saved_kcal', { kcal: number(workout.kcal_burned) })
              : t('train.cardio_saved'),
          )
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('train.cardio_title')}>
      <div className="space-y-4 pb-2">
        <Field label={t('train.activity')} htmlFor="cardio-exercise">
          <Select id="cardio-exercise" value={exerciseId} onChange={(event) => setExerciseId(event.target.value)}>
            {options.map((exercise) => (
              <option key={exercise.id} value={exercise.id}>
                {exerciseName(exercise)}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('train.duration_minutes')} htmlFor="cardio-minutes">
            <Input id="cardio-minutes" inputMode="decimal" value={minutes} onChange={(e) => setMinutes(e.target.value)} className="tnum" />
          </Field>
          <Field label={t('train.distance_km')} htmlFor="cardio-distance">
            <Input
              id="cardio-distance"
              inputMode="decimal"
              value={distance}
              onChange={(e) => setDistance(e.target.value)}
              className="tnum"
              placeholder="—"
            />
          </Field>
        </div>
        <p className="text-xs muted">{t('train.burned_not_budget')}</p>
        <Button block loading={quick.isPending} disabled={!exerciseId || !minutes.trim()} onClick={save}>
          {t('train.cardio_save')}
        </Button>
      </div>
    </Sheet>
  )
}

export function PlanDaySheet({
  day,
  templates,
  onClose,
}: {
  day: PlanDay | null
  templates: Template[]
  onClose: () => void
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const setPlan = useSetPlan()
  const [scope, setScope] = useState<'weekday' | 'date'>('weekday')
  useEffect(() => {
    if (day) setScope(day.source === 'date' ? 'date' : 'weekday')
  }, [day])
  if (!day) return <Sheet open={false} onClose={onClose}>{null}</Sheet>

  const choose = (payload: { template_id?: string; is_rest_day?: boolean } | null) => {
    setPlan.mutate(
      scope === 'weekday'
        ? { scope, weekday: day.weekday, payload }
        : { scope, date: day.date, payload },
      {
        onSuccess: () => {
          toast.success(t('train.plan_saved'))
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }
  const current = day.is_rest_day ? 'rest' : (day.template?.id ?? '')
  const option = (key: string, label: string, hint: string | null, onPick: () => void) => (
    <button
      key={key}
      type="button"
      onClick={onPick}
      disabled={setPlan.isPending}
      className={clsx(
        'flex min-h-tap w-full items-center justify-between gap-3 rounded-2xl border px-4 py-3 text-left',
        current === key ? 'border-aqua-400/50 bg-aqua-400/10' : 'border-[rgb(var(--border))]',
      )}
    >
      <span className="min-w-0">
        <span className="block truncate font-medium">{label}</span>
        {hint && <span className="block truncate text-xs muted">{hint}</span>}
      </span>
      {current === key && <IconCheck size={18} className="shrink-0 text-[rgb(var(--accent))]" />}
    </button>
  )

  return (
    <Sheet open={!!day} onClose={onClose} title={dayLabel(day.date, 'long')}>
      <div className="space-y-3 pb-2" data-testid="plan-sheet">
        <SegmentedControl
          value={scope}
          onChange={setScope}
          options={[
            { value: 'weekday', label: t('train.plan_every', { day: weekdayShort(day.weekday) }) },
            { value: 'date', label: t('train.plan_only_date') },
          ]}
        />
        {templates.map((template) =>
          option(
            template.id,
            template.name,
            t('train.exercise_count', { n: template.items.length }),
            () => choose({ template_id: template.id }),
          ),
        )}
        {option('rest', t('train.rest_day'), null, () => choose({ is_rest_day: true }))}
        {templates.length === 0 && <p className="text-sm muted">{t('train.plan_needs_template')}</p>}
        <Button variant="ghost" block onClick={() => choose(null)} loading={setPlan.isPending}>
          {scope === 'weekday' ? t('train.plan_clear_weekday') : t('train.plan_clear_date')}
        </Button>
      </div>
    </Sheet>
  )
}
