/**
 * «Повторить» on Сегодня (stage 10, the unified «Сегодня», every theme): the three foods
 * (or recipes) logged most recently — `GET /foods/recents`, the list «Недавние» on Питание
 * shows — each one tap to log it again: the same food, the grams it was last logged with,
 * the meal type the log sheet would pick. The write is the log sheet's (`useLogMeal`):
 * through the offline queue with a client UUID, so a replay never duplicates it, shown in
 * the day at once while it waits; the toast offers «Отменить». Hidden until something
 * has been logged. `sk-repeat*` are hooks for the themes.
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { IconPlus } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { MealItemIn, Recent } from '@/api/types'
import { errorMessage } from '@/lib/errors'
import { number } from '@/lib/format'
import { guessMealType } from '@/features/food/mealType'
import { useDeleteMeal, useLogMeal, useRecents, type MealPreview } from '@/features/food/useNutrition'
import { planToast } from '@/features/plan/tasks/toast'

const SHOWN = 3

interface Portion {
  key: string
  name: string
  item: MealItemIn
  /** what the day shows while the write waits offline (as the log sheet's preview) */
  preview: MealPreview
}

const round2 = (value: number) => Math.round(value * 100) / 100

/** A recent food or recipe as one portion: the grams it was last logged with. */
function portionOf(recent: Recent, language: string): Portion | null {
  const food = recent.food
  const recipe = recent.recipe
  const source = food ?? recipe
  if (!source) return null
  const name = food
    ? language === 'en'
      ? (food.name_en ?? food.name_ru ?? '')
      : (food.name_ru ?? food.name_en ?? '')
    : (recipe?.name ?? '')
  const fallback = food ? (food.favorite_grams ?? food.default_serving_g ?? 100) : recipe?.portion_size_g || 100
  const grams = Math.round(recent.last_grams || fallback)
  if (!name || grams <= 0) return null
  const factor = grams / 100
  const macros = {
    kcal: round2(source.kcal_per_100g * factor),
    protein_g: round2(source.protein_per_100g * factor),
    fat_g: round2(source.fat_per_100g * factor),
    carbs_g: round2(source.carbs_per_100g * factor),
    fiber_g: round2(source.fiber_per_100g * factor),
  }
  return {
    key: recent.id,
    name,
    item: food
      ? { food_id: food.id, grams, match_method: 'manual' }
      : { recipe_id: (recipe as NonNullable<typeof recipe>).id, grams, match_method: 'manual' },
    preview: { name, ...macros, items: [{ name, grams, ...macros }] },
  }
}

export function RepeatFoods() {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const titleId = useId()
  const recents = useRecents()
  const logMeal = useLogMeal()
  const deleteMeal = useDeleteMeal()
  const [busy, setBusy] = useState<string | null>(null)

  const portions = (recents.data?.items ?? [])
    .map((recent) => portionOf(recent, i18n.language))
    .filter((portion): portion is Portion => portion !== null)
    .slice(0, SHOWN)
  if (!portions.length) return null

  const relog = (portion: Portion) => {
    if (busy) return // one write at a time: a double tap logs once
    const id = crypto.randomUUID()
    const name = portion.name
    const kcal = number(Math.round(portion.preview.kcal))
    setBusy(portion.key)
    logMeal.mutate(
      { id, meal_type: guessMealType(), source: 'recent', items: [portion.item], preview: portion.preview },
      {
        onSuccess: (result) => {
          const undo = {
            label: t('task.undo'),
            onClick: () =>
              deleteMeal.mutate(id, {
                onSuccess: (removed) => {
                  if (removed.withdrawn) toast.info(t('sync.meal_discarded'))
                  else if (removed.queued) toast.info(t('sync.queued'))
                  else toast.info(t('today.repeat_undone', { name }))
                },
                onError: (error) => toast.error(errorMessage(error)),
              }),
          }
          if (result.queued) planToast.info(t('sync.meal_queued', { name, kcal }), undo)
          else planToast.success(t('food.logged_toast', { name, kcal }), undo)
        },
        onError: (error) => toast.error(errorMessage(error)),
        onSettled: () => setBusy(null),
      },
    )
  }

  return (
    <section className="space-y-2" aria-labelledby={titleId} data-testid="repeat-foods">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h2 id={titleId} className="h2">
          {t('today.repeat_title')}
        </h2>
        <span className="text-[13px] muted">{t('today.repeat_hint')}</span>
      </div>
      <div role="group" aria-label={t('today.repeat_group')} className="grid grid-cols-3 gap-2.5">
        {portions.map((portion) => {
          const kcal = number(Math.round(portion.preview.kcal))
          return (
            <button
              key={portion.key}
              type="button"
              className={clsx(
                'sk-repeat card flex min-h-[4.25rem] min-w-0 flex-col justify-between gap-1 px-2.5 py-2 text-left transition-transform active:scale-[.97]',
                busy === portion.key && 'opacity-60',
              )}
              aria-label={t('today.repeat_aria', { name: portion.name, kcal })}
              aria-busy={busy === portion.key || undefined}
              onClick={() => relog(portion)}
              data-testid="repeat-food"
            >
              <span className="line-clamp-2 text-[13px] font-semibold leading-tight">{portion.name}</span>
              {/* a real space: the visible text reads «Овсянка 290 ккал», as the name says it (label in name) */}{' '}
              <span className="tnum flex items-center justify-between gap-1 text-xs font-medium muted">
                <span className="truncate">
                  {kcal} {t('common.kcal')}
                </span>
                <span
                  aria-hidden="true"
                  className="sk-repeat-plus flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[rgb(var(--accent))]/15 text-[rgb(var(--accent))]"
                >
                  <IconPlus size={12} strokeWidth={2.6} />
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}
