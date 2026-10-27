/**
 * App-wide notification plumbing, mounted once in `AppShell` (stage 7):
 *
 * - `?task=<id>` on any route opens that task (`openTask` from the planner's
 *   events) and the parameter is dropped, so a reload does not reopen it —
 *   a push reminder links to `/plan?task=<id>`;
 * - `dsos:navigate` from the service worker (a notification tapped while the app
 *   was open) navigates in place instead of reloading;
 * - `push-subscription-changed` from the service worker registers the browser's
 *   new subscription with the API.
 */
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { openTask } from '@/features/plan/events'
import { reRegisterPush } from '@/lib/push'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function useNotificationBridge(): void {
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const taskId = params.get('task')
    if (taskId === null) return
    if (UUID.test(taskId)) openTask(taskId)
    params.delete('task')
    const search = params.toString()
    navigate({ pathname: location.pathname, search: search ? `?${search}` : '', hash: location.hash }, { replace: true })
  }, [location.pathname, location.search, location.hash, navigate])

  useEffect(() => {
    if (!('serviceWorker' in navigator)) return
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; url?: unknown } | null
      if (data?.type === 'dsos:navigate' && typeof data.url === 'string' && /^\/(?!\/)/.test(data.url)) {
        navigate(data.url)
      } else if (data?.type === 'push-subscription-changed') {
        void reRegisterPush().catch(() => undefined) // best effort: Settings shows the state either way
      }
    }
    navigator.serviceWorker.addEventListener('message', onMessage)
    return () => navigator.serviceWorker.removeEventListener('message', onMessage)
  }, [navigate])
}
