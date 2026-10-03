/**
 * Translate an API error into a human string.
 * `error.code` maps 1:1 to `errors.*` in the locale files.
 */
import i18n from '@/i18n'
import { ApiError } from '@/api/client'

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const key = `errors.${error.code}`
    const translated = i18n.t(key)
    if (translated !== key) return translated
    return error.message || i18n.t('app.error_generic')
  }
  if (error instanceof Error) return error.message || i18n.t('app.error_generic')
  return i18n.t('app.error_generic')
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.isUnauthorized
}

export function isOffline(error: unknown): boolean {
  return error instanceof ApiError && error.isOffline
}

export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.code !== 'validation_error') return {}
  const details = error.details as { loc?: unknown[]; msg?: string }[] | undefined
  const out: Record<string, string> = {}
  for (const item of Array.isArray(details) ? details : []) {
    const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body') : []
    const key = loc.join('.')
    if (key && item.msg) out[key] = item.msg
  }
  return out
}
