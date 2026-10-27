/**
 * Settings → Интеграции (stage 9): the optional one-way Notion mirror.
 *
 * Not connected: what the sync does (DisciplineOS stays the source of truth; Notion
 * is a copy, edits made there are overwritten), the steps to create a connection and
 * share a page with it, and the form (secret — a password field, never shown again —
 * and the page link). Connected: the page, per-kind switches with «Открыть в Notion»,
 * «Создать базы» / «Создать заново», the last sync in words, «Синхронизировать
 * сейчас» (polls until the run is done) and «Отключить» (Notion keeps its pages).
 */
import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { Badge, Button, Callout, Card, ErrorState, Field, Input, Section, Sheet, SkeletonCard } from '@/components/ui'
import { IconChevronRight, IconRefresh, IconShield, IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { ApiError } from '@/api/client'
import { errorMessage } from '@/lib/errors'
import { relativeTime } from '@/lib/format'
import i18n from '@/i18n'
import { integrationsApi } from './api'
import { integrationKeys, useNotion, useSetKinds } from './queries'
import { NOTION_KINDS, type NotionIntegration, type NotionKind, type NotionKindInfo } from './types'

/** Notion's Developer portal → Internal connections (developers.notion.com/guides/get-started/internal-connections). */
export const NOTION_CONNECTIONS_URL = 'https://app.notion.com/developers/connections'
/** Errors only a new secret fixes: the card offers the secret form right there. */
const SECRET_ERRORS = new Set(['notion_unauthorized', 'notion_secret_unreadable'])
const FIELD_ERRORS: Record<string, 'secret' | 'page'> = {
  notion_secret_invalid: 'secret',
  notion_unauthorized: 'secret',
  notion_page_invalid: 'page',
  notion_page_not_shared: 'page',
  notion_page_in_trash: 'page',
}

/** A `notion_*` code in plain words with what to do (`errors.notion_*`). */
export function notionErrorText(code: string | null | undefined): string {
  if (!code) return ''
  const key = `errors.${code}`
  return i18n.exists(key) ? i18n.t(key) : i18n.t('app.error_generic')
}

export function IntegrationsScreen() {
  const { t } = useTranslation()
  const query = useNotion()

  if (query.isLoading) return <SkeletonCard lines={6} />
  if (!query.data) {
    return <ErrorState message={errorMessage(query.error)} onRetry={() => query.refetch()} retryLabel={t('app.retry')} />
  }
  const data = query.data
  return (
    <div className="space-y-5 pb-6">
      <h1 className="h1">{t('integrations.title')}</h1>
      {data.connected ? <NotionConnected data={data} /> : <NotionIntro />}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Not connected
// --------------------------------------------------------------------------- //
function Steps({ items }: { items: string[] }) {
  return (
    <ol className="list-inside list-decimal space-y-2 text-sm leading-relaxed">
      {items.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ol>
  )
}

function NotionIntro() {
  const { t } = useTranslation()
  return (
    <Card className="space-y-4" data-testid="notion-card" data-state="disconnected">
      <div className="flex items-center justify-between gap-3">
        <span className="text-lg font-semibold">Notion</span>
        <Badge>{t('integrations.notion.badge_off')}</Badge>
      </div>
      <p className="text-sm leading-relaxed">{t('integrations.notion.what')}</p>
      <Callout tone="info" title={t('integrations.notion.one_way_title')}>
        {t('integrations.notion.one_way')}
      </Callout>
      <div className="space-y-2">
        <p className="label">{t('integrations.notion.steps_title')}</p>
        <Steps
          items={[
            t('integrations.notion.step_create'),
            t('integrations.notion.step_capabilities'),
            t('integrations.notion.step_page'),
            t('integrations.notion.step_share'),
            t('integrations.notion.step_paste'),
          ]}
        />
        <a
          href={NOTION_CONNECTIONS_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="btn-secondary w-full"
          data-testid="notion-portal-link"
        >
          {t('integrations.notion.open_portal')}
          <IconChevronRight size={16} />
        </a>
      </div>
      <ConnectForm mode="connect" />
    </Card>
  )
}

/** Connect (or give a new secret for the same page). The secret is a password field,
 *  cleared after a successful connect and never filled from the server. */
function ConnectForm({ mode, page: initialPage = '', onDone }: { mode: 'connect' | 'secret'; page?: string; onDone?: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const secretId = useId()
  const pageId = useId()
  const [secret, setSecret] = useState('')
  const [page, setPage] = useState(initialPage)
  const [touched, setTouched] = useState(false)
  const [serverError, setServerError] = useState<unknown>(null)

  const connect = useMutation({
    mutationFn: () => integrationsApi.connect({ secret: secret.trim(), page: page.trim() }),
    onSuccess: (data) => {
      setSecret('')
      setServerError(null)
      queryClient.setQueryData(integrationKeys.notion, data)
      toast.success(t(mode === 'connect' ? 'integrations.notion.connected_toast' : 'integrations.notion.secret_saved'))
      onDone?.()
    },
    onError: (error) => setServerError(error),
  })

  const code = serverError instanceof ApiError ? serverError.code : null
  const fieldOf = code ? FIELD_ERRORS[code] : undefined
  const serverText = serverError ? (code?.startsWith('notion_') ? notionErrorText(code) : errorMessage(serverError)) : ''
  const secretError =
    (touched && !secret.trim() ? t('integrations.notion.secret_required') : null) ||
    (fieldOf === 'secret' ? serverText : null)
  const pageError =
    (touched && !page.trim() ? t('integrations.notion.page_required') : null) || (fieldOf === 'page' ? serverText : null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setTouched(true)
    if (!secret.trim() || !page.trim()) return
    setServerError(null)
    connect.mutate()
  }

  return (
    <form className="space-y-3" onSubmit={submit} noValidate data-testid="notion-connect-form">
      <Field label={t('integrations.notion.secret_label')} hint={t('integrations.notion.secret_hint')} error={secretError} htmlFor={secretId}>
        <Input
          id={secretId}
          name="notion-secret"
          type="password"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          inputMode="text"
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
          aria-invalid={secretError ? true : undefined}
          placeholder="ntn_…"
        />
      </Field>
      <Field label={t('integrations.notion.page_label')} hint={t('integrations.notion.page_hint')} error={pageError} htmlFor={pageId}>
        <Input
          id={pageId}
          name="notion-page"
          type="url"
          inputMode="url"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          value={page}
          onChange={(event) => setPage(event.target.value)}
          aria-invalid={pageError ? true : undefined}
          placeholder="https://www.notion.so/…"
        />
      </Field>
      {serverError && !fieldOf ? (
        <div role="alert" data-testid="notion-connect-error">
          <Callout tone="danger" title={t('integrations.notion.connect_failed')}>
            {serverText}
          </Callout>
        </div>
      ) : null}
      <Button type="submit" block loading={connect.isPending} data-testid="notion-connect">
        {t(mode === 'connect' ? 'integrations.notion.connect' : 'integrations.notion.save_secret')}
      </Button>
      <p className="flex items-start gap-2 text-xs muted">
        <IconShield size={14} className="mt-0.5 shrink-0" /> {t('integrations.notion.secret_privacy')}
      </p>
    </form>
  )
}

// --------------------------------------------------------------------------- //
// Connected
// --------------------------------------------------------------------------- //
function NotionConnected({ data }: { data: NotionIntegration }) {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const [replacing, setReplacing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const watched = useRef<string | null>(null)
  const setKinds = useSetKinds((error) => toast.error(errorMessage(error)))

  const refresh = () => queryClient.invalidateQueries({ queryKey: integrationKeys.notion })

  const sync = useMutation({
    mutationFn: () => integrationsApi.sync(),
    onSuccess: async (started) => {
      watched.current = started.run_id
      await refresh()
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.code === 'notion_sync_running') {
        toast.info(notionErrorText(error.code))
        await refresh()
        return
      }
      toast.error(code(error))
    },
  })

  const setup = useMutation({
    mutationFn: (kinds: NotionKind[]) => integrationsApi.setup(kinds),
    onSuccess: (next) => {
      queryClient.setQueryData(integrationKeys.notion, next)
      toast.success(t('integrations.notion.databases_created'))
      sync.mutate() // fill the new databases right away
    },
    onError: async (error) => {
      toast.error(code(error))
      await refresh()
    },
  })

  const disconnect = useMutation({
    mutationFn: () => integrationsApi.disconnect(),
    onSuccess: async () => {
      setConfirming(false)
      toast.success(t('integrations.notion.disconnected_toast'))
      await refresh()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  // the run we started is over: say how it went
  useEffect(() => {
    const run = data.sync
    if (!watched.current || run.state !== 'idle' || run.run_id !== watched.current) return
    watched.current = null
    if (run.result === 'ok' || run.result === 'partial') toast.success(t('integrations.notion.sync_done'))
    else toast.error(notionErrorText(run.error) || t('integrations.notion.sync_failed'))
  }, [data.sync, t, toast])

  const running = data.sync.state === 'running' || sync.isPending
  const enabled = data.kinds.filter((item) => item.enabled)
  const toCreate = enabled.filter((item) => item.database !== 'ok')
  const onlyMissing = toCreate.length > 0 && toCreate.every((item) => item.database === 'missing')
  const kinds = NOTION_KINDS.map((kind) => data.kinds.find((item) => item.kind === kind)).filter(
    (item): item is NotionKindInfo => !!item,
  )
  const toggle = (kind: NotionKind, next: boolean) => {
    const set = new Set(enabled.map((item) => item.kind))
    if (next) set.add(kind)
    else set.delete(kind)
    setKinds.mutate(NOTION_KINDS.filter((item) => set.has(item)))
  }

  return (
    <>
      <Card className="space-y-3" data-testid="notion-card" data-state={data.status}>
        <div className="flex items-center justify-between gap-3">
          <span className="text-lg font-semibold">Notion</span>
          {data.status === 'error' ? (
            <Badge tone="danger">{t('integrations.notion.badge_error')}</Badge>
          ) : (
            <Badge tone="accent">{t('integrations.notion.badge_on')}</Badge>
          )}
        </div>
        {data.page ? (
          <a
            href={data.page.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex min-h-tap items-center gap-3 rounded-2xl border border-[rgb(var(--border))] px-4 py-2"
            data-testid="notion-page-link"
          >
            <span className="min-w-0 flex-1">
              <span className="block text-xs muted">{t('integrations.notion.page')}</span>
              <span className="block truncate font-medium">{data.page.title || t('integrations.notion.untitled')}</span>
            </span>
            <span className="shrink-0 text-sm text-[rgb(var(--accent))]">{t('integrations.notion.open')}</span>
          </a>
        ) : null}
        {data.bot_name || data.workspace_name ? (
          <p className="text-xs muted">
            {t('integrations.notion.connection', {
              name: data.bot_name ?? '—',
              workspace: data.workspace_name ?? '—',
            })}
          </p>
        ) : null}
        <p className="text-xs muted">{t('integrations.notion.one_way_short')}</p>
        {data.status === 'error' && data.error ? (
          <div role="alert">
            <Callout tone="danger" title={t('integrations.notion.attention')}>
              <p>{notionErrorText(data.error)}</p>
              {SECRET_ERRORS.has(data.error) && !replacing ? (
                <Button className="mt-3" variant="secondary" block onClick={() => setReplacing(true)}>
                  {t('integrations.notion.replace_secret')}
                </Button>
              ) : null}
            </Callout>
          </div>
        ) : null}
        {replacing ? (
          <ConnectForm mode="secret" page={data.page?.url ?? data.page?.id ?? ''} onDone={() => setReplacing(false)} />
        ) : null}
      </Card>

      <Section title={t('integrations.notion.kinds_title')}>
        <p className="-mt-1 text-xs muted">{t('integrations.notion.kinds_hint')}</p>
        <div className="card divide-y divide-[rgb(var(--border))] overflow-hidden" data-testid="notion-kinds">
          {kinds.map((item) => (
            <KindRow key={item.kind} item={item} onToggle={(next) => toggle(item.kind, next)} />
          ))}
        </div>
        {toCreate.length > 0 ? (
          <Button
            block
            variant={onlyMissing ? 'secondary' : 'primary'}
            loading={setup.isPending}
            disabled={running}
            onClick={() => setup.mutate(toCreate.map((item) => item.kind))}
            data-testid="notion-setup"
          >
            {t(onlyMissing ? 'integrations.notion.create_again' : 'integrations.notion.create_databases')}
          </Button>
        ) : null}
      </Section>

      <Section title={t('integrations.notion.sync_title')}>
        <Card className="space-y-3" data-testid="notion-sync" data-state={running ? 'running' : 'idle'}>
          <SyncSummary data={data} running={running} />
          <Button
            block
            variant="secondary"
            icon={<IconRefresh size={18} />}
            loading={running}
            disabled={running || enabled.every((item) => item.database !== 'ok')}
            onClick={() => sync.mutate()}
            data-testid="notion-sync-now"
          >
            {running ? t('integrations.notion.syncing') : t('integrations.notion.sync_now')}
          </Button>
          <p className="text-xs muted">{t('integrations.notion.auto', { minutes: data.interval_minutes })}</p>
        </Card>
      </Section>

      <Button
        block
        variant="danger"
        icon={<IconTrash size={18} />}
        onClick={() => setConfirming(true)}
        data-testid="notion-disconnect"
      >
        {t('integrations.notion.disconnect')}
      </Button>

      <Sheet open={confirming} onClose={() => setConfirming(false)} title={t('integrations.notion.disconnect_title')}>
        <div className="space-y-4 pb-2">
          <p className="text-sm leading-relaxed">{t('integrations.notion.disconnect_body')}</p>
          <Button
            block
            variant="danger"
            loading={disconnect.isPending}
            onClick={() => disconnect.mutate()}
            data-testid="notion-disconnect-confirm"
          >
            {t('integrations.notion.disconnect_confirm')}
          </Button>
          <Button block variant="secondary" onClick={() => setConfirming(false)}>
            {t('app.cancel')}
          </Button>
        </div>
      </Sheet>
    </>
  )
}

/** An API failure in words; `notion_*` codes say what to do. */
function code(error: unknown): string {
  if (error instanceof ApiError && error.code.startsWith('notion_')) return notionErrorText(error.code)
  return errorMessage(error)
}

/** The whole left part is the switch (48 px); «Открыть в Notion» is a link of its own. */
function KindRow({ item, onToggle }: { item: NotionKindInfo; onToggle: (next: boolean) => void }) {
  const { t } = useTranslation()
  const hintId = useId()
  const label = t(`integrations.notion.kinds.${item.kind}`)
  const hint =
    item.database === 'ok'
      ? item.last_run
        ? t('integrations.notion.db_ok_count', { count: item.last_run.total })
        : t('integrations.notion.db_ok')
      : item.database === 'missing'
        ? t('integrations.notion.db_missing')
        : t('integrations.notion.db_none')
  return (
    <div className="flex items-center gap-2 pr-2" data-testid={`notion-kind-${item.kind}`} data-database={item.database}>
      <button
        type="button"
        role="switch"
        aria-checked={item.enabled}
        aria-label={label}
        aria-describedby={hintId}
        onClick={() => onToggle(!item.enabled)}
        className="flex min-h-tap min-w-0 flex-1 items-center gap-3 py-3 pl-4 text-left"
      >
        <span
          aria-hidden="true"
          className={clsx(
            'relative h-7 w-12 shrink-0 rounded-pill border transition-colors duration-200',
            item.enabled
              ? 'border-transparent bg-[rgb(var(--accent))]'
              : 'border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))]',
          )}
        >
          <span
            className={clsx(
              'absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-[left] duration-200',
              item.enabled ? 'left-[1.55rem]' : 'left-0.5',
            )}
          />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-medium">{label}</span>
          <span
            id={hintId}
            className={clsx(
              'mt-0.5 block text-xs',
              item.database === 'missing' ? 'text-amber-300' : 'text-[rgb(var(--text-muted))]',
            )}
          >
            {hint}
          </span>
        </span>
      </button>
      {item.database === 'ok' && item.url ? (
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex min-h-tap shrink-0 items-center rounded-xl px-2 text-sm font-medium text-[rgb(var(--accent))]"
          aria-label={t('integrations.notion.open_kind', { kind: label })}
        >
          {t('integrations.notion.open')}
        </a>
      ) : null}
    </div>
  )
}

function SyncSummary({ data, running }: { data: NotionIntegration; running: boolean }) {
  const { t } = useTranslation()
  const run = data.sync
  if (running) {
    return <p className="text-sm">{t('integrations.notion.running')}</p>
  }
  if (!run.last_sync_at && !run.result) {
    return <p className="text-sm muted">{t('integrations.notion.never')}</p>
  }
  const totals = run.totals
  const changes = totals ? totals.created + totals.updated + totals.trashed : 0
  return (
    <div className="space-y-2 text-sm" data-testid="notion-last-run" data-result={run.result ?? ''}>
      {run.last_sync_at ? (
        <p>
          {t('integrations.notion.last_sync', { when: relativeTime(run.last_sync_at) })}
        </p>
      ) : null}
      {totals && (run.result === 'ok' || run.result === 'partial') ? (
        <p className="muted tnum">
          {changes > 0
            ? t('integrations.notion.counts', {
                created: totals.created,
                updated: totals.updated,
                trashed: totals.trashed,
              })
            : t('integrations.notion.no_changes', { total: totals.total })}
        </p>
      ) : null}
      {run.result === 'partial' && !run.error ? <p className="muted">{t('integrations.notion.partial')}</p> : null}
      {totals && totals.pending > 0 ? <p className="muted">{t('integrations.notion.pending', { count: totals.pending })}</p> : null}
      {totals && totals.failed > 0 ? (
        <p className="text-amber-300">{t('integrations.notion.failed_rows', { count: totals.failed })}</p>
      ) : null}
      {run.result === 'interrupted' ? <p className="text-amber-300">{t('integrations.notion.interrupted')}</p> : null}
      {run.error && data.status !== 'error' ? (
        <div role="status">
          <Callout tone={run.result === 'error' ? 'danger' : 'warn'}>{notionErrorText(run.error)}</Callout>
        </div>
      ) : null}
    </div>
  )
}
