/**
 * Stats endpoints (stage 8): one calendar period per request, computed by the
 * server in the user's timezone. `date` is any day of the period (omitted =
 * the server's today); the client never works out where a period starts.
 */
import { api } from '@/api/client'
import type { Period, StatsBySection, StatsSection } from './types'

export const statsApi = {
  get: <K extends StatsSection>(section: K, period: Period, date: string | null) =>
    api.get<StatsBySection[K]>(`/v1/stats/${section}`, { query: { period, date } }),
}
