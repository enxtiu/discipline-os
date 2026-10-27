/**
 * Planner endpoints (stage 6) — reads go straight to the API, writes go
 * through the offline queue (`queries.ts` → `lib/offlineQueue.mutate`), so the
 * paths of every write live in `planPaths`.
 */
import { api } from '@/api/client'
import type {
  ArchiveView,
  AreaDetail,
  Area,
  CalendarView,
  Counts,
  InboxView,
  JournalDay,
  JournalList,
  ParseResult,
  ProjectDetail,
  ProjectFilter,
  Project,
  Resource,
  SearchResult,
  Tag,
  TagView,
  TaskDetail,
  TimeEntryList,
  TimeSummary,
  TimerState,
  TodayView,
  UpcomingView,
} from './types'

const P = '/v1/planner'

export const planPaths = {
  tasks: `${P}/tasks`,
  task: (id: string) => `${P}/tasks/${id}`,
  completeTask: (id: string) => `${P}/tasks/${id}/complete`,
  reopenTask: (id: string) => `${P}/tasks/${id}/reopen`,
  quickAdd: `${P}/tasks/quick`,
  areas: `${P}/areas`,
  area: (id: string) => `${P}/areas/${id}`,
  reorderAreas: `${P}/areas/reorder`,
  projects: `${P}/projects`,
  project: (id: string) => `${P}/projects/${id}`,
  reorderProjects: `${P}/projects/reorder`,
  resources: `${P}/resources`,
  resource: (id: string) => `${P}/resources/${id}`,
  tags: `${P}/tags`,
  tag: (id: string) => `${P}/tags/${id}`,
  timerStart: `${P}/timer/start`,
  timerStop: `${P}/timer/stop`,
  timeEntries: `${P}/time-entries`,
  timeEntry: (id: string) => `${P}/time-entries/${id}`,
  journal: (date: string) => `${P}/journal/${date}`,
}

export const planApi = {
  // ---- views -----------------------------------------------------------------
  today: (date?: string) => api.get<TodayView>(`${P}/views/today`, { query: { date } }),
  inbox: () => api.get<InboxView>(`${P}/views/inbox`),
  upcoming: (from?: string, days = 14) => api.get<UpcomingView>(`${P}/views/upcoming`, { query: { from, days } }),
  calendar: (from: string, to: string) => api.get<CalendarView>(`${P}/views/calendar`, { query: { from, to } }),
  tagView: (id: string, includeDone = false) =>
    api.get<TagView>(`${P}/views/tag/${id}`, { query: { include_done: includeDone } }),
  archive: () => api.get<ArchiveView>(`${P}/views/archive`),
  counts: () => api.get<Counts>(`${P}/counts`),
  search: (q: string, includeDone = false) =>
    api.get<SearchResult>(`${P}/search`, { query: { q, include_done: includeDone } }),

  // ---- tasks -----------------------------------------------------------------
  task: (id: string) => api.get<TaskDetail>(planPaths.task(id)),
  /** Preview only (no write): what the quick-add line would create. */
  parse: (text: string, capturedAt?: string, signal?: AbortSignal) =>
    api.post<ParseResult>(`${P}/parse`, { text, captured_at: capturedAt }, { signal }),

  // ---- PARA --------------------------------------------------------------------
  areas: (includeArchived = false) =>
    api.get<{ items: Area[] }>(planPaths.areas, { query: { include_archived: includeArchived } }),
  area: (id: string) => api.get<AreaDetail>(planPaths.area(id)),
  projects: (status: ProjectFilter = 'open', areaId?: string) =>
    api.get<{ items: Project[] }>(planPaths.projects, { query: { status, area_id: areaId } }),
  project: (id: string) => api.get<ProjectDetail>(planPaths.project(id)),
  resources: (areaId?: string, includeArchived = false) =>
    api.get<{ items: Resource[] }>(planPaths.resources, {
      query: { area_id: areaId, include_archived: includeArchived },
    }),
  tags: () => api.get<{ items: Tag[] }>(planPaths.tags),

  // ---- time tracking -----------------------------------------------------------
  timer: () => api.get<TimerState>(`${P}/timer`),
  timeEntries: (from: string, to: string) => api.get<TimeEntryList>(planPaths.timeEntries, { query: { from, to } }),
  timeSummary: (period: 'week' | 'month' = 'week', date?: string) =>
    api.get<TimeSummary>(`${P}/time/summary`, { query: { period, date } }),

  // ---- journal -------------------------------------------------------------------
  journal: (date: string) => api.get<JournalDay>(planPaths.journal(date)),
  journalList: (from: string, to: string) => api.get<JournalList>(`${P}/journal`, { query: { from, to } }),
}
