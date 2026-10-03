/**
 * Sync indicator: online/offline, queued writes, and a manual "retry now".
 * Sits in the header so its state is always visible but never in the way.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AnimatePresence, motion } from 'framer-motion'

import { IconCloudCheck, IconRefresh, IconWifiOff } from '@/components/ui/icons'
import { countPending, flushQueue, onQueueChange } from '@/lib/offlineQueue'
import clsx from 'clsx'

export function SyncStatus() {
  const { t } = useTranslation()
  const [online, setOnline] = useState(() => navigator.onLine)
  const [pending, setPending] = useState(0)
  const [flushing, setFlushing] = useState(false)

  useEffect(() => {
    const unsubscribe = onQueueChange(setPending)
    const handleOnline = () => setOnline(true)
    const handleOffline = () => {
      setOnline(false)
      void countPending().then(setPending)
    }
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    return () => {
      unsubscribe()
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  const retry = async () => {
    setFlushing(true)
    try {
      await flushQueue()
    } finally {
      setFlushing(false)
    }
  }

  const offline = !online
  const hasPending = pending > 0

  const label = offline
    ? t('app.offline')
    : hasPending
      ? t('app.sync_pending', { count: pending })
      : t('app.sync_done')

  return (
    <button
      type="button"
      onClick={hasPending || offline ? retry : undefined}
      disabled={!hasPending && !offline}
      className={clsx(
        'flex min-h-[2.25rem] items-center gap-1.5 rounded-pill border px-3 text-xs font-medium transition-colors',
        offline
          ? 'border-amber-400/40 bg-amber-400/10 text-amber-300'
          : hasPending
            ? 'border-violet-400/40 bg-violet-400/10 text-violet-300'
            : 'border-[rgb(var(--border))] muted',
      )}
      aria-label={label}
      title={label}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={offline ? 'off' : hasPending ? 'pending' : 'ok'}
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.8 }}
          transition={{ duration: 0.15 }}
          className={clsx('flex items-center gap-1.5', flushing && 'animate-pulse-soft')}
        >
          {offline ? (
            <IconWifiOff size={15} />
          ) : hasPending ? (
            <IconRefresh size={15} />
          ) : (
            <IconCloudCheck size={15} />
          )}
          {hasPending && <span className="tnum">{pending}</span>}
        </motion.span>
      </AnimatePresence>
    </button>
  )
}
