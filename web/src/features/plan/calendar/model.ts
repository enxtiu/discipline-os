/**
 * Calendar view model: the server's `/views/calendar` days regrouped by the
 * task's own day key, the overlap layout of a day's blocks, colours.
 *
 * Blocks are grouped by the date of the task's own block and all-day tasks by
 * their due date (not by the array they arrived in), so an optimistic move
 * written into the caches (`patchTaskInCaches`) shows up in its new column at
 * once.
 */
import type { CalendarView, Occurrence, Priority, Task } from '../types'
import { MINUTES_PER_DAY, toMinutes } from './dates'

export interface DayBucket {
  date: string
  /** Real tasks whose block starts this day, by start time. */
  blocks: Task[]
  /** Tasks due this day without a block (may carry a `due_time` label). */
  allDay: Task[]
  /** Virtual future repeats (read-only). */
  occurrences: Occurrence[]
  /** Server count of tasks completed this day. */
  doneCount: number
}

export function isDone(task: Pick<Task, 'status'>): boolean {
  return task.status === 'done' || task.status === 'cancelled'
}

function byStart(a: Task, b: Task): number {
  const left = a.block ? toMinutes(a.block.start) : 0
  const right = b.block ? toMinutes(b.block.start) : 0
  return left - right || a.priority - b.priority || a.title.localeCompare(b.title)
}

function byDue(a: Task, b: Task): number {
  return (
    Number(isDone(a)) - Number(isDone(b)) ||
    a.priority - b.priority ||
    (a.due_time ?? '99:99').localeCompare(b.due_time ?? '99:99') ||
    a.title.localeCompare(b.title)
  )
}

export function indexCalendar(view: CalendarView | undefined): Map<string, DayBucket> {
  const buckets = new Map<string, DayBucket>()
  if (!view) return buckets
  for (const day of view.days) {
    buckets.set(day.date, { date: day.date, blocks: [], allDay: [], occurrences: [], doneCount: day.done_count })
  }
  const seen = new Set<string>()
  for (const day of view.days) {
    for (const task of [...day.blocks, ...day.all_day]) {
      if (seen.has(task.id)) continue
      seen.add(task.id)
      if (task.block) buckets.get(task.block.date)?.blocks.push(task)
      else if (task.due_date) buckets.get(task.due_date)?.allDay.push(task)
    }
    for (const occurrence of day.occurrences) buckets.get(occurrence.date)?.occurrences.push(occurrence)
  }
  for (const bucket of buckets.values()) {
    bucket.blocks.sort(byStart)
    bucket.allDay.sort(byDue)
    bucket.occurrences.sort(
      (a, b) =>
        (a.block?.start ?? a.due_time ?? '99:99').localeCompare(b.block?.start ?? b.due_time ?? '99:99') ||
        a.title.localeCompare(b.title),
    )
  }
  return buckets
}

// --------------------------------------------------------------------------- //
// Time-grid items and their side-by-side layout
// --------------------------------------------------------------------------- //
export type GridItem =
  /** A real task's block starting this day (`end` may pass 1440 — it ends the next day). */
  | { kind: 'task'; key: string; task: Task; start: number; end: number }
  /** The part after midnight of a block that started the day before (display only). */
  | { kind: 'continuation'; key: string; task: Task; start: number; end: number }
  /** A virtual repeat with a block (read-only, dashed). */
  | { kind: 'occurrence'; key: string; occurrence: Occurrence; start: number; end: number }

/** The grid items of `day`: its blocks, repeats and yesterday's blocks running past midnight. */
export function gridItems(day: string, bucket: DayBucket | undefined, previous: DayBucket | undefined): GridItem[] {
  const items: GridItem[] = []
  for (const task of bucket?.blocks ?? []) {
    if (!task.block) continue
    const start = toMinutes(task.block.start)
    items.push({ kind: 'task', key: task.id, task, start, end: start + Math.max(1, task.block.minutes) })
  }
  for (const occurrence of bucket?.occurrences ?? []) {
    if (!occurrence.block) continue
    const start = toMinutes(occurrence.block.start)
    items.push({
      kind: 'occurrence',
      key: `occ:${occurrence.task_id}:${occurrence.date}`,
      occurrence,
      start,
      end: start + Math.max(1, occurrence.block.minutes),
    })
  }
  for (const task of previous?.blocks ?? []) {
    if (!task.block?.ends_next_day) continue
    const end = toMinutes(task.block.end)
    if (end > 0) items.push({ kind: 'continuation', key: `cont:${task.id}:${day}`, task, start: 0, end })
  }
  return items
}

export interface Placed<T> {
  item: T
  /** 0-based column inside its cluster of overlapping items. */
  column: number
  /** Columns in that cluster (the item is 1/columns wide). */
  columns: number
}

/**
 * Overlapping items side by side: sweep by start time, put each item in the
 * first free column of its cluster (a run of transitively overlapping items);
 * every item of a cluster gets the cluster's column count. `minLength` is the
 * shortest visual length (a 15-min block is drawn taller than 15 min, so it
 * must not be overlapped by the next block either).
 */
export function layoutColumns<T extends { start: number; end: number }>(items: T[], minLength = 0): Placed<T>[] {
  const sorted = [...items].sort((a, b) => a.start - b.start || b.end - a.end)
  const placed: Placed<T>[] = []
  let cluster: Placed<T>[] = []
  let columnEnds: number[] = []
  let clusterEnd = Number.NEGATIVE_INFINITY

  const flush = () => {
    for (const entry of cluster) entry.columns = columnEnds.length
    placed.push(...cluster)
    cluster = []
    columnEnds = []
  }

  for (const item of sorted) {
    const end = Math.max(item.end, item.start + minLength)
    if (cluster.length > 0 && item.start >= clusterEnd) {
      flush()
      clusterEnd = Number.NEGATIVE_INFINITY
    }
    let column = columnEnds.findIndex((columnEnd) => columnEnd <= item.start)
    if (column === -1) {
      column = columnEnds.length
      columnEnds.push(end)
    } else {
      columnEnds[column] = end
    }
    cluster.push({ item, column, columns: 1 })
    clusterEnd = Math.max(clusterEnd, end)
  }
  flush()
  return placed
}

/** Clamp an item to the visible day (a block may run past midnight). */
export function visibleEnd(end: number): number {
  return Math.min(end, MINUTES_PER_DAY)
}

// --------------------------------------------------------------------------- //
// Colours — literal class strings (Tailwind purges anything built at runtime),
// palette hues the design system already uses, matching PRIORITY_STYLES.
// --------------------------------------------------------------------------- //
export const PRIORITY_TINT: Record<Priority, string> = {
  1: 'bg-rose-400/15',
  2: 'bg-amber-400/15',
  3: 'bg-violet-300/15',
  4: 'bg-[rgb(var(--border))]/60',
}

export const PRIORITY_CHIP: Record<Priority, string> = {
  1: 'bg-rose-400/20',
  2: 'bg-amber-400/20',
  3: 'bg-violet-300/20',
  4: 'bg-[rgb(var(--border))]/70',
}
