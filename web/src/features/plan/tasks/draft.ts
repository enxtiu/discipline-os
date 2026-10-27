/**
 * The task editor's draft: the server copy (or the prefill for a new task)
 * plus an overlay of the fields the user touched. Untouched fields always show
 * the latest server value (a refetch after ticking a subtask never wipes an
 * edit), and «Сохранить» sends only the fields whose value really changed.
 */
import { useState } from 'react'

import type { BlockIn, Priority, ReminderIn, TaskDetail, TaskIn, TaskPatchIn, TaskStatusIn } from '../types'
import { toReminderIn } from './reminders'

export interface Draft {
  title: string
  notes_md: string
  /** null = let the server decide (new tasks: inbox/open). */
  status: TaskStatusIn | null
  priority: Priority
  is_pinned: boolean
  project_id: string | null
  area_id: string | null
  due_date: string | null
  due_time: string | null
  block: BlockIn | null
  estimate_minutes: number | null
  /** A preset key (`weekly`) or an RRULE. */
  recurrence_rule: string | null
  recurrence_until: string | null
  reminders: ReminderIn[]
  tags: string[]
  context: string
}
export type DraftKey = keyof Draft

export function draftFromTask(task: TaskDetail): Draft {
  return {
    title: task.title,
    notes_md: task.notes_md ?? '',
    status: task.status === 'scheduled' ? 'open' : task.status,
    priority: task.priority,
    is_pinned: task.is_pinned,
    project_id: task.project?.id ?? null,
    area_id: task.area?.id ?? null,
    due_date: task.due_date,
    due_time: task.due_time,
    block: task.block ? { date: task.block.date, start: task.block.start, end: task.block.end } : null,
    estimate_minutes: task.estimate_minutes,
    recurrence_rule: task.recurrence_rule
      ? task.recurrence_preset && task.recurrence_preset !== 'custom'
        ? task.recurrence_preset
        : task.recurrence_rule
      : null,
    recurrence_until: task.recurrence_until,
    reminders: toReminderIn(task.reminders),
    tags: task.tags.map((tag) => tag.name),
    context: task.context ?? '',
  }
}

export function draftFromPrefill(prefill: Partial<TaskIn> | undefined): Draft {
  const p = prefill ?? {}
  return {
    title: p.title ?? '',
    notes_md: p.notes_md ?? '',
    status: p.status ?? null,
    priority: p.priority ?? 4,
    is_pinned: p.is_pinned ?? false,
    project_id: p.project_id ?? null,
    area_id: p.area_id ?? null,
    due_date: p.due_date ?? null,
    due_time: p.due_time ?? null,
    block: p.block ?? null,
    estimate_minutes: p.estimate_minutes ?? null,
    recurrence_rule: p.recurrence_rule ?? null,
    recurrence_until: p.recurrence_until ?? null,
    reminders: p.reminders ?? [],
    tags: p.tags ?? [],
    context: p.context ?? '',
  }
}

export function cleanContext(value: string): string | null {
  const trimmed = value.trim().replace(/^@+/, '').trim()
  return trimmed || null
}

/** The value the API receives for a field. */
function payload(key: DraftKey, draft: Draft): unknown {
  switch (key) {
    case 'title':
      return draft.title.trim()
    case 'notes_md':
      return draft.notes_md.trim() ? draft.notes_md : null
    case 'context':
      return cleanContext(draft.context)
    default:
      return draft[key]
  }
}

/** Comparable form (tags ignore order and case, the server matches them case-insensitively). */
function comparable(key: DraftKey, draft: Draft): string {
  const value = payload(key, draft)
  if (key === 'tags') return JSON.stringify([...(value as string[])].map((tag) => tag.toLowerCase()).sort())
  return JSON.stringify(value ?? null)
}

export function changedKeys(base: Draft, draft: Draft, touched: DraftKey[]): DraftKey[] {
  return touched.filter((key) => comparable(key, base) !== comparable(key, draft))
}

export function buildPatch(base: Draft, draft: Draft, touched: DraftKey[]): TaskPatchIn {
  const patch: Record<string, unknown> = {}
  for (const key of changedKeys(base, draft, touched)) {
    if (key === 'status' && draft.status === null) continue
    patch[key] = payload(key, draft)
  }
  // a cleared date clears its time; a new time travels with its date
  if ('due_date' in patch && patch.due_date === null) patch.due_time = null
  if ('due_time' in patch && patch.due_time !== null && !('due_date' in patch)) patch.due_date = draft.due_date
  return patch as TaskPatchIn
}

/** POST body for a new task; `extra` carries prefill fields the editor does not show (parent_id…). */
export function buildCreate(draft: Draft, id: string, extra?: Partial<TaskIn>): TaskIn {
  const body: TaskIn = { id, title: draft.title.trim() }
  if (extra?.parent_id) body.parent_id = extra.parent_id
  if (extra?.energy != null) body.energy = extra.energy
  if (extra?.sort_order != null) body.sort_order = extra.sort_order
  if (draft.notes_md.trim()) body.notes_md = draft.notes_md
  if (draft.status) body.status = draft.status
  if (draft.priority !== 4) body.priority = draft.priority
  if (draft.is_pinned) body.is_pinned = true
  if (draft.project_id) body.project_id = draft.project_id
  if (draft.area_id) body.area_id = draft.area_id
  if (draft.due_date) {
    body.due_date = draft.due_date
    if (draft.due_time) body.due_time = draft.due_time
  }
  if (draft.block) body.block = draft.block
  if (draft.estimate_minutes) body.estimate_minutes = draft.estimate_minutes
  if (draft.recurrence_rule) {
    body.recurrence_rule = draft.recurrence_rule
    if (draft.recurrence_until) body.recurrence_until = draft.recurrence_until
  }
  if (draft.reminders.length > 0) body.reminders = draft.reminders
  if (draft.tags.length > 0) body.tags = draft.tags
  const context = cleanContext(draft.context)
  if (context) body.context = context
  return body
}

/**
 * Draft state keyed by an editing session: a new `sessionKey` (the sheet
 * reopened, another task opened) drops every pending edit.
 */
export function useTaskDraft(base: Draft, sessionKey: string) {
  const [overlay, setOverlay] = useState<Partial<Draft>>({})
  const [key, setKey] = useState(sessionKey)
  let current = overlay
  if (key !== sessionKey) {
    // reset during render (derived state) so the stale draft never paints
    setKey(sessionKey)
    setOverlay({})
    current = {}
  }
  const draft: Draft = { ...base, ...current }
  const touched = Object.keys(current) as DraftKey[]
  const changed = changedKeys(base, draft, touched)
  return {
    draft,
    touched,
    changed,
    dirty: changed.length > 0,
    set: <K extends DraftKey>(field: K, value: Draft[K]) => setOverlay((value0) => ({ ...value0, [field]: value })),
    setMany: (values: Partial<Draft>) => setOverlay((value0) => ({ ...value0, ...values })),
    reset: () => setOverlay({}),
  }
}
