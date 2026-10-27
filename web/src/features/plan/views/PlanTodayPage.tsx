/** Stage 6 — owned by the views workstream (placeholder until it lands). */
import { useTranslation } from 'react-i18next'
import { EmptyState } from '@/components/ui'

export function PlanTodayPage() {
  const { t } = useTranslation()
  return <EmptyState title={t('plan.today')} />
}
