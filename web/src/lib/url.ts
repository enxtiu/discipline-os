/**
 * URL helpers.
 *
 * In production Caddy serves the SPA and reverse-proxies `/api/*` to the API,
 * so every request is same-origin and relative (`/api/v1/...`). During `vite
 * dev` the dev server proxies `/api` to the local API. `VITE_API_BASE` can
 * override the prefix (e.g. a staging API on another host).
 */
const RAW_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'

export const API_BASE = RAW_BASE.replace(/\/+$/, '')

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${suffix}`
}

export function apiPath(versioned: string): string {
  // `/v1/auth/session` -> `/api/v1/auth/session`
  return apiUrl(versioned.startsWith('/') ? versioned : `/${versioned}`)
}
