/**
 * Application shell: sticky header (greeting, sync status, settings), the
 * routed page, the bottom tab bar, the quick-add sheet and (stage 6) the
 * task sheets any screen can open.
 *
 * Page content is inset by `pb-tabbar` so nothing hides behind the navigation,
 * and the whole shell respects iOS safe-area insets.
 */
import { useState, type ReactNode } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { IconSettings } from '@/components/ui/icons'
import { BottomTabBar } from './BottomTabBar'
import { QuickAddSheet } from './QuickAddSheet'
import { SyncStatus } from './SyncStatus'
import { useAuth } from '@/features/auth/AuthProvider'
import { IconButton } from '@/components/ui'
import { AiCaptureProvider } from '@/features/ai/AiCapture'
import { TaskSheetHost } from '@/features/plan/tasks/TaskSheetHost'
import { useNotificationBridge } from '@/features/notifications/useNotificationBridge'

export function AppShell() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { session } = useAuth()
  const [quickAddOpen, setQuickAddOpen] = useState(false)
  useNotificationBridge() // stage 7: ?task=<id> deep links, service-worker navigation

  const greeting = greetingKey()
  const name = session?.user.display_name ?? ''

  return (
    <AiCaptureProvider>
      <div className="flex min-h-[100dvh] flex-col">
        <header className="sticky top-0 z-30 border-b border-[rgb(var(--border))]/70 bg-[rgb(var(--bg))]/85 backdrop-blur-xl">
          <div className="safe-top mx-auto flex max-w-lg items-center gap-3 px-4 pb-2.5">
            <div className="min-w-0 flex-1">
              <button
                type="button"
                onClick={() => navigate('/today')}
                className="block max-w-full truncate text-left"
              >
                <span className="block truncate text-[13px] muted">{t(greeting)}</span>
                <span className="block truncate text-[15px] font-semibold">{name}</span>
              </button>
            </div>
            <SyncStatus />
            <IconButton
              label={t('nav.settings')}
              icon={<IconSettings size={20} />}
              onClick={() => navigate('/settings')}
              variant="secondary"
              className="rounded-xl"
            />
          </div>
        </header>

        <main className="mx-auto w-full max-w-lg flex-1 px-4 pt-4 pb-tabbar">
          <Outlet />
        </main>

        <BottomTabBar onQuickAdd={() => setQuickAddOpen(true)} />
        <QuickAddSheet open={quickAddOpen} onClose={() => setQuickAddOpen(false)} />
        {/* stage 6: task editor + quick add, opened from any screen (features/plan/events.ts) */}
        <TaskSheetHost />
      </div>
    </AiCaptureProvider>
  )
}

/** A plain page wrapper for screens that are not inside the tab shell. */
export function BareShell({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-lg px-4 pt-4 pb-tabbar">{children}</div>
}

function greetingKey(): string {
  const hour = new Date().getHours()
  if (hour < 5) return 'today.greeting_evening'
  if (hour < 12) return 'today.greeting_morning'
  if (hour < 18) return 'today.greeting_day'
  return 'today.greeting_evening'
}
