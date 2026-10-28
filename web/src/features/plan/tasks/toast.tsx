/**
 * Planner toasts with an optional action («Отменить», «Открыть»).
 *
 * The shared `components/ui/Toast` only carries a message, and an undo needs a
 * button, so the planner keeps its own tiny store here. It looks exactly like
 * the shared toast and sits in the same place; `TaskSheetHost` (mounted once in
 * the app shell) renders it, so any planner screen — and the Today card — can
 * call `planToast.*` without a provider.
 *
 * `placement: 'top'` is for toasts raised while a sheet stays open (quick add,
 * the editor): at the bottom they would cover the sheet's own buttons — and
 * with the iPhone keyboard up, the top of the screen is what is visible.
 */
import { useSyncExternalStore } from 'react'
import { AnimatePresence, m } from 'framer-motion'

import { IconAlert, IconCheck, IconInfo } from '@/components/ui/icons'
import { useReducedMotionPref } from '@/lib/appearance'

export interface PlanToastAction {
  label: string
  onClick: () => void
}

export type PlanToastPlacement = 'top' | 'bottom'

export interface PlanToastOptions {
  action?: PlanToastAction
  placement?: PlanToastPlacement
}

interface PlanToastItem {
  id: string
  tone: 'success' | 'error' | 'info'
  message: string
  action?: PlanToastAction
  placement: PlanToastPlacement
}

let items: PlanToastItem[] = []
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function remove(id: string) {
  items = items.filter((item) => item.id !== id)
  emit()
}

/** The second argument is an action or full options (`{ action, placement }`). */
type Options = PlanToastAction | PlanToastOptions | undefined

function normalize(options: Options): PlanToastOptions {
  if (!options) return {}
  return 'label' in options ? { action: options } : options
}

function push(message: string, tone: PlanToastItem['tone'], options: Options): string {
  const { action, placement = 'bottom' } = normalize(options)
  const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
  items = [...items.slice(-2), { id, tone, message, action, placement }]
  emit()
  // an action needs time to be reached with a thumb
  const duration = action ? 5500 : tone === 'error' ? 5000 : 3200
  window.setTimeout(() => remove(id), duration)
  return id
}

export const planToast = {
  success: (message: string, options?: Options) => push(message, 'success', options),
  info: (message: string, options?: Options) => push(message, 'info', options),
  error: (message: string, options?: Omit<PlanToastOptions, 'action'>) => push(message, 'error', options),
  dismiss: remove,
}

export function PlanToastHost() {
  const list = useSyncExternalStore(subscribe, () => items)
  return (
    <>
      <ToastStack items={list.filter((item) => item.placement === 'top')} placement="top" />
      <ToastStack items={list.filter((item) => item.placement === 'bottom')} placement="bottom" />
    </>
  )
}

function ToastStack({ items: stack, placement }: { items: PlanToastItem[]; placement: PlanToastPlacement }) {
  const reduce = useReducedMotionPref() // the OS or «Меньше анимации» (stage 10)
  const offset = placement === 'top' ? -16 : 16
  return (
    <div
      className={`pointer-events-none fixed inset-x-0 z-[60] flex flex-col items-center gap-2 px-4 ${placement === 'bottom' ? 'sk-toasts' : ''}`}
      style={
        placement === 'top'
          ? { top: 'calc(0.75rem + env(safe-area-inset-top, 0px))' }
          : { bottom: 'calc(7.25rem + env(safe-area-inset-bottom, 0px))' }
      }
      aria-live="polite"
      role="status"
    >
      <AnimatePresence initial={false}>
        {stack.map((item) => (
          <m.div
            key={item.id}
            layout={!reduce}
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: offset, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: offset / 2, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 500, damping: 34 }}
            className="pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-2xl border border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))] py-1 pl-4 pr-2 shadow-card"
            data-testid="plan-toast"
            data-placement={placement}
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
            <p className="min-w-0 flex-1 py-2.5 text-sm leading-snug" onClick={() => remove(item.id)}>
              {item.message}
            </p>
            {item.action && (
              <button
                type="button"
                className="min-h-tap shrink-0 rounded-xl px-3 text-sm font-semibold text-[rgb(var(--accent))] active:bg-[rgb(var(--border))]/40"
                onClick={() => {
                  remove(item.id)
                  item.action?.onClick()
                }}
              >
                {item.action.label}
              </button>
            )}
          </m.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
