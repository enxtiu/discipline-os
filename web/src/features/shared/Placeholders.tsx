/** Stage placeholders for Plan / Stats route targets (Food since stage 2, Train since stage 4). */
import { useTranslation } from 'react-i18next'
import { StagePage } from './StageCard'

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
