/**
 * Small planner UI pieces shared by the task lists, the editor and the PARA
 * screens. Everything builds on `components/ui` and the design tokens; colours
 * that come from data (a project's colour) are applied inline, never as
 * runtime-built class names.
 */
import { useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, SegmentedControl } from '@/components/ui'
import { IconChevronLeft, IconTrash } from '@/components/ui/icons'
import { Markdown, toggleTaskLine } from '@/lib/markdown'

export { ColorDot } from './ColorDot'

/** "‹ Проекты" — back to the list a detail screen belongs to (48 px target). */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="-ml-2 inline-flex min-h-tap items-center gap-1 rounded-xl px-2 text-sm text-[rgb(var(--text-muted))]"
    >
      <IconChevronLeft size={18} />
      {label}
    </Link>
  )
}

/** A selectable chip with a full 48 px hit area. */
export function Chip({
  active,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={clsx('chip min-h-tap shrink-0 justify-center', active && 'chip-active', className)}
      {...rest}
    >
      {children}
    </button>
  )
}

/** Delete with a second, explicit tap. */
export function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
  loading,
  testId,
}: {
  label: string
  confirmLabel: string
  onConfirm: () => void
  loading?: boolean
  testId?: string
}) {
  const { t } = useTranslation()
  const [armed, setArmed] = useState(false)
  if (!armed) {
    return (
      <Button
        variant="ghost"
        block
        className="text-rose-400"
        icon={<IconTrash size={18} />}
        onClick={() => setArmed(true)}
        data-testid={testId}
      >
        {label}
      </Button>
    )
  }
  return (
    <div className="flex gap-2" role="group" aria-label={label}>
      <Button
        variant="danger"
        className="flex-1"
        loading={loading}
        onClick={onConfirm}
        data-testid={testId ? `${testId}-confirm` : undefined}
      >
        {confirmLabel}
      </Button>
      <Button variant="secondary" onClick={() => setArmed(false)}>
        {t('app.cancel')}
      </Button>
    </div>
  )
}

/** Notes in Markdown: Edit / Preview; checkboxes in the preview toggle the source. */
export function MarkdownField({
  id,
  label,
  value,
  onChange,
  placeholder,
  error,
  testId,
}: {
  id: string
  label: ReactNode
  value: string
  onChange: (next: string) => void
  placeholder?: string
  error?: ReactNode
  testId?: string
}) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<'edit' | 'preview'>(() => (value.trim() ? 'preview' : 'edit'))
  return (
    <div className="field">
      <div className="flex items-center justify-between gap-3">
        {mode === 'edit' ? (
          <label htmlFor={id} className="label">
            {label}
          </label>
        ) : (
          <span className="label">{label}</span>
        )}
        <SegmentedControl
          value={mode}
          onChange={setMode}
          className="w-48 shrink-0 [&>button]:min-h-tap"
          options={[
            { value: 'edit', label: t('task.md_edit') },
            { value: 'preview', label: t('task.md_preview') },
          ]}
        />
      </div>
      {mode === 'edit' ? (
        <textarea
          id={id}
          className="input min-h-[8rem] resize-y leading-relaxed"
          value={value}
          maxLength={20000}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          data-testid={testId}
        />
      ) : (
        <div className="surface-sunken min-h-[4rem] px-4 py-3" data-testid={testId ? `${testId}-preview` : undefined}>
          {value.trim() ? (
            <Markdown source={value} onToggleTask={(line, checked) => onChange(toggleTaskLine(value, line, checked))} />
          ) : (
            <p className="text-sm muted">{t('task.md_empty')}</p>
          )}
        </div>
      )}
      {error ? (
        <p className="text-xs text-rose-400">{error}</p>
      ) : (
        <p className="text-xs muted">{t('task.md_hint')}</p>
      )}
    </div>
  )
}
