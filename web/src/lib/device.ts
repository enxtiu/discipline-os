/**
 * Device identity: token storage, stable fingerprint, platform sniffing.
 *
 * The token lives in `localStorage` (not a cookie) because the app is a
 * single-user PWA used on a personal phone: it must survive reloads, work
 * offline and never be sent to third parties.
 */
import { apiUrl } from './url'

export const TOKEN_KEY = 'dsos.device_token'
export const DEVICE_META_KEY = 'dsos.device_meta'
export const FINGERPRINT_KEY = 'dsos.fingerprint'
/** The light / dark / system mode (stage 10: the rest of the appearance is in lib/appearance.ts). */
export const THEME_KEY = 'dsos.theme'
export const OFFLINE_QUEUE_KEY = 'dsos.offline_queue'

export interface StoredDeviceMeta {
  id?: string
  name: string
  platform: string
  model?: string
  paired_at?: string
}

// --------------------------------------------------------------------------- //
// Token
// --------------------------------------------------------------------------- //
export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* private mode: the app still works for the current tab */
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY)
    window.localStorage.removeItem(DEVICE_META_KEY)
  } catch {
    /* ignore */
  }
}

export function hasToken(): boolean {
  return !!getToken()
}

// --------------------------------------------------------------------------- //
// Device metadata
// --------------------------------------------------------------------------- //
export type DevicePlatform = 'ios' | 'android' | 'macos' | 'windows' | 'linux' | 'web'

export function detectPlatform(): DevicePlatform {
  const ua = navigator.userAgent
  if (/iPhone|iPod/.test(ua)) return 'ios'
  if (/iPad/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) return 'ios'
  if (/Android/.test(ua)) return 'android'
  if (/Mac OS X/.test(ua)) return 'macos'
  if (/Windows/.test(ua)) return 'windows'
  if (/Linux/.test(ua)) return 'linux'
  return 'web'
}

export function detectModel(): string {
  const ua = navigator.userAgent
  // Safari on iOS: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)..."
  const ios = ua.match(/\(([^)]+)\)/)
  if (ios?.[1]) return ios[1].slice(0, 110)
  return navigator.platform || 'Web'
}

export function suggestDeviceName(): string {
  const platform = detectPlatform()
  const names: Record<string, string> = {
    ios: 'iPhone',
    android: 'Android',
    macos: 'Mac',
    windows: 'PC',
    linux: 'Linux',
    web: 'Browser',
  }
  const base = names[platform] ?? 'Device'
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches
  return isStandalone ? `${base} (app)` : base
}

/**
 * Stable per-browser fingerprint. Sent hashed on the server so re-pairing the
 * same browser reuses the existing device row instead of consuming a slot.
 */
export function deviceFingerprint(): string {
  try {
    const existing = window.localStorage.getItem(FINGERPRINT_KEY)
    if (existing) return existing
    const entropy = [
      navigator.userAgent,
      navigator.language,
      screen.width,
      screen.height,
      screen.colorDepth,
      new Date().getTimezoneOffset(),
      Math.random().toString(36).slice(2),
    ].join('|')
    const fingerprint = `fp_${simpleHash(entropy)}`
    window.localStorage.setItem(FINGERPRINT_KEY, fingerprint)
    return fingerprint
  } catch {
    return `fp_ephemeral_${Math.random().toString(36).slice(2)}`
  }
}

function simpleHash(input: string): string {
  let hash = 5381
  for (let index = 0; index < input.length; index += 1) {
    hash = (hash * 33) ^ input.charCodeAt(index)
  }
  return (hash >>> 0).toString(36) + input.length.toString(36)
}

export function storedDeviceMeta(): StoredDeviceMeta {
  try {
    const raw = window.localStorage.getItem(DEVICE_META_KEY)
    if (raw) return JSON.parse(raw) as StoredDeviceMeta
  } catch {
    /* fall through */
  }
  return { name: suggestDeviceName(), platform: detectPlatform() }
}

export function storeDeviceMeta(meta: StoredDeviceMeta): void {
  try {
    window.localStorage.setItem(DEVICE_META_KEY, JSON.stringify(meta))
  } catch {
    /* ignore */
  }
}

// --------------------------------------------------------------------------- //
// PWA / iOS specifics
// --------------------------------------------------------------------------- //
export function isStandalone(): boolean {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  )
}

export function isIos(): boolean {
  return detectPlatform() === 'ios'
}

export function isSafari(): boolean {
  const ua = navigator.userAgent
  return /Safari/.test(ua) && !/Chrome|CriOS|Firefox|FxiOS|EdgiOS/.test(ua)
}

export function supportsPush(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

// Push availability (incl. "install to the Home Screen first" on iPhone): see `pushSupport` in ./push.ts

// --------------------------------------------------------------------------- //
// Timezone
// --------------------------------------------------------------------------- //
export function detectedTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function detectedLanguage(): 'ru' | 'en' {
  const value = (navigator.language || 'ru').slice(0, 2).toLowerCase()
  return value === 'en' ? 'en' : 'ru'
}

// Theme, mode and the other appearance settings (stage 10): see ./appearance.ts

export function apiBase(): string {
  return apiUrl('')
}
