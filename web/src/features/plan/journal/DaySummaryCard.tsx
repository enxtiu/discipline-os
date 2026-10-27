/**
 * The server's digest of the day for the evening review (`DaySummaryOut`):
 * tasks done and still due, tracked time by area, food against the target,
 * workouts with their burned calories, steps and sleep. Burned calories have
 * their own tile and are never added to (or subtracted from) the food budget.
 */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Card, ProgressBar } from '@/components/ui'
import { IconCheck, IconFlame } from '@/components/ui/icons'
import { kcal, number } from '@/lib/format'
import { formatMinutes } from '../format'
import type { DaySummary } from '../types'

function Tile({
  label,
  value,
  hint,
  icon,
  children,
  testId,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  children?: ReactNode
  testId?: string
}) {
  return (
    <div className="surface-sunken min-w-0 space-y-1 px-3 py-2.5" data-testid={testId}>
      <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide muted">
        {icon}
        <span className="truncate">{label}</span>
      </p>
      <p className="tnum truncate text-lg font-semibold" data-testid={testId ? `${testId}-value` : undefined}>
        {value}
      </p>
      {hint && <p className="truncate text-xs muted">{hint}</p>}
      {children}
    </div>
  )
}

export function DaySummaryCard({ day }: { day: DaySummary }) {
  const { t } = useTranslation()
  const titles = day.tasks_done_titles
  const moreDone = Math.max(0, day.tasks_done - titles.length)

  return (
    <Card className="space-y-4" data-testid="journal-summary">
      <h2 className="h2">{t('journal.summary_title')}</h2>

      <div className="grid grid-cols-2 gap-3">
        <Tile
          label={t('journal.tasks')}
          value={t('journal.tasks_done', { n: day.tasks_done })}
          hint={day.tasks_open_due > 0 ? t('journal.tasks_open_due', { n: day.tasks_open_due }) : t('journal.tasks_clear')}
          testId="journal-tasks"
        />
        <Tile label={t('journal.time')} value={formatMinutes(day.time_minutes)} testId="journal-time" />
        <Tile
          label={t('journal.food')}
          value={kcal(day.kcal)}
          hint={day.target_kcal ? t('journal.food_of_target', { target: kcal(day.target_kcal) }) : t('journal.food_no_target')}
          testId="journal-food"
        >
          {day.target_kcal ? (
            <ProgressBar
              value={day.kcal}
              max={day.target_kcal}
              tone={day.kcal > day.target_kcal ? 'warn' : 'accent'}
              className="mt-1"
            />
          ) : null}
        </Tile>
        <Tile
          label={t('journal.burned')}
          icon={<IconFlame size={13} className="text-amber-400" />}
          value={day.workout_kcal != null ? kcal(day.workout_kcal) : '—'}
          hint={t('journal.workouts', { n: day.workouts })}
          testId="journal-burned"
        />
        <Tile
          label={t('journal.steps')}
          value={day.steps != null ? number(day.steps) : '—'}
          testId="journal-steps"
        />
        <Tile
          label={t('journal.sleep')}
          value={day.sleep_minutes != null ? formatMinutes(day.sleep_minutes) : '—'}
          testId="journal-sleep"
        />
      </div>
      <p className="text-xs muted">{t('journal.burned_note')}</p>

      {titles.length > 0 && (
        <div className="space-y-1.5">
          <p className="label">{t('journal.done_titles')}</p>
          <ul className="space-y-1 text-sm">
            {titles.map((title, index) => (
              <li key={`${index}:${title}`} className="flex items-start gap-2">
                <IconCheck size={16} className="mt-0.5 shrink-0 text-[rgb(var(--accent))]" />
                <span className="min-w-0 break-words">{title}</span>
              </li>
            ))}
          </ul>
          {moreDone > 0 && <p className="text-xs muted">{t('journal.and_more', { n: moreDone })}</p>}
        </div>
      )}

      {day.time_by_area.length > 0 && (
        <div className="space-y-1.5">
          <p className="label">{t('journal.time_by_area')}</p>
          <ul className="space-y-1.5 text-sm">
            {day.time_by_area.map((row) => (
              <li key={row.area?.id ?? 'none'} className="flex items-center justify-between gap-3">
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    className={clsx('block h-2.5 w-2.5 shrink-0 rounded-full', !row.area?.color && 'bg-[rgb(var(--border))]')}
                    style={row.area?.color ? { backgroundColor: row.area.color } : undefined}
                    aria-hidden="true"
                  />
                  <span className="truncate">{row.area?.name ?? t('journal.no_area')}</span>
                </span>
                <span className="tnum shrink-0 muted">{formatMinutes(row.minutes)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}
