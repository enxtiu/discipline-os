/**
 * TanStack Query configuration.
 *
 * * one retry for transient failures, none for 4xx (the UI shows the message),
 * * refetch on reconnect (the offline queue flushes in the same moment),
 * * long `staleTime` for settings — this is a personal tracker, not a feed.
 *
 * Freshness per family (stage 9): the day's live reads (nutrition day, planner
 * views, health today, the score…) keep the 30 s default — a tab switch within
 * it does not refetch, and the queries that follow other devices or the Shortcut
 * also refetch on focus. Reference lists that only this app's own writes change
 * (and those writes invalidate them) stay fresh for 5 minutes. Explicit
 * `staleTime` in a hook wins over both.
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

const REFERENCE_STALE_MS = 5 * 60_000
for (const family of [
  ['recipes'],
  ['foods', 'favorites'],
  ['foods', 'recents'],
  ['training', 'templates'],
  ['training', 'exercises'],
  ['training', 'records'],
  ['planner', 'areas'],
  ['planner', 'projects'],
  ['planner', 'resources'],
  ['planner', 'tags'],
  ['settings'],
  ['body-params'],
  ['devices'],
  ['tokens'],
  ['notifications', 'prefs'],
  ['notifications', 'subscriptions'],
]) {
  queryClient.setQueryDefaults(family, { staleTime: REFERENCE_STALE_MS })
}

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
  // stage 4: training
  trainingToday: ['training', 'today'] as const,
  weekPlan: (start: string) => ['training', 'plan', start] as const,
  templates: ['training', 'templates'] as const,
  exercises: (q: string, muscle: string) => ['training', 'exercises', q, muscle] as const,
  exerciseHistory: (id: string) => ['training', 'exercise', id] as const,
  workout: (id: string) => ['training', 'workout', id] as const,
  workouts: ['training', 'workouts'] as const,
  records: ['training', 'records'] as const,
  burned: (days: number) => ['training', 'burned', days] as const,
  // stage 5: health data (steps, weight, sleep) — `health` above is the API probe
  healthData: ['health-data'] as const,
  healthToday: ['health-data', 'today'] as const,
  steps: (days: number) => ['health-data', 'steps', days] as const,
  weight: (days: number) => ['health-data', 'weight', days] as const,
  sleep: (days: number) => ['health-data', 'sleep', days] as const,
  // later stages: tasks, stats…
}
