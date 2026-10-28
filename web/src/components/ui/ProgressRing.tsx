/**
 * SVG progress ring. The glow is reserved for *key* elements only (rings, XP
 * bar) per the design brief; everything else stays flat and calm.
 *
 * Caps at 100 % visually but reports the raw percentage to screen readers, so
 * "142 % of protein" is still announced correctly.
 *
 * With a `name` the ring is one progressbar for screen readers («Белки, 8 из
 * 160 г») and the text drawn inside it is hidden from them; without one the
 * circle is decoration and the text inside is read as plain text (use that
 * when a surrounding control or caption already says it all).
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
  /** Accessible name; without it the ring is decorative. */
  name?: string
  /** What a screen reader says for the value, e.g. «220 из 2 080 ккал». */
  valueText?: string
}

// strokes follow the theme tokens (a light-theme ring stays visible on white)
const TONES = {
  accent: { stroke: 'rgb(var(--accent))', glow: 'rgb(56 224 200 / .45)' },
  violet: { stroke: 'rgb(var(--accent-2))', glow: 'rgb(139 92 246 / .45)' },
  warn: { stroke: 'rgb(var(--warn))', glow: 'rgb(251 191 36 / .45)' },
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
  name,
  valueText,
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
      {...(name
        ? {
            role: 'progressbar',
            'aria-label': name,
            'aria-valuenow': percent,
            'aria-valuemin': 0,
            'aria-valuemax': 100,
            'aria-valuetext': valueText,
          }
        : {})}
    >
      <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
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
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          style={{ stroke: colors.stroke, ...(glow ? { filter: `drop-shadow(0 0 6px ${colors.glow})` } : {}) }}
        />
      </svg>
      <div
        className="absolute inset-0 flex flex-col items-center justify-center gap-0.5 px-2 text-center"
        aria-hidden={name ? true : undefined}
      >
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
            name={ring.title}
            valueText={`${Math.round(ring.value)} / ${Math.round(ring.max)} ${ring.unit ?? ''}`.trim()}
          />
          <div className="text-center" aria-hidden="true">
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
