/**
 * Add a time entry by hand, or edit / delete one. Date, start and end are the
 * local wall-clock fields the server converts with your timezone; an end at or
 * before the start means the next day. The server checks the rest (1 min …
 * 24 h, not in the future) and its message is shown as is.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Callout, Field, Input, Sheet } from '@/components/ui'
import { IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { toMinutes } from '../calendar/dates'
import { formatMinutes } from '../format'
import { useCreateTimeEntry, useDeleteTimeEntry, useUpdateTimeEntry } from '../queries'
import type { Ref, TaskRef, TimeEntry, TimeEntryPatchIn } from '../types'
import { AreaSelect, ProjectSelect, TaskSelect } from './pickers'

interface Form {
  date: string
  start: string
  end: string
  task: TaskRef | null
  project: Ref | null
  area: Ref | null
  description: string
  note: string
}

const TIME = /^\d{2}:\d{2}$/
const DAY = /^\d{4}-\d{2}-\d{2}$/

function fromEntry(entry: TimeEntry): Form {
  return {
    date: entry.date,
    start: entry.start,
    end: entry.end ?? '',
    task: entry.task,
    project: entry.project,
    area: entry.area,
    description: entry.description ?? '',
    note: entry.note ?? '',
  }
}

function blank(day: string | undefined): Form {
  return { date: day ?? '', start: '', end: '', task: null, project: null, area: null, description: '', note: '' }
}

export function TimeEntrySheet({
  open,
  onClose,
  entry,
  today,
}: {
  open: boolean
  onClose: () => void
  /** null → a new manual entry. */
  entry: TimeEntry | null
  /** The server's today: default date and the latest allowed one. */
  today: string | undefined
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const create = useCreateTimeEntry()
  const update = useUpdateTimeEntry()
  const remove = useDeleteTimeEntry()
  const [form, setForm] = useState<Form>(() => (entry ? fromEntry(entry) : blank(today)))
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setForm(entry ? fromEntry(entry) : blank(today))
    setConfirming(false)
  }, [open, entry, today])

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((current) => ({ ...current, [key]: value }))

  const timesValid = DAY.test(form.date) && TIME.test(form.start) && TIME.test(form.end)
  const minutes = useMemo(() => {
    if (!TIME.test(form.start) || !TIME.test(form.end)) return null
    const span = (toMinutes(form.end) - toMinutes(form.start) + 1440) % 1440
    return span === 0 ? 1440 : span
  }, [form.start, form.end])
  const nextDay = TIME.test(form.start) && TIME.test(form.end) && toMinutes(form.end) <= toMinutes(form.start)

  const submit = async () => {
    if (!timesValid || busy) return
    setBusy(true)
    try {
      if (entry) {
        const patch: TimeEntryPatchIn = {}
        if (form.date !== entry.date || form.start !== entry.start || form.end !== (entry.end ?? '')) {
          patch.date = form.date
          patch.start = form.start
          patch.end = form.end
        }
        if ((form.task?.id ?? null) !== (entry.task?.id ?? null)) patch.task_id = form.task?.id ?? null
        if ((form.project?.id ?? null) !== (entry.project?.id ?? null)) patch.project_id = form.project?.id ?? null
        if ((form.area?.id ?? null) !== (entry.area?.id ?? null)) patch.area_id = form.area?.id ?? null
        if (form.description.trim() !== (entry.description ?? '')) patch.description = form.description.trim() || null
        if (form.note.trim() !== (entry.note ?? '')) patch.note = form.note.trim() || null
        if (Object.keys(patch).length > 0) {
          const result = await update.mutateAsync({ id: entry.id, patch })
          toast.success(result.queued ? t('timer.queued') : t('timer.entry_saved'))
        }
      } else {
        const result = await create.mutateAsync({
          date: form.date,
          start: form.start,
          end: form.end,
          task_id: form.task?.id,
          project_id: form.project?.id,
          area_id: form.area?.id,
          description: form.description.trim() || undefined,
          note: form.note.trim() || undefined,
        })
        toast.success(result.queued ? t('timer.queued') : t('timer.entry_added'))
      }
      onClose()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async () => {
    if (!entry || busy) return
    setBusy(true)
    try {
      const result = await remove.mutateAsync({ id: entry.id })
      toast.success(result.queued ? t('timer.queued') : t('timer.entry_deleted'))
      onClose()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      size="tall"
      title={entry ? t('timer.edit_entry') : t('timer.add_entry')}
      footer={
        <Button block disabled={!timesValid} loading={busy} onClick={() => void submit()} data-testid="time-entry-save">
          {entry ? t('app.save') : t('timer.add_entry_action')}
        </Button>
      }
    >
      <div className="space-y-4 pb-2" data-testid="time-entry-sheet">
        <Field label={t('timer.date')} htmlFor="te-date">
          <Input
            id="te-date"
            type="date"
            className="tnum"
            value={form.date}
            max={today}
            onChange={(event) => set('date', event.target.value)}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('timer.start_time')} htmlFor="te-start">
            <Input id="te-start" type="time" className="tnum" value={form.start} onChange={(event) => set('start', event.target.value)} />
          </Field>
          <Field label={t('timer.end_time')} htmlFor="te-end">
            <Input id="te-end" type="time" className="tnum" value={form.end} onChange={(event) => set('end', event.target.value)} />
          </Field>
        </div>
        {minutes !== null && (
          <p className="text-sm muted" aria-live="polite" data-testid="te-duration">
            {nextDay
              ? t('timer.duration_next_day', { duration: formatMinutes(minutes) })
              : t('timer.duration_is', { duration: formatMinutes(minutes) })}
          </p>
        )}
        <Field label={t('timer.description')} htmlFor="te-description">
          <Input
            id="te-description"
            value={form.description}
            maxLength={300}
            autoComplete="off"
            placeholder={t('timer.description_placeholder')}
            onChange={(event) => set('description', event.target.value)}
          />
        </Field>
        <TaskSelect id="te-task" value={form.task?.id ?? ''} current={entry?.task} onChange={(task) => set('task', task)} />
        <div className="grid grid-cols-2 gap-3">
          <ProjectSelect id="te-project" value={form.project?.id ?? ''} current={entry?.project} onChange={(ref) => set('project', ref)} />
          <AreaSelect id="te-area" value={form.area?.id ?? ''} current={entry?.area} onChange={(ref) => set('area', ref)} />
        </div>
        <Field label={t('timer.note')} htmlFor="te-note">
          <textarea
            id="te-note"
            className="input min-h-[5rem] resize-y"
            value={form.note}
            maxLength={2000}
            placeholder={t('timer.note_placeholder')}
            onChange={(event) => set('note', event.target.value)}
          />
        </Field>

        {entry &&
          (confirming ? (
            <Callout tone="danger" title={t('timer.delete_confirm')}>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Button variant="danger" loading={busy} onClick={() => void doDelete()} data-testid="time-entry-delete-confirm">
                  {t('app.delete')}
                </Button>
                <Button variant="secondary" onClick={() => setConfirming(false)}>
                  {t('app.cancel')}
                </Button>
              </div>
            </Callout>
          ) : (
            <Button
              block
              variant="ghost"
              className="text-rose-400"
              icon={<IconTrash size={18} />}
              onClick={() => setConfirming(true)}
              data-testid="time-entry-delete"
            >
              {t('timer.delete_entry')}
            </Button>
          ))}
      </div>
    </Sheet>
  )
}
