/**
 * Integrations (stage 9) — mirror `app/schemas/integrations.py` one to one.
 * The secret is sent once (`NotionConnectIn`) and never comes back: responses only
 * say that one is set.
 */

export const NOTION_KINDS = ['tasks', 'projects', 'areas', 'journal', 'days'] as const
export type NotionKind = (typeof NOTION_KINDS)[number]
export type IntegrationStatus = 'connected' | 'error' | 'disconnected'
export type DatabaseState = 'ok' | 'missing' | 'none'
export type SyncResult = 'ok' | 'partial' | 'error' | 'interrupted'

export interface IntegrationPage {
  id: string
  title: string | null
  url: string
}

export interface NotionKindCounts {
  created: number
  updated: number
  trashed: number
  unchanged: number
  failed: number
  pending: number
  total: number
  state: string
}

export interface NotionKindInfo {
  kind: NotionKind
  enabled: boolean
  database: DatabaseState
  url: string | null
  last_run: NotionKindCounts | null
}

export interface NotionTotals {
  created: number
  updated: number
  trashed: number
  unchanged: number
  failed: number
  pending: number
  total: number
}

export interface NotionSync {
  state: 'idle' | 'running'
  run_id: string | null
  started_at: string | null
  finished_at: string | null
  last_sync_at: string | null
  result: SyncResult | null
  /** notion_* code (errors.notion_*) */
  error: string | null
  error_kind: NotionKind | null
  totals: NotionTotals | null
  requests: number
}

export interface NotionIntegration {
  provider: 'notion'
  status: IntegrationStatus
  connected: boolean
  secret_set: boolean
  bot_name: string | null
  workspace_name: string | null
  page: IntegrationPage | null
  kinds: NotionKindInfo[]
  sync: NotionSync
  error: string | null
  notion_version: string
  interval_minutes: number
}

export interface IntegrationSummary {
  provider: 'notion'
  status: IntegrationStatus
  connected: boolean
  last_sync_at: string | null
  error: string | null
}

export interface IntegrationList {
  items: IntegrationSummary[]
}

export interface NotionSyncStart {
  run_id: string
  state: 'running'
}

export interface NotionConnectIn {
  secret: string
  page: string
}

export interface NotionKindsIn {
  kinds: NotionKind[]
}

// --------------------------------------------------------------------------- //
// Drift guard: the read models must be subtypes of the server's OpenAPI schema
// (`npm run gen:api`) and must not invent fields; the generator marks defaulted
// response fields optional, the server always sends them. A schema change that
// breaks them fails `tsc`.
// --------------------------------------------------------------------------- //
import type { components } from '@/api/generated/schema'

type S = components['schemas']
type NoExtraKeys<T, U> = Exclude<keyof T, keyof U> extends never ? true : false
type Matches<T, U> = [T] extends [U] ? NoExtraKeys<T, U> : false
type Check<T extends true> = T

/** Compile-time only (exported so `noUnusedLocals` keeps it). */
export type IntegrationsSchemaGuard = [
  Check<Matches<IntegrationPage, S['IntegrationPageOut']>>,
  Check<Matches<NotionKindCounts, Required<S['NotionKindCountsOut']>>>,
  Check<Matches<NotionKindInfo, Required<S['NotionKindOut']>>>,
  Check<Matches<NotionTotals, Required<S['NotionTotalsOut']>>>,
  Check<Matches<NotionSync, Required<S['NotionSyncOut']>>>,
  Check<Matches<NotionIntegration, Required<S['NotionIntegrationOut']>>>,
  Check<Matches<IntegrationSummary, S['IntegrationSummaryOut']>>,
  Check<Matches<IntegrationList, S['IntegrationListOut']>>,
  Check<Matches<NotionSyncStart, Required<S['NotionSyncStartOut']>>>,
]

type InMatches<T, U> = [T] extends [Partial<U>] ? NoExtraKeys<T, U> : false
export type IntegrationsInputGuard = [
  Check<InMatches<NotionConnectIn, S['NotionConnectIn']>>,
  Check<InMatches<NotionKindsIn, S['NotionKindsIn']>>,
  Check<InMatches<NotionKindsIn, S['NotionSetupIn']>>,
]
