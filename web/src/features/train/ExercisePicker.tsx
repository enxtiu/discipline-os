/**
 * Exercise picker: search the library (ru/en, aliases, typos), filter by muscle,
 * recent first when the field is empty, or create your own in place.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Badge, Button, Field, Input, Select, Sheet, Skeleton, Toggle } from '@/components/ui'
import { IconPlus, IconSearch } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { Equipment, Exercise, ExerciseType, MuscleGroup } from '@/api/types'
import { errorMessage } from '@/lib/errors'
import { exerciseName } from './format'
import { useCreateExercise, useExercises } from './useTraining'

const MUSCLE_FILTERS: MuscleGroup[] = [
  'chest',
  'back',
  'shoulders',
  'biceps',
  'triceps',
  'abs',
  'quads',
  'hamstrings',
  'glutes',
  'cardio',
]
const ALL_MUSCLES: MuscleGroup[] = [
  'chest', 'back', 'lats', 'traps', 'shoulders', 'biceps', 'triceps', 'forearms', 'abs', 'obliques',
  'lower_back', 'glutes', 'quads', 'hamstrings', 'calves', 'full_body', 'cardio', 'other',
]
const EQUIPMENT: Equipment[] = [
  'barbell', 'dumbbell', 'kettlebell', 'machine', 'cable', 'bodyweight', 'bands', 'cardio_machine', 'other',
]
const TYPES: ExerciseType[] = ['strength', 'cardio', 'mobility', 'sport', 'other']

export function ExercisePicker({
  open,
  onClose,
  onPick,
  title,
  excludeIds = [],
}: {
  open: boolean
  onClose: () => void
  onPick: (exercise: Exercise) => void
  title?: string
  excludeIds?: string[]
}) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [muscle, setMuscle] = useState<MuscleGroup | ''>('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(query.trim()), 200)
    return () => window.clearTimeout(handle)
  }, [query])
  useEffect(() => {
    if (!open) {
      setQuery('')
      setDebounced('')
      setMuscle('')
      setCreating(false)
    }
  }, [open])

  const list = useExercises(debounced, muscle, open)
  const items = useMemo(() => {
    const data = list.data
    if (!data) return []
    const hidden = new Set(excludeIds)
    const rows = data.items.filter((item) => !hidden.has(item.id))
    if (debounced || muscle) return rows
    // empty search: what you did recently first, then the library
    const rank = new Map((data.recent_ids ?? []).map((id, index) => [id, index]))
    return [...rows].sort((a, b) => (rank.get(a.id) ?? 999) - (rank.get(b.id) ?? 999))
  }, [list.data, excludeIds, debounced, muscle])
  const recent = new Set(list.data?.recent_ids ?? [])

  return (
    <Sheet open={open} onClose={onClose} title={title ?? t('train.pick_exercise')} size="tall">
      {creating ? (
        <CreateExerciseForm
          initialName={query}
          onCancel={() => setCreating(false)}
          onCreated={(exercise) => {
            setCreating(false)
            onPick(exercise)
          }}
        />
      ) : (
        <div className="space-y-3 pb-2">
          <div className="relative">
            <IconSearch size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 muted" />
            <Input
              id="exercise-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('train.search_placeholder')}
              className="pl-10"
              autoComplete="off"
              aria-label={t('train.search_placeholder')}
            />
          </div>
          <div className="scroll-x" role="group" aria-label={t('train.muscle_filter')}>
            {MUSCLE_FILTERS.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={muscle === value}
                onClick={() => setMuscle(muscle === value ? '' : value)}
                className={clsx(
                  'min-h-tap shrink-0 rounded-pill border px-4 text-sm',
                  muscle === value
                    ? 'border-aqua-400/50 bg-aqua-400/10 text-aqua-200'
                    : 'border-[rgb(var(--border))] text-[rgb(var(--text-muted))]',
                )}
              >
                {t(`train.muscle.${value}`)}
              </button>
            ))}
          </div>

          <div className="space-y-1" data-testid="exercise-results">
            {list.isLoading && (
              <div className="space-y-2">
                <Skeleton className="h-12" />
                <Skeleton className="h-12" />
              </div>
            )}
            {items.map((exercise) => (
              <button
                key={exercise.id}
                type="button"
                onClick={() => onPick(exercise)}
                className="flex min-h-tap w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left active:bg-[rgb(var(--border))]/40"
              >
                <span className="min-w-0">
                  <span className="block truncate text-[15px] font-medium">{exerciseName(exercise)}</span>
                  <span className="block truncate text-xs muted">
                    {t(`train.muscle.${exercise.muscle_group}`)}, {t(`train.equipment.${exercise.equipment}`).toLowerCase()}
                  </span>
                </span>
                <span className="flex shrink-0 gap-1">
                  {exercise.is_custom && <Badge tone="accent">{t('train.mine')}</Badge>}
                  {!debounced && recent.has(exercise.id) && <Badge>{t('train.recent')}</Badge>}
                </span>
              </button>
            ))}
            {!list.isLoading && items.length === 0 && (
              <p className="px-3 py-4 text-sm muted">{t('train.no_exercises')}</p>
            )}
          </div>
          <Button variant="secondary" block icon={<IconPlus size={18} />} onClick={() => setCreating(true)}>
            {t('train.create_exercise')}
          </Button>
        </div>
      )}
    </Sheet>
  )
}

function CreateExerciseForm({
  initialName,
  onCancel,
  onCreated,
}: {
  initialName: string
  onCancel: () => void
  onCreated: (exercise: Exercise) => void
}) {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const create = useCreateExercise()
  const [name, setName] = useState(initialName)
  const [muscle, setMuscle] = useState<MuscleGroup>('other')
  const [equipment, setEquipment] = useState<Equipment>('other')
  const [type, setType] = useState<ExerciseType>('strength')
  const [timed, setTimed] = useState(false)

  const save = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    create.mutate(
      {
        id: crypto.randomUUID(),
        ...(i18n.language === 'en' ? { name_en: trimmed } : { name_ru: trimmed }),
        muscle_group: muscle,
        equipment,
        exercise_type: type,
        is_timed: timed || type === 'cardio',
      },
      {
        onSuccess: (exercise) => {
          toast.success(t('train.exercise_created', { name: exerciseName(exercise) }))
          onCreated(exercise)
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <div className="space-y-4 pb-2">
      <Field label={t('train.exercise_name')} htmlFor="exercise-name">
        <Input id="exercise-name" value={name} onChange={(event) => setName(event.target.value)} autoFocus />
      </Field>
      <Field label={t('train.muscle_group')} htmlFor="exercise-muscle">
        <Select id="exercise-muscle" value={muscle} onChange={(event) => setMuscle(event.target.value as MuscleGroup)}>
          {ALL_MUSCLES.map((value) => (
            <option key={value} value={value}>
              {t(`train.muscle.${value}`)}
            </option>
          ))}
        </Select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('train.equipment_label')} htmlFor="exercise-equipment">
          <Select
            id="exercise-equipment"
            value={equipment}
            onChange={(event) => setEquipment(event.target.value as Equipment)}
          >
            {EQUIPMENT.map((value) => (
              <option key={value} value={value}>
                {t(`train.equipment.${value}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t('train.type_label')} htmlFor="exercise-type">
          <Select id="exercise-type" value={type} onChange={(event) => setType(event.target.value as ExerciseType)}>
            {TYPES.map((value) => (
              <option key={value} value={value}>
                {t(`train.type.${value}`)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0">
          <span className="block text-[15px] font-medium">{t('train.timed')}</span>
          <span className="block text-xs muted">{t('train.timed_hint')}</span>
        </span>
        <Toggle checked={timed || type === 'cardio'} onChange={setTimed} label={t('train.timed')} />
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" onClick={onCancel}>
          {t('app.cancel')}
        </Button>
        <Button className="flex-1" loading={create.isPending} disabled={!name.trim()} onClick={save}>
          {t('train.create_exercise')}
        </Button>
      </div>
    </div>
  )
}
