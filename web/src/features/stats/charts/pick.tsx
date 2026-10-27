/**
 * Picking a day (or week) on a chart — the tooltip that works by tap.
 *
 * Recharts hands every pointer, mouse and touch event on the chart the slot
 * under it (`activeTooltipIndex`, `activeCoordinate`): a tap selects a day, a
 * finger dragging across the chart scrubs, a mouse hovering follows; leaving
 * with the mouse or tapping anywhere outside the chart closes it. The readout
 * box sits at the top of the plot on the side away from the finger, so the
 * finger never covers it.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { CategoricalChartState } from 'recharts/types/chart/types'

const TOUCH_GRACE_MS = 1000

export interface Picked {
  index: number
  /** the slot's centre in px from the chart's left edge */
  x: number
}

export function useChartPick(resetKey: string) {
  const [picked, setPicked] = useState<Picked | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  // another period or another series: the old slot means nothing any more
  useEffect(() => setPicked(null), [resetKey])

  useEffect(() => {
    if (!picked) return
    const close = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setPicked(null)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [picked])

  // after a tap, browsers emulate a mouse that leaves at once: that must not close the readout
  const touchedAt = useRef(0)

  const pick = (state: CategoricalChartState | null) => {
    const index = state?.activeTooltipIndex
    const x = state?.activeCoordinate?.x
    if (index == null || index < 0 || x == null) return
    setPicked((current) => (current?.index === index && current.x === x ? current : { index, x }))
  }
  const touch = (state: CategoricalChartState | null) => {
    touchedAt.current = Date.now()
    pick(state)
  }

  return {
    picked,
    ref,
    handlers: {
      onMouseMove: pick,
      onMouseDown: pick,
      onTouchStart: touch,
      onTouchMove: touch,
      onMouseLeave: () => {
        if (Date.now() - touchedAt.current > TOUCH_GRACE_MS) setPicked(null)
      },
    },
  }
}

/** The readout box: at the top of the plot, on the side of the slot away from the finger. */
export function TipBox({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const right = x > width / 2
  return (
    <div
      className="pointer-events-none absolute top-0 z-10 min-w-[9rem] max-w-[15rem] rounded-xl border border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))] px-3 py-2 text-xs shadow-card"
      style={right ? { right: Math.max(4, width - x + 10) } : { left: Math.max(4, x + 10) }}
      data-testid="chart-tooltip"
    >
      {children}
    </div>
  )
}
