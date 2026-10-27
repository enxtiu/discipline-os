/**
 * Offline write queue (stage 1, polished in stage 9).
 *
 * Every offline-capable write goes through `mutate()`: it is sent at once when
 * the network is up and nothing it depends on is still waiting, otherwise it is
 * stored here (IndexedDB via Dexie) with a **client-generated UUID** and
 * replayed in order. Replays are idempotent — the server keys rows by that id.
 *
 * Statuses: `pending` (waiting; removed once sent) → `failed` (the server
 * refused it: kept with its HTTP status, error code and message) or `conflict`
 * (409 `edit_conflict`: kept with the server's current row). Nothing is ever
 * dropped silently: failed and conflicting writes wait for the user's decision
 * in the sync sheet (retry / discard / keep theirs / keep mine).
 *
 * Replay (`flushQueue`): oldest first. A transient failure — no network, 5xx,
 * 408, 429 (honouring `Retry-After`), a retryable 409 — stops the replay and
 * schedules a retry with exponential, jittered backoff (2 s → 5 min). Replays
 * also start on `online`, when the page becomes visible, after any successful
 * API call (the server is reachable again) and from the sync sheet. A 401 stops
 * the replay and keeps every entry: the auth flow takes over, and the queue
 * resumes once a new device token is stored. A DELETE answered 404 is done
 * (the row is gone either way).
 *
 * Dependencies (kept simple on purpose): a write waits behind an earlier
 * unsent one — failed, conflicting, or itself waiting — when their entity paths
 * are prefixes of one another (`/planner/tasks/X` and `/planner/tasks/X/complete`;
 * a create counts as `<collection>/<its id>`) or when its path or body names the
 * earlier write's entity id (`parent_id`, `task_id`, `/meals/<id>` …). So a
 * subtask never overtakes its parent, and an edit never overtakes its create.
 *
 * Writes sent with `merge: true` (PATCH/PUT autosaves) are coalesced into the
 * newest queued write of the same path while it waits: later fields win, and a
 * field's `base` (compare-and-set) stays the one it was first queued with.
 */
import Dexie, { type Table } from 'dexie'
import { api, ApiError, onApiSuccess } from '@/api/client'
import { getToken, OFFLINE_QUEUE_KEY } from './device'

export type QueuedMethod = 'POST' | 'PATCH' | 'PUT' | 'DELETE'
export type QueueStatus = 'pending' | 'failed' | 'conflict'

/** Why the server refused a write (the envelope of the answer). */
export interface QueueError {
  status: number
  code: string
  message: string
  details?: unknown
}

/** A 409 `edit_conflict`: the fields that moved and the row as the server shows it now. */
export interface QueueConflict {
  fields: string[]
  current: unknown
}

/** Shown in the sync sheet and used by optimistic views; never sent. */
export interface QueueMeta {
  /** A short human description: «Гречка · 165 ккал», a task title… */
  title?: string
  /** The day view the write was made on (optimistic meals and water). */
  day?: string
  [key: string]: unknown
}

export interface QueuedRequest {
  id?: number
  clientUuid: string
  method: QueuedMethod
  path: string
  body?: unknown
  createdAt: number
  attempts: number
  lastError?: string
  /** Grouping label for the sync UI: 'meal' | 'water' | 'task' | 'set' | … */
  kind: string
  /** Rows stored by a stage-1 build have none: they are pending. */
  status?: QueueStatus
  error?: QueueError | null
  conflict?: QueueConflict | null
  updatedAt?: number
  /** Set while a replay is sending this entry (a merge never lands in a write on its way). */
  sending?: number | null
  meta?: QueueMeta
}

class OfflineDatabase extends Dexie {
  queue!: Table<QueuedRequest, number>

  constructor() {
    super('dsos')
    this.version(1).stores({
      queue: '++id, clientUuid, createdAt, kind',
    })
    // stage 9: a status per entry; rows queued by an older build are pending
    this.version(2)
      .stores({ queue: '++id, clientUuid, createdAt, kind, status' })
      .upgrade((tx) =>
        tx
          .table('queue')
          .toCollection()
          .modify((entry: QueuedRequest) => {
            if (!entry.status) entry.status = 'pending'
            if (typeof entry.attempts !== 'number') entry.attempts = 0
          }),
      )
  }
}

export const offlineDb = new OfflineDatabase()

export function statusOf(entry: Pick<QueuedRequest, 'status'>): QueueStatus {
  return entry.status ?? 'pending'
}

// --------------------------------------------------------------------------- //
// State and subscriptions
// --------------------------------------------------------------------------- //
export interface QueueState {
  online: boolean
  /** Waiting to be sent (including writes waiting behind a failed one). */
  pending: number
  failed: number
  conflict: number
  total: number
  /** A replay is running. */
  sending: boolean
  /** When the next automatic retry runs (backoff), or null. */
  nextRetryAt: number | null
  /** The server answered 401: waiting for the device to be paired again. */
  authBlocked: boolean
  /** Bumps on every change; hooks that read entries re-read on it. */
  version: number
}

let state: QueueState = {
  online: typeof navigator === 'undefined' ? true : navigator.onLine,
  pending: 0,
  failed: 0,
  conflict: 0,
  total: 0,
  sending: false,
  nextRetryAt: null,
  authBlocked: false,
  version: 0,
}

type Listener = (pending: number) => void
type StateListener = (state: QueueState) => void
type SentListener = (entry: QueuedRequest, data: unknown) => void
const listeners = new Set<Listener>()
const stateListeners = new Set<StateListener>()
const sentListeners = new Set<SentListener>()

function publish(patch: Partial<QueueState>, entriesChanged: boolean): void {
  state = { ...state, ...patch, version: state.version + 1 }
  if (entriesChanged) {
    try {
      window.localStorage.setItem(OFFLINE_QUEUE_KEY, String(state.pending))
    } catch {
      /* ignore */
    }
  }
  for (const listener of stateListeners) listener(state)
  if (entriesChanged) for (const listener of listeners) listener(state.pending)
}

let refreshSeq = 0
/** Recount from the database and tell every subscriber. */
async function refresh(): Promise<void> {
  const seq = ++refreshSeq
  const entries = await readAll()
  if (seq !== refreshSeq) return // a newer count is on its way
  const counts = { pending: 0, failed: 0, conflict: 0 }
  for (const entry of entries) counts[statusOf(entry)] += 1
  publish({ ...counts, total: entries.length }, true)
}

async function readAll(): Promise<QueuedRequest[]> {
  try {
    return await offlineDb.queue.toArray() // primary-key order = the order of the writes
  } catch {
    return []
  }
}

/** Called with the number of pending writes whenever the queue changes (and once at once). */
export function onQueueChange(listener: Listener): () => void {
  listeners.add(listener)
  void countPending().then(listener)
  return () => listeners.delete(listener)
}

/** The richer subscription: counts by status, online, sending, next retry, auth. */
export function onQueueState(listener: StateListener): () => void {
  stateListeners.add(listener)
  return () => stateListeners.delete(listener)
}

export function getQueueState(): QueueState {
  return state
}

/** A write left the queue: the server accepted it (`data` is its answer; null for a DELETE already gone). */
export function onQueueSent(listener: SentListener): () => void {
  sentListeners.add(listener)
  return () => sentListeners.delete(listener)
}

/** Writes waiting to be sent. */
export async function countPending(): Promise<number> {
  try {
    return await offlineDb.queue.filter((entry) => statusOf(entry) === 'pending').count()
  } catch {
    return 0
  }
}

/** Pending writes whose path starts with `prefix`, oldest first (a screen can show what waits). */
export async function pendingFor(prefix: string): Promise<QueuedRequest[]> {
  return (await readAll()).filter((entry) => statusOf(entry) === 'pending' && entry.path.startsWith(prefix))
}

/** Every entry (pending, failed, conflict), oldest first — the sync sheet's list. */
export function listEntries(): Promise<QueuedRequest[]> {
  return readAll()
}

// --------------------------------------------------------------------------- //
// Dependencies
// --------------------------------------------------------------------------- //
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

type Write = Pick<QueuedRequest, 'method' | 'path' | 'body'>

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function createdId(write: Write): string | null {
  if (write.method !== 'POST' || !isRecord(write.body)) return null
  const id = write.body.id
  return typeof id === 'string' && UUID.test(id) ? id.toLowerCase() : null
}

/** The entity a write touches: its path, or `<collection>/<id>` for a create that names its id. */
export function entityScope(write: Write): string {
  const id = createdId(write)
  return id && !write.path.toLowerCase().endsWith(`/${id}`) ? `${write.path}/${id}` : write.path
}

/** The entity's id: the last UUID of its scope (none for a journal day, say). */
export function entityId(write: Write): string | null {
  const parts = entityScope(write).split('/')
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    if (UUID.test(parts[index])) return parts[index].toLowerCase()
  }
  return null
}

/** Ids a write names: in its path and anywhere in its body (three levels deep). */
function mentionedIds(write: Write): Set<string> {
  const out = new Set<string>()
  for (const part of write.path.split('/')) if (UUID.test(part)) out.add(part.toLowerCase())
  const visit = (value: unknown, depth: number) => {
    if (typeof value === 'string') {
      if (UUID.test(value)) out.add(value.toLowerCase())
      return
    }
    if (depth > 3 || !value || typeof value !== 'object') return
    for (const child of Array.isArray(value) ? value : Object.values(value)) visit(child, depth + 1)
  }
  visit(write.body, 0)
  return out
}

function related(a: string, b: string): boolean {
  return a === b || a.startsWith(`${b}/`) || b.startsWith(`${a}/`)
}

/** `later` must wait behind `earlier` while `earlier` is unsent (see the module doc). */
export function dependsOn(later: Write, earlier: Write): boolean {
  if (related(entityScope(later), entityScope(earlier))) return true
  const id = entityId(earlier)
  return id !== null && mentionedIds(later).has(id)
}

// --------------------------------------------------------------------------- //
// Enqueue (with coalescing)
// --------------------------------------------------------------------------- //
export interface EnqueueInput {
  method: QueuedMethod
  path: string
  body?: unknown
  kind: string
  clientUuid?: string
  meta?: QueueMeta
  /** Coalesce into the newest queued write of the same path (PATCH/PUT autosaves). */
  merge?: boolean
}

function canMerge(last: QueuedRequest | undefined, input: EnqueueInput): last is QueuedRequest {
  return (
    !!last &&
    !!input.merge &&
    statusOf(last) === 'pending' &&
    !last.sending &&
    (input.method === 'PATCH' || input.method === 'PUT') &&
    last.method === input.method &&
    last.path === input.path &&
    isRecord(last.body) &&
    isRecord(input.body)
  )
}

/**
 * Later values win; a field's `base` is the one it was first queued with — and a field
 * the older write set without a base gets none (its base would be our own unsent value).
 */
export function mergeBodies(older: Record<string, unknown>, newer: Record<string, unknown>): Record<string, unknown> {
  const olderBase = isRecord(older.base) ? older.base : {}
  const newerBase = isRecord(newer.base) ? newer.base : {}
  const merged: Record<string, unknown> = { ...older, ...newer }
  delete merged.base
  const base: Record<string, unknown> = {}
  for (const [field, value] of Object.entries(newerBase)) if (!(field in older)) base[field] = value
  for (const [field, value] of Object.entries(olderBase)) base[field] = value
  if (Object.keys(base).length > 0) merged.base = base
  return merged
}

export async function enqueue(input: EnqueueInput): Promise<QueuedRequest> {
  const now = Date.now()
  const entry: QueuedRequest = {
    clientUuid: input.clientUuid ?? crypto.randomUUID(),
    method: input.method,
    path: input.path,
    body: input.body,
    kind: input.kind,
    createdAt: now,
    updatedAt: now,
    attempts: 0,
    status: 'pending',
    error: null,
    conflict: null,
    meta: input.meta,
  }
  let stored: QueuedRequest = entry
  await offlineDb.transaction('rw', offlineDb.queue, async () => {
    const last = input.merge ? await offlineDb.queue.toCollection().last() : undefined
    if (canMerge(last, input)) {
      stored = {
        ...last,
        body: mergeBodies(last.body as Record<string, unknown>, input.body as Record<string, unknown>),
        meta: { ...(last.meta ?? {}), ...(input.meta ?? {}) },
        updatedAt: now,
      }
      await offlineDb.queue.put(stored)
      return
    }
    const id = await offlineDb.queue.add(entry)
    stored = { ...entry, id }
  })
  await refresh()
  return stored
}

/** Is anything already queued that this write has to wait behind? */
async function mustWait(write: Write): Promise<boolean> {
  return (await readAll()).some((entry) => dependsOn(write, entry))
}

// --------------------------------------------------------------------------- //
// Sending and classifying answers
// --------------------------------------------------------------------------- //
type Outcome =
  | { kind: 'sent'; data: unknown }
  | { kind: 'failed'; error: QueueError }
  | { kind: 'conflict'; error: QueueError; conflict: QueueConflict }
  | { kind: 'transient'; message: string; retryAfterMs: number | null }
  | { kind: 'auth' }

/** What an error means for a queued write (exported for the tests of the rules). */
export function classify(error: unknown, write?: Pick<QueuedRequest, 'method'>): Outcome {
  if (!(error instanceof ApiError)) {
    return { kind: 'transient', message: error instanceof Error ? error.message : 'unknown', retryAfterMs: null }
  }
  if (error.isOffline) return { kind: 'transient', message: error.message, retryAfterMs: null }
  if (error.status === 401) return { kind: 'auth' }
  const details = error.details as Record<string, unknown> | null | undefined
  if (
    error.status >= 500 ||
    error.status === 408 ||
    error.status === 429 ||
    (error.status === 409 && details?.retryable === true)
  ) {
    return { kind: 'transient', message: error.message, retryAfterMs: error.retryAfterMs }
  }
  const failure: QueueError = { status: error.status, code: error.code, message: error.message, details: details ?? undefined }
  if (error.status === 409 && error.code === 'edit_conflict') {
    const fields = Array.isArray(details?.fields) ? (details.fields as unknown[]).map(String) : []
    return { kind: 'conflict', error: failure, conflict: { fields, current: details?.current ?? null } }
  }
  if (error.status === 404 && write?.method === 'DELETE') return { kind: 'sent', data: null }
  return { kind: 'failed', error: failure }
}

async function send(entry: QueuedRequest): Promise<Outcome> {
  try {
    const data = await api.request(entry.path, { method: entry.method, body: entry.body, clientUuid: entry.clientUuid })
    return { kind: 'sent', data }
  } catch (error) {
    if ((error as Error)?.name === 'AbortError') return { kind: 'transient', message: 'aborted', retryAfterMs: null }
    return classify(error, entry)
  }
}

// --------------------------------------------------------------------------- //
// Backoff and auth
// --------------------------------------------------------------------------- //
export const MIN_RETRY_MS = 2_000
export const MAX_RETRY_MS = 5 * 60_000
let failures = 0
let retryTimer: number | undefined

/** 2 s, 4 s, 8 s … ≤ 5 min, ±20 % jitter; a `Retry-After` from the server wins. */
export function backoffDelay(attempt: number, retryAfterMs: number | null = null, random = Math.random()): number {
  if (retryAfterMs != null) return Math.min(Math.max(retryAfterMs, 1_000), 60 * 60_000)
  const exponential = MIN_RETRY_MS * 2 ** Math.min(Math.max(attempt - 1, 0), 16)
  return Math.round(Math.min(MAX_RETRY_MS, exponential * (0.8 + random * 0.4)))
}

function clearRetry(): void {
  if (retryTimer !== undefined) window.clearTimeout(retryTimer)
  retryTimer = undefined
}

function scheduleRetry(retryAfterMs: number | null): void {
  failures += 1
  clearRetry()
  if (!navigator.onLine) {
    publish({ nextRetryAt: null }, false) // the `online` event will replay
    return
  }
  const delay = backoffDelay(failures, retryAfterMs)
  retryTimer = window.setTimeout(() => {
    retryTimer = undefined
    void flushQueue()
  }, delay)
  publish({ nextRetryAt: Date.now() + delay }, false)
}

/** The token the server refused (undefined = not refused). */
let refusedToken: string | null | undefined

function authBlocked(): boolean {
  const token = getToken()
  return !token || (refusedToken !== undefined && token === refusedToken)
}

// --------------------------------------------------------------------------- //
// Replay
// --------------------------------------------------------------------------- //
let flushing: Promise<{ sent: number; remaining: number }> | null = null
let again = false

/** Replay the queue oldest-first (see the module doc for the rules). */
export function flushQueue(): Promise<{ sent: number; remaining: number }> {
  if (flushing) {
    again = true
    return flushing
  }
  flushing = runFlush().finally(() => {
    flushing = null
    if (again) {
      again = false
      void flushQueue()
    }
  })
  return flushing
}

/**
 * The next entry after `cursor`; `claimed` when it is to be sent now (it is marked as
 * sending in the same transaction, so a coalescing write never lands in it meanwhile).
 */
async function claimNext(
  cursor: number,
  blockers: QueuedRequest[],
): Promise<{ entry: QueuedRequest; claimed: boolean } | undefined> {
  return offlineDb.transaction('rw', offlineDb.queue, async () => {
    const next = await offlineDb.queue.where(':id').above(cursor).first()
    if (!next || next.id === undefined) return undefined
    const claimed = statusOf(next) === 'pending' && !blockers.some((blocker) => dependsOn(next, blocker))
    if (claimed) {
      next.sending = Date.now()
      await offlineDb.queue.update(next.id, { sending: next.sending })
    }
    return { entry: next, claimed }
  })
}

async function runFlush(): Promise<{ sent: number; remaining: number }> {
  if (!navigator.onLine || authBlocked()) {
    await refresh()
    return { sent: 0, remaining: state.pending }
  }
  clearRetry()
  publish({ sending: true, nextRetryAt: null, authBlocked: false }, false)
  let sent = 0
  const blockers: QueuedRequest[] = []
  let cursor = 0
  try {
    for (;;) {
      const next = await claimNext(cursor, blockers)
      if (!next || next.entry.id === undefined) break
      const entry = next.entry as QueuedRequest & { id: number }
      cursor = entry.id
      if (!next.claimed) {
        blockers.push(entry) // failed, conflicting, or waiting behind one of those
        continue
      }
      const outcome = await send(entry)
      if (outcome.kind === 'sent') {
        await offlineDb.queue.delete(entry.id)
        sent += 1
        failures = 0
        for (const listener of sentListeners) listener(entry, outcome.data)
        await refresh()
        continue
      }
      if (outcome.kind === 'failed' || outcome.kind === 'conflict') {
        const update: Partial<QueuedRequest> = {
          status: outcome.kind === 'conflict' ? 'conflict' : 'failed',
          error: outcome.error,
          conflict: outcome.kind === 'conflict' ? outcome.conflict : null,
          lastError: outcome.error.message,
          attempts: entry.attempts + 1,
          updatedAt: Date.now(),
          sending: null,
        }
        await offlineDb.queue.update(entry.id, update)
        blockers.push({ ...entry, ...update })
        await refresh()
        continue
      }
      await offlineDb.queue.update(entry.id, {
        sending: null,
        ...(outcome.kind === 'transient'
          ? { attempts: entry.attempts + 1, lastError: outcome.message, updatedAt: Date.now() }
          : {}),
      })
      if (outcome.kind === 'auth') {
        refusedToken = getToken() // resumes once the device is paired again (a new token)
        publish({ authBlocked: true }, false)
      } else {
        scheduleRetry(outcome.retryAfterMs)
      }
      break
    }
  } finally {
    publish({ sending: false }, false)
    await refresh()
  }
  return { sent, remaining: state.pending }
}

/** Replay now: the scheduled retry (if any) is replaced. */
export function flushNow(): Promise<{ sent: number; remaining: number }> {
  clearRetry()
  return flushQueue()
}

// --------------------------------------------------------------------------- //
// The write path
// --------------------------------------------------------------------------- //
export interface MutateOptions {
  method?: QueuedMethod
  body?: unknown
  kind?: string
  clientUuid?: string
  meta?: QueueMeta
  /** Coalesce with the newest queued write of the same path while it waits (autosaves). */
  merge?: boolean
}

/**
 * Run a mutation now, or queue it when the network is down, the server is
 * unreachable or failing (5xx/429), or an earlier write it depends on is still
 * waiting (the order is kept). Returns `{ queued: true }` when the write waits.
 * A refusal (4xx) of a write sent now is thrown to the caller, as before.
 */
export async function mutate<T>(
  path: string,
  options: MutateOptions = {},
): Promise<{ queued: false; data: T } | { queued: true; clientUuid: string }> {
  const method = options.method ?? 'POST'
  const clientUuid = options.clientUuid ?? crypto.randomUUID()
  const input: EnqueueInput = {
    path,
    method,
    body: options.body,
    kind: options.kind ?? 'generic',
    clientUuid,
    meta: options.meta,
    merge: options.merge,
  }

  if (!navigator.onLine) {
    await enqueue(input)
    return { queued: true, clientUuid }
  }
  if (await mustWait(input)) {
    await enqueue(input)
    if (!authBlocked()) void flushQueue()
    return { queued: true, clientUuid }
  }

  try {
    const data = await api.request<T>(path, { method, body: options.body, clientUuid })
    return { queued: false, data }
  } catch (error) {
    const outcome = classify(error, { method })
    if (outcome.kind === 'transient' || outcome.kind === 'auth') {
      // the write is kept: replayed when the server is reachable (or the device paired) again
      await enqueue(input)
      if (outcome.kind === 'transient') scheduleRetry(outcome.retryAfterMs)
      else {
        refusedToken = getToken()
        publish({ authBlocked: true }, false)
      }
      return { queued: true, clientUuid }
    }
    if (outcome.kind === 'sent') return { queued: false, data: null as T } // a DELETE of a row already gone
    throw error
  }
}

// --------------------------------------------------------------------------- //
// The user's decisions (sync sheet)
// --------------------------------------------------------------------------- //
/** Drop one write for good. */
export async function discardEntry(id: number): Promise<void> {
  await offlineDb.queue.delete(id)
  await refresh()
}

/** Send a failed (or conflicting) write again as it is. */
export async function retryEntry(id: number): Promise<void> {
  await offlineDb.queue.update(id, { status: 'pending', error: null, conflict: null, sending: null, updatedAt: Date.now() })
  await refresh()
  void flushNow()
}

/**
 * Settle a conflict field by field. `mine`: send again without `base` for the
 * conflicting fields (they overwrite). `theirs`: drop those fields from the
 * write (the other device's values stay); a write left with nothing is removed.
 */
export async function resolveConflict(id: number, choice: 'mine' | 'theirs'): Promise<void> {
  const entry = await offlineDb.queue.get(id)
  if (!entry) return
  const fields = entry.conflict?.fields ?? []
  const body = isRecord(entry.body) ? { ...entry.body } : {}
  const base = isRecord(body.base) ? { ...body.base } : {}
  for (const field of fields) {
    delete base[field]
    if (choice === 'theirs') delete body[field]
  }
  if (Object.keys(base).length > 0) body.base = base
  else delete body.base
  const rest = Object.keys(body).filter((key) => key !== 'base' && key !== 'id')
  if (choice === 'theirs' && rest.length === 0) {
    await discardEntry(id)
    return
  }
  await offlineDb.queue.update(id, {
    body,
    status: 'pending',
    error: null,
    conflict: null,
    sending: null,
    updatedAt: Date.now(),
  })
  await refresh()
  void flushNow()
}

/** «Отправить всё сейчас»: failed writes get another try, the backoff wait is skipped. */
export async function sendAllNow(): Promise<void> {
  await offlineDb.queue
    .filter((entry) => statusOf(entry) === 'failed')
    .modify({ status: 'pending', error: null, sending: null })
  failures = 0
  await refresh()
  await flushNow()
}

// --------------------------------------------------------------------------- //
// Installation
// --------------------------------------------------------------------------- //
let lastKick = 0

/** Called once at startup: replay on reconnect, on return to the page and after any answered request. */
export function installOfflineSync(): () => void {
  const replay = () => {
    if (state.pending > 0) void flushNow()
  }
  const onOnline = () => {
    publish({ online: true }, false)
    void flushNow()
  }
  const onOffline = () => {
    clearRetry()
    publish({ online: false, nextRetryAt: null }, false)
    void refresh()
  }
  const onVisible = () => {
    if (document.visibilityState === 'visible' && navigator.onLine) replay()
  }
  const stopSuccess = onApiSuccess(() => {
    // the server answers again: a write waiting for its retry goes now (not from inside a replay)
    if (flushing || state.pending === 0 || Date.now() - lastKick < 1_000) return
    lastKick = Date.now()
    window.setTimeout(() => void flushNow(), 0)
  })
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)
  document.addEventListener('visibilitychange', onVisible)
  // a replay that was cut off (tab closed mid-send) left its claim behind
  void offlineDb.queue
    .filter((entry) => !!entry.sending)
    .modify({ sending: null })
    .catch(() => undefined)
    .then(() => {
      if (navigator.onLine) void flushQueue()
      else void refresh()
    })
  return () => {
    stopSuccess()
    clearRetry()
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
    document.removeEventListener('visibilitychange', onVisible)
  }
}
