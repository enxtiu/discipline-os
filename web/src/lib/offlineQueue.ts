/**
 * Offline write queue (engineering foundation for stage 9).
 *
 * Every mutating call can be enqueued instead of fired directly. Each entry
 * carries a **client-generated UUID** and a monotonically increasing sequence,
 * so replaying is idempotent: the server keys records by that UUID and applies
 * last-write-wins on `updated_at`.
 *
 * Persistence is IndexedDB (Dexie) with a localStorage mirror for the counters
 * the UI shows in the sync indicator.
 */
import Dexie, { type Table } from 'dexie'
import { api, ApiError } from '@/api/client'
import { OFFLINE_QUEUE_KEY } from './device'

export type QueuedMethod = 'POST' | 'PATCH' | 'PUT' | 'DELETE'

export interface QueuedRequest {
  id?: number
  clientUuid: string
  method: QueuedMethod
  path: string
  body?: unknown
  createdAt: number
  attempts: number
  lastError?: string
  /** Grouping label for the sync UI: 'meal' | 'water' | 'workout' | … */
  kind: string
}

class OfflineDatabase extends Dexie {
  queue!: Table<QueuedRequest, number>

  constructor() {
    super('dsos')
    this.version(1).stores({
      queue: '++id, clientUuid, createdAt, kind',
    })
  }
}

export const offlineDb = new OfflineDatabase()

type Listener = (pending: number) => void
const listeners = new Set<Listener>()

function notify(pending: number): void {
  try {
    window.localStorage.setItem(OFFLINE_QUEUE_KEY, String(pending))
  } catch {
    /* ignore */
  }
  listeners.forEach((listener) => listener(pending))
}

export function onQueueChange(listener: Listener): () => void {
  listeners.add(listener)
  void countPending().then(listener)
  return () => listeners.delete(listener)
}

export async function countPending(): Promise<number> {
  try {
    return await offlineDb.queue.count()
  } catch {
    return 0
  }
}

export async function enqueue(
  input: Omit<QueuedRequest, 'id' | 'createdAt' | 'attempts' | 'clientUuid'> & { clientUuid?: string },
): Promise<QueuedRequest> {
  const clientUuid = input.clientUuid ?? crypto.randomUUID()
  const entry: QueuedRequest = {
    clientUuid,
    method: input.method,
    path: input.path,
    body: input.body,
    kind: input.kind,
    createdAt: Date.now(),
    attempts: 0,
  }
  const id = await offlineDb.queue.add(entry)
  notify(await countPending())
  return { ...entry, id }
}

/**
 * Run a mutation now, or queue it when the network is down.
 * Returns `{ queued: true }` when the write is waiting for connectivity.
 */
export async function mutate<T>(
  path: string,
  options: { method?: QueuedMethod; body?: unknown; kind?: string; clientUuid?: string } = {},
): Promise<{ queued: false; data: T } | { queued: true; clientUuid: string }> {
  const method = options.method ?? 'POST'
  const clientUuid = options.clientUuid ?? crypto.randomUUID()

  if (!navigator.onLine) {
    await enqueue({ ...options, path, method, clientUuid, kind: options.kind ?? 'generic' })
    return { queued: true, clientUuid }
  }

  try {
    const data = await api.request<T>(path, { method, body: options.body, clientUuid })
    return { queued: false, data }
  } catch (error) {
    // Offline / server unreachable -> queue and let the sync loop replay it.
    if (error instanceof ApiError && error.isOffline) {
      await enqueue({ ...options, path, method, clientUuid, kind: options.kind ?? 'generic' })
      return { queued: true, clientUuid }
    }
    throw error
  }
}

let flushing = false

/** Replay the queue oldest-first. Stops on the first network failure. */
export async function flushQueue(): Promise<{ sent: number; remaining: number }> {
  if (flushing || !navigator.onLine) return { sent: 0, remaining: await countPending() }
  flushing = true
  let sent = 0

  try {
    const entries = await offlineDb.queue.orderBy('createdAt').toArray()
    for (const entry of entries) {
      try {
        await api.request(entry.path, {
          method: entry.method,
          body: entry.body,
          clientUuid: entry.clientUuid,
        })
        if (entry.id !== undefined) await offlineDb.queue.delete(entry.id)
        sent += 1
      } catch (error) {
        if (error instanceof ApiError && error.isOffline) break // still offline
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          // permanent failure (validation/conflict): drop it, don't loop forever
          if (entry.id !== undefined) await offlineDb.queue.delete(entry.id)
          continue
        }
        if (entry.id !== undefined) {
          await offlineDb.queue.update(entry.id, {
            attempts: entry.attempts + 1,
            lastError: error instanceof Error ? error.message : 'unknown',
          })
        }
      }
    }
  } finally {
    flushing = false
    notify(await countPending())
  }

  return { sent, remaining: await countPending() }
}

/** Called by the app shell when connectivity returns. */
export function installOfflineSync(): () => void {
  const onOnline = () => {
    void flushQueue()
  }
  const onOffline = () => {
    void countPending().then(notify)
  }
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)
  if (navigator.onLine) void flushQueue()
  return () => {
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
  }
}
