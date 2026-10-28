/**
 * Entry point: theme before first paint, i18n, TanStack Query, router,
 * toast provider, service-worker registration and the offline sync loop.
 *
 * Stage 9: the first render waits for the active language (one locale chunk,
 * preloaded by index.html) so no key ever flashes, and — on a paired device — for
 * the session that index.html already requested, so the first render is the app
 * shell, not a spinner swapped for it a moment later (index.html shows the same
 * spinner meanwhile). The animation features of framer-motion load alongside.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'framer-motion'
import { registerSW } from 'virtual:pwa-register'

import App from './App'
import { queryClient, queryKeys } from '@/lib/queryClient'
import { endpoints } from '@/api/client'
import { ToastProvider } from '@/components/ui/Toast'
import { applyTheme, hasToken, storedTheme } from '@/lib/device'
import { installOfflineSync } from '@/lib/offlineQueue'
import { installQueueRefresh } from '@/lib/queueRefresh'
import { MotionProvider } from '@/lib/motion'
import { i18nReady } from '@/i18n'
import { syncDocumentLanguage } from '@/i18n/documentLanguage'
import '@/styles/index.css'

// Theme first — avoids a flash of the wrong palette.
applyTheme(storedTheme())
// <html lang> follows the UI language (screen-reader voice, hyphenation).
syncDocumentLanguage()

// Offline queue: flush on reconnect, keep the indicator honest; one refresh after a flush.
installOfflineSync()
installQueueRefresh(queryClient)

// Auto-update the app shell in the background; iOS applies it on next launch.
if (import.meta.env.PROD) {
  registerSW({
    immediate: true,
    onNeedRefresh() {
      window.dispatchEvent(new CustomEvent('dsos:update-available'))
    },
  })
}

const container = document.getElementById('root')
if (!container) throw new Error('#root is missing from index.html')

/** The session in the cache before the first render (never waits longer than 3 s, never offline). */
function sessionReady(): Promise<unknown> {
  if (!hasToken() || !navigator.onLine) return Promise.resolve()
  const loaded = queryClient
    .prefetchQuery({ queryKey: queryKeys.session, queryFn: () => endpoints.session(), retry: false, staleTime: 60_000 })
    .catch(() => undefined)
  return Promise.race([loaded, new Promise((resolve) => setTimeout(resolve, 3000))])
}

void Promise.all([i18nReady, sessionReady()]).then(() =>
  createRoot(container).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <MotionProvider>
            {/* prefers-reduced-motion: no slides, springs or layout animations anywhere */}
            <MotionConfig reducedMotion="user">
              <ToastProvider>
                <App />
              </ToastProvider>
            </MotionConfig>
          </MotionProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  ),
)
