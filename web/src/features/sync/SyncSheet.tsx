/**
 * The sync sheet (tap the header pill): online or not, what waits to be sent
 * (grouped by kind, with when it was made), what the server refused — with its
 * reason in plain words, «Повторить» / «Удалить» — and what changed on another
 * device meanwhile («Открыть» → keep theirs / keep mine). Reads the queue from
 * IndexedDB only, so it works offline.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { MotionConfig } from 'framer-motion'
import clsx from 'clsx'

import { Button, Sheet } from '@/components/ui'
import { IconAlert, IconChevronLeft, IconCloudCheck, IconRefresh, IconWifiOff } from '@/components/ui/icons'
import {
  dependsOn,
  discardEntry,
  resolveConflict,
  retryEntry,
  sendAllNow,
  statusOf,
  type QueuedRequest,
} from '@/lib/offlineQueue'
import { ConflictView } from './ConflictView'
import { actionLabel, countdown, failureReason, kindLabel, madeAt, queueAction, queueKind, type QueueKind } from './labels'
import { usePrefersReducedMotion, useQueueEntries, useQueueState, useTicker } from './useQueue'

interface Row {
  entry: QueuedRequest & { id: number }
  /** Pending, but waiting behind a failed or conflicting write it depends on. */
  blocked: boolean
}

/** The same walk as the replay: what is stuck behind an unsent write. */
function classify(entries: QueuedRequest[]): { conflicts: Row[]; failed: Row[]; pending: Map<QueueKind, Row[]> } {
  const blockers: QueuedRequest[] = []
  const conflicts: Row[] = []
  const failed: Row[] = []
  const pending = new Map<QueueKind, Row[]>()
  for (const entry of entries) {
    if (entry.id === undefined) continue
    const status = statusOf(entry)
    const row: Row = { entry: entry as Row['entry'], blocked: false }
    if (status === 'conflict') conflicts.push(row)
    else if (status === 'failed') failed.push(row)
    else {
      row.blocked = blockers.some((blocker) => dependsOn(entry, blocker))
      const kind = queueKind(entry)
      pending.set(kind, [...(pending.get(kind) ?? []), row])
    }
    if (status !== 'pending' || row.blocked) blockers.push(entry)
  }
  return { conflicts, failed, pending }
}

function body(entry: QueuedRequest): Record<string, unknown> {
  return entry.body && typeof entry.body === 'object' && !Array.isArray(entry.body)
    ? (entry.body as Record<string, unknown>)
    : {}
}

function headline(entry: QueuedRequest): string {
  return `${kindLabel(queueKind(entry))} · ${actionLabel(queueAction(entry))}`
}

export function SyncSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const state = useQueueState()
  const entries = useQueueEntries()
  const reduced = usePrefersReducedMotion()
  const now = useTicker(open, 1000)
  const [openId, setOpenId] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | 'all' | null>(null)
  const { conflicts, failed, pending } = useMemo(() => classify(entries), [entries])
  const opened = openId !== null ? conflicts.find((row) => row.entry.id === openId)?.entry : undefined

  const run = async (key: number | 'all', action: () => Promise<void>) => {
    setBusy(key)
    try {
      await action()
    } finally {
      setBusy(null)
    }
  }

  const status = !state.online
    ? t('sync.status_offline')
    : state.authBlocked
      ? t('sync.status_auth')
      : state.sending
        ? t('sync.status_sending')
        : state.nextRetryAt && state.pending > 0
          ? t('sync.status_retry', { time: countdown(state.nextRetryAt - now) })
          : t('sync.status_online')

  const nothing = entries.length === 0
  const canSend = state.online && !state.authBlocked && state.pending + state.failed > 0

  const footer = opened ? null : canSend ? (
    <Button
      block
      icon={<IconRefresh size={18} />}
      loading={busy === 'all' || state.sending}
      onClick={() => void run('all', sendAllNow)}
      data-testid="sync-send-all"
    >
      {t('sync.send_all')}
    </Button>
  ) : (
    <Button block variant="secondary" onClick={onClose} data-testid="sync-close">
      {t('app.close')}
    </Button>
  )

  return (
    <MotionConfig reducedMotion={reduced ? 'always' : 'never'}>
      <Sheet
        open={open}
        onClose={() => {
          setOpenId(null)
          onClose()
        }}
        title={t('sync.title')}
        size="tall"
        footer={footer}
      >
        <div className="space-y-5 pb-2" data-testid="sync-sheet">
          <p
            className={clsx(
              'flex items-start gap-2 rounded-2xl px-3 py-2.5 text-sm',
              state.online ? 'bg-[rgb(var(--bg-sunken))]' : 'bg-amber-400/10 text-amber-200',
            )}
            role="status"
            data-testid="sync-status-line"
            data-online={state.online ? 'true' : 'false'}
          >
            <span className="mt-0.5 shrink-0">{state.online ? <IconCloudCheck size={16} /> : <IconWifiOff size={16} />}</span>
            <span>{status}</span>
          </p>

          {opened ? (
            <section className="space-y-3" data-testid="sync-conflict" data-entry-id={opened.id}>
              <button
                type="button"
                className="-ml-2 flex min-h-tap items-center gap-1 rounded-xl px-2 text-sm muted"
                onClick={() => setOpenId(null)}
                data-testid="sync-conflict-back"
              >
                <IconChevronLeft size={18} />
                {t('sync.back')}
              </button>
              <ConflictView
                fields={opened.conflict?.fields ?? []}
                mine={body(opened)}
                current={opened.conflict?.current ?? null}
                subtitle={[headline(opened), opened.meta?.title].filter(Boolean).join(' — ')}
                busy={busy === opened.id}
                onKeepTheirs={() =>
                  void run(opened.id as number, async () => {
                    await resolveConflict(opened.id as number, 'theirs')
                    setOpenId(null)
                  })
                }
                onKeepMine={() =>
                  void run(opened.id as number, async () => {
                    await resolveConflict(opened.id as number, 'mine')
                    setOpenId(null)
                  })
                }
              />
            </section>
          ) : nothing ? (
            <div className="flex flex-col items-center gap-2 py-8 text-center" data-testid="sync-empty">
              <span className="text-[rgb(var(--accent))]">
                <IconCloudCheck size={28} />
              </span>
              <p className="font-medium">{t('sync.all_sent')}</p>
              <p className="max-w-xs text-sm muted">{t('sync.all_sent_hint')}</p>
            </div>
          ) : (
            <>
              {conflicts.length > 0 && (
                <section className="space-y-2" data-testid="sync-conflicts">
                  <h3 className="label">{t('sync.section_conflicts')}</h3>
                  {conflicts.map(({ entry }) => (
                    <div
                      key={entry.id}
                      className="flex items-center gap-3 rounded-2xl border border-amber-400/35 bg-amber-400/10 py-2 pl-3 pr-2"
                      data-testid="sync-entry"
                      data-status="conflict"
                      data-kind={queueKind(entry)}
                    >
                      <span className="shrink-0 text-amber-300">
                        <IconAlert size={18} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{headline(entry)}</p>
                        {entry.meta?.title && <p className="truncate text-xs">{entry.meta.title}</p>}
                        <p className="text-xs muted">
                          {t('sync.conflict_title')} · {madeAt(entry.createdAt, now)}
                        </p>
                      </div>
                      <Button
                        size="sm"
                        className="min-h-tap shrink-0"
                        onClick={() => setOpenId(entry.id)}
                        data-testid="sync-open"
                      >
                        {t('sync.open')}
                      </Button>
                    </div>
                  ))}
                </section>
              )}

              {failed.length > 0 && (
                <section className="space-y-2" data-testid="sync-failed">
                  <h3 className="label">{t('sync.section_failed')}</h3>
                  {failed.map(({ entry }) => (
                    <div
                      key={entry.id}
                      className="space-y-2 rounded-2xl border border-rose-500/30 bg-rose-500/5 px-3 py-2.5"
                      data-testid="sync-entry"
                      data-status="failed"
                      data-kind={queueKind(entry)}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{headline(entry)}</p>
                        {entry.meta?.title && <p className="truncate text-xs">{entry.meta.title}</p>}
                        <p className="text-xs text-rose-300" data-testid="sync-reason">
                          {failureReason(entry)}
                        </p>
                        <p className="text-xs muted">{madeAt(entry.createdAt, now)}</p>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          className="min-h-tap"
                          loading={busy === entry.id}
                          disabled={!state.online}
                          onClick={() => void run(entry.id, () => retryEntry(entry.id))}
                          data-testid="sync-retry"
                        >
                          {t('app.retry')}
                        </Button>
                        <Button
                          variant="danger"
                          size="sm"
                          className="min-h-tap"
                          disabled={busy === entry.id}
                          onClick={() => void run(entry.id, () => discardEntry(entry.id))}
                          data-testid="sync-discard"
                        >
                          {t('app.delete')}
                        </Button>
                      </div>
                    </div>
                  ))}
                </section>
              )}

              {pending.size > 0 && (
                <section className="space-y-2" data-testid="sync-pending">
                  <h3 className="label">{t('sync.section_pending')}</h3>
                  {[...pending.entries()].map(([kind, rows]) => (
                    <div key={kind} className="rounded-2xl bg-[rgb(var(--bg-sunken))] px-3 py-2" data-testid="sync-group" data-kind={kind}>
                      <p className="flex items-baseline justify-between gap-2 text-sm font-medium">
                        <span>{kindLabel(kind)}</span>
                        <span className="tnum text-xs muted">{rows.length}</span>
                      </p>
                      <ul className="mt-1 space-y-1">
                        {rows.map(({ entry, blocked }) => (
                          <li
                            key={entry.id}
                            className="flex min-h-[2rem] flex-wrap items-baseline gap-x-2 text-xs"
                            data-testid="sync-entry"
                            data-status="pending"
                            data-kind={kind}
                            data-blocked={blocked ? 'true' : 'false'}
                          >
                            <span className="muted">{actionLabel(queueAction(entry))}</span>
                            {entry.meta?.title && <span className="min-w-0 truncate">{entry.meta.title}</span>}
                            <span className="ml-auto tnum muted">{madeAt(entry.createdAt, now)}</span>
                            {blocked && <span className="basis-full text-amber-300">{t('sync.waiting_behind')}</span>}
                            {!blocked && entry.attempts > 0 && (
                              <span className="basis-full muted">{t('sync.attempts', { n: entry.attempts })}</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
        </div>
      </Sheet>
    </MotionConfig>
  )
}
