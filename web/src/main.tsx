/**
 * Entry point: theme before first paint, i18n, TanStack Query, router,
 * toast provider, service-worker registration and the offline sync loop.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'framer-motion'
import { registerSW } from 'virtual:pwa-register'

import App from './App'
import { queryClient } from '@/lib/queryClient'
import { ToastProvider } from '@/components/ui/Toast'
import { applyTheme, storedTheme } from '@/lib/device'
import { installOfflineSync } from '@/lib/offlineQueue'
import '@/i18n'
import { syncDocumentLanguage } from '@/i18n/documentLanguage'
import '@/styles/index.css'

// Theme first — avoids a flash of the wrong palette.
applyTheme(storedTheme())
// <html lang> follows the UI language (screen-reader voice, hyphenation).
syncDocumentLanguage()

// Offline queue: flush on reconnect, keep the indicator honest.
installOfflineSync()

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

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* prefers-reduced-motion: no slides, springs or layout animations anywhere */}
        <MotionConfig reducedMotion="user">
          <ToastProvider>
            <App />
          </ToastProvider>
        </MotionConfig>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
