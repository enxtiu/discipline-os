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
import { PairingScreen } from '@/features/auth/PairingScreen'
import { OnboardingWizard } from '@/features/auth/OnboardingWizard'
import { TodayPage } from '@/features/today/TodayPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { ProfileSection } from '@/features/settings/ProfileSection'
import { AppearanceSection } from '@/features/settings/AppearanceSection'
import { NotificationsSection } from '@/features/settings/NotificationsSection'
import { DevicesSection } from '@/features/settings/DevicesSection'
import { TokensSection } from '@/features/settings/TokensSection'
import { ShortcutSection } from '@/features/settings/ShortcutSection'
import { DataSection } from '@/features/settings/DataSection'
import { StatsPage } from '@/features/shared/Placeholders'
// stage 4: the training screens load on first visit (Today stays light)
const TrainPage = lazy(() => import('@/features/train/TrainPage').then((m) => ({ default: m.TrainPage })))
const WorkoutPage = lazy(() => import('@/features/train/WorkoutPage').then((m) => ({ default: m.WorkoutPage })))
const HealthPage = lazy(() => import('@/features/health/HealthPage').then((m) => ({ default: m.HealthPage })))
// stage 6: the planner is one lazy chunk with its own nested routes
const PlanRoutes = lazy(() => import('@/features/plan/PlanRoutes'))
import { FoodPage } from '@/features/food/FoodPage'
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
  if (status === 'unauthenticated') return <PairingScreen />

  return (
    <Routes>
      <Route element={<AuthenticatedLayout />}>
        <Route path="/" element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<TodayPage />} />
        <Route path="/food" element={<FoodPage />} />
        <Route path="/train" element={<Lazy><TrainPage /></Lazy>} />
        <Route path="/train/workout/:id" element={<Lazy><WorkoutPage /></Lazy>} />
        <Route path="/health" element={<Lazy><HealthPage /></Lazy>} />
        <Route path="/plan/*" element={<Lazy><PlanRoutes /></Lazy>} />
        <Route path="/stats" element={<StatsPage />} />

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

  if (needsOnboarding) {
    return <OnboardingWizard onDone={() => setForceOnboarded(false)} />
  }

  return <AppShell />
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
      <Outlet />
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
