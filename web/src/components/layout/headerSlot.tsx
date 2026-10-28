/**
 * The shell header's slot for a page's own actions (stage 10, a theme trait:
 * `SkinTraits.headerTitle`). In «Наклейки» the tab's title and the page's buttons share the
 * header row with sync and settings (Ffin-Plan: «План» · поиск · новая задача · sync · settings);
 * everywhere else `PageActions` renders its children in place, so the other themes keep their
 * stage-1–9 layout.
 */
import { createContext, useContext, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { useSkinTraits } from '@/lib/appearance'

export const HeaderSlotContext = createContext<HTMLElement | null>(null)

export function PageActions({ children }: { children: ReactNode }) {
  const { headerTitle } = useSkinTraits()
  const slot = useContext(HeaderSlotContext)
  if (!headerTitle) return <>{children}</>
  return slot ? createPortal(children, slot) : null
}

/** The tabs whose title the header shows (their own `h1` stays for screen readers). */
export function tabTitleKey(pathname: string): string | null {
  if (pathname === '/food' || pathname === '/food/') return 'nav.food'
  if (pathname === '/train' || pathname === '/train/') return 'nav.train'
  if (pathname === '/stats' || pathname === '/stats/') return 'nav.stats'
  if (pathname === '/plan' || pathname.startsWith('/plan/')) return 'nav.plan'
  return null
}
