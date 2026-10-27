/**
 * Активность: steps against the step target, workouts per week against the
 * plan, calories burned (workouts and Health's active energy) as their **own**
 * card — never subtracted from food, never "you can eat more" — the working
 * volume per muscle group and the period's personal records.
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { IconTrophy } from '@/features/game/icons'
import { dayLabel, number } from '@/lib/format'
import { recordExerciseName, recordValue } from '@/features/train/format'
import { BarList } from '../charts/BarList'
import { DailyChart, type TooltipLine } from '../charts/DailyChart'
import { useChartTheme } from '../charts/theme'
import { WeeksChart } from '../charts/WeeksChart'
import { activityTotals, elapsedDays, hasBurned, hasSteps, hasWorkouts } from '../derive'
import { periodLabel, shortDay, compact } from '../format'
import { CardEmpty, DataTable, Legend, StatCard, Tiles } from '../parts'
import type { ActivityDay, ActivityStats } from '../types'

const RECORDS_SHOWN = 5

export function ActivitySection({ stats, dimmed }: { stats: ActivityStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const any = hasSteps(stats) || hasWorkouts(stats) || hasBurned(stats)
  if (!any) {
    return (
      <StatCard title={t('stats.activity.steps_title')} testId="stats-steps-card" dimmed={dimmed}>
        <CardEmpty
          title={t('stats.activity.empty')}
          description={t('stats.activity.empty_hint')}
          action={{ label: t('stats.activity.empty_action'), onClick: () => navigate('/train') }}
        />
      </StatCard>
    )
  }
  return (
    <>
      <StepsCard stats={stats} dimmed={dimmed} />
      <WorkoutsCard stats={stats} dimmed={dimmed} />
      <BurnedCard stats={stats} dimmed={dimmed} />
      {stats.muscle_volume.length > 0 && <MusclesCard stats={stats} dimmed={dimmed} />}
      {stats.records_total > 0 && <RecordsCard stats={stats} dimmed={dimmed} />}
    </>
  )
}

function StepsCard({ stats, dimmed }: { stats: ActivityStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = activityTotals(stats)
  const target = stats.target_steps
  const steps = t('stats.activity.steps_unit')

  const colour = (day: ActivityDay) => {
    if (day.date === stats.today && !day.on_target) return theme.alpha(theme.move, 0.45)
    return day.on_target ? theme.move : theme.rest
  }
  const tooltip = (day: ActivityDay): TooltipLine[] =>
    day.steps == null
      ? [{ label: '', value: t('stats.no_data'), mark: 'none' }]
      : [
          {
            label: day.on_target ? t('stats.activity.on_target') : day.date === stats.today ? t('stats.day_running') : t('stats.activity.below_target'),
            value: `${number(day.steps)} ${steps}`,
            color: colour(day),
          },
        ]

  return (
    <StatCard
      title={t('stats.activity.steps_title')}
      hint={target ? t('stats.activity.steps_hint', { steps: number(target) }) : t('stats.activity.no_steps_target')}
      testId="stats-steps-card"
      dimmed={dimmed}
    >
      <Tiles
        items={[
          {
            label: t('stats.activity.steps_avg'),
            value: totals.stepsMean != null ? number(totals.stepsMean) : '—',
            sub: t('stats.activity.steps_per_day'),
            testId: 'stat-steps-avg',
          },
          {
            label: t('stats.activity.on_target_days'),
            value: t('stats.n_of_m', { n: totals.onTargetDays, m: totals.stepsDays }),
            sub: t('stats.activity.days_with_steps'),
            testId: 'stat-steps-target',
          },
          {
            label: t('stats.activity.workouts'),
            value: number(totals.workouts),
            sub: t('stats.activity.in_period'),
            testId: 'stat-workouts',
          },
        ]}
      />
      {hasSteps(stats) ? (
        <>
          <DailyChart
            rows={stats.days}
            range={stats}
            testId="chart-steps"
            summary={t('stats.activity.steps_summary', {
              period: periodLabel(stats),
              avg: totals.stepsMean != null ? number(totals.stepsMean) : '—',
              n: totals.onTargetDays,
              m: totals.stepsDays,
              target: target ? number(target) : '—',
            })}
            formatY={compact}
            series={[{ key: 'steps', value: (day) => day.steps, color: theme.move, colorOf: colour, ghost: true }]}
            target={target ? { value: target, label: t('stats.activity.target_label', { steps: number(target) }) } : undefined}
            tooltip={tooltip}
          />
          <Legend
            items={[
              { mark: 'bar', color: theme.move, label: t('stats.activity.legend_on_target') },
              { mark: 'bar', color: theme.rest, label: t('stats.activity.legend_below') },
              { mark: 'ghost', color: theme.muted, label: t('stats.legend_no_data') },
              ...(target ? [{ mark: 'dash' as const, color: theme.ink, label: t('stats.legend_target') }] : []),
            ]}
          />
          <DataTable
            caption={t('stats.activity.steps_title')}
            testId="table-steps"
            columns={[{ label: t('stats.col.day') }, { label: t('stats.col.steps'), align: 'right' }, { label: t('stats.col.workouts'), align: 'right' }]}
            rows={() =>
              stats.days
                .filter((day) => day.date <= stats.today)
                .map((day) => ({
                  key: day.date,
                  muted: day.steps == null,
                  cells: [shortDay(day.date), day.steps != null ? number(day.steps) : '—', number(day.workouts)],
                }))
            }
          />
        </>
      ) : (
        <p className="text-sm muted">{t('stats.activity.no_steps')}</p>
      )}
    </StatCard>
  )
}

function WorkoutsCard({ stats, dimmed }: { stats: ActivityStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = activityTotals(stats)
  const weeksMet = stats.weeks.filter((week) => week.workouts >= week.required).length
  const volume = stats.muscle_volume.reduce((total, item) => total + item.volume_kg, 0)
  const single = stats.weeks.length === 1 ? stats.weeks[0] : null

  return (
    <StatCard title={t('stats.activity.workouts_title')} hint={t('stats.activity.workouts_hint')} testId="stats-workouts-card" dimmed={dimmed}>
      <Tiles
        items={[
          { label: t('stats.activity.workouts'), value: number(totals.workouts), sub: t('stats.activity.in_period'), testId: 'stat-workouts-total' },
          {
            label: t('stats.activity.weeks_met'),
            value: t('stats.n_of_m', { n: weeksMet, m: stats.weeks.length }),
            sub: t('stats.activity.weeks_short'),
            testId: 'stat-weeks-met',
          },
          {
            label: t('stats.activity.volume'),
            value: volume > 0 ? `${compact(Math.round(volume))} ${t('common.kg')}` : '—',
            sub: t('stats.activity.volume_hint'),
            testId: 'stat-volume',
          },
        ]}
      />
      {single ? (
        <div className="surface-sunken flex items-center justify-between gap-3 px-3 py-2.5 text-sm" data-testid="week-progress">
          <span className="muted">{single.planned > 0 ? t('stats.activity.week_planned') : t('stats.activity.week_default')}</span>
          <span className="tnum font-semibold">{t('stats.n_of_m', { n: single.workouts, m: single.required })}</span>
        </div>
      ) : (
        <>
          <WeeksChart
            weeks={stats.weeks}
            summary={t('stats.activity.weeks_summary', { period: periodLabel(stats), n: weeksMet, m: stats.weeks.length, total: totals.workouts })}
          />
          <Legend
            items={[
              { mark: 'bar', color: theme.move, label: t('stats.activity.legend_week_met') },
              { mark: 'bar', color: theme.rest, label: t('stats.activity.legend_week_short') },
              { mark: 'line', color: theme.ink, label: t('stats.activity.legend_required') },
            ]}
          />
        </>
      )}
    </StatCard>
  )
}

function BurnedCard({ stats, dimmed }: { stats: ActivityStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = activityTotals(stats)
  const kcal = t('common.kcal')
  const elapsed = elapsedDays(stats.days, stats.today)
  const tooltip = (day: ActivityDay): TooltipLine[] => [
    { label: t('stats.activity.burn_workouts'), value: `${number(day.workout_kcal)} ${kcal}`, color: theme.burn },
    {
      label: t('stats.activity.burn_active'),
      value: day.active_kcal != null ? `${number(day.active_kcal)} ${kcal}` : t('stats.no_data'),
      color: theme.move,
    },
  ]
  return (
    <StatCard
      title={t('stats.activity.burned_title')}
      hint={t('stats.activity.burned_hint')}
      testId="stats-burned"
      dimmed={dimmed}
    >
      <Tiles
        items={[
          { label: t('stats.activity.burn_workouts'), value: `${number(totals.workoutKcal)} ${kcal}`, sub: t('stats.activity.in_period'), testId: 'stat-burn-workouts' },
          {
            label: t('stats.activity.burn_active'),
            value: totals.activeDays ? `${number(totals.activeKcal)} ${kcal}` : '—',
            sub: t('stats.n_of_m_days', { n: totals.activeDays, m: elapsed }),
            testId: 'stat-burn-active',
          },
        ]}
      />
      {hasBurned(stats) ? (
        <>
          <DailyChart
            rows={stats.days}
            range={stats}
            testId="chart-burned"
            height={150}
            summary={t('stats.activity.burned_summary', {
              period: periodLabel(stats),
              workouts: number(totals.workoutKcal),
              active: number(totals.activeKcal),
            })}
            formatY={compact}
            series={[
              { key: 'workout_kcal', value: (day) => (day.workout_kcal > 0 ? day.workout_kcal : day.date <= stats.today ? 0 : null), color: theme.burn },
              { key: 'active_kcal', value: (day) => day.active_kcal, color: theme.move },
            ]}
            tooltip={tooltip}
          />
          <Legend
            items={[
              { mark: 'bar', color: theme.burn, label: t('stats.activity.burn_workouts') },
              { mark: 'bar', color: theme.move, label: t('stats.activity.burn_active') },
            ]}
          />
        </>
      ) : (
        <p className="text-sm muted">{t('stats.activity.no_burned')}</p>
      )}
      <p className="text-xs muted" data-testid="burned-note">{t('stats.activity.burned_note')}</p>
    </StatCard>
  )
}

function MusclesCard({ stats, dimmed }: { stats: ActivityStats; dimmed: boolean }) {
  const { t } = useTranslation()
  return (
    <StatCard title={t('stats.activity.muscles_title')} hint={t('stats.activity.muscles_hint')} testId="stats-muscles" dimmed={dimmed}>
      <BarList
        testId="muscle-list"
        colorClass="bg-[rgb(var(--accent-2))]"
        items={stats.muscle_volume.map((item) => ({
          key: item.muscle,
          label: t(`train.muscle.${item.muscle}`),
          value: item.volume_kg,
          text: t('stats.activity.muscle_value', { kg: number(Math.round(item.volume_kg)), sets: item.sets }),
        }))}
      />
    </StatCard>
  )
}

function RecordsCard({ stats, dimmed }: { stats: ActivityStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const shown = stats.records.slice(0, RECORDS_SHOWN)
  return (
    <StatCard
      title={t('stats.activity.records_title')}
      aside={<span className="tnum text-sm font-semibold" data-testid="records-total">{number(stats.records_total)}</span>}
      testId="stats-records"
      dimmed={dimmed}
    >
      <ul className="divide-y divide-[rgb(var(--border))]">
        {shown.map((record) => (
          <li key={record.id} className="flex items-center gap-3 py-2.5" data-testid="record-row">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[rgb(var(--accent-2))]/15 text-[rgb(var(--accent-2))]">
              <IconTrophy size={18} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{recordExerciseName(record)}</span>
              <span className="block truncate text-xs muted">
                {t(`train.record_kind.${record.kind}`)} · {dayLabel(record.local_day)}
              </span>
            </span>
            <span className="tnum shrink-0 text-sm font-semibold">{recordValue(record)}</span>
          </li>
        ))}
      </ul>
      {stats.records_total > shown.length && (
        <p className="text-xs muted">{t('stats.activity.records_more', { n: stats.records_total - shown.length })}</p>
      )}
    </StatCard>
  )
}
