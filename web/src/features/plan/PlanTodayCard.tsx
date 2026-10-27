/** Stage 6 — the planner card on Today (owned by the views workstream; placeholder until it lands). */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Card } from '@/components/ui'

export function PlanTodayCard() {
  const { t } = useTranslation()
  return (
    <Link to="/plan" className="block">
      <Card>
        <span className="font-semibold">{t('plan.title')}</span>
      </Card>
    </Link>
  )
}
