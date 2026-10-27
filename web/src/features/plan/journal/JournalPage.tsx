/** /plan/journal — the daily review loads as its own chunk on the first visit; the skeleton stays here. */
import { lazy, Suspense } from 'react'

import { Skeleton, SkeletonCard } from '@/components/ui'

const JournalScreen = lazy(() => import('./JournalScreen'))

export function JournalPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-4" aria-hidden="true">
          <Skeleton className="h-12" />
          <Skeleton className="h-16" />
          <SkeletonCard lines={3} />
          <SkeletonCard lines={4} />
        </div>
      }
    >
      <JournalScreen />
    </Suspense>
  )
}
