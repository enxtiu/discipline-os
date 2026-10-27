/**
 * /plan/journal?date=YYYY-MM-DD — the daily review.
 *
 * Date navigation (prev / next / today — the server's today from `useCounts`)
 * and a month strip with a dot on every day that has an entry; rating, mood
 * and energy as 1–5 selectors; wins, improvements and notes as Markdown with
 * Edit / Preview; everything autosaves (see `useJournalAutosave`). Below: the
 * server's summary of the day and deleting the entry.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Button, Callout, Card, ErrorState, IconButton, Skeleton, SkeletonCard } from '@/components/ui'
import { IconCheck, IconChevronLeft, IconChevronRight, IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { Markdown } from '@/lib/markdown'
import { errorMessage } from '@/lib/errors'
import { addDays } from '@/lib/format'
import { addMonths, dayMonth, dayNumber, dayTitle, endOfMonth, monthDays, monthTitle, readDayKey, sameMonth, weekdayShort } from '../calendar/dates'
import { relativeDay } from '../format'
import { useCounts, useJournal, useJournalList } from '../queries'
import { DaySummaryCard } from './DaySummaryCard'
import { useJournalAutosave, type JournalFields, type SaveStatus } from './useJournalAutosave'
import { ConflictView } from '@/features/sync/ConflictView'

type Scale = 'rating' | 'mood' | 'energy'
const SCALES: Scale[] = ['rating', 'mood', 'energy']
type TextField = 'wins_md' | 'improvements_md' | 'note_md'
const TEXTS: { key: TextField; id: string }[] = [
  { key: 'wins_md', id: 'wins' },
  { key: 'improvements_md', id: 'improvements' },
  { key: 'note_md', id: 'notes' },
]

/** The same day of the month `delta` months away (clamped to that month, never after `today`). */
function shiftMonth(date: string, delta: number, today: string | undefined): string {
  const first = addMonths(date, delta)
  const day = Math.min(dayNumber(date), dayNumber(endOfMonth(first)))
  const target = `${first.slice(0, 8)}${String(day).padStart(2, '0')}`
  return today && target > today ? today : target
}

export default function JournalScreen() {
  const { t } = useTranslation()
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const counts = useCounts()
  const today = counts.data?.date
  const requested = readDayKey(params.get('date'))
  // never a future day (the server allows at most tomorrow; a review is for days that happened)
  const date = (requested && today && requested > today ? today : requested) ?? today ?? ''
  const journal = useJournal(date)
  const autosave = useJournalAutosave(date, journal.data)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => setConfirming(false), [date])

  const go = (day: string | null) => {
    const query = new URLSearchParams(params)
    if (day && day !== today) query.set('date', day)
    else query.delete('date')
    setParams(query, { replace: true })
  }

  const near = !!today && !!date && (date === today || date === addDays(today, -1))
  const draft = autosave.draft

  const onDelete = async () => {
    setDeleting(true)
    try {
      const result = await autosave.deleteEntry()
      toast.success(result?.queued ? t('journal.delete_queued') : t('journal.deleted'))
      setConfirming(false)
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setDeleting(false)
    }
  }

  if (!date) {
    return counts.isError ? (
      <ErrorState message={errorMessage(counts.error)} onRetry={() => void counts.refetch()} retryLabel={t('app.retry')} />
    ) : (
      <JournalSkeleton />
    )
  }

  return (
    <div className="space-y-4" data-testid="journal-page" data-date={date}>
      <div className="flex items-center gap-1">
        <IconButton label={t('journal.prev_day')} icon={<IconChevronLeft size={22} />} onClick={() => go(addDays(date, -1))} />
        <div className="min-w-0 flex-1 text-center">
          <h2 className="truncate text-[15px] font-semibold" data-testid="journal-title">
            {near ? relativeDay(date, today) : dayTitle(date)}
          </h2>
          {near && <p className="truncate text-xs muted">{dayTitle(date)}</p>}
        </div>
        <IconButton
          label={t('journal.next_day')}
          icon={<IconChevronRight size={22} />}
          disabled={!today || date >= today}
          onClick={() => go(addDays(date, 1))}
        />
        <Button variant="secondary" size="sm" className="min-h-tap shrink-0" disabled={!today || date === today} onClick={() => go(null)}>
          {t('journal.today')}
        </Button>
      </div>

      <MonthStrip date={date} today={today} onPick={go} />

      {journal.isError && !journal.data ? (
        <ErrorState message={errorMessage(journal.error)} onRetry={() => void journal.refetch()} retryLabel={t('app.retry')} />
      ) : !draft || !journal.data ? (
        <JournalSkeleton compact />
      ) : (
        <>
          <StatusLine status={autosave.status} error={autosave.error} onRetry={autosave.flush} />

          {autosave.conflict && (
            <ConflictView
              fields={autosave.conflict.fields}
              mine={autosave.conflict.mine as Record<string, unknown>}
              current={autosave.conflict.theirs}
              onKeepTheirs={() => void autosave.resolve('theirs')}
              onKeepMine={() => void autosave.resolve('mine')}
            />
          )}

          <Card className="space-y-5" data-testid="journal-scales">
            {SCALES.map((scale) => (
              <ScaleField key={scale} scale={scale} value={draft[scale]} onChange={(value) => autosave.edit({ [scale]: value })} />
            ))}
          </Card>

          {TEXTS.map((field) => (
            <MarkdownField
              key={field.key}
              id={field.id}
              label={t(`journal.fields.${field.id}`)}
              placeholder={t(`journal.placeholders.${field.id}`)}
              value={draft[field.key]}
              onChange={(value) => autosave.edit({ [field.key]: value } as Partial<JournalFields>)}
            />
          ))}

          <DaySummaryCard day={journal.data.day} />

          {autosave.exists &&
            (confirming ? (
              <Callout tone="danger" title={t('journal.delete_confirm', { day: dayMonth(date) })}>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <Button variant="danger" loading={deleting} onClick={() => void onDelete()} data-testid="journal-delete-confirm">
                    {t('app.delete')}
                  </Button>
                  <Button variant="secondary" onClick={() => setConfirming(false)}>
                    {t('app.cancel')}
                  </Button>
                </div>
              </Callout>
            ) : (
              <Button
                block
                variant="ghost"
                className="text-rose-400"
                icon={<IconTrash size={18} />}
                onClick={() => setConfirming(true)}
                data-testid="journal-delete"
              >
                {t('journal.delete')}
              </Button>
            ))}
        </>
      )}
    </div>
  )
}

function JournalSkeleton({ compact = false }: { compact?: boolean }) {
  return (
    <div className="space-y-4" aria-hidden="true">
      {!compact && <Skeleton className="h-12" />}
      {!compact && <Skeleton className="h-16" />}
      <SkeletonCard lines={3} />
      <SkeletonCard lines={2} />
      <SkeletonCard lines={4} />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Save status
// --------------------------------------------------------------------------- //
function StatusLine({ status, error, onRetry }: { status: SaveStatus; error: string | null; onRetry: () => void }) {
  const { t } = useTranslation()
  const text =
    status === 'pending' || status === 'saving'
      ? t('journal.status.saving')
      : status === 'saved'
        ? t('journal.status.saved')
        : status === 'queued'
          ? t('journal.status.queued')
          : status === 'conflict'
            ? t('sync.conflict_title')
            : status === 'error'
            ? t('journal.status.error', { reason: error ?? t('app.error_generic') })
            : t('journal.status.idle')
  return (
    <div
      className={clsx(
        'flex min-h-[1.75rem] items-center gap-2 px-1 text-xs',
        status === 'error'
          ? 'text-rose-400'
          : status === 'conflict'
            ? 'text-amber-300'
            : status === 'saved'
              ? 'text-[rgb(var(--accent))]'
              : 'muted',
      )}
      role="status"
      aria-live="polite"
      data-testid="journal-status"
      data-status={status}
    >
      {status === 'saved' && <IconCheck size={14} />}
      <span className="min-w-0 flex-1">{text}</span>
      {status === 'error' && (
        <Button variant="ghost" size="sm" className="min-h-tap shrink-0" onClick={onRetry}>
          {t('app.retry')}
        </Button>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Month strip with entry dots
// --------------------------------------------------------------------------- //
function MonthStrip({ date, today, onPick }: { date: string; today: string | undefined; onPick: (day: string) => void }) {
  const { t } = useTranslation()
  const days = monthDays(date)
  const from = days[0]
  const last = days[days.length - 1]
  // the server reads at most tomorrow
  const limit = today ? addDays(today, 1) : last
  const to = last > limit ? (limit >= from ? limit : from) : last
  const list = useJournalList(from, to)
  const marked = new Set(
    (list.data?.items ?? [])
      .filter((item) => item.has_note || item.rating != null || item.mood != null || item.energy != null)
      .map((item) => item.date),
  )
  const scroller = useRef<HTMLDivElement>(null)

  // keep the selected day in view without scrolling the page
  useLayoutEffect(() => {
    const container = scroller.current
    const selected = container?.querySelector<HTMLElement>(`[data-date="${date}"]`)
    if (!container || !selected) return
    container.scrollLeft = selected.offsetLeft - container.clientWidth / 2 + selected.clientWidth / 2
  }, [date])

  return (
    <div className="space-y-1.5" data-testid="journal-strip">
      <div className="flex items-center justify-between gap-2">
        <IconButton
          label={t('journal.prev_month')}
          icon={<IconChevronLeft size={18} />}
          onClick={() => onPick(shiftMonth(date, -1, today))}
        />
        <span className="text-sm font-semibold">{monthTitle(date)}</span>
        <IconButton
          label={t('journal.next_month')}
          icon={<IconChevronRight size={18} />}
          disabled={!today || sameMonth(date, today) || date > today}
          onClick={() => onPick(shiftMonth(date, 1, today))}
        />
      </div>
      <div ref={scroller} className="scroll-x gap-1.5" role="list" aria-label={monthTitle(date)}>
        {days.map((day) => {
          const has = marked.has(day)
          const future = !!today && day > today
          const selected = day === date
          return (
            <div key={day} role="listitem" className="shrink-0">
              <button
                type="button"
                disabled={future}
                onClick={() => onPick(day)}
                aria-pressed={selected}
                aria-label={has ? t('journal.day_with_entry', { day: dayTitle(day) }) : dayTitle(day)}
                className={clsx(
                  'flex h-14 w-12 flex-col items-center justify-center gap-0.5 rounded-2xl border text-xs transition-colors',
                  selected
                    ? 'border-[rgb(var(--accent))]/60 bg-[rgb(var(--accent))]/10 text-[rgb(var(--accent))]'
                    : 'border-transparent',
                  future && 'opacity-40',
                  day === today && !selected && 'text-[rgb(var(--accent))]',
                )}
                data-testid="journal-strip-day"
                data-date={day}
                data-has-entry={has ? 'true' : 'false'}
              >
                <span className="text-[10px] uppercase leading-none muted">{weekdayShort(day)}</span>
                <span className="tnum text-sm font-semibold leading-none">{dayNumber(day)}</span>
                <span
                  className={clsx('h-1.5 w-1.5 rounded-full', has ? 'bg-[rgb(var(--accent))]' : 'bg-transparent')}
                  aria-hidden="true"
                />
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// 1–5 selectors
// --------------------------------------------------------------------------- //
function ScaleField({ scale, value, onChange }: { scale: Scale; value: number | null; onChange: (value: number | null) => void }) {
  const { t } = useTranslation()
  const label = t(`journal.scales.${scale}`)
  return (
    <div className="space-y-2" role="group" aria-labelledby={`journal-${scale}-label`} data-testid={`journal-${scale}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span id={`journal-${scale}-label`} className="label">
          {label}
        </span>
        <span className="text-xs muted" aria-live="polite">
          {value ? t(`journal.levels.${scale}.${value}`) : t('journal.not_set')}
        </span>
      </div>
      <div className="grid grid-cols-5 gap-2">
        {[1, 2, 3, 4, 5].map((n) => {
          const active = value === n
          const filled = value !== null && n <= value
          return (
            <button
              key={n}
              type="button"
              aria-pressed={active}
              aria-label={t('journal.scale_option', { label, n, level: t(`journal.levels.${scale}.${n}`) })}
              onClick={() => onChange(active ? null : n)}
              className={clsx(
                'tnum min-h-tap rounded-2xl border text-lg font-semibold transition-colors',
                active
                  ? 'border-transparent bg-[rgb(var(--accent))] text-ink-950'
                  : filled
                    ? 'border-[rgb(var(--accent))]/40 bg-[rgb(var(--accent))]/10 text-[rgb(var(--accent))]'
                    : 'border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))] text-[rgb(var(--text-muted))]',
              )}
              data-testid={`journal-${scale}-${n}`}
            >
              {n}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Markdown field with Edit / Preview
// --------------------------------------------------------------------------- //
function MarkdownField({
  id,
  label,
  placeholder,
  value,
  onChange,
}: {
  id: string
  label: string
  placeholder: string
  value: string
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const [preview, setPreview] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)

  // grow with the text (no inner scrollbar on a phone)
  useLayoutEffect(() => {
    const element = area.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight + 2}px`
  }, [value, preview])

  return (
    <Card className="space-y-2" data-testid={`journal-field-${id}`}>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={`journal-${id}`} className="label">
          {label}
        </label>
        <Button
          variant="ghost"
          size="sm"
          className="min-h-tap"
          aria-pressed={preview}
          onClick={() => setPreview((current) => !current)}
          data-testid={`journal-${id}-toggle`}
        >
          {preview ? t('journal.edit') : t('journal.preview')}
        </Button>
      </div>
      {preview ? (
        value.trim() ? (
          <Markdown source={value} className="min-h-[3rem]" />
        ) : (
          <p className="text-sm muted">{t('journal.preview_empty')}</p>
        )
      ) : (
        <textarea
          id={`journal-${id}`}
          ref={area}
          rows={3}
          maxLength={20000}
          className="input min-h-[5.5rem] resize-none overflow-hidden leading-relaxed"
          placeholder={placeholder}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          data-testid={`journal-${id}`}
        />
      )}
    </Card>
  )
}
