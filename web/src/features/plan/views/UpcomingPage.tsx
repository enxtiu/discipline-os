/**
 * /plan/upcoming — the next 14 days, every day under its own heading, real
 * tasks plus dimmed virtual repeats, a «+» per day, and «Показать ещё» that
 * appends the following 14 days. Day keys come from the server; the client only
 * steps from one `YYYY-MM-DD` key to the next for paging.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, ErrorState, IconButton } from '@/components/ui'
import { IconPlus } from '@/components/ui/icons'
import { addDays, dayLabel } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { quickAdd } from '../events'
import { relativeDay } from '../format'
import { useCounts, useUpcoming } from '../queries'
import type { UpcomingDay } from '../types'
import { TaskListSkeleton } from '../tasks/TaskList'
import { OccurrenceRow, TaskRow } from '../tasks/TaskRow'

const PAGE_DAYS = 14

function DaySection({ day, today }: { day: UpcomingDay; today: string | undefined }) {
  const { t } = useTranslation()
  const relative = relativeDay(day.date, today)
  const full = dayLabel(day.date)
  const isToday = day.date === today
  const empty = day.tasks.length === 0 && day.occurrences.length === 0
  return (
    <section className="space-y-1.5" data-testid="upcoming-day" data-date={day.date}>
      <header className="flex items-center justify-between gap-2 pl-1">
        <h2 className={clsx('flex items-baseline gap-2 text-sm font-semibold', isToday && 'text-[rgb(var(--accent))]')}>
          <span className="inline-block first-letter:uppercase">{relative}</span>
          {relative !== full && <span className="text-xs font-normal muted">{full}</span>}
        </h2>
        <IconButton
          label={t('plan.add_to_day', { day: full })}
          icon={<IconPlus size={18} />}
          onClick={() => quickAdd({ defaults: { due_date: day.date } })}
          data-testid="upcoming-add"
        />
      </header>
      {empty ? (
        <p className="-mt-2 px-1 pb-1 text-xs muted">{t('plan.day_free')}</p>
      ) : (
        <div className="card divide-y divide-[rgb(var(--border))] overflow-hidden">
          {day.tasks.map((task) => (
            <TaskRow key={task.id} task={task} today={today} day={day.date} />
          ))}
          {day.occurrences.map((occurrence) => (
            <OccurrenceRow
              key={`${occurrence.task_id}:${occurrence.date}`}
              occurrence={occurrence}
              today={today}
              day={day.date}
            />
          ))}
        </div>
      )}
    </section>
  )
}

/** One window of days; `from` undefined = starting today (the server's today). */
function UpcomingChunk({
  from,
  today,
  isLast,
  onMore,
}: {
  from: string | undefined
  today: string | undefined
  isLast: boolean
  onMore: (nextFrom: string) => void
}) {
  const { t } = useTranslation()
  const upcoming = useUpcoming(from, PAGE_DAYS)
  if (upcoming.isLoading) return <TaskListSkeleton rows={3} />
  if (upcoming.isError || !upcoming.data) {
    return (
      <ErrorState message={errorMessage(upcoming.error)} onRetry={() => void upcoming.refetch()} retryLabel={t('app.retry')} />
    )
  }
  const data = upcoming.data
  const dayToday = today ?? (from === undefined ? data.from : undefined)
  return (
    <>
      {data.days.map((day) => (
        <DaySection key={day.date} day={day} today={dayToday} />
      ))}
      {isLast && (
        <Button variant="secondary" block onClick={() => onMore(addDays(data.to, 1))} data-testid="upcoming-more">
          {t('plan.show_more_days', { n: PAGE_DAYS })}
        </Button>
      )}
    </>
  )
}

export function UpcomingPage() {
  const today = useCounts().data?.date
  // later windows start the day after the previous one ends
  const [starts, setStarts] = useState<(string | undefined)[]>([undefined])
  return (
    <div className="space-y-4" data-testid="upcoming">
      {starts.map((from, index) => (
        <UpcomingChunk
          key={from ?? 'today'}
          from={from}
          today={today}
          isLast={index === starts.length - 1}
          onMore={(next) => setStarts((current) => (current.includes(next) ? current : [...current, next]))}
        />
      ))}
    </div>
  )
}
