/**
 * Paired devices: rename, revoke, see last activity, and mint pairing codes for
 * additional devices. Mirrors the security model of `app/services/pairing.py`.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { QRCodeSVG } from 'qrcode.react'

import {
  Badge,
  Button,
  Callout,
  Card,
  Field,
  Input,
  Rows,
  Row,
  Sheet,
  SkeletonCard,
} from '@/components/ui'
import { IconKey, IconLaptop, IconPhone, IconShield, IconTrash } from '@/components/ui/icons'
import { endpoints } from '@/api/client'
import type { Device } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { relativeTime } from '@/lib/format'
import { useAuth } from '@/features/auth/AuthProvider'

export function DevicesSection() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const { session, logout } = useAuth()

  const devicesQuery = useQuery({ queryKey: queryKeys.devices, queryFn: () => endpoints.devices() })
  const [renaming, setRenaming] = useState<Device | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [code, setCode] = useState<{ code: string; expiresAt: string } | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.devices })

  const revokeMutation = useMutation({
    mutationFn: (id: string) => endpoints.revokeDevice(id),
    onSuccess: async (_, id) => {
      await invalidate()
      if (id === session?.device?.id) {
        toast.info(t('auth.revoked'))
        await logout()
      } else {
        toast.success(t('app.saved'))
      }
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => endpoints.updateDevice(id, { name }),
    onSuccess: async () => {
      await invalidate()
      setRenaming(null)
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const codeMutation = useMutation({
    mutationFn: () => endpoints.createPairingCode(),
    onSuccess: (result) => {
      setCode({ code: result.code, expiresAt: result.expires_at })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  if (devicesQuery.isLoading) return <SkeletonCard lines={4} />
  if (devicesQuery.isError || !devicesQuery.data) return null

  const { devices, active_count, max_devices } = devicesQuery.data

  return (
    <div className="space-y-4 pb-6">
      <h1 className="h1">{t('settings.devices_title')}</h1>

      <Card className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm">
          <IconShield size={18} className="text-aqua-400" />
          {t('settings.devices_count', { active: active_count, limit: max_devices })}
        </span>
        <Button size="sm" variant="secondary" onClick={() => codeMutation.mutate()} loading={codeMutation.isPending}>
          {t('settings.new_pairing_code')}
        </Button>
      </Card>

      {code && (
        <Card className="space-y-3">
          <p className="label">{t('auth.code_label')}</p>
          <p className="text-center font-stat text-3xl font-semibold tracking-[0.3em] text-aqua-300">
            {code.code}
          </p>
          <div className="flex justify-center">
            <div className="rounded-2xl bg-white p-3">
              <QRCodeSVG value={code.code} size={132} />
            </div>
          </div>
          <p className="text-center text-xs muted">{t('settings.pairing_code_once')}</p>
          <Button
            block
            variant="secondary"
            icon={<IconKey size={16} />}
            onClick={() => {
              void navigator.clipboard.writeText(code.code)
              toast.success(t('app.copied'))
            }}
          >
            {t('app.copy')}
          </Button>
        </Card>
      )}

      <Rows>
        {devices.map((device) => (
          <Row
            key={device.id}
            icon={device.platform === 'ios' || device.platform === 'android' ? <IconPhone size={18} /> : <IconLaptop size={18} />}
            label={
              <span className="flex items-center gap-2">
                {device.name}
                {device.is_current && <Badge tone="accent">{t('settings.device_current')}</Badge>}
                {!device.is_active && <Badge tone="danger">{t('errors.device_revoked')}</Badge>}
              </span>
            }
            hint={
              device.last_seen_at
                ? t('settings.device_last_seen', { when: relativeTime(device.last_seen_at) })
                : t('settings.device_never_seen')
            }
            trailing={
              device.is_active && (
                <span className="flex gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setRenaming(device)
                      setRenameValue(device.name)
                    }}
                  >
                    {t('settings.device_rename')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="w-tap px-0 text-rose-400"
                    aria-label={t('a11y.device_revoke', { name: device.name })}
                    title={t('settings.device_revoke')}
                    onClick={() => revokeMutation.mutate(device.id)}
                    loading={revokeMutation.isPending && revokeMutation.variables === device.id}
                  >
                    <IconTrash size={16} />
                  </Button>
                </span>
              )
            }
          />
        ))}
      </Rows>

      <Callout tone="info">
        {t('auth.last_device_warning')}
        <br />
        {t('auth.privacy_note')}
      </Callout>

      <Sheet open={!!renaming} onClose={() => setRenaming(null)} title={t('settings.device_rename')}>
        <div className="space-y-4 pb-2">
          <Field label={t('auth.device_name')}>
            <Input value={renameValue} onChange={(event) => setRenameValue(event.target.value)} maxLength={60} />
          </Field>
          <Button
            block
            loading={renameMutation.isPending}
            onClick={() => renaming && renameMutation.mutate({ id: renaming.id, name: renameValue })}
          >
            {t('app.save')}
          </Button>
        </div>
      </Sheet>
    </div>
  )
}
