/** Stage 6 — natural-language quick add (owned by the views workstream; placeholder until it lands). */
import { useTranslation } from 'react-i18next'
import { Sheet } from '@/components/ui'
import type { QuickAddRequest } from '../events'

export function QuickAddTaskSheet({
  open,
  request,
  onClose,
}: {
  open: boolean
  request: QuickAddRequest
  onClose: () => void
}) {
  const { t } = useTranslation()
  void request
  return <Sheet open={open} onClose={onClose} title={t('plan.add_task')}>{null}</Sheet>
}
