/**
 * «План дня» (stage 10, the unified «Сегодня»): the day as one timeline — the meals of
 * the food diary (one row per meal type: its first time and its kcal), today's planner
 * tasks that have a time (a block on this day or a due time), and the main meals not
 * logged yet («Ужин — пока не записан»).
 *
 * Pure display: every input is the server's — the day's meals (`GET /nutrition/day`,
 * with the writes still waiting offline), the planner's today buckets (`GET
 * /planner/views/today`: which tasks are today's, their local HH:MM, `overdue`). Rows are
 * ordered by instant; the client draws no day boundary. `next` is the first open task
 * still ahead (a block not over yet, a due time the server does not call overdue).
 */
import type { MealLog, MealType } from '@/api/types'
import type { Task, TodayView } from '@/features/plan/types'

/** The meals a day is expected to have: missing ones get a «пока не записан» row. */
export const MAIN_MEALS: readonly MealType[] = ['breakfast', 'lunch', 'dinner']

export type PlanState = 'done' | 'next' | 'open' | 'missing'

export type PlanRow =
  | {
      kind: 'meal'
      key: string
      state: 'done'
      at: number
      /** the logged instant, formatted by the caller in the user's timezone */
      loggedAt: string
      meal: MealType
      kcal: number
      pending: boolean
    }
  | {
      kind: 'task'
      key: string
      state: 'done' | 'next' | 'open'
      at: number
      /** local HH:MM as the server sent it */
      time: string
      title: string
      minutes: number | null
    }
  | { kind: 'missing'; key: string; state: 'missing'; meal: MealType }

interface Timed {
  task: Task
  at: number
  time: string
  minutes: number | null
  /** when the task stops being "ahead" (a block's end, the due instant) */
  until: number
}

/** A task's place on `day`: its block that starts on the day, else its due time on the day. */
function timed(task: Task, day: string): Timed | null {
  if (task.block && task.block.date === day) {
    return {
      task,
      at: Date.parse(task.block.start_at),
      time: task.block.start,
      minutes: task.block.minutes,
      until: Date.parse(task.block.end_at),
    }
  }
  if (task.due_date === day && task.due_time && task.due_at) {
    const at = Date.parse(task.due_at)
    return { task, at, time: task.due_time, minutes: task.estimate_minutes, until: at }
  }
  return null
}

export function buildDayPlan({
  meals,
  pendingIds,
  plan,
  now,
}: {
  meals: MealLog[]
  pendingIds: ReadonlySet<string>
  /** the planner's today view; null while it is not there (offline, an error) */
  plan: Pick<TodayView, 'date' | 'today' | 'scheduled' | 'done'> | null | undefined
  /** Date.now() of the render: only decides which open task is «Далее» */
  now: number
}): PlanRow[] {
  const rows: PlanRow[] = []

  const byType = new Map<MealType, { first: MealLog; kcal: number; pending: boolean }>()
  for (const meal of meals) {
    const group = byType.get(meal.meal_type)
    const pending = pendingIds.has(meal.id)
    if (!group) {
      byType.set(meal.meal_type, { first: meal, kcal: meal.kcal, pending })
      continue
    }
    if (Date.parse(meal.logged_at) < Date.parse(group.first.logged_at)) group.first = meal
    group.kcal += meal.kcal
    group.pending = group.pending || pending
  }
  for (const [meal, group] of byType) {
    rows.push({
      kind: 'meal',
      key: `meal:${meal}`,
      state: 'done',
      at: Date.parse(group.first.logged_at),
      loggedAt: group.first.logged_at,
      meal,
      kcal: group.kcal,
      pending: group.pending,
    })
  }

  if (plan) {
    const open = [...plan.scheduled, ...plan.today]
      .map((task) => timed(task, plan.date))
      .filter((item): item is Timed => item !== null)
      .sort((a, b) => a.at - b.at)
    const next = open.find((item) => (item.task.block ? item.until > now : !item.task.overdue))
    for (const item of open) {
      rows.push({
        kind: 'task',
        key: `task:${item.task.id}`,
        state: item === next ? 'next' : 'open',
        at: item.at,
        time: item.time,
        title: item.task.title,
        minutes: item.minutes,
      })
    }
    for (const task of plan.done) {
      const item = timed(task, plan.date)
      if (!item) continue
      rows.push({
        kind: 'task',
        key: `task:${task.id}`,
        state: 'done',
        at: item.at,
        time: item.time,
        title: task.title,
        minutes: item.minutes,
      })
    }
  }

  rows.sort((a, b) => ('at' in a ? a.at : 0) - ('at' in b ? b.at : 0))
  for (const meal of MAIN_MEALS) {
    if (!byType.has(meal)) rows.push({ kind: 'missing', key: `missing:${meal}`, state: 'missing', meal })
  }
  return rows
}

/**
 * How the line between two rows is drawn: solid up to «Далее» (the day so far), dashed
 * after it and into the meals not logged yet. `index` is the lower row of the pair.
 */
export function lineStyle(rows: PlanRow[], index: number): 'solid' | 'dashed' {
  const next = rows.findIndex((row) => row.state === 'next')
  if (rows[index]?.state === 'missing') return 'dashed'
  return next >= 0 && index > next ? 'dashed' : 'solid'
}
