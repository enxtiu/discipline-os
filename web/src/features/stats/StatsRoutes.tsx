/**
 * The Stats tab: nested routes under /stats, each screen its own lazy chunk
 * (the dashboards carry Recharts; the achievements screen does not need it).
 *
 *   /stats                the dashboards: nutrition, activity, body, planner, progress
 *   /stats/achievements   levels, XP, streaks, quests, achievements (stage 8)
 */
import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { Spinner } from '@/components/ui'

const StatsPage = lazy(() => import('./StatsPage'))
const AchievementsPage = lazy(() => import('@/features/game/AchievementsPage'))

export default function StatsRoutes() {
  return (
    <Routes>
      <Route
        index
        element={
          <Lazy>
            <StatsPage />
          </Lazy>
        }
      />
      <Route
        path="achievements"
        element={
          <Lazy>
            <AchievementsPage />
          </Lazy>
        }
      />
      <Route path="*" element={<Navigate to="/stats" replace />} />
    </Routes>
  )
}

function Lazy({ children }: { children: ReactNode }) {
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
