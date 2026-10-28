/**
 * The live workout logger — and the editable record of a finished workout.
 *
 * One tap per set: rows come pre-filled from last time (or the template), the
 * check button saves the set (queued offline, idempotent by its id) and starts
 * the rest timer. Finishing computes duration, burned calories and records on
 * the server; burned calories are shown on their own, never as food budget.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import clsx from 'clsx'

import { Button, Callout, Card, ErrorState, Field, Input, Sheet, SkeletonCard } from '@/components/ui'
import { IconCheck, IconChevronLeft, IconClock, IconFlame, IconPlus, IconStar, IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { Exercise, LogMode, Workout, WorkoutExercise, WorkoutSet, WorkoutSetIn } from '@/api/types'
import { dayLabel, number } from '@/lib/format'
import i18n from '@/i18n'
import { errorMessage } from '@/lib/errors'
import { ExercisePicker } from './ExercisePicker'
import { ExerciseHistorySheet } from './ExerciseHistorySheet'
import { clock, exerciseName, minutesLabel, recordExerciseName, recordValue, setSummary, weight } from './format'
import {
  useDeleteWorkout,
  useFinishWorkout,
  usePendingSets,
  useSaveAsTemplate,
  useSetWrite,
  useWorkout,
} from './useTraining'

// --------------------------------------------------------------------------- //
// Local draft (exercises added before their first set, rest timer) — survives a reload
// --------------------------------------------------------------------------- //
interface Draft {
  extra: Exercise[]
  hidden: string[]
  rest: { endsAt: number; total: number } | null
}

const EMPTY_DRAFT: Draft = { extra: [], hidden: [], rest: null }

function draftKey(id: string): string {
  return `dsos:workout:${id}`
}

function loadDraft(id: string): Draft {
  try {
    const raw = window.localStorage.getItem(draftKey(id))
    return raw ? { ...EMPTY_DRAFT, ...(JSON.parse(raw) as Draft) } : EMPTY_DRAFT
  } catch {
    return EMPTY_DRAFT
  }
}

function useDraft(id: string | undefined) {
  const [draft, setDraft] = useState<Draft>(() => (id ? loadDraft(id) : EMPTY_DRAFT))
  useEffect(() => {
    if (!id) return
    try {
      window.localStorage.setItem(draftKey(id), JSON.stringify(draft))
    } catch {
      /* private mode: the draft lives for this page only */
    }
  }, [id, draft])
  return [draft, setDraft] as const
}

export function clearDraft(id: string): void {
  try {
    window.localStorage.removeItem(draftKey(id))
  } catch {
    /* ignore */
  }
}

// --------------------------------------------------------------------------- //
// Set rows
// --------------------------------------------------------------------------- //
type RowState = 'planned' | 'saving' | 'saved' | 'queued'

interface Row {
  id: string
  warmup: boolean
  weight: string
  reps: string
  seconds: string
  minutes: string
  km: string
  state: RowState
  dirty: boolean
}

const parse = (value: string): number | null => {
  const cleaned = value.replace(',', '.').trim()
  if (!cleaned) return null
  const parsed = Number(cleaned)
  return Number.isFinite(parsed) ? parsed : null
}

const asText = (value: number | null | undefined, digits = 2): string => {
  if (value === null || value === undefined) return ''
  const text = String(Number(value.toFixed(digits)))
  return i18n.language === 'en' ? text : text.replace('.', ',')
}

function rowFromValues(
  id: string,
  values: { weight_kg?: number | null; reps?: number | null; duration_s?: number | null; distance_m?: number | null },
  state: RowState,
  warmup = false,
): Row {
  return {
    id,
    warmup,
    weight: asText(values.weight_kg),
    reps: values.reps ? String(values.reps) : '',
    seconds: values.duration_s ? String(values.duration_s) : '',
    minutes: values.duration_s ? asText(values.duration_s / 60, 1) : '',
    km: values.distance_m ? asText(values.distance_m / 1000, 2) : '',
    state,
    dirty: false,
  }
}

function rowFromSet(set: WorkoutSet): Row {
  return rowFromValues(set.id, set, 'saved', set.is_warmup ?? false)
}

function rowFromQueued(setId: string, body: WorkoutSetIn): Row {
  return rowFromValues(setId, body, 'queued', body.is_warmup ?? false)
}

function plannedRows(entry: WorkoutExercise, inProgress: boolean, queued: WorkoutSetIn[]): Row[] {
  if (!inProgress) return []
  const target = entry.target
  const working =
    entry.sets.filter((set) => !set.is_warmup).length + queued.filter((body) => !body.is_warmup).length
  const wanted = target ? target.target_sets : entry.sets.length + queued.length === 0 ? 1 : 0
  const previous = (entry.previous ?? []).filter((set) => !set.is_warmup)
  const rows: Row[] = []
  for (let index = working; index < wanted; index += 1) {
    const prev = previous[index] ?? previous[previous.length - 1]
    const values = prev ?? {
      weight_kg: target?.target_weight_kg,
      reps: target?.target_reps ?? target?.target_reps_max,
      duration_s: target?.target_duration_s,
      distance_m: target?.target_distance_m,
    }
    rows.push(rowFromValues(crypto.randomUUID(), values, 'planned'))
  }
  return rows
}

function toPayload(row: Row, mode: LogMode, exerciseId: string, sortOrder: number, setIndex: number): WorkoutSetIn | null {
  const body: WorkoutSetIn = { exercise_id: exerciseId, sort_order: sortOrder, set_index: setIndex, is_warmup: row.warmup }
  if (mode === 'weight_reps') {
    const reps = parse(row.reps)
    if (!reps) return null
    return { ...body, reps: Math.round(reps), weight_kg: parse(row.weight) ?? undefined }
  }
  if (mode === 'reps') {
    const reps = parse(row.reps)
    return reps ? { ...body, reps: Math.round(reps) } : null
  }
  if (mode === 'duration') {
    const seconds = parse(row.seconds)
    return seconds ? { ...body, duration_s: Math.round(seconds) } : null
  }
  const minutes = parse(row.minutes)
  const kilometres = parse(row.km)
  if (!minutes && !kilometres) return null
  return {
    ...body,
    duration_s: minutes ? Math.round(minutes * 60) : undefined,
    distance_m: kilometres ? Math.round(kilometres * 1000) : undefined,
  }
}

/** What a row holds, for "did the user change it while it was saving?" */
function valuesKey(row: Row): string {
  return [row.warmup, row.weight, row.reps, row.seconds, row.minutes, row.km].join('|')
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //
export function WorkoutPage() {
  const { id } = useParams()
  // a new id is a new screen: drafts and row state never leak between workouts
  return <WorkoutScreen key={id} id={id} />
}

function WorkoutScreen({ id }: { id: string | undefined }) {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const toast = useToast()
  const workout = useWorkout(id)
  const [draft, setDraft] = useDraft(id)
  const pending = usePendingSets(id)
  const [picking, setPicking] = useState(false)
  const [finishing, setFinishing] = useState(false)
  const [summary, setSummary] = useState(false)
  const [historyOf, setHistoryOf] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const remove = useDeleteWorkout()

  const data = workout.data
  const inProgress = data?.status === 'in_progress'

  const entries = useMemo(() => {
    if (!data) return []
    const present = new Set(data.exercises.map((entry) => entry.exercise.id))
    const hidden = new Set(draft.hidden)
    const extra: WorkoutExercise[] = draft.extra
      .filter((exercise) => !present.has(exercise.id))
      .map((exercise) => ({
        exercise,
        target: null,
        sets: [],
        previous: [],
        previous_day: null,
        bests: {},
      }))
    const queuedFor = new Set([...pending.values()].map((body) => body.exercise_id))
    return [...data.exercises, ...(inProgress ? extra : [])].filter(
      (entry) => entry.sets.length > 0 || queuedFor.has(entry.exercise.id) || !hidden.has(entry.exercise.id),
    )
  }, [data, draft.extra, draft.hidden, inProgress, pending])

  if (workout.isLoading) {
    return (
      <div className="space-y-3">
        <SkeletonCard lines={2} />
        <SkeletonCard lines={4} />
      </div>
    )
  }
  if (workout.isError || !data) {
    return <ErrorState message={errorMessage(workout.error)} onRetry={() => void workout.refetch()} retryLabel={t('app.retry')} />
  }

  const startRest = (seconds: number) => {
    if (!inProgress || seconds <= 0) return
    setDraft((current) => ({ ...current, rest: { endsAt: Date.now() + seconds * 1000, total: seconds } }))
  }

  const discardOrDelete = () => {
    if (!confirmDelete) {
      setConfirmDelete(true)
      window.setTimeout(() => setConfirmDelete(false), 4000)
      return
    }
    remove.mutate(data.id, {
      onSuccess: () => {
        clearDraft(data.id)
        toast.success(inProgress ? t('train.discarded') : t('train.deleted'))
        navigate('/train', { replace: true })
      },
      onError: (error) => toast.error(errorMessage(error)),
    })
  }

  return (
    <div className="space-y-4 pb-24">
      <WorkoutHeader workout={data} onBack={() => navigate('/train')} onFinish={() => setFinishing(true)} onSummary={() => setSummary(true)} />

      {entries.length === 0 && (
        <Callout tone="info">{t('train.empty_workout_hint')}</Callout>
      )}

      {entries.map((entry, index) => (
        <ExerciseCard
          key={entry.exercise.id}
          workout={data}
          entry={entry}
          position={index}
          inProgress={inProgress}
          queued={pending}
          onRest={startRest}
          onOpenHistory={() => setHistoryOf(entry.exercise.id)}
          onHide={() =>
            setDraft((current) => ({
              ...current,
              extra: current.extra.filter((exercise) => exercise.id !== entry.exercise.id),
              hidden: [...current.hidden, entry.exercise.id],
            }))
          }
        />
      ))}

      <Button variant="secondary" block icon={<IconPlus size={18} />} onClick={() => setPicking(true)}>
        {t('train.add_exercise')}
      </Button>

      <Button
        variant="ghost"
        block
        className="text-rose-400"
        icon={<IconTrash size={18} />}
        loading={remove.isPending}
        onClick={discardOrDelete}
      >
        {confirmDelete
          ? t('train.confirm_tap_again')
          : inProgress
            ? t('train.discard_workout')
            : t('train.delete_workout')}
      </Button>

      {inProgress && draft.rest && (
        <RestBar
          rest={draft.rest}
          onChange={(rest) => setDraft((current) => ({ ...current, rest }))}
        />
      )}

      <ExercisePicker
        open={picking}
        onClose={() => setPicking(false)}
        excludeIds={entries.map((entry) => entry.exercise.id)}
        onPick={(exercise) => {
          setDraft((current) => ({
            ...current,
            extra: [...current.extra.filter((item) => item.id !== exercise.id), exercise],
            hidden: current.hidden.filter((item) => item !== exercise.id),
          }))
          setPicking(false)
        }}
      />
      <FinishSheet
        open={finishing}
        workout={data}
        pendingSets={pending.size}
        onClose={() => setFinishing(false)}
        onFinished={() => {
          clearDraft(data.id)
          setFinishing(false)
          setSummary(true)
        }}
      />
      <SummarySheet open={summary} workout={data} onClose={() => setSummary(false)} onDone={() => navigate('/train')} />
      <ExerciseHistorySheet exerciseId={historyOf} onClose={() => setHistoryOf(null)} />
    </div>
  )
}

function WorkoutHeader({
  workout,
  onBack,
  onFinish,
  onSummary,
}: {
  workout: Workout
  onBack: () => void
  onFinish: () => void
  onSummary: () => void
}) {
  const { t } = useTranslation()
  const inProgress = workout.status === 'in_progress'
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!inProgress) return
    const handle = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(handle)
  }, [inProgress])
  const elapsed = (now - new Date(workout.started_at).getTime()) / 1000

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={onBack}
        aria-label={t('train.back_to_train')}
        className="-ml-3 flex h-tap w-tap shrink-0 items-center justify-center muted"
      >
        <IconChevronLeft size={22} />
      </button>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-lg font-semibold">{workout.title ?? t('train.workout_default_title')}</h1>
        <p className="tnum text-sm muted" aria-live="off">
          {inProgress ? (
            <span className="text-[rgb(var(--accent))]" data-testid="workout-clock">
              {clock(elapsed)}
            </span>
          ) : (
            <>
              {dayLabel(workout.local_day)}, {minutesLabel(workout.duration_s)}
            </>
          )}
        </p>
      </div>
      {inProgress ? (
        <Button size="sm" className="min-h-tap" onClick={onFinish}>
          {t('train.finish')}
        </Button>
      ) : (
        <Button size="sm" variant="secondary" className="min-h-tap" onClick={onSummary}>
          {t('train.summary')}
        </Button>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// One exercise
// --------------------------------------------------------------------------- //
function ExerciseCard({
  workout,
  entry,
  position,
  inProgress,
  queued,
  onRest,
  onOpenHistory,
  onHide,
}: {
  workout: Workout
  entry: WorkoutExercise
  position: number
  inProgress: boolean
  queued: Map<string, WorkoutSetIn>
  onRest: (seconds: number) => void
  onOpenHistory: () => void
  onHide: () => void
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const write = useSetWrite(workout.id)
  const mode = (entry.exercise.log_mode ?? 'weight_reps') as LogMode
  const mine = [...queued.entries()].filter(
    ([setId, body]) => body.exercise_id === entry.exercise.id && !entry.sets.some((set) => set.id === setId),
  )
  const [rows, setRows] = useState<Row[]>(() => [
    ...entry.sets.map(rowFromSet),
    ...mine.map(([setId, body]) => rowFromQueued(setId, body)),
    ...plannedRows(
      entry,
      inProgress,
      mine.map(([, body]) => body),
    ),
  ])
  const [menuFor, setMenuFor] = useState<string | null>(null)

  // the server is the truth for saved sets; local planned, queued and edited rows stay
  const serverKey = JSON.stringify(entry.sets)
  const queuedKey = JSON.stringify(mine)
  useEffect(() => {
    setRows((current) => {
      const server = new Map(entry.sets.map((set) => [set.id, set]))
      const next = current
        .filter((row) => server.has(row.id) || row.state !== 'saved')
        .map((row) => {
          const set = server.get(row.id)
          if (!set || row.dirty || row.state === 'saving') return row
          return rowFromSet(set)
        })
      for (const set of entry.sets) {
        if (!next.some((row) => row.id === set.id)) next.push(rowFromSet(set))
      }
      for (const [setId, body] of mine) {
        if (!next.some((row) => row.id === setId)) next.push(rowFromQueued(setId, body))
      }
      return next
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey, queuedKey])

  // records are the server's call (live while running, record events once finished)
  const recordSets = new Set(entry.sets.filter((set) => set.is_pr).map((set) => set.id))
  const target = entry.target
  const restSeconds = target?.rest_seconds ?? 90

  const update = (id: string, patch: Partial<Row>) =>
    setRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)))

  const save = async (row: Row, index: number) => {
    const body = toPayload(row, mode, entry.exercise.id, position, index + 1)
    if (!body) {
      toast.error(t('train.set_needs_value'))
      return
    }
    const firstSave = row.state === 'planned'
    const sent = valuesKey(row)
    update(row.id, { state: 'saving' })
    try {
      const result = await write.mutateAsync({ kind: 'put', setId: row.id, body })
      // an edit typed while the request was in flight stays an unsaved edit
      setRows((current) =>
        current.map((item) =>
          item.id === row.id
            ? { ...item, state: result.queued ? 'queued' : 'saved', dirty: valuesKey(item) !== sent }
            : item,
        ),
      )
      if (result.queued) toast.info(t('train.set_queued'))
      if (firstSave && !row.warmup) onRest(restSeconds)
    } catch (error) {
      update(row.id, { state: firstSave ? 'planned' : row.state === 'queued' ? 'queued' : 'saved' })
      toast.error(errorMessage(error))
    }
  }

  const removeRow = async (row: Row) => {
    setMenuFor(null)
    if (row.state === 'planned') {
      setRows((current) => current.filter((item) => item.id !== row.id))
      return
    }
    try {
      await write.mutateAsync({ kind: 'delete', setId: row.id })
      setRows((current) => current.filter((item) => item.id !== row.id))
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  const addRow = () => {
    const last = rows[rows.length - 1]
    setRows((current) => [
      ...current,
      last
        ? { ...last, id: crypto.randomUUID(), warmup: false, state: 'planned', dirty: false }
        : rowFromValues(crypto.randomUUID(), {}, 'planned'),
    ])
  }

  const previous = (entry.previous ?? []).filter((set) => !set.is_warmup)
  const targetText = target
    ? [
        `${target.target_sets} × ${target.target_reps ?? (target.target_reps_min && target.target_reps_max ? `${target.target_reps_min}–${target.target_reps_max}` : '…')}`,
        target.target_weight_kg ? `${weight(target.target_weight_kg)} ${t('common.kg')}` : null,
      ]
        .filter(Boolean)
        .join(', ')
    : null
  let workingIndex = 0

  return (
    <Card className="space-y-3 p-3.5" data-testid="workout-exercise">
      <div className="flex items-start gap-2">
        <button type="button" onClick={onOpenHistory} className="min-h-tap min-w-0 flex-1 text-left">
          <span className="block truncate text-[15px] font-semibold">{exerciseName(entry.exercise)}</span>
          {targetText && <span className="block text-xs muted">{t('train.target', { value: targetText })}</span>}
          {previous.length > 0 ? (
            <span className="block truncate text-xs muted">
              {t('train.last_time', {
                day: entry.previous_day ? dayLabel(entry.previous_day) : '',
                sets: previous.map((set) => setSummary(set)).join(', '),
              })}
            </span>
          ) : (
            inProgress && <span className="block text-xs muted">{t('train.first_time')}</span>
          )}
        </button>
        {inProgress && entry.sets.length === 0 && mine.length === 0 && (
          <button
            type="button"
            onClick={onHide}
            aria-label={t('train.remove_exercise', { name: exerciseName(entry.exercise) })}
            className="-mr-2 -mt-1.5 flex h-tap w-tap shrink-0 items-center justify-center muted"
          >
            <IconTrash size={18} />
          </button>
        )}
      </div>

      <div className="space-y-2">
        {(inProgress ? rows : rows.filter((row) => row.state !== 'planned')).map((row, index) => {
          const label = row.warmup ? t('train.warmup_short') : String((workingIndex += 1))
          const saved = row.state === 'saved' || row.state === 'queued'
          const isPr = saved && !row.dirty && recordSets.has(row.id)
          return (
            <div key={row.id} className="space-y-1.5">
              <div className={clsx('flex items-center gap-2 rounded-2xl', saved && !row.dirty && 'bg-aqua-400/[.07]')}>
                <button
                  type="button"
                  onClick={() => setMenuFor(menuFor === row.id ? null : row.id)}
                  aria-label={t('train.set_menu', { n: label })}
                  aria-expanded={menuFor === row.id}
                  className={clsx(
                    'flex h-tap w-tap shrink-0 items-center justify-center rounded-xl text-sm font-semibold tnum',
                    row.warmup ? 'text-amber-300' : 'muted',
                  )}
                >
                  {label}
                </button>
                <SetInputs
                  row={row}
                  mode={mode}
                  label={label}
                  onChange={(patch) => update(row.id, { ...patch, dirty: row.state !== 'planned' })}
                />
                {isPr && (
                  <span className="shrink-0 text-violet-300" title={t('train.record')} role="img" aria-label={t('train.record')}>
                    <IconStar size={16} filled />
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => void save(row, index)}
                  disabled={row.state === 'saving'}
                  aria-label={saved && !row.dirty ? t('train.set_saved', { n: label }) : t('train.save_set', { n: label })}
                  className={clsx(
                    'flex h-tap w-tap shrink-0 items-center justify-center rounded-2xl border transition-colors',
                    saved && !row.dirty
                      ? 'border-transparent bg-[rgb(var(--accent))] text-on-accent'
                      : 'border-[rgb(var(--border))] text-[rgb(var(--text-muted))]',
                  )}
                  data-testid="save-set"
                >
                  {row.state === 'queued' && !row.dirty ? <IconClock size={18} /> : <IconCheck size={20} />}
                </button>
              </div>
              {menuFor === row.id && (
                <div className="flex gap-2 pl-14">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="min-h-tap"
                    onClick={() => {
                      const next = { ...row, warmup: !row.warmup, dirty: row.state !== 'planned' }
                      update(row.id, next)
                      setMenuFor(null)
                      if (row.state !== 'planned') void save(next, index)
                    }}
                  >
                    {row.warmup ? t('train.mark_working') : t('train.mark_warmup')}
                  </Button>
                  <Button size="sm" variant="ghost" className="min-h-tap text-rose-400" onClick={() => void removeRow(row)}>
                    {t('train.delete_set')}
                  </Button>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <Button size="sm" variant="ghost" className="min-h-tap" icon={<IconPlus size={16} />} onClick={addRow}>
        {t('train.add_set')}
      </Button>
    </Card>
  )
}

function SetInputs({
  row,
  mode,
  label,
  onChange,
}: {
  row: Row
  mode: LogMode
  label: string
  onChange: (patch: Partial<Row>) => void
}) {
  const { t } = useTranslation()
  const field = 'tnum h-tap min-w-0 flex-1 px-2 text-center text-lg font-semibold'
  if (mode === 'weight_reps') {
    return (
      <>
        <Input
          inputMode="decimal"
          value={row.weight}
          onChange={(event) => onChange({ weight: event.target.value })}
          className={field}
          aria-label={t('train.weight_for', { n: label })}
          placeholder={t('common.kg')}
        />
        <span className="muted" aria-hidden="true">
          ×
        </span>
        <Input
          inputMode="numeric"
          value={row.reps}
          onChange={(event) => onChange({ reps: event.target.value })}
          className={field}
          aria-label={t('train.reps_for', { n: label })}
          placeholder={t('train.unit.reps')}
        />
      </>
    )
  }
  if (mode === 'reps') {
    return (
      <Input
        inputMode="numeric"
        value={row.reps}
        onChange={(event) => onChange({ reps: event.target.value })}
        className={field}
        aria-label={t('train.reps_for', { n: label })}
        placeholder={t('train.unit.reps')}
      />
    )
  }
  if (mode === 'duration') {
    return (
      <Input
        inputMode="numeric"
        value={row.seconds}
        onChange={(event) => onChange({ seconds: event.target.value })}
        className={field}
        aria-label={t('train.seconds_for', { n: label })}
        placeholder={t('train.unit.sec')}
      />
    )
  }
  return (
    <>
      <Input
        inputMode="decimal"
        value={row.minutes}
        onChange={(event) => onChange({ minutes: event.target.value })}
        className={field}
        aria-label={t('train.minutes_for', { n: label })}
        placeholder={t('common.min')}
      />
      <Input
        inputMode="decimal"
        value={row.km}
        onChange={(event) => onChange({ km: event.target.value })}
        className={field}
        aria-label={t('train.km_for', { n: label })}
        placeholder={t('train.unit.km')}
      />
    </>
  )
}

// --------------------------------------------------------------------------- //
// Rest timer (pinned above the tab bar; survives a reload through the draft)
// --------------------------------------------------------------------------- //
function RestBar({
  rest,
  onChange,
}: {
  rest: { endsAt: number; total: number }
  onChange: (rest: { endsAt: number; total: number } | null) => void
}) {
  const { t } = useTranslation()
  const [now, setNow] = useState(() => Date.now())
  const buzzed = useRef(rest.endsAt <= Date.now()) // an old timer after a reload: clear it quietly
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(handle)
  }, [])
  const left = Math.ceil((rest.endsAt - now) / 1000)
  useEffect(() => {
    if (left > 0) return
    if (!buzzed.current) {
      buzzed.current = true
      try {
        navigator.vibrate?.([180, 80, 180])
      } catch {
        /* not supported (iOS) */
      }
    }
    if (left < -4) onChange(null)
  }, [left, onChange])

  const shift = (seconds: number) =>
    onChange({ endsAt: Math.max(Date.now(), rest.endsAt + seconds * 1000), total: Math.max(15, rest.total + seconds) })

  return (
    <div className="fixed inset-x-0 z-30 px-4 above-tabbar" data-testid="rest-timer">
      {/* announced when the rest starts, changes length and ends — never every second */}
      <p className="sr-only" role="status" aria-live="polite">
        {left > 0 ? t('a11y.rest_started', { time: clock(rest.total) }) : t('train.rest_over')}
      </p>
      <div className="mx-auto flex max-w-lg items-center gap-2 rounded-2xl border border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))]/95 p-2 pl-4 shadow-card backdrop-blur-xl">
        <div className="min-w-0 flex-1" role="timer" aria-live="off">
          <p className="text-xs muted">{left > 0 ? t('train.rest') : t('train.rest_over')}</p>
          <p className="tnum text-xl font-semibold">{clock(Math.max(0, left))}</p>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-[rgb(var(--border))]">
            <div
              className="h-full rounded-full bg-[rgb(var(--accent))] transition-[width] duration-300"
              style={{ width: `${Math.max(0, Math.min(100, (left / rest.total) * 100))}%` }}
            />
          </div>
        </div>
        <Button size="sm" variant="ghost" className="min-h-tap px-2" onClick={() => shift(-15)} aria-label={t('train.rest_minus')}>
          −15
        </Button>
        <Button size="sm" variant="ghost" className="min-h-tap px-2" onClick={() => shift(15)} aria-label={t('train.rest_plus')}>
          +15
        </Button>
        <Button size="sm" variant="secondary" className="min-h-tap" onClick={() => onChange(null)}>
          {t('train.rest_skip')}
        </Button>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Finish → summary
// --------------------------------------------------------------------------- //
function FinishSheet({
  open,
  workout,
  pendingSets,
  onClose,
  onFinished,
}: {
  open: boolean
  workout: Workout
  pendingSets: number
  onClose: () => void
  onFinished: () => void
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const finish = useFinishWorkout()
  const [minutes, setMinutes] = useState('')
  const [rating, setRating] = useState<number | null>(null)

  useEffect(() => {
    if (!open) return
    const elapsed = Math.round((Date.now() - new Date(workout.started_at).getTime()) / 60_000)
    setMinutes(String(Math.max(1, Math.min(elapsed, 600))))
    setRating(null)
  }, [open, workout.started_at])

  const elapsedMinutes = Math.round((Date.now() - new Date(workout.started_at).getTime()) / 60_000)
  const submit = () => {
    const value = parse(minutes)
    finish.mutate(
      {
        id: workout.id,
        payload: {
          duration_s: value && value >= 1 ? Math.round(value * 60) : undefined,
          rating: rating ?? undefined,
        },
      },
      {
        onSuccess: (done) => {
          toast.success(t('train.finished_toast', { sets: done.total_sets }))
          onFinished()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('train.finish_title')}>
      <div className="space-y-4 pb-2">
        <p className="tnum text-sm muted">
          {t('train.finish_stats', { sets: workout.total_sets, volume: number(workout.total_volume_kg) })}
        </p>
        {pendingSets > 0 ? (
          <Callout tone="warn">{t('train.finish_pending', { n: pendingSets })}</Callout>
        ) : (
          workout.total_sets === 0 && <Callout tone="warn">{t('train.finish_empty')}</Callout>
        )}
        <Field
          label={t('train.duration_minutes')}
          htmlFor="finish-minutes"
          hint={elapsedMinutes > 240 ? t('train.duration_long_hint') : undefined}
        >
          <Input
            id="finish-minutes"
            inputMode="numeric"
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
            className="tnum"
          />
        </Field>
        <div className="space-y-2">
          <p className="label">{t('train.rating')}</p>
          <div className="grid grid-cols-5 gap-2" role="radiogroup" aria-label={t('train.rating')}>
            {[1, 2, 3, 4, 5].map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={rating === value}
                onClick={() => setRating(rating === value ? null : value)}
                className={clsx(
                  'min-h-tap rounded-2xl border text-base font-semibold tnum',
                  rating === value
                    ? 'border-aqua-400/50 bg-aqua-400/10 text-aqua-200'
                    : 'border-[rgb(var(--border))] text-[rgb(var(--text-muted))]',
                )}
              >
                {value}
              </button>
            ))}
          </div>
          <p className="text-xs muted">{t('train.rating_hint')}</p>
        </div>
        <Button block loading={finish.isPending} disabled={workout.total_sets === 0 || pendingSets > 0} onClick={submit}>
          {t('train.finish_save')}
        </Button>
      </div>
    </Sheet>
  )
}

function SummarySheet({
  open,
  workout,
  onClose,
  onDone,
}: {
  open: boolean
  workout: Workout
  onClose: () => void
  onDone: () => void
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const saveAs = useSaveAsTemplate()
  const [name, setName] = useState('')
  useEffect(() => {
    if (open) setName(workout.title ?? '')
  }, [open, workout.title])

  return (
    <Sheet open={open} onClose={onClose} title={t('train.summary_title')}>
      <div className="space-y-4 pb-2" data-testid="workout-summary">
        <div className="grid grid-cols-3 gap-2">
          <Stat label={t('train.stat_time')} value={minutesLabel(workout.duration_s)} />
          <Stat label={t('train.stat_sets')} value={number(workout.total_sets)} />
          <Stat label={t('train.stat_volume')} value={`${number(workout.total_volume_kg)} ${t('common.kg')}`} />
        </div>
        <div className="surface-sunken flex items-center gap-3 rounded-2xl p-3">
          <IconFlame size={20} className="shrink-0 text-amber-400" />
          <div className="min-w-0 flex-1">
            <p className="tnum text-base font-semibold">
              {workout.kcal_burned != null ? `${number(workout.kcal_burned)} ${t('common.kcal')}` : '—'}
            </p>
            <p className="text-xs muted">
              {workout.kcal_burned != null ? t('train.burned_not_budget') : t('train.burned_needs_weight')}
            </p>
          </div>
        </div>
        {(workout.records ?? []).length > 0 && (
          <div className="space-y-2" data-testid="workout-records">
            <p className="flex items-center gap-1.5 text-sm font-semibold">
              <IconStar size={16} filled className="text-violet-300" />
              {t('train.records_title', { n: workout.records?.length ?? 0 })}
            </p>
            {(workout.records ?? []).map((record) => (
              <div key={record.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 truncate">
                  {recordExerciseName(record)}
                  <span className="muted">, {t(`train.record_kind.${record.kind}`)}</span>
                </span>
                <span className="tnum shrink-0 font-semibold">
                  {recordValue(record)}
                  {record.previous_value != null && (
                    <span className="ml-1 text-xs font-normal muted">
                      {t('train.was', { value: recordValue({ ...record, value: record.previous_value, reps: null }) })}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
        {!workout.template_id && workout.status === 'completed' && (
          <div className="flex items-end gap-2">
            <Field label={t('train.save_as_template')} htmlFor="template-name">
              <Input id="template-name" value={name} onChange={(event) => setName(event.target.value)} />
            </Field>
            <Button
              variant="secondary"
              loading={saveAs.isPending}
              disabled={!name.trim()}
              onClick={() =>
                saveAs.mutate(
                  { workoutId: workout.id, templateId: crypto.randomUUID(), name: name.trim() },
                  {
                    onSuccess: (template) => toast.success(t('train.template_saved', { name: template.name })),
                    onError: (error) => toast.error(errorMessage(error)),
                  },
                )
              }
            >
              {t('app.save')}
            </Button>
          </div>
        )}
        <Button block onClick={onDone}>
          {t('train.done')}
        </Button>
      </div>
    </Sheet>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="surface-sunken rounded-2xl p-3">
      <p className="text-xs muted">{label}</p>
      <p className="tnum mt-0.5 text-base font-semibold">{value}</p>
    </div>
  )
}

