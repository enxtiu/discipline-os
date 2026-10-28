/**
 * Lazy-loading helpers (stage 9 — performance).
 *
 * Sheets that most visits never open load on first use: render them only once
 * `useOnceTrue(open)` is true, inside a `<Suspense fallback={null}>`. They stay
 * mounted afterwards, so the close animation still plays.
 *
 * Screens that belong together (the settings hub and its sections) share one chunk:
 * `chunk(() => import(...))('Name')` gives a component that suspends only while the
 * chunk is loading — once any screen of it has loaded, the others render at once
 * (`React.lazy` alone would suspend on the first render of each of them). A tab's chunk
 * can be loaded ahead of the first tap: `whenIdle(() => tab.preload())`.
 */
import { createElement, lazy, useState, type ComponentType } from 'react'

/** False until `value` is true for the first time, then true for good. */
export function useOnceTrue(value: boolean): boolean {
  const [seen, setSeen] = useState(value)
  if (value && !seen) setSeen(true) // derived state: React re-renders at once, before committing
  return seen || value
}

export function chunk<M extends Record<string, unknown>>(load: () => Promise<M>) {
  let loaded: M | null = null
  let pending: Promise<M> | null = null
  const get = () => (pending ??= load().then((module) => (loaded = module)))
  function screen<K extends keyof M & string>(name: K): ComponentType {
    const Lazy = lazy(() => get().then((module) => ({ default: module[name] as ComponentType })))
    function ChunkScreen() {
      // decided once per mount: an instance that started lazily stays lazy (no remount)
      const [ready] = useState(() => (loaded ? (loaded[name] as ComponentType) : null))
      return createElement(ready ?? Lazy)
    }
    ChunkScreen.displayName = name
    return ChunkScreen
  }
  /** Loads the chunk ahead of its first render; a failed load is tried again on that render. */
  screen.preload = () => {
    get().catch(() => {
      if (!loaded) pending = null
    })
  }
  return screen
}

/**
 * Runs `task` once the app has been up for `delay` ms and the browser is idle (an idle
 * callback where there is one — Safari has none: then at `delay`). Returns a cancel function.
 */
export function whenIdle(task: () => void, delay = 2000): () => void {
  let idle: number | undefined
  const timer = window.setTimeout(() => {
    if (typeof window.requestIdleCallback === 'function') idle = window.requestIdleCallback(task, { timeout: 2000 })
    else task()
  }, delay)
  return () => {
    window.clearTimeout(timer)
    if (idle !== undefined) window.cancelIdleCallback(idle)
  }
}
