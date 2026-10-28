/**
 * Stats queries (stage 8). One query per section and period, keyed by the
 * anchor day the user navigated to (`null` = the server's today). Switching
 * periods keeps the previous render on screen (dimmed) until the new numbers
 * arrive — no skeleton flash, no layout jump.
 *
 * The series change with every write anywhere in the app, so they are always
 * refetched when the dashboard opens, and while it is open a successful write
 * (the quick-add sheet is one tap away) or a replayed offline write marks them
 * stale once (debounced).
 */
import { useEffect } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'

import { onQueueChange } from '@/lib/offlineQueue'
import { statsApi } from './api'
import type { Period, StatsBySection, StatsSection } from './types'

export const statsKeys = {
  all: ['stats'] as const,
  section: <K extends StatsSection>(section: K, period: Period, date: string | null) =>
    ['stats', section, period, date ?? 'today'] as const,
}

export function useStats<K extends StatsSection>(section: K, period: Period, date: string | null) {
  return useQuery<StatsBySection[K]>({
    queryKey: statsKeys.section(section, period, date),
    queryFn: () => statsApi.get(section, period, date),
    placeholderData: keepPreviousData,
    staleTime: 0,
    // the day moves on: a dashboard left open picks up new data when the app is reopened
    refetchOnWindowFocus: true,
  })
}

const REFRESH_DELAY_MS = 400

/** While the dashboard is mounted: writes elsewhere (quick add, offline replay) refresh it. */
export function useStatsAutoRefresh(): void {
  const queryClient = useQueryClient()
  useEffect(() => {
    let timer: number | undefined
    const refresh = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: statsKeys.all }), REFRESH_DELAY_MS)
    }
    const unsubscribe = queryClient.getMutationCache().subscribe((event) => {
      if (event.type === 'updated' && event.action.type === 'success') refresh()
    })
    let pending = -1
    const unlisten = onQueueChange((count) => {
      if (pending > count) refresh()
      pending = count
    })
    return () => {
      window.clearTimeout(timer)
      unsubscribe()
      unlisten()
    }
  }, [queryClient])
}
