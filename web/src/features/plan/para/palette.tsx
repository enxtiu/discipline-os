/**
 * Colours for projects, areas and tags — a small palette taken from the
 * existing design tokens only (`tailwind.config.js` / `styles/index.css`):
 * aqua-400 (accent), the sleep blue, violet-400, rose-400, amber-400 and
 * mist-400. The API stores `#RRGGBB`; colours from data are applied inline.
 */
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

export const PALETTE = [
  { key: 'aqua', hex: '#38E0C8' },
  { key: 'blue', hex: '#60A5FA' },
  { key: 'violet', hex: '#A78BFA' },
  { key: 'rose', hex: '#FB7185' },
  { key: 'amber', hex: '#FBBF24' },
  { key: 'gray', hex: '#8492AB' },
] as const

export function ColorPicker({ value, onChange }: { value: string | null; onChange: (next: string | null) => void }) {
  const { t } = useTranslation()
  const selected = value?.toLowerCase() ?? null
  const ring = 'ring-2 ring-[rgb(var(--accent))] ring-offset-2 ring-offset-[rgb(var(--bg-elevated))]'
  return (
    <div role="radiogroup" aria-label={t('plan.color_label')} className="flex flex-wrap gap-0.5" data-testid="color-picker">
      <button
        type="button"
        role="radio"
        aria-checked={selected === null}
        aria-label={t('plan.color_none')}
        onClick={() => onChange(null)}
        className="flex h-12 w-12 items-center justify-center rounded-2xl"
      >
        <span
          className={clsx(
            'h-7 w-7 rounded-full border-2 border-dashed border-[rgb(var(--text-muted))]/60',
            selected === null && ring,
          )}
        />
      </button>
      {PALETTE.map((color) => (
        <button
          key={color.key}
          type="button"
          role="radio"
          aria-checked={selected === color.hex.toLowerCase()}
          aria-label={t(`plan.color.${color.key}`)}
          onClick={() => onChange(color.hex)}
          className="flex h-12 w-12 items-center justify-center rounded-2xl"
          data-testid={`color-${color.key}`}
        >
          <span
            className={clsx('h-7 w-7 rounded-full', selected === color.hex.toLowerCase() && ring)}
            style={{ backgroundColor: color.hex }}
          />
        </button>
      ))}
    </div>
  )
}
