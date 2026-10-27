/**
 * /stats (stage 8): the dashboards — Питание, Активность, Тело, План,
 * Прогресс — for one calendar period, with a period switcher above everything
 * it scopes. Lazy chunk (Recharts lives here, never in the main bundle).
 *
 * The period and the anchor day live in the URL (`?period=month&date=…`), so a
 * reload or the back button keeps them; without `date` the server's today is
 * the anchor. Each section loads on its own and has its own skeleton, error
 * with retry and empty state; while another period loads, the previous one
 * stays on screen, dimmed.
 */
import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { UseQueryResult } from '@tanstack/react-query'

import { IconTrophy } from '@/features/game/icons'
import { useAuth } from '@/features/auth/AuthProvider'
import { addDays } from '@/lib/format'
import { PERIODS, PeriodSwitcher } from './PeriodSwitcher'
import { CardError, CardSkeleton } from './parts'
import { useStats, useStatsAutoRefresh } from './queries'
import { ActivitySection } from './sections/ActivitySection'
import { BodySection } from './sections/BodySection'
import { NutritionSection } from './sections/NutritionSection'
import { PlannerSection } from './sections/PlannerSection'
import { ProgressSection } from './sections/ProgressSection'
import type { Period, StatsRange, StatsSection } from './types'

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/
const SECTIONS: StatsSection[] = ['nutrition', 'activity', 'body', 'planner', 'progress']

export default function StatsPage() {
  const { t } = useTranslation()
  const { session } = useAuth()
  const [params, setParams] = useSearchParams()
  const rawPeriod = params.get('period')
  const period: Period = PERIODS.includes(rawPeriod as Period) ? (rawPeriod as Period) : 'week'
  const rawDate = params.get('date')
  const date = rawDate && ISO_DAY.test(rawDate) ? rawDate : null
  useStatsAutoRefresh()

  const nutrition = useStats('nutrition', period, date)
  const activity = useStats('activity', period, date)
  const body = useStats('body', period, date)
  const planner = useStats('planner', period, date)
  const progress = useStats('progress', period, date)
  const queries = [nutrition, activity, body, planner, progress]

  // the shown period: the first section that answered for the current request
  const fresh = queries.find((query) => query.data && !query.isPlaceholderData)?.data as StatsRange | undefined
  const shown = fresh ?? (queries.find((query) => query.data)?.data as StatsRange | undefined) ?? null
  const loading = queries.some((query) => query.isFetching)

  const go = (next: { period?: Period; date?: string | null }) => {
    const search = new URLSearchParams(params)
    const nextPeriod = next.period ?? period
    if (nextPeriod === 'week') search.delete('period')
    else search.set('period', nextPeriod)
    const nextDate = next.date === undefined ? date : next.date
    if (nextDate) search.set('date', nextDate)
    else search.delete('date')
    setParams(search, { replace: true })
  }

  const weekStart = session?.settings.week_start ?? 1

  return (
    <div className="space-y-5 pb-2" data-testid="stats-page" data-period={period} data-loading={loading || undefined}>
      <header className="flex items-center gap-3">
        <h1 className="h1 min-w-0 flex-1 truncate">{t('stats.title')}</h1>
        <Link to="/stats/achievements" className="btn-secondary min-h-tap shrink-0 px-3 text-sm" data-testid="stats-achievements-link">
          <IconTrophy size={18} />
          <span>{t('stats.achievements')}</span>
        </Link>
      </header>

      <PeriodSwitcher
        period={period}
        range={shown}
        stale={!fresh}
        loading={loading}
        onPeriod={(next) => go({ period: next })}
        onPrev={() => fresh && go({ date: addDays(fresh.from, -1) })}
        onNext={() => fresh && go({ date: addDays(fresh.to, 1) > fresh.today ? null : addDays(fresh.to, 1) })}
        onCurrent={() => go({ date: null })}
      />

      <nav className="scroll-x" aria-label={t('stats.sections_label')}>
        {SECTIONS.map((section) => (
          <a
            key={section}
            href={`#stats-${section}`}
            className="chip min-h-tap shrink-0 whitespace-nowrap"
            onClick={(event) => {
              event.preventDefault()
              document.getElementById(`stats-${section}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }}
          >
            {t(`stats.section.${section}`)}
          </a>
        ))}
      </nav>

      <Section id="nutrition" title={t('stats.section.nutrition')}>
        <Loaded query={nutrition} section="nutrition">
          {(data, dimmed) => <NutritionSection stats={data} dimmed={dimmed} />}
        </Loaded>
      </Section>
      <Section id="activity" title={t('stats.section.activity')}>
        <Loaded query={activity} section="activity">
          {(data, dimmed) => <ActivitySection stats={data} dimmed={dimmed} />}
        </Loaded>
      </Section>
      <Section id="body" title={t('stats.section.body')}>
        <Loaded query={body} section="body">
          {(data, dimmed) => <BodySection stats={data} dimmed={dimmed} />}
        </Loaded>
      </Section>
      <Section id="planner" title={t('stats.section.planner')}>
        <Loaded query={planner} section="planner">
          {(data, dimmed) => <PlannerSection stats={data} dimmed={dimmed} />}
        </Loaded>
      </Section>
      <Section id="progress" title={t('stats.section.progress')}>
        <Loaded query={progress} section="progress">
          {(data, dimmed) => <ProgressSection stats={data} dimmed={dimmed} weekStart={weekStart} />}
        </Loaded>
      </Section>
    </div>
  )
}

function Section({ id, title, children }: { id: StatsSection; title: string; children: ReactNode }) {
  return (
    <section
      id={`stats-${id}`}
      aria-labelledby={`stats-${id}-title`}
      className="scroll-mt-32 space-y-3"
      data-testid={`stats-section-${id}`}
    >
      <h2 id={`stats-${id}-title`} className="h2">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Loaded<T>({
  query,
  section,
  children,
}: {
  query: UseQueryResult<T>
  section: StatsSection
  children: (data: T, dimmed: boolean) => ReactNode
}) {
  if (query.data !== undefined) return <>{children(query.data, query.isPlaceholderData)}</>
  if (query.isError) return <CardError onRetry={() => void query.refetch()} testId={`stats-error-${section}`} />
  return <CardSkeleton testId={`stats-skeleton-${section}`} />
}
