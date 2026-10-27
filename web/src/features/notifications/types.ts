/**
 * Notification types (stage 7) — mirror `app/schemas/notifications.py` one to one.
 *
 * Kept feature-local while stage 6 regenerates `src/api/generated/schema.d.ts`
 * in parallel; after the next `npm run gen:api` these can be re-pointed at
 * `Schemas['…Out']` under the same names, so components never change.
 */

export const CATEGORIES = [
  'task_reminders',
  'meal_logging',
  'water',
  'workout',
  'evening_review',
  'streak_at_risk',
] as const
export type Category = (typeof CATEGORIES)[number]

export interface PushConfig {
  enabled: boolean
  vapid_public_key: string | null
}

export interface PushSubscriptionIn {
  endpoint: string
  keys: { p256dh: string; auth: string }
  /** PushSubscription.expirationTime (epoch ms) */
  expiration_time?: number | null
  user_agent?: string | null
}

export interface PushSubscriptionInfo {
  id: string
  device_id: string | null
  device_name: string | null
  device_active: boolean
  is_current_device: boolean
  endpoint_host: string
  is_active: boolean
  failure_count: number
  last_success_at: string | null
  last_failure_at: string | null
  last_error: string | null
  expiration_time: string | null
  created_at: string
  updated_at: string
}

export interface PushTestResult {
  sent: number
  failed: number
  deactivated: number
}

export interface QuietHours {
  enabled: boolean
  start: string // HH:MM local
  end: string
}

export interface NotificationPrefs {
  task_reminders: boolean
  meal_logging: boolean
  water: boolean
  workout: boolean
  evening_review: boolean
  streak_at_risk: boolean
  meal_times: string[] // HH:MM local, sorted
  water_times: string[]
  workout_time: string
  evening_review_time: string
  quiet_hours: QuietHours
  timezone: string
  /** the server has VAPID keys (= feature flag push_notifications) */
  push_enabled: boolean
  active_subscriptions: number
}

/** PUT /notifications/prefs — fields not sent keep their value. */
export type NotificationPrefsIn = Partial<
  Pick<
    NotificationPrefs,
    | Category
    | 'meal_times'
    | 'water_times'
    | 'workout_time'
    | 'evening_review_time'
  >
>

export type NotificationStatus = 'sent' | 'deferred' | 'retrying' | 'failed' | 'suppressed' | 'cancelled' | 'pending'

export interface NotificationItem {
  id: string
  kind: string
  title: string
  body: string | null
  url: string | null
  status: NotificationStatus | string
  sent_at: string | null
  scheduled_for: string | null
  quiet_hours_suppressed: boolean
  /** short code, e.g. "quiet_hours", "stale", "delivery_failed:http_410" */
  error: string | null
  created_at: string
  updated_at: string
}
