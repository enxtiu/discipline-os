/**
 * Settings → Данные: your data as files (stage 8) — everything as JSON, one
 * CSV per table in a zip for Excel, and a restorable backup zip — with what
 * each holds, progress while the server streams it, the size once it is here,
 * and how to restore a backup on the server. Then the iOS Shortcut entry,
 * versions, and the "revoke this device" escape hatch.
 *
 * Downloads go through `fetch` with the device token (`lib/download.ts`): an
 * `<a download>` everywhere, the share sheet in the installed iPhone app.
 * The server allows 6 exports a minute; a 429 says so in words.
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'

import { Button, Callout, Card, ProgressBar, Row, Rows } from '@/components/ui'
import { IconChevronRight, IconCopy, IconDownload, IconFootprints, IconRefresh, IconShield } from '@/components/ui/icons'
import { ApiError, endpoints } from '@/api/client'
import { queryKeys } from '@/lib/queryClient'
import { APP_ENV, APP_VERSION } from '@/lib/build'
import { useAuth } from '@/features/auth/AuthProvider'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { fileSize } from '@/lib/format'
import {
  fetchFile,
  isIosStandalone,
  saveFile,
  shareFile,
  shareableFile,
  type DownloadedFile,
  type DownloadProgress,
} from '@/lib/download'

type ExportKind = 'json' | 'csv' | 'backup'

const EXPORTS: { kind: ExportKind; path: string; fallbackName: string }[] = [
  { kind: 'json', path: '/v1/export/json', fallbackName: 'disciplineos.json' },
  { kind: 'csv', path: '/v1/export/csv', fallbackName: 'disciplineos-csv.zip' },
  { kind: 'backup', path: '/v1/export/backup', fallbackName: 'disciplineos-backup.zip' },
]

const RESTORE_COPY = 'docker compose cp disciplineos-YYYY-MM-DD.zip api:/tmp/backup.zip'
const RESTORE_DRY_RUN = 'docker compose exec api python -m app.cli restore-backup /tmp/backup.zip --dry-run'
const RESTORE_RUN = 'docker compose exec api python -m app.cli restore-backup /tmp/backup.zip'

export function DataSection() {
  const { t } = useTranslation()
  const { session, logout } = useAuth()
  const toast = useToast()
  const configQuery = useQuery({ queryKey: queryKeys.appConfig, queryFn: () => endpoints.appConfig() })
  const [busy, setBusy] = useState<ExportKind | null>(null)

  return (
    <div className="space-y-4 pb-6">
      <h1 className="h1">{t('settings.sections.data')}</h1>

      <Card className="space-y-3" data-testid="data-export">
        <div>
          <p className="label">{t('settings.data_export_title')}</p>
          <p className="mt-1 text-xs muted">{t('settings.data_export_hint')}</p>
        </div>
        {EXPORTS.map((item) => (
          <ExportRow key={item.kind} {...item} busy={busy} onBusy={setBusy} />
        ))}
        <p className="flex items-start gap-2 text-xs muted">
          <IconShield size={14} className="mt-0.5 shrink-0" />
          <span>{t('settings.data_no_secrets')}</span>
        </p>
      </Card>

      <Card className="space-y-3" data-testid="data-restore">
        <p className="label">{t('settings.data_restore_title')}</p>
        <ol className="space-y-3 text-sm">
          <RestoreStep n={1} text={t('settings.data_restore_step_copy')} command={RESTORE_COPY} />
          <RestoreStep n={2} text={t('settings.data_restore_step_check')} command={RESTORE_DRY_RUN} />
          <RestoreStep n={3} text={t('settings.data_restore_step_run')} command={RESTORE_RUN} />
        </ol>
        <p className="text-xs muted">{t('settings.data_restore_note')}</p>
        <p className="text-xs muted">{t('settings.data_server_backups')}</p>
      </Card>

      <Rows>
        <Link to="/settings/shortcut" className="block">
          <Row
            icon={<IconFootprints size={18} />}
            label={t('settings.data_shortcut')}
            hint={t('settings.data_shortcut_hint')}
            trailing={<IconChevronRight size={18} className="text-[rgb(var(--text-muted))]" />}
          />
        </Link>
        <Row label={t('settings.about_version')} value={`${APP_VERSION} (${APP_ENV})`} />
        <Row label={t('settings.data_account')} value={session?.user.display_name ?? '—'} />
        <Row
          label={t('settings.data_api')}
          value={configQuery.data ? `${configQuery.data.api_version} · ${configQuery.data.env}` : '—'}
        />
        <Row
          label={t('settings.data_ai')}
          value={configQuery.data?.ai?.provider ? String(configQuery.data.ai.provider) : t('settings.data_ai_off')}
          hint={t('settings.data_ai_photo', { px: configQuery.data?.ai?.photo_downscale_px ?? 1280 })}
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

// --------------------------------------------------------------------------- //
// One export
// --------------------------------------------------------------------------- //
type ExportState =
  | { status: 'idle' }
  | { status: 'downloading'; progress: DownloadProgress }
  | { status: 'done'; file: DownloadedFile; share: File | null }
  | { status: 'error'; message: string }

function ExportRow({
  kind,
  path,
  fallbackName,
  busy,
  onBusy,
}: {
  kind: ExportKind
  path: string
  fallbackName: string
  busy: ExportKind | null
  onBusy: (kind: ExportKind | null) => void
}) {
  const { t } = useTranslation()
  const [state, setState] = useState<ExportState>({ status: 'idle' })
  const abort = useRef<AbortController | null>(null)
  const frame = useRef(0)

  useEffect(
    () => () => {
      abort.current?.abort()
      window.cancelAnimationFrame(frame.current)
    },
    [],
  )

  const start = async () => {
    const controller = new AbortController()
    abort.current = controller
    onBusy(kind)
    setState({ status: 'downloading', progress: { received: 0, total: null } })
    let latest: DownloadProgress = { received: 0, total: null }
    try {
      const file = await fetchFile(path, {
        signal: controller.signal,
        fallbackName,
        onProgress: (progress) => {
          latest = progress
          // one re-render per frame, however many chunks arrive
          window.cancelAnimationFrame(frame.current)
          frame.current = window.requestAnimationFrame(() => setState({ status: 'downloading', progress: latest }))
        },
      })
      window.cancelAnimationFrame(frame.current)
      let share: File | null = null
      if (isIosStandalone()) {
        share = shareableFile(file)
        if (share) {
          const outcome = await shareFile(share)
          if (outcome === 'failed') {
            saveFile(file)
            share = null
          }
        } else {
          saveFile(file)
        }
      } else {
        saveFile(file)
      }
      setState({ status: 'done', file, share })
    } catch (error) {
      window.cancelAnimationFrame(frame.current)
      if ((error as Error)?.name === 'AbortError') {
        setState({ status: 'idle' })
      } else if (error instanceof ApiError && error.status === 429) {
        setState({ status: 'error', message: t('settings.data_rate_limited') })
      } else {
        setState({ status: 'error', message: errorMessage(error) })
      }
    } finally {
      abort.current = null
      onBusy(null)
    }
  }

  const downloading = state.status === 'downloading'
  const progress = downloading ? state.progress : null

  return (
    <div
      className="surface-sunken space-y-2.5 p-3"
      data-testid={`export-${kind}`}
      data-state={state.status}
      data-size={state.status === 'done' ? state.file.size : undefined}
      data-filename={state.status === 'done' ? state.file.filename : undefined}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[rgb(var(--accent))]/10 text-[rgb(var(--accent))]">
          <IconDownload size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-semibold leading-tight">{t(`settings.export.${kind}.title`)}</p>
          <p className="mt-1 text-xs leading-relaxed muted">{t(`settings.export.${kind}.desc`)}</p>
        </div>
      </div>

      {progress && (
        <div className="space-y-1.5" aria-live="polite">
          {progress.total ? (
            <ProgressBar value={progress.received} max={progress.total} />
          ) : (
            <div className="skeleton h-2 w-full rounded-pill" aria-hidden="true" />
          )}
          <p className="tnum text-xs muted">
            {progress.received > 0
              ? t('settings.data_downloading', { size: fileSize(progress.received) })
              : t('settings.data_preparing')}
          </p>
        </div>
      )}
      {state.status === 'done' && (
        <p className="tnum break-all text-xs text-[rgb(var(--accent))]" aria-live="polite" data-testid="export-done">
          {t('settings.data_done', { size: fileSize(state.file.size), name: state.file.filename })}
        </p>
      )}
      {state.status === 'error' && (
        <p className="text-xs text-rose-400" role="alert" data-testid="export-error">
          {state.message}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {downloading ? (
          <Button variant="secondary" onClick={() => abort.current?.abort()} data-testid="export-cancel">
            {t('settings.data_cancel')}
          </Button>
        ) : (
          <Button
            variant="secondary"
            icon={<IconDownload size={18} />}
            onClick={() => void start()}
            disabled={busy != null}
            className={clsx(state.status === 'done' && state.share && 'hidden')}
            data-testid="export-download"
          >
            {state.status === 'done' ? t('settings.data_download_again') : t('settings.data_download')}
          </Button>
        )}
        {state.status === 'done' && state.share && (
          <Button
            variant="primary"
            onClick={() => {
              const file = state.share
              if (file) void shareFile(file)
            }}
            data-testid="export-share"
          >
            {t('settings.data_save_file')}
          </Button>
        )}
      </div>
      {state.status === 'done' && state.share && <p className="text-xs muted">{t('settings.data_share_hint')}</p>}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Restore instructions
// --------------------------------------------------------------------------- //
function RestoreStep({ n, text, command }: { n: number; text: string; command: string }) {
  const { t } = useTranslation()
  const toast = useToast()
  return (
    <li className="space-y-1.5">
      <p className="flex gap-2">
        <span className="tnum flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[rgb(var(--border))] text-[11px] font-semibold">
          {n}
        </span>
        <span>{text}</span>
      </p>
      <div className="surface-sunken flex items-center gap-1 rounded-xl pl-3">
        <code className="min-w-0 flex-1 break-all py-2 font-mono text-[11.5px] leading-relaxed" data-testid="restore-command">
          {command}
        </code>
        <button
          type="button"
          className="flex h-12 w-12 shrink-0 items-center justify-center text-[rgb(var(--accent))]"
          aria-label={t('settings.data_copy')}
          onClick={() => {
            void navigator.clipboard?.writeText(command).then(
              () => toast.success(t('app.copied')),
              () => toast.error(t('app.error_generic')),
            )
          }}
        >
          <IconCopy size={18} />
        </button>
      </div>
    </li>
  )
}
