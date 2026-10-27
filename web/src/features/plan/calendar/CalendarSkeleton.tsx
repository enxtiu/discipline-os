/** Loading placeholder for the calendar (kept out of the calendar chunk so it can be its Suspense fallback). */
import { Skeleton } from '@/components/ui'

export function CalendarSkeleton({ view = 'week' }: { view?: 'day' | 'week' | 'month' }) {
  if (view === 'month') {
    return (
      <div className="card grid grid-cols-7 gap-1 p-2" aria-hidden="true" data-testid="cal-skeleton">
        {Array.from({ length: 42 }, (_, index) => (
          <Skeleton key={index} className="h-16 rounded-lg" />
        ))}
      </div>
    )
  }
  return (
    <div className="card space-y-3 p-3" aria-hidden="true" data-testid="cal-skeleton">
      <Skeleton className="h-10" />
      {Array.from({ length: 7 }, (_, index) => (
        <div key={index} className="flex items-center gap-3">
          <Skeleton className="h-3 w-8" />
          <Skeleton className={index % 3 === 1 ? 'h-12 w-2/3' : 'h-3 w-full opacity-50'} />
        </div>
      ))}
    </div>
  )
}
