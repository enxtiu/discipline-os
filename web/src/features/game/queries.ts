/**
 * Gamification queries & mutations (stage 8).
 *
 * * Every read lives under `['gamification', …]`. The server recomputes today's
 *   score, XP, streaks and quests on read, so after any successful write
 *   anywhere in the app (a meal, water, a set, a task …) the gamification reads
 *   are invalidated once (debounced): the Today card follows the day without
 *   every feature having to know about it. Replayed offline writes count too.
 * * Claiming is idempotent on the server; the claimed quest is put in place
 *   right away, then XP, level and achievements are refetched.
 */
import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { onQueueChange } from '@/lib/offlineQueue'
import { gameApi } from './api'
import type { GameProfile, GameToday, QuestClaim, UserQuest } from './types'

export const gameKeys = {
  all: ['gamification'] as const,
  today: ['gamification', 'today'] as const,
  profile: ['gamification', 'profile'] as const,
}

const CLAIM_KEY = ['gamification', 'claim'] as const
const SEEN_KEY = ['gamification', 'seen'] as const

export function useGameToday() {
  return useQuery({
    queryKey: gameKeys.today,
    queryFn: gameApi.today,
    refetchOnWindowFocus: true,
  })
}

export function useGameProfile() {
  return useQuery({
    queryKey: gameKeys.profile,
    queryFn: gameApi.profile,
    refetchOnWindowFocus: true,
  })
}

// --------------------------------------------------------------------------- //
// Follow the day: invalidate after writes elsewhere in the app
// --------------------------------------------------------------------------- //
const REFRESH_DELAY_MS = 350
let installedOn: QueryClient | null = null

/**
 * Installs (once per QueryClient, for the app's lifetime) the listeners that
 * mark the gamification reads stale after any successful write: active screens
 * refetch, inactive ones refetch when they mount again. Bursts (a workout's
 * sets) collapse into one refetch.
 */
export function useGameAutoRefresh(): void {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (installedOn === queryClient) return
    installedOn = queryClient
    let timer: number | undefined
    const refresh = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: gameKeys.all }), REFRESH_DELAY_MS)
    }
    queryClient.getMutationCache().subscribe((event) => {
      if (event.type !== 'updated' || event.action.type !== 'success') return
      const key = event.mutation.options.mutationKey
      if (Array.isArray(key) && key[0] === gameKeys.all[0]) return // our own writes refresh themselves
      refresh()
    })
    // the offline queue replays writes outside TanStack: a shrinking queue means data changed
    let pending = -1
    onQueueChange((count) => {
      if (pending > count) refresh()
      pending = count
    })
  }, [queryClient])
}

// --------------------------------------------------------------------------- //
// Writes
// --------------------------------------------------------------------------- //
function replaceQuest(quests: UserQuest[], quest: UserQuest): UserQuest[] {
  return quests.map((item) => (item.id === quest.id ? quest : item))
}

export function useClaimQuest() {
  const queryClient = useQueryClient()
  return useMutation<QuestClaim, Error, UserQuest>({
    mutationKey: CLAIM_KEY,
    mutationFn: (quest) => gameApi.claimQuest(quest.id),
    onSuccess: (result) => {
      queryClient.setQueryData<GameToday>(gameKeys.today, (data) =>
        data ? { ...data, quests: replaceQuest(data.quests, result.quest) } : data,
      )
      void queryClient.invalidateQueries({ queryKey: gameKeys.all })
    },
  })
}

/** Celebrated achievements → seen (they are dropped from `celebrate` locally at once). */
export function useMarkSeen() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, string[]>({
    mutationKey: SEEN_KEY,
    mutationFn: (ids) => gameApi.markSeen(ids),
    onMutate: (ids) => {
      queryClient.setQueryData<GameToday>(gameKeys.today, (data) =>
        data ? { ...data, celebrate: data.celebrate.filter((item) => !ids.includes(item.id)) } : data,
      )
      queryClient.setQueryData<GameProfile>(gameKeys.profile, (data) =>
        data
          ? { ...data, achievements: data.achievements.map((item) => (ids.includes(item.id) ? { ...item, seen: true } : item)) }
          : data,
      )
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: gameKeys.today }),
  })
}
