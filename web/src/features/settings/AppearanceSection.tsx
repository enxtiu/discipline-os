/**
 * Appearance: language, theme, timezone, week start, units.
 * All four are stored server-side (so a second device picks them up) and the
 * theme is also mirrored to localStorage for a flash-free first paint.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge, Callout, Card, Field, Rows, Row, SegmentedControl, Select, SkeletonCard, Toggle } from '@/components/ui'
import { endpoints } from '@/api/client'
import type { SettingsUpdate } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { detectedTimezone, storedTheme, applyTheme, type Theme } from '@/lib/device'
import { time, fromApiTime, toApiTime } from '@/lib/format'
import i18n, { type Language } from '@/i18n'
import { useAuth } from '@/features/auth/AuthProvider'

const COMMON_TIMEZONES = [
  'Asia/Bangkok',
  'Asia/Phuket',
  'Europe/Moscow',
  'Europe/Kyiv',
  'Europe/Berlin',
  'Europe/London',
  'America/New_York',
  'America/Los_Angeles',
  'Asia/Dubai',
  'Asia/Almaty',
  'Asia/Tbilisi',
  'Asia/Tokyo',
  'UTC',
]

export function AppearanceSection() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const { refreshSession } = useAuth()
  const [theme, setTheme] = useState<Theme>(() => storedTheme())

  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: () => endpoints.settings() })

  const mutation = useMutation({
    mutationFn: (payload: SettingsUpdate) => endpoints.updateSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings })
      await refreshSession()
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  if (settingsQuery.isLoading) return <SkeletonCard lines={5} />
  if (!settingsQuery.data) return null

  const settings = settingsQuery.data.settings

  const changeLanguage = async (language: Language) => {
    await i18n.changeLanguage(language)
    mutation.mutate({ language })
  }

  return (
    <div className="space-y-4 pb-6">
      <h1 className="h1">{t('settings.sections.appearance')}</h1>

      <Card className="space-y-4">
        <Field label={t('settings.language')}>
          <SegmentedControl
            value={(settings.language as Language) ?? 'ru'}
            onChange={(value) => void changeLanguage(value)}
            options={[
              { value: 'ru', label: t('settings.language_ru') },
              { value: 'en', label: t('settings.language_en') },
            ]}
          />
        </Field>

        <Field label={t('settings.theme')}>
          <SegmentedControl
            value={theme}
            onChange={(value) => setTheme(value)}
            options={[
              { value: 'dark', label: t('settings.theme_dark') },
              { value: 'light', label: t('settings.theme_light') },
            ]}
          />
        </Field>
      </Card>

      <Card className="space-y-4">
        <Field
          label={t('settings.timezone')}
          hint={t('settings.timezone_detected', {
            time: time(new Date(), settings.timezone),
            tz: settings.timezone,
          })}
        >
          <Select
            value={settings.timezone}
            onChange={(event) => mutation.mutate({ timezone: event.target.value, timezone_auto: false })}
          >
            {Array.from(new Set([detectedTimezone(), settings.timezone, ...COMMON_TIMEZONES])).map((zone) => (
              <option key={zone} value={zone}>
                {zone}
              </option>
            ))}
          </Select>
        </Field>

        <Rows>
          <Row
            label={t('settings.timezone_auto')}
            hint={detectedTimezone()}
            trailing={
              <Toggle
                label={t('settings.timezone_auto')}
                checked={settings.timezone_auto}
                onChange={(value) =>
                  mutation.mutate({ timezone_auto: value, timezone: detectedTimezone() })
                }
              />
            }
          />
          <Row
            label={t('settings.week_start')}
            trailing={
              <SegmentedControl
                value={String(settings.week_start)}
                onChange={(value) => mutation.mutate({ week_start: Number(value) })}
                options={[
                  { value: '1', label: t('settings.week_start_monday') },
                  { value: '0', label: t('settings.week_start_sunday') },
                ]}
              />
            }
          />
          <Row label={t('settings.units')} value={t('settings.units_metric')} />
        </Rows>
      </Card>

      <Callout tone="info">
        <Badge tone="accent">{settings.timezone}</Badge>{' '}
        {fromApiTime(settings.quiet_hours_start as string)} – {fromApiTime(settings.quiet_hours_end as string)}{' '}
        {toApiTime('')}
      </Callout>
    </div>
  )
}
