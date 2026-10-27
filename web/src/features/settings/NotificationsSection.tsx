/**
 * Settings → Уведомления (stage 7).
 *
 * One screen: this device's push status with the matching action, what to send
 * (per category, with local reminder times), quiet hours, the devices that get
 * notifications and the recent history. Categories and times are saved through
 * `PUT /notifications/prefs` (each toggle/time on its own, optimistically);
 * quiet hours stay in the settings row (`PATCH /settings`).
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { Badge, Button, Card, ErrorState, IconButton, Input, Section, SkeletonCard } from '@/components/ui'
import { IconClose, IconPlus, IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { endpoints } from '@/api/client'
import { queryKeys } from '@/lib/queryClient'
import { errorMessage } from '@/lib/errors'
import { relativeTime, toApiTime } from '@/lib/format'
import i18n from '@/i18n'
import { PushStatusCard, pushErrorMessage } from '@/features/notifications/PushStatusCard'
import { notificationsApi } from '@/features/notifications/api'
import {
  notificationKeys,
  useNotificationHistory,
  useNotificationPrefs,
  usePushSubscriptions,
  useSavePrefs,
} from '@/features/notifications/queries'
import {
  CATEGORIES,
  type Category,
  type NotificationItem,
  type NotificationPrefs,
  type NotificationPrefsIn,
} from '@/features/notifications/types'

/** Categories with reminder times of their own (task reminders take theirs from each task). */
const TIMES: Partial<Record<Category, { field: 'meal_times' | 'water_times'; max: number } | { field: 'workout_time' | 'evening_review_time' }>> = {
  meal_logging: { field: 'meal_times', max: 6 },
  water: { field: 'water_times', max: 8 },
  workout: { field: 'workout_time' },
  evening_review: { field: 'evening_review_time' },
}

const COMMIT_DELAY_MS = 800

export function NotificationsSection() {
  const { t } = useTranslation()
  const toast = useToast()
  const prefsQuery = useNotificationPrefs()
  const save = useSavePrefs((error) => toast.error(errorMessage(error)))

  if (prefsQuery.isLoading) return <SkeletonCard lines={6} />
  if (!prefsQuery.data) {
    return <ErrorState message={errorMessage(prefsQuery.error)} onRetry={() => prefsQuery.refetch()} retryLabel={t('app.retry')} />
  }
  const prefs = prefsQuery.data

  return (
    <div className="space-y-5 pb-6">
      <h1 className="h1">{t('settings.sections.notifications')}</h1>

      <PushStatusCard />

      <Section title={t('settings.notify.what_title')}>
        <p className="-mt-1 text-xs muted">{t('settings.notify.what_hint')}</p>
        <div className="card divide-y divide-[rgb(var(--border))] overflow-hidden">
          {CATEGORIES.map((category) => (
            <CategoryBlock key={category} category={category} prefs={prefs} onSave={(payload) => save.mutate(payload)} />
          ))}
        </div>
      </Section>

      <QuietHoursCard prefs={prefs} />
      <DevicesList />
      <HistoryList timezone={prefs.timezone} />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Categories
// --------------------------------------------------------------------------- //
/** The whole row is the switch: a 48 px target instead of the 28 px knob. */
function SwitchRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string
  hint?: string
  checked: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className="flex min-h-tap w-full items-center gap-3 py-3 text-left"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-medium">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-[rgb(var(--text-muted))]">{hint}</span>}
      </span>
      <span
        aria-hidden="true"
        className={clsx(
          'relative h-7 w-12 shrink-0 rounded-pill border transition-colors duration-200',
          checked
            ? 'border-transparent bg-[rgb(var(--accent))]'
            : 'border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))]',
        )}
      >
        <span
          className={clsx(
            'absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-[left] duration-200',
            checked ? 'left-[1.55rem]' : 'left-0.5',
          )}
        />
      </span>
    </button>
  )
}

function CategoryBlock({
  category,
  prefs,
  onSave,
}: {
  category: Category
  prefs: NotificationPrefs
  onSave: (payload: NotificationPrefsIn) => void
}) {
  const { t } = useTranslation()
  const label = t(`settings.notify_types.${category}`)
  const times = TIMES[category]
  const enabled = prefs[category]

  return (
    <div className="px-4" data-testid={`notify-${category}`}>
      <SwitchRow
        label={label}
        hint={t(`settings.notify.hints.${category}`)}
        checked={enabled}
        onChange={(value) => onSave({ [category]: value })}
      />
      {enabled && times && 'max' in times && (
        <TimeList
          label={label}
          values={prefs[times.field]}
          max={times.max}
          onSave={(values) => onSave({ [times.field]: values })}
        />
      )}
      {enabled && times && !('max' in times) && (
        <div className="pb-3">
          <TimeField
            label={t('settings.notify.time_label', { category: label, n: 1 })}
            value={prefs[times.field]}
            onCommit={(value) => onSave({ [times.field]: value })}
          />
        </div>
      )}
    </div>
  )
}

/** A time input that saves after a short pause (or on blur), never on every wheel tick. */
function TimeField({
  label,
  value,
  onCommit,
  trailing,
}: {
  label: string
  value: string
  onCommit: (value: string) => void
  trailing?: React.ReactNode
}) {
  const [draft, setDraft] = useState(value)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => setDraft(value), [value])
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const commit = (next: string) => {
    window.clearTimeout(timer.current)
    if (/^\d{2}:\d{2}/.test(next) && next.slice(0, 5) !== value) onCommit(next.slice(0, 5))
  }

  return (
    <div className="flex items-center gap-2">
      <Input
        type="time"
        aria-label={label}
        value={draft}
        className="min-h-tap tnum"
        onChange={(event) => {
          const next = event.target.value
          setDraft(next)
          window.clearTimeout(timer.current)
          timer.current = window.setTimeout(() => commit(next), COMMIT_DELAY_MS)
        }}
        onBlur={(event) => commit(event.target.value)}
      />
      {trailing}
    </div>
  )
}

function nextTime(values: string[]): string {
  const last = values.length ? values[values.length - 1] : '11:00'
  const hour = Math.min(Number(last.slice(0, 2)) + 1, 22)
  return `${String(hour).padStart(2, '0')}:${last.slice(3, 5)}`
}

function TimeList({
  label,
  values,
  max,
  onSave,
}: {
  label: string
  values: string[]
  max: number
  onSave: (values: string[]) => void
}) {
  const { t } = useTranslation()
  const normalised = (list: string[]) => Array.from(new Set(list)).sort()

  return (
    <div className="space-y-2 pb-3">
      {values.map((value, index) => (
        <TimeField
          key={`${value}-${index}`}
          label={t('settings.notify.time_label', { category: label, n: index + 1 })}
          value={value}
          onCommit={(next) => onSave(normalised(values.map((item, i) => (i === index ? next : item))))}
          trailing={
            <IconButton
              label={t('settings.notify.remove_time', { time: value })}
              icon={<IconClose size={18} />}
              onClick={() => onSave(values.filter((_, i) => i !== index))}
            />
          }
        />
      ))}
      {values.length < max && (
        <Button
          variant="ghost"
          size="sm"
          className="min-h-tap"
          icon={<IconPlus size={16} />}
          onClick={() => onSave(normalised([...values, nextTime(values)]))}
        >
          {t('settings.notify.add_time')}
        </Button>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Quiet hours (settings row)
// --------------------------------------------------------------------------- //
function QuietHoursCard({ prefs }: { prefs: NotificationPrefs }) {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => endpoints.updateSettings(payload),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.settings }),
        queryClient.invalidateQueries({ queryKey: notificationKeys.prefs }),
      ])
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const quiet = prefs.quiet_hours

  return (
    <Card className="space-y-3" data-testid="quiet-hours">
      <SwitchRow
        label={t('settings.quiet_hours')}
        hint={t('settings.quiet_hours_hint')}
        checked={quiet.enabled}
        onChange={(value) => mutation.mutate({ quiet_hours_enabled: value })}
      />
      <div className="grid grid-cols-2 gap-3">
        <label className="field">
          <span className="label">{t('settings.notify.quiet_from')}</span>
          <TimeField
            label={t('settings.notify.quiet_from')}
            value={quiet.start}
            onCommit={(value) => mutation.mutate({ quiet_hours_start: toApiTime(value) })}
          />
        </label>
        <label className="field">
          <span className="label">{t('settings.notify.quiet_to')}</span>
          <TimeField
            label={t('settings.notify.quiet_to')}
            value={quiet.end}
            onCommit={(value) => mutation.mutate({ quiet_hours_end: toApiTime(value) })}
          />
        </label>
      </div>
      <p className="text-xs muted">
        {t('settings.quiet_hours_range', { from: quiet.start, to: quiet.end })} · {prefs.timezone}
      </p>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Devices with push
// --------------------------------------------------------------------------- //
function DevicesList() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const subscriptions = usePushSubscriptions()
  const remove = useMutation({
    mutationFn: (id: string) => notificationsApi.deleteSubscription(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: notificationKeys.all })
      toast.success(t('settings.notify.device_removed'))
    },
    onError: (error) => toast.error(pushErrorMessage(error)),
  })
  const items = subscriptions.data ?? []

  return (
    <Section title={t('settings.notify.devices_title')}>
      {items.length === 0 ? (
        <p className="text-sm muted">{t('settings.notify.devices_empty')}</p>
      ) : (
        <ul className="card divide-y divide-[rgb(var(--border))] overflow-hidden" data-testid="push-devices">
          {items.map((item) => {
            const name = item.device_name ?? t('settings.notify.device_unknown')
            const live = item.is_active && item.device_active
            return (
              <li key={item.id} className="flex items-center gap-3 px-4 py-3">
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-[15px] font-medium">{name}</span>
                    {item.is_current_device && <Badge tone="accent">{t('settings.notify.device_this')}</Badge>}
                    {!live && (
                      <Badge tone="warn">
                        {item.device_active ? t('settings.notify.device_inactive') : t('settings.notify.device_revoked')}
                      </Badge>
                    )}
                  </span>
                  <span className="mt-0.5 block truncate text-xs muted">
                    {item.endpoint_host} ·{' '}
                    {item.last_success_at
                      ? t('settings.notify.device_last_ok', { when: relativeTime(item.last_success_at) })
                      : t('settings.notify.device_never')}
                    {item.failure_count > 0 && ` · ${t('settings.notify.device_failures', { count: item.failure_count })}`}
                  </span>
                </span>
                <IconButton
                  label={t('settings.notify.device_remove', { name })}
                  icon={<IconTrash size={18} />}
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(item.id)}
                />
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}

// --------------------------------------------------------------------------- //
// History
// --------------------------------------------------------------------------- //
const STATUS_TONES: Record<string, 'neutral' | 'accent' | 'violet' | 'warn' | 'danger'> = {
  sent: 'accent',
  deferred: 'violet',
  retrying: 'warn',
  failed: 'danger',
}

function when(item: NotificationItem, timezone: string): string {
  const value = item.sent_at ?? item.scheduled_for ?? item.updated_at
  try {
    return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: timezone,
    }).format(new Date(value))
  } catch {
    return value
  }
}

function reason(error: string | null): string | null {
  if (!error) return null
  const key = `settings.notify.reason.${error.split(':', 1)[0]}`
  return i18n.exists(key) ? i18n.t(key) : null
}

function HistoryList({ timezone }: { timezone: string }) {
  const { t } = useTranslation()
  const history = useNotificationHistory()
  const items = history.data ?? []

  return (
    <Section title={t('settings.notify.history_title')}>
      {items.length === 0 ? (
        <p className="text-sm muted">{t('settings.notify.history_empty')}</p>
      ) : (
        <ul className="card divide-y divide-[rgb(var(--border))] overflow-hidden" data-testid="push-history">
          {items.map((item) => {
            const why = reason(item.error)
            return (
              <li key={item.id} className="space-y-1 px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <span className="min-w-0 truncate text-[15px] font-medium">{item.title}</span>
                  <Badge tone={STATUS_TONES[item.status] ?? 'neutral'} className="shrink-0">
                    {i18n.exists(`settings.notify.status.${item.status}`)
                      ? t(`settings.notify.status.${item.status}`)
                      : item.status}
                  </Badge>
                </div>
                {item.body && <p className="line-clamp-2 text-sm muted">{item.body}</p>}
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs muted tnum">
                  <span>{when(item, timezone)}</span>
                  {item.quiet_hours_suppressed && <Badge tone="violet">{t('settings.notify.quiet_badge')}</Badge>}
                  {why && <span>{why}</span>}
                </p>
              </li>
            )
          })}
        </ul>
      )}
    </Section>
  )
}
