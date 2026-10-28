/**
 * Today — the forms a theme may choose instead of the rings (`SkinTraits`,
 * lib/appearance.ts): the calorie sticker (the number, a bar, «Осталось N ккал»,
 * the peel corner) and the macros as labelled bars. The same numbers and the same
 * names for screen readers as the rings they replace; «Эволюция» never renders them.
 *
 * The `sk-*` classes and `data-cat` are styled by the theme (styles/skins/*.css).
 */
import { useTranslation } from 'react-i18next'

import { ProgressBar, Skeleton } from '@/components/ui'
import type { Category } from '@/components/ui/ProgressRing'
import { number } from '@/lib/format'

export function CalorieSticker({ eaten, target, loading }: { eaten: number; target: number | null; loading: boolean }) {
  const { t } = useTranslation()
  if (loading) return <Skeleton className="h-40 rounded-card" />
  const left = target != null ? Math.max(0, Math.round(target - eaten)) : null
  const kcal = t('common.kcal')
  return (
    <section
      className="card sk-tile sk-hero relative px-[18px] pb-[22px] pt-4"
      data-cat="kcal"
      aria-label={t('today.kcal')}
      data-testid="calorie-sticker"
    >
      <p className="text-[15px] font-bold leading-tight">{t('today.eaten')}</p>
      <p className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
        <span className="tnum text-[3.5rem] font-extrabold leading-[1.1] tracking-tight" data-testid="calorie-sticker-value">
          {number(eaten)}
        </span>
        <span className="sk-soft text-base font-bold">
          {target != null ? t('today.of_kcal', { kcal: number(target) }) : t('today.no_target')}
        </span>
      </p>
      <ProgressBar
        value={eaten}
        max={target ?? 1}
        tone="kcal"
        className="sk-bar-hero mt-3 h-6"
        label={t('today.kcal')}
        valueText={
          target != null
            ? t('a11y.value_of', { value: number(eaten), target: number(target), unit: kcal })
            : t('a11y.value_unit', { value: number(eaten), unit: kcal })
        }
      />
      {left != null && (
        <span className="sk-badge sk-label sk-overhang sk-tilt-r tnum" data-testid="calorie-sticker-left">
          {t('today.left_kcal', { kcal: number(left) })}
        </span>
      )}
      {/* the peel: the corner of the sticker lifting off the page */}
      <svg aria-hidden="true" width="34" height="34" viewBox="0 0 34 34" className="sk-peel">
        <path d="M8 34L34 8V34Z" className="sk-peel-hole" />
        <path d="M6 28L28 6H32V10L10 32H6Z" className="sk-peel-shade" />
        <path d="M6 28L28 6H6Z" className="sk-peel-flap" />
      </svg>
    </section>
  )
}

export interface MacroBar {
  key: Category
  title: string
  value: number
  max: number
}

/** Four macros as labelled bars (2 × 2), each one progressbar «Белки, 95 из 160 г». */
export function MacroBars({ items, unit }: { items: MacroBar[]; unit: string }) {
  const { t } = useTranslation()
  return (
    <div className="sk-macros grid grid-cols-2 gap-x-4 gap-y-3.5" data-testid="macro-bars">
      {items.map((item) => (
        <div key={item.key} className="min-w-0 space-y-1.5" data-cat-bar={item.key}>
          <p className="flex items-baseline justify-between gap-2 text-sm" aria-hidden="true">
            <span className="truncate font-bold">{item.title}</span>
            <span className="tnum shrink-0 font-semibold muted">
              {number(item.value)}/{number(item.max)} {unit}
            </span>
          </p>
          <ProgressBar
            value={item.value}
            max={item.max || 1}
            tone={item.key}
            className="sk-bar-macro h-3.5"
            label={item.title}
            valueText={t('a11y.value_of', { value: number(item.value), target: number(item.max), unit })}
          />
        </div>
      ))}
    </div>
  )
}
