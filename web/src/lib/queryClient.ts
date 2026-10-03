/**
 * TanStack Query configuration.
 *
 * * one retry for transient failures, none for 4xx (the UI shows the message),
 * * refetch on reconnect (the offline queue flushes in the same moment),
 * * long `staleTime` for settings — this is a personal tracker, not a feed.
 */
import { QueryClient, type DefaultOptions } from '@tanstack/react-query'
import { ApiError } from '@/api/client'

const defaults: DefaultOptions = {
  queries: {
    staleTime: 30_000,
    gcTime: 10 * 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: true,
    retry: (failureCount, error) => {
      if (error instanceof ApiError) {
        if (error.status >= 400 && error.status < 500) return false
        if (error.status === 401 || error.status === 403) return false
      }
      return failureCount < 2
    },
    networkMode: 'online',
  },
  mutations: {
    retry: 0,
    networkMode: 'online',
  },
}

export const queryClient = new QueryClient({ defaultOptions: defaults })

export const queryKeys = {
  session: ['session'] as const,
  appConfig: ['app-config'] as const,
  health: ['health'] as const,
  settings: ['settings'] as const,
  bodyParams: ['body-params'] as const,
  devices: ['devices'] as const,
  tokens: ['tokens'] as const,
  today: (date: string) => ['today', date] as const,
  day: (date: string) => ['nutrition', 'day', date] as const,
  foodSearch: (q: string) => ['foods', 'search', q] as const,
  favorites: ['foods', 'favorites'] as const,
  recents: ['foods', 'recents'] as const,
  recipes: ['recipes'] as const,
  // later stages: workouts, tasks, stats…
}
