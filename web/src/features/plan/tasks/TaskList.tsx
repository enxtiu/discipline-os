/** A titled group of task rows (optionally collapsible) and its loading skeleton. */
import { useId, useState, type ReactNode } from 'react'
import clsx from 'clsx'

import { Skeleton } from '@/components/ui'
import { IconChevronRight } from '@/components/ui/icons'

export function TaskGroup({
  title,
  count,
  tone = 'default',
  action,
  collapsible = false,
  defaultOpen = true,
  testId,
  children,
}: {
  title: ReactNode
  count?: number
  tone?: 'default' | 'danger' | 'accent'
  action?: ReactNode
  collapsible?: boolean
  defaultOpen?: boolean
  testId?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  const headingId = useId()
  const label = (
    <span
      className={clsx(
        'flex items-center gap-2 text-sm font-semibold',
        tone === 'danger' && 'text-rose-400',
        tone === 'accent' && 'text-[rgb(var(--accent))]',
      )}
    >
      {title}
      {count !== undefined && (
        <span className="tnum rounded-pill bg-[rgb(var(--border))]/60 px-1.5 text-[11px] font-medium text-[rgb(var(--text-muted))]">
          {count}
        </span>
      )}
    </span>
  )
  return (
    <section className="space-y-1.5" aria-labelledby={headingId} data-testid={testId}>
      <header className="flex min-h-tap items-center justify-between gap-2 px-1">
        <h2 id={headingId} className="min-w-0 flex-1">
          {collapsible ? (
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
              className="-ml-1 flex min-h-tap w-full items-center gap-1 px-1 text-left"
            >
              <IconChevronRight
                size={16}
                className={clsx('shrink-0 text-[rgb(var(--text-muted))] transition-transform', open && 'rotate-90')}
              />
              {label}
            </button>
          ) : (
            label
          )}
        </h2>
        {action}
      </header>
      {(!collapsible || open) && (
        <div className="card divide-y divide-[rgb(var(--border))] overflow-hidden">{children}</div>
      )}
    </section>
  )
}

export function TaskListSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card divide-y divide-[rgb(var(--border))] overflow-hidden" aria-hidden="true">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-3 px-3 py-3.5">
          <Skeleton className="h-[22px] w-[22px] shrink-0 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className={index % 2 ? 'h-3.5 w-1/2' : 'h-3.5 w-3/4'} />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  )
}
