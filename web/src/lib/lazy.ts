/**
 * Lazy-loading helpers (stage 9 — performance).
 *
 * Sheets that most visits never open load on first use: render them only once
 * `useOnceTrue(open)` is true, inside a `<Suspense fallback={null}>`. They stay
 * mounted afterwards, so the close animation still plays.
 */
import { useState } from 'react'

/** False until `value` is true for the first time, then true for good. */
export function useOnceTrue(value: boolean): boolean {
  const [seen, setSeen] = useState(value)
  if (value && !seen) setSeen(true) // derived state: React re-renders at once, before committing
  return seen || value
}
