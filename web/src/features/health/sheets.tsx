/**
 * Manual entry sheets: steps, weight, sleep. Each saves one local day (PUT),
 * works offline through the queue, and never touches the food budget.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Callout, Field, Input, SegmentedControl, Sheet } from '@/components/ui'
import { IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { useAuth } from '@/features/auth/AuthProvider'
import { addDays, dayLabel, number, timeShort } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { decimal, kg, previewMinutes, signedKg, sleepDuration, wholeNumber } from './format'
import {
  useDeleteSleep,
  useDeleteSteps,
  useDeleteWeight,
  useHealthToday,
  useSaveSleep,
  useSaveSteps,
  useSaveWeight,
  useSleepSeries,
  useStepsSeries,
  useWeightSeries,
} from './useHealth'

type WhichDay = 'today' | 'yesterday'

/**
 * Which local day a sheet writes to. "Today" is the server's day in the user's
 * timezone (from /health/today), never the phone clock; until it has loaded the
 * sheet cannot save.
 */
function useDayChoice(open: boolean) {
  const { session } = useAuth()
  const tz = session?.settings.timezone
  const serverToday = useHealthToday().data?.day.date
  const [which, setWhich] = useState<WhichDay>('today')
  useEffect(() => {
    if (open) setWhich('today')
  }, [open])
  const day = serverToday ? (which === 'today' ? serverToday : addDays(serverToday, -1)) : ''
  return { which, setWhich, day, ready: !!serverToday, tz }
}

function DayToggle({ value, onChange }: { value: WhichDay; onChange: (next: WhichDay) => void }) {
  const { t } = useTranslation()
  return (
    <SegmentedControl
      value={value}
      onChange={onChange}
      options={[
        { value: 'today', label: t('health.today') },
        { value: 'yesterday', label: t('health.yesterday') },
      ]}
    />
  )
}

// --------------------------------------------------------------------------- //
// Steps
// --------------------------------------------------------------------------- //
export function StepsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const { which, setWhich, day, ready, tz } = useDayChoice(open)
  const series = useStepsSeries(7, open)
  const save = useSaveSteps()
  const remove = useDeleteSteps()
  const existing = series.data?.days.find((row) => row.date === day)
  const existingSteps = existing?.steps ?? 0
  const [value, setValue] = useState('')

  useEffect(() => {
    if (open) setValue(existingSteps ? String(existingSteps) : '')
  }, [open, day, existingSteps])

  const steps = wholeNumber(value)
  const valid = ready && steps !== null && steps >= 0 && steps <= 200_000

  const submit = () => {
    if (!valid || steps === null) return
    save.mutate(
      { day, body: { steps } },
      {
        onSuccess: (result) => {
          toast.success(result.queued ? t('health.queued') : t('health.steps_saved'))
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('health.steps_title')}>
      <div className="space-y-4 pb-2">
        <DayToggle value={which} onChange={setWhich} />
        <Field label={t('health.steps_label')} htmlFor="steps-input" hint={t('health.steps_hint')}>
          <Input
            id="steps-input"
            inputMode="numeric"
            enterKeyHint="done"
            autoComplete="off"
            className="tnum text-lg"
            placeholder={number(8000)}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && submit()}
          />
        </Field>
        {existing?.source && existing.source !== 'manual' && existing.updated_at && (
          <p className="text-xs muted" data-testid="steps-synced-note">
            {t('health.steps_from_shortcut', { time: timeShort(existing.updated_at, tz) })}
          </p>
        )}
        <Button block disabled={!valid} loading={save.isPending} onClick={submit}>
          {t('health.steps_save')}
        </Button>
        {existing?.source && (
          <Button
            block
            variant="ghost"
            className="text-rose-400"
            loading={remove.isPending}
            onClick={() =>
              remove.mutate(day, {
                onSuccess: (result) => {
                  toast.success(result.queued ? t('health.queued') : t('health.steps_deleted'))
                  onClose()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }
          >
            {t('health.steps_delete')}
          </Button>
        )}
      </div>
    </Sheet>
  )
}

// --------------------------------------------------------------------------- //
// Weight
// --------------------------------------------------------------------------- //
export function WeightSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const { which, setWhich, day, ready } = useDayChoice(open)
  const series = useWeightSeries(90, open)
  const save = useSaveWeight()
  const remove = useDeleteWeight()
  const items = series.data?.items ?? []
  const existing = items.find((row) => row.date === day)
  const existingKg = existing?.weight_kg
  const existingFat = existing?.body_fat_pct
  const latest = series.data?.latest
  const [weight, setWeight] = useState('')
  const [fat, setFat] = useState('')

  useEffect(() => {
    if (!open) return
    setWeight(existingKg != null ? number(existingKg, 1) : '')
    setFat(existingFat != null ? number(existingFat, 1) : '')
  }, [open, day, existingKg, existingFat])

  const kgValue = decimal(weight)
  const fatValue = decimal(fat)
  const valid = ready && kgValue !== null && kgValue > 20 && kgValue < 400 && (fat.trim() === '' || (fatValue !== null && fatValue > 2 && fatValue < 70))

  const submit = () => {
    if (!valid || kgValue === null) return
    save.mutate(
      { day, body: { weight_kg: kgValue, body_fat_pct: fatValue ?? undefined } },
      {
        onSuccess: (result) => {
          toast.success(result.queued ? t('health.queued') : t('health.weight_saved'))
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  const recent = [...items].reverse().slice(0, 5)

  return (
    <Sheet open={open} onClose={onClose} title={t('health.weight_title')}>
      <div className="space-y-4 pb-2">
        <DayToggle value={which} onChange={setWhich} />
        <div className="grid grid-cols-[3fr_2fr] gap-3">
          <Field label={t('health.weight_label')} htmlFor="weight-input">
            <Input
              id="weight-input"
              inputMode="decimal"
              enterKeyHint="done"
              autoComplete="off"
              className="tnum text-lg"
              placeholder={number(latest?.weight_kg ?? 75, 1)}
              value={weight}
              onChange={(event) => setWeight(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && submit()}
            />
          </Field>
          <Field label={t('health.body_fat_label')} htmlFor="fat-input">
            <Input
              id="fat-input"
              inputMode="decimal"
              autoComplete="off"
              className="tnum text-lg"
              placeholder="—"
              value={fat}
              onChange={(event) => setFat(event.target.value)}
            />
          </Field>
        </div>
        <p className="text-xs muted">{t('health.weight_hint')}</p>
        <Button block disabled={!valid} loading={save.isPending} onClick={submit}>
          {t('health.weight_save')}
        </Button>

        {recent.length > 0 && (
          <div className="space-y-1 border-t border-[rgb(var(--border))] pt-3">
            <p className="text-sm font-semibold">{t('health.weight_recent')}</p>
            {recent.map((row) => (
              <div key={row.date} className="flex min-h-tap items-center gap-3 text-sm">
                <span className="flex-1 muted">{dayLabel(row.date)}</span>
                <span className="tnum font-medium">
                  {kg(row.weight_kg)} {t('common.kg')}
                </span>
                <button
                  type="button"
                  className="-mr-2 flex h-12 w-12 items-center justify-center muted"
                  aria-label={t('health.weight_delete', { day: dayLabel(row.date, 'long') })}
                  onClick={() =>
                    remove.mutate(row.date, {
                      onSuccess: (result) =>
                        toast.success(result.queued ? t('health.queued') : t('health.weight_deleted')),
                      onError: (error) => toast.error(errorMessage(error)),
                    })
                  }
                >
                  <IconTrash size={16} />
                </button>
              </div>
            ))}
            {series.data?.change_30d_kg != null && (
              <p className="pt-1 text-xs muted">
                {t('health.weight_change_30d', { kg: signedKg(series.data.change_30d_kg) })}
              </p>
            )}
          </div>
        )}
      </div>
    </Sheet>
  )
}

// --------------------------------------------------------------------------- //
// Sleep
// --------------------------------------------------------------------------- //
const QUALITY = ['1', '2', '3', '4', '5'] as const

export function SleepSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const { which, setWhich, day, ready, tz } = useDayChoice(open)
  const series = useSleepSeries(14, open)
  const save = useSaveSleep()
  const remove = useDeleteSleep()
  const existing = series.data?.items.find((row) => row.date === day)
  const bedAt = existing?.bed_at
  const wakeAt = existing?.wake_at
  const existingQuality = existing?.quality
  const [bed, setBed] = useState('23:00')
  const [wake, setWake] = useState('07:00')
  const [quality, setQuality] = useState<string>('')

  useEffect(() => {
    if (!open) return
    if (bedAt && wakeAt) {
      setBed(timeShort(bedAt, tz))
      setWake(timeShort(wakeAt, tz))
      setQuality(existingQuality ? String(existingQuality) : '')
    } else {
      setBed('23:00')
      setWake('07:00')
      setQuality('')
    }
  }, [open, day, bedAt, wakeAt, existingQuality, tz])

  const minutes = useMemo(() => previewMinutes(bed, wake), [bed, wake])
  const valid = ready && minutes !== null && minutes <= 20 * 60

  const submit = () => {
    if (!valid) return
    save.mutate(
      { day, body: { bed_time: `${bed}:00`, wake_time: `${wake}:00`, quality: quality ? Number(quality) : undefined } },
      {
        onSuccess: (result) => {
          toast.success(result.queued ? t('health.queued') : t('health.sleep_saved'))
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('health.sleep_title')}>
      <div className="space-y-4 pb-2">
        <DayToggle value={which} onChange={setWhich} />
        {day && <p className="text-xs muted">{t('health.sleep_day_hint', { day: dayLabel(day, 'long') })}</p>}
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('health.bed_time')} htmlFor="bed-input">
            <Input id="bed-input" type="time" className="tnum text-lg" value={bed} onChange={(e) => setBed(e.target.value)} />
          </Field>
          <Field label={t('health.wake_time')} htmlFor="wake-input">
            <Input id="wake-input" type="time" className="tnum text-lg" value={wake} onChange={(e) => setWake(e.target.value)} />
          </Field>
        </div>
        <p className="tnum text-sm" data-testid="sleep-preview">
          {minutes !== null && valid
            ? t('health.sleep_preview', { duration: sleepDuration(minutes) })
            : t('health.sleep_invalid')}
        </p>
        <Field label={t('health.sleep_quality')}>
          <SegmentedControl
            value={quality}
            onChange={(next) => setQuality(next === quality ? '' : next)}
            options={QUALITY.map((value) => ({ value, label: value }))}
          />
        </Field>
        <Button block disabled={!valid} loading={save.isPending} onClick={submit}>
          {t('health.sleep_save')}
        </Button>
        {existing && (
          <Button
            block
            variant="ghost"
            className="text-rose-400"
            loading={remove.isPending}
            onClick={() =>
              remove.mutate(day, {
                onSuccess: (result) => {
                  toast.success(result.queued ? t('health.queued') : t('health.sleep_deleted'))
                  onClose()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }
          >
            {t('health.sleep_delete')}
          </Button>
        )}
        {which === 'today' && !existing && series.data && series.data.items.length === 0 && (
          <Callout tone="info">{t('health.sleep_first')}</Callout>
        )}
      </div>
    </Sheet>
  )
}
