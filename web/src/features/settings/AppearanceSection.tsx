/**
 * Language and region: language, timezone, week start, units — stored server-side
 * (so a second device picks them up).
 *
 * Stage 10: the theme moved to «Оформление» (/settings/theme, ThemeSection); the light /
 * dark / system mode is kept here as well (the same per-device setting, lib/appearance.ts),
 * with a way to the theme screen.
 */
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge, Callout, Card, Field, Rows, Row, SegmentedControl, Select, SkeletonCard, Toggle } from '@/components/ui'
import { IconChevronRight } from '@/components/ui/icons'
import { endpoints } from '@/api/client'
import type { SettingsUpdate } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { detectedTimezone } from '@/lib/device'
import { setAppearance, useAppearance, type Mode } from '@/lib/appearance'
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
  const appearance = useAppearance()

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

        <Field label={t('look.mode_title')}>
          <SegmentedControl<Mode>
            value={appearance.mode}
            onChange={(mode) => setAppearance({ mode })}
            options={[
              { value: 'dark', label: t('settings.theme_dark') },
              { value: 'light', label: t('settings.theme_light') },
              { value: 'system', label: t('look.mode.system') },
            ]}
          />
        </Field>
      </Card>

      <Link to="/settings/theme" className="block" data-testid="appearance-to-theme">
        <Rows>
          <Row
            label={t('settings.theme_link')}
            hint={t('settings.theme_link_hint', { theme: t(`look.skins.${appearance.skin}`) })}
            trailing={<IconChevronRight size={18} className="text-[rgb(var(--text-muted))]" />}
          />
        </Rows>
      </Link>

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
