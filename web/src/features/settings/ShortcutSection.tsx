/**
 * Settings → iOS Shortcut: everything needed to send steps and active energy
 * from Apple Health — a token that can only ingest, the exact request, the
 * steps to build the Shortcut, and when it last arrived.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { Button, Callout, Card } from '@/components/ui'
import { IconCopy, IconKey, IconRefresh } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { endpoints } from '@/api/client'
import { queryKeys } from '@/lib/queryClient'
import { errorMessage } from '@/lib/errors'
import { relativeTime } from '@/lib/format'
import { apiPath } from '@/lib/url'
import { useHealthToday } from '@/features/health/useHealth'

const BODY_KEYS = [
  { key: 'steps', labelKey: 'shortcut.field_steps' },
  { key: 'active_kcal', labelKey: 'shortcut.field_kcal' },
  { key: 'distance_km', labelKey: 'shortcut.field_distance' },
] as const

const STEPS = ['shortcut.step_1', 'shortcut.step_2', 'shortcut.step_3', 'shortcut.step_4', 'shortcut.step_5', 'shortcut.step_6'] as const

function absolute(path: string): string {
  return new URL(apiPath(path), window.location.origin).toString()
}

function CopyLine({ label, value, display, testId }: { label: string; value: string; display?: string; testId?: string }) {
  const { t } = useTranslation()
  const toast = useToast()
  return (
    <div className="space-y-1">
      <p className="text-xs muted">{label}</p>
      <div className="surface-sunken flex items-center gap-2 rounded-xl pl-3">
        <code className="min-w-0 flex-1 break-words py-2.5 font-mono text-xs" data-testid={testId}>
          {(display ?? value).split('/').map((part, index) => (
            <span key={index}>
              {index > 0 && '/'}
              {index > 0 && <wbr />}
              {part}
            </span>
          ))}
        </code>
        <button
          type="button"
          className="flex h-12 w-12 shrink-0 items-center justify-center text-[rgb(var(--accent))]"
          aria-label={`${t('app.copy')}: ${label}`}
          onClick={() => {
            void navigator.clipboard?.writeText(value).then(
              () => toast.success(t('app.copied')),
              () => toast.error(t('shortcut.copy_failed')),
            )
          }}
        >
          <IconCopy size={18} />
        </button>
      </div>
    </div>
  )
}

export function ShortcutSection() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const health = useHealthToday()
  const [withBody, setWithBody] = useState(false)
  const [token, setToken] = useState<string | null>(null)

  const create = useMutation({
    mutationFn: () =>
      endpoints.createToken({
        name: t('shortcut.token_name'),
        scopes: withBody ? ['ingest:health', 'ingest:weight', 'ingest:sleep'] : ['ingest:health'],
      }),
    onSuccess: async (result) => {
      setToken(result.token)
      await queryClient.invalidateQueries({ queryKey: queryKeys.tokens })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const lastSync = health.data?.last_sync_at
  const url = absolute('/v1/ingest/health')
  const header = `Bearer ${token ?? t('shortcut.token_placeholder')}`

  return (
    <div className="space-y-4 pb-6">
      <h1 className="h1">{t('shortcut.title')}</h1>
      <p className="text-sm muted">{t('shortcut.intro')}</p>

      <Card className="flex items-center gap-3" data-testid="shortcut-sync">
        <IconRefresh size={20} className={lastSync ? 'text-[rgb(var(--accent))]' : 'muted'} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {lastSync ? t('health.last_sync', { time: relativeTime(lastSync) }) : t('health.never_synced')}
          </p>
          <p className="text-xs muted">{t('shortcut.sync_hint')}</p>
        </div>
      </Card>

      <Card className="space-y-3">
        <p className="text-sm font-semibold">{t('shortcut.token_title')}</p>
        <p className="text-xs muted">{t('shortcut.token_hint')}</p>
        <label className="flex min-h-tap items-center gap-3 text-sm">
          <input
            type="checkbox"
            className="h-5 w-5 accent-aqua-400"
            checked={withBody}
            onChange={(event) => setWithBody(event.target.checked)}
          />
          {t('shortcut.with_body')}
        </label>
        {token ? (
          <Callout tone="warn" title={t('settings.token_created')}>
            <span className="block break-all font-mono text-xs" data-testid="shortcut-token">
              {token}
            </span>
          </Callout>
        ) : (
          <Button block icon={<IconKey size={18} />} loading={create.isPending} onClick={() => create.mutate()}>
            {t('shortcut.create_token')}
          </Button>
        )}
      </Card>

      <Card className="space-y-3">
        <p className="text-sm font-semibold">{t('shortcut.request_title')}</p>
        <CopyLine label={t('shortcut.url')} value={url} testId="shortcut-url" />
        <CopyLine label={t('shortcut.header')} value={header} display={`Authorization: ${header}`} />
        <div className="space-y-1">
          <p className="text-xs muted">{t('shortcut.body')}</p>
          <div className="surface-sunken divide-y divide-[rgb(var(--border))] rounded-xl">
            {BODY_KEYS.map((field) => (
              <div key={field.key} className="flex items-center justify-between gap-3 px-3 py-2.5 text-xs">
                <code className="font-mono">{field.key}</code>
                <span className="text-right muted">{t(field.labelKey)}</span>
              </div>
            ))}
          </div>
          <p className="text-xs muted">{t('shortcut.body_hint')}</p>
        </div>
      </Card>

      <Card className="space-y-3">
        <p className="text-sm font-semibold">{t('shortcut.howto_title')}</p>
        <ol className="space-y-2.5">
          {STEPS.map((key, index) => (
            <li key={key} className="flex gap-3 text-sm">
              <span className="tnum flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[rgb(var(--accent))]/15 text-xs font-semibold text-[rgb(var(--accent))]">
                {index + 1}
              </span>
              <span className="min-w-0 pt-0.5">{t(key)}</span>
            </li>
          ))}
        </ol>
      </Card>

      {withBody && (
        <Card className="space-y-3">
          <p className="text-sm font-semibold">{t('shortcut.extra_title')}</p>
          <CopyLine label={t('shortcut.extra_weight')} value={absolute('/v1/ingest/weight')} />
          <CopyLine label={t('shortcut.extra_sleep')} value={absolute('/v1/ingest/sleep')} />
          <p className="text-xs muted">{t('shortcut.extra_hint')}</p>
        </Card>
      )}

      <p className="text-xs muted">{t('shortcut.manual')}</p>
    </div>
  )
}
