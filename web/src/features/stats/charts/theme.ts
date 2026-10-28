/**
 * Chart colours from the design tokens (`styles/index.css`), read through
 * `getComputedStyle` so Recharts (SVG attributes, where `var()` does not
 * resolve) gets real colours — and re-read whenever the theme attribute on
 * `<html>` changes, so dark ↔ light repaints every chart at once.
 *
 * Roles, not hues: a re-skin changes the tokens, never this file.
 *  - `food`   kcal bars in the zone, done tasks, the daily score (`--accent`)
 *  - `move`   steps, workouts, muscle volume, XP, active energy (`--accent-2`)
 *  - `burn`   calories burned in workouts (`--chart-burn`)
 *  - `sleep`  sleep bars, freeze days (`--chart-sleep`)
 *  - `rest`   de-emphasised marks: outside the zone, below a target (`--text-muted`)
 *  - `ink` / `muted`  text; `grid` hairlines; `surface` the card behind the marks
 */
import { useEffect, useState } from 'react'

import { useReducedMotionPref } from '@/lib/appearance'

export interface ChartTheme {
  dark: boolean
  food: string
  move: string
  burn: string
  sleep: string
  rest: string
  ink: string
  muted: string
  grid: string
  surface: string
  sunken: string
  /** `alpha(theme.food, 0.12)` → `rgba(…, 0.12)` */
  alpha: (color: string, opacity: number) => string
}

const FALLBACK: Record<string, string> = {
  '--accent': '56 224 200',
  '--accent-2': '161 130 249',
  '--chart-burn': '201 122 6',
  '--chart-sleep': '96 165 250',
  '--text': '232 238 247',
  '--text-muted': '138 151 176',
  '--border': '31 44 69',
  '--bg-elevated': '17 26 43',
  '--bg-sunken': '8 13 22',
}

function channels(style: CSSStyleDeclaration | null, name: string): string {
  const raw = style?.getPropertyValue(name).trim() || FALLBACK[name]
  return raw.split(/[\s,]+/).filter(Boolean).slice(0, 3).join(', ')
}

function alpha(color: string, opacity: number): string {
  const match = color.match(/^rgb\((.+)\)$/)
  return match ? `rgba(${match[1]}, ${opacity})` : color
}

export function readChartTheme(): ChartTheme {
  const style = typeof window === 'undefined' ? null : getComputedStyle(document.documentElement)
  const rgb = (name: string) => `rgb(${channels(style, name)})`
  return {
    dark: typeof document === 'undefined' || document.documentElement.dataset.theme !== 'light',
    food: rgb('--accent'),
    move: rgb('--accent-2'),
    burn: rgb('--chart-burn'),
    sleep: rgb('--chart-sleep'),
    rest: rgb('--text-muted'),
    ink: rgb('--text'),
    muted: rgb('--text-muted'),
    grid: rgb('--border'),
    surface: rgb('--bg-elevated'),
    sunken: rgb('--bg-sunken'),
    alpha,
  }
}

export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState(readChartTheme)
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(readChartTheme()))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] })
    return () => observer.disconnect()
  }, [])
  return theme
}

/** Less motion, live: the OS preference or «Меньше анимации» (charts skip their grow-in animation). */
export const useReducedMotion = useReducedMotionPref
