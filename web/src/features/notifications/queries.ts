/**
 * Notification queries & mutations (stage 7). Every key lives under
 * `['notifications', …]`; one invalidation refreshes the whole settings screen.
 */
import { useCallback, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { pushStatus, type PushStatus } from '@/lib/push'
import { notificationsApi } from './api'
import type { NotificationPrefs, NotificationPrefsIn } from './types'

export const notificationKeys = {
  all: ['notifications'] as const,
  config: ['notifications', 'push-config'] as const,
  prefs: ['notifications', 'prefs'] as const,
  subscriptions: ['notifications', 'subscriptions'] as const,
  history: ['notifications', 'history'] as const,
}

export function usePushConfig() {
  return useQuery({ queryKey: notificationKeys.config, queryFn: notificationsApi.pushConfig, staleTime: 5 * 60_000 })
}

export function useNotificationPrefs() {
  return useQuery({ queryKey: notificationKeys.prefs, queryFn: notificationsApi.prefs })
}

export function usePushSubscriptions() {
  return useQuery({
    queryKey: notificationKeys.subscriptions,
    queryFn: async () => (await notificationsApi.subscriptions()).items,
  })
}

export function useNotificationHistory(limit = 50) {
  return useQuery({
    queryKey: notificationKeys.history,
    queryFn: async () => (await notificationsApi.history(limit)).items,
  })
}

/** Optimistic: the toggle flips at once; a failure puts the old value back. */
export function useSavePrefs(onError?: (error: unknown) => void) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: NotificationPrefsIn) => notificationsApi.savePrefs(payload),
    onMutate: async (payload) => {
      await queryClient.cancelQueries({ queryKey: notificationKeys.prefs })
      const previous = queryClient.getQueryData<NotificationPrefs>(notificationKeys.prefs)
      if (previous) queryClient.setQueryData<NotificationPrefs>(notificationKeys.prefs, { ...previous, ...payload })
      return { previous }
    },
    onError: (error, _payload, context) => {
      if (context?.previous) queryClient.setQueryData(notificationKeys.prefs, context.previous)
      onError?.(error)
    },
    onSuccess: (prefs) => queryClient.setQueryData(notificationKeys.prefs, prefs),
  })
}

/**
 * This device's push status. Re-checked when the subscriptions change and when
 * the app comes back to the foreground (the permission may have been changed in
 * the iOS Settings app meanwhile).
 */
export function usePushStatus(): { status: PushStatus | 'checking'; refresh: () => void } {
  const subscriptions = usePushSubscriptions()
  const [status, setStatus] = useState<PushStatus | 'checking'>('checking')
  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => setTick((value) => value + 1), [])
  const serverHasThisDevice = (subscriptions.data ?? []).some((item) => item.is_current_device && item.is_active)
  const known = subscriptions.isSuccess || subscriptions.isError

  useEffect(() => {
    if (!known) return
    let cancelled = false
    void pushStatus(serverHasThisDevice).then((value) => {
      if (!cancelled) setStatus(value)
    })
    return () => {
      cancelled = true
    }
  }, [known, serverHasThisDevice, tick])

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [refresh])

  return { status, refresh }
}
