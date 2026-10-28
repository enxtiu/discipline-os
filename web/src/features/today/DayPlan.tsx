/**
 * «План дня» on Сегодня (stage 10, the unified «Сегодня», every theme): the day as one
 * timeline — the meals of the food diary with their time and kcal (done), today's planner
 * tasks that have a time (done, open, the next one emphasised as «Далее») and the main
 * meals not logged yet («Ужин — пока не записан» + «Добавить», which opens the search of
 * Питание for that meal). The rows come from ./dayPlan.ts — server data only.
 *
 * The reads are the ones Today already makes (the day, the planner's today view), so the
 * block costs no request of its own. Rows are text (time, name, meta) with the state for
 * screen readers in words; the dots and lines are drawn and hidden from them. `sk-plan*`
 * and `data-state` are hooks for the themes (styles/skins/*.css).
 */
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'

import { Button, Card, Skeleton } from '@/components/ui'
import { IconCheck, IconPlus } from '@/components/ui/icons'
import type { MealType } from '@/api/types'
import { useAuth } from '@/features/auth/AuthProvider'
import { useDayView } from '@/features/food/useNutrition'
import { formatMinutes } from '@/features/plan/format'
import { useTodayView } from '@/features/plan/queries'
import { number, time } from '@/lib/format'
import { buildDayPlan, lineStyle, type PlanRow, type PlanState } from './dayPlan'

const ADD_LABEL: Record<MealType, string> = {
  breakfast: 'today.plan_add_breakfast',
  lunch: 'today.plan_add_lunch',
  dinner: 'today.plan_add_dinner',
  snack: 'today.plan_add',
  other: 'today.plan_add',
}

/** The dot of each state (literal classes: Tailwind purges built ones). */
const DOT: Record<PlanState, string> = {
  done: 'bg-[rgb(var(--accent))] text-[rgb(var(--on-accent))]',
  next: 'border-2 border-[rgb(var(--accent))] bg-[rgb(var(--bg-elevated))] text-[rgb(var(--accent))]',
  open: 'border-2 border-[rgb(var(--text-muted))]/60 bg-[rgb(var(--bg-elevated))]',
  missing: 'border-2 border-dashed border-[rgb(var(--text-muted))]/60 bg-[rgb(var(--bg-elevated))]',
}

/** Re-renders once a minute, so «Далее» moves on while the screen stays open. */
function useMinute(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

export function DayPlan() {
  const { t } = useTranslation()
  const titleId = useId()
  const day = useDayView()
  const plan = useTodayView()
  const now = useMinute()

  if (!day.data && day.isError) return null // the meal list above says what went wrong
  const loading = day.isLoading || (plan.isLoading && !plan.data)
  const rows = loading ? [] : buildDayPlan({ meals: day.data?.meals ?? [], pendingIds: day.pendingIds, plan: plan.data, now })

  return (
    <section className="space-y-2" aria-labelledby={titleId} data-testid="day-plan">
      <h2 id={titleId} className="h2">
        {t('today.plan_title')}
      </h2>
      {loading ? (
        <Skeleton className="h-44 rounded-card" />
      ) : (
        <Card padded={false} className="sk-plan px-3 py-1.5">
          <ol aria-labelledby={titleId}>
            {rows.map((row, index) => (
              <PlanItem
                key={row.key}
                row={row}
                above={index > 0 ? lineStyle(rows, index) : null}
                below={index < rows.length - 1 ? lineStyle(rows, index + 1) : null}
              />
            ))}
          </ol>
        </Card>
      )}
    </section>
  )
}

function PlanItem({ row, above, below }: { row: PlanRow; above: 'solid' | 'dashed' | null; below: 'solid' | 'dashed' | null }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { session } = useAuth()
  const tall = row.state === 'next' || row.kind === 'missing'
  const clock = row.kind === 'meal' ? time(row.loggedAt, session?.settings.timezone) : row.kind === 'task' ? row.time : ''

  return (
    <li
      className={clsx(
        'sk-plan-row relative flex items-center gap-2.5',
        row.state === 'next'
          ? 'z-[1] -mx-2 my-1 min-h-[3.5rem] rounded-2xl bg-[rgb(var(--accent))]/10 px-2 ring-1 ring-inset ring-[rgb(var(--accent))]/35'
          : tall
            ? 'min-h-[3.5rem]'
            : 'min-h-[2.25rem]',
      )}
      data-state={row.state}
      data-kind={row.kind}
      data-testid="plan-row"
    >
      <span className="sk-plan-time tnum w-11 shrink-0 text-sm font-semibold" data-testid="plan-time">
        {clock}
      </span>
      {/* the timeline: the line through the dots (solid so far, dashed ahead), drawn only */}
      <span aria-hidden="true" className="relative flex w-6 shrink-0 items-center justify-center self-stretch">
        {above && (
          <span
            className={clsx(
              'sk-plan-line absolute -top-1 bottom-1/2 left-1/2 -translate-x-1/2 border-l-2 border-[rgb(var(--text-muted))]/40',
              above === 'dashed' && 'border-dashed',
            )}
          />
        )}
        {below && (
          <span
            className={clsx(
              'sk-plan-line absolute -bottom-1 top-1/2 left-1/2 -translate-x-1/2 border-l-2 border-[rgb(var(--text-muted))]/40',
              below === 'dashed' && 'border-dashed',
            )}
          />
        )}
        <span className={clsx('sk-plan-dot relative flex h-5 w-5 items-center justify-center rounded-full', DOT[row.state])}>
          {row.state === 'done' && <IconCheck size={12} strokeWidth={3.2} />}
          {row.state === 'next' && (
            <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor" focusable="false">
              <path d="M7 4.5v15l12.5-7.5z" />
            </svg>
          )}
        </span>
      </span>
      <PlanText row={row} />
      {row.state === 'next' && (
        <span className="sk-plan-next sk-tilt-r shrink-0 rounded-lg bg-[rgb(var(--accent))] px-2 py-0.5 text-xs font-semibold text-[rgb(var(--on-accent))]">
          {t('today.plan_next')}
        </span>
      )}
      {row.kind === 'missing' && (
        <Button
          size="sm"
          variant="secondary"
          className="shrink-0"
          icon={<IconPlus size={16} />}
          aria-label={t(ADD_LABEL[row.meal])}
          onClick={() => navigate(`/food?action=search&meal=${row.meal}`)}
          data-testid="plan-add"
          data-meal={row.meal}
        >
          {t('today.plan_add')}
        </Button>
      )}
    </li>
  )
}

function PlanText({ row }: { row: PlanRow }) {
  const { t } = useTranslation()
  if (row.kind === 'missing') {
    return (
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-[15px] font-medium leading-tight">{t(`meal.${row.meal}`)}</span>
        <span className="text-[13px] leading-tight muted">{t('today.plan_missing')}</span>
      </span>
    )
  }
  if (row.kind === 'meal') {
    return (
      <span className="min-w-0 flex-1 truncate text-[15px] leading-tight">
        <span className="font-medium">{t(`meal.${row.meal}`)}</span>
        <span className="tnum muted">
          {' · '}
          {number(row.kcal)} {t('common.kcal')}
        </span>
        <span className="sr-only">, {t('today.plan_logged')}</span>
        {row.pending && <span className="ml-1.5 text-[11px] text-violet-300">{t('sync.pending_mark')}</span>}
      </span>
    )
  }
  const minutes = row.minutes ? formatMinutes(row.minutes) : null
  if (row.state === 'next') {
    return (
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[15px] font-semibold leading-tight">{row.title}</span>
        {minutes && <span className="tnum text-[13px] leading-tight muted">{minutes}</span>}
      </span>
    )
  }
  return (
    <span className="flex min-w-0 flex-1 items-baseline text-[15px] leading-tight">
      <span className="truncate font-medium">{row.title}</span>
      {minutes && <span className="tnum shrink-0 whitespace-pre muted">{` · ${minutes}`}</span>}
      {row.state === 'done' && <span className="sr-only">, {t('a11y.done')}</span>}
    </span>
  )
}
