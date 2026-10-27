/**
 * Entry point: theme before first paint, i18n, TanStack Query, router,
 * toast provider, service-worker registration and the offline sync loop.
 *
 * Stage 9: the first render waits for the active language (one locale chunk,
 * preloaded by index.html) so no key ever flashes; the animation features of
 * framer-motion load alongside (`MotionProvider`).
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { registerSW } from 'virtual:pwa-register'

import App from './App'
import { queryClient } from '@/lib/queryClient'
import { ToastProvider } from '@/components/ui/Toast'
import { applyTheme, storedTheme } from '@/lib/device'
import { installOfflineSync } from '@/lib/offlineQueue'
import { installQueueRefresh } from '@/lib/queueRefresh'
import { MotionProvider } from '@/lib/motion'
import { i18nReady } from '@/i18n'
import '@/styles/index.css'

// Theme first — avoids a flash of the wrong palette.
applyTheme(storedTheme())

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

void i18nReady.then(() =>
  createRoot(container).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <MotionProvider>
            <ToastProvider>
              <App />
            </ToastProvider>
          </MotionProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  ),
)
