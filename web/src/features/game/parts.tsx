/** Small presentational pieces shared by the Today card, the sheets and the achievements screen. */
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'

import { GameIcon } from './icons'
import { RARITY_STYLES } from './format'
import type { Achievement, Rarity } from './types'

const GLOW: Record<Rarity, string> = {
  common: '',
  rare: 'glow-accent',
  epic: 'glow-violet',
  legendary: 'shadow-glow-amber',
}

/** Round badge: rarity colours once unlocked; dashed and muted while locked; a lock for masked secrets. */
export function AchievementBadge({
  item,
  size = 56,
  glow = false,
  className,
}: {
  item: Pick<Achievement, 'rarity' | 'icon' | 'unlocked' | 'masked'>
  size?: number
  glow?: boolean
  className?: string
}) {
  const style = RARITY_STYLES[item.rarity]
  const locked = !item.unlocked
  return (
    <span
      className={clsx(
        'relative inline-flex shrink-0 items-center justify-center rounded-full border-2',
        locked
          ? 'border-dashed border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))] text-[rgb(var(--text-muted))]'
          : [style.border, style.bg, style.text],
        glow && !locked && GLOW[item.rarity],
        className,
      )}
      style={{ width: size, height: size }}
      data-rarity={item.rarity}
      aria-hidden="true"
    >
      <GameIcon icon={item.masked ? 'lock' : item.icon} size={Math.round(size * 0.46)} className={locked ? 'opacity-70' : undefined} />
    </span>
  )
}

export function RarityPill({ rarity, className }: { rarity: Rarity; className?: string }) {
  const { t } = useTranslation()
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-pill border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide',
        RARITY_STYLES[rarity].pill,
        className,
      )}
    >
      {t(`game.rarity.${rarity}`)}
    </span>
  )
}

/** «предварительно» — today's score while the day is still running. */
export function ProvisionalPill({ className }: { className?: string }) {
  const { t } = useTranslation()
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-pill border border-[rgb(var(--border))] px-2 py-0.5 text-[11px] font-medium text-[rgb(var(--text-muted))]',
        className,
      )}
      data-testid="score-provisional"
    >
      {t('game.provisional')}
    </span>
  )
}
