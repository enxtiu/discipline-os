/** Stage placeholder for the Stats route (Food since stage 2, Train since stage 4, Plan since stage 6). */
import { useTranslation } from 'react-i18next'
import { StagePage } from './StageCard'

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
