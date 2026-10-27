/**
 * "Add a task here" at the end of a project's or an area's list: the same
 * natural-language line as the quick-add sheet (live highlights and chips),
 * submitted with the container as the default (`defaults.project_id` /
 * `area_id`); explicit tokens in the line still win.
 */
import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { IconButton } from '@/components/ui'
import { IconPlus } from '@/components/ui/icons'
import { errorMessage } from '@/lib/errors'
import { openTask, type QuickAddRequest } from '../events'
import { newId, useCounts, useQuickAdd } from '../queries'
import { HighlightInput } from './HighlightInput'
import { highlightSegments, useParsePreview } from './parse'
import { ParseChips, parseChips } from './ParseChips'
import { planToast } from './toast'

export function InlineQuickAdd({
  defaults,
  placeholder,
  testId = 'inline-add',
}: {
  defaults: NonNullable<QuickAddRequest['defaults']>
  placeholder: string
  testId?: string
}) {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState('')
  const today = useCounts().data?.date
  const quick = useQuickAdd()
  const { preview } = useParsePreview(text, text.trim().length > 0)
  const segments = useMemo(() => highlightSegments(text, preview), [text, preview])
  const chips = preview && text.trim() ? parseChips(preview.result, today) : []

  const submit = () => {
    const value = text.trim()
    if (!value) return
    setText('')
    quick.mutate(
      { id: newId(), text: value, captured_at: new Date().toISOString(), defaults },
      {
        onSuccess: (result) => {
          if (result.queued) planToast.info(t('task.queued'), { placement: 'top' })
          else {
            const task = result.data.task
            planToast.success(t('task.added', { title: task.title }), {
              action: { label: t('task.open'), onClick: () => openTask(task.id) },
              placement: 'top',
            })
          }
        },
        onError: (error) => {
          planToast.error(errorMessage(error), { placement: 'top' })
          setText((current) => current || value)
        },
      },
    )
    inputRef.current?.focus()
  }

  return (
    <div className="space-y-2" data-testid={testId}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <HighlightInput
            ref={inputRef}
            value={text}
            onChange={setText}
            onSubmit={submit}
            segments={segments}
            priority={preview?.result.priority}
            placeholder={placeholder}
            label={placeholder}
            testId={`${testId}-input`}
          />
        </div>
        <IconButton
          label={t('app.add')}
          icon={<IconPlus size={20} />}
          variant="secondary"
          disabled={!text.trim()}
          onClick={submit}
          data-testid={`${testId}-submit`}
        />
      </div>
      <ParseChips chips={chips} />
    </div>
  )
}
