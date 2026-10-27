/**
 * The Stats tab: nested routes under /stats, one lazy chunk.
 *
 *   /stats                the dashboards (still the stage placeholder here)
 *   /stats/achievements   levels, XP, streaks, quests, achievements (stage 8, its own chunk)
 */
import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { Spinner } from '@/components/ui'
import { StatsPage } from '@/features/shared/Placeholders'

const AchievementsPage = lazy(() => import('@/features/game/AchievementsPage'))

export default function StatsRoutes() {
  return (
    <Routes>
      <Route index element={<StatsPage />} />
      <Route
        path="achievements"
        element={
          <Suspense
            fallback={
              <div className="flex justify-center py-16">
                <Spinner />
              </div>
            }
          >
            <AchievementsPage />
          </Suspense>
        }
      />
      <Route path="*" element={<Navigate to="/stats" replace />} />
    </Routes>
  )
}
