/**
 * Priority text colours readable in both themes. `PRIORITY_STYLES.text`
 * (shared, tuned for dark) is near-invisible as text on white for P2/P3, so
 * text uses theme tokens here: `--warn` is amber-400 in dark and a darker amber
 * in light; P3 keeps violet-300 in dark and takes `--accent-2` in light.
 * Literal class strings only (Tailwind purges anything built at runtime).
 */
import type { Priority } from '../types'

export const PRIORITY_TEXT: Record<Priority, string> = {
  1: 'text-rose-400',
  2: 'text-[rgb(var(--warn))]',
  3: 'text-violet-300 [[data-theme=light]_&]:text-[rgb(var(--accent-2))]',
  4: 'text-[rgb(var(--text-muted))]',
}
