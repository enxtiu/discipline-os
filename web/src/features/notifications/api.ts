/** Notification endpoints (stage 7): `/push/*` and `/notifications/*` (device principal). */
import { api } from '@/api/client'
import type {
  NotificationItem,
  NotificationPrefs,
  NotificationPrefsIn,
  PushConfig,
  PushSubscriptionIn,
  PushSubscriptionInfo,
  PushTestResult,
} from './types'

export const notificationsApi = {
  pushConfig: () => api.get<PushConfig>('/v1/push/config'),
  subscriptions: () => api.get<{ items: PushSubscriptionInfo[] }>('/v1/push/subscriptions'),
  subscribe: (payload: PushSubscriptionIn) => api.post<PushSubscriptionInfo>('/v1/push/subscriptions', payload),
  deleteSubscription: (id: string) => api.delete<void>(`/v1/push/subscriptions/${id}`),
  unsubscribe: (endpoint: string) => api.post<void>('/v1/push/unsubscribe', { endpoint }),
  test: () => api.post<PushTestResult>('/v1/push/test'),
  prefs: () => api.get<NotificationPrefs>('/v1/notifications/prefs'),
  savePrefs: (payload: NotificationPrefsIn) => api.put<NotificationPrefs>('/v1/notifications/prefs', payload),
  history: (limit = 50) => api.get<{ items: NotificationItem[] }>('/v1/notifications', { query: { limit } }),
}
