/**
 * Session state for the whole app.
 *
 * The device token is the only credential. `useSession()` is backed by TanStack
 * Query so settings/devices/etc. can invalidate it after changes. A 401 from
 * *any* request (handled in the fetch wrapper) tears the session down and sends
 * the user back to the pairing screen.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, UNAUTHORIZED_EVENT, endpoints } from '@/api/client'
import type { PairingResult, Session } from '@/api/types'
import { EMPTY_TARGETS } from '@/lib/targets'
import { queryKeys } from '@/lib/queryClient'
import {
  clearToken,
  detectedLanguage,
  detectedTimezone,
  deviceFingerprint,
  getToken,
  hasToken,
  setToken,
  storeDeviceMeta,
  suggestDeviceName,
  detectPlatform,
  detectModel,
} from '@/lib/device'
import i18n, { isSupported } from '@/i18n'

export type AuthStatus = 'loading' | 'unauthenticated' | 'authenticated'

interface PairInput {
  code: string
  deviceName?: string
}

interface AuthContextValue {
  status: AuthStatus
  session: Session | null
  error: ApiError | null
  pair: (input: PairInput) => Promise<PairingResult>
  logout: () => Promise<void>
  isPairing: boolean
  pairingError: ApiError | null
  refreshSession: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [tokenPresent, setTokenPresent] = useState<boolean>(() => hasToken())
  const [pairingError, setPairingError] = useState<ApiError | null>(null)

  const sessionQuery = useQuery({
    queryKey: queryKeys.session,
    queryFn: () => endpoints.session(),
    enabled: tokenPresent,
    retry: false,
    staleTime: 60_000,
  })

  // Any 401 from any endpoint drops the session.
  useEffect(() => {
    const handler = () => {
      clearToken()
      setTokenPresent(false)
      queryClient.clear()
    }
    window.addEventListener(UNAUTHORIZED_EVENT, handler)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler)
  }, [queryClient])

  // Keep the UI language in sync with what the server has stored.
  useEffect(() => {
    const language = sessionQuery.data?.settings.language
    if (isSupported(language) && i18n.resolvedLanguage !== language) {
      void i18n.changeLanguage(language)
    }
  }, [sessionQuery.data?.settings.language])

  const pairMutation = useMutation({
    mutationFn: async ({ code, deviceName }: PairInput) => {
      setPairingError(null)
      return endpoints.pair({
        code: code.trim(),
        device: {
          name: deviceName?.trim() || suggestDeviceName(),
          platform: detectPlatform(),
          model: detectModel(),
          app_version: import.meta.env.VITE_APP_VERSION ?? 'dev',
          fingerprint: deviceFingerprint(),
        },
        timezone: detectedTimezone(),
        language: detectedLanguage(),
      })
    },
    onSuccess: (result) => {
      setToken(result.token)
      storeDeviceMeta({
        id: result.device.id,
        name: result.device.name,
        platform: result.device.platform ?? detectPlatform(),
        paired_at: new Date().toISOString(),
      })
      setTokenPresent(true)
      // Prime the cache with what the pairing response already contains, then
      // refresh in the background (the session endpoint adds server targets).
      queryClient.setQueryData(queryKeys.session, {
        user: result.user,
        settings: result.settings,
        device: result.device,
        api_token: null,
        auth_kind: 'device',
        scopes: ['*'],
        server_time: new Date().toISOString(),
        targets: EMPTY_TARGETS,
      } satisfies Session)
      void queryClient.invalidateQueries({ queryKey: queryKeys.session })
    },
    onError: (error) => {
      if (error instanceof ApiError) setPairingError(error)
      else setPairingError(new ApiError(0, { code: 'network', message: 'Network error' }))
    },
  })

  const logout = useCallback(async () => {
    try {
      await endpoints.logout()
    } catch (error) {
      // A revoked/expired token returns 401 — that is exactly what we want.
      if (!(error instanceof ApiError)) throw error
    } finally {
      clearToken()
      setTokenPresent(false)
      queryClient.clear()
    }
  }, [queryClient])

  const status: AuthStatus = !tokenPresent
    ? 'unauthenticated'
    : sessionQuery.isLoading
      ? 'loading'
      : sessionQuery.data
        ? 'authenticated'
        : 'unauthenticated'

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      session: sessionQuery.data ?? null,
      error: (sessionQuery.error as ApiError | null) ?? null,
      pair: (input) => pairMutation.mutateAsync(input),
      logout,
      isPairing: pairMutation.isPending,
      pairingError,
      refreshSession: async () => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.session })
      },
    }),
    [status, sessionQuery.data, sessionQuery.error, pairMutation, logout, pairingError, queryClient],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}

export function useSession(): Session {
  const { session } = useAuth()
  if (!session) throw new Error('useSession requires an authenticated session')
  return session
}

/** Token presence without subscribing to session state (used by dev tools). */
export function currentToken(): string | null {
  return getToken()
}
