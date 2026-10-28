/**
 * Прогресс: the stored daily score (0–100) as a line with the overall-streak
 * level, XP per day, and the streak calendar (counted days) — with a
 * way to the levels, streaks and achievements screen (`/stats/achievements`).
 * Every number is the server's; today's score is provisional and says so.
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { IconChevronRight } from '@/components/ui/icons'
import { number } from '@/lib/format'
import { OVERALL_MIN_SCORE } from '@/features/game/rules'
import { DailyChart, DENSE_AFTER, type TooltipLine } from '../charts/DailyChart'
import { StreakCalendar } from '../charts/StreakCalendar'
import { useChartTheme } from '../charts/theme'
import { progressTotals } from '../derive'
import { dayMonth, periodLabel, shortDay } from '../format'
import { DataTable, Legend, StatCard, Tiles } from '../parts'
import type { ProgressStats, ScorePoint, XpPoint } from '../types'

const SCORE_TICKS = [0, 20, 40, 60, 80, 100]

export function ProgressSection({ stats, dimmed, weekStart }: { stats: ProgressStats; dimmed: boolean; weekStart: number }) {
  return (
    <>
      <ScoreCard stats={stats} dimmed={dimmed} />
      <XpCard stats={stats} dimmed={dimmed} />
      <StreakCard stats={stats} dimmed={dimmed} weekStart={weekStart} />
    </>
  )
}

function ScoreCard({ stats, dimmed }: { stats: ProgressStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = progressTotals(stats)
  const counted = new Map(stats.streak_calendar.map((day) => [day.date, day]))
  const best = stats.scores.reduce<ScorePoint | null>((top, day) => (day.score != null && (top?.score == null || day.score > top.score) ? day : top), null)
  const colour = () => theme.score
  const tooltip = (day: ScorePoint): TooltipLine[] => {
    if (day.score == null) return [{ label: '', value: t('stats.no_data'), mark: 'none' }]
    const flag = counted.get(day.date)
    const note =
      day.date === stats.today
        ? t('stats.progress.provisional')
        : flag?.overall
          ? t('stats.progress.cell.overall')
          : t('stats.progress.cell.missed')
    return [{ label: note, value: t('stats.progress.score_value', { score: number(day.score) }), color: colour() }]
  }
  return (
    <StatCard title={t('stats.progress.score_title')} hint={t('stats.progress.score_hint', { min: OVERALL_MIN_SCORE })} testId="stats-score-card" dimmed={dimmed}>
      <Tiles
        items={[
          {
            label: t('stats.progress.score_avg'),
            value: totals.scoreMean != null ? number(totals.scoreMean) : '—',
            sub: totals.scoredDays ? t('stats.over_days', { n: totals.scoredDays }) : t('stats.no_finished_days'),
            testId: 'stat-score-avg',
          },
          {
            label: t('stats.progress.best_day'),
            value: best?.score != null ? number(best.score) : '—',
            sub: best ? dayMonth(best.date) : '',
            testId: 'stat-score-best',
          },
          {
            label: t('stats.progress.overall_days', { min: OVERALL_MIN_SCORE }),
            value: number(totals.overallDays),
            sub: t('stats.days_short'),
            testId: 'stat-overall-days',
          },
        ]}
      />
      <DailyChart
        rows={stats.scores}
        range={stats}
        testId="chart-score"
        yDomain={[0, 100]}
        yTicks={SCORE_TICKS}
        formatY={(value) => number(value)}
        summary={t('stats.progress.score_summary', {
          period: periodLabel(stats),
          avg: totals.scoreMean != null ? number(totals.scoreMean) : '—',
          n: totals.overallDays,
          min: OVERALL_MIN_SCORE,
        })}
        series={[
          {
            key: 'score',
            value: (day) => day.score,
            color: theme.score,
            outlineOf: (day) => (day.date === stats.today ? theme.score : undefined),
            kind: 'line',
            denseKind: 'line',
            dots: stats.scores.length <= DENSE_AFTER,
          },
        ]}
        target={{ value: OVERALL_MIN_SCORE }}
        tooltip={tooltip}
      />
      <Legend
        items={[
          { mark: 'line', color: theme.score, label: t('stats.progress.legend_score') },
          ...(stats.scores.some((day) => day.date === stats.today && day.score != null)
            ? [{ mark: 'ring' as const, color: theme.score, label: t('stats.progress.legend_provisional') }]
            : []),
          { mark: 'dash', color: theme.ink, label: t('stats.progress.legend_streak', { min: OVERALL_MIN_SCORE }) },
        ]}
      />
      <DataTable
        caption={t('stats.progress.score_title')}
        testId="table-score"
        columns={[{ label: t('stats.col.day') }, { label: t('stats.col.score'), align: 'right' }, { label: 'XP', align: 'right' }]}
        rows={() => {
          const xp = new Map(stats.xp.map((day) => [day.date, day.xp]))
          return stats.scores
            .filter((day) => day.date <= stats.today)
            .map((day) => ({
              key: day.date,
              muted: day.score == null,
              cells: [shortDay(day.date), day.score != null ? number(day.score) : '—', number(xp.get(day.date) ?? 0)],
            }))
        }}
      />
    </StatCard>
  )
}

function XpCard({ stats, dimmed }: { stats: ProgressStats; dimmed: boolean }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = progressTotals(stats)
  const best = stats.xp.reduce<XpPoint | null>((top, day) => (day.xp > 0 && (!top || day.xp > top.xp) ? day : top), null)
  const tooltip = (day: XpPoint): TooltipLine[] => [{ label: t('stats.progress.xp_day'), value: `${number(day.xp)} XP`, color: theme.xp }]
  return (
    <StatCard title={t('stats.progress.xp_title')} hint={t('stats.progress.xp_hint')} testId="stats-xp-card" dimmed={dimmed}>
      <Tiles
        items={[
          { label: t('stats.progress.xp_total'), value: number(totals.xp), unit: 'XP', sub: t('stats.activity.in_period'), testId: 'stat-xp-total' },
          {
            label: t('stats.progress.best_day'),
            value: best ? number(best.xp) : '—',
            unit: best ? 'XP' : undefined,
            sub: best ? dayMonth(best.date) : '',
            testId: 'stat-xp-best',
          },
        ]}
      />
      <DailyChart
        rows={stats.xp}
        range={stats}
        testId="chart-xp"
        height={140}
        formatY={(value) => number(value)}
        summary={t('stats.progress.xp_summary', { period: periodLabel(stats), xp: number(totals.xp) })}
        series={[{ key: 'xp', value: (day) => (day.date <= stats.today ? day.xp : null), color: theme.xp }]}
        tooltip={tooltip}
      />
    </StatCard>
  )
}

function StreakCard({ stats, dimmed, weekStart }: { stats: ProgressStats; dimmed: boolean; weekStart: number }) {
  const { t } = useTranslation()
  const theme = useChartTheme()
  const totals = progressTotals(stats)
  return (
    <StatCard
      title={t('stats.progress.calendar_title')}
      hint={t('stats.progress.calendar_sub', { min: OVERALL_MIN_SCORE })}
      testId="stats-streak-card"
      dimmed={dimmed}
    >
      <Tiles
        items={[
          { label: t('stats.progress.counted'), value: number(totals.overallDays), sub: t('stats.days_short'), testId: 'stat-streak-days' },
        ]}
      />
      <StreakCalendar
        days={stats.streak_calendar}
        period={stats.period}
        today={stats.today}
        weekStart={weekStart}
        summary={t('stats.progress.calendar_summary', {
          period: periodLabel(stats),
          n: totals.overallDays,
        })}
      />
      <Legend
        items={[
          { mark: 'cell', color: theme.food, label: t('stats.progress.legend_counted') },
          { mark: 'empty', color: theme.muted, label: t('stats.progress.legend_missed') },
        ]}
      />
      <Link
        to="/stats/achievements"
        className="btn-secondary min-h-tap w-full justify-between px-4 text-sm"
        data-testid="stats-to-achievements"
      >
        <span>{t('stats.progress.to_achievements')}</span>
        <IconChevronRight size={18} />
      </Link>
    </StatCard>
  )
}
