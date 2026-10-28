/**
 * Toast notifications with safe-area aware placement.
 * `success` doubles as the confirmation channel for offline-queued writes.
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence, m } from 'framer-motion'
import { IconAlert, IconCheck, IconInfo } from './icons'

export type ToastTone = 'success' | 'error' | 'info'

export interface ToastItem {
  id: string
  tone: ToastTone
  message: string
  /** ms; 0 = sticky */
  duration: number
}

interface ToastApi {
  push: (message: string, tone?: ToastTone, duration?: number) => void
  success: (message: string) => void
  error: (message: string) => void
  info: (message: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const remove = useCallback((id: string) => {
    setItems((current) => current.filter((item) => item.id !== id))
  }, [])

  const push = useCallback(
    (message: string, tone: ToastTone = 'info', duration = 3200) => {
      const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
      setItems((current) => [...current.slice(-2), { id, tone, message, duration }])
      if (duration > 0) window.setTimeout(() => remove(id), duration)
    },
    [remove],
  )

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (message: string) => push(message, 'success'),
      error: (message: string) => push(message, 'error', 5000),
      info: (message: string) => push(message, 'info'),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="sk-toasts pointer-events-none fixed inset-x-0 z-[60] flex flex-col items-center gap-2 px-4"
        style={{ bottom: 'calc(7.25rem + env(safe-area-inset-bottom, 0px))' }}
        aria-live="polite"
        role="status"
      >
        <AnimatePresence initial={false}>
          {items.map((item) => (
            <m.div
              key={item.id}
              layout
              initial={{ opacity: 0, y: 16, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={{ type: 'spring', stiffness: 500, damping: 34 }}
              onClick={() => remove(item.id)}
              className="pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-2xl border border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))] px-4 py-3 shadow-card"
            >
              <span
                className={
                  item.tone === 'success'
                    ? 'text-aqua-400'
                    : item.tone === 'error'
                      ? 'text-rose-400'
                      : 'text-[rgb(var(--text-muted))]'
                }
              >
                {item.tone === 'success' ? (
                  <IconCheck size={18} />
                ) : item.tone === 'error' ? (
                  <IconAlert size={18} />
                ) : (
                  <IconInfo size={18} />
                )}
              </span>
              <p className="flex-1 text-sm leading-snug">{item.message}</p>
            </m.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside <ToastProvider>')
  return context
}
