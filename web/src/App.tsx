/**
 * Routing & guards.
 *
 *   unauthenticated -> PairingScreen (the only reachable screen)
 *   authenticated   -> AppShell with the five tabs; /settings renders outside
 *                      the tab layout but keeps the same styling
 *
 * The onboarding wizard shows up after pairing until `onboarding_completed` is
 * true (skippable — the user can also live with defaults and set things later).
 */
import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { AppShell } from '@/components/layout/AppShell'
import { Spinner } from '@/components/ui'
import { AuthProvider, useAuth } from '@/features/auth/AuthProvider'
import { TodayPage } from '@/features/today/TodayPage'
import { chunk, whenIdle } from '@/lib/lazy'
// stage 9: what a paired phone does not need for its first screen loads on demand —
// pairing and the onboarding wizard are chunks of their own; the settings hub and its
// sections are one chunk (moving between sections never waits once it is there)
const PairingScreen = lazy(() => import('@/features/auth/PairingScreen').then((module) => ({ default: module.PairingScreen })))
const OnboardingWizard = lazy(() =>
  import('@/features/auth/OnboardingWizard').then((module) => ({ default: module.OnboardingWizard })),
)
const settings = chunk(() => import('@/features/settings/sections'))
const SettingsPage = settings('SettingsPage')
const ProfileSection = settings('ProfileSection')
const AppearanceSection = settings('AppearanceSection')
const NotificationsSection = settings('NotificationsSection')
const DevicesSection = settings('DevicesSection')
const TokensSection = settings('TokensSection')
const ShortcutSection = settings('ShortcutSection')
const DataSection = settings('DataSection')
// stage 4: the training screens load on first visit (Today stays light)
const TrainPage = lazy(() => import('@/features/train/TrainPage').then((m) => ({ default: m.TrainPage })))
const WorkoutPage = lazy(() => import('@/features/train/WorkoutPage').then((m) => ({ default: m.WorkoutPage })))
const HealthPage = lazy(() => import('@/features/health/HealthPage').then((m) => ({ default: m.HealthPage })))
// stage 6: the planner is one lazy chunk with its own nested routes
const plan = chunk(() => import('@/features/plan/PlanRoutes'))
const PlanRoutes = plan('default')
// stage 8: the Stats tab (dashboards, achievements) is one lazy chunk with nested routes
const StatsRoutes = lazy(() => import('@/features/stats/StatsRoutes'))
// stage 9: the Food tab (and its log, recipe and custom-food sheets) is a chunk of its own;
// Food's and Plan's chunks load once the app is up and idle (prefetchTabs)
const food = chunk(() => import('@/features/food/FoodPage'))
const FoodPage = food('FoodPage')
import { SettingsHeader } from '@/features/settings/SettingsHeader'

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}

function Shell() {
  const { status } = useAuth()

  if (status === 'loading') return <BootSplash />
  if (status === 'unauthenticated') {
    return (
      <Suspense fallback={<BootSplash />}>
        <PairingScreen />
      </Suspense>
    )
  }

  return (
    <Routes>
      <Route element={<AuthenticatedLayout />}>
        <Route path="/" element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<TodayPage />} />
        <Route path="/food" element={<Lazy><FoodPage /></Lazy>} />
        <Route path="/train" element={<Lazy><TrainPage /></Lazy>} />
        <Route path="/train/workout/:id" element={<Lazy><WorkoutPage /></Lazy>} />
        <Route path="/health" element={<Lazy><HealthPage /></Lazy>} />
        <Route path="/plan/*" element={<Lazy><PlanRoutes /></Lazy>} />
        <Route path="/stats/*" element={<Lazy><StatsRoutes /></Lazy>} />

        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<SettingsPage />} />
          <Route path="profile" element={<ProfileSection />} />
          <Route path="appearance" element={<AppearanceSection />} />
          <Route path="notifications" element={<NotificationsSection />} />
          <Route path="devices" element={<DevicesSection />} />
          <Route path="tokens" element={<TokensSection />} />
          <Route path="shortcut" element={<ShortcutSection />} />
          <Route path="data" element={<DataSection />} />
        </Route>

        <Route path="*" element={<Navigate to="/today" replace />} />
      </Route>
    </Routes>
  )
}

function AuthenticatedLayout() {
  const { session } = useAuth()
  const [forceOnboarded, setForceOnboarded] = useState(false)

  // Let the "set up targets" button in Today open the wizard again.
  useEffect(() => {
    const handler = () => setForceOnboarded(true)
    window.addEventListener('dsos:open-onboarding', handler)
    return () => window.removeEventListener('dsos:open-onboarding', handler)
  }, [])

  const needsOnboarding =
    !!session && (!session.settings.onboarding_completed || forceOnboarded)

  useEffect(() => (needsOnboarding ? undefined : prefetchTabs()), [needsOnboarding])

  if (needsOnboarding) {
    return (
      <Suspense fallback={<BootSplash />}>
        <OnboardingWizard onDone={() => setForceOnboarded(false)} />
      </Suspense>
    )
  }

  return <AppShell />
}

/**
 * Food and Plan are the tabs tapped most after Today: their code loads once the first
 * screen is up and the browser idle (from the service worker's cache once installed), so
 * the first tap shows the screen at once instead of a spinner while the chunk loads.
 * Not with Data Saver on. Returns the cancel function.
 */
function prefetchTabs(): (() => void) | undefined {
  if ((navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData) return undefined
  return whenIdle(() => {
    food.preload()
    plan.preload()
  })
}

/**
 * Settings sub-pages share a back button; the hub itself keeps the plain title.
 * Everything still renders inside `AppShell`, so the tab bar stays available.
 */
function SettingsLayout() {
  const { t } = useTranslation()
  const location = useLocation()
  return (
    <>
      {location.pathname !== '/settings' && <SettingsHeader title={t('settings.title')} />}
      <Lazy>
        <Outlet />
      </Lazy>
    </>
  )
}

function BootSplash() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-4">
      <Spinner size={28} className="text-aqua-400" />
      <p className="text-sm muted">{t('app.loading')}</p>
    </div>
  )
}

function Lazy({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      }
    >
      {children}
    </Suspense>
  )
}
