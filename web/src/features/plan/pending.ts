/**
 * Optimistic queued task edits (stage 9).
 *
 * A task edited while its write waits in the offline queue — editor save,
 * complete / reopen, move, reschedule — shows the edited values at once in
 * every cached copy (`patchTaskInCaches`) and is marked «ждёт отправки». The
 * edits are re-applied whenever a planner query brings fresh server data while
 * they still wait, and dropped from the overlay once sent (the refetch after
 * the send brings the server's truth). Refused or conflicting writes are not
 * applied: they wait for a decision in the sync sheet.
 */
import { useEffect, useSyncExternalStore } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'

import { listEntries, onQueueState, statusOf, type QueuedRequest } from '@/lib/offlineQueue'
import { planPaths } from './api'
import { patchTaskInCaches, planKeys } from './queries'
import { isPresetKey } from './tasks/rrule'
import type { Area, Block, Counts, Project, Ref, Tag, TagRef, Task, TaskStatus } from './types'

const TASK_PATH = new RegExp(`^${planPaths.tasks}/([0-9a-f-]{36})(/complete|/reopen)?$`, 'i')

/** The task a queued write edits, and how (`edit` = PATCH, or a completion toggle). */
export function taskWriteOf(entry: Pick<QueuedRequest, 'method' | 'path'>): { id: string; action: 'edit' | 'complete' | 'reopen' } | null {
  const match = TASK_PATH.exec(entry.path)
  if (!match) return null
  if (match[2] === '/complete' && entry.method === 'POST') return { id: match[1].toLowerCase(), action: 'complete' }
  if (match[2] === '/reopen' && entry.method === 'POST') return { id: match[1].toLowerCase(), action: 'reopen' }
  if (!match[2] && entry.method === 'PATCH') return { id: match[1].toLowerCase(), action: 'edit' }
  return null
}

interface Lookup {
  today?: string
  project: (id: string) => Ref | null
  area: (id: string) => Ref | null
  tag: (name: string) => TagRef | null
}

function lookupFrom(queryClient: QueryClient): Lookup {
  const find = <T extends { id: string; name: string; color: string | null }>(prefix: readonly unknown[]) => {
    const rows: T[] = []
    for (const [, data] of queryClient.getQueriesData<{ items?: T[] }>({ queryKey: prefix })) {
      if (data && Array.isArray(data.items)) rows.push(...data.items)
    }
    return rows
  }
  const projects = find<Project>(['planner', 'projects'])
  const areas = find<Area>(['planner', 'areas'])
  const tags = find<Tag>(planKeys.tags)
  const ref = (row: { id: string; name: string; color: string | null } | undefined): Ref | null =>
    row ? { id: row.id, name: row.name, color: row.color } : null
  return {
    today: queryClient.getQueryData<Counts>(planKeys.counts)?.date,
    project: (id) => ref(projects.find((row) => row.id === id)),
    area: (id) => ref(areas.find((row) => row.id === id)),
    tag: (name) => {
      const row = tags.find((item) => item.name.toLowerCase() === name.toLowerCase())
      return row ? { id: row.id, name: row.name, color: row.color } : null
    },
  }
}

function minutesOf(hhmm: string): number {
  const [hours, minutes] = hhmm.split(':').map(Number)
  return hours * 60 + minutes
}

/** A queued PATCH body as `Task` fields (only what the client can know without the server). */
export function taskFieldsFromPatch(body: Record<string, unknown>, task: Task, lookup: Lookup): Partial<Task> {
  const out: Partial<Task> = {}
  const has = (key: string) => Object.prototype.hasOwnProperty.call(body, key)
  for (const key of ['title', 'notes_md', 'priority', 'is_pinned', 'sort_order', 'estimate_minutes', 'energy', 'recurrence_until'] as const) {
    if (has(key)) (out as Record<string, unknown>)[key] = body[key]
  }
  if (has('context')) out.context = typeof body.context === 'string' ? body.context.replace(/^@+/, '').trim() || null : null
  if (has('due_date')) {
    out.due_date = (body.due_date as string | null) ?? null
    if (out.due_date === null) {
      out.due_time = null
      out.due_at = null
    }
    if (lookup.today) out.overdue = !!out.due_date && out.due_date < lookup.today && task.status !== 'done'
  }
  if (has('due_time')) out.due_time = (body.due_time as string | null) ?? null
  let block = task.block
  if (has('block')) {
    const value = body.block as { date: string; start: string; end: string } | null
    if (value) {
      const start = minutesOf(value.start)
      let end = minutesOf(value.end)
      if (end <= start) end += 24 * 60
      block = {
        ...(task.block ?? ({ start_at: '', end_at: '' } as Block)),
        date: value.date,
        start: value.start,
        end: value.end,
        minutes: end - start,
        ends_next_day: end >= 24 * 60,
      }
    } else block = null
    out.block = block
  }
  let status: TaskStatus = task.status
  if (has('status') && typeof body.status === 'string') status = body.status as TaskStatus
  // `scheduled` is derived from the block, as the server does
  if ((status === 'open' || status === 'inbox') && block) status = 'scheduled'
  if (status === 'scheduled' && !block) status = 'open'
  if (status !== task.status) out.status = status
  if (has('project_id')) {
    const id = body.project_id as string | null
    out.project = id ? (lookup.project(id) ?? (task.project?.id === id ? task.project : null)) : null
  }
  if (has('area_id')) {
    const id = body.area_id as string | null
    out.area = id ? (lookup.area(id) ?? (task.area?.id === id ? task.area : null)) : null
  }
  if (has('tags') && Array.isArray(body.tags)) {
    out.tags = (body.tags as string[]).map(
      (name) => lookup.tag(name) ?? task.tags.find((tag) => tag.name.toLowerCase() === name.toLowerCase()) ?? { id: `new:${name}`, name, color: null },
    )
  }
  if (has('recurrence_rule')) {
    const rule = (body.recurrence_rule as string | null) || null
    out.recurrence_rule = rule
    out.is_recurring = !!rule
    out.recurrence_preset = rule ? (isPresetKey(rule) ? rule : 'custom') : null
  }
  return out
}

/** Every pending task write, in queue order, as one cache patch per task. */
export function applyQueuedTaskEdits(queryClient: QueryClient, entries: QueuedRequest[]): void {
  const lookup = lookupFrom(queryClient)
  for (const entry of entries) {
    if (statusOf(entry) !== 'pending') continue
    const write = taskWriteOf(entry)
    if (!write) continue
    const body = entry.body && typeof entry.body === 'object' ? (entry.body as Record<string, unknown>) : {}
    if (write.action === 'complete') {
      const completedAt = typeof body.completed_at === 'string' ? body.completed_at : new Date(entry.createdAt).toISOString()
      patchTaskInCaches(queryClient, write.id, { status: 'done', completed_at: completedAt, overdue: false })
    } else if (write.action === 'reopen') {
      patchTaskInCaches(queryClient, write.id, { status: 'open', completed_at: null, completed_day: null })
    } else {
      patchTaskInCaches(queryClient, write.id, (task) => taskFieldsFromPatch(body, task, lookup))
    }
  }
}

// --------------------------------------------------------------------------- //
// Which tasks have a write waiting (one read per queue change, shared by every row)
// --------------------------------------------------------------------------- //
let pendingIds: ReadonlySet<string> = new Set()
let pendingEntries: QueuedRequest[] = []
let signature = ''
let readVersion = -1
const idListeners = new Set<() => void>()

async function reread(version: number): Promise<void> {
  const entries = await listEntries()
  if (version < readVersion) return
  readVersion = version
  const waiting = entries.filter((entry) => statusOf(entry) === 'pending' && taskWriteOf(entry) !== null)
  // the queue changes often during a replay (sending flags, counts): act only when these writes did
  const next = waiting.map((entry) => `${entry.id}:${entry.updatedAt ?? entry.createdAt}`).join(',')
  if (next === signature) return
  signature = next
  pendingEntries = waiting
  const ids = new Set(waiting.map((entry) => taskWriteOf(entry)!.id))
  const same = ids.size === pendingIds.size && [...ids].every((id) => pendingIds.has(id))
  if (!same) pendingIds = ids
  for (const listener of idListeners) listener()
}

let started = false
function start() {
  if (started) return
  started = true
  onQueueState((state) => void reread(state.version))
  void reread(0)
}

function subscribeIds(listener: () => void) {
  start()
  idListeners.add(listener)
  return () => {
    idListeners.delete(listener)
  }
}

/** Ids of tasks with an edit waiting in the queue («ждёт отправки»). */
export function usePendingTaskIds(): ReadonlySet<string> {
  return useSyncExternalStore(subscribeIds, () => pendingIds, () => pendingIds)
}

/**
 * Mounted once (the task sheet host): queued task edits show in every planner
 * cache now and after every refetch until they are sent.
 */
export function usePlannerPendingEdits(): void {
  const queryClient = useQueryClient()
  useEffect(() => {
    const apply = () => applyQueuedTaskEdits(queryClient, pendingEntries)
    idListeners.add(apply)
    start()
    const stopCache = queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== 'updated' || event.action.type !== 'success' || event.action.manual) return
      if (event.query.queryKey[0] !== 'planner' || pendingEntries.length === 0) return
      apply() // fresh server data, but these edits have not reached the server yet
    })
    apply()
    return () => {
      idListeners.delete(apply)
      stopCache()
    }
  }, [queryClient])
}
