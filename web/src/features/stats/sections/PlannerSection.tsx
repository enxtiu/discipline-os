/**
 * План: tasks done and created per day (with the tracked time in the tooltip),
 * tracked time per area against the area's target scaled to the period, and
 * project progress.
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { ProgressBar } from '@/components/ui'
import { number } from '@/lib/format'
import { ColorDot } from '@/features/plan/tasks/ColorDot'
import { BarList } from '../charts/BarList'
import { DailyChart, isDense, type TooltipLine } from '../charts/DailyChart'
import { useChartTheme } from '../charts/theme'
import { hasPlanner, plannerTotals, showAreas } from '../derive'
import { hoursMinutes, periodLabel, shortDay } from '../format'
import { CardEmpty, DataTable, Legend, StatCard, Tiles } from '../parts'
import type { PlannerDay, PlannerStats } from '../types'

export function PlannerSection({ stats, dimmed }: { stats: PlannerStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <>
      {hasPlanner(stats) ? (
        <TasksCard stats={stats} dimmed={dimmed} />
      ) : (
        <StatCard title={t('stats.planner.tasks_title')} testId="stats-tasks-card" dimmed={dimmed}>
          <CardEmpty
            title={t('stats.planner.empty')}
            description={t('stats.planner.empty_hint')}
            action={{ label: t('stats.planner.empty_action'), onClick: () => navigate('/plan') }}
          />
        </StatCard>
      )}
      {showAreas(stats) && <AreasCard stats={stats} dimmed={dimmed} />}
      {/* project progress is all-time: shown even when the period itself is empty */}
      {stats.projects.length > 0 && <ProjectsCard stats={stats} dimmed={dimmed} />}
    </>
  )
}

function TasksCard({ stats, dimmed }: { stats: PlannerStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = plannerTotals(stats)
  const tooltip = (day: PlannerDay): TooltipLine[] => [
    { label: t('stats.planner.done'), value: number(day.done), color: theme.food },
    { label: t('stats.planner.created'), value: number(day.created), color: theme.rest },
    { label: t('stats.planner.tracked'), value: hoursMinutes(day.tracked_minutes), mark: 'none' },
  ]
  return (
    <StatCard title={t('stats.planner.tasks_title')} hint={t('stats.planner.tasks_hint')} testId="stats-tasks-card" dimmed={dimmed}>
      <Tiles
        items={[
          { label: t('stats.planner.done'), value: number(totals.done), sub: t('stats.activity.in_period'), testId: 'stat-tasks-done' },
          { label: t('stats.planner.created'), value: number(totals.created), sub: t('stats.activity.in_period'), testId: 'stat-tasks-created' },
          { label: t('stats.planner.tracked'), value: hoursMinutes(totals.tracked), sub: t('stats.activity.in_period'), testId: 'stat-tracked' },
        ]}
      />
      <DailyChart
        rows={stats.days}
        range={stats}
        testId="chart-tasks"
        height={150}
        formatY={(value) => number(value)}
        summary={t('stats.planner.tasks_summary', {
          period: periodLabel(stats),
          done: totals.done,
          created: totals.created,
          tracked: hoursMinutes(totals.tracked),
        })}
        series={[
          { key: 'done', value: (day) => (day.date <= stats.today ? day.done : null), color: theme.food },
          { key: 'created', value: (day) => (day.date <= stats.today ? day.created : null), color: theme.rest, kind: 'dots', denseKind: 'line' },
        ]}
        tooltip={tooltip}
      />
      <Legend
        items={[
          { mark: isDense(stats.days) ? 'line' : 'bar', color: theme.food, label: t('stats.planner.done') },
          { mark: isDense(stats.days) ? 'line' : 'dot', color: theme.rest, label: t('stats.planner.created') },
        ]}
      />
      <DataTable
        caption={t('stats.planner.tasks_title')}
        testId="table-tasks"
        columns={[
          { label: t('stats.col.day') },
          { label: t('stats.planner.done'), align: 'right' },
          { label: t('stats.planner.created'), align: 'right' },
          { label: t('stats.planner.tracked'), align: 'right' },
        ]}
        rows={() =>
          stats.days
            .filter((day) => day.date <= stats.today)
            .map((day) => ({
              key: day.date,
              muted: day.done === 0 && day.created === 0 && day.tracked_minutes === 0,
              cells: [shortDay(day.date), number(day.done), number(day.created), hoursMinutes(day.tracked_minutes)],
            }))
        }
      />
    </StatCard>
  )
}

function AreasCard({ stats, dimmed }: { stats: PlannerStats; dimmed: boolean }) {
  const { t } = useTranslation()
  return (
    <StatCard title={t('stats.planner.areas_title')} hint={t('stats.planner.areas_hint')} testId="stats-areas" dimmed={dimmed}>
      <BarList
        testId="area-list"
        colorClass="bg-[rgb(var(--accent))]"
        items={stats.by_area.map((item) => ({
          key: item.area?.id ?? 'none',
          label: (
            <>
              <ColorDot color={item.area?.color} className="h-2.5 w-2.5" />
              <span className="truncate">{item.area?.name ?? t('stats.planner.no_area')}</span>
            </>
          ),
          value: item.minutes,
          target: item.target_minutes,
          text:
            item.target_minutes != null
              ? t('stats.planner.of_target', { value: hoursMinutes(item.minutes), target: hoursMinutes(item.target_minutes) })
              : hoursMinutes(item.minutes),
        }))}
      />
      {stats.by_area.some((item) => item.target_minutes != null) && (
        <Legend
          items={[
            { mark: 'bar', color: 'rgb(var(--accent))', label: t('stats.planner.legend_tracked') },
            { mark: 'line', color: 'rgb(var(--text))', label: t('stats.planner.legend_target') },
          ]}
        />
      )}
    </StatCard>
  )
}

function ProjectsCard({ stats, dimmed }: { stats: PlannerStats; dimmed: boolean }) {
  const { t } = useTranslation()
  return (
    <StatCard title={t('stats.planner.projects_title')} hint={t('stats.planner.projects_hint')} testId="stats-projects" dimmed={dimmed}>
      <ul className="space-y-3">
        {stats.projects.map((item) => (
          <li key={item.project.id} className="space-y-1.5" data-testid="project-row" data-done={item.done} data-total={item.total}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="flex min-w-0 items-center gap-2 font-medium">
                <ColorDot color={item.project.color} className="h-2.5 w-2.5" />
                <span className="truncate">{item.project.name}</span>
              </span>
              <span className="tnum shrink-0 text-xs muted">
                {t('stats.n_of_m', { n: item.done, m: item.total })}
                {item.done_in_period > 0 && ` · ${t('stats.planner.in_period', { n: item.done_in_period })}`}
              </span>
            </div>
            <ProgressBar value={item.done} max={Math.max(1, item.total)} />
          </li>
        ))}
      </ul>
    </StatCard>
  )
}
