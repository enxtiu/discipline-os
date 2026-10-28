/**
 * Appearance (stage 10): the theme ("skin"), the light / dark / system mode, the
 * skin's own parameters and two switches for every theme. Per device — kept in
 * localStorage next to the device token (no server sync yet, see docs/stage-10-part1.md).
 *
 * Everything is applied as data attributes on <html>, which the stylesheets key on:
 *
 *   data-theme   dark | light        the resolved mode (as since stage 1; `system` follows the OS)
 *   data-skin    evolution | stickers
 *   data-ground  lilac | mint | peach | lemonade   (stickers, light mode; dark uses the night set)
 *   data-tilt    on | off            (stickers: badges may lean −3°…+3°)
 *   data-shadow  hard | soft         (stickers)
 *   data-text    normal | large
 *   data-motion  full | reduce       (the setting or the OS preference)
 *
 * `index.html` runs the same rules inline before the stylesheet and the app load (no flash
 * of the wrong theme); `installAppearance()` re-applies them with this module and keeps
 * them live (system mode, the OS motion preference, another tab). Keep both in step.
 */
import { useSyncExternalStore } from 'react'

import { THEME_KEY } from './device'

/** Themes that can be chosen today. */
export const SKINS = ['evolution', 'stickers'] as const
export type Skin = (typeof SKINS)[number]
/** Themes shown in the gallery as «Скоро» (they come in later parts of stage 10). */
export const RESERVED_SKINS = ['forge', 'clarity', 'pulse', 'notebook', 'ios', 'gentle', 'glass'] as const
export type ReservedSkin = (typeof RESERVED_SKINS)[number]

export const MODES = ['light', 'dark', 'system'] as const
export type Mode = (typeof MODES)[number]
export type ResolvedTheme = 'dark' | 'light'

/** «Набор цветов» of the stickers theme (light mode). */
export const GROUNDS = ['lilac', 'mint', 'peach', 'lemonade'] as const
export type Ground = (typeof GROUNDS)[number]

export const SHADOWS = ['hard', 'soft'] as const
export type Shadow = (typeof SHADOWS)[number]

export interface Appearance {
  skin: Skin
  mode: Mode
  ground: Ground
  tilt: boolean
  shadow: Shadow
  largeText: boolean
  reduceMotion: boolean
}

export const DEFAULT_APPEARANCE: Appearance = {
  skin: 'evolution',
  mode: 'dark',
  ground: 'lilac',
  tilt: true,
  shadow: 'hard',
  largeText: false,
  reduceMotion: false,
}

/** Everything except the mode (the mode keeps its stage-1 key, `dsos.theme`). */
export const APPEARANCE_KEY = 'dsos.appearance'

/**
 * The page background of each theme and mode: `<meta name="theme-color">` (the iOS
 * status bar area) and the boot screen in index.html use the same values as the tokens.
 */
export const GROUND_HEX: Record<Ground, string> = {
  lilac: '#E8EAFB',
  mint: '#DBF1E5',
  peach: '#FBE6DC',
  lemonade: '#F0EFD2',
}
const NIGHT_HEX = '#17161F'
const EVOLUTION_HEX: Record<ResolvedTheme, string> = { dark: '#0B1220', light: '#F6F8FC' }

export function pageColor(appearance: Appearance, theme: ResolvedTheme): string {
  if (appearance.skin === 'stickers') return theme === 'dark' ? NIGHT_HEX : GROUND_HEX[appearance.ground]
  return EVOLUTION_HEX[theme]
}

// --------------------------------------------------------------------------- //
// Storage
// --------------------------------------------------------------------------- //
function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : fallback
}

export function readAppearance(): Appearance {
  let mode: Mode = DEFAULT_APPEARANCE.mode
  let stored: Record<string, unknown> = {}
  try {
    mode = pick(window.localStorage.getItem(THEME_KEY), MODES, DEFAULT_APPEARANCE.mode)
    const raw = window.localStorage.getItem(APPEARANCE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object') stored = parsed as Record<string, unknown>
  } catch {
    /* private mode or a damaged value: the defaults */
  }
  return {
    skin: pick(stored.skin, SKINS, DEFAULT_APPEARANCE.skin),
    mode,
    ground: pick(stored.ground, GROUNDS, DEFAULT_APPEARANCE.ground),
    tilt: typeof stored.tilt === 'boolean' ? stored.tilt : DEFAULT_APPEARANCE.tilt,
    shadow: pick(stored.shadow, SHADOWS, DEFAULT_APPEARANCE.shadow),
    largeText: stored.largeText === true,
    reduceMotion: stored.reduceMotion === true,
  }
}

function writeAppearance(appearance: Appearance): void {
  try {
    window.localStorage.setItem(THEME_KEY, appearance.mode)
    const { mode: _mode, ...rest } = appearance
    window.localStorage.setItem(APPEARANCE_KEY, JSON.stringify(rest))
  } catch {
    /* private mode: the choice holds for this tab */
  }
}

// --------------------------------------------------------------------------- //
// Applying
// --------------------------------------------------------------------------- //
const DARK_QUERY = '(prefers-color-scheme: dark)'
const REDUCED_QUERY = '(prefers-reduced-motion: reduce)'

function media(query: string): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query) : null
}

export function resolveTheme(mode: Mode): ResolvedTheme {
  if (mode === 'system') return media(DARK_QUERY)?.matches === false ? 'light' : 'dark'
  return mode
}

/** The OS asks for less motion. */
export function systemReducedMotion(): boolean {
  return !!media(REDUCED_QUERY)?.matches
}

export function applyAppearance(appearance: Appearance): void {
  const root = document.documentElement
  const theme = resolveTheme(appearance.mode)
  root.dataset.theme = theme
  root.classList.toggle('dark', theme === 'dark')
  root.dataset.skin = appearance.skin
  root.dataset.ground = appearance.ground
  root.dataset.tilt = appearance.tilt ? 'on' : 'off'
  root.dataset.shadow = appearance.shadow
  root.dataset.text = appearance.largeText ? 'large' : 'normal'
  root.dataset.motion = appearance.reduceMotion || systemReducedMotion() ? 'reduce' : 'full'
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', pageColor(appearance, theme))
}

// --------------------------------------------------------------------------- //
// The store (one per page)
// --------------------------------------------------------------------------- //
let current: Appearance | null = null
const listeners = new Set<() => void>()

export function getAppearance(): Appearance {
  if (!current) current = typeof window === 'undefined' ? DEFAULT_APPEARANCE : readAppearance()
  return current
}

function emit(): void {
  for (const listener of listeners) listener()
}

export function subscribeAppearance(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Change, store and apply (the whole page repaints at once). */
export function setAppearance(patch: Partial<Appearance>): void {
  const next = { ...getAppearance(), ...patch }
  current = next
  writeAppearance(next)
  applyAppearance(next)
  emit()
}

let installed = false

/**
 * Apply before the first render and keep it live: the OS dark/light switch (system mode),
 * the OS motion preference, and a change made in another tab of the app.
 */
export function installAppearance(): void {
  current = readAppearance()
  applyAppearance(current)
  if (installed || typeof window === 'undefined') return
  installed = true
  const reapply = () => {
    // a new snapshot, so subscribers re-read what the OS now prefers
    current = { ...getAppearance() }
    applyAppearance(current)
    emit()
  }
  media(DARK_QUERY)?.addEventListener?.('change', reapply)
  media(REDUCED_QUERY)?.addEventListener?.('change', reapply)
  window.addEventListener('storage', (event) => {
    if (event.key !== THEME_KEY && event.key !== APPEARANCE_KEY && event.key !== null) return
    current = readAppearance()
    applyAppearance(current)
    emit()
  })
}

// --------------------------------------------------------------------------- //
// Skin traits: the few widgets a theme draws in another form
// --------------------------------------------------------------------------- //
/**
 * Almost everything a theme changes is CSS (tokens and the shared classes). These are
 * the exceptions — a form CSS cannot reshape (a ring cannot become a bar) — so a
 * component asks for its form instead of checking the theme's name. «Эволюция» keeps
 * the stage-1–9 forms.
 */
export interface SkinTraits {
  /** Today's calories: the ring with three numbers under it, or a sticker with a bar */
  calorieHero: 'ring' | 'sticker'
  /** Today's macros: four small rings, or four labelled bars */
  macros: 'rings' | 'bars'
  /** Today's water and steps: inside the macros card, or two tiles of their own */
  waterSteps: 'inline' | 'tiles'
  /** the level as a round medal with the number (else a small «ур. N» chip) */
  levelMedal: boolean
  /** the quick add: a round «+» over the Stats tab, or a «＋ Записать» sticker floating above the bar */
  addButton: 'round' | 'sticker'
  /** Статистика: the achievements earned this week as a strip of stickers */
  weekStickers: boolean
}

export const SKIN_TRAITS: Record<Skin, SkinTraits> = {
  evolution: { calorieHero: 'ring', macros: 'rings', waterSteps: 'inline', levelMedal: false, addButton: 'round', weekStickers: false },
  stickers: { calorieHero: 'sticker', macros: 'bars', waterSteps: 'tiles', levelMedal: true, addButton: 'sticker', weekStickers: true },
}

// --------------------------------------------------------------------------- //
// React
// --------------------------------------------------------------------------- //
export function useAppearance(): Appearance {
  return useSyncExternalStore(subscribeAppearance, getAppearance, getAppearance)
}

/** The theme in effect right now (`system` resolved), live. */
export function useResolvedTheme(): ResolvedTheme {
  return resolveTheme(useAppearance().mode)
}

/** Less motion: the «Меньше анимации» switch or the OS preference, live. */
export function useReducedMotionPref(): boolean {
  const appearance = useAppearance()
  return appearance.reduceMotion || systemReducedMotion()
}

/** The current theme's widget forms (live). */
export function useSkinTraits(): SkinTraits {
  return SKIN_TRAITS[useAppearance().skin]
}
