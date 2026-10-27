/** Stage 6 — the task editor (owned by the views workstream; placeholder until it lands). */
import { useTranslation } from 'react-i18next'
import { Sheet } from '@/components/ui'
import type { TaskIn } from '../types'

export function TaskSheet({
  open,
  taskId,
  prefill,
  onClose,
}: {
  open: boolean
  /** Edit an existing task… */
  taskId?: string
  /** …or create a new one with these fields prefilled. */
  prefill?: Partial<TaskIn>
  onClose: () => void
}) {
  const { t } = useTranslation()
  void taskId
  void prefill
  return <Sheet open={open} onClose={onClose} title={t('plan.add_task')}>{null}</Sheet>
}
