/** Stage placeholders for Train / Plan / Stats route targets (Food is real since stage 2). */
import { useTranslation } from 'react-i18next'
import { StagePage } from './StageCard'

export function TrainPage() {
  const { t } = useTranslation()
  return (
    <StagePage
      stage={4}
      title={t('train.title')}
      description={t('train.subtitle_planned')}
      bullets={[t('train.start'), t('today.burned_card'), '1RM · PR · volume', 'templates + weekly plan']}
    />
  )
}

export function PlanPage() {
  const { t } = useTranslation()
  return (
    <StagePage
      stage={6}
      title={t('plan.title')}
      description={t('plan.subtitle_planned')}
      bullets={[t('plan.inbox'), t('plan.calendar'), t('plan.projects'), 'time tracker · journal']}
    />
  )
}

export function StatsPage() {
  const { t } = useTranslation()
  return (
    <StagePage
      stage={8}
      title={t('stats.title')}
      description={t('stats.subtitle_planned')}
      bullets={[t('stats.day'), t('stats.week'), t('stats.month'), 'streaks · PRs · time by area']}
    />
  )
}
