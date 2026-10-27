/**
 * Питание: calories per day against the target line and the zone band, plus
 * protein / fiber / water adherence and the averages.
 *
 * Safety (binding): a day without meals is «нет записей», never 0 kcal eaten;
 * a day below the safe floor is never highlighted as good (only finished days
 * the server marks `in_zone` get the accent); nothing here praises eating
 * less. Burned calories are not part of this section — they are in Activity.
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { ProgressBar } from '@/components/ui'
import { number } from '@/lib/format'
import { DailyChart, type TooltipLine } from '../charts/DailyChart'
import { useChartTheme } from '../charts/theme'
import { elapsedDays, fiberDays, hasNutrition } from '../derive'
import { periodLabel, shortDay } from '../format'
import { CardEmpty, DataTable, Legend, StatCard, Tiles } from '../parts'
import type { NutritionDay, NutritionStats } from '../types'

type Status = 'in_zone' | 'below_floor' | 'below_zone' | 'above_zone' | 'today' | 'no_meals' | 'future' | 'logged'

/** How the server judged a day, in words (comparisons against its own zone and floor). */
export function dayStatus(day: NutritionDay, stats: NutritionStats): Status {
  if (day.date > stats.today) return 'future'
  if (day.meals === 0 || day.kcal == null) return 'no_meals'
  if (day.date === stats.today) return 'today'
  if (day.in_zone === true) return 'in_zone'
  if (day.kcal < stats.floor) return 'below_floor'
  if (!stats.zone) return 'logged'
  return day.kcal < (stats.zone.low + stats.zone.high) / 2 ? 'below_zone' : 'above_zone'
}

export function NutritionSection({ stats, dimmed }: { stats: NutritionStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  if (!hasNutrition(stats)) {
    return (
      <StatCard title={t('stats.nutrition.kcal_title')} testId="stats-nutrition-card" dimmed={dimmed}>
        <CardEmpty
          title={t('stats.nutrition.empty')}
          description={t('stats.nutrition.empty_hint')}
          action={{ label: t('stats.nutrition.empty_action'), onClick: () => navigate('/food') }}
        />
      </StatCard>
    )
  }
  return (
    <>
      <KcalCard stats={stats} dimmed={dimmed} />
      <AdherenceCard stats={stats} dimmed={dimmed} />
    </>
  )
}

function KcalCard({ stats, dimmed }: { stats: NutritionStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const { averages, adherence, zone, target_kcal: target } = stats
  const elapsed = elapsedDays(stats.days, stats.today)
  const missing = stats.days.filter((day) => day.date <= stats.today && day.meals === 0).length
  const kcal = t('common.kcal')

  const colour = (day: NutritionDay) => {
    const status = dayStatus(day, stats)
    if (status === 'in_zone') return theme.food
    if (status === 'today') return theme.alpha(theme.food, 0.45)
    return theme.rest
  }

  const statusText = (day: NutritionDay): string => {
    const status = dayStatus(day, stats)
    switch (status) {
      case 'below_floor':
        return t('stats.nutrition.status.below_floor', { floor: number(stats.floor) })
      default:
        return t(`stats.nutrition.status.${status}`)
    }
  }

  const tooltip = (day: NutritionDay): TooltipLine[] => {
    if (day.meals === 0 || day.kcal == null) {
      const lines: TooltipLine[] = [{ label: '', value: t('stats.no_records'), mark: 'none' }]
      if (day.water_ml != null) lines.push({ label: t('stats.nutrition.water'), value: `${number(day.water_ml)} ${t('common.ml')}`, mark: 'none' })
      return lines
    }
    return [
      { label: statusText(day), value: `${number(day.kcal)} ${kcal}`, color: colour(day) },
      { label: t('stats.nutrition.meals_label'), value: number(day.meals), mark: 'none' },
      { label: t('stats.nutrition.protein'), value: `${number(day.protein_g)} ${t('common.g')}`, mark: 'none' },
      ...(day.water_ml != null
        ? [{ label: t('stats.nutrition.water'), value: `${number(day.water_ml)} ${t('common.ml')}`, mark: 'none' as const }]
        : []),
    ]
  }

  const summary = [
    t('stats.nutrition.summary', {
      period: periodLabel(stats),
      avg: averages.kcal != null ? number(averages.kcal) : '—',
      days: averages.days,
      zone: adherence.zone_days,
      missing,
    }),
    target != null && zone
      ? t('stats.nutrition.summary_target', { target: number(target), low: number(zone.low), high: number(zone.high) })
      : t('stats.nutrition.no_target'),
  ].join(' ')

  return (
    <StatCard
      title={t('stats.nutrition.kcal_title')}
      hint={
        target != null && zone
          ? t('stats.nutrition.kcal_hint', { target: number(target), low: number(zone.low), high: number(zone.high) })
          : t('stats.nutrition.no_target')
      }
      testId="stats-nutrition-card"
      dimmed={dimmed}
    >
      <Tiles
        items={[
          {
            label: t('stats.nutrition.avg'),
            value: averages.kcal != null ? `${number(averages.kcal)} ${kcal}` : '—',
            sub: averages.days ? t('stats.over_days', { n: averages.days }) : t('stats.no_finished_days'),
            testId: 'stat-kcal-avg',
          },
          {
            label: t('stats.nutrition.in_zone'),
            value: t('stats.n_of_m', { n: adherence.zone_days, m: averages.days }),
            sub: t('stats.days_short'),
            testId: 'stat-zone-days',
          },
          {
            label: t('stats.nutrition.logged'),
            value: t('stats.n_of_m', { n: adherence.logged_days, m: elapsed }),
            sub: t('stats.days_short'),
            testId: 'stat-logged-days',
          },
        ]}
      />
      <DailyChart
        rows={stats.days}
        range={stats}
        summary={summary}
        testId="chart-kcal"
        formatY={(value) => number(value)}
        series={[{ key: 'kcal', value: (day) => day.kcal, color: theme.food, colorOf: colour, ghost: true }]}
        target={target != null ? { value: target, label: t('stats.nutrition.target_label', { kcal: number(target) }) } : undefined}
        band={zone ?? undefined}
        tooltip={tooltip}
      />
      <Legend
        items={[
          { mark: 'bar', color: theme.food, label: t('stats.nutrition.legend_in_zone') },
          { mark: 'bar', color: theme.rest, label: t('stats.nutrition.legend_other') },
          { mark: 'bar', color: theme.alpha(theme.food, 0.45), label: t('stats.legend_today') },
          { mark: 'ghost', color: theme.muted, label: t('stats.legend_no_records') },
          ...(target != null ? [{ mark: 'dash' as const, color: theme.ink, label: t('stats.legend_target') }] : []),
          ...(zone ? [{ mark: 'band' as const, color: theme.food, label: t('stats.nutrition.legend_zone') }] : []),
        ]}
      />
      <DataTable
        caption={t('stats.nutrition.table_caption', { period: periodLabel(stats) })}
        testId="table-kcal"
        columns={[
          { label: t('stats.col.day') },
          { label: kcal, align: 'right' },
          { label: t('stats.col.meals'), align: 'right' },
          { label: t('stats.col.protein'), align: 'right' },
          { label: t('stats.col.water'), align: 'right' },
          { label: t('stats.col.status') },
        ]}
        rows={() =>
          stats.days
            .filter((day) => day.date <= stats.today)
            .map((day) => ({
              key: day.date,
              muted: day.meals === 0,
              cells: [
                shortDay(day.date),
                day.kcal != null ? number(day.kcal) : '—',
                number(day.meals),
                day.protein_g != null ? number(day.protein_g) : '—',
                day.water_ml != null ? number(day.water_ml) : '—',
                day.meals === 0 ? t('stats.no_records') : statusText(day),
              ],
            }))
        }
      />
    </StatCard>
  )
}

function AdherenceCard({ stats, dimmed }: { stats: NutritionStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const { averages, adherence } = stats
  const elapsed = elapsedDays(stats.days, stats.today)
  const fiber = fiberDays(stats)
  const g = t('common.g')
  const rows = [
    {
      key: 'protein',
      label: stats.target_protein_g != null ? t('stats.nutrition.protein_goal', { g: number(stats.target_protein_g) }) : t('stats.nutrition.protein'),
      n: adherence.protein_days,
      m: adherence.logged_days,
      avg: averages.protein_g != null ? t('stats.nutrition.avg_value', { value: `${number(averages.protein_g)} ${g}` }) : null,
      enabled: stats.target_protein_g != null,
    },
    {
      key: 'fiber',
      label: stats.target_fiber_g != null ? t('stats.nutrition.fiber_goal', { g: number(stats.target_fiber_g) }) : t('stats.nutrition.fiber'),
      n: fiber,
      m: adherence.logged_days,
      avg: averages.fiber_g != null ? t('stats.nutrition.avg_value', { value: `${number(averages.fiber_g)} ${g}` }) : null,
      enabled: stats.target_fiber_g != null,
    },
    {
      key: 'water',
      label: stats.target_water_ml != null
        ? t('stats.nutrition.water_goal', { ml: number(stats.target_water_ml) })
        : t('stats.nutrition.water'),
      n: adherence.water_days,
      m: elapsed,
      avg: averages.water_ml != null ? t('stats.nutrition.avg_value', { value: `${number(averages.water_ml)} ${t('common.ml')}` }) : null,
      enabled: stats.target_water_ml != null,
    },
  ]
  const macros = [
    averages.fat_g != null ? `${t('stats.nutrition.fat')} ${number(averages.fat_g)} ${g}` : null,
    averages.carbs_g != null ? `${t('stats.nutrition.carbs')} ${number(averages.carbs_g)} ${g}` : null,
    averages.meals != null ? t('stats.nutrition.meals_avg', { n: number(averages.meals, 1) }) : null,
  ].filter(Boolean)

  return (
    <StatCard
      title={t('stats.nutrition.adherence_title')}
      hint={t('stats.nutrition.adherence_hint')}
      testId="stats-adherence-card"
      dimmed={dimmed}
    >
      <ul className="space-y-3">
        {rows.map((row) => (
          <li key={row.key} className="space-y-1.5" data-testid={`adherence-${row.key}`} data-n={row.n} data-m={row.m}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-sm font-medium">{row.label}</span>
              <span className="tnum shrink-0 text-sm font-semibold">
                {row.enabled ? t('stats.n_of_m_days', { n: row.n, m: row.m }) : '—'}
              </span>
            </div>
            <ProgressBar value={row.enabled ? row.n : 0} max={Math.max(1, row.m)} />
            {row.avg && <p className="tnum text-xs muted">{row.avg}</p>}
          </li>
        ))}
      </ul>
      {macros.length > 0 && (
        <p className="tnum text-xs muted" data-testid="macros-avg">
          {t('stats.nutrition.macros_avg', { list: macros.join(' · ') })}
        </p>
      )}
    </StatCard>
  )
}
