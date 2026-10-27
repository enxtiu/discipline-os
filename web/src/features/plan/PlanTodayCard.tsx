/**
 * «План на сегодня» on Today: overdue / today / done counts (the server's
 * buckets), up to four open tasks with working checkboxes, «+ Задача» and a
 * link to /plan. It lives in the main chunk, so it only imports the light
 * task row — the editor and quick add load lazily through `TaskSheetHost`.
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, Card, Skeleton } from '@/components/ui'
import { IconChevronRight, IconPlan, IconPlus } from '@/components/ui/icons'
import { errorMessage } from '@/lib/errors'
import { quickAdd } from './events'
import { usePlannerQueueRefresh, useTodayView } from './queries'
import type { Task } from './types'
import { TaskRow } from './tasks/TaskRow'

const SHOWN = 4

function Stat({ label, value, danger }: { label: string; value: number; danger?: boolean }) {
  return (
    <div className="surface-sunken rounded-2xl px-2 py-2">
      <p className={clsx('tnum text-lg font-semibold leading-tight', danger && 'text-rose-400')}>{value}</p>
      <p className="text-[11px] muted">{label}</p>
    </div>
  )
}

export function PlanTodayCard() {
  const { t } = useTranslation()
  const view = useTodayView()
  // ticks made offline on this card refresh it once the queue has sent them
  usePlannerQueueRefresh()
  const data = view.data
  const byStart = (a: Task, b: Task) => Date.parse(a.block?.start_at ?? '') - Date.parse(b.block?.start_at ?? '')
  const open = data ? [...data.overdue, ...[...data.scheduled].sort(byStart), ...data.today] : []
  const shown = open.slice(0, SHOWN)

  return (
    <Card className="space-y-3" data-testid="today-plan">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <IconPlan size={16} className="text-[rgb(var(--accent))]" />
          {t('plan.card_title')}
        </p>
        <Link to="/plan" className="-my-3 -mr-2 flex min-h-tap items-center gap-0.5 px-2 text-xs muted">
          {t('plan.card_open')}
          <IconChevronRight size={14} />
        </Link>
      </div>

      {view.isLoading && <Skeleton className="h-28" />}
      {view.isError && (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm muted">{errorMessage(view.error)}</p>
          <Button variant="secondary" size="sm" className="min-h-tap" onClick={() => void view.refetch()}>
            {t('app.retry')}
          </Button>
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-3 gap-2 text-center" data-testid="today-plan-counts">
            <Stat label={t('plan.card_overdue')} value={data.counts.overdue} danger={data.counts.overdue > 0} />
            <Stat label={t('plan.card_today')} value={data.counts.today} />
            <Stat label={t('plan.card_done')} value={data.done.length} />
          </div>
          {shown.length > 0 ? (
            <div className="-mx-4 divide-y divide-[rgb(var(--border))] border-y border-[rgb(var(--border))]">
              {shown.map((task) => (
                <TaskRow key={task.id} task={task} today={data.date} day={data.date} />
              ))}
            </div>
          ) : (
            <p className="text-sm muted">{data.done.length > 0 ? t('plan.card_all_done') : t('plan.card_empty')}</p>
          )}
          <div className="flex items-center justify-between gap-3">
            <Button
              variant="secondary"
              size="sm"
              className="min-h-tap"
              icon={<IconPlus size={16} />}
              onClick={() => quickAdd()}
              data-testid="today-plan-add"
            >
              {t('plan.card_add')}
            </Button>
            {open.length > SHOWN && (
              <Link to="/plan" className="-mr-2 flex min-h-tap items-center gap-0.5 px-2 text-xs muted">
                {t('plan.card_more', { n: open.length - SHOWN })}
                <IconChevronRight size={14} />
              </Link>
            )}
          </div>
        </>
      )}
    </Card>
  )
}
