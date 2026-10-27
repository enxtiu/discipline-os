/**
 * One app-wide refresh after the offline queue has sent writes (stage 9 — performance).
 *
 * When the number of waiting writes goes down, the server has new data: every cached
 * read is invalidated once, a moment after the last write of the burst left — screens
 * on display refetch their queries, the others are marked stale and refetch when they
 * mount. Screens therefore no longer refetch everything each time they mount just in
 * case a flush happened while they were away (that was a refetch storm on every tab
 * switch), and a flush of many writes costs one refresh, not one per write and screen.
 *
 * The first report is the count already waiting at start — not a flush.
 */
import type { QueryClient } from '@tanstack/react-query'

import { onQueueChange } from './offlineQueue'

const SETTLE_MS = 300
/** Reads no queued write can change. */
const UNAFFECTED = new Set(['app-config', 'health', 'devices', 'tokens'])

export function installQueueRefresh(queryClient: QueryClient): () => void {
  let last: number | null = null
  let timer: number | undefined
  const stop = onQueueChange((count) => {
    if (last !== null && count < last) {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        void queryClient.invalidateQueries({
          predicate: (query) => {
            const [family, kind] = query.queryKey as unknown[]
            return !UNAFFECTED.has(String(family)) && !(family === 'foods' && kind === 'search')
          },
        })
      }, SETTLE_MS)
    }
    last = count
  })
  return () => {
    window.clearTimeout(timer)
    stop()
  }
}
