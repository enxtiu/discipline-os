/**
 * The one calm insight on Сегодня (stage 10, the unified «Сегодня», every theme):
 * `GET /insights/today` — at most one observation, chosen and computed by the server
 * (protein, fibre, water or steps above the 4-week average; never calories, never
 * "less", never for a day whose food stayed below the safe floor). The client only picks
 * the sentence for `metric` and `day`; when there is none, nothing is shown.
 * `sk-insight*` are hooks for the themes.
 */
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { endpoints } from '@/api/client'
import type { Insight } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'

const SENTENCE: Record<Insight['metric'], Record<Insight['day'], string>> = {
  protein: { today: 'today.insight_protein', yesterday: 'today.insight_protein_yesterday' },
  fiber: { today: 'today.insight_fiber', yesterday: 'today.insight_fiber_yesterday' },
  water: { today: 'today.insight_water', yesterday: 'today.insight_water_yesterday' },
  steps: { today: 'today.insight_steps', yesterday: 'today.insight_steps_yesterday' },
}

export function TodayInsight() {
  const { t } = useTranslation()
  const query = useQuery({ queryKey: queryKeys.insight, queryFn: () => endpoints.todayInsight() })
  const insight = query.data?.insight
  if (!insight) return null
  return (
    <section
      className="card sk-insight flex items-center gap-3 px-3.5 py-3"
      aria-label={t('today.insight_title')}
      data-testid="today-insight"
      data-metric={insight.metric}
      data-day={insight.day}
    >
      <span
        aria-hidden="true"
        className="sk-insight-badge sk-tilt-l flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[rgb(var(--accent-2))]/15 text-[rgb(var(--accent-2))]"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" focusable="false">
          <path d="M12 3.5l1.8 5.4c.3.9 1 1.6 1.9 1.9l5.3 1.7-5.3 1.7c-.9.3-1.6 1-1.9 1.9L12 21.5l-1.8-5.4c-.3-.9-1-1.6-1.9-1.9L3 12.5l5.3-1.7c.9-.3 1.6-1 1.9-1.9z" />
        </svg>
      </span>
      <p className="text-sm font-medium leading-snug">{t(SENTENCE[insight.metric][insight.day])}</p>
    </section>
  )
}
