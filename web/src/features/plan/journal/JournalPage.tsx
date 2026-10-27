/** Stage 6 — owned by the calendar/time/journal workstream (placeholder until it lands). */
import { useTranslation } from 'react-i18next'
import { EmptyState } from '@/components/ui'

export function JournalPage() {
  const { t } = useTranslation()
  return <EmptyState title={t('plan.journal')} />
}
