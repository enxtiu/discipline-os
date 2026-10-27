/**
 * This device's push status with the one action that fits it (stage 7):
 *
 *   needs_install → the «На экран Домой» steps (iPhone Safari tab: no push there)
 *   denied        → how to allow it again (iOS Settings / the browser's site settings)
 *   off           → «Включить уведомления»
 *   on            → «Отправить тестовое» + «Выключить на этом устройстве»
 *   server_off    → an honest note for the admin (no VAPID keys on the server)
 *
 * Used by Settings → Уведомления and, in a shorter form, by the onboarding wizard.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { Badge, Button, Card, Skeleton } from '@/components/ui'
import { IconBell } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { ApiError } from '@/api/client'
import { errorMessage } from '@/lib/errors'
import { isIos } from '@/lib/device'
import i18n from '@/i18n'
import { PushError, disablePush, enablePush, type PushStatus } from '@/lib/push'
import { notificationsApi } from './api'
import { notificationKeys, usePushConfig, usePushStatus, usePushSubscriptions } from './queries'

type CardState = PushStatus | 'server_off' | 'checking'

const TONES: Record<CardState, 'neutral' | 'accent' | 'warn' | 'danger'> = {
  on: 'accent',
  off: 'neutral',
  denied: 'danger',
  unsupported: 'neutral',
  needs_install: 'warn',
  server_off: 'warn',
  checking: 'neutral',
}

/** Browser-side and push-specific API failures in words (`settings.push_errors.*`). */
export function pushErrorMessage(error: unknown): string {
  const code = error instanceof PushError || error instanceof ApiError ? error.code : null
  const key = `settings.push_errors.${code}`
  if (code && i18n.exists(key)) return i18n.t(key)
  return errorMessage(error)
}

function Steps({ items }: { items: string[] }) {
  return (
    <ol className="list-inside list-decimal space-y-1.5 text-sm leading-relaxed">
      {items.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ol>
  )
}

export function PushStatusCard({ variant = 'settings' }: { variant?: 'settings' | 'onboarding' }) {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const config = usePushConfig()
  const subscriptions = usePushSubscriptions()
  const { status, refresh } = usePushStatus()
  const [busy, setBusy] = useState<'enable' | 'disable' | 'test' | null>(null)

  const state: CardState = config.data && !config.data.enabled ? 'server_off' : config.isLoading ? 'checking' : status
  const onboarding = variant === 'onboarding'

  const settle = async () => {
    setBusy(null)
    await queryClient.invalidateQueries({ queryKey: notificationKeys.all })
    refresh()
  }

  // straight from the tap: iOS shows the permission prompt only for a user gesture
  const enable = async () => {
    const key = config.data?.vapid_public_key
    if (!key) return
    setBusy('enable')
    try {
      await enablePush(key)
      toast.success(t('settings.push.enabled'))
    } catch (error) {
      toast.error(pushErrorMessage(error))
    } finally {
      await settle()
    }
  }

  const disable = async () => {
    setBusy('disable')
    try {
      const mine = (subscriptions.data ?? []).filter((item) => item.is_current_device).map((item) => item.id)
      await disablePush(mine)
      toast.success(t('settings.push.disabled'))
    } catch (error) {
      toast.error(pushErrorMessage(error))
    } finally {
      await settle()
    }
  }

  const sendTest = async () => {
    setBusy('test')
    try {
      const result = await notificationsApi.test()
      if (result.sent > 0) toast.success(t('settings.push.test_sent', { count: result.sent }))
      else if (result.failed > 0) toast.error(t('settings.push.test_failed', { count: result.failed }))
      else toast.info(t('settings.push.test_none'))
    } catch (error) {
      toast.error(pushErrorMessage(error))
    } finally {
      await settle()
    }
  }

  return (
    <Card className="space-y-3" data-testid="push-status" data-state={state}>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="flex items-center gap-2 font-semibold">
          <IconBell size={18} className="shrink-0 text-[rgb(var(--text-muted))]" />
          {onboarding ? t('onboarding.push_title') : t('settings.push.title')}
        </span>
        <Badge tone={TONES[state]}>{t(`settings.push.status.${state}`)}</Badge>
      </div>

      {onboarding && <p className="text-sm muted">{t('onboarding.push_intro')}</p>}

      {state === 'checking' && <Skeleton className="h-tap" />}

      {state === 'needs_install' && (
        <div className="space-y-2">
          <p className="text-sm">{onboarding ? t('onboarding.push_ios_first') : t('settings.push.install_intro')}</p>
          <Steps items={(t('settings.install_steps', { returnObjects: true }) as string[]) ?? []} />
        </div>
      )}

      {state === 'denied' && (
        <div className="space-y-2">
          <p className="text-sm">{t('settings.push.denied_intro')}</p>
          {isIos() ? (
            <Steps items={(t('settings.push.denied_ios', { returnObjects: true }) as string[]) ?? []} />
          ) : (
            <p className="text-sm">{t('settings.push.denied_desktop')}</p>
          )}
        </div>
      )}

      {state === 'unsupported' && <p className="text-sm muted">{t('settings.push.unsupported_hint')}</p>}
      {state === 'server_off' && <p className="text-sm muted">{t('settings.push.server_off_hint')}</p>}

      {state === 'off' && (
        <>
          {!onboarding && <p className="text-sm muted">{t('settings.push.off_hint')}</p>}
          <Button block onClick={enable} loading={busy === 'enable'} disabled={busy !== null}>
            {t('settings.push.enable')}
          </Button>
        </>
      )}

      {state === 'on' &&
        (onboarding ? (
          <p className="text-sm text-aqua-300">{t('onboarding.push_on')}</p>
        ) : (
          <>
            <p className="text-sm muted">{t('settings.push.on_hint')}</p>
            <div className="space-y-2">
              <Button block variant="secondary" onClick={sendTest} loading={busy === 'test'} disabled={busy !== null}>
                {t('settings.push.test')}
              </Button>
              <Button block variant="ghost" onClick={disable} loading={busy === 'disable'} disabled={busy !== null}>
                {t('settings.push.disable')}
              </Button>
            </div>
          </>
        ))}

      {onboarding && state !== 'on' && <p className="text-xs muted">{t('onboarding.push_later')}</p>}
    </Card>
  )
}
