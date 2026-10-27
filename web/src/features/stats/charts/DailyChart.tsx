/**
 * One chart over the days of a period (Recharts `ComposedChart`).
 *
 * Marks follow one spec everywhere: bars ≤ 24 px with 4 px rounded tops on a
 * single baseline and a 2 px gap; 2 px lines; dots with a ring in the card
 * colour; hairline horizontal grid; muted tabular ticks on the right at round
 * values. Up to a month the days are bars; a quarter or a year (90–366 days)
 * is drawn with each series' dense form — dots (one per day, coloured like
 * the bars would be) or a line broken where nothing was logged.
 *
 * "Nothing logged" is never drawn as zero: a series with `ghost` marks a past
 * day without data with a small dashed placeholder (bars), a dense series
 * simply leaves the day empty, and every tooltip says «нет записей». Tooltips
 * open on tap and follow a dragging finger; the chart itself is one
 * `role="img"` whose name is the text summary the card passes.
 */
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from 'recharts'

import { useChartTheme, useReducedMotion, type ChartTheme } from './theme'
import { tickDays, tickLabel, shortDay } from '../format'
import type { StatsRange } from '../types'

export type SeriesKind = 'bar' | 'line' | 'dots'

export interface ChartSeries<Row> {
  key: string
  value: (row: Row) => number | null
  color: string
  /** per-day colour (emphasis: in the zone vs not); default `color` */
  colorOf?: (row: Row) => string
  /** the mark up to a month (default: bars) */
  kind?: SeriesKind
  /** the mark for a quarter or a year (default: a line) */
  denseKind?: SeriesKind
  /** dashed placeholder for a past day without data (bars only) */
  ghost?: boolean
  /** smooth curve (a moving average); daily values stay straight segments */
  smooth?: boolean
  /** lines: a dot on every day (default: only on a day with no data on either side) */
  dots?: boolean
}

export interface TooltipLine {
  label: string
  value: string
  color?: string
  /** the key mark beside the label: a short line (default), a dashed one, none */
  mark?: 'line' | 'dash' | 'none'
}

export interface DailyChartProps<Row extends { date: string }> {
  rows: Row[]
  range: StatsRange
  series: ChartSeries<Row>[]
  /** what the tooltip shows for one day (values lead, labels follow) */
  tooltip: (row: Row) => TooltipLine[]
  /** the chart's accessible name: a sentence with the key numbers */
  summary: string
  height?: number
  formatY?: (value: number) => string
  /** fixed y range (e.g. a score 0–100); default 0 … a round ceiling over data, target and band */
  yDomain?: [number, number]
  /** start the y axis near the data instead of 0 (weight) */
  fitY?: boolean
  yTicks?: number[]
  target?: { value: number }
  band?: { low: number; high: number }
  average?: { value: number }
  testId?: string
}

/** More than a month of days is drawn in each series' dense form. */
export const DENSE_AFTER = 31
const GHOST_SHARE = 0.035

export function isDense(rows: unknown[]): boolean {
  return rows.length > DENSE_AFTER
}

/** Round ticks from 0: the step is 1, 2, 2.5 or 5 × 10ⁿ, about `count` intervals. */
export function niceTicks(max: number, count = 3): number[] {
  if (!(max > 0)) return [0, 1]
  const raw = max / count
  const power = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= raw) ?? 10 * power
  const ticks: number[] = [0]
  while (ticks[ticks.length - 1] < max) ticks.push(Math.round((ticks[ticks.length - 1] + step) * 1000) / 1000)
  return ticks
}

type Datum = { date: string; __row: unknown } & Record<string, unknown>

export function DailyChart<Row extends { date: string }>({
  rows,
  range,
  series,
  tooltip,
  summary,
  height = 168,
  formatY = (value) => String(value),
  yDomain,
  fitY,
  yTicks,
  target,
  band,
  average,
  testId,
}: DailyChartProps<Row>) {
  const theme = useChartTheme()
  const reduced = useReducedMotion()
  const dense = isDense(rows)
  const today = range.today

  const kinds: SeriesKind[] = series.map((item) => (dense ? (item.denseKind ?? 'line') : (item.kind ?? 'bar')))
  const values = rows.flatMap((row) => series.map((item) => item.value(row))).filter((v): v is number => v != null)
  const top = Math.max(1, ...values, target?.value ?? 0, band?.high ?? 0, average?.value ?? 0)

  let domain: [number, number] | undefined = yDomain
  let axisTicks = yTicks
  if (!domain && fitY && values.length) {
    const low = Math.min(...values)
    const high = Math.max(...values)
    const pad = Math.max(0.5, (high - low) * 0.25)
    domain = [Math.floor(low - pad), Math.ceil(high + pad)]
  } else if (!domain) {
    axisTicks = yTicks ?? niceTicks(top)
    domain = [0, axisTicks[axisTicks.length - 1]]
  }

  const data: Datum[] = rows.map((row, index) => {
    const datum: Datum = { date: row.date, __row: row }
    series.forEach((item, s) => {
      const value = item.value(row)
      datum[item.key] = value
      if (item.ghost && kinds[s] === 'bar') {
        datum[`${item.key}__ghost`] = value == null && row.date <= today ? top * GHOST_SHARE : null
      }
      if (kinds[s] === 'line') {
        // an isolated day (no data on either side) still shows as a dot
        const before = index > 0 ? item.value(rows[index - 1]) : null
        const after = index < rows.length - 1 ? item.value(rows[index + 1]) : null
        datum[`${item.key}__alone`] = value != null && before == null && after == null
      }
    })
    return datum
  })

  const ticks = tickDays(
    rows.map((row) => row.date),
    range.period,
  )
  const widest = Math.max(...(axisTicks ?? domain ?? [top]).map((value) => formatY(value).length))
  const yWidth = Math.max(26, Math.ceil(widest * 6.4) + 10)
  const hasBars = kinds.includes('bar')
  const animate = !reduced
  const dotRadius = dense ? (rows.length > 120 ? 1.6 : 2.2) : 3.5

  return (
    <div role="img" aria-label={summary} data-testid={testId} data-dense={dense || undefined} className="stats-chart -mx-1 select-none">
      <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 320, height }}>
        <ComposedChart data={data} margin={{ top: 8, right: 0, bottom: 0, left: 4 }} barCategoryGap={dense ? 0 : 2} barGap={2}>
          <CartesianGrid vertical={false} stroke={theme.grid} strokeWidth={1} />
          {band && (
            <ReferenceArea
              y1={band.low}
              y2={band.high}
              ifOverflow="extendDomain"
              fill={theme.food}
              fillOpacity={theme.dark ? 0.13 : 0.16}
              stroke="none"
              className="stats-zone"
            />
          )}
          <XAxis
            dataKey="date"
            ticks={ticks}
            interval={0}
            tickLine={false}
            axisLine={{ stroke: theme.grid }}
            height={22}
            tick={(props: TickProps) => <DayTick {...props} theme={theme} today={today} period={range.period} />}
          />
          <YAxis
            orientation="right"
            width={yWidth}
            domain={domain}
            ticks={axisTicks}
            tickCount={axisTicks ? undefined : 4}
            interval={0}
            allowDecimals={false}
            tickLine={false}
            axisLine={false}
            tick={{ fill: theme.muted, fontSize: 11 }}
            tickFormatter={(value: number) => formatY(value)}
          />
          <Tooltip
            content={<TooltipBox render={tooltip as (row: unknown) => TooltipLine[]} theme={theme} />}
            cursor={hasBars ? { fill: theme.alpha(theme.ink, theme.dark ? 0.07 : 0.06) } : { stroke: theme.muted, strokeWidth: 1 }}
            isAnimationActive={false}
            position={{ y: 0 }}
            offset={12}
            allowEscapeViewBox={{ x: false, y: true }}
            wrapperStyle={{ outline: 'none', zIndex: 5 }}
          />
          {!hasBars && (
            // a bar series with no values: the category axis becomes a band scale, so the days
            // of a line sit at the centres of their slots (aligned with the ticks), not on the edges
            <Bar dataKey="__slot" fill="none" isAnimationActive={false} legendType="none" />
          )}
          {series.map((item, s) => {
            const kind = kinds[s]
            if (kind === 'bar') {
              const stack = item.ghost ? `${item.key}-stack` : undefined
              return [
                <Bar
                  key={item.key}
                  dataKey={item.key}
                  stackId={stack}
                  fill={item.color}
                  maxBarSize={24}
                  radius={dense ? 0 : [4, 4, 0, 0]}
                  isAnimationActive={animate}
                  animationDuration={350}
                  className={`stats-bar stats-bar-${item.key}`}
                >
                  {item.colorOf && rows.map((row) => <Cell key={row.date} fill={item.colorOf?.(row) ?? item.color} />)}
                </Bar>,
                item.ghost ? (
                  <Bar
                    key={`${item.key}__ghost`}
                    dataKey={`${item.key}__ghost`}
                    stackId={stack}
                    fill="none"
                    stroke={theme.muted}
                    strokeOpacity={0.7}
                    strokeDasharray="2 2"
                    maxBarSize={24}
                    radius={[3, 3, 0, 0]}
                    isAnimationActive={false}
                    className="stats-ghost"
                  />
                ) : null,
              ]
            }
            const line = kind === 'line'
            return (
              <Line
                key={item.key}
                dataKey={item.key}
                type={item.smooth ? 'monotone' : 'linear'}
                stroke={line ? item.color : 'none'}
                strokeWidth={dense && line ? 1.5 : 2}
                strokeLinecap="round"
                strokeLinejoin="round"
                connectNulls={false}
                isAnimationActive={animate && line}
                animationDuration={450}
                className={`stats-${kind} stats-${kind}-${item.key}`}
                dot={({ key: _key, ...props }: DotProps & { key?: unknown }) => (
                  <SeriesDot
                    key={`${item.key}-${props.index}`}
                    {...props}
                    dataKey={item.key}
                    color={item.colorOf ? item.colorOf((props.payload as Datum).__row as Row) : item.color}
                    surface={theme.surface}
                    radius={dotRadius}
                    ring={!dense}
                    always={!line || item.dots === true}
                  />
                )}
                activeDot={{ r: 4.5, fill: item.color, stroke: theme.surface, strokeWidth: 2 }}
              />
            )
          })}
          {target && (
            <ReferenceLine
              y={target.value}
              ifOverflow="extendDomain"
              stroke={theme.ink}
              strokeOpacity={0.6}
              strokeWidth={1.5}
              strokeDasharray="5 4"
              className="stats-target"
            />
          )}
          {average && (
            <ReferenceLine
              y={average.value}
              ifOverflow="extendDomain"
              stroke={theme.ink}
              strokeOpacity={0.5}
              strokeWidth={1.5}
              strokeDasharray="2 3"
              className="stats-average"
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Parts
// --------------------------------------------------------------------------- //
interface TickProps {
  x?: number
  y?: number
  payload?: { value: string }
}

function DayTick({ x = 0, y = 0, payload, theme, today, period }: TickProps & { theme: ChartTheme; today: string; period: StatsRange['period'] }) {
  const day = payload?.value ?? ''
  const isToday = day === today
  const future = day > today
  return (
    <text
      x={x}
      y={y + 13}
      textAnchor={period === 'week' || period === 'month' ? 'middle' : 'start'}
      fontSize={11}
      fontWeight={isToday ? 700 : 400}
      fill={isToday ? theme.ink : theme.muted}
      fillOpacity={future ? 0.55 : 1}
      className="tnum"
    >
      {tickLabel(day, period)}
    </text>
  )
}

interface DotProps {
  cx?: number
  cy?: number
  index?: number
  value?: number | null
  payload?: unknown
}

function SeriesDot({
  cx,
  cy,
  value,
  payload,
  dataKey,
  color,
  surface,
  radius,
  ring,
  always,
}: DotProps & { dataKey: string; color: string; surface: string; radius: number; ring: boolean; always: boolean }) {
  if (value == null || cx == null || cy == null) return null
  const alone = Boolean((payload as Record<string, unknown> | undefined)?.[`${dataKey}__alone`])
  if (!always && !alone) return null
  return (
    <circle
      cx={cx}
      cy={cy}
      r={always ? radius : Math.max(radius, 2.5)}
      fill={color}
      stroke={ring ? surface : 'none'}
      strokeWidth={ring ? 2 : 0}
      className="stats-dot"
    />
  )
}

function TooltipBox({
  active,
  payload,
  render,
  theme,
}: TooltipProps<number, string> & { render: (row: unknown) => TooltipLine[]; theme: ChartTheme }) {
  const datum = payload?.[0]?.payload as Datum | undefined
  if (!active || !datum) return null
  const lines = render(datum.__row)
  return (
    <div
      className="pointer-events-none min-w-[9rem] max-w-[15rem] rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))] px-3 py-2 text-xs shadow-card"
      data-testid="chart-tooltip"
    >
      <p className="mb-1 font-medium muted">{shortDay(datum.date)}</p>
      <ul className="space-y-0.5">
        {lines.map((line, index) => (
          <li key={index} className="flex items-center gap-2">
            {line.mark !== 'none' && (
              <span
                aria-hidden="true"
                className="inline-block h-0 w-3 shrink-0 border-t-2"
                style={{ borderColor: line.color ?? theme.muted, borderStyle: line.mark === 'dash' ? 'dashed' : 'solid' }}
              />
            )}
            <span className="tnum font-semibold text-[rgb(var(--text))]">{line.value}</span>
            <span className="min-w-0 truncate muted">{line.label}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
