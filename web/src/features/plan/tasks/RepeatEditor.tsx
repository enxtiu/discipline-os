/**
 * Repeat: a preset (sent as its key, the server expands it) or a small custom
 * builder — every N days/weeks/months/years, weekdays for weekly, a day of the
 * month for monthly — that produces an RRULE; plus an optional end date.
 */
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, Input, SegmentedControl, Select } from '@/components/ui'
import { dayLabel } from '@/lib/format'
import {
  buildRule,
  DEFAULT_PARTS,
  describeRepeat,
  type Freq,
  isPresetKey,
  MAX_INTERVAL,
  MAX_SPACED_MONTHDAY,
  normalizeParts,
  parseRule,
  type PresetKey,
  PRESETS,
  type RuleParts,
  type Weekday,
  WEEKDAYS,
  weekdayShort,
} from './rrule'

const PRESET_PARTS: Record<PresetKey, RuleParts> = {
  daily: { freq: 'DAILY', interval: 1, byday: [], bymonthday: null },
  weekdays: { freq: 'WEEKLY', interval: 1, byday: ['MO', 'TU', 'WE', 'TH', 'FR'], bymonthday: null },
  weekly: { freq: 'WEEKLY', interval: 1, byday: [], bymonthday: null },
  biweekly: { freq: 'WEEKLY', interval: 2, byday: [], bymonthday: null },
  monthly: { freq: 'MONTHLY', interval: 1, byday: [], bymonthday: null },
  yearly: { freq: 'YEARLY', interval: 1, byday: [], bymonthday: null },
}

type Mode = 'none' | PresetKey | 'custom'

export function RepeatEditor({
  rule,
  until,
  onChange,
}: {
  rule: string | null
  until: string | null
  onChange: (rule: string | null, until: string | null) => void
}) {
  const { t } = useTranslation()
  const selectId = useId()
  const untilId = useId()
  const intervalId = useId()
  const mode: Mode = !rule ? 'none' : isPresetKey(rule) ? rule : 'custom'
  const parts = mode === 'custom' ? parseRule(rule) : null
  const [intervalText, setIntervalText] = useState(parts ? String(parts.interval) : '1')
  useEffect(() => {
    if (parts) setIntervalText(String(parts.interval))
    // only when the rule itself changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rule])

  const setMode = (next: Mode) => {
    if (next === 'none') onChange(null, null)
    else if (next === 'custom') onChange(buildRule(mode !== 'none' && mode !== 'custom' ? PRESET_PARTS[mode] : DEFAULT_PARTS), until)
    else onChange(next, until)
  }
  const update = (patch: Partial<RuleParts>) =>
    onChange(buildRule(normalizeParts({ ...(parts ?? DEFAULT_PARTS), ...patch })), until)
  const toggleDay = (day: Weekday) => {
    const current = parts?.byday ?? []
    update({ byday: current.includes(day) ? current.filter((value) => value !== day) : [...current, day] })
  }

  return (
    <div className="space-y-3">
      <label htmlFor={selectId} className="sr-only">
        {t('task.repeat')}
      </label>
      <Select
        id={selectId}
        value={mode}
        onChange={(event) => setMode(event.target.value as Mode)}
        data-testid="repeat-select"
      >
        <option value="none">{t('task.repeat_none')}</option>
        {PRESETS.map((preset) => (
          <option key={preset} value={preset}>
            {t(`plan.recurrence.${preset}`)}
          </option>
        ))}
        <option value="custom">{t('task.repeat_custom')}</option>
      </Select>

      {mode === 'custom' &&
        (parts ? (
          <div className="surface-sunken space-y-3 p-3" data-testid="repeat-builder">
            <div className="flex items-center gap-2">
              <label htmlFor={intervalId} className="text-sm">
                {t('task.repeat_every')}
              </label>
              <Input
                id={intervalId}
                type="number"
                inputMode="numeric"
                min={1}
                max={MAX_INTERVAL}
                className="w-20 tnum"
                value={intervalText}
                onChange={(event) => {
                  setIntervalText(event.target.value)
                  const n = Math.round(Number(event.target.value))
                  if (Number.isFinite(n) && n >= 1 && n <= MAX_INTERVAL) update({ interval: n })
                }}
                data-testid="repeat-interval"
              />
            </div>
            <SegmentedControl<Freq>
              value={parts.freq}
              onChange={(freq) =>
                update({
                  freq,
                  byday: freq === 'WEEKLY' ? parts.byday : [],
                  bymonthday: freq === 'MONTHLY' ? parts.bymonthday : null,
                })
              }
              className="[&>button]:min-h-tap [&>button]:px-1"
              options={[
                { value: 'DAILY', label: t('task.unit_days') },
                { value: 'WEEKLY', label: t('task.unit_weeks') },
                { value: 'MONTHLY', label: t('task.unit_months') },
                { value: 'YEARLY', label: t('task.unit_years') },
              ]}
            />
            {parts.freq === 'WEEKLY' && (
              <div className="grid grid-cols-7 gap-1" role="group" aria-label={t('task.repeat_weekdays')}>
                {WEEKDAYS.map((day) => (
                  <button
                    key={day}
                    type="button"
                    aria-pressed={parts.byday.includes(day)}
                    onClick={() => toggleDay(day)}
                    className={clsx('chip min-h-tap justify-center px-0 text-xs', parts.byday.includes(day) && 'chip-active')}
                    data-testid={`repeat-day-${day}`}
                  >
                    {weekdayShort(day)}
                  </button>
                ))}
              </div>
            )}
            {parts.freq === 'MONTHLY' && (
              <MonthdayInput
                value={parts.bymonthday}
                max={parts.interval > 1 ? MAX_SPACED_MONTHDAY : 31}
                onChange={(bymonthday) => update({ bymonthday })}
              />
            )}
          </div>
        ) : (
          <div className="surface-sunken space-y-2 p-3 text-sm">
            <p className="break-all muted">{describeRepeat(rule)}</p>
            <Button size="sm" variant="secondary" className="min-h-tap" onClick={() => update(DEFAULT_PARTS)}>
              {t('task.repeat_rebuild')}
            </Button>
          </div>
        ))}

      {mode !== 'none' && (
        <div className="grid grid-cols-[auto_1fr] items-center gap-3">
          <label htmlFor={untilId} className="text-sm muted">
            {t('task.repeat_until')}
          </label>
          <Input
            id={untilId}
            type="date"
            className="tnum"
            value={until ?? ''}
            onChange={(event) => onChange(rule, event.target.value || null)}
            data-testid="repeat-until"
          />
        </div>
      )}
      {rule && (
        <p className="text-xs muted" data-testid="repeat-summary">
          {until
            ? t('task.repeat_summary_until', { repeat: describeRepeat(rule), date: dayLabel(until) })
            : describeRepeat(rule)}
        </p>
      )}
    </div>
  )
}

function MonthdayInput({
  value,
  max,
  onChange,
}: {
  value: number | null
  /** 31, or 28 when the repeat skips months (the server rule). */
  max: number
  onChange: (next: number | null) => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const [text, setText] = useState(value ? String(value) : '')
  const typed = Number(text)
  const outOfRange = text !== '' && (!Number.isFinite(typed) || typed < 1 || typed > max)
  // follow the rule when it changes from outside (e.g. clamped to 28)
  useEffect(() => {
    setText((current) => (Number(current) === value || (current === '' && value === null) ? current : value ? String(value) : ''))
  }, [value])
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <label htmlFor={id} className="text-sm">
          {t('task.repeat_monthday_label')}
        </label>
        <Input
          id={id}
          type="number"
          inputMode="numeric"
          min={1}
          max={max}
          className={clsx('w-20 tnum', outOfRange && 'border-rose-400')}
          value={text}
          placeholder="—"
          aria-invalid={outOfRange || undefined}
          onChange={(event) => {
            setText(event.target.value)
            const n = Math.round(Number(event.target.value))
            if (event.target.value === '') onChange(null)
            else if (Number.isFinite(n) && n >= 1 && n <= max) onChange(n)
          }}
          data-testid="repeat-monthday"
        />
      </div>
      {max < 31 && <p className="text-xs muted">{t('task.repeat_monthday_max', { max })}</p>}
    </div>
  )
}
