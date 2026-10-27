/**
 * Tags as chips: type a name and press Enter (or a comma/space), or tap a
 * suggestion; tap a chip to remove it. Names follow the server's rule —
 * letters, digits, "_" and "-" (any alphabet), 1–64 characters, ≤ 10 tags.
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { IconButton, Input } from '@/components/ui'
import { IconClose, IconPlus } from '@/components/ui/icons'
import { Chip } from './ui'

const TAG_RE = /^[\p{L}\p{N}_-]{1,64}$/u

export function TagEditor({
  tags,
  onChange,
  suggestions,
  max = 10,
  testId = 'tag-input',
}: {
  tags: string[]
  onChange: (next: string[]) => void
  suggestions: string[]
  max?: number
  testId?: string
}) {
  const { t } = useTranslation()
  const inputId = useId()
  const [input, setInput] = useState('')
  const [invalid, setInvalid] = useState(false)
  const has = (name: string) => tags.some((tag) => tag.toLowerCase() === name.toLowerCase())
  const full = tags.length >= max

  const add = (raw: string) => {
    const name = raw.trim().replace(/^#+/, '')
    if (!name) return
    if (!TAG_RE.test(name)) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    setInput('')
    if (has(name) || full) return
    onChange([...tags, name])
  }

  const needle = input.trim().replace(/^#+/, '').toLowerCase()
  const matches = suggestions.filter((name) => !has(name) && (!needle || name.toLowerCase().startsWith(needle))).slice(0, 8)

  return (
    <div className="space-y-2">
      {tags.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {tags.map((tag) => (
            <li key={tag.toLowerCase()}>
              <button
                type="button"
                className="chip chip-active min-h-tap"
                aria-label={t('task.tag_remove', { tag })}
                onClick={() => onChange(tags.filter((value) => value !== tag))}
                data-testid="tag-chip"
              >
                #{tag}
                <IconClose size={14} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <label htmlFor={inputId} className="sr-only">
          {t('task.tag_placeholder')}
        </label>
        <Input
          id={inputId}
          value={input}
          disabled={full}
          placeholder={t('task.tag_placeholder')}
          autoCapitalize="none"
          autoCorrect="off"
          enterKeyHint="done"
          onChange={(event) => {
            const value = event.target.value
            if (/[,\s]$/.test(value)) add(value.slice(0, -1))
            else {
              setInput(value)
              setInvalid(false)
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              add(input)
            } else if (event.key === 'Backspace' && !input && tags.length > 0) {
              onChange(tags.slice(0, -1))
            }
          }}
          data-testid={testId}
        />
        {input.trim() && (
          <IconButton
            label={t('task.tag_add')}
            icon={<IconPlus size={18} />}
            variant="secondary"
            onClick={() => add(input)}
          />
        )}
      </div>
      {matches.length > 0 && !full && (
        <div className="flex flex-wrap gap-2" aria-label={t('task.tag_suggestions')}>
          {matches.map((name) => (
            <Chip key={name} onClick={() => add(name)} data-testid="tag-suggestion">
              #{name}
            </Chip>
          ))}
        </div>
      )}
      {invalid && <p className="text-xs text-rose-400">{t('errors.invalid_tag')}</p>}
      {full && <p className="text-xs muted">{t('errors.too_many_tags')}</p>}
    </div>
  )
}
