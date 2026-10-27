/**
 * Planner types (stage 6).
 *
 * These mirror the stage-6 API contract one to one. Once the backend's OpenAPI
 * schema is regenerated (`npm run gen:api`), every read model below is
 * re-pointed at `Schemas['…Out']` from `@/api/generated/schema` under the same
 * exported name, so components never change.
 */

// --------------------------------------------------------------------------- //
// Shared
// --------------------------------------------------------------------------- //
export type TaskStatus =
  | 'inbox'
  | 'open'
  | 'scheduled'
  | 'in_progress'
  | 'waiting'
  | 'blocked'
  | 'done'
  | 'cancelled'
/** Statuses the client may set (`scheduled` is derived by the server). */
export type TaskStatusIn = Exclude<TaskStatus, 'scheduled'>
export type Priority = 1 | 2 | 3 | 4
export type ProjectStatus = 'active' | 'on_hold' | 'completed' | 'cancelled' | 'archived'
export type RecurrencePreset = 'daily' | 'weekdays' | 'weekly' | 'biweekly' | 'monthly' | 'yearly' | 'custom'
export type ReminderStatus = 'pending' | 'sent' | 'failed' | 'cancelled' | 'suppressed'
export type TimeEntrySource = 'timer' | 'manual' | 'shortcut' | 'import'
export type TokenKind =
  | 'date'
  | 'time'
  | 'block'
  | 'duration'
  | 'priority'
  | 'tag'
  | 'project'
  | 'context'
  | 'recurrence'
  | 'reminder'

export interface Ref {
  id: string
  name: string
  color: string | null
}

export interface TaskRef {
  id: string
  title: string
}

export interface Block {
  date: string // YYYY-MM-DD (local day the block starts on)
  start: string // HH:MM local
  end: string // HH:MM local
  minutes: number
  start_at: string // ISO instant
  end_at: string
  ends_next_day: boolean
}

export interface Reminder {
  id: string
  lead_minutes: number | null
  date: string | null
  time: string | null
  remind_at: string
  status: ReminderStatus
  sent_at: string | null
}

export interface TagRef {
  id: string
  name: string
  color: string | null
}

// --------------------------------------------------------------------------- //
// Tasks
// --------------------------------------------------------------------------- //
export interface Task {
  id: string
  title: string
  notes_md: string | null
  status: TaskStatus
  priority: Priority
  project: Ref | null
  area: Ref | null
  parent_id: string | null
  parent_title: string | null
  depth: number
  due_date: string | null
  due_time: string | null
  due_at: string | null
  block: Block | null
  estimate_minutes: number | null
  actual_minutes: number
  completed_at: string | null
  completed_day: string | null
  recurrence_rule: string | null
  recurrence_until: string | null
  recurrence_preset: RecurrencePreset | null
  is_recurring: boolean
  series_id: string | null
  series_index: number
  tags: TagRef[]
  context: string | null
  energy: number | null
  is_pinned: boolean
  sort_order: number
  overdue: boolean
  subtask_counts: { done: number; total: number }
  reminders: Reminder[]
  source: 'manual' | 'quick_add' | 'recurrence' | 'import'
  created_at: string
  updated_at: string
}

export interface TaskDetail extends Task {
  subtasks: TaskDetail[]
  time: { total_minutes: number; entries: TimeEntry[] }
}

export interface Occurrence {
  task_id: string
  title: string
  date: string
  due_time: string | null
  block: { start: string; end: string; minutes: number } | null
  priority: Priority
  project: Ref | null
  area: Ref | null
  virtual: true
}

export type ReminderIn = { lead_minutes: number } | { date: string; time: string }

export interface BlockIn {
  date: string
  start: string
  end: string
}

/** POST body; every field optional in PATCH (`TaskPatchIn`). */
export interface TaskIn {
  id?: string
  title: string
  notes_md?: string | null
  status?: TaskStatusIn
  priority?: Priority
  project_id?: string | null
  area_id?: string | null
  parent_id?: string | null
  due_date?: string | null
  due_time?: string | null
  block?: BlockIn | null
  estimate_minutes?: number | null
  recurrence_rule?: string | null
  recurrence_until?: string | null
  tags?: string[]
  context?: string | null
  energy?: number | null
  is_pinned?: boolean
  sort_order?: number
  reminders?: ReminderIn[]
}
export type TaskPatchIn = Partial<Omit<TaskIn, 'id'>>

export interface TaskPatchResult {
  task: TaskDetail
  next_instance: Task | null
  removed_next_instance_id: string | null
}
export interface TaskCompleteResult {
  task: Task
  next_instance: Task | null
}
export interface TaskReopenResult {
  task: Task
  removed_next_instance_id: string | null
}

export interface ParseToken {
  start: number // UTF-16 offsets (String.slice)
  end: number
  kind: TokenKind
  text: string
}

export interface ParseResult {
  title: string
  due_date: string | null
  due_time: string | null
  block: { date: string; start: string; end: string } | null
  estimate_minutes: number | null
  priority: Priority | null
  tags: string[]
  project: { id: string; name: string; kind: 'project' | 'area' } | null
  context: string | null
  recurrence_rule: string | null
  recurrence_preset: RecurrencePreset | null
  reminders: { lead_minutes: number }[]
  tokens: ParseToken[]
}

export interface QuickAddIn {
  id?: string
  text: string
  captured_at?: string
  defaults?: { project_id?: string; area_id?: string; due_date?: string; parent_id?: string }
}
export interface QuickAddResult {
  task: TaskDetail
  parsed: ParseResult
}

// --------------------------------------------------------------------------- //
// Views
// --------------------------------------------------------------------------- //
export interface Counts {
  /** The user's today (server, user's timezone) — label days against this, never the device clock. */
  date: string
  inbox: number
  today: number
  overdue: number
  timer_running: boolean
}

export interface TodayView {
  date: string
  overdue: Task[]
  today: Task[]
  scheduled: Task[]
  done: Task[]
  timer: RunningTimer | null
  counts: Counts
}

export interface InboxView {
  items: Task[]
}

export interface UpcomingDay {
  date: string
  tasks: Task[]
  occurrences: Occurrence[]
}
export interface UpcomingView {
  from: string
  to: string
  days: UpcomingDay[]
}

export interface CalendarDay {
  date: string
  all_day: Task[]
  blocks: Task[]
  occurrences: Occurrence[]
  done_count: number
}
export interface CalendarView {
  from: string
  to: string
  days: CalendarDay[]
}

export interface TagView {
  tag: Tag
  items: Task[]
}

export interface ArchiveView {
  projects: Project[]
  areas: Area[]
  resources: Resource[]
  tasks: Task[]
}

export interface SearchResult {
  query: string
  tasks: Task[]
  projects: Project[]
  areas: Area[]
  resources: Resource[]
  journal: { date: string; snippet: string }[]
}

// --------------------------------------------------------------------------- //
// PARA containers
// --------------------------------------------------------------------------- //
export interface Area {
  id: string
  name: string
  description: string | null
  color: string | null
  icon: string | null
  sort_order: number
  is_archived: boolean
  weekly_target_minutes: number | null
  counts: { open_tasks: number; active_projects: number }
  week_minutes: number
  created_at: string
  updated_at: string
}
export interface AreaIn {
  id?: string
  name: string
  description?: string | null
  color?: string | null
  icon?: string | null
  weekly_target_minutes?: number | null
}
export type AreaPatchIn = Partial<Omit<AreaIn, 'id'>> & { is_archived?: boolean; sort_order?: number }
export interface AreaDetail {
  area: Area
  projects: Project[]
  tasks: Task[]
  resources: Resource[]
}

export interface Project {
  id: string
  name: string
  outcome: string | null
  description_md: string | null
  area: Ref | null
  status: ProjectStatus
  deadline: string | null
  start_date: string | null
  completed_at: string | null
  color: string | null
  icon: string | null
  is_archived: boolean
  sort_order: number
  counts: { open: number; done: number; total: number }
  progress: number
  tracked_minutes: number
  overdue: boolean
  created_at: string
  updated_at: string
}
export interface ProjectIn {
  id?: string
  name: string
  outcome?: string | null
  description_md?: string | null
  area_id?: string | null
  status?: ProjectStatus
  deadline?: string | null
  start_date?: string | null
  color?: string | null
  icon?: string | null
}
export type ProjectPatchIn = Partial<Omit<ProjectIn, 'id'>> & { is_archived?: boolean; sort_order?: number }
export interface ProjectDetail {
  project: Project
  tasks: Task[]
  done: Task[]
}
export type ProjectFilter = 'open' | 'active' | 'on_hold' | 'completed' | 'cancelled' | 'archived' | 'all'

export interface Resource {
  id: string
  name: string
  notes_md: string | null
  url: string | null
  area: Ref | null
  tags: string[]
  is_archived: boolean
  created_at: string
  updated_at: string
}
export interface ResourceIn {
  id?: string
  name: string
  notes_md?: string | null
  url?: string | null
  area_id?: string | null
  tags?: string[]
}
export type ResourcePatchIn = Partial<Omit<ResourceIn, 'id'>> & { is_archived?: boolean }

export interface Tag {
  id: string
  name: string
  color: string | null
  open_tasks: number
}
export interface TagIn {
  id?: string
  name: string
  color?: string | null
}

// --------------------------------------------------------------------------- //
// Time tracking
// --------------------------------------------------------------------------- //
export interface TimeEntry {
  id: string
  task: TaskRef | null
  project: Ref | null
  area: Ref | null
  description: string | null
  note: string | null
  started_at: string
  ended_at: string | null
  duration_s: number | null
  date: string
  start: string
  end: string | null
  ends_next_day: boolean
  source: TimeEntrySource
  running: boolean
}

export interface RunningTimer {
  entry: TimeEntry
  elapsed_s: number
  server_now: string
}

export interface TimerState {
  running: RunningTimer | null
  server_now: string
}
export interface TimerStartIn {
  id: string
  task_id?: string | null
  project_id?: string | null
  area_id?: string | null
  description?: string | null
  started_at?: string
}
export interface TimerStartResult {
  entry: TimeEntry
  running: RunningTimer | null
  stopped: TimeEntry | null
}
export interface TimerStopIn {
  entry_id?: string
  ended_at?: string
}
export interface TimerStopResult {
  stopped: TimeEntry | null
  server_now: string
}

export interface TimeEntryIn {
  id?: string
  task_id?: string | null
  project_id?: string | null
  area_id?: string | null
  description?: string | null
  note?: string | null
  date: string
  start: string
  end: string
}
export type TimeEntryPatchIn = Partial<Omit<TimeEntryIn, 'id'>>

export interface TimeEntryList {
  from: string
  to: string
  items: TimeEntry[]
  total_minutes: number
  by_day: { date: string; minutes: number }[]
}

export interface TimeSummary {
  period: 'week' | 'month'
  from: string
  to: string
  total_minutes: number
  by_area: { area: Ref | null; minutes: number; target_minutes: number | null }[]
  by_project: { project: Ref | null; minutes: number }[]
  by_day: { date: string; minutes: number }[]
}

// --------------------------------------------------------------------------- //
// Journal
// --------------------------------------------------------------------------- //
export interface JournalEntry {
  id: string
  date: string
  note_md: string | null
  rating: number | null
  mood: number | null
  energy: number | null
  wins_md: string | null
  improvements_md: string | null
  tags: string[]
  created_at: string
  updated_at: string
}
export interface JournalIn {
  id?: string
  note_md?: string | null
  rating?: number | null
  mood?: number | null
  energy?: number | null
  wins_md?: string | null
  improvements_md?: string | null
  tags?: string[]
}
export interface DaySummary {
  date: string
  tasks_done: number
  tasks_done_titles: string[]
  tasks_open_due: number
  time_minutes: number
  time_by_area: { area: Ref | null; minutes: number }[]
  kcal: number
  target_kcal: number | null
  workouts: number
  workout_kcal: number | null
  steps: number | null
  sleep_minutes: number | null
}
export interface JournalDay {
  entry: JournalEntry | null
  day: DaySummary
}
export interface JournalList {
  items: { date: string; rating: number | null; mood: number | null; energy: number | null; has_note: boolean }[]
}

// --------------------------------------------------------------------------- //
// Drift guard: every read model above must be a subtype of the server's
// OpenAPI schema (`npm run gen:api`) and must not invent fields. The generator
// marks defaulted response fields optional and widens literals (priority is
// `number`, `virtual` is `boolean`); these hand-narrowed types are what the
// server actually sends. A schema change that breaks them fails `tsc`.
// --------------------------------------------------------------------------- //
import type { components } from '@/api/generated/schema'

type S = components['schemas']
type NoExtraKeys<T, U> = Exclude<keyof T, keyof U> extends never ? true : false
type Matches<T, U> = [T] extends [U] ? NoExtraKeys<T, U> : false
type Check<T extends true> = T

/** Compile-time only (exported so `noUnusedLocals` keeps it): one entry per read model. */
export type PlannerSchemaGuard = [
  Check<Matches<Ref, S['RefOut']>>,
  Check<Matches<TagRef, S['TagRefOut']>>,
  Check<Matches<TaskRef, S['TaskRefOut']>>,
  Check<Matches<Block, S['BlockOut']>>,
  Check<Matches<Reminder, S['ReminderOut']>>,
  Check<Matches<Task, S['TaskOut']>>,
  Check<Matches<TaskDetail, S['TaskDetailOut']>>,
  Check<Matches<Occurrence, S['OccurrenceOut']>>,
  Check<Matches<TaskPatchResult, S['TaskPatchResultOut']>>,
  Check<Matches<TaskCompleteResult, S['TaskCompleteOut']>>,
  Check<Matches<TaskReopenResult, S['TaskReopenOut']>>,
  Check<Matches<ParseToken, S['ParseTokenOut']>>,
  Check<Matches<ParseResult, S['ParseOut']>>,
  Check<Matches<QuickAddResult, S['QuickAddOut']>>,
  Check<Matches<Counts, S['CountsOut']>>,
  Check<Matches<TodayView, S['TodayViewOut']>>,
  Check<Matches<InboxView, S['InboxViewOut']>>,
  Check<Matches<UpcomingDay, S['UpcomingDayOut']>>,
  Check<Matches<UpcomingView, S['UpcomingViewOut']>>,
  Check<Matches<CalendarDay, S['CalendarDayOut']>>,
  Check<Matches<CalendarView, S['CalendarViewOut']>>,
  Check<Matches<TagView, S['TagViewOut']>>,
  Check<Matches<ArchiveView, S['ArchiveViewOut']>>,
  Check<Matches<SearchResult, S['SearchOut']>>,
  Check<Matches<Area, S['AreaOut']>>,
  Check<Matches<AreaDetail, S['AreaDetailOut']>>,
  Check<Matches<Project, S['ProjectOut']>>,
  Check<Matches<ProjectDetail, S['ProjectDetailOut']>>,
  Check<Matches<Resource, S['ResourceOut']>>,
  Check<Matches<Tag, S['TagOut']>>,
  Check<Matches<TimeEntry, S['TimeEntryOut']>>,
  Check<Matches<RunningTimer, S['RunningTimerOut']>>,
  Check<Matches<TimerState, S['TimerStateOut']>>,
  Check<Matches<TimerStartResult, S['TimerStartOut']>>,
  Check<Matches<TimerStopResult, S['TimerStopOut']>>,
  Check<Matches<TimeEntryList, S['TimeEntryListOut']>>,
  Check<Matches<TimeSummary, S['TimeSummaryOut']>>,
  Check<Matches<JournalEntry, S['JournalOut']>>,
  Check<Matches<DaySummary, S['JournalDaySummaryOut']>>,
  Check<Matches<JournalDay, S['JournalDayOut']>>,
  Check<Matches<JournalList, S['JournalListOut']>>,
]

/** Request bodies: every key must exist on the server model (it forbids unknown fields). */
type InMatches<T, U> = [T] extends [Partial<U>] ? NoExtraKeys<T, U> : false
export type PlannerInputGuard = [
  Check<InMatches<TaskIn, S['TaskIn']>>,
  Check<InMatches<TaskPatchIn, S['TaskPatchIn']>>,
  Check<InMatches<QuickAddIn, S['QuickAddIn']>>,
  Check<InMatches<BlockIn, S['BlockIn']>>,
  Check<InMatches<AreaIn, S['AreaIn']>>,
  Check<InMatches<AreaPatchIn, S['AreaPatchIn']>>,
  Check<InMatches<ProjectIn, S['ProjectIn']>>,
  Check<InMatches<ProjectPatchIn, S['ProjectPatchIn']>>,
  Check<InMatches<ResourceIn, S['ResourceIn']>>,
  Check<InMatches<ResourcePatchIn, S['ResourcePatchIn']>>,
  Check<InMatches<TagIn, S['TagIn']>>,
  Check<InMatches<TimerStartIn, S['TimerStartIn']>>,
  Check<InMatches<TimerStopIn, S['TimerStopIn']>>,
  Check<InMatches<TimeEntryIn, S['TimeEntryIn']>>,
  Check<InMatches<TimeEntryPatchIn, S['TimeEntryPatchIn']>>,
  Check<InMatches<JournalIn, S['JournalIn']>>,
]
