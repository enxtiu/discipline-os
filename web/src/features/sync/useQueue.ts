/**
 * React bindings of the offline queue (stage 9): its live state (counts by
 * status, online, sending, next retry) and its entries, re-read on every change.
 */
import { useEffect, useState, useSyncExternalStore } from 'react'

import { useReducedMotionPref } from '@/lib/appearance'
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

/**
 * The live reduced-motion preference: the OS or «Меньше анимации» (stage 10,
 * lib/appearance.ts — framer-motion's own hook keeps the first value).
 */
export const usePrefersReducedMotion = useReducedMotionPref

/** The time now, re-rendering every `ms` while `active` (relative times, a retry countdown). */
export function useTicker(active: boolean, ms = 1000): number {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setTick((value) => value + 1), ms)
    return () => window.clearInterval(timer)
  }, [active, ms])
  return Date.now()
}
