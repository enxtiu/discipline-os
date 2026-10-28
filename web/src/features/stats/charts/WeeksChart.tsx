/**
 * Workouts per week against what the week needs (the weekly plan, or 3
 * without one — the workout streak's rule, sent by the server as `required`):
 * a bar per week, a dash at the required level. Rest days are part of a plan,
 * so the unit is the week, never the day. Tap a week to read it.
 */
import { Bar, CartesianGrid, Cell, ComposedChart, ReferenceArea, ResponsiveContainer, Scatter, XAxis, YAxis } from 'recharts'
import { useTranslation } from 'react-i18next'

import { addDays, number } from '@/lib/format'
import { TipBox, useChartPick } from './pick'
import { useChartTheme, useReducedMotion } from './theme'
import { dayMonth, monthNarrow, weekRange } from '../format'
import type { ActivityWeek } from '../types'

interface Datum {
  week: string
  workouts: number
  required: number
}

export function WeeksChart({ weeks, summary, height = 150 }: { weeks: ActivityWeek[]; summary: string; height?: number }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const reduced = useReducedMotion()
  const { picked, ref, handlers } = useChartPick(weeks.map((week) => week.week_start).join(','))
  const data: Datum[] = weeks.map((week) => ({ week: week.week_start, workouts: week.workouts, required: week.required }))
  const top = Math.max(3, ...weeks.map((week) => Math.max(week.workouts, week.required)))
  const dense = weeks.length > 16
  // label every week (every other one past two months); a year names each month once, at the
  // first week whose middle (start + 3 days) falls in it
  const middle = (week: ActivityWeek) => addDays(week.week_start, 3)
  const ticks = dense
    ? weeks
        .filter((week, index) => index === 0 || middle(week).slice(5, 7) !== middle(weeks[index - 1]).slice(5, 7))
        .map((week) => week.week_start)
    : weeks.map((week) => week.week_start).filter((_, index) => weeks.length <= 8 || index % 2 === 0)
  const chosen = picked ? weeks[picked.index] : undefined

  return (
    <div ref={ref} role="img" aria-label={summary} data-testid="chart-weeks" className="stats-chart relative -mx-1 touch-pan-y select-none">
      <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 320, height }}>
        <ComposedChart data={data} margin={{ top: 8, right: 0, bottom: 0, left: 4 }} barCategoryGap={dense ? 1 : 2} {...handlers}>
          <CartesianGrid vertical={false} stroke={theme.grid} />
          <XAxis
            dataKey="week"
            ticks={ticks}
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
          {chosen && (
            <ReferenceArea
              x1={chosen.week_start}
              x2={chosen.week_start}
              ifOverflow="hidden"
              fill={theme.ink}
              fillOpacity={theme.dark ? 0.08 : 0.06}
              stroke="none"
              className="stats-picked"
            />
          )}
          <Bar dataKey="workouts" maxBarSize={24} radius={dense ? 0 : [4, 4, 0, 0]} isAnimationActive={!reduced} animationDuration={350} className="stats-bar stats-bar-workouts">
            {data.map((datum) => (
              <Cell
                key={datum.week}
                fill={datum.workouts >= datum.required ? theme.move : theme.rest}
                stroke={theme.outline ?? undefined}
                strokeWidth={theme.outline ? theme.outlineWidth : undefined}
              />
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
      {picked && chosen && (
        <TipBox x={picked.x} width={ref.current?.clientWidth ?? 320}>
          <p className="mb-1 font-medium muted">{weekRange(chosen.week_start)}</p>
          <p>
            <span className="tnum font-semibold text-[rgb(var(--text))]">{t('stats.n_of_m', { n: chosen.workouts, m: chosen.required })}</span>{' '}
            <span className="muted">{chosen.planned > 0 ? t('stats.activity.week_planned') : t('stats.activity.week_default')}</span>
          </p>
          {chosen.volume_kg > 0 && <p className="tnum muted">{t('stats.activity.volume_value', { kg: number(chosen.volume_kg) })}</p>}
        </TipBox>
      )}
    </div>
  )
}
