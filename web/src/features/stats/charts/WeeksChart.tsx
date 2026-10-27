/**
 * Workouts per week against what the week needs (the weekly plan, or 3
 * without one — the workout streak's rule, sent by the server as `required`):
 * a bar per week, a dash at the required level. Rest days are part of a plan,
 * so the unit is the week, never the day.
 */
import { Bar, CartesianGrid, Cell, ComposedChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis, type TooltipProps } from 'recharts'
import { useTranslation } from 'react-i18next'

import { number } from '@/lib/format'
import { useChartTheme, useReducedMotion } from './theme'
import { addDays } from '@/lib/format'
import { dayMonth, monthNarrow, weekRange } from '../format'
import type { ActivityWeek } from '../types'

interface Datum {
  week: string
  workouts: number
  required: number
  __week: ActivityWeek
}

export function WeeksChart({
  weeks,
  summary,
  height = 150,
}: {
  weeks: ActivityWeek[]
  summary: string
  height?: number
}) {
  const theme = useChartTheme()
  const reduced = useReducedMotion()
  const data: Datum[] = weeks.map((week) => ({ week: week.week_start, workouts: week.workouts, required: week.required, __week: week }))
  const top = Math.max(3, ...weeks.map((week) => Math.max(week.workouts, week.required)))
  const dense = weeks.length > 16
  // label every week (every other one past two months); a year names each month once, at the
  // first week whose middle (start + 3 days) falls in it
  const middle = (week: ActivityWeek) => addDays(week.week_start, 3)
  const ticks = dense
    ? weeks
        .filter((week, index) => index === 0 || middle(week).slice(5, 7) !== middle(weeks[index - 1]).slice(5, 7))
        .map((week) => week.week_start)
    : weeks.map((week) => week.week_start)
  const step = weeks.length > 8 ? 2 : 1

  return (
    <div role="img" aria-label={summary} data-testid="chart-weeks" className="stats-chart -mx-1 select-none">
      <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 320, height }}>
        <ComposedChart data={data} margin={{ top: 8, right: 0, bottom: 0, left: 4 }} barCategoryGap={dense ? 1 : 2}>
          <CartesianGrid vertical={false} stroke={theme.grid} />
          <XAxis
            dataKey="week"
            ticks={dense ? ticks : ticks.filter((_, index) => index % step === 0)}
            interval={0}
            tickLine={false}
            axisLine={{ stroke: theme.grid }}
            height={22}
            tick={{ fill: theme.muted, fontSize: 11 }}
            tickFormatter={(value: string) => (dense ? monthNarrow(addDays(value, 3)) : dayMonth(value).replace('.', ''))}
          />
          <YAxis
            orientation="right"
            width={24}
            domain={[0, top]}
            allowDecimals={false}
            tickCount={Math.min(top + 1, 5)}
            tickLine={false}
            axisLine={false}
            tick={{ fill: theme.muted, fontSize: 11 }}
          />
          <Tooltip
            content={<WeekTooltip />}
            cursor={{ fill: theme.alpha(theme.ink, theme.dark ? 0.07 : 0.06) }}
            isAnimationActive={false}
            position={{ y: 0 }}
            allowEscapeViewBox={{ x: false, y: true }}
            wrapperStyle={{ outline: 'none', zIndex: 5 }}
          />
          <Bar dataKey="workouts" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={!reduced} animationDuration={350} className="stats-bar stats-bar-workouts">
            {data.map((datum) => (
              <Cell key={datum.week} fill={datum.workouts >= datum.required ? theme.move : theme.rest} />
            ))}
          </Bar>
          <Scatter
            dataKey="required"
            isAnimationActive={false}
            className="stats-required"
            shape={(props: { cx?: number; cy?: number }) => (
              <line
                x1={(props.cx ?? 0) - (dense ? 3 : 11)}
                x2={(props.cx ?? 0) + (dense ? 3 : 11)}
                y1={props.cy}
                y2={props.cy}
                stroke={theme.ink}
                strokeOpacity={0.75}
                strokeWidth={2}
                strokeLinecap="round"
              />
            )}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function WeekTooltip({ active, payload }: TooltipProps<number, string>) {
  const { t } = useTranslation()
  const datum = payload?.[0]?.payload as Datum | undefined
  if (!active || !datum) return null
  const week = datum.__week
  return (
    <div
      className="pointer-events-none min-w-[9rem] rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))] px-3 py-2 text-xs shadow-card"
      data-testid="chart-tooltip"
    >
      <p className="mb-1 font-medium muted">{weekRange(week.week_start)}</p>
      <p>
        <span className="tnum font-semibold">{t('stats.n_of_m', { n: week.workouts, m: week.required })}</span>{' '}
        <span className="muted">{week.planned > 0 ? t('stats.activity.week_planned') : t('stats.activity.week_default')}</span>
      </p>
      {week.volume_kg > 0 && (
        <p className="tnum muted">{t('stats.activity.volume_value', { kg: number(week.volume_kg) })}</p>
      )}
    </div>
  )
}
