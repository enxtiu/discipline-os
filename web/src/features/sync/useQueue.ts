/**
 * React bindings of the offline queue (stage 9): its live state (counts by
 * status, online, sending, next retry) and its entries, re-read on every change.
 */
import { useEffect, useState, useSyncExternalStore } from 'react'

import { getQueueState, listEntries, onQueueState, type QueuedRequest, type QueueState } from '@/lib/offlineQueue'

export function useQueueState(): QueueState {
  return useSyncExternalStore(onQueueState, getQueueState, getQueueState)
}

/**
 * Queue entries (oldest first), optionally filtered — re-read whenever the
 * queue changes. `filter` must be stable (a module-level function) or the
 * entries are re-read on every render.
 */
export function useQueueEntries(filter?: (entry: QueuedRequest) => boolean): QueuedRequest[] {
  const { version } = useQueueState()
  const [entries, setEntries] = useState<QueuedRequest[]>([])
  useEffect(() => {
    let alive = true
    void listEntries().then((all) => {
      if (alive) setEntries(filter ? all.filter(filter) : all)
    })
    return () => {
      alive = false
    }
  }, [version, filter])
  return entries
}

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

/** The live reduced-motion preference (framer-motion's hook keeps the first value). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && !!window.matchMedia?.(REDUCED_MOTION).matches,
  )
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

/** Re-render every `ms` while `active` (relative times, a retry countdown). */
export function useTicker(active: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(timer)
  }, [active, ms])
  return now
}
