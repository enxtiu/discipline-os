/** Integration endpoints (stage 9): `/integrations/*` (a paired device only). */
import { api } from '@/api/client'
import type {
  IntegrationList,
  NotionConnectIn,
  NotionIntegration,
  NotionKind,
  NotionSyncStart,
} from './types'

export const integrationsApi = {
  list: () => api.get<IntegrationList>('/v1/integrations'),
  notion: () => api.get<NotionIntegration>('/v1/integrations/notion'),
  connect: (payload: NotionConnectIn) => api.post<NotionIntegration>('/v1/integrations/notion/connect', payload),
  setup: (kinds: NotionKind[]) => api.post<NotionIntegration>('/v1/integrations/notion/setup', { kinds }),
  setKinds: (kinds: NotionKind[]) => api.patch<NotionIntegration>('/v1/integrations/notion', { kinds }),
  sync: () => api.post<NotionSyncStart>('/v1/integrations/notion/sync'),
  disconnect: () => api.delete<void>('/v1/integrations/notion'),
}
