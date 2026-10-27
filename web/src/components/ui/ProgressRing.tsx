/**
 * SVG progress ring. The glow is reserved for *key* elements only (rings, XP
 * bar) per the design brief; everything else stays flat and calm.
 *
 * Caps at 100 % visually but reports the raw percentage to screen readers, so
 * "142 % of protein" is still announced correctly.
 */
import { m } from 'framer-motion'
import clsx from 'clsx'
import type { ReactNode } from 'react'

export interface RingProps {
  value: number
  max: number
  size?: number
  thickness?: number
  tone?: 'accent' | 'violet' | 'warn' | 'muted'
  label?: ReactNode
  sublabel?: ReactNode
  glow?: boolean
  className?: string
  /** Rendered inside the ring under the label (e.g. unit). */
  children?: ReactNode
}

const TONES = {
  accent: { stroke: '#38E0C8', glow: 'rgb(56 224 200 / .45)' },
  violet: { stroke: '#8B5CF6', glow: 'rgb(139 92 246 / .45)' },
  warn: { stroke: '#FBBF24', glow: 'rgb(251 191 36 / .45)' },
  muted: { stroke: '#3B4E70', glow: 'transparent' },
} as const

export function ProgressRing({
  value,
  max,
  size = 112,
  thickness = 10,
  tone = 'accent',
  label,
  sublabel,
  glow = true,
  className,
  children,
}: RingProps) {
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0
  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - ratio)
  const colors = TONES[tone]
  const percent = max > 0 ? Math.round((value / max) * 100) : 0

  return (
    <div
      className={clsx('relative inline-flex items-center justify-center', className)}
      style={{ width: size, height: size }}
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={typeof label === 'string' ? label : undefined}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="rgb(var(--border))"
          strokeWidth={thickness}
        />
        <m.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={colors.stroke}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          style={glow ? { filter: `drop-shadow(0 0 6px ${colors.glow})` } : undefined}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-0.5 px-2 text-center">
        {label !== undefined && <span className="stat-value leading-none">{label}</span>}
        {sublabel !== undefined && (
          <span className="text-[11px] font-medium uppercase tracking-wide text-[rgb(var(--text-muted))]">
            {sublabel}
          </span>
        )}
        {children}
      </div>
    </div>
  )
}

/**
 * Compact multi-ring cluster used at the top of the Today screen: calories are
 * the hero ring, macros/fiber/water orbit it as a grid of small rings.
 */
export function RingCluster({
  rings,
  className,
}: {
  rings: {
    key: string
    value: number
    max: number
    label: string
    title: string
    tone?: RingProps['tone']
    unit?: string
  }[]
  className?: string
}) {
  return (
    <div className={clsx('grid grid-cols-3 gap-x-2 gap-y-4 sm:grid-cols-5', className)}>
      {rings.map((ring) => (
        <div key={ring.key} className="flex flex-col items-center gap-2">
          <ProgressRing
            size={76}
            thickness={7}
            value={ring.value}
            max={ring.max}
            tone={ring.tone}
            label={Math.round(ring.value)}
            glow={false}
          />
          <div className="text-center">
            <p className="text-xs font-semibold leading-tight">{ring.title}</p>
            <p className="text-[10px] uppercase tracking-wide text-[rgb(var(--text-muted))]">
              / {Math.round(ring.max)} {ring.unit ?? ''}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}
