/**
 * Тело: weigh-ins with the server's 7-day mean and the 30-day change (shown
 * neutrally — no colour or wording that rewards losing weight fast), and sleep
 * per night with the period's average.
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { number } from '@/lib/format'
import { DailyChart, type TooltipLine } from '../charts/DailyChart'
import { useChartTheme } from '../charts/theme'
import { bodyTotals } from '../derive'
import { dayMonth, hoursMinutes, hoursTick, periodLabel, shortDay, signed } from '../format'
import { CardEmpty, DataTable, Legend, StatCard, Tiles } from '../parts'
import type { BodyStats, SleepPoint, WeightPoint } from '../types'

const SLEEP_TICK = 180 // minutes: 0 · 3 h · 6 h · 9 h

export function BodySection({ stats, dimmed }: { stats: BodyStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const totals = bodyTotals(stats)
  const anyWeight = stats.weight.some((point) => point.kg != null || point.avg7 != null)
  if (!anyWeight && totals.nights === 0) {
    return (
      <StatCard title={t('stats.body.weight_title')} testId="stats-weight-card" dimmed={dimmed}>
        <CardEmpty
          title={t('stats.body.empty')}
          description={t('stats.body.empty_hint')}
          action={{ label: t('stats.body.empty_action'), onClick: () => navigate('/health') }}
        />
      </StatCard>
    )
  }
  return (
    <>
      <WeightCard stats={stats} dimmed={dimmed} />
      <SleepCard stats={stats} dimmed={dimmed} />
    </>
  )
}

function WeightCard({ stats, dimmed }: { stats: BodyStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = bodyTotals(stats)
  const kg = t('common.kg')
  const anyPoint = stats.weight.some((point) => point.kg != null || point.avg7 != null)
  const tooltip = (point: WeightPoint): TooltipLine[] => [
    point.kg != null
      ? { label: t('stats.body.weigh_in'), value: `${number(point.kg, 1)} ${kg}`, color: theme.ink }
      : { label: t('stats.body.weigh_in'), value: '—', mark: 'none' },
    point.avg7 != null
      ? { label: t('stats.body.avg7'), value: `${number(point.avg7, 1)} ${kg}`, color: theme.food }
      : { label: t('stats.body.avg7'), value: '—', mark: 'none' },
  ]
  return (
    <StatCard title={t('stats.body.weight_title')} hint={t('stats.body.weight_hint')} testId="stats-weight-card" dimmed={dimmed}>
      <Tiles
        items={[
          {
            label: t('stats.body.latest'),
            value: totals.latest ? `${number(totals.latest.kg, 1)} ${kg}` : '—',
            sub: totals.latest ? dayMonth(totals.latest.date) : t('stats.body.no_weigh_in'),
            testId: 'stat-weight-latest',
          },
          {
            label: t('stats.body.change_30d'),
            value: stats.change_30d != null ? `${signed(stats.change_30d)} ${kg}` : '—',
            sub: stats.change_30d != null ? t('stats.body.change_hint') : t('stats.body.change_none'),
            testId: 'stat-weight-change',
          },
          { label: t('stats.body.weigh_ins'), value: number(totals.weighIns), sub: t('stats.activity.in_period'), testId: 'stat-weigh-ins' },
        ]}
      />
      {anyPoint ? (
        <>
          <DailyChart
            rows={stats.weight}
            range={stats}
            testId="chart-weight"
            fitY
            formatY={(value) => number(value)}
            summary={t('stats.body.weight_summary', {
              period: periodLabel(stats),
              latest: totals.latest ? number(totals.latest.kg, 1) : '—',
              n: totals.weighIns,
              change: stats.change_30d != null ? signed(stats.change_30d) : '—',
            })}
            series={[
              { key: 'avg7', value: (point) => point.avg7, color: theme.food, kind: 'line', smooth: true },
              { key: 'kg', value: (point) => point.kg, color: theme.ink, kind: 'dots' },
            ]}
            tooltip={tooltip}
          />
          <Legend
            items={[
              { mark: 'dot', color: theme.ink, label: t('stats.body.legend_weigh_in') },
              { mark: 'line', color: theme.food, label: t('stats.body.legend_avg7') },
            ]}
          />
          <DataTable
            caption={t('stats.body.weight_title')}
            testId="table-weight"
            columns={[{ label: t('stats.col.day') }, { label: t('stats.col.weight'), align: 'right' }, { label: t('stats.col.avg7'), align: 'right' }]}
            rows={() =>
              stats.weight
                .filter((point) => point.kg != null || point.avg7 != null)
                .map((point) => ({
                  key: point.date,
                  muted: point.kg == null,
                  cells: [shortDay(point.date), point.kg != null ? number(point.kg, 1) : '—', point.avg7 != null ? number(point.avg7, 1) : '—'],
                }))
            }
          />
        </>
      ) : (
        <p className="text-sm muted">{t('stats.body.no_weigh_in')}</p>
      )}
    </StatCard>
  )
}

function SleepCard({ stats, dimmed }: { stats: BodyStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = bodyTotals(stats)
  const longest = Math.max(0, ...stats.sleep.map((night) => night.minutes ?? 0))
  const ceiling = Math.max(SLEEP_TICK * 3, Math.ceil(longest / SLEEP_TICK) * SLEEP_TICK)
  const ticks = Array.from({ length: ceiling / SLEEP_TICK + 1 }, (_, index) => index * SLEEP_TICK)
  const tooltip = (night: SleepPoint): TooltipLine[] =>
    night.minutes == null
      ? [{ label: '', value: t('stats.no_records'), mark: 'none' }]
      : [{ label: t('stats.body.night'), value: hoursMinutes(night.minutes), color: theme.sleep }]
  return (
    <StatCard title={t('stats.body.sleep_title')} hint={t('stats.body.sleep_hint')} testId="stats-sleep-card" dimmed={dimmed}>
      <Tiles
        items={[
          {
            label: t('stats.body.sleep_avg'),
            value: stats.sleep_avg != null ? hoursMinutes(stats.sleep_avg) : '—',
            sub: t('stats.body.per_night'),
            testId: 'stat-sleep-avg',
          },
          { label: t('stats.body.nights'), value: number(totals.nights), sub: t('stats.body.nights_hint'), testId: 'stat-nights' },
        ]}
      />
      {totals.nights > 0 ? (
        <>
          <DailyChart
            rows={stats.sleep}
            range={stats}
            testId="chart-sleep"
            yDomain={[0, ceiling]}
            yTicks={ticks}
            formatY={hoursTick}
            summary={t('stats.body.sleep_summary', {
              period: periodLabel(stats),
              avg: stats.sleep_avg != null ? hoursMinutes(stats.sleep_avg) : '—',
              n: totals.nights,
            })}
            series={[{ key: 'minutes', value: (night) => night.minutes, color: theme.sleep, ghost: true }]}
            average={stats.sleep_avg != null ? { value: stats.sleep_avg, label: t('stats.body.avg_label', { value: hoursMinutes(stats.sleep_avg) }) } : undefined}
            tooltip={tooltip}
          />
          <Legend
            items={[
              { mark: 'bar', color: theme.sleep, label: t('stats.body.legend_night') },
              { mark: 'ghost', color: theme.muted, label: t('stats.legend_no_records') },
              ...(stats.sleep_avg != null ? [{ mark: 'dash' as const, color: theme.ink, label: t('stats.legend_average') }] : []),
            ]}
          />
        </>
      ) : (
        <p className="text-sm muted">{t('stats.body.no_sleep')}</p>
      )}
    </StatCard>
  )
}
