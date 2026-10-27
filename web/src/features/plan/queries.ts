/**
 * Planner queries & mutations (stage 6).
 *
 * * Every read lives under the `['planner', …]` key, so one invalidation after a
 *   write refreshes every planner screen (lists are small, a personal planner).
 * * Every write goes through the offline queue (`mutate`): the client generates
 *   the id, the server keys the row by it, so a task captured in the metro is
 *   replayed later without duplicates. A queued write resolves with
 *   `{ queued: true }`; the screen says "will be sent" instead of pretending.
 * * Ticking timers never trust the device clock: elapsed time is the server's
 *   `elapsed_s` plus the time since that response arrived (`useElapsed`).
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { mutate, onQueueChange, type QueuedMethod } from '@/lib/offlineQueue'
import { planApi, planPaths } from './api'
import type {
  AreaIn,
  AreaPatchIn,
  JournalIn,
  ProjectFilter,
  ProjectIn,
  ProjectPatchIn,
  QuickAddIn,
  QuickAddResult,
  ResourceIn,
  ResourcePatchIn,
  RunningTimer,
  Task,
  TaskCompleteResult,
  TaskDetail,
  TaskIn,
  TaskPatchIn,
  TaskPatchResult,
  TaskReopenResult,
  TagIn,
  TimeEntry,
  TimeEntryIn,
  TimeEntryPatchIn,
  TimerStartIn,
  TimerStartResult,
  TimerStopIn,
  TimerStopResult,
  Area,
  Project,
  Resource,
  Tag,
  JournalEntry,
} from './types'

// --------------------------------------------------------------------------- //
// Keys
// --------------------------------------------------------------------------- //
export const planKeys = {
  all: ['planner'] as const,
  today: (date: string) => ['planner', 'today', date] as const,
  inbox: ['planner', 'inbox'] as const,
  upcoming: (from: string, days: number) => ['planner', 'upcoming', from, days] as const,
  calendar: (from: string, to: string) => ['planner', 'calendar', from, to] as const,
  tagView: (id: string, includeDone: boolean) => ['planner', 'tag', id, includeDone] as const,
  archive: ['planner', 'archive'] as const,
  counts: ['planner', 'counts'] as const,
  search: (q: string, includeDone: boolean) => ['planner', 'search', q, includeDone] as const,
  task: (id: string) => ['planner', 'task', id] as const,
  areas: (includeArchived: boolean) => ['planner', 'areas', includeArchived] as const,
  area: (id: string) => ['planner', 'area', id] as const,
  projects: (status: ProjectFilter, areaId: string) => ['planner', 'projects', status, areaId] as const,
  project: (id: string) => ['planner', 'project', id] as const,
  resources: (areaId: string, includeArchived: boolean) => ['planner', 'resources', areaId, includeArchived] as const,
  tags: ['planner', 'tags'] as const,
  timer: ['planner', 'timer'] as const,
  timeEntries: (from: string, to: string) => ['planner', 'time-entries', from, to] as const,
  timeSummary: (period: string, date: string) => ['planner', 'time-summary', period, date] as const,
  journal: (date: string) => ['planner', 'journal', date] as const,
  journalList: (from: string, to: string) => ['planner', 'journal-list', from, to] as const,
}

// --------------------------------------------------------------------------- //
// Reads
// --------------------------------------------------------------------------- //
/** `date` omitted → the server's today for this user. */
export function useTodayView(date?: string) {
  return useQuery({
    queryKey: planKeys.today(date ?? 'today'),
    queryFn: () => planApi.today(date),
    refetchOnWindowFocus: true,
  })
}

export function useInbox() {
  return useQuery({ queryKey: planKeys.inbox, queryFn: () => planApi.inbox() })
}

export function useUpcoming(from?: string, days = 14) {
  return useQuery({
    queryKey: planKeys.upcoming(from ?? 'today', days),
    queryFn: () => planApi.upcoming(from, days),
  })
}

export function useCalendar(from: string, to: string, enabled = true) {
  return useQuery({
    queryKey: planKeys.calendar(from, to),
    queryFn: () => planApi.calendar(from, to),
    enabled: enabled && !!from && !!to,
    placeholderData: (previous) => previous,
  })
}

export function useTagView(id: string | undefined, includeDone = false) {
  return useQuery({
    queryKey: planKeys.tagView(id ?? '', includeDone),
    queryFn: () => planApi.tagView(id as string, includeDone),
    enabled: !!id,
  })
}

export function useArchive() {
  return useQuery({ queryKey: planKeys.archive, queryFn: () => planApi.archive() })
}

export function useCounts() {
  return useQuery({ queryKey: planKeys.counts, queryFn: () => planApi.counts(), refetchOnWindowFocus: true })
}

export function useSearch(q: string, includeDone = false) {
  const trimmed = q.trim()
  return useQuery({
    queryKey: planKeys.search(trimmed, includeDone),
    queryFn: () => planApi.search(trimmed, includeDone),
    enabled: trimmed.length >= 2,
    placeholderData: (previous) => previous,
  })
}

export function useTask(id: string | null | undefined) {
  return useQuery({
    queryKey: planKeys.task(id ?? ''),
    queryFn: () => planApi.task(id as string),
    enabled: !!id,
    staleTime: 0,
  })
}

export function useAreas(includeArchived = false) {
  return useQuery({ queryKey: planKeys.areas(includeArchived), queryFn: () => planApi.areas(includeArchived) })
}

export function useArea(id: string | undefined) {
  return useQuery({ queryKey: planKeys.area(id ?? ''), queryFn: () => planApi.area(id as string), enabled: !!id })
}

export function useProjects(status: ProjectFilter = 'open', areaId?: string) {
  return useQuery({
    queryKey: planKeys.projects(status, areaId ?? ''),
    queryFn: () => planApi.projects(status, areaId),
  })
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: planKeys.project(id ?? ''),
    queryFn: () => planApi.project(id as string),
    enabled: !!id,
  })
}

export function useResources(areaId?: string, includeArchived = false) {
  return useQuery({
    queryKey: planKeys.resources(areaId ?? '', includeArchived),
    queryFn: () => planApi.resources(areaId, includeArchived),
  })
}

export function useTags() {
  return useQuery({ queryKey: planKeys.tags, queryFn: () => planApi.tags(), staleTime: 5 * 60_000 })
}

/**
 * The running timer is server state, so it survives reloads and follows you to
 * another device: refetched on focus, when the tab becomes visible and every
 * minute while mounted.
 */
export function useTimer() {
  const query = useQuery({
    queryKey: planKeys.timer,
    queryFn: () => planApi.timer(),
    refetchOnWindowFocus: true,
    refetchInterval: 60_000,
    staleTime: 10_000,
  })
  const { refetch } = query
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refetch()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [refetch])
  return query
}

/**
 * Seconds elapsed on a running timer, ticking locally once a second.
 * `receivedAt` is when the response carrying `running.elapsed_s` arrived
 * (TanStack Query's `dataUpdatedAt`), so the device clock only measures the
 * few seconds since then — a phone with a wrong clock still shows the truth.
 */
export function useElapsed(running: RunningTimer | null | undefined, receivedAt: number): number {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setTick((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  if (!running) return 0
  const since = Math.max(0, (Date.now() - receivedAt) / 1000)
  return Math.max(0, Math.floor(running.elapsed_s + since))
}

export function useTimeEntries(from: string, to: string) {
  return useQuery({
    queryKey: planKeys.timeEntries(from, to),
    queryFn: () => planApi.timeEntries(from, to),
    enabled: !!from && !!to,
  })
}

export function useTimeSummary(period: 'week' | 'month' = 'week', date?: string) {
  return useQuery({
    queryKey: planKeys.timeSummary(period, date ?? 'today'),
    queryFn: () => planApi.timeSummary(period, date),
  })
}

export function useJournal(date: string) {
  return useQuery({ queryKey: planKeys.journal(date), queryFn: () => planApi.journal(date), enabled: !!date })
}

export function useJournalList(from: string, to: string) {
  return useQuery({
    queryKey: planKeys.journalList(from, to),
    queryFn: () => planApi.journalList(from, to),
    enabled: !!from && !!to,
  })
}

// --------------------------------------------------------------------------- //
// Writes (offline-capable)
// --------------------------------------------------------------------------- //
export type Saved<T> = { queued: true; clientUuid: string } | { queued: false; data: T }

interface WriteRequest {
  path: string
  method: QueuedMethod
  body?: unknown
  /** Idempotency key; for creates it is the new row's id. */
  clientUuid?: string
}

/**
 * Refetch every planner screen. `gone` is the key prefix of a row just
 * deleted: its own detail query is still mounted until the screen closes, and
 * refetching it would only answer 404.
 */
export function invalidatePlanner(queryClient: QueryClient, gone?: readonly unknown[]) {
  return queryClient.invalidateQueries({
    queryKey: planKeys.all,
    predicate: gone ? (query) => !gone.every((part, index) => query.queryKey[index] === part) : undefined,
  })
}

/** Refresh planner screens once queued writes have been sent (mount once per screen tree). */
export function usePlannerQueueRefresh() {
  const queryClient = useQueryClient()
  useEffect(() => {
    let last = Number.POSITIVE_INFINITY
    return onQueueChange((count) => {
      if (last > count) void invalidatePlanner(queryClient)
      last = count
    })
  }, [queryClient])
}

function usePlanWrite<TVars, TData>(
  build: (vars: TVars) => WriteRequest,
  /** For deletes: the deleted row's own query key (not refetched, see `invalidatePlanner`). */
  goneOf?: (vars: TVars) => readonly unknown[],
) {
  const queryClient = useQueryClient()
  return useMutation({
    networkMode: 'always', // offline writes go to the queue instead of pausing
    mutationFn: async (vars: TVars): Promise<Saved<TData>> => {
      const request = build(vars)
      const result = await mutate<TData>(request.path, {
        method: request.method,
        body: request.body,
        clientUuid: request.clientUuid,
        kind: 'planner',
      })
      return result.queued ? { queued: true, clientUuid: result.clientUuid } : { queued: false, data: result.data }
    },
    onSuccess: (result, vars) => {
      if (!result.queued) void invalidatePlanner(queryClient, goneOf?.(vars))
    },
  })
}

export function newId(): string {
  return crypto.randomUUID()
}

// ---- tasks -----------------------------------------------------------------
/** Create a task; pass `id` to know it before the server answers (generated if absent). */
export function useCreateTask() {
  return usePlanWrite<TaskIn, TaskDetail>((input) => {
    const id = input.id ?? newId()
    return { path: planPaths.tasks, method: 'POST', body: { ...input, id }, clientUuid: id }
  })
}

/** Quick add: the server parses relative to `captured_at`, so "завтра" stays tomorrow even if sent later. */
export function useQuickAdd() {
  return usePlanWrite<QuickAddIn, QuickAddResult>((input) => {
    const id = input.id ?? newId()
    return {
      path: planPaths.quickAdd,
      method: 'POST',
      body: { ...input, id, captured_at: input.captured_at ?? new Date().toISOString() },
      clientUuid: id,
    }
  })
}

export function useUpdateTask() {
  return usePlanWrite<{ id: string; patch: TaskPatchIn }, TaskPatchResult>(({ id, patch }) => ({
    path: planPaths.task(id),
    method: 'PATCH',
    body: patch,
  }))
}

export function useCompleteTask() {
  return usePlanWrite<{ id: string; completed_at?: string }, TaskCompleteResult>(({ id, completed_at }) => ({
    path: planPaths.completeTask(id),
    method: 'POST',
    body: { completed_at: completed_at ?? new Date().toISOString() },
  }))
}

export function useReopenTask() {
  return usePlanWrite<{ id: string }, TaskReopenResult>(({ id }) => ({
    path: planPaths.reopenTask(id),
    method: 'POST',
    body: {},
  }))
}

export function useDeleteTask() {
  return usePlanWrite<{ id: string }, void>(
    ({ id }) => ({ path: planPaths.task(id), method: 'DELETE' }),
    ({ id }) => planKeys.task(id),
  )
}

/**
 * Optimistically merge `patch` into every cached copy of a task (lists, views,
 * details). Returns a rollback. Use it for the completion checkbox so the tap
 * answers instantly; the invalidation after the write brings the server truth.
 */
export function patchTaskInCaches(queryClient: QueryClient, taskId: string, patch: Partial<Task>): () => void {
  const snapshots = queryClient.getQueriesData({ queryKey: planKeys.all })
  const visit = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(visit)
    if (value && typeof value === 'object') {
      const record = value as Record<string, unknown>
      const next: Record<string, unknown> = {}
      for (const [key, child] of Object.entries(record)) next[key] = visit(child)
      if (record.id === taskId && typeof record.title === 'string' && 'status' in record) Object.assign(next, patch)
      return next
    }
    return value
  }
  for (const [key, data] of snapshots) {
    if (data !== undefined) queryClient.setQueryData(key, visit(data))
  }
  return () => {
    for (const [key, data] of snapshots) queryClient.setQueryData(key, data)
  }
}

// ---- PARA containers ----------------------------------------------------------
export function useCreateArea() {
  return usePlanWrite<AreaIn, Area>((input) => {
    const id = input.id ?? newId()
    return { path: planPaths.areas, method: 'POST', body: { ...input, id }, clientUuid: id }
  })
}
export function useUpdateArea() {
  return usePlanWrite<{ id: string; patch: AreaPatchIn }, Area>(({ id, patch }) => ({
    path: planPaths.area(id),
    method: 'PATCH',
    body: patch,
  }))
}
export function useDeleteArea() {
  return usePlanWrite<{ id: string }, void>(
    ({ id }) => ({ path: planPaths.area(id), method: 'DELETE' }),
    ({ id }) => planKeys.area(id),
  )
}
export function useReorderAreas() {
  return usePlanWrite<string[], { ok: boolean }>((ids) => ({ path: planPaths.reorderAreas, method: 'POST', body: { ids } }))
}

export function useCreateProject() {
  return usePlanWrite<ProjectIn, Project>((input) => {
    const id = input.id ?? newId()
    return { path: planPaths.projects, method: 'POST', body: { ...input, id }, clientUuid: id }
  })
}
export function useUpdateProject() {
  return usePlanWrite<{ id: string; patch: ProjectPatchIn }, Project>(({ id, patch }) => ({
    path: planPaths.project(id),
    method: 'PATCH',
    body: patch,
  }))
}
export function useDeleteProject() {
  return usePlanWrite<{ id: string }, void>(
    ({ id }) => ({ path: planPaths.project(id), method: 'DELETE' }),
    ({ id }) => planKeys.project(id),
  )
}
export function useReorderProjects() {
  return usePlanWrite<string[], { ok: boolean }>((ids) => ({
    path: planPaths.reorderProjects,
    method: 'POST',
    body: { ids },
  }))
}

export function useCreateResource() {
  return usePlanWrite<ResourceIn, Resource>((input) => {
    const id = input.id ?? newId()
    return { path: planPaths.resources, method: 'POST', body: { ...input, id }, clientUuid: id }
  })
}
export function useUpdateResource() {
  return usePlanWrite<{ id: string; patch: ResourcePatchIn }, Resource>(({ id, patch }) => ({
    path: planPaths.resource(id),
    method: 'PATCH',
    body: patch,
  }))
}
export function useDeleteResource() {
  return usePlanWrite<{ id: string }, void>(({ id }) => ({ path: planPaths.resource(id), method: 'DELETE' }))
}

export function useCreateTag() {
  return usePlanWrite<TagIn, Tag>((input) => {
    const id = input.id ?? newId()
    return { path: planPaths.tags, method: 'POST', body: { ...input, id }, clientUuid: id }
  })
}
export function useUpdateTag() {
  return usePlanWrite<{ id: string; patch: Partial<Omit<TagIn, 'id'>> }, Tag>(({ id, patch }) => ({
    path: planPaths.tag(id),
    method: 'PATCH',
    body: patch,
  }))
}
export function useDeleteTag() {
  return usePlanWrite<{ id: string }, void>(
    ({ id }) => ({ path: planPaths.tag(id), method: 'DELETE' }),
    ({ id }) => ['planner', 'tag', id],
  )
}

// ---- time tracking -----------------------------------------------------------
/** Start (or switch) the timer. `started_at` is the tap instant, so an offline start replays at the right time. */
export function useTimerStart() {
  return usePlanWrite<Omit<TimerStartIn, 'id'> & { id?: string }, TimerStartResult>((input) => {
    const id = input.id ?? newId()
    return {
      path: planPaths.timerStart,
      method: 'POST',
      body: { ...input, id, started_at: input.started_at ?? new Date().toISOString() },
      clientUuid: id,
    }
  })
}

/** Stop the running timer; `ended_at` defaults to the tap instant (correct even when replayed later). */
export function useTimerStop() {
  return usePlanWrite<TimerStopIn, TimerStopResult>((input) => ({
    path: planPaths.timerStop,
    method: 'POST',
    body: { ...input, ended_at: input.ended_at ?? new Date().toISOString() },
  }))
}

export function useCreateTimeEntry() {
  return usePlanWrite<TimeEntryIn, TimeEntry>((input) => {
    const id = input.id ?? newId()
    return { path: planPaths.timeEntries, method: 'POST', body: { ...input, id }, clientUuid: id }
  })
}
export function useUpdateTimeEntry() {
  return usePlanWrite<{ id: string; patch: TimeEntryPatchIn }, TimeEntry>(({ id, patch }) => ({
    path: planPaths.timeEntry(id),
    method: 'PATCH',
    body: patch,
  }))
}
export function useDeleteTimeEntry() {
  return usePlanWrite<{ id: string }, void>(({ id }) => ({ path: planPaths.timeEntry(id), method: 'DELETE' }))
}

// ---- journal -------------------------------------------------------------------
/** Upsert by day; fields not sent keep their value (autosave sends only what changed). */
export function useSaveJournal() {
  return usePlanWrite<{ date: string; body: JournalIn }, JournalEntry>(({ date, body }) => ({
    path: planPaths.journal(date),
    method: 'PUT',
    body,
  }))
}
export function useDeleteJournal() {
  return usePlanWrite<{ date: string }, void>(({ date }) => ({ path: planPaths.journal(date), method: 'DELETE' }))
}
