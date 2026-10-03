/**
 * Runtime helpers for the (server-computed) nutrition targets.
 *
 * Targets live on the server: the client never derives them, it only renders
 * them and asks for a preview while the user edits body params. The empty
 * value below exists so the pairing response can prime the query cache with a
 * structurally valid object before the first session fetch lands.
 */
import type { Targets } from '@/api/types'

/**
 * Placeholder used only to prime the query cache before the first session
 * fetch: `target_kcal === null` means "the server has not computed targets
 * yet", and every screen shows a skeleton in that case.
 */
export const EMPTY_TARGETS: Targets = {
  formula_used: 'none',
  safe_floor_kcal: 0,
  is_manual: false,
  warnings: [],
  macros_kcal: {},
}

/** `true` when the server has enough body params to compute real targets. */
export function hasTargets(targets: Targets | undefined): boolean {
  return Boolean(targets?.target_kcal)
}
