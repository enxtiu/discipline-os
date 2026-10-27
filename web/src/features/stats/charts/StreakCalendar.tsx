/**
 * The overall-streak calendar: which days counted (daily score ≥ 60 with
 * credit) and which a freeze token saved — as a month calendar for a week or
 * a month, as a week-column grid for a quarter or a year. Tap (or drag across)
 * a day to read it below the grid; colour is never the only signal: freeze
 * days carry a snowflake where the cells are large enough, and the readout and
 * the legend say it in words.
 */
import { useMemo, useState, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { IconSnowflake } from '@/features/game/icons'
import { addDays } from '@/lib/format'
import { monthShort, shortDay, weekday, weekdayShort } from '../format'
import type { Period, StreakDay } from '../types'

type CellState = 'overall' | 'freeze' | 'missed' | 'future'

function stateOf(day: StreakDay, today: string): CellState {
  if (day.freeze) return 'freeze'
  if (day.overall) return 'overall'
  return day.date > today ? 'future' : 'missed'
}

const CELL: Record<CellState, string> = {
  overall: 'bg-[rgb(var(--accent))] text-ink-950',
  freeze: 'bg-[rgb(var(--chart-sleep))] text-ink-950 [[data-theme=light]_&]:text-white',
  missed: 'bg-[rgb(var(--bg-sunken))] text-[rgb(var(--text-muted))] border border-[rgb(var(--border))]',
  future: 'border border-dashed border-[rgb(var(--border))] text-[rgb(var(--text-muted))]/60',
}

export function StreakCalendar({
  days,
  period,
  today,
  weekStart,
  summary,
}: {
  days: StreakDay[]
  period: Period
  today: string
  /** 0 = Sunday … 6 = Saturday (the user's setting, as the server's weeks) */
  weekStart: number
  summary: string
}) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<string | null>(null)
  const byDate = useMemo(() => new Map(days.map((day) => [day.date, day])), [days])
  const compactGrid = period === 'quarter' || period === 'year'

  // weeks of 7 slots starting on the user's week start; slots outside the period stay empty
  const weeks = useMemo(() => {
    if (!days.length) return [] as (StreakDay | null)[][]
    const first = days[0].date
    const lead = (weekday(first) - weekStart + 7) % 7
    let cursor = addDays(first, -lead)
    const last = days[days.length - 1].date
    const out: (StreakDay | null)[][] = []
    while (cursor <= last) {
      const week: (StreakDay | null)[] = []
      for (let index = 0; index < 7; index += 1) {
        week.push(byDate.get(cursor) ?? null)
        cursor = addDays(cursor, 1)
      }
      out.push(week)
    }
    return out
  }, [days, byDate, weekStart])

  const pick = (event: PointerEvent<HTMLDivElement>) => {
    const element = document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null
    const date = element?.closest<HTMLElement>('[data-date]')?.dataset.date
    if (date) setSelected(date)
  }

  const header = weeks[0]?.map((_, index) => {
    const anyDay = weeks.flat().find((day, slot) => day && slot % 7 === index)
    return anyDay ? weekdayShort(anyDay.date) : ''
  })

  const chosen = selected ? byDate.get(selected) ?? null : null
  const readout = chosen
    ? `${shortDay(chosen.date)} — ${t(`stats.progress.cell.${stateOf(chosen, today)}`)}`
    : t('stats.progress.calendar_hint')

  return (
    <div className="space-y-2" data-testid="streak-calendar">
      <div
        role="img"
        aria-label={summary}
        className="touch-pan-y select-none"
        onPointerDown={pick}
        onPointerMove={(event) => event.buttons > 0 && pick(event)}
      >
        {compactGrid ? (
          <div className="flex gap-[2px]" aria-hidden="true">
            {weeks.map((week, column) => {
              const firstOfMonth = week.find((day) => day?.date.endsWith('-01'))
              return (
                <div key={column} className="flex min-w-0 flex-1 flex-col gap-[2px]">
                  <span className="h-3 overflow-visible whitespace-nowrap text-[9px] leading-3 muted">
                    {firstOfMonth ? monthShort(firstOfMonth.date) : ''}
                  </span>
                  {week.map((day, row) =>
                    day ? (
                      <span
                        key={row}
                        data-date={day.date}
                        data-state={stateOf(day, today)}
                        className={clsx(
                          'aspect-square w-full rounded-[2px]',
                          CELL[stateOf(day, today)],
                          day.date === today && 'ring-1 ring-[rgb(var(--text))]',
                          day.date === selected && 'ring-2 ring-[rgb(var(--text))]',
                        )}
                      />
                    ) : (
                      <span key={row} className="aspect-square w-full" />
                    ),
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="space-y-1" aria-hidden="true">
            <div className="grid grid-cols-7 gap-1 text-center text-[11px] muted">
              {header?.map((label, index) => <span key={index}>{label}</span>)}
            </div>
            {weeks.map((week, row) => (
              <div key={row} className="grid grid-cols-7 gap-1">
                {week.map((day, column) =>
                  day ? (
                    <span
                      key={column}
                      data-date={day.date}
                      data-state={stateOf(day, today)}
                      className={clsx(
                        'tnum flex h-9 items-center justify-center rounded-lg text-xs font-semibold',
                        CELL[stateOf(day, today)],
                        day.date === today && 'ring-2 ring-[rgb(var(--text))]/70',
                        day.date === selected && 'ring-2 ring-[rgb(var(--text))]',
                      )}
                    >
                      {day.freeze ? <IconSnowflake size={14} /> : Number(day.date.slice(8, 10))}
                    </span>
                  ) : (
                    <span key={column} className="h-9" />
                  ),
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      <p className="min-h-[1.25rem] text-xs muted tnum" aria-live="polite" data-testid="calendar-readout">
        {readout}
      </p>
    </div>
  )
}
