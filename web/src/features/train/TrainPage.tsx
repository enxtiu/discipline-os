/**
 * Train tab: the workout of the day first (start in one tap), then the week,
 * burned calories on their own card, templates, records and history.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import clsx from 'clsx'

import { Button, Card, EmptyState, IconButton, Section, Skeleton } from '@/components/ui'
import {
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconFlame,
  IconPlus,
  IconSearch,
  IconStar,
  IconTrain,
} from '@/components/ui/icons'
import type { PlanDay, Template, WorkoutBrief } from '@/api/types'
import { addDays, dayLabel, number, today as isoToday } from '@/lib/format'
import { BurnedChart } from './BurnedChart'
import { ExerciseHistorySheet } from './ExerciseHistorySheet'
import { ExercisePicker } from './ExercisePicker'
import { clock, exerciseName, minutesLabel, recordExerciseName, recordValue, weekdayShort } from './format'
import { PlanDaySheet, QuickCardioSheet, StartWorkoutSheet } from './sheets'
import { useStart } from './useStart'
import { TemplateSheet } from './TemplateSheet'
import {
  useBurned,
  useRecords,
  useTemplates,
  useTrainingToday,
  useWeekPlan,
  useWorkoutHistory,
} from './useTraining'

export function TrainPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const today = useTrainingToday()
  const [weekShift, setWeekShift] = useState(0)
  const week = useWeekPlan(weekShift ? addDays(isoToday(), weekShift * 7) : undefined)
  const templates = useTemplates()
  const burned = useBurned(7)
  const records = useRecords()
  const history = useWorkoutHistory()
  const start = useStart()

  const [starting, setStarting] = useState(false)
  const [cardio, setCardio] = useState(false)
  const [planDay, setPlanDay] = useState<PlanDay | null>(null)
  const [editing, setEditing] = useState<Template | null | undefined>(undefined)
  const [library, setLibrary] = useState(false)
  const [historyOf, setHistoryOf] = useState<string | null>(null)

  // deep link from quick add: /train?action=start — continue the running workout,
  // start today's planned one, or offer the choice (two taps from anywhere)
  useEffect(() => {
    if (params.get('action') !== 'start' || !today.data) return
    setParams({}, { replace: true })
    const data = today.data
    const template = data.plan.template
    const doneAlready = !!template && (data.done ?? []).some((workout) => workout.template_id === template.id)
    if (data.active) navigate(`/train/workout/${data.active.id}`)
    else if (template && !doneAlready) start.run(template.id)
    else setStarting(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, today.data])

  const templateList = templates.data ?? []
  const plan = today.data?.plan
  const planned = plan?.template ? templateList.find((item) => item.id === plan.template?.id) : undefined
  const planDone = !!plan?.template && (today.data?.done ?? []).some((workout) => workout.template_id === plan.template?.id)
  const workouts = useMemo(() => history.data?.pages.flatMap((page) => page.items) ?? [], [history.data])

  return (
    <div className="space-y-6">
      {/* the one thing to do now */}
      {today.isLoading ? (
        <Skeleton className="h-36 rounded-card" />
      ) : today.data?.active ? (
        <ActiveCard workout={today.data.active} onOpen={() => navigate(`/train/workout/${today.data?.active?.id}`)} />
      ) : (
        <Card className="space-y-3" data-testid="today-plan">
          <p className="text-sm muted">{t('train.today_label')}</p>
          {plan?.template && !planDone ? (
            <>
              <div>
                <h2 className="text-xl font-semibold">{plan.template.name}</h2>
                <p className="text-sm muted">
                  {t('train.exercise_count', { n: plan.template.exercise_count })}
                  {plan.template.estimated_minutes ? `, ~${plan.template.estimated_minutes} ${t('common.min')}` : ''}
                </p>
              </div>
              <Button block size="md" loading={start.pending} onClick={() => start.run(plan.template?.id)}>
                {t('train.start_planned')}
              </Button>
              {planned && planned.items.length > 0 && (
                <p className="truncate text-xs muted">
                  {planned.items.map((item) => exerciseName(item.exercise)).join(', ')}
                </p>
              )}
            </>
          ) : (
            <>
              <h2 className="text-xl font-semibold">
                {planDone
                  ? t('train.plan_done', { name: plan?.template?.name })
                  : plan?.is_rest_day
                    ? t('train.rest_day')
                    : t('train.nothing_planned')}
              </h2>
              <p className="text-sm muted">
                {planDone
                  ? t('train.plan_done_hint')
                  : plan?.is_rest_day
                    ? t('train.rest_day_hint')
                    : t('train.nothing_planned_hint')}
              </p>
              <div className="grid grid-cols-2 gap-2">
                <Button icon={<IconTrain size={18} />} onClick={() => setStarting(true)}>
                  {t('train.strength_short')}
                </Button>
                <Button variant="secondary" icon={<IconFlame size={18} />} onClick={() => setCardio(true)}>
                  {t('train.cardio')}
                </Button>
              </div>
            </>
          )}
          {(today.data?.done?.length ?? 0) > 0 && (
            <p className="text-sm text-[rgb(var(--accent))]">
              {t('train.done_today', { n: today.data?.done?.length, kcal: number(today.data?.burned_kcal ?? 0) })}
            </p>
          )}
        </Card>
      )}

      {/* the week */}
      <Section
        title={t('train.week')}
        action={
          <span className="flex items-center">
            <IconButton
              label={t('train.week_prev')}
              icon={<IconChevronLeft size={18} />}
              onClick={() => setWeekShift((value) => value - 1)}
            />
            {weekShift !== 0 && (
              <Button size="sm" variant="ghost" className="min-h-tap" onClick={() => setWeekShift(0)}>
                {t('train.week_current')}
              </Button>
            )}
            <IconButton
              label={t('train.week_next')}
              icon={<IconChevronRight size={18} />}
              onClick={() => setWeekShift((value) => value + 1)}
            />
          </span>
        }
      >
        <div className="grid grid-cols-7 gap-1" data-testid="week-strip">
          {(week.data?.days ?? []).map((day) => (
            <WeekDay key={day.date} day={day} onClick={() => setPlanDay(day)} />
          ))}
          {week.isLoading && Array.from({ length: 7 }).map((_, index) => <Skeleton key={index} className="h-20" />)}
        </div>
        <p className="text-xs muted">{t('train.week_hint')}</p>
      </Section>

      <BurnedChart data={burned.data} loading={burned.isLoading} />

      <Section
        title={t('train.templates')}
        action={
          <Button size="sm" variant="ghost" icon={<IconPlus size={16} />} onClick={() => setEditing(null)}>
            {t('train.new_template')}
          </Button>
        }
      >
        {templateList.length === 0 ? (
          <EmptyState
            title={t('train.no_templates')}
            description={t('train.no_templates_hint')}
            action={
              <Button size="sm" onClick={() => setEditing(null)}>
                {t('train.new_template')}
              </Button>
            }
          />
        ) : (
          <div className="space-y-2">
            {templateList.map((template) => (
              <div key={template.id} className="card flex items-center gap-2 p-2 pl-4" data-testid="template-row">
                <button type="button" onClick={() => setEditing(template)} className="min-w-0 flex-1 py-1.5 text-left">
                  <span className="block truncate font-medium">{template.name}</span>
                  <span className="block truncate text-xs muted">
                    {t('train.exercise_count', { n: template.items.length })}
                    {template.use_count ? `, ${t('train.used_times', { n: template.use_count })}` : ''}
                  </span>
                </button>
                <Button
                  size="sm"
                  variant="secondary"
                  className="min-h-tap"
                  loading={start.pending}
                  onClick={() => start.run(template.id)}
                >
                  {t('train.start_short')}
                </Button>
              </div>
            ))}
          </div>
        )}
      </Section>

      {(records.data?.length ?? 0) > 0 && (
        <Section title={t('train.records')}>
          <div className="card divide-y divide-[rgb(var(--border))]" data-testid="records-list">
            {records.data?.map((record) => (
              <button
                key={record.id}
                type="button"
                onClick={() => setHistoryOf(record.exercise_id)}
                className="flex min-h-tap w-full items-center gap-3 px-4 py-2.5 text-left"
              >
                <IconStar size={16} filled className="shrink-0 text-violet-300" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{recordExerciseName(record)}</span>
                  <span className="block text-xs muted">
                    {t(`train.record_kind.${record.kind}`)}, {dayLabel(record.local_day)}
                  </span>
                </span>
                <span className="tnum shrink-0 text-sm font-semibold">{recordValue(record)}</span>
              </button>
            ))}
          </div>
        </Section>
      )}

      <Section title={t('train.history')}>
        {workouts.length === 0 && !history.isLoading ? (
          <p className="text-sm muted">{t('train.no_history')}</p>
        ) : (
          <div className="card divide-y divide-[rgb(var(--border))]" data-testid="history-list">
            {workouts.map((workout) => (
              <HistoryRow key={workout.id} workout={workout} onOpen={() => navigate(`/train/workout/${workout.id}`)} />
            ))}
          </div>
        )}
        {history.hasNextPage && (
          <Button variant="ghost" size="sm" loading={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>
            {t('train.show_more')}
          </Button>
        )}
      </Section>

      <Button variant="secondary" block icon={<IconSearch size={18} />} onClick={() => setLibrary(true)}>
        {t('train.library')}
      </Button>

      <StartWorkoutSheet
        open={starting}
        templates={templateList}
        onClose={() => setStarting(false)}
        onCardio={() => {
          setStarting(false)
          setCardio(true)
        }}
      />
      <QuickCardioSheet open={cardio} onClose={() => setCardio(false)} />
      <PlanDaySheet day={planDay} templates={templateList} onClose={() => setPlanDay(null)} />
      <TemplateSheet open={editing !== undefined} template={editing ?? null} onClose={() => setEditing(undefined)} />
      <ExercisePicker
        open={library}
        title={t('train.library')}
        onClose={() => setLibrary(false)}
        onPick={(exercise) => {
          setLibrary(false)
          setHistoryOf(exercise.id)
        }}
      />
      <ExerciseHistorySheet exerciseId={historyOf} onClose={() => setHistoryOf(null)} />
    </div>
  )
}

function ActiveCard({ workout, onOpen }: { workout: WorkoutBrief; onOpen: () => void }) {
  const { t } = useTranslation()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(handle)
  }, [])
  return (
    <Card className="glow-accent space-y-3" data-testid="active-workout">
      <p className="text-sm text-[rgb(var(--accent))]">{t('train.in_progress')}</p>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-xl font-semibold">{workout.title ?? t('train.workout_default_title')}</h2>
          <p className="text-sm muted">{t('train.sets_logged', { n: workout.total_sets })}</p>
        </div>
        <p className="tnum text-2xl font-semibold">{clock((now - new Date(workout.started_at).getTime()) / 1000)}</p>
      </div>
      <Button block onClick={onOpen}>
        {t('train.continue')}
      </Button>
    </Card>
  )
}

function WeekDay({ day, onClick }: { day: PlanDay; onClick: () => void }) {
  const { t } = useTranslation()
  const done = (day.workouts ?? []).some((workout) => workout.status === 'completed')
  const label = day.is_rest_day ? t('train.rest_short') : (day.template?.name ?? day.title ?? '')
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'flex min-h-[5rem] min-w-0 flex-col items-center gap-1 rounded-2xl border px-0.5 py-2 text-center',
        day.is_today ? 'border-aqua-400/50 bg-aqua-400/[.08]' : 'border-[rgb(var(--border))]',
      )}
    >
      <span className="sr-only">
        {t('train.plan_day_label', { day: dayLabel(day.date, 'long'), plan: label || t('a11y.nothing_planned') })}
        {done && `, ${t('a11y.done')}`}
      </span>
      <span
        className={clsx('text-[11px]', day.is_today ? 'font-semibold text-[rgb(var(--accent))]' : 'muted')}
        aria-hidden="true"
      >
        {weekdayShort(day.weekday)}
      </span>
      <span className="tnum text-sm font-semibold" aria-hidden="true">
        {Number(day.date.slice(8, 10))}
      </span>
      <span
        className={clsx(
          'mt-auto flex h-5 w-5 items-center justify-center rounded-full text-[10px]',
          done
            ? 'bg-[rgb(var(--accent))] text-ink-950'
            : day.template
              ? 'border border-aqua-400/60'
              : day.is_rest_day
                ? 'text-[rgb(var(--text-muted))]'
                : '',
        )}
        aria-hidden="true"
      >
        {done && <IconCheck size={12} />}
      </span>
      <span className="w-full truncate px-0.5 text-[10px] leading-tight muted" aria-hidden="true">
        {label}
      </span>
    </button>
  )
}

function HistoryRow({ workout, onOpen }: { workout: WorkoutBrief; onOpen: () => void }) {
  const { t } = useTranslation()
  return (
    <button type="button" onClick={onOpen} className="flex min-h-tap w-full items-center gap-3 px-4 py-3 text-left">
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate font-medium">{workout.title ?? t('train.workout_default_title')}</span>
          {workout.pr_count > 0 && (
            <span className="flex shrink-0 items-center gap-0.5 text-xs text-violet-300">
              <IconStar size={12} filled />
              {workout.pr_count}
            </span>
          )}
          {workout.status === 'in_progress' && <span className="text-xs text-[rgb(var(--accent))]">{t('train.in_progress')}</span>}
        </span>
        <span className="block truncate text-xs muted">
          {dayLabel(workout.local_day)}, {minutesLabel(workout.duration_s)}
          {workout.exercise_names?.length ? `, ${workout.exercise_names.join(', ')}` : ''}
        </span>
      </span>
      <span className="shrink-0 text-right">
        <span className="tnum block text-sm">
          {workout.total_volume_kg ? `${number(workout.total_volume_kg)} ${t('common.kg')}` : t('train.sets_logged', { n: workout.total_sets })}
        </span>
        {workout.kcal_burned != null && (
          <span className="tnum block text-xs text-amber-300">
            {number(workout.kcal_burned)} {t('common.kcal')}
          </span>
        )}
      </span>
      <IconChevronRight size={16} className="shrink-0 muted" />
    </button>
  )
}
