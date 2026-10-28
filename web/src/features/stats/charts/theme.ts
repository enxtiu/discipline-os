/**
 * Chart colours from the design tokens (`styles/index.css`), read through
 * `getComputedStyle` so Recharts (SVG attributes, where `var()` does not
 * resolve) gets real colours — and re-read whenever the theme attribute on
 * `<html>` changes, so dark ↔ light repaints every chart at once.
 *
 * Roles, not hues: a re-skin changes the tokens, never this file. Stage 10: each role
 * has a `--chart-*` token of its own, so a theme (`data-skin`) can colour the roles
 * differently from its UI accents; in «Эволюция» they are the stage-8 values.
 *  - `food`   kcal bars in the zone, done tasks (`--chart-food` = `--accent`)
 *  - `move`   steps, workouts, muscle volume, active energy (`--chart-move` = `--accent-2`)
 *  - `score`  the daily score (`--chart-score` = `--accent`)
 *  - `xp`     XP per day (`--chart-xp` = `--accent-2`)
 *  - `burn`   calories burned in workouts (`--chart-burn`)
 *  - `sleep`  sleep bars, freeze days (`--chart-sleep`)
 *  - `rest`   de-emphasised marks: outside the zone, below a target (`--chart-rest` = `--text-muted`)
 *  - `ink` / `muted`  text; `grid` hairlines (`--chart-grid` = `--border`); `surface` the card
 *  - `outline` a theme that draws marks with an outline (stickers): its colour and width
 *    (`--chart-outline`, `--chart-outline-width`); `null` in «Эволюция»
 */
import { useEffect, useState } from 'react'

import { useReducedMotionPref } from '@/lib/appearance'

export interface ChartTheme {
  dark: boolean
  food: string
  move: string
  score: string
  xp: string
  burn: string
  sleep: string
  rest: string
  ink: string
  muted: string
  grid: string
  surface: string
  sunken: string
  /** bars and dots drawn with an outline (stickers): its colour, else null */
  outline: string | null
  outlineWidth: number
  /** `alpha(theme.food, 0.12)` → `rgba(…, 0.12)` */
  alpha: (color: string, opacity: number) => string
}

const FALLBACK: Record<string, string> = {
  '--chart-food': '56 224 200',
  '--chart-move': '161 130 249',
  '--chart-score': '56 224 200',
  '--chart-xp': '161 130 249',
  '--chart-rest': '138 151 176',
  '--chart-grid': '31 44 69',
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
  const outline = style?.getPropertyValue('--chart-outline').trim() ?? ''
  const outlineWidth = parseFloat(style?.getPropertyValue('--chart-outline-width') ?? '')
  return {
    dark: typeof document === 'undefined' || document.documentElement.dataset.theme !== 'light',
    food: rgb('--chart-food'),
    move: rgb('--chart-move'),
    score: rgb('--chart-score'),
    xp: rgb('--chart-xp'),
    burn: rgb('--chart-burn'),
    sleep: rgb('--chart-sleep'),
    rest: rgb('--chart-rest'),
    ink: rgb('--text'),
    muted: rgb('--text-muted'),
    grid: rgb('--chart-grid'),
    surface: rgb('--bg-elevated'),
    sunken: rgb('--bg-sunken'),
    outline: outline && outline !== 'none' ? `rgb(${channels(style, '--chart-outline')})` : null,
    outlineWidth: Number.isFinite(outlineWidth) && outlineWidth > 0 ? outlineWidth : 2,
    alpha,
  }
}

export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState(readChartTheme)
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(readChartTheme()))
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class', 'data-skin', 'data-ground'],
    })
    return () => observer.disconnect()
  }, [])
  return theme
}

/** Less motion, live: the OS preference or «Меньше анимации» (charts skip their grow-in animation). */
export const useReducedMotion = useReducedMotionPref
