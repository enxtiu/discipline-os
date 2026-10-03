/**
 * Data: backup status, export (stage 8), iOS Shortcut recipe (stage 5),
 * and the "revoke this device" escape hatch.
 */
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { Badge, Button, Callout, Card, CheckList, Rows, Row } from '@/components/ui'
import { IconRefresh, IconShield } from '@/components/ui/icons'
import { endpoints } from '@/api/client'
import { queryKeys } from '@/lib/queryClient'
import { APP_ENV, APP_VERSION } from '@/lib/build'
import { useAuth } from '@/features/auth/AuthProvider'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'

export function DataSection() {
  const { t } = useTranslation()
  const { session, logout } = useAuth()
  const toast = useToast()
  const configQuery = useQuery({ queryKey: queryKeys.appConfig, queryFn: () => endpoints.appConfig() })

  return (
    <div className="space-y-4 pb-6">
      <h1 className="h1">{t('settings.sections.data')}</h1>

      <Card className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="label">{t('settings.export_title')}</span>
          <Badge tone="violet">stage 8</Badge>
        </div>
        <p className="text-xs muted">{t('settings.export_hint')}</p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled>
            CSV
          </Button>
          <Button size="sm" variant="secondary" disabled>
            JSON
          </Button>
          <Button size="sm" variant="secondary" disabled>
            {t('settings.sections.data')} backup
          </Button>
        </div>
      </Card>

      {configQuery.data && (
        <Card className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="label">iOS Shortcut · /api/v1/ingest/health</span>
            <Badge tone="violet">stage 5</Badge>
          </div>
          <CheckList
            items={[
              'Shortcuts → Automation → Time of Day (13:00, 22:00)',
              'Action “Find Health Samples” → Steps, Active Energy → Today',
              'Action “Get Contents of URL”: POST https://<your-domain>/api/v1/ingest/health',
              'Headers: Authorization: Bearer <scoped token from API tokens>',
              'Body (JSON): {"date": "YYYY-MM-DD", "steps": …, "active_kcal": …, "distance_m": …}',
            ]}
          />
          <p className="text-xs muted">
            Idempotent: running it twice only overwrites today&apos;s row. A scoped
            <span className="font-mono"> ingest:health </span>
            token cannot read anything else.
          </p>
        </Card>
      )}

      <Rows>
        <Row label={t('settings.about_version')} value={`${APP_VERSION} (${APP_ENV})`} />
        <Row label={t('settings.about_env')} value={session?.user.display_name ?? '—'} />
        <Row
          label="API"
          value={configQuery.data ? `${configQuery.data.api_version} · ${configQuery.data.env}` : '—'}
        />
        <Row
          label="AI"
          value={configQuery.data?.ai?.provider ? String(configQuery.data.ai.provider) : 'off'}
          hint={String(configQuery.data?.ai?.photo_downscale_px ?? 1280) + ' px photo downscale'}
        />
      </Rows>

      <Callout tone="danger" title={t('auth.logout')}>
        <p className="mb-3 text-xs">{t('auth.logout_confirm')}</p>
        <Button
          size="sm"
          variant="danger"
          icon={<IconRefresh size={16} />}
          onClick={() => {
            void logout().catch((error) => toast.error(errorMessage(error)))
          }}
        >
          {t('auth.logout')}
        </Button>
      </Callout>

      <p className="flex items-center gap-2 text-xs muted">
        <IconShield size={14} /> {t('auth.privacy_note')}
      </p>
    </div>
  )
}
