/**
 * Notifications: per-type toggles + quiet hours.
 *
 * Stage 1 stores the preferences server-side (they are what the stage-7
 * scheduler reads). The push permission button explains the iOS requirement:
 * the site must be added to the Home Screen first, so we detect standalone
 * mode and say so instead of failing silently.
 */
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge, Button, Callout, Card, CheckList, Field, Input, Rows, Row, SkeletonCard, Toggle } from '@/components/ui'
import { endpoints } from '@/api/client'
import { queryKeys } from '@/lib/queryClient'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { toApiTime, fromApiTime } from '@/lib/format'
import { isIos, isStandalone, pushAvailability } from '@/lib/device'

const TYPES = ['task_reminders', 'meal_logging', 'water', 'workout', 'evening_review', 'streak_at_risk'] as const

export function NotificationsSection() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()

  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: () => endpoints.settings() })

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => endpoints.updateSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings })
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  if (settingsQuery.isLoading) return <SkeletonCard lines={5} />
  if (!settingsQuery.data) return null

  const settings = settingsQuery.data.settings
  const prefs = (settings.notification_prefs ?? {}) as Record<string, boolean>
  const availability = pushAvailability()

  const setPref = (key: string, value: boolean) =>
    mutation.mutate({ notification_prefs: { ...prefs, [key]: value } })

  return (
    <div className="space-y-4 pb-6">
      <h1 className="h1">{t('settings.sections.notifications')}</h1>

      {isIos() && !isStandalone() && (
        <Callout tone="warn" title={t('settings.install_app')}>
          <p className="mb-3 text-xs">{t('settings.standalone_required')}</p>
          <CheckList
            className="text-xs"
            items={(t('settings.install_steps', { returnObjects: true }) as string[]) ?? []}
          />
        </Callout>
      )}

      {isStandalone() && (
        <Callout tone="success">
          {t('settings.standalone_detected')}{' '}
          <Badge tone={availability === 'ready' ? 'accent' : 'warn'}>{availability}</Badge>
        </Callout>
      )}

      <Rows>
        {TYPES.map((key) => (
          <Row
            key={key}
            label={t(`settings.notify_types.${key}`)}
            trailing={<Toggle label={key} checked={prefs[key] ?? true} onChange={(value) => setPref(key, value)} />}
          />
        ))}
      </Rows>

      <Card className="space-y-4">
        <Row
          label={t('settings.quiet_hours')}
          hint={t('settings.quiet_hours_hint')}
          trailing={
            <Toggle
              label={t('settings.quiet_hours')}
              checked={settings.quiet_hours_enabled}
              onChange={(value) => mutation.mutate({ quiet_hours_enabled: value })}
            />
          }
        />
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('settings.quiet_hours')}>
            <Input
              type="time"
              value={fromApiTime(settings.quiet_hours_start as string)}
              onChange={(event) =>
                mutation.mutate({ quiet_hours_start: toApiTime(event.target.value) })
              }
            />
          </Field>
          <Field label="—">
            <Input
              type="time"
              value={fromApiTime(settings.quiet_hours_end as string)}
              onChange={(event) => mutation.mutate({ quiet_hours_end: toApiTime(event.target.value) })}
            />
          </Field>
        </div>
        <p className="text-xs muted">
          {t('settings.quiet_hours_range', {
            from: fromApiTime(settings.quiet_hours_start as string),
            to: fromApiTime(settings.quiet_hours_end as string),
          })}{' '}
          · {settings.timezone}
        </p>
      </Card>

      <Callout tone="info">
        <p className="mb-2 text-xs">Web Push (VAPID) — stage 7</p>
        <Button
          size="sm"
          variant="secondary"
          disabled={availability !== 'ready'}
          onClick={() => mutation.mutate({ notification_prefs: { ...prefs, push_requested: true } })}
        >
          {availability === 'ready' ? 'Enable push' : t('settings.standalone_required')}
        </Button>
      </Callout>
    </div>
  )
}
