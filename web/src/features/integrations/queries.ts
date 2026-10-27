/**
 * Integration queries (stage 9). The Notion card polls while a sync runs (the server
 * says so: `sync.state === 'running'`), and stops by itself when it is done.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { integrationsApi } from './api'
import type { NotionIntegration, NotionKind } from './types'

export const integrationKeys = {
  all: ['integrations'] as const,
  notion: ['integrations', 'notion'] as const,
}

export const POLL_MS = 1500

export function useNotion() {
  return useQuery({
    queryKey: integrationKeys.notion,
    queryFn: integrationsApi.notion,
    refetchInterval: (query) => (query.state.data?.sync.state === 'running' ? POLL_MS : false),
  })
}

/** Optimistic: the switch flips at once; a failure puts the old value back. */
export function useSetKinds(onError?: (error: unknown) => void) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (kinds: NotionKind[]) => integrationsApi.setKinds(kinds),
    onMutate: async (kinds) => {
      await queryClient.cancelQueries({ queryKey: integrationKeys.notion })
      const previous = queryClient.getQueryData<NotionIntegration>(integrationKeys.notion)
      if (previous) {
        queryClient.setQueryData<NotionIntegration>(integrationKeys.notion, {
          ...previous,
          kinds: previous.kinds.map((item) => ({ ...item, enabled: kinds.includes(item.kind) })),
        })
      }
      return { previous }
    },
    onError: (error, _kinds, context) => {
      if (context?.previous) queryClient.setQueryData(integrationKeys.notion, context.previous)
      onError?.(error)
    },
    onSuccess: (data) => queryClient.setQueryData(integrationKeys.notion, data),
  })
}
