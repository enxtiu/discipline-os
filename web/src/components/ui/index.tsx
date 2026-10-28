/**
 * UI primitives. Deliberately small and unstyled-by-default: the visual
 * language lives in `styles/index.css` (`.card`, `.btn`, …), these components
 * add behaviour (accessibility, keyboard, motion) and structure.
 */
import {
  createContext,
  forwardRef,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'
import { AnimatePresence, m } from 'framer-motion'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { IconCheck, IconClose, IconInfo } from './icons'
import type { Category } from './ProgressRing'

// --------------------------------------------------------------------------- //
// Layout
// --------------------------------------------------------------------------- //
export function Card({
  className,
  padded = true,
  children,
  ...rest
}: { className?: string; padded?: boolean; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx('card', padded && 'p-4', className)} {...rest}>
      {children}
    </div>
  )
}

export function Section({
  title,
  action,
  children,
  className,
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={clsx('flex flex-col gap-3', className)}>
      {(title || action) && (
        <header className="flex items-baseline justify-between gap-3">
          {title && <h2 className="h2">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function Rows({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx('card divide-y divide-[rgb(var(--border))] overflow-hidden', className)}>
      {children}
    </div>
  )
}

export function Row({
  label,
  value,
  hint,
  onClick,
  icon,
  trailing,
  className,
}: {
  label: ReactNode
  value?: ReactNode
  hint?: ReactNode
  onClick?: () => void
  icon?: ReactNode
  trailing?: ReactNode
  className?: string
}) {
  const content = (
    <>
      {icon && <span className="shrink-0 text-[rgb(var(--text-muted))]">{icon}</span>}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-medium">{label}</span>
        {hint && <span className="mt-0.5 block truncate text-xs text-[rgb(var(--text-muted))]">{hint}</span>}
      </span>
      {value !== undefined && <span className="shrink-0 text-sm tnum muted">{value}</span>}
      {trailing}
    </>
  )
  if (!onClick) {
    return <div className={clsx('flex items-center gap-3 px-4 py-3.5', className)}>{content}</div>
  }
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors active:bg-[rgb(var(--border))]/40',
        className,
      )}
    >
      {content}
    </button>
  )
}

// --------------------------------------------------------------------------- //
// Buttons
// --------------------------------------------------------------------------- //
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: ButtonVariant
    size?: 'md' | 'sm'
    loading?: boolean
    icon?: ReactNode
    block?: boolean
  }
>(function Button(
  { variant = 'primary', size = 'md', loading, icon, block, className, children, disabled, onClick, ...rest },
  ref,
) {
  // While loading the button keeps its focus (a `disabled` button drops it to
  // <body> and a keyboard or VoiceOver user loses their place): it is marked
  // busy and ignores presses instead.
  return (
    <button
      ref={ref}
      className={clsx(
        `btn-${variant}`,
        size === 'sm' && 'btn-sm',
        block && 'w-full',
        loading && 'pointer-events-none opacity-50',
        className,
      )}
      disabled={disabled}
      aria-disabled={loading || undefined}
      aria-busy={loading || undefined}
      onClick={(event) => {
        if (loading) {
          event.preventDefault()
          return
        }
        onClick?.(event)
      }}
      {...rest}
    >
      {loading ? <Spinner size={18} /> : icon}
      {children}
    </button>
  )
})

export function IconButton({
  label,
  icon,
  className,
  variant = 'ghost',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; icon: ReactNode; variant?: ButtonVariant }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={clsx(
        `btn-${variant}`,
        'h-tap w-tap min-h-tap shrink-0 rounded-2xl p-0',
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  )
}

// --------------------------------------------------------------------------- //
// Feedback
// --------------------------------------------------------------------------- //
export function Spinner({ size = 20, className }: { size?: number; className?: string }) {
  return (
    <svg
      className={clsx('animate-spin', className)}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton h-4 w-full', className)} />
}

export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <Card className="space-y-3">
      <Skeleton className="h-5 w-1/3" />
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} className={index === lines - 1 ? 'w-2/3' : 'w-full'} />
      ))}
    </Card>
  )
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-card border border-dashed border-[rgb(var(--border))] px-6 py-10 text-center">
      {icon && <span className="text-[rgb(var(--text-muted))]">{icon}</span>}
      <p className="font-medium">{title}</p>
      {description && <p className="max-w-xs text-sm text-[rgb(var(--text-muted))]">{description}</p>}
      {action}
    </div>
  )
}

export function ErrorState({
  message,
  onRetry,
  retryLabel,
}: {
  message: string
  onRetry?: () => void
  retryLabel?: string
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-card border border-rose-500/30 bg-rose-500/5 px-6 py-8 text-center">
      <span className="text-rose-400">
        <IconInfo size={28} />
      </span>
      <p className="text-sm text-rose-200">{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          {retryLabel ?? 'Retry'}
        </Button>
      )}
    </div>
  )
}

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: 'neutral' | 'accent' | 'violet' | 'warn' | 'danger'
  className?: string
}) {
  const tones: Record<string, string> = {
    neutral: 'border-[rgb(var(--border))] text-[rgb(var(--text-muted))]',
    accent: 'border-aqua-400/40 bg-aqua-400/10 text-aqua-300',
    violet: 'border-violet-400/40 bg-violet-400/10 text-violet-300',
    warn: 'border-amber-400/40 bg-amber-400/10 text-amber-300',
    danger: 'border-rose-500/40 bg-rose-500/10 text-rose-400',
  }
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-pill border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Marks a screen that ships in a later stage (keeps the shell honest). */
export function StageBadge({ stage }: { stage: number }) {
  return (
    <Badge tone="violet">
      <span className="lowercase">stage</span> {stage}
    </Badge>
  )
}

// --------------------------------------------------------------------------- //
// Forms
// --------------------------------------------------------------------------- //
/**
 * What a `Field` tells the control inside it: the id its <label> points at, the
 * label's own id (for widgets such as a segmented control) and the hint/error.
 */
const FieldContext = createContext<{ controlId: string; labelId?: string; describedBy?: string } | null>(null)

/** Props an `Input` / `Select` inside a `Field` needs to be labelled and described by it. */
function useFieldControl(props: { id?: string; 'aria-label'?: string; 'aria-labelledby'?: string; 'aria-describedby'?: string }) {
  const field = useContext(FieldContext)
  if (!field) return {}
  const named = props['aria-label'] || props['aria-labelledby']
  return {
    id: props.id ?? field.controlId,
    // an explicit id that is not the label's target: name it through the label's id
    'aria-labelledby': !named && props.id && props.id !== field.controlId ? field.labelId : props['aria-labelledby'],
    'aria-describedby': props['aria-describedby'] ?? field.describedBy,
  }
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label?: ReactNode
  hint?: ReactNode
  error?: ReactNode
  children: ReactNode
  htmlFor?: string
}) {
  // The label is tied to the Input/Select inside even without `htmlFor`, and a
  // segmented control takes it as its name; the hint or error describes the field.
  const autoId = useId()
  const controlId = htmlFor ?? `${autoId}control`
  const labelId = label ? `${autoId}label` : undefined
  const noteId = error || hint ? `${autoId}note` : undefined
  return (
    <FieldContext.Provider value={{ controlId, labelId, describedBy: noteId }}>
      <div className="field">
        {label && (
          <label id={labelId} htmlFor={controlId} className="label">
            {label}
          </label>
        )}
        {children}
        {error ? (
          <p id={noteId} className="text-xs text-rose-400">
            {error}
          </p>
        ) : (
          hint && (
            <p id={noteId} className="text-xs text-[rgb(var(--text-muted))]">
              {hint}
            </p>
          )
        )}
      </div>
    </FieldContext.Provider>
  )
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  const field = useFieldControl(rest)
  return <input ref={ref} className={clsx('input', className)} {...rest} {...field} />
})

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  const field = useFieldControl(rest)
  return (
    <select ref={ref} className={clsx('input appearance-none pr-10', className)} {...rest} {...field}>
      {children}
    </select>
  )
})

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: string
  disabled?: boolean
}) {
  // The 28 px track is drawn inside a 48 px tall button; the negative margin keeps
  // the rows it sits in exactly as tall as before.
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="-mx-1 -my-2.5 inline-flex h-tap w-14 shrink-0 items-center justify-center rounded-2xl"
    >
      <span
        className={clsx(
          'relative h-7 w-12 shrink-0 rounded-pill border transition-colors duration-200',
          checked
            ? 'border-transparent bg-[rgb(var(--accent))]'
            : 'border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))]',
          disabled && 'opacity-50',
        )}
        aria-hidden="true"
      >
        <m.span
          layout
          transition={{ type: 'spring', stiffness: 700, damping: 34 }}
          className={clsx(
            'absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm',
            checked ? 'left-[1.55rem]' : 'left-0.5',
          )}
        />
      </span>
    </button>
  )
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  className,
  label,
}: {
  value: T
  options: { value: T; label: ReactNode }[]
  onChange: (next: T) => void
  className?: string
  /** Accessible name; inside a `Field` the field's label is used. */
  label?: string
}) {
  const field = useContext(FieldContext)
  return (
    <div
      className={clsx('segmented', className)}
      role="tablist"
      aria-label={label}
      aria-labelledby={!label ? field?.labelId : undefined}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          data-active={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Sheet (bottom sheet — the primary mobile interaction)
// --------------------------------------------------------------------------- //
const FOCUSABLE =
  'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]'

function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.tabIndex >= 0 && !el.closest('[inert], [aria-hidden="true"]') && el.getClientRects().length > 0,
  )
}

/** Open sheets, the topmost last: only the topmost one traps focus and answers Escape. */
const openSheets: symbol[] = []

/**
 * Modal focus for a sheet: focus moves into it when it opens (unless a field
 * inside already took it with `autoFocus`), Tab / Shift+Tab cycle inside it,
 * Escape closes it, and focus goes back to the control that opened it.
 */
function useSheetFocus(open: boolean, dialog: React.RefObject<HTMLElement>, onClose: () => void) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  // The opener is read while rendering the open state: by the time effects run,
  // an `autoFocus` field inside the sheet already holds the focus.
  const opener = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)
  if (open !== wasOpen.current) {
    wasOpen.current = open
    if (open && typeof document !== 'undefined') {
      const active = document.activeElement
      opener.current = active instanceof HTMLElement && active !== document.body ? active : null
    }
  }

  useEffect(() => {
    if (!open) return
    const token = Symbol('sheet')
    openSheets.push(token)
    const isTop = () => openSheets[openSheets.length - 1] === token
    const returnTo = opener.current
    const frame = window.requestAnimationFrame(() => {
      const root = dialog.current
      if (root && !root.contains(document.activeElement)) root.focus({ preventScroll: true })
    })
    const onKeyDown = (event: KeyboardEvent) => {
      const root = dialog.current
      if (!root || !isTop() || event.defaultPrevented || event.isComposing) return
      if (event.key === 'Escape') {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables(root)
      const active = document.activeElement
      if (!items.length) {
        event.preventDefault()
        root.focus({ preventScroll: true })
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && (active === first || active === root || !root.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !root.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }
    // focus that lands outside (a click on the page behind) goes back into the sheet
    const onFocusIn = (event: FocusEvent) => {
      const root = dialog.current
      if (!root || !isTop() || !(event.target instanceof Node) || root.contains(event.target)) return
      if (event.target instanceof Element && event.target.closest('[role="dialog"], [role="status"], [aria-live]')) return
      ;(focusables(root)[0] ?? root).focus({ preventScroll: true })
    }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('focusin', onFocusIn)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('focusin', onFocusIn)
      const index = openSheets.indexOf(token)
      if (index >= 0) openSheets.splice(index, 1)
      const root = dialog.current
      const active = document.activeElement
      const focusWasInside = !active || active === document.body || (!!root && root.contains(active))
      if (!focusWasInside) return
      if (returnTo?.isConnected) {
        returnTo.focus({ preventScroll: true })
      } else {
        // the opener is gone (the sheet replaced it, e.g. «Запустить таймер» → the timer
        // bar): continue from the page content rather than from the top of the document
        const main = document.querySelector('main')
        if (main) {
          if (!main.hasAttribute('tabindex')) main.setAttribute('tabindex', '-1')
          main.focus({ preventScroll: true })
        }
      }
    }
  }, [open, dialog])
}

export function Sheet({
  open,
  onClose,
  title,
  children,
  footer,
  size = 'auto',
}: {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: 'auto' | 'tall'
}) {
  const { t } = useTranslation()
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  useSheetFocus(open, dialogRef, onClose)
  // A body that scrolls but holds nothing focusable (a long explanation) takes the
  // focus itself, so the keyboard can scroll it.
  const [scrollStop, setScrollStop] = useState(false)
  useEffect(() => {
    const body = bodyRef.current
    if (!open || !body) return
    const update = () => setScrollStop(body.scrollHeight > body.clientHeight + 1 && focusables(body).length === 0)
    update()
    const resize = new ResizeObserver(update)
    resize.observe(body)
    const mutation = new MutationObserver(update)
    mutation.observe(body, { childList: true, subtree: true })
    return () => {
      resize.disconnect()
      mutation.disconnect()
    }
  }, [open])
  return (
    <AnimatePresence>
      {open && (
        <>
          <m.div
            className="sk-scrim fixed inset-0 z-50 bg-black/60 backdrop-blur-[2px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden="true"
          />
          <m.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={title ? titleId : undefined}
            tabIndex={-1}
            className={clsx(
              'sk-sheet fixed inset-x-0 bottom-0 z-50 flex flex-col rounded-t-3xl border border-b-0 border-[rgb(var(--border))] bg-[rgb(var(--bg-elevated))] focus:outline-none',
              size === 'tall' ? 'max-h-[92dvh]' : 'max-h-[85dvh]',
            )}
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', stiffness: 420, damping: 38, mass: 0.8 }}
            drag="y"
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.4 }}
            onDragEnd={(_, info) => {
              if (info.offset.y > 120 || info.velocity.y > 800) onClose()
            }}
          >
            <div className="flex items-center justify-between gap-3 px-5 pt-4" aria-hidden="true">
              <div className="sk-sheet-handle mx-auto h-1.5 w-10 rounded-pill bg-[rgb(var(--border))]" />
            </div>
            {title && (
              <header className="flex items-center justify-between gap-3 px-5 pb-2 pt-3">
                <h2 id={titleId} className="h2">
                  {title}
                </h2>
                <IconButton label={t('app.close')} icon={<IconClose size={20} />} onClick={onClose} />
              </header>
            )}
            <div
              ref={bodyRef}
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-4"
              {...(scrollStop
                ? { tabIndex: 0, role: 'region', 'aria-labelledby': title ? titleId : undefined }
                : {})}
            >
              {children}
            </div>
            {footer && (
              <footer
                className="border-t border-[rgb(var(--border))] px-5 pt-3"
                style={{ paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom, 0px))' }}
              >
                {footer}
              </footer>
            )}
          </m.div>
        </>
      )}
    </AnimatePresence>
  )
}

// --------------------------------------------------------------------------- //
// Progress
// --------------------------------------------------------------------------- //
/**
 * A thin progress bar. Decorative (hidden from screen readers) unless `label`
 * is given: everywhere in the app the numbers it draws are also written as
 * text next to it, and reading them twice only adds noise. Give it a `label`
 * (and a `valueText` such as «3 из 5 дней») when the bar is the only place
 * the value appears.
 */
export function ProgressBar({
  value,
  max,
  tone = 'accent',
  className,
  label,
  valueText,
}: {
  value: number
  max: number
  tone?: 'accent' | 'violet' | 'warn' | Category
  className?: string
  label?: string
  valueText?: string
}) {
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0
  // stage 10: a category bar takes the theme's `--cat-*` fill and the glow of the stage-8 tone
  // it replaced (literal classes: Tailwind purges built ones); the `sk-*` hooks let a theme
  // restyle the track and the fill (styles/skins/*.css)
  const tones: Record<NonNullable<typeof tone>, string> = {
    accent: 'bg-aqua-400 shadow-[0_0_12px_rgb(56_224_200/.6)]',
    violet: 'bg-violet-400 shadow-[0_0_12px_rgb(139_92_246/.6)]',
    warn: 'bg-amber-400 shadow-[0_0_12px_rgb(251_191_36/.6)]',
    kcal: 'bg-[rgb(var(--cat-kcal))] shadow-[0_0_12px_rgb(56_224_200/.6)]',
    protein: 'bg-[rgb(var(--cat-protein))] shadow-[0_0_12px_rgb(139_92_246/.6)]',
    fat: 'bg-[rgb(var(--cat-fat))] shadow-[0_0_12px_rgb(251_191_36/.6)]',
    carbs: 'bg-[rgb(var(--cat-carbs))] shadow-[0_0_12px_rgb(56_224_200/.6)]',
    fibre: 'bg-[rgb(var(--cat-fibre))] shadow-[0_0_12px_rgb(56_224_200/.6)]',
    water: 'bg-[rgb(var(--cat-water))] shadow-[0_0_12px_rgb(56_224_200/.6)]',
    steps: 'bg-[rgb(var(--cat-steps))] shadow-[0_0_12px_rgb(139_92_246/.6)]',
    streak: 'bg-[rgb(var(--cat-streak))] shadow-[0_0_12px_rgb(251_191_36/.6)]',
    score: 'bg-[rgb(var(--cat-score))] shadow-[0_0_12px_rgb(56_224_200/.6)]',
    xp: 'bg-[rgb(var(--cat-xp))] shadow-[0_0_12px_rgb(139_92_246/.6)]',
  }
  const semantics = label
    ? {
        role: 'progressbar',
        'aria-label': label,
        'aria-valuenow': Math.round(ratio * 100),
        'aria-valuemin': 0,
        'aria-valuemax': 100,
        'aria-valuetext': valueText,
      }
    : { 'aria-hidden': true }
  return (
    <div
      className={clsx('sk-bar h-2 w-full overflow-hidden rounded-pill bg-[rgb(var(--bg-sunken))]', className)}
      data-tone={tone}
      {...semantics}
    >
      <m.div
        className={clsx('sk-bar-fill h-full rounded-pill', tones[tone])}
        initial={{ width: 0 }}
        animate={{ width: `${ratio * 100}%` }}
        transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
      />
    </div>
  )
}

export function Callout({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'warn' | 'danger' | 'success'
  title?: ReactNode
  children: ReactNode
}) {
  const tones = {
    info: 'border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))] text-[rgb(var(--text-muted))]',
    warn: 'border-amber-400/35 bg-amber-400/10 text-amber-200',
    danger: 'border-rose-500/35 bg-rose-500/10 text-rose-200',
    success: 'border-aqua-400/35 bg-aqua-400/10 text-aqua-200',
  }
  return (
    <div className={clsx('rounded-2xl border px-4 py-3 text-sm', tones[tone])}>
      {title && <p className="mb-1 flex items-center gap-2 font-semibold">{title}</p>}
      <div className="leading-relaxed">{children}</div>
    </div>
  )
}

export function CheckList({ items, className }: { items: ReactNode[]; className?: string }) {
  return (
    <ul className={clsx('space-y-2 text-sm', className)}>
      {items.map((item, index) => (
        <li key={index} className="flex gap-2.5">
          <span className="mt-0.5 text-aqua-400">
            <IconCheck size={16} />
          </span>
          <span className="leading-relaxed">{item}</span>
        </li>
      ))}
    </ul>
  )
}
