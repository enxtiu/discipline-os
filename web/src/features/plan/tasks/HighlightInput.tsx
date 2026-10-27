/**
 * A one-line-at-a-time text field that highlights recognised quick-add tokens
 * *inside* the typed text. A mirror layer renders the same text with marks
 * and sizes the box; a transparent textarea on top keeps the native caret,
 * selection, IME and keyboard. Both share exactly the same box metrics (only
 * background tints on the marks — no padding, no weight change), and Enter
 * submits instead of inserting a newline.
 */
import { forwardRef } from 'react'
import clsx from 'clsx'

import type { Priority, TokenKind } from '../types'
import type { Segment } from './parse'

const BOX = 'm-0 min-h-[3rem] whitespace-pre-wrap break-words px-4 py-3 font-sans text-base leading-6 tracking-normal'

/** Background tints only (text keeps its colour, so both themes stay readable). */
const TOKEN_CLASS: Record<TokenKind, string> = {
  date: 'bg-aqua-400/25',
  time: 'bg-aqua-400/25',
  block: 'bg-aqua-400/25',
  duration: 'bg-aqua-400/25',
  recurrence: 'bg-violet-400/30',
  reminder: 'bg-amber-400/25',
  priority: 'bg-rose-400/25',
  tag: 'bg-violet-400/30',
  project: 'bg-amber-400/25',
  context: 'bg-[rgb(var(--border))]',
}
const PRIORITY_TOKEN: Record<Priority, string> = {
  1: 'bg-rose-400/30',
  2: 'bg-amber-400/30',
  3: 'bg-violet-300/30',
  4: 'bg-[rgb(var(--border))]',
}

export const HighlightInput = forwardRef<
  HTMLTextAreaElement,
  {
    id?: string
    value: string
    onChange: (value: string) => void
    onSubmit: () => void
    segments: Segment[]
    priority?: Priority | null
    placeholder: string
    label: string
    autoFocus?: boolean
    describedBy?: string
    testId?: string
  }
>(function HighlightInput(
  { id, value, onChange, onSubmit, segments, priority, placeholder, label, autoFocus, describedBy, testId },
  ref,
) {
  return (
    <div className="relative rounded-2xl border border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))] transition-colors focus-within:border-[rgb(var(--accent))]">
      <div aria-hidden="true" className={clsx(BOX, 'pointer-events-none text-[rgb(var(--text))]')}>
        {value ? (
          segments.map((segment, index) =>
            segment.kind ? (
              <mark
                key={index}
                className={clsx(
                  'rounded-[4px] text-inherit box-decoration-clone',
                  segment.kind === 'priority' && priority ? PRIORITY_TOKEN[priority] : TOKEN_CLASS[segment.kind],
                )}
                data-kind={segment.kind}
                data-testid="quick-add-token"
              >
                {segment.text}
              </mark>
            ) : (
              <span key={index}>{segment.text}</span>
            ),
          )
        ) : (
          <span className="text-[rgb(var(--text-muted))]/70">{placeholder}</span>
        )}
        {'​'}
      </div>
      <textarea
        ref={ref}
        id={id}
        rows={1}
        value={value}
        aria-label={label}
        aria-describedby={describedBy}
        maxLength={500}
        autoFocus={autoFocus}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        enterKeyHint="send"
        className={clsx(
          BOX,
          'absolute inset-0 h-full w-full resize-none overflow-hidden bg-transparent text-transparent caret-[rgb(var(--text))] outline-none',
        )}
        style={{ WebkitTextFillColor: 'transparent' }}
        onChange={(event) => onChange(event.target.value.replace(/[\r\n]+/g, ' '))}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
            event.preventDefault()
            onSubmit()
          }
        }}
        data-testid={testId}
      />
    </div>
  )
})
