/**
 * Journal autosave.
 *
 * The draft is local. About 800 ms after the last change, only the fields
 * that differ from what the server has are sent (`PUT /journal/{date}` is an
 * upsert by day; fields not sent keep their value; the first create carries a
 * client id). Saves run one after another. Leaving the day, hiding the page or
 * leaving the screen sends what is pending at once.
 *
 * The draft is initialised once per day, so a refetch never overwrites what is
 * being typed. Every write is mirrored into the cached `GET /journal/{date}`
 * right away, so coming back to a day (before the refetch lands, or offline)
 * starts from what was typed, not from the copy fetched before the edits.
 * Offline, the write waits in the queue and the status says so until it has
 * been sent.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { errorMessage } from '@/lib/errors'
import { onQueueChange, pendingFor } from '@/lib/offlineQueue'
import { planPaths } from '../api'
import { newId, planKeys, useDeleteJournal, useSaveJournal } from '../queries'
import type { JournalDay, JournalEntry, JournalIn } from '../types'

export interface JournalFields {
  wins_md: string
  improvements_md: string
  note_md: string
  rating: number | null
  mood: number | null
  energy: number | null
}

export type SaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'queued' | 'error'

export const DEBOUNCE_MS = 800

type Key = keyof JournalFields
/** Request/response representation of some fields (empty text is null). */
type Wire = Pick<JournalIn, Key>

const EMPTY: JournalFields = { wins_md: '', improvements_md: '', note_md: '', rating: null, mood: null, energy: null }
const KEYS = Object.keys(EMPTY) as Key[]

function fieldsOf(entry: JournalEntry | null | undefined): JournalFields {
  if (!entry) return EMPTY
  return {
    wins_md: entry.wins_md ?? '',
    improvements_md: entry.improvements_md ?? '',
    note_md: entry.note_md ?? '',
    rating: entry.rating,
    mood: entry.mood,
    energy: entry.energy,
  }
}

function changedKeys(base: JournalFields, draft: JournalFields): Key[] {
  return KEYS.filter((key) => base[key] !== draft[key])
}

function pick(fields: JournalFields, keys: Key[]): JournalFields {
  const out = { ...EMPTY }
  for (const key of keys) (out as Record<Key, string | number | null>)[key] = fields[key]
  return out
}

/** Wire values of `keys`: empty text is sent as null (the server clears it). */
function wire(fields: JournalFields, keys: Key[]): Wire {
  const out: Record<string, string | number | null> = {}
  for (const key of keys) {
    const value = fields[key]
    out[key] = typeof value === 'string' ? (value.trim() === '' ? null : value) : value
  }
  return out as Wire
}

function merge(base: JournalFields, from: JournalFields, keys: Key[]): JournalFields {
  const out = { ...base }
  for (const key of keys) (out as Record<Key, string | number | null>)[key] = from[key]
  return out
}

interface Live {
  /** The day the draft belongs to (null while the next day loads). */
  date: string | null
  draft: JournalFields
  /** What the server has — or will have once the queued / in-flight writes land. */
  base: JournalFields
  exists: boolean
  clientId: string
  timer: number
  chain: Promise<unknown>
}

export function useJournalAutosave(date: string, server: JournalDay | undefined) {
  const queryClient = useQueryClient()
  const save = useSaveJournal()
  const remove = useDeleteJournal()
  const saveRef = useRef(save.mutateAsync)
  const removeRef = useRef(remove.mutateAsync)
  useEffect(() => {
    saveRef.current = save.mutateAsync
    removeRef.current = remove.mutateAsync
  })

  const live = useRef<Live>({
    date: null,
    draft: EMPTY,
    base: EMPTY,
    exists: false,
    clientId: newId(),
    timer: 0,
    chain: Promise.resolve(),
  })
  const [view, setView] = useState<{ date: string | null; draft: JournalFields; exists: boolean }>({
    date: null,
    draft: EMPTY,
    exists: false,
  })
  const [status, setStatus] = useState<SaveStatus>('idle')
  const [error, setError] = useState<string | null>(null)

  /** Mirror a write into the cached day (`null` = the entry is gone). */
  const mirror = useCallback(
    (day: string, values: Wire | null, clientId: string) => {
      queryClient.setQueryData<JournalDay>(planKeys.journal(day), (old) => {
        if (!old) return old
        if (values === null) return { ...old, entry: null }
        const entry: JournalEntry = old.entry ?? {
          id: clientId,
          date: day,
          note_md: null,
          rating: null,
          mood: null,
          energy: null,
          wins_md: null,
          improvements_md: null,
          tags: [],
          created_at: '',
          updated_at: '',
        }
        return { ...old, entry: { ...entry, ...values } }
      })
    },
    [queryClient],
  )

  /** Send the pending changes of the current day now. */
  const flush = useCallback(() => {
    const state = live.current
    if (state.timer) {
      window.clearTimeout(state.timer)
      state.timer = 0
    }
    const day = state.date
    if (!day) return
    const keys = changedKeys(state.base, state.draft)
    if (keys.length === 0) return
    const values = wire(state.draft, keys)
    const body: JournalIn = { ...values }
    if (!state.exists) body.id = state.clientId
    const before = { values: pick(state.base, keys), exists: state.exists, clientId: state.clientId }

    state.base = merge(state.base, state.draft, keys)
    state.exists = true
    mirror(day, values, state.clientId)
    setView((current) => (current.date === day ? { ...current, exists: true } : current))
    const current = () => live.current.date === day

    state.chain = state.chain.then(async () => {
      if (current()) setStatus('saving')
      try {
        const result = await saveRef.current({ date: day, body })
        if (current()) {
          setStatus(result.queued ? 'queued' : 'saved')
          setError(null)
        }
      } catch (failure) {
        // the server kept none of it: undo the mirror; on this day the fields go again with the next change or a retry
        mirror(day, before.exists ? wire(before.values, keys) : null, before.clientId)
        if (!current()) return
        const now = live.current
        now.base = merge(now.base, before.values, keys)
        if (!before.exists) now.exists = false
        setView((value) => (value.date === day ? { ...value, exists: now.exists } : value))
        setStatus('error')
        setError(errorMessage(failure))
      }
    })
  }, [mirror])

  /** Change some fields; they are sent ≈ 800 ms after the last change. */
  const edit = useCallback(
    (patch: Partial<JournalFields>) => {
      const state = live.current
      if (!state.date) return
      state.draft = { ...state.draft, ...patch }
      const draft = state.draft
      setView((current) => ({ ...current, draft }))
      setStatus('pending')
      if (state.timer) window.clearTimeout(state.timer)
      state.timer = window.setTimeout(() => {
        state.timer = 0
        flush()
      }, DEBOUNCE_MS)
    },
    [flush],
  )

  // Initialise once per day; leaving a day sends its pending changes first.
  const loaded = server !== undefined
  const entry = server?.entry
  useEffect(() => {
    const state = live.current
    if (state.date === date) return
    if (state.date) flush()
    if (!loaded || !date) {
      if (state.date !== null) {
        state.date = null
        setView({ date: null, draft: EMPTY, exists: false })
      }
      return
    }
    const fields = fieldsOf(entry)
    state.date = date
    state.draft = fields
    state.base = fields
    state.exists = !!entry
    state.clientId = newId()
    setView({ date, draft: fields, exists: !!entry })
    setStatus('idle')
    setError(null)
  }, [date, loaded, entry, flush])

  // Hiding the page (app switch, lock screen) or leaving the screen sends what is pending.
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') flush()
    }
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', flush)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', flush)
      flush()
    }
  }, [flush])

  // Queued offline: «Сохранено» once this day's writes have left the queue.
  useEffect(() => {
    if (status !== 'queued' || !view.date) return
    const day = view.date
    return onQueueChange(() => {
      void pendingFor(planPaths.journal(day)).then((pending) => {
        if (pending.length === 0 && live.current.date === day) {
          setStatus((value) => (value === 'queued' ? 'saved' : value))
        }
      })
    })
  }, [status, view.date])

  /** Delete the day's entry (after any save in flight); edits not sent yet are dropped. */
  const deleteEntry = useCallback(async () => {
    const state = live.current
    const day = state.date
    if (!day) return null
    if (state.timer) {
      window.clearTimeout(state.timer)
      state.timer = 0
    }
    const run = state.chain.then(() => removeRef.current({ date: day }))
    state.chain = run.catch(() => undefined)
    try {
      const result = await run
      mirror(day, null, state.clientId)
      if (live.current.date === day) {
        const now = live.current
        now.draft = EMPTY
        now.base = EMPTY
        now.exists = false
        now.clientId = newId()
        setView({ date: day, draft: EMPTY, exists: false })
        setStatus('idle')
        setError(null)
      }
      return result
    } catch (failure) {
      // the text stays; anything not sent yet goes with the next change
      const now = live.current
      if (now.date === day && changedKeys(now.base, now.draft).length > 0) {
        setStatus('pending')
        now.timer = window.setTimeout(() => {
          now.timer = 0
          flush()
        }, DEBOUNCE_MS)
      }
      throw failure
    }
  }, [flush, mirror])

  return {
    /** The draft of `date`, or null while that day loads. */
    draft: date && view.date === date ? view.draft : null,
    exists: view.exists,
    status,
    error,
    edit,
    flush,
    deleteEntry,
  }
}
