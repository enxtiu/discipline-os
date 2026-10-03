/**
 * Typed fetch wrapper.
 *
 * Responsibilities:
 *  - inject the device bearer token,
 *  - normalise the `{error: {code, message, details}}` envelope into `ApiError`
 *    so the UI can translate `error.code` instead of parsing prose,
 *  - broadcast `unauthorized` so the app can drop the session and show the
 *    pairing screen,
 *  - carry a client-generated `X-Request-ID` for log correlation.
 */
import type { ApiErrorBody } from './types'
import { apiPath } from '@/lib/url'
import { getToken } from '@/lib/device'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details?: Record<string, unknown> | null

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || body.code)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details ?? null
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  /** Not enough rights (e.g. a scoped token hitting an owner endpoint). */
  get isForbidden(): boolean {
    return this.status === 403
  }

  get isOffline(): boolean {
    return this.status === 0
  }
}

export const UNAUTHORIZED_EVENT = 'dsos:unauthorized'

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
  signal?: AbortSignal
  /** Idempotency key for offline-replayed writes. */
  clientUuid?: string
  headers?: Record<string, string>
  /** Skip the automatic 401 broadcast (used by the pairing flow itself). */
  silentAuth?: boolean
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = apiPath(path)
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal, headers = {}, clientUuid, silentAuth } = options

  const finalHeaders: Record<string, string> = {
    Accept: 'application/json',
    'X-Request-ID': crypto.randomUUID?.().slice(0, 16) ?? String(Date.now()),
    ...headers,
  }
  const token = getToken()
  if (token) finalHeaders.Authorization = `Bearer ${token}`
  if (body !== undefined) finalHeaders['Content-Type'] = 'application/json'
  if (clientUuid) finalHeaders['X-Client-UUID'] = clientUuid

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers: finalHeaders,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      credentials: 'omit', // token auth, no cookies
    })
  } catch (cause) {
    if ((cause as Error)?.name === 'AbortError') throw cause
    throw new ApiError(0, {
      code: 'network',
      message: 'Cannot reach the server',
    })
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!response.ok) {
    const envelope = (payload as { error?: ApiErrorBody } | null)?.error
    const error = new ApiError(
      response.status,
      envelope ?? { code: 'internal_error', message: response.statusText || 'Request failed' },
    )
    if (error.isUnauthorized && !silentAuth) {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: error }))
    }
    throw error
  }

  return payload as T
}

export const api = {
  /** Escape hatch used by the offline queue/anything needing full control. */
  request,
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PUT', body }),
  delete: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'DELETE' }),
}

// --------------------------------------------------------------------------- //
// Endpoint helpers (stage 1 surface)
// --------------------------------------------------------------------------- //
export const endpoints = {
  health: () => api.get<import('./types').Health>('/v1/meta/health', { silentAuth: true }),
  appConfig: () => api.get<import('./types').AppConfig>('/v1/meta/app-config', { silentAuth: true }),
  pair: (payload: import('./types').PairingRequest) =>
    api.post<import('./types').PairingResult>('/v1/auth/pair', payload, { silentAuth: true }),
  session: () => api.get<import('./types').Session>('/v1/auth/session'),
  refresh: () => api.post<import('./types').PairingResult>('/v1/auth/refresh'),
  logout: () => api.post<{ ok: boolean; message: string }>('/v1/auth/logout'),

  devices: () => api.get<import('./types').DeviceList>('/v1/devices'),
  createPairingCode: () => api.post<import('./types').PairingCodeInfo>('/v1/devices/pairing-code'),
  updateDevice: (id: string, payload: import('./types').DeviceUpdate) =>
    api.patch<import('./types').Device>(`/v1/devices/${id}`, payload),
  revokeDevice: (id: string) => api.delete<{ ok: boolean; id: string }>(`/v1/devices/${id}`),

  settings: () => api.get<import('./types').SettingsEnvelope>('/v1/settings'),
  updateSettings: (payload: import('./types').SettingsUpdate) =>
    api.patch<import('./types').SettingsEnvelope>('/v1/settings', payload),
  setTargets: (payload: Record<string, unknown>) =>
    api.put<import('./types').Targets>('/v1/settings/targets', payload),
  recomputeTargets: () => api.post<import('./types').Targets>('/v1/settings/targets/recompute'),
  previewTargets: (payload: import('./types').TargetPreview) =>
    api.post<import('./types').Targets>('/v1/settings/targets/preview', payload),
  bodyParams: () => api.get<import('./types').BodyParams>('/v1/settings/body-params'),
  onboarding: (payload: import('./types').OnboardingPayload) =>
    api.post<import('./types').SettingsEnvelope>('/v1/settings/onboarding', payload),

  // ---- nutrition (stage 2) --------------------------------------------------
  searchFoods: (q: string, limit = 24) =>
    api.get<import('./types').FoodSearch>('/v1/foods/search', { query: { q, limit } }),
  barcodeFood: (code: string) =>
    api.get<{ food: import('./types').Food | null; barcode: string }>(`/v1/foods/barcode/${code}`),
  favorites: () => api.get<import('./types').Favorite[]>('/v1/foods/favorites'),
  addFavorite: (foodId: string, defaultGrams?: number) =>
    api.post<import('./types').Favorite>('/v1/foods/favorites', {
      food_id: foodId,
      default_grams: defaultGrams,
    }),
  removeFavorite: (foodId: string) => api.delete<{ ok: boolean }>(`/v1/foods/favorites/${foodId}`),
  recents: () => api.get<import('./types').RecentList>('/v1/foods/recents'),
  createFood: (payload: import('./types').FoodIn) => api.post<import('./types').Food>('/v1/foods', payload),

  day: (date?: string) => api.get<import('./types').DaySummary>('/v1/nutrition/day', { query: { date } }),
  logMeal: (payload: import('./types').MealLogIn) =>
    api.post<import('./types').MealLog>('/v1/nutrition/meals', payload, { clientUuid: payload.id }),
  deleteMeal: (id: string) => api.delete<{ ok: boolean }>(`/v1/nutrition/meals/${id}`),
  copyMeals: (payload: { from_day: string; meal_type?: string; to_meal_type?: string }) =>
    api.post<{ copied: number; meals: import('./types').MealLog[] }>('/v1/nutrition/meals/copy', payload),
  addWater: (amountMl: number, id?: string) =>
    api.post<import('./types').WaterLog>('/v1/nutrition/water', { amount_ml: amountMl, id }),
  deleteWater: (id: string) => api.delete<{ ok: boolean }>(`/v1/nutrition/water/${id}`),

  recipes: () => api.get<import('./types').RecipeList>('/v1/recipes'),
  createRecipe: (payload: import('./types').RecipeIn) =>
    api.post<import('./types').Recipe>('/v1/recipes', payload),
  deleteRecipe: (id: string) => api.delete<{ ok: boolean; id: string }>(`/v1/recipes/${id}`),

  tokens: () => api.get<import('./types').ApiTokenList>('/v1/tokens'),
  createToken: (payload: import('./types').ApiTokenCreate) =>
    api.post<import('./types').ApiTokenIssued>('/v1/tokens', payload),
  revokeToken: (id: string) => api.delete<{ ok: boolean; id: string }>(`/v1/tokens/${id}`),
}
