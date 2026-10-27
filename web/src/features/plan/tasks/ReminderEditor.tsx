/**
 * Reminders: relative ones (at the due time, 15/30/60 min, a day before) or a
 * fixed date + time. Stage 6 stores and shows them; push delivery arrives in
 * stage 7 and the editor says so.
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Input } from '@/components/ui'
import { IconClose } from '@/components/ui/icons'
import { IconBellSmall } from '../icons'
import type { Reminder, ReminderIn } from '../types'
import {
  isRelativeReminder,
  LEAD_PRESETS,
  leadChip,
  MAX_REMINDERS,
  reminderInLabel,
  reminderOutLabel,
  sameReminder,
} from './reminders'
import { Chip } from './ui'

export function ReminderEditor({
  reminders,
  history,
  hasAnchor,
  today,
  onChange,
}: {
  /** Pending reminders (the editable set). */
  reminders: ReminderIn[]
  /** Already sent / failed / cancelled reminders of the task (read-only). */
  history: Reminder[]
  /** The task has a date or a block — relative reminders need one. */
  hasAnchor: boolean
  today: string | undefined
  onChange: (next: ReminderIn[]) => void
}) {
  const { t } = useTranslation()
  const dateId = useId()
  const timeId = useId()
  const [absoluteOpen, setAbsoluteOpen] = useState(false)
  const [date, setDate] = useState('')
  const [time, setTime] = useState('09:00')
  const full = reminders.length >= MAX_REMINDERS
  const add = (reminder: ReminderIn) => {
    if (full || reminders.some((item) => sameReminder(item, reminder))) return
    onChange([...reminders, reminder])
  }
  const orphaned = !hasAnchor && reminders.some(isRelativeReminder)

  return (
    <div className="space-y-2">
      {reminders.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {reminders.map((reminder, index) => {
            const label = reminderInLabel(reminder, today)
            return (
              <li key={`${label}-${index}`}>
                <button
                  type="button"
                  className="chip chip-active min-h-tap"
                  aria-label={t('task.reminder_remove', { label })}
                  onClick={() => onChange(reminders.filter((_, position) => position !== index))}
                  data-testid="reminder-chip"
                >
                  <IconBellSmall size={14} />
                  <span className="tnum">{label}</span>
                  <IconClose size={14} />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        {LEAD_PRESETS.map((lead) => (
          <Chip
            key={lead}
            disabled={!hasAnchor || full || reminders.some((item) => sameReminder(item, { lead_minutes: lead }))}
            onClick={() => add({ lead_minutes: lead })}
            data-testid={`reminder-${lead}`}
          >
            {leadChip(lead)}
          </Chip>
        ))}
        <Chip
          active={absoluteOpen}
          disabled={full}
          onClick={() => {
            setAbsoluteOpen((open) => !open)
            if (!date && today) setDate(today)
          }}
          data-testid="reminder-absolute"
        >
          {t('task.reminder_at_date')}
        </Chip>
      </div>
      {absoluteOpen && (
        <div className="grid grid-cols-[minmax(0,3fr)_minmax(0,2fr)_auto] items-end gap-2">
          <div className="min-w-0">
            <label htmlFor={dateId} className="sr-only">
              {t('task.reminder_date')}
            </label>
            <Input id={dateId} type="date" className="tnum" value={date} onChange={(event) => setDate(event.target.value)} />
          </div>
          <div className="min-w-0">
            <label htmlFor={timeId} className="sr-only">
              {t('task.reminder_time')}
            </label>
            <Input id={timeId} type="time" className="tnum" value={time} onChange={(event) => setTime(event.target.value)} />
          </div>
          <Button
            variant="secondary"
            disabled={!date || !time || full}
            onClick={() => {
              add({ date, time })
              setAbsoluteOpen(false)
            }}
            data-testid="reminder-absolute-add"
          >
            {t('app.add')}
          </Button>
        </div>
      )}
      {history.length > 0 && (
        <p className="text-xs muted">
          {t('task.reminder_history', {
            list: history
              .map((reminder) => `${reminderOutLabel(reminder, today)} (${t(`task.reminder_status.${reminder.status}`)})`)
              .join(', '),
          })}
        </p>
      )}
      {orphaned ? (
        <p className="text-xs text-rose-400">{t('errors.invalid_reminder')}</p>
      ) : (
        !hasAnchor && <p className="text-xs muted">{t('task.reminder_needs_date')}</p>
      )}
      {full && <p className="text-xs muted">{t('errors.too_many_reminders')}</p>}
      <p className="text-xs muted">{t('task.reminder_stage7')}</p>
    </div>
  )
}
