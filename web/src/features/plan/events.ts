/**
 * Cross-screen planner actions without prop drilling.
 *
 * Any screen (calendar, search, Today, time log) can ask the planner shell to
 * open the task editor or the quick-add sheet; `TaskSheetHost` (mounted once by
 * `PlanLayout` and by `TodayPage`) listens and shows the sheet.
 */
import type { TaskIn } from './types'

const OPEN_TASK = 'dsos:plan:open-task'
const NEW_TASK = 'dsos:plan:new-task'
const QUICK_ADD = 'dsos:plan:quick-add'

export interface QuickAddRequest {
  /** Prefilled text, e.g. "+Ремонт " from a project screen. */
  text?: string
  defaults?: { project_id?: string; area_id?: string; due_date?: string; parent_id?: string }
}

/** Open the full editor for an existing task. */
export function openTask(id: string): void {
  window.dispatchEvent(new CustomEvent<string>(OPEN_TASK, { detail: id }))
}

/** Open the full editor for a new task, optionally prefilled (e.g. a calendar slot). */
export function newTask(prefill: Partial<TaskIn> = {}): void {
  window.dispatchEvent(new CustomEvent<Partial<TaskIn>>(NEW_TASK, { detail: prefill }))
}

/** Open the natural-language quick-add sheet. */
export function quickAdd(request: QuickAddRequest = {}): void {
  window.dispatchEvent(new CustomEvent<QuickAddRequest>(QUICK_ADD, { detail: request }))
}

function listen<T>(name: string, handler: (detail: T) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<T>).detail)
  window.addEventListener(name, listener)
  return () => window.removeEventListener(name, listener)
}

export const onOpenTask = (handler: (id: string) => void) => listen<string>(OPEN_TASK, handler)
export const onNewTask = (handler: (prefill: Partial<TaskIn>) => void) => listen<Partial<TaskIn>>(NEW_TASK, handler)
export const onQuickAdd = (handler: (request: QuickAddRequest) => void) => listen<QuickAddRequest>(QUICK_ADD, handler)
