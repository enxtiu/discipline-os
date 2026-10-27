/**
 * Sync indicator in the header: online/offline, writes waiting to be sent and
 * an attention dot for writes the server refused or that changed on another
 * device. Tapping it opens the sync sheet (stage 9) — what waits, what failed
 * and why, conflicts to settle, «Отправить всё сейчас». The whole 48 px square
 * is the target; the visible pill stays compact.
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { AnimatePresence, MotionConfig, motion } from 'framer-motion'
import clsx from 'clsx'

import { IconCloudCheck, IconRefresh, IconWifiOff } from '@/components/ui/icons'
import { SyncSheet } from '@/features/sync/SyncSheet'
import { usePrefersReducedMotion, useQueueState } from '@/features/sync/useQueue'

export function SyncStatus() {
  const { t } = useTranslation()
  const state = useQueueState()
  const reduced = usePrefersReducedMotion()
  const [open, setOpen] = useState(false)

  const offline = !state.online
  const pending = state.pending
  const attention = state.failed + state.conflict
  const hasPending = pending > 0

  const parts = [
    offline ? t('app.offline') : hasPending ? t('sync.pill_pending', { count: pending }) : t('app.sync_done'),
  ]
  if (offline && hasPending) parts.push(t('sync.pill_pending', { count: pending }))
  if (attention > 0) parts.push(t('sync.pill_attention', { count: attention }))
  const label = parts.join(' · ')

  return (
    <MotionConfig reducedMotion={reduced ? 'always' : 'never'}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="-my-1.5 flex min-h-tap items-center"
        aria-label={label}
        aria-haspopup="dialog"
        title={label}
        data-testid="sync-pill"
        data-online={offline ? 'false' : 'true'}
        data-pending={pending}
        data-attention={attention}
      >
        <span
          className={clsx(
            'relative flex min-h-[2.25rem] items-center gap-1.5 rounded-pill border px-3 text-xs font-medium transition-colors',
            offline
              ? 'border-amber-400/40 bg-amber-400/10 text-amber-300'
              : hasPending
                ? 'border-violet-400/40 bg-violet-400/10 text-violet-300'
                : attention > 0
                  ? 'border-rose-500/40 bg-rose-500/10 text-rose-300'
                  : 'border-[rgb(var(--border))] muted',
          )}
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={offline ? 'off' : hasPending ? 'pending' : 'ok'}
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={{ duration: 0.15 }}
              className={clsx('flex items-center gap-1.5', state.sending && 'animate-pulse-soft')}
            >
              {offline ? <IconWifiOff size={15} /> : hasPending ? <IconRefresh size={15} /> : <IconCloudCheck size={15} />}
              {hasPending && <span className="tnum" data-testid="sync-pill-count">{pending}</span>}
            </motion.span>
          </AnimatePresence>
          {attention > 0 && (
            <span
              className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-rose-400 ring-2 ring-[rgb(var(--bg))]"
              aria-hidden="true"
              data-testid="sync-pill-dot"
            />
          )}
        </span>
      </button>
      {/* the header blurs its backdrop, which would trap a fixed sheet inside it: render at the root */}
      {createPortal(<SyncSheet open={open} onClose={() => setOpen(false)} />, document.body)}
    </MotionConfig>
  )
}
