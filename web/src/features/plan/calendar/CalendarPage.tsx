/**
 * /plan/calendar. The calendar (time grid, month, drag) is its own lazy chunk
 * inside the /plan chunk, loaded on the first visit; the skeleton stays here.
 */
import { lazy, Suspense } from 'react'

import { CalendarSkeleton } from './CalendarSkeleton'

const CalendarScreen = lazy(() => import('./CalendarScreen'))

export function CalendarPage() {
  return (
    <Suspense fallback={<CalendarSkeleton />}>
      <CalendarScreen />
    </Suspense>
  )
}
