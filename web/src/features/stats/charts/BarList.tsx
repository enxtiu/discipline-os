/**
 * A horizontal bar list (HTML, no chart library): one row per item with its
 * name, its value in words and a bar on a shared scale. An optional target
 * draws a tick on the same scale (a bullet bar), so «3 ч из 5 ч» reads at a
 * glance and every row stays comparable with the others.
 */
import type { ReactNode } from 'react'
import clsx from 'clsx'

export interface BarListItem {
  key: string
  label: ReactNode
  value: number
  /** the value in words, right-aligned (e.g. «4 200 кг · 12 подх.») */
  text: string
  target?: number | null
}

export function BarList({
  items,
  colorClass,
  testId,
}: {
  items: BarListItem[]
  /** a literal Tailwind class for the bar fill (purge-safe), e.g. `bg-[rgb(var(--accent-2))]` */
  colorClass: string
  testId?: string
}) {
  const scale = Math.max(1, ...items.map((item) => Math.max(item.value, item.target ?? 0)))
  return (
    <ul className="space-y-2.5" data-testid={testId}>
      {items.map((item) => {
        const width = (item.value / scale) * 100
        const tick = item.target ? (item.target / scale) * 100 : null
        return (
          <li key={item.key} className="space-y-1" data-key={item.key} data-value={item.value}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="flex min-w-0 items-center gap-2 truncate font-medium">{item.label}</span>
              <span className="tnum shrink-0 text-xs muted">{item.text}</span>
            </div>
            <div className="relative h-2 rounded-pill bg-[rgb(var(--bg-sunken))]" aria-hidden="true">
              <div
                className={clsx('absolute inset-y-0 left-0 rounded-pill', colorClass)}
                style={{ width: `${Math.max(item.value > 0 ? 2 : 0, width)}%` }}
              />
              {tick != null && (
                <div
                  className="absolute -inset-y-1 w-0.5 rounded-pill bg-[rgb(var(--text))]/80"
                  style={{ left: `calc(${tick}% - 1px)` }}
                  data-testid="bar-target"
                />
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
