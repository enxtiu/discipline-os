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
 * Offline, the write waits in the queue (autosaves of one day coalesce into one
 * write) and the status says so until it has been sent.
 *
 * Stage 9: every write carries `base` — the values the edited fields had when
 * this device last knew them — so a field changed on another device meanwhile
 * answers `edit_conflict` instead of being overwritten. A conflict (sent now or
 * replayed from the queue) shows both values: «Оставить их версию» takes the
 * other device's text, «Сохранить мою» sends this one without the check. While
 * it is open, the conflicting fields are not sent.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/client'
import { errorMessage } from '@/lib/errors'
import { listEntries, onQueueChange, resolveConflict, retryEntry, statusOf } from '@/lib/offlineQueue'
import { failureReason } from '@/features/sync/labels'
import { planPaths } from '../api'
import { dayTitle } from '../calendar/dates'
import { newId, planKeys, useDeleteJournal, useSaveJournal } from '../queries'
import type { EditConflictDetails, JournalDay, JournalEntry, JournalIn } from '../types'

export interface JournalFields {
  wins_md: string
  improvements_md: string
  note_md: string
  rating: number | null
  mood: number | null
  energy: number | null
}

export type SaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'queued' | 'error' | 'conflict'

export const DEBOUNCE_MS = 800

type Key = keyof JournalFields
/** Request/response representation of some fields (empty text is null). */
type Wire = Pick<JournalIn, Key>

const EMPTY: JournalFields = { wins_md: '', improvements_md: '', note_md: '', rating: null, mood: null, energy: null }
const KEYS = Object.keys(EMPTY) as Key[]

function isKey(value: string): value is Key {
  return (KEYS as string[]).includes(value)
}

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

/** A conflict waiting for the user's choice. */
export interface JournalConflict {
  fields: Key[]
  /** The entry as the server has it now (null: the other device deleted the day's entry). */
  theirs: JournalEntry | null
  /** This device's values of those fields (as sent). */
  mine: Wire
  /** The queued write it belongs to (a replay), or null (a write sent online). */
  entryId: number | null
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
  /** Fields of an open conflict: not sent until the user chooses. */
  held: Set<Key>
  /** Fields the user chose to keep: sent once without the check. */
  force: Set<Key>
  /** The day must start again from the server's copy (a conflict settled in the sync sheet). */
  resync: boolean
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
    held: new Set(),
    force: new Set(),
    resync: false,
  })
  const [view, setView] = useState<{ date: string | null; draft: JournalFields; exists: boolean }>({
    date: null,
    draft: EMPTY,
    exists: false,
  })
  const [status, setStatus] = useState<SaveStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<JournalConflict | null>(null)
  /** A refused queued write of this day (for «Повторить»). */
  const failedEntry = useRef<number | null>(null)

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

  /** Open a conflict: the conflicting fields now start from the server's values and are held back. */
  const openConflict = useCallback((details: EditConflictDetails<JournalEntry>, entryId: number | null) => {
    const state = live.current
    const fields = (details.fields ?? []).filter(isKey)
    if (fields.length === 0) return false
    state.base = merge(state.base, fieldsOf(details.current), fields)
    for (const field of fields) state.held.add(field)
    if (!details.current) state.exists = false // deleted elsewhere: the next write creates it again
    setConflict({ fields, theirs: details.current ?? null, mine: wire(state.draft, fields), entryId })
    setStatus('conflict')
    return true
  }, [])

  /** Send the pending changes of the current day now (fields held by a conflict wait). */
  const flush = useCallback(() => {
    const state = live.current
    if (state.timer) {
      window.clearTimeout(state.timer)
      state.timer = 0
    }
    const day = state.date
    if (!day) return
    const keys = changedKeys(state.base, state.draft).filter((key) => !state.held.has(key))
    if (keys.length === 0) return
    const values = wire(state.draft, keys)
    const checked = keys.filter((key) => !state.force.has(key))
    const body: JournalIn = { ...values }
    if (checked.length > 0) body.base = wire(state.base, checked)
    if (!state.exists) body.id = state.clientId
    for (const key of keys) state.force.delete(key)
    const before = { values: pick(state.base, keys), exists: state.exists, clientId: state.clientId }

    state.base = merge(state.base, state.draft, keys)
    state.exists = true
    mirror(day, values, state.clientId)
    setView((current) => (current.date === day ? { ...current, exists: true } : current))
    const current = () => live.current.date === day

    state.chain = state.chain.then(async () => {
      if (current()) setStatus('saving')
      try {
        const result = await saveRef.current({ date: day, body, title: dayTitle(day) })
        if (current()) {
          setStatus((value) => (value === 'conflict' ? value : result.queued ? 'queued' : 'saved'))
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
        if (failure instanceof ApiError && failure.code === 'edit_conflict') {
          const details = failure.details as unknown as EditConflictDetails<JournalEntry>
          if (openConflict(details, null)) {
            if (details.current) mirror(day, wire(fieldsOf(details.current), KEYS), before.clientId)
            return
          }
        }
        setStatus('error')
        setError(errorMessage(failure))
      }
    })
  }, [mirror, openConflict])

  /** Change some fields; they are sent ≈ 800 ms after the last change. */
  const edit = useCallback(
    (patch: Partial<JournalFields>) => {
      const state = live.current
      if (!state.date) return
      state.draft = { ...state.draft, ...patch }
      const draft = state.draft
      setView((current) => ({ ...current, draft }))
      setStatus((value) => (value === 'conflict' ? value : 'pending'))
      if (state.timer) window.clearTimeout(state.timer)
      state.timer = window.setTimeout(() => {
        state.timer = 0
        flush()
      }, DEBOUNCE_MS)
    },
    [flush],
  )

  /** Settle the open conflict (see the module doc). */
  const resolve = useCallback(
    async (choice: 'mine' | 'theirs') => {
      const open = conflict
      const state = live.current
      const day = state.date
      if (!open || !day) return
      for (const field of open.fields) state.held.delete(field)
      setConflict(null)
      if (choice === 'theirs') {
        const theirs = fieldsOf(open.theirs)
        state.draft = merge(state.draft, theirs, open.fields)
        state.base = merge(state.base, theirs, open.fields)
        const draft = state.draft
        setView((current) => (current.date === day ? { ...current, draft } : current))
        mirror(day, wire(theirs, open.fields), state.clientId)
        if (open.entryId !== null) await resolveConflict(open.entryId, 'theirs')
      } else if (open.entryId !== null) {
        // the queued write goes again without the check; these values are what the server will have
        state.base = merge(state.base, state.draft, open.fields)
        await resolveConflict(open.entryId, 'mine')
        setStatus('queued')
        return
      } else {
        for (const field of open.fields) state.force.add(field)
      }
      if (changedKeys(state.base, state.draft).length > 0) {
        setStatus('pending')
        flush()
      } else setStatus('saved')
    },
    [conflict, flush, mirror],
  )

  // Initialise once per day; leaving a day sends its pending changes first.
  const loaded = server !== undefined
  const entry = server?.entry
  useEffect(() => {
    const state = live.current
    if (state.date === date && !(state.resync && loaded)) return
    if (state.date && state.date !== date) flush()
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
    state.held = new Set()
    state.force = new Set()
    state.resync = false
    failedEntry.current = null
    setView({ date, draft: fields, exists: !!entry })
    setConflict(null)
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

  // Queued offline: «Сохранено» once this day's writes have left the queue — or the
  // conflict / refusal the replay met (settled here or in the sync sheet).
  const watching = status === 'queued' || status === 'conflict' || status === 'error'
  const conflictEntry = conflict?.entryId ?? null
  useEffect(() => {
    if (!watching || !view.date) return
    const day = view.date
    const path = planPaths.journal(day)
    return onQueueChange(() => {
      void listEntries().then((all) => {
        const state = live.current
        if (state.date !== day) return
        const own = all.filter((item) => item.path === path)
        const conflicted = own.find((item) => statusOf(item) === 'conflict')
        const failed = own.find((item) => statusOf(item) === 'failed')
        if (conflicted?.id !== undefined && conflicted.conflict) {
          if (conflictEntry !== conflicted.id) {
            openConflict(conflicted.conflict as EditConflictDetails<JournalEntry>, conflicted.id)
          }
          return
        }
        if (conflictEntry !== null) {
          // settled in the sync sheet: start again from what the server has
          state.resync = true
          setConflict(null)
          state.held = new Set()
          setStatus('queued')
          void queryClient.invalidateQueries({ queryKey: planKeys.journal(day) })
          return
        }
        if (failed?.id !== undefined) {
          failedEntry.current = failed.id
          setStatus('error')
          setError(failureReason(failed))
          return
        }
        failedEntry.current = null
        if (!own.some((item) => statusOf(item) === 'pending')) {
          setStatus((value) => (value === 'queued' || value === 'error' ? 'saved' : value))
        }
      })
    })
  }, [watching, view.date, conflictEntry, openConflict, queryClient])

  /** «Повторить»: a refused queued write goes again; otherwise what is pending is sent now. */
  const retry = useCallback(() => {
    if (failedEntry.current !== null) {
      void retryEntry(failedEntry.current)
      setStatus('queued')
      return
    }
    flush()
  }, [flush])

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
        now.held = new Set()
        now.force = new Set()
        setView({ date: day, draft: EMPTY, exists: false })
        setConflict(null)
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
    conflict: date && view.date === date ? conflict : null,
    resolve,
    edit,
    flush: retry,
    deleteEntry,
  }
}
