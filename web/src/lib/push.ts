/**
 * Web push in the browser (stage 7).
 *
 *   enablePush(key)  permission → pushManager.subscribe → POST /push/subscriptions
 *   disablePush()    POST /push/unsubscribe + browser unsubscribe
 *   pushStatus()     unsupported | needs_install | denied | off | on
 *   reRegisterPush() the service worker saw `pushsubscriptionchange` → send the new one
 *
 * iOS rules that shape this file: push exists only in a Home Screen web app
 * (iOS 16.4+), and the permission prompt must come straight from a tap — so
 * `enablePush` asks for permission *first*, before any other await.
 */
import { ApiError } from '@/api/client'
import { notificationsApi } from '@/features/notifications/api'
import type { PushSubscriptionInfo } from '@/features/notifications/types'
import { isIos, isStandalone, supportsPush } from './device'

export type PushStatus = 'unsupported' | 'needs_install' | 'denied' | 'off' | 'on'
export type PushErrorCode = 'unsupported' | 'needs_install' | 'denied' | 'dismissed' | 'sw_unavailable' | 'subscribe_failed'

/** A browser-side failure; `code` maps to `settings.push_errors.*`. */
export class PushError extends Error {
  readonly code: PushErrorCode

  constructor(code: PushErrorCode) {
    super(code)
    this.name = 'PushError'
    this.code = code
  }
}

const SW_READY_TIMEOUT_MS = 8000

/** iPhone Safari tab → install first (checked before feature detection: Safari tabs have no PushManager). */
export function pushSupport(): 'ok' | 'needs_install' | 'unsupported' {
  if (isIos() && !isStandalone()) return 'needs_install'
  if (!supportsPush()) return 'unsupported'
  return 'ok'
}

export function notificationPermission(): NotificationPermission | 'unsupported' {
  return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission
}

/**
 * The browser's own subscription: `null` = there is none, `undefined` = cannot tell
 * (no service worker registered — e.g. `vite dev`, where the PWA plugin is off).
 */
export async function browserSubscription(): Promise<PushSubscription | null | undefined> {
  if (!('serviceWorker' in navigator)) return undefined
  try {
    const registration = await navigator.serviceWorker.getRegistration()
    if (!registration?.pushManager) return undefined
    return await registration.pushManager.getSubscription()
  } catch {
    return undefined
  }
}

/**
 * `on` = permission granted and the server holds an active subscription for this
 * device (and the browser still has one, when it can tell).
 */
export async function pushStatus(serverHasThisDevice: boolean): Promise<PushStatus> {
  const support = pushSupport()
  if (support !== 'ok') return support
  const permission = notificationPermission()
  if (permission === 'denied') return 'denied'
  if (permission !== 'granted' || !serverHasThisDevice) return 'off'
  return (await browserSubscription()) === null ? 'off' : 'on'
}

function urlBase64ToUint8Array(value: string): Uint8Array {
  const padded = `${value}${'='.repeat((4 - (value.length % 4)) % 4)}`.replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded)
  return Uint8Array.from(raw, (char) => char.charCodeAt(0))
}

function sameKey(current: ArrayBuffer | null | undefined, wanted: Uint8Array): boolean {
  if (!current) return false
  const bytes = new Uint8Array(current)
  return bytes.length === wanted.length && bytes.every((byte, index) => byte === wanted[index])
}

async function swRegistration(): Promise<ServiceWorkerRegistration> {
  if (!('serviceWorker' in navigator)) throw new PushError('sw_unavailable')
  let timer: number | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = window.setTimeout(() => reject(new PushError('sw_unavailable')), SW_READY_TIMEOUT_MS)
  })
  try {
    return await Promise.race([navigator.serviceWorker.ready, timeout])
  } finally {
    window.clearTimeout(timer)
  }
}

async function subscribeBrowser(registration: ServiceWorkerRegistration, key: Uint8Array): Promise<PushSubscription> {
  let subscription = await registration.pushManager.getSubscription()
  // created with an older server key (VAPID rotated) → it can never work again
  if (subscription && !sameKey(subscription.options?.applicationServerKey, key)) {
    await subscription.unsubscribe().catch(() => undefined)
    subscription = null
  }
  if (subscription) return subscription
  try {
    return await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })
  } catch {
    throw new PushError('subscribe_failed')
  }
}

function register(subscription: PushSubscription): Promise<PushSubscriptionInfo> {
  const json = subscription.toJSON()
  return notificationsApi.subscribe({
    endpoint: json.endpoint ?? subscription.endpoint,
    keys: { p256dh: json.keys?.p256dh ?? '', auth: json.keys?.auth ?? '' },
    expiration_time: json.expirationTime ?? null,
    user_agent: navigator.userAgent.slice(0, 255),
  })
}

/** Call straight from a tap handler (iOS shows the permission prompt only then). */
export async function enablePush(vapidPublicKey: string): Promise<PushSubscriptionInfo> {
  const support = pushSupport()
  if (support !== 'ok') throw new PushError(support)
  const permission = await Notification.requestPermission()
  if (permission === 'denied') throw new PushError('denied')
  if (permission !== 'granted') throw new PushError('dismissed')

  const registration = await swRegistration()
  const key = urlBase64ToUint8Array(vapidPublicKey)
  const subscription = await subscribeBrowser(registration, key)
  try {
    return await register(subscription)
  } catch (error) {
    // this browser endpoint is registered to another account: start over with a fresh one
    if (error instanceof ApiError && error.code === 'subscription_conflict') {
      await subscription.unsubscribe().catch(() => undefined)
      return register(await subscribeBrowser(registration, key))
    }
    throw error
  }
}

/** Stop push on this device: the server forgets it, the browser drops it. */
export async function disablePush(serverIdsForThisDevice: string[] = []): Promise<void> {
  const subscription = await browserSubscription()
  if (subscription) {
    await notificationsApi.unsubscribe(subscription.endpoint)
    await subscription.unsubscribe().catch(() => undefined)
  }
  // whatever the server still binds to this device (it may be unreachable from the browser side)
  for (const id of serverIdsForThisDevice) {
    await notificationsApi.deleteSubscription(id).catch((error: unknown) => {
      if (!(error instanceof ApiError && error.status === 404)) throw error
    })
  }
}

/** The service worker reported a rotated subscription: register the new one (no prompt needed). */
export async function reRegisterPush(): Promise<boolean> {
  if (pushSupport() !== 'ok' || notificationPermission() !== 'granted') return false
  const config = await notificationsApi.pushConfig()
  if (!config.enabled || !config.vapid_public_key) return false
  const registration = await swRegistration()
  await register(await subscribeBrowser(registration, urlBase64ToUint8Array(config.vapid_public_key)))
  return true
}
