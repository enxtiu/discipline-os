/** /plan/time — the time log loads as its own chunk on the first visit; the skeleton stays here. */
import { lazy, Suspense } from 'react'

import { Skeleton, SkeletonCard } from '@/components/ui'

const TimeScreen = lazy(() => import('./TimeScreen'))

export function TimePage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-4" aria-hidden="true">
          <Skeleton className="h-11" />
          <SkeletonCard lines={4} />
          <SkeletonCard lines={3} />
        </div>
      }
    >
      <TimeScreen />
    </Suspense>
  )
}
