/**
 * Optimistic nutrition (stage 9): meals and water logged (or deleted) while
 * their write waits in the offline queue are shown in the day at once.
 *
 * The server's day stays the truth — its targets, day boundaries and totals:
 * a waiting meal only adds the kcal/macros the log sheet already showed, a
 * waiting delete only takes the row's own numbers away, and the rows are
 * marked «ждёт отправки». Once the write is sent the day is refetched and the
 * server's answer replaces the optimistic row (a sent write keeps showing until
 * that refetch lands, so nothing blinks). Display only: scores and streaks are
 * computed on the server and never see these numbers.
 */
import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import type { DaySummary, MealItem, MealLog, MealType, WaterLog } from '@/api/types'
import { onQueueSent, statusOf, type QueuedRequest } from '@/lib/offlineQueue'
import { useQueueEntries } from '@/features/sync/useQueue'

export const MEALS_PATH = '/v1/nutrition/meals'
export const WATER_PATH = '/v1/nutrition/water'

/** What the log sheet showed for a meal (kept in the queue entry's `meta.meal`). */
export interface MealPreview {
  name: string
  kcal: number
  protein_g: number
  fat_g: number
  carbs_g: number
  fiber_g: number
  items: { name: string; grams: number; kcal: number; protein_g: number; fat_g: number; carbs_g: number; fiber_g: number }[]
}

const MEAL_ORDER: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack', 'other']
const MACROS = ['kcal', 'protein_g', 'fat_g', 'carbs_g', 'fiber_g'] as const

export function isNutritionEntry(entry: QueuedRequest): boolean {
  return entry.path.startsWith('/v1/nutrition/')
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

const round2 = (value: number) => Math.round(value * 100) / 100

// --------------------------------------------------------------------------- //
// Writes sent in this session, until the day is refetched after them
// --------------------------------------------------------------------------- //
let recent: { entry: QueuedRequest; sentAt: number }[] = []
let recentVersion = 0
const recentListeners = new Set<() => void>()

onQueueSent((entry) => {
  if (!isNutritionEntry(entry)) return
  const now = Date.now()
  recent = [...recent.filter((item) => now - item.sentAt < 60_000), { entry, sentAt: now }]
  recentVersion += 1
  for (const listener of recentListeners) listener()
})

function subscribeRecent(listener: () => void) {
  recentListeners.add(listener)
  return () => {
    recentListeners.delete(listener)
  }
}

// --------------------------------------------------------------------------- //
// The merge
// --------------------------------------------------------------------------- //
function optimisticMeal(entry: QueuedRequest, preview: MealPreview, day: string): MealLog {
  const body = record(entry.body)
  const id = String(body.id)
  const items: MealItem[] = preview.items.map((item, index) => ({
    id: `${id}:${index}`,
    name: item.name,
    grams: item.grams,
    quantity: 1,
    unit: 'g',
    kcal: item.kcal,
    protein_g: item.protein_g,
    fat_g: item.fat_g,
    carbs_g: item.carbs_g,
    fiber_g: item.fiber_g,
    item_source: 'custom',
    match_method: 'manual',
    is_edited: false,
    sort_order: index,
  }))
  return {
    id,
    client_uuid: id,
    logged_at: typeof body.logged_at === 'string' ? body.logged_at : new Date(entry.createdAt).toISOString(),
    local_day: day,
    meal_type: (body.meal_type as MealType) ?? 'other',
    source: (body.source as MealLog['source']) ?? 'manual',
    title: typeof body.title === 'string' ? body.title : null,
    notes: null,
    kcal: preview.kcal,
    protein_g: preview.protein_g,
    fat_g: preview.fat_g,
    carbs_g: preview.carbs_g,
    fiber_g: preview.fiber_g,
    item_count: items.length,
    ai_job_id: null,
    has_photo: false,
    items,
  }
}

/** The server's day with the waiting writes applied; `pendingIds` are the optimistic rows. */
export function applyPending(
  data: DaySummary,
  entries: QueuedRequest[],
  day: string,
): { data: DaySummary; pendingIds: Set<string> } {
  let meals = [...data.meals]
  let water: WaterLog[] = [...(data.water_logs ?? [])]
  const pendingIds = new Set<string>()
  let changed = false
  const known = (rows: { id: string; client_uuid: string }[], id: string) =>
    rows.some((row) => row.id === id || row.client_uuid === id)

  for (const entry of entries) {
    if (statusOf(entry) !== 'pending') continue // refused or conflicting writes are in the sync sheet, not in the day
    const body = record(entry.body)
    if (entry.method === 'POST' && entry.path === MEALS_PATH) {
      const preview = entry.meta?.meal as MealPreview | undefined
      const id = typeof body.id === 'string' ? body.id : null
      if (!id || !preview || entry.meta?.day !== day || known(meals, id)) continue
      meals.push(optimisticMeal(entry, preview, day))
      pendingIds.add(id)
      changed = true
    } else if (entry.method === 'DELETE' && entry.path.startsWith(`${MEALS_PATH}/`)) {
      const id = entry.path.slice(MEALS_PATH.length + 1)
      const kept = meals.filter((meal) => meal.id !== id && meal.client_uuid !== id)
      changed = changed || kept.length !== meals.length
      meals = kept
    } else if (entry.method === 'POST' && entry.path === WATER_PATH) {
      const id = typeof body.id === 'string' ? body.id : null
      if (!id || entry.meta?.day !== day || known(water, id)) continue
      water.push({
        id,
        client_uuid: id,
        logged_at: typeof body.logged_at === 'string' ? body.logged_at : new Date(entry.createdAt).toISOString(),
        amount_ml: Number(body.amount_ml) || 0,
        source: 'manual',
      })
      pendingIds.add(id)
      changed = true
    } else if (entry.method === 'DELETE' && entry.path.startsWith(`${WATER_PATH}/`)) {
      const id = entry.path.slice(WATER_PATH.length + 1)
      const kept = water.filter((row) => row.id !== id && row.client_uuid !== id)
      changed = changed || kept.length !== water.length
      water = kept
    }
  }
  if (!changed) return { data, pendingIds }

  // totals = the server's totals ± the rows that came or went (never recomputed from scratch)
  const totals = { ...data.totals }
  const byType = new Map(data.by_meal_type.map((bucket) => [bucket.meal_type, { ...bucket }]))
  const apply = (meal: MealLog, sign: 1 | -1) => {
    for (const key of MACROS) totals[key] = round2(totals[key] + sign * meal[key])
    const bucket = byType.get(meal.meal_type) ?? {
      meal_type: meal.meal_type,
      meals: 0,
      kcal: 0,
      protein_g: 0,
      fat_g: 0,
      carbs_g: 0,
      fiber_g: 0,
    }
    bucket.meals += sign
    for (const key of MACROS) bucket[key] = round2(bucket[key] + sign * meal[key])
    byType.set(meal.meal_type, bucket)
  }
  const after = new Set(meals)
  for (const meal of data.meals) if (!after.has(meal)) apply(meal, -1)
  for (const meal of meals) if (pendingIds.has(meal.id)) apply(meal, 1)
  meals.sort((a, b) => Date.parse(a.logged_at) - Date.parse(b.logged_at))
  water.sort((a, b) => Date.parse(a.logged_at) - Date.parse(b.logged_at))
  const waterDelta =
    water.filter((row) => pendingIds.has(row.id)).reduce((sum, row) => sum + row.amount_ml, 0) -
    (data.water_logs ?? []).filter((row) => !water.includes(row)).reduce((sum, row) => sum + row.amount_ml, 0)

  return {
    data: {
      ...data,
      totals: {
        kcal: Math.max(0, totals.kcal),
        protein_g: Math.max(0, totals.protein_g),
        fat_g: Math.max(0, totals.fat_g),
        carbs_g: Math.max(0, totals.carbs_g),
        fiber_g: Math.max(0, totals.fiber_g),
      },
      by_meal_type: MEAL_ORDER.map((type) => byType.get(type)).filter(
        (bucket): bucket is NonNullable<typeof bucket> => !!bucket && bucket.meals > 0,
      ),
      meals,
      water_logs: water,
      water_ml: Math.max(0, data.water_ml + waterDelta),
      logged_meals: meals.length,
    },
    pendingIds,
  }
}

/**
 * Refetch the day (and what moves with it) once a nutrition write left the queue;
 * mounted by every optimistic day view.
 */
function useNutritionQueueRefresh() {
  const queryClient = useQueryClient()
  useEffect(
    () =>
      onQueueSent((entry) => {
        if (!isNutritionEntry(entry)) return
        void queryClient.invalidateQueries({ queryKey: ['nutrition'] })
        void queryClient.invalidateQueries({ queryKey: ['foods'] })
      }),
    [queryClient],
  )
}

/** `data` = the server's day + the waiting writes (see the module doc). */
export function usePendingDay(data: DaySummary | undefined, dataUpdatedAt: number, day: string) {
  useNutritionQueueRefresh()
  const queued = useQueueEntries(isNutritionEntry)
  const version = useSyncExternalStore(subscribeRecent, () => recentVersion)
  return useMemo(() => {
    if (!data) return { data, pendingIds: new Set<string>() }
    // sent after this copy of the day was fetched: the server's row is not in it yet
    const sent = recent.filter((item) => item.sentAt > dataUpdatedAt).map((item) => item.entry)
    return applyPending(data, [...sent, ...queued], day)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, dataUpdatedAt, day, queued, version])
}
