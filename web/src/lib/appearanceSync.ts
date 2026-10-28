/**
 * Appearance sync (stage 10): «Одинаково на всех устройствах».
 *
 * The appearance store (lib/appearance.ts) stays per device and instant: a tap repaints at once
 * and is saved to localStorage first. When this device's switch is ON (the default, stored on the
 * device in `dsos.appearance.sync`), the same choice is also kept on the account
 * (the `appearance` of the settings row, `PATCH /settings`), so a second device shows the same look. When it is
 * OFF this module neither reads nor writes the account's value.
 *
 * Kept out of the start-up bundle: main.tsx loads it after the first render, and «Оформление»
 * (which owns the switch) imports it from its own lazy chunk.
 *
 * The rules (docs/stage-10-appearance-sync.md §2):
 *
 *  - `dsos.appearance.base` is the account's appearance this device last saw (`null` = it had
 *    none; absent, or left by another account paired on this browser = never seen). The device's own changes are "local vs base".
 *  - Whenever the server's value is seen (the session or settings query, a push's answer, a
 *    409's `current`) it is merged key by key: a key changed on this device since `base` stays
 *    (mine), every other key follows the account. Nothing on the account yet → this device's
 *    appearance is uploaded when it has one of its own (a fresh install uploads nothing). Never
 *    seen before (first start, or the switch just turned on) and the account has one → the
 *    account's is applied.
 *  - Changes are sent to the account after a short pause with only the keys that changed and
 *    `base` for exactly those keys (the stage-9 rules: a 409 `edit_conflict` carries the current
 *    value; it is merged again and sent once more, so this device's own choice wins on a key both
 *    devices changed and every other key follows the account).
 *  - Settings are not queued (like every other setting): if the network is down or the server
 *    fails, the change simply waits — the device's value differs from `base` — and goes out when
 *    the connection is back (online / visible again / a timer with a back-off). Nothing is lost
 *    and nothing is shown in the sync sheet.
 *  - A refused write (403 `feature_not_in_plan`, the seam for subscriptions) stops the sending
 *    for this page load and says so calmly; the device keeps its own appearance.
 */
import { useSyncExternalStore } from 'react'
import type { QueryClient } from '@tanstack/react-query'

import { ApiError, endpoints } from '@/api/client'
import type { AccountAppearance, Session, SettingsEnvelope, SettingsUpdate, UserSettings } from '@/api/types'
import {
  APPEARANCE_KEY,
  GROUNDS,
  MODES,
  SHADOWS,
  SKINS,
  getAppearance,
  setAppearance,
  subscribeAppearance,
  type Appearance,
} from './appearance'
import { THEME_KEY, hasToken } from './device'
import { queryKeys } from './queryClient'

/** `on` | `off`; absent = on (the default). Per device. */
export const SYNC_KEY = 'dsos.appearance.sync'
/** `{user_id, appearance}`: the account's appearance this device last saw (`null` = none yet); absent or
 *  another `user_id` = never seen. */
export const BASE_KEY = 'dsos.appearance.base'

const DEBOUNCE_MS = 800
const RETRY_FIRST_MS = 15_000
const RETRY_MAX_MS = 5 * 60_000
const REFRESH_AFTER_MS = 60_000
const MAX_DEPTH = 3

export type SyncStatus = 'idle' | 'saving' | 'waiting' | 'blocked'
export interface SyncSnapshot {
  on: boolean
  status: SyncStatus
}

type Wire = AccountAppearance
type WireKey = keyof Wire
const WIRE_KEYS: readonly WireKey[] = ['skin', 'mode', 'ground', 'tilt', 'shadow', 'large_text', 'reduce_motion']

// --------------------------------------------------------------------------- //
// Wire ⇄ the device's store
// --------------------------------------------------------------------------- //
function toWire(a: Appearance): Wire {
  return {
    skin: a.skin,
    mode: a.mode,
    ground: a.ground,
    tilt: a.tilt,
    shadow: a.shadow,
    large_text: a.largeText,
    reduce_motion: a.reduceMotion,
  }
}

/** The value of one key if this build knows it (a newer server may send a theme this build lacks). */
function known<K extends WireKey>(key: K, value: unknown): value is Wire[K] {
  switch (key) {
    case 'skin':
      return (SKINS as readonly unknown[]).includes(value)
    case 'mode':
      return (MODES as readonly unknown[]).includes(value)
    case 'ground':
      return (GROUNDS as readonly unknown[]).includes(value)
    case 'shadow':
      return (SHADOWS as readonly unknown[]).includes(value)
    default:
      return typeof value === 'boolean'
  }
}

function patchOf(wire: Partial<Wire>): Partial<Appearance> {
  const patch: Partial<Appearance> = {}
  if (known('skin', wire.skin)) patch.skin = wire.skin
  if (known('mode', wire.mode)) patch.mode = wire.mode
  if (known('ground', wire.ground)) patch.ground = wire.ground
  if (known('tilt', wire.tilt)) patch.tilt = wire.tilt
  if (known('shadow', wire.shadow)) patch.shadow = wire.shadow
  if (known('large_text', wire.large_text)) patch.largeText = wire.large_text
  if (known('reduce_motion', wire.reduce_motion)) patch.reduceMotion = wire.reduce_motion
  return patch
}

/** This build's view of the account's value: a key it does not know is replaced by the device's own. */
function sanitise(server: Wire, local: Wire): Wire {
  const out = { ...local }
  for (const key of WIRE_KEYS) if (known(key, server[key])) (out as Record<WireKey, unknown>)[key] = server[key]
  return out
}

// --------------------------------------------------------------------------- //
// Device storage (the switch, the last seen account value)
// --------------------------------------------------------------------------- //
function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

export function syncEnabled(): boolean {
  return storage()?.getItem(SYNC_KEY) !== 'off'
}

/** The account the stored `base` belongs to (set when its settings are first seen). */
let accountId: string | null = null

function readBase(): Wire | null | undefined {
  const raw = storage()?.getItem(BASE_KEY)
  if (raw == null || accountId === null) return undefined
  try {
    const parsed = JSON.parse(raw) as { user_id?: unknown; appearance?: unknown } | null
    // another account paired on this browser: nothing is known about it yet
    if (!parsed || typeof parsed !== 'object' || parsed.user_id !== accountId) return undefined
    if (parsed.appearance === null) return null
    if (parsed.appearance && typeof parsed.appearance === 'object') {
      return { ...toWire(getAppearance()), ...(parsed.appearance as Partial<Wire>) }
    }
  } catch {
    /* damaged: as never seen */
  }
  return undefined
}

function writeBase(base: Wire | null | undefined): void {
  try {
    if (base === undefined || accountId === null) storage()?.removeItem(BASE_KEY)
    else storage()?.setItem(BASE_KEY, JSON.stringify({ user_id: accountId, appearance: base }))
  } catch {
    /* private mode: the page keeps going without */
  }
}

/** Has this device an appearance of its own (anything stored), or is it still the factory look? */
function hasOwnAppearance(): boolean {
  const s = storage()
  return !!s && (s.getItem(APPEARANCE_KEY) !== null || s.getItem(THEME_KEY) !== null)
}

// --------------------------------------------------------------------------- //
// The snapshot the switch shows
// --------------------------------------------------------------------------- //
let snapshot: SyncSnapshot = { on: syncEnabled(), status: 'idle' }
const listeners = new Set<() => void>()

function publish(next: Partial<SyncSnapshot>): void {
  const merged = { ...snapshot, ...next }
  if (merged.on === snapshot.on && merged.status === snapshot.status) return
  snapshot = merged
  for (const listener of listeners) listener()
}

function setStatus(status: SyncStatus): void {
  publish({ status })
}

// --------------------------------------------------------------------------- //
// Merge and send
// --------------------------------------------------------------------------- //
let client: QueryClient | null = null
let installed = false
let lastStamp = 0 // updated_at of the newest settings row seen: an older (late) answer is ignored
let timer: number | undefined
let retryTimer: number | undefined
let retryDelay = RETRY_FIRST_MS
let pushing = false
let again = false
let blocked = false
let applying = false // this module is writing the store: not a change to send

/** What this device wants the account to hold, given the value it last saw. */
function pending(base: Wire | null | undefined, local: Wire): Partial<Wire> | null {
  if (base === undefined) return null // nothing seen yet: wait for the server's value first
  if (base === null) return hasOwnAppearance() ? local : null
  const changes: Partial<Wire> = {}
  for (const key of WIRE_KEYS) if (local[key] !== base[key]) (changes as Record<WireKey, unknown>)[key] = local[key]
  return Object.keys(changes).length > 0 ? changes : null
}

/** The server's value has been seen: merge it into this device and send what is still ours. */
function reconcile(server: Wire | null): void {
  if (!syncEnabled()) return
  const base = readBase()
  const local = toWire(getAppearance())
  let target: Partial<Wire> = {}
  if (server !== null) {
    if (base === undefined || base === null) target = server // first sight: the account's look wins
    else for (const key of WIRE_KEYS) if (local[key] === base[key]) (target as Record<WireKey, unknown>)[key] = server[key]
  }
  writeBase(server === null ? null : sanitise(server, local))
  const patch = patchOf(target)
  const differs = (Object.keys(patch) as (keyof Appearance)[]).some((key) => patch[key] !== getAppearance()[key])
  if (differs) {
    applying = true
    try {
      setAppearance(patch)
    } finally {
      applying = false
    }
  }
  if (!pushing) {
    if (pending(readBase(), toWire(getAppearance()))) schedule(0)
    else setStatus('idle')
  }
}

function schedule(delay = DEBOUNCE_MS): void {
  if (!syncEnabled() || blocked) return
  window.clearTimeout(timer)
  timer = window.setTimeout(() => void push(), delay)
}

function pick(base: Wire, keys: string[]): Partial<Wire> {
  const out: Partial<Wire> = {}
  for (const key of keys as WireKey[]) (out as Record<WireKey, unknown>)[key] = base[key]
  return out
}

function cacheAccount(envelope: SettingsEnvelope): void {
  if (!client) return
  client.setQueryData<Session>(queryKeys.session, (old) => (old ? { ...old, settings: envelope.settings } : old))
  client.setQueryData<SettingsEnvelope>(queryKeys.settings, envelope)
}

function armRetry(): void {
  setStatus('waiting')
  window.clearTimeout(retryTimer)
  retryTimer = window.setTimeout(() => void push(), retryDelay)
  retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS)
}

async function push(depth = 0): Promise<void> {
  if (!syncEnabled() || blocked) return
  if (pushing) {
    again = true
    return
  }
  window.clearTimeout(timer)
  const base = readBase()
  const changes = pending(base, toWire(getAppearance()))
  if (!changes) {
    setStatus('idle')
    return
  }
  if (!navigator.onLine || !hasToken()) {
    setStatus('waiting')
    return
  }
  pushing = true
  setStatus('saving')
  let outcome: 'sent' | 'conflict' | 'refused' | 'failed' = 'sent'
  try {
    const body: SettingsUpdate = {
      appearance: changes,
      base: { appearance: base ? pick(base, Object.keys(changes)) : null },
    }
    const envelope = await endpoints.updateSettings(body)
    // what the account held plus what was just sent — a key changed here since is still ours
    writeBase(base ? { ...base, ...changes } : (changes as Wire))
    retryDelay = RETRY_FIRST_MS
    window.clearTimeout(retryTimer)
    cacheAccount(envelope)
    observe(envelope.settings)
  } catch (error) {
    outcome = 'failed'
    if (error instanceof ApiError) {
      if (error.status === 409 && error.code === 'edit_conflict') {
        const current = (error.details?.current as Partial<UserSettings> | undefined)?.appearance
        if (current !== undefined && depth < MAX_DEPTH) {
          reconcile(current)
          outcome = 'conflict'
        }
      } else if (error.status === 403 && error.code === 'feature_not_in_plan') {
        blocked = true
        outcome = 'refused'
      } else if (error.status === 401) outcome = 'refused'
    }
  } finally {
    pushing = false
  }
  if (outcome === 'refused') {
    setStatus(blocked ? 'blocked' : 'idle')
    return
  }
  if (outcome === 'failed') return armRetry()
  const more = again || !!pending(readBase(), toWire(getAppearance()))
  again = false
  if (!more) return setStatus('idle')
  if (depth < MAX_DEPTH) return push(depth + 1)
  armRetry()
}

/** A settings row from the server (session, settings query, a push's answer). */
function observe(settings: Pick<UserSettings, 'appearance' | 'updated_at' | 'user_id'> | null | undefined): void {
  if (!settings || settings.appearance === undefined) return // a server without the field
  if (accountId !== settings.user_id) {
    accountId = settings.user_id
    lastStamp = 0
  }
  const stamp = Date.parse(settings.updated_at)
  if (Number.isFinite(stamp)) {
    if (stamp < lastStamp) return
    lastStamp = stamp
  }
  reconcile(settings.appearance)
}

/** The session is the cheapest read of the account's settings; refetched when older than a minute. */
function refresh(maxAgeMs = REFRESH_AFTER_MS): Promise<unknown> {
  if (!client || !syncEnabled() || !hasToken() || !navigator.onLine) return Promise.resolve()
  return client
    .fetchQuery({ queryKey: queryKeys.session, queryFn: () => endpoints.session(), staleTime: maxAgeMs })
    .catch(() => undefined)
}

// --------------------------------------------------------------------------- //
// Install and the switch
// --------------------------------------------------------------------------- //
export function installAppearanceSync(queryClient: QueryClient): void {
  client = queryClient
  if (installed || typeof window === 'undefined') return
  installed = true
  publish({ on: syncEnabled() })

  queryClient.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated' || event.action.type !== 'success') return
    const key = event.query.queryKey
    if (key.length !== 1 || (key[0] !== 'session' && key[0] !== 'settings')) return
    observe((event.query.state.data as { settings?: UserSettings } | undefined)?.settings)
  })
  subscribeAppearance(() => {
    if (!applying) schedule()
  })
  window.addEventListener('online', () => {
    void refresh(0).then(() => push())
  })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return
    void refresh().then(() => push())
  })
  window.addEventListener('storage', (event) => {
    if (event.key === SYNC_KEY || event.key === null) publish({ on: syncEnabled() })
  })

  // what the session already holds (the app waits for it before the first render)
  observe(queryClient.getQueryData<Session>(queryKeys.session)?.settings)
}

/**
 * The switch. Turning it on makes this device a first-time reader: the account's appearance (if
 * any) is applied here, else this device's own is uploaded. Turning it off keeps the look as
 * it is and stops both reading and writing.
 */
export function setAppearanceSync(on: boolean): void {
  try {
    storage()?.setItem(SYNC_KEY, on ? 'on' : 'off')
  } catch {
    /* private mode: holds for this page */
  }
  window.clearTimeout(timer)
  window.clearTimeout(retryTimer)
  writeBase(undefined)
  retryDelay = RETRY_FIRST_MS
  publish({ on, status: 'idle' })
  if (!on) return
  const cached = client?.getQueryData<Session>(queryKeys.session)?.settings
  // the freshest value first (another device may have changed it), the cached one when offline
  void refresh(0).then(() => {
    if (readBase() === undefined && syncEnabled()) observe(cached)
  })
}

export function useAppearanceSync(): SyncSnapshot {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => snapshot,
    () => snapshot,
  )
}
