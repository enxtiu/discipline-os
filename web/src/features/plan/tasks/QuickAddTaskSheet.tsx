/**
 * Natural-language quick add. One field; the server previews the parse as you
 * type (tokens highlighted in the text, the result as chips); Enter or
 * «Добавить» creates the task (`POST /tasks/quick` with a client id and the tap
 * instant as `captured_at`, so "завтра" stays tomorrow even if sent later from
 * the offline queue). The sheet stays open for the next line.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Sheet } from '@/components/ui'
import { IconCheck, IconWifiOff } from '@/components/ui/icons'
import { errorMessage } from '@/lib/errors'
import { openTask, type QuickAddRequest } from '../events'
import { relativeDay } from '../format'
import { IconFolder, IconLayers, IconSubtasks } from '../icons'
import { newId, useAreas, useCounts, useProjects, useQuickAdd } from '../queries'
import { HighlightInput } from './HighlightInput'
import { highlightSegments, useParsePreview } from './parse'
import { ParseChips, parseChips } from './ParseChips'
import { planToast } from './toast'

interface Added {
  id: string
  title: string
  queued: boolean
}

const EXAMPLES = ['task.qa_example_1', 'task.qa_example_2', 'task.qa_example_3'] as const

export function QuickAddTaskSheet({
  open,
  request,
  onClose,
}: {
  open: boolean
  request: QuickAddRequest
  onClose: () => void
}) {
  const { t } = useTranslation()
  const hintId = useId()
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState('')
  const [added, setAdded] = useState<Added[]>([])
  const today = useCounts().data?.date
  const quick = useQuickAdd()
  const { preview, status } = useParsePreview(text, open)
  const defaults = request.defaults
  const projects = useProjects('open')
  const areas = useAreas()

  // a fresh line every time the sheet opens (prefilled e.g. with "+Ремонт ")
  useEffect(() => {
    if (!open) return
    setText(request.text ?? '')
    setAdded([])
    const handle = window.setTimeout(() => inputRef.current?.focus(), 60)
    return () => window.clearTimeout(handle)
  }, [open, request])

  const segments = useMemo(() => highlightSegments(text, preview), [text, preview])
  const parsed = preview?.result ?? null
  const chips = parsed && text.trim() ? parseChips(parsed, today) : []
  const title = parsed && text.trim() ? parsed.title.trim() : ''

  const defaultProject = defaults?.project_id
    ? projects.data?.items.find((project) => project.id === defaults.project_id)
    : undefined
  const defaultArea = defaults?.area_id ? areas.data?.items.find((area) => area.id === defaults.area_id) : undefined

  const submit = () => {
    const value = text.trim()
    if (!value || value.length > 500) return
    const id = newId()
    const shownTitle = (preview?.text === text && title) || value
    setText(request.text ?? '')
    inputRef.current?.focus()
    quick.mutate(
      { id, text: value, captured_at: new Date().toISOString(), defaults },
      {
        onSuccess: (result) => {
          if (result.queued) {
            setAdded((list) => [{ id, title: shownTitle, queued: true }, ...list].slice(0, 5))
            planToast.info(t('task.queued'))
            return
          }
          const task = result.data.task
          setAdded((list) => [{ id: task.id, title: task.title, queued: false }, ...list].slice(0, 5))
          planToast.success(t('task.added', { title: task.title }), {
            label: t('task.open'),
            onClick: () => openTask(task.id),
          })
        },
        onError: (error) => {
          planToast.error(errorMessage(error))
          // give the line back if nothing new was typed meanwhile
          setText((current) => (current === (request.text ?? '') ? value : current))
        },
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('task.quick_add_title')}>
      <div className="space-y-4 pb-2" data-testid="quick-add-sheet">
        {(defaults?.project_id || defaults?.area_id || defaults?.due_date || defaults?.parent_id) && (
          <div className="flex flex-wrap gap-1.5 text-xs" data-testid="quick-add-defaults">
            {defaults?.project_id && (
              <span className="chip px-2.5 py-1 text-xs">
                <IconFolder size={14} />
                {defaultProject?.name ?? t('task.default_project')}
              </span>
            )}
            {defaults?.area_id && (
              <span className="chip px-2.5 py-1 text-xs">
                <IconLayers size={14} />
                {defaultArea?.name ?? t('task.default_area')}
              </span>
            )}
            {defaults?.due_date && (
              <span className="chip px-2.5 py-1 text-xs">{relativeDay(defaults.due_date, today)}</span>
            )}
            {defaults?.parent_id && (
              <span className="chip px-2.5 py-1 text-xs">
                <IconSubtasks size={14} />
                {t('task.default_subtask')}
              </span>
            )}
          </div>
        )}

        <HighlightInput
          ref={inputRef}
          value={text}
          onChange={setText}
          onSubmit={submit}
          segments={segments}
          priority={parsed?.priority}
          placeholder={t('plan.quick_add_placeholder')}
          label={t('task.quick_add_label')}
          describedBy={hintId}
          testId="quick-add-input"
        />

        <div className="min-h-[2rem] space-y-2" aria-live="polite">
          {title && (
            <p className="text-sm" data-testid="quick-add-title">
              <span className="muted">{t('task.qa_title')} </span>
              <span className="font-medium">{title}</span>
            </p>
          )}
          <ParseChips chips={chips} />
          {status === 'offline' && text.trim() && (
            <p className="flex items-center gap-1.5 text-xs muted">
              <IconWifiOff size={14} />
              {t('task.qa_offline')}
            </p>
          )}
          {status === 'error' && text.trim() && <p className="text-xs muted">{t('task.qa_preview_failed')}</p>}
        </div>

        <Button block disabled={!text.trim()} onClick={submit} data-testid="quick-add-submit">
          {t('app.add')}
        </Button>

        {added.length > 0 ? (
          <div className="space-y-1" data-testid="quick-add-added">
            <p className="label px-1">{t('task.qa_added')}</p>
            <ul className="card divide-y divide-[rgb(var(--border))] overflow-hidden">
              {added.map((item) => (
                <li key={item.id} className="flex min-h-tap items-center gap-3 pl-4 text-sm">
                  <IconCheck size={16} className="shrink-0 text-[rgb(var(--accent))]" />
                  <span className="min-w-0 flex-1 truncate">{item.title}</span>
                  {item.queued ? (
                    <span className="shrink-0 pr-4 text-xs muted">{t('task.qa_waiting')}</span>
                  ) : (
                    <button
                      type="button"
                      className="min-h-tap shrink-0 px-4 text-sm font-medium text-[rgb(var(--accent))]"
                      onClick={() => openTask(item.id)}
                    >
                      {t('task.open')}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div id={hintId} className="space-y-1.5">
            <p className="text-xs muted">{t('task.qa_hint')}</p>
            <ul className="space-y-1">
              {EXAMPLES.map((key) => (
                <li key={key}>
                  <button
                    type="button"
                    className="flex min-h-tap w-full items-center rounded-xl px-3 text-left text-sm surface-sunken"
                    onClick={() => {
                      setText(t(key))
                      inputRef.current?.focus()
                    }}
                  >
                    <span className="truncate font-mono text-[13px]">{t(key)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Sheet>
  )
}
