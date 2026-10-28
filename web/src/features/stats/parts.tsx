/**
 * Building blocks of the dashboard cards: the card frame with its states
 * (skeleton, error with retry, empty), the numbers row, the legend and the
 * table view every chart has for exact values.
 */
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, Card, EmptyState, ErrorState, Skeleton } from '@/components/ui'
import { IconChevronRight } from '@/components/ui/icons'

// --------------------------------------------------------------------------- //
// Card frame
// --------------------------------------------------------------------------- //
export function StatCard({
  title,
  hint,
  aside,
  children,
  testId,
  dimmed,
  className,
}: {
  title: ReactNode
  hint?: ReactNode
  aside?: ReactNode
  children: ReactNode
  testId?: string
  /** the previous period is still shown while the new one loads */
  dimmed?: boolean
  className?: string
}) {
  return (
    <Card
      className={clsx('space-y-3 transition-opacity duration-200', dimmed && 'opacity-60', className)}
      data-testid={testId}
      aria-busy={dimmed || undefined}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold leading-tight">{title}</h3>
          {hint && <p className="mt-0.5 text-xs muted">{hint}</p>}
        </div>
        {aside && <div className="shrink-0 text-right">{aside}</div>}
      </header>
      {children}
    </Card>
  )
}

export function CardSkeleton({ testId, chart = true }: { testId?: string; chart?: boolean }) {
  return (
    <Card className="space-y-3" data-testid={testId} aria-busy="true">
      <Skeleton className="h-5 w-2/5" />
      <div className="grid grid-cols-3 gap-2">
        <Skeleton className="h-11" />
        <Skeleton className="h-11" />
        <Skeleton className="h-11" />
      </div>
      {chart && <Skeleton className="h-40" />}
    </Card>
  )
}

export function CardError({ onRetry, testId }: { onRetry: () => void; testId?: string }) {
  const { t } = useTranslation()
  return (
    <div data-testid={testId}>
      <ErrorState message={t('stats.load_error')} onRetry={onRetry} retryLabel={t('app.retry')} />
    </div>
  )
}

export function CardEmpty({
  title,
  description,
  action,
  testId,
}: {
  title: string
  description?: string
  action?: { label: string; onClick: () => void }
  testId?: string
}) {
  return (
    <div data-testid={testId ?? 'stats-empty'}>
      <EmptyState
        title={title}
        description={description}
        action={
          action ? (
            <Button size="sm" variant="secondary" onClick={action.onClick}>
              {action.label}
              <IconChevronRight size={16} />
            </Button>
          ) : undefined
        }
      />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Numbers row
// --------------------------------------------------------------------------- //
export interface Tile {
  label: string
  value: string
  /** a unit set smaller after the value (kcal, kg, XP) */
  unit?: string
  sub?: string
  testId?: string
}

export function Tiles({ items }: { items: Tile[] }) {
  return (
    <dl className={clsx('grid gap-2', items.length === 3 ? 'grid-cols-3' : 'grid-cols-2')}>
      {items.map((item) => (
        <div key={item.label} className="surface-sunken min-w-0 px-2.5 py-2" data-testid={item.testId}>
          <dt className="truncate text-xs muted">{item.label}</dt>
          <dd className="mt-0.5 flex min-w-0 items-baseline gap-1 leading-tight">
            <span className="tnum truncate text-[17px] font-semibold" data-testid={item.testId ? `${item.testId}-value` : undefined}>
              {item.value}
            </span>
            {item.unit && <span className="shrink-0 text-[11px] font-medium muted">{item.unit}</span>}
          </dd>
          {item.sub && <dd className="tnum mt-0.5 truncate text-[11px] muted">{item.sub}</dd>}
        </div>
      ))}
    </dl>
  )
}

// --------------------------------------------------------------------------- //
// Legend (identity never by colour alone: a mark beside a text label)
// --------------------------------------------------------------------------- //
export type LegendMark = 'bar' | 'line' | 'dash' | 'dot' | 'band' | 'ghost' | 'cell' | 'empty' | 'outline' | 'ring'

export interface LegendItem {
  mark: LegendMark
  color: string
  label: string
}

export function Legend({ items, className }: { items: LegendItem[]; className?: string }) {
  return (
    <ul className={clsx('flex flex-wrap gap-x-3 gap-y-1 text-[11px] muted', className)} data-testid="chart-legend">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <LegendSwatch mark={item.mark} color={item.color} />
          <span>{item.label}</span>
        </li>
      ))}
    </ul>
  )
}

function LegendSwatch({ mark, color }: { mark: LegendMark; color: string }) {
  switch (mark) {
    case 'bar':
      return <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ backgroundColor: color }} />
    case 'cell':
      return <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ backgroundColor: color }} />
    case 'dot':
      return <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
    case 'line':
      return <span aria-hidden="true" className="inline-block h-0 w-3.5 border-t-2" style={{ borderColor: color }} />
    case 'dash':
      return <span aria-hidden="true" className="inline-block h-0 w-3.5 border-t-2 border-dashed" style={{ borderColor: color }} />
    case 'band':
      return <span aria-hidden="true" className="inline-block h-2.5 w-3.5 rounded-[2px] opacity-40" style={{ backgroundColor: color }} />
    case 'ghost':
      return <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[3px] border border-dashed" style={{ borderColor: color }} />
    case 'outline':
      return <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[3px] border-[1.5px]" style={{ borderColor: color }} />
    case 'ring':
      return <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full border-[1.5px]" style={{ borderColor: color }} />
    case 'empty':
      return <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-[3px] border border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))]" />
  }
}

// --------------------------------------------------------------------------- //
// Table view — the exact numbers behind a chart, keyboard and screen-reader friendly
// --------------------------------------------------------------------------- //
export interface TableColumn {
  label: string
  align?: 'left' | 'right'
}

export function DataTable({
  caption,
  columns,
  rows,
  testId,
}: {
  caption: string
  columns: TableColumn[]
  /** cells as display strings; a row is rendered only when the table is open */
  rows: () => { key: string; cells: string[]; muted?: boolean }[]
  testId?: string
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <details
      className="group -mx-1 rounded-xl"
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
      data-testid={testId}
    >
      <summary className="flex min-h-tap cursor-pointer list-none items-center gap-1.5 rounded-xl px-1 text-sm font-medium text-[rgb(var(--accent))] [&::-webkit-details-marker]:hidden">
        <IconChevronRight size={16} className="transition-transform group-open:rotate-90" />
        {open ? t('stats.table_hide') : t('stats.table_show')}
      </summary>
      {open && (
        <div className="max-h-72 overflow-auto rounded-xl border border-[rgb(var(--border))]">
          <table className="w-full text-xs">
            <caption className="sr-only">{caption}</caption>
            <thead className="sticky top-0 bg-[rgb(var(--bg-elevated))]">
              <tr>
                {columns.map((column) => (
                  <th
                    key={column.label}
                    scope="col"
                    className={clsx('px-2.5 py-2 font-medium muted', column.align === 'right' ? 'text-right' : 'text-left')}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows().map((row) => (
                <tr key={row.key} className={clsx('border-t border-[rgb(var(--border))]', row.muted && 'muted')} data-row={row.key}>
                  {row.cells.map((cell, index) => (
                    <td
                      key={index}
                      className={clsx('tnum px-2.5 py-1.5', columns[index]?.align === 'right' ? 'text-right' : 'text-left')}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  )
}

/** A screen-reader-only sentence (the visible numbers row says the same in tiles). */
export function SrOnly({ children }: { children: ReactNode }) {
  return <p className="sr-only">{children}</p>
}
