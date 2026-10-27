/**
 * Achievement celebrations: unseen unlocked achievements (`celebrate` in
 * `GET /gamification/today`) → a celebration sheet (badge, name, description,
 * +XP, rarity), then `POST /achievements/seen` when it is closed — once seen,
 * never shown again.
 *
 * The sheet opens by itself where it cannot get in the way: on the
 * achievements screen right away, on Today once the score has been on screen
 * for a moment (the card also shows a pill that opens it any time). Each
 * achievement is offered automatically once per app session; reduced motion
 * turns the badge animation and the sheet's slide off.
 */
import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { m, MotionConfig } from 'framer-motion'

import { Button, Sheet } from '@/components/ui'
import { IconChevronRight } from '@/components/ui/icons'
import { achievementDescription, achievementName, RARITY_ORDER } from './format'
import { IconTrophy } from './icons'
import { AchievementBadge, RarityPill } from './parts'
import { useMarkSeen } from './queries'
import type { Achievement } from './types'

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

/**
 * The live reduced-motion preference. framer-motion's `useReducedMotion` keeps
 * the value from the first render, and this sheet is mounted long before it
 * opens — a setting changed in between must still count.
 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(REDUCED_MOTION).matches)
  useEffect(() => {
    const media = window.matchMedia?.(REDUCED_MOTION)
    if (!media) return
    const update = () => setReduced(media.matches)
    update()
    media.addEventListener?.('change', update)
    return () => media.removeEventListener?.('change', update)
  }, [])
  return reduced
}

/** The halo behind the hero badge takes the rarity's colour (literal classes: Tailwind purges built ones). */
const HALO: Record<Achievement['rarity'], string> = {
  common: 'bg-[radial-gradient(closest-side,rgb(var(--text-muted)/0.35)_55%,transparent)]',
  rare: 'bg-[radial-gradient(closest-side,rgb(var(--accent)/0.4)_55%,transparent)]',
  epic: 'bg-[radial-gradient(closest-side,rgb(var(--accent-2)/0.5)_55%,transparent)]',
  legendary: 'bg-[radial-gradient(closest-side,rgb(var(--warn)/0.45)_55%,transparent)]',
}
const RING: Record<Achievement['rarity'], string> = {
  common: 'border-[rgb(var(--text-muted))]/40',
  rare: 'border-[rgb(var(--accent))]/50',
  epic: 'border-[rgb(var(--accent-2))]/50',
  legendary: 'border-[rgb(var(--warn))]/50',
}

/** Ids offered automatically in this app session (a reload offers the unseen ones again). */
const offered = new Set<string>()

export function useCelebration(items: Achievement[] | undefined, auto: boolean) {
  const [showing, setShowing] = useState<Achievement[] | null>(null)
  const markSeen = useMarkSeen()

  useEffect(() => {
    if (!auto || showing || !items?.length) return
    if (items.every((item) => offered.has(item.id))) return
    // never on top of another sheet (a log sheet, the quick add …): the pill still offers it
    if (document.querySelector('[role="dialog"]')) return
    items.forEach((item) => offered.add(item.id))
    setShowing(items)
  }, [auto, items, showing])

  return {
    pending: items ?? [],
    showing,
    open: () => {
      if (!items?.length) return
      items.forEach((item) => offered.add(item.id))
      setShowing(items)
    },
    close: () => {
      if (showing?.length) markSeen.mutate(showing.map((item) => item.id))
      setShowing(null)
    },
  }
}

/** The rarest, then the newest, leads the sheet. */
function hero(items: Achievement[]): Achievement {
  return [...items].sort(
    (a, b) =>
      RARITY_ORDER.indexOf(b.rarity) - RARITY_ORDER.indexOf(a.rarity) ||
      (b.unlocked_at ?? '').localeCompare(a.unlocked_at ?? ''),
  )[0]
}

export function CelebrationSheet({ items, onClose }: { items: Achievement[] | null; onClose: () => void }) {
  const { t } = useTranslation()
  const reduced = usePrefersReducedMotion()
  const lead = items?.length ? hero(items) : null
  const rest = lead ? (items ?? []).filter((item) => item.id !== lead.id) : []
  const title = (items?.length ?? 0) > 1 ? t('game.celebrate.title_many', { count: items?.length }) : t('game.celebrate.title')

  return (
    <MotionConfig reducedMotion={reduced ? 'always' : 'never'}>
      <Sheet
        open={!!lead}
        onClose={onClose}
        title={title}
        footer={
          <Button block onClick={onClose} data-testid="celebration-ok">
            {t('game.celebrate.ok')}
          </Button>
        }
      >
        {lead && (
          <div className="space-y-4 pb-2">
            <div
              className="flex flex-col items-center gap-2.5 pt-2 text-center"
              data-testid="celebration"
              data-motion={reduced ? 'reduced' : 'full'}
              data-code={lead.code}
            >
              <div className="relative flex h-36 w-36 items-center justify-center">
                <span className={clsx('absolute inset-0 rounded-full', HALO[lead.rarity])} aria-hidden="true" />
                {!reduced && (
                  <m.span
                    className={clsx('absolute inset-3 rounded-full border-2', RING[lead.rarity])}
                    initial={{ scale: 0.8, opacity: 0.8 }}
                    animate={{ scale: 1.35, opacity: 0 }}
                    transition={{ duration: 1.4, repeat: 2, ease: 'easeOut', delay: 0.3 }}
                  />
                )}
                <m.div
                  initial={reduced ? false : { scale: 0.4, rotate: -14, opacity: 0 }}
                  animate={{ scale: 1, rotate: 0, opacity: 1 }}
                  transition={{ type: 'spring', stiffness: 260, damping: 13, delay: 0.12 }}
                >
                  {/* an opaque base: the badge's tinted fill must not take the halo's colour */}
                  <span className="block rounded-full bg-[rgb(var(--bg-elevated))]">
                    <AchievementBadge item={lead} size={96} glow />
                  </span>
                </m.div>
              </div>
              <RarityPill rarity={lead.rarity} />
              <h3 className="text-xl font-semibold leading-tight">{achievementName(lead)}</h3>
              {achievementDescription(lead) && (
                <p className="max-w-xs text-sm leading-relaxed muted">{achievementDescription(lead)}</p>
              )}
              <p className="tnum text-lg font-semibold text-[rgb(var(--accent-2))]">
                {t('game.celebrate.xp', { xp: lead.xp_reward })}
              </p>
            </div>
            {rest.length > 0 && (
              <section className="space-y-2">
                <p className="label">{t('game.celebrate.more')}</p>
                <ul className="space-y-2">
                  {rest.map((item) => (
                    <li key={item.id} className="surface-sunken flex items-center gap-3 px-3 py-2.5" data-testid="celebration-more">
                      <AchievementBadge item={item} size={40} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{achievementName(item)}</span>
                        <span className="block truncate text-xs muted">{t(`game.rarity.${item.rarity}`)}</span>
                      </span>
                      <span className="tnum shrink-0 text-sm font-semibold text-[rgb(var(--accent-2))]">
                        {t('game.celebrate.xp', { xp: item.xp_reward })}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </Sheet>
    </MotionConfig>
  )
}

/** "New achievement" pill — opens the sheet on tap (Today shows it at the top of the score card). */
export function CelebrationPill({ items, onOpen }: { items: Achievement[]; onOpen: () => void }) {
  const { t } = useTranslation()
  if (!items.length) return null
  const lead = hero(items)
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex min-h-tap w-full items-center gap-3 rounded-2xl border border-[rgb(var(--accent-2))]/40 bg-[rgb(var(--accent-2))]/10 px-3 py-2 text-left"
      data-testid="celebrate-pill"
    >
      <span className="text-[rgb(var(--accent-2))]">
        <IconTrophy size={20} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-[rgb(var(--accent-2))]">
          {items.length > 1 ? t('game.celebrate.pill_many', { count: items.length }) : t('game.celebrate.pill')}
        </span>
        <span className="block truncate text-xs muted">{achievementName(lead)}</span>
      </span>
      <IconChevronRight size={18} className="shrink-0 text-[rgb(var(--accent-2))]" />
    </button>
  )
}
