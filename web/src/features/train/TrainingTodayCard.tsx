/** Today's training on the home screen: continue, start the planned workout, or start one. */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Button, Card, Skeleton } from '@/components/ui'
import { IconTrain } from '@/components/ui/icons'
import type { TrainingToday } from '@/api/types'
import { number } from '@/lib/format'
import { clock } from './format'
import { useStart } from './useStart'

export function TrainingTodayCard({ data, loading }: { data: TrainingToday | undefined; loading: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const start = useStart()
  const [now, setNow] = useState(() => Date.now())
  const active = data?.active
  useEffect(() => {
    if (!active) return
    const handle = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(handle)
  }, [active])

  if (loading && !data) return <Skeleton className="h-32 rounded-card" />
  const plan = data?.plan
  const done = data?.done?.length ?? 0
  const planDone = !!plan?.template && (data?.done ?? []).some((workout) => workout.template_id === plan.template?.id)

  return (
    <Card className="space-y-3" data-testid="today-training">
      <p className="flex items-center gap-2 text-sm font-semibold">
        <IconTrain size={16} className="text-[rgb(var(--accent))]" />
        {t('train.title')}
      </p>
      {active ? (
        <>
          <p className="text-sm">
            {active.title ?? t('train.workout_default_title')}{' '}
            <span className="tnum text-[rgb(var(--accent))]">{clock((now - new Date(active.started_at).getTime()) / 1000)}</span>
          </p>
          <Button block onClick={() => navigate(`/train/workout/${active.id}`)}>
            {t('train.continue')}
          </Button>
        </>
      ) : plan?.template && !planDone ? (
        <>
          <p className="text-sm">{t('train.planned_today', { name: plan.template.name })}</p>
          <Button block loading={start.pending} onClick={() => start.run(plan.template?.id)}>
            {t('train.start_planned')}
          </Button>
        </>
      ) : (
        <>
          <p className="text-sm muted">
            {planDone
              ? t('train.plan_done', { name: plan?.template?.name })
              : plan?.is_rest_day
                ? t('train.rest_day')
                : done
                  ? t('train.done_today_short')
                  : t('train.nothing_planned')}
          </p>
          <Button block variant="secondary" onClick={() => navigate('/train?action=start')}>
            {t('train.start')}
          </Button>
        </>
      )}
      {done > 0 && (
        <p className="text-xs muted">{t('train.done_today', { n: done, kcal: number(data?.burned_kcal ?? 0) })}</p>
      )}
    </Card>
  )
}
