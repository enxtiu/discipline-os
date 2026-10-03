/**
 * Honest placeholder for features that arrive in a later stage.
 * It links to the real route (so navigation and layout are already final) and
 * states the stage number instead of pretending to work.
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Badge, Card } from '@/components/ui'
import { IconChevronRight } from '@/components/ui/icons'

export function StageCard({
  stage,
  title,
  description,
  to,
  icon,
}: {
  stage: number
  title: string
  description: string
  to: string
  icon?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <Link to={to} className="block">
      <Card className="flex items-start gap-3 transition-transform active:scale-[.98]">
        <span className="mt-0.5 rounded-xl bg-violet-400/10 p-2 text-violet-300">
          {icon ?? <IconChevronRight size={18} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate font-semibold">{title}</span>
            <Badge tone="violet">{t('today.stage_badge', { stage })}</Badge>
          </span>
          <span className="mt-1 block text-xs leading-relaxed muted">{description}</span>
        </span>
        <IconChevronRight size={18} className="mt-1 shrink-0 text-[rgb(var(--text-muted))]" />
      </Card>
    </Link>
  )
}

/** Full-page variant used by the placeholder routes. */
export function StagePage({
  stage,
  title,
  description,
  bullets,
}: {
  stage: number
  title: string
  description: string
  bullets: string[]
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between gap-3">
        <h1 className="h1">{title}</h1>
        <Badge tone="violet">{t('today.stage_badge', { stage })}</Badge>
      </header>
      <Card className="space-y-3">
        <p className="text-sm leading-relaxed muted">{description}</p>
        <ul className="space-y-2 text-sm">
          {bullets.map((bullet) => (
            <li key={bullet} className="flex gap-2.5">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-aqua-400" />
              <span className="leading-relaxed">{bullet}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs muted">{t('today.coming_soon', { stage })}</p>
      </Card>
      <Card className="space-y-2">
        <p className="label">{t('app.name')}</p>
        <p className="text-xs muted">
          {t('today.burned_hint')}
        </p>
      </Card>
    </div>
  )
}
