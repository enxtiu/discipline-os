/**
 * Timer sheets, loaded on first use (the bar itself sits on Today):
 * * start — what you are timing: a description, optionally a task (today's or
 *   inbox), a project and an area (a task's own project/area are inherited);
 * * duration — for a timer left running over a day: how long it really ran.
 *   `ended_at = started_at + duration` is plain instant arithmetic, nothing is
 *   converted between timezones.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Callout, Field, Input, Sheet } from '@/components/ui'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { dayMonth } from '../calendar/dates'
import { formatMinutes } from '../format'
import { IconPlay, IconStop } from '../icons'
import type { Ref, TaskRef } from '../types'
import { AreaSelect, ProjectSelect, TaskSelect } from './pickers'
import { useStartTimer, useStopTimer } from './useTimerActions'

export function TimerStartSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const start = useStartTimer()
  const [description, setDescription] = useState('')
  const [task, setTask] = useState<TaskRef | null>(null)
  const [project, setProject] = useState<Ref | null>(null)
  const [area, setArea] = useState<Ref | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setDescription('')
    setTask(null)
    setProject(null)
    setArea(null)
  }, [open])

  const submit = async () => {
    if (busy) return
    setBusy(true)
    try {
      const result = await start({ task, project, area, description })
      if (result.queued) toast.info(t('timer.start_queued'))
      else toast.success(t('timer.started'))
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
      title={t('timer.start_title')}
      footer={
        <Button block loading={busy} icon={<IconPlay size={18} />} onClick={() => void submit()} data-testid="timer-start-submit">
          {t('timer.start_action')}
        </Button>
      }
    >
      <div className="space-y-4 pb-2" data-testid="timer-start-sheet">
        <Field label={t('timer.description')} htmlFor="timer-description">
          <Input
            id="timer-description"
            value={description}
            maxLength={300}
            autoComplete="off"
            enterKeyHint="go"
            placeholder={t('timer.description_placeholder')}
            onChange={(event) => setDescription(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit()
            }}
          />
        </Field>
        <TaskSelect id="timer-task" value={task?.id ?? ''} onChange={setTask} />
        <div className="grid grid-cols-2 gap-3">
          <ProjectSelect id="timer-project" value={project?.id ?? ''} onChange={setProject} />
          <AreaSelect id="timer-area" value={area?.id ?? ''} onChange={setArea} />
        </div>
        {task && !project && !area && <p className="text-xs muted">{t('timer.inherits')}</p>}
      </div>
    </Sheet>
  )
}

export interface LongTimer {
  entryId: string
  /** Instant the timer started (from the 409's `details.started_at` or the entry). */
  startedAt: string
  title: string
  /** Server's local day and time of the start ('' while a start is still queued). */
  date: string
  start: string
}

function wholeNumber(value: string): number | null {
  const trimmed = value.trim()
  if (trimmed === '') return 0
  return /^\d{1,2}$/.test(trimmed) ? Number(trimmed) : null
}

export function TimerDurationSheet({ open, onClose, timer }: { open: boolean; onClose: () => void; timer: LongTimer }) {
  const { t } = useTranslation()
  const toast = useToast()
  const stop = useStopTimer()
  const [hours, setHours] = useState('')
  const [minutes, setMinutes] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setHours('')
    setMinutes('')
  }, [open, timer.entryId])

  const h = wholeNumber(hours)
  const m = wholeNumber(minutes)
  const total = h !== null && m !== null ? h * 60 + m : 0
  const valid = h !== null && m !== null && m < 60 && total >= 1 && total <= 24 * 60 && !Number.isNaN(Date.parse(timer.startedAt))

  const submit = async () => {
    if (!valid || busy) return
    setBusy(true)
    try {
      const endedAt = new Date(Date.parse(timer.startedAt) + total * 60_000).toISOString()
      const result = await stop({ entryId: timer.entryId, endedAt })
      if (result.queued) toast.info(t('timer.stop_queued'))
      else toast.success(t('timer.stopped', { duration: formatMinutes(total) }))
      onClose()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('timer.duration_title')}>
      <div className="space-y-4 pb-2" data-testid="timer-duration-sheet">
        <Callout tone="warn">
          {timer.date && timer.start
            ? t('timer.duration_body', { title: timer.title, day: dayMonth(timer.date), time: timer.start })
            : t('timer.duration_body_short', { title: timer.title })}
        </Callout>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('timer.hours')} htmlFor="timer-hours">
            <Input
              id="timer-hours"
              inputMode="numeric"
              autoComplete="off"
              className="tnum text-lg"
              placeholder="0"
              value={hours}
              onChange={(event) => setHours(event.target.value)}
            />
          </Field>
          <Field label={t('timer.minutes')} htmlFor="timer-minutes">
            <Input
              id="timer-minutes"
              inputMode="numeric"
              autoComplete="off"
              className="tnum text-lg"
              placeholder="0"
              value={minutes}
              onChange={(event) => setMinutes(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void submit()
              }}
            />
          </Field>
        </div>
        <p className="text-sm muted" aria-live="polite">
          {valid ? t('timer.duration_preview', { duration: formatMinutes(total) }) : t('timer.duration_hint')}
        </p>
        <Button block disabled={!valid} loading={busy} icon={<IconStop size={18} />} onClick={() => void submit()}>
          {t('timer.stop_with_duration')}
        </Button>
      </div>
    </Sheet>
  )
}
