/**
 * framer-motion, loaded in two parts (stage 9 — performance).
 *
 * Components render `m.*` (the slim motion component) instead of `motion.*`; the
 * animation features — domMax: animate/exit, tap and hover, drag (the sheets) and
 * layout (the planner toast) — are a chunk of their own, requested as soon as the
 * app code runs, so it arrives in parallel with the session and the first API
 * reads. Until it is there an `m.*` element shows its `initial` state. Every
 * animation and the reduced-motion handling (MotionConfig, useReducedMotion, the
 * CSS media query) are unchanged.
 *
 * Use `m` from 'framer-motion' in new code: a `motion.*` component still works
 * under LazyMotion but carries the full feature bundle into its chunk again
 * (`node scripts/perf/bundle-report.mjs --check` reports it).
 */
import { LazyMotion } from 'framer-motion'
import type { ReactNode } from 'react'

const features = import('./motionFeatures').then((module) => module.default)

export function MotionProvider({ children }: { children: ReactNode }) {
  return <LazyMotion features={() => features}>{children}</LazyMotion>
}
