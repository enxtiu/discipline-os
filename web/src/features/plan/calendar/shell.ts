/**
 * The app shell's sticky top bar (`header.sticky` in AppShell) and fixed tab
 * bar (`.tabbar`): the calendar's own headers stick right below the top
 * bar, and drag auto-scroll stops above the tab bar. Measured, with typical
 * sizes as the fallback, so a re-skin of the shell does not break the grid.
 */
import { useEffect, useLayoutEffect, useRef, useState, type MutableRefObject } from 'react'

const FALLBACK = { top: 60, bottom: 80 }

export interface ShellInsets {
  /** Height of the sticky app header (px from the viewport top). */
  top: number
  /** Height of the fixed tab bar (px from the viewport bottom). */
  bottom: number
}

export function shellInsets(): ShellInsets {
  const header = document.querySelector<HTMLElement>('header.sticky')
  const tabbar = document.querySelector<HTMLElement>('.tabbar')
  return {
    top: header ? header.getBoundingClientRect().height : FALLBACK.top,
    bottom: tabbar ? tabbar.getBoundingClientRect().height : FALLBACK.bottom,
  }
}

export function useShellInsets(): ShellInsets {
  const [insets, setInsets] = useState<ShellInsets>(FALLBACK)
  useLayoutEffect(() => {
    const update = () =>
      setInsets((current) => {
        const next = shellInsets()
        return next.top === current.top && next.bottom === current.bottom ? current : next
      })
    update()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    for (const element of [document.querySelector('header.sticky'), document.querySelector('.tabbar')]) {
      if (element && observer) observer.observe(element)
    }
    window.addEventListener('resize', update)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', update)
    }
  }, [])
  return insets
}

const AUTOSCROLL_EDGE = 44
/** Movement (px) away from the pick-up point before the page may scroll that way. */
const DIRECTION_SLOP = 12

/**
 * While `active`, scroll the page when the pointer (`point.current`) comes
 * within 44 px of the visible area — below `top()` (the app header plus any
 * sticky calendar header) and above the tab bar — faster the further in it
 * is, but only in the direction the pointer moved from where the drag began:
 * a block picked up right under the sticky headers (or just above the tab
 * bar) does not run away on its own. `onScroll` runs after every page scroll
 * so a drag preview can follow the content that moved under a still finger.
 */
export function useEdgeAutoScroll(
  active: boolean,
  point: MutableRefObject<{ x: number; y: number } | null>,
  top: () => number,
  onScroll: () => void,
): void {
  const latest = useRef({ top, onScroll })
  useEffect(() => {
    latest.current = { top, onScroll }
  })
  useEffect(() => {
    if (!active) return
    const origin = point.current?.y ?? null
    let raf = 0
    const tick = () => {
      const current = point.current
      if (current) {
        const upper = latest.current.top()
        const lower = window.innerHeight - shellInsets().bottom
        const from = origin ?? current.y
        let delta = 0
        if (current.y < upper + AUTOSCROLL_EDGE && current.y < from - DIRECTION_SLOP) {
          delta = -(upper + AUTOSCROLL_EDGE - current.y)
        } else if (current.y > lower - AUTOSCROLL_EDGE && current.y > from + DIRECTION_SLOP) {
          delta = current.y - (lower - AUTOSCROLL_EDGE)
        }
        const step = Math.max(-14, Math.min(14, Math.round(delta / 3)))
        if (step !== 0) window.scrollBy(0, step)
      }
      raf = window.requestAnimationFrame(tick)
    }
    raf = window.requestAnimationFrame(tick)
    const follow = () => latest.current.onScroll()
    window.addEventListener('scroll', follow, { passive: true })
    return () => {
      window.cancelAnimationFrame(raf)
      window.removeEventListener('scroll', follow)
    }
  }, [active, point])
}
