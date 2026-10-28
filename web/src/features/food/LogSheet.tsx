/**
 * The log sheet: grams in, meal out.
 *
 * One screen for foods and recipes — the macros preview updates live so the
 * user sees exactly what will land in the day dashboard before saving.
 * Saving is explicit: nothing is written until the button is pressed.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, SegmentedControl, Sheet } from '@/components/ui'
import { useToast } from '@/components/ui/Toast'
import type { Food, MealType, Recipe } from '@/api/types'
import { number } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { guessMealType } from './mealType'
import { macrosFor, useLogMeal } from './useNutrition'
import { VerifyNote } from './VerifyFood'
import { guessMealType } from './mealType'

export type LogTarget = { kind: 'food'; food: Food } | { kind: 'recipe'; recipe: Recipe }

const MEAL_TYPES: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack', 'other']

export function LogSheet({
  target,
  onClose,
  defaultMealType,
  source,
}: {
  target: LogTarget | null
  onClose: () => void
  defaultMealType?: MealType
  /** how the food was found (stage 3: a scanned barcode) */
  source?: 'barcode'
}) {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const logMeal = useLogMeal()

  const [grams, setGrams] = useState(100)
  const [mealType, setMealType] = useState<MealType>(defaultMealType ?? guessMealType())

  // a recipe opens at one portion of the cooked dish (what "1 portion" means)
  const defaultGrams =
    target?.kind === 'food'
      ? (target.food.favorite_grams ?? target.food.default_serving_g ?? 100)
      : target?.kind === 'recipe'
        ? target.recipe.portion_size_g || 100
        : 100

  useEffect(() => {
    if (target) {
      setGrams(Math.round(defaultGrams))
      setMealType(defaultMealType ?? guessMealType())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target])

  const name = useMemo(() => {
    if (!target) return ''
    if (target.kind === 'food') {
      return i18n.language === 'en'
        ? (target.food.name_en ?? target.food.name_ru ?? '')
        : (target.food.name_ru ?? target.food.name_en ?? '')
    }
    return target.recipe.name
  }, [target, i18n.language])

  const macros = useMemo(() => (target ? macrosFor(target, grams || 0) : null), [target, grams])

  const save = () => {
    if (!target || !grams) return
    const item =
      target.kind === 'food'
        ? { food_id: target.food.id, grams, match_method: source === 'barcode' ? ('barcode' as const) : ('manual' as const) }
        : { recipe_id: target.recipe.id, grams, match_method: 'manual' as const }
    // what this sheet shows is what the day shows while the write waits offline
    const shown = macros ?? { kcal: 0, protein: 0, fat: 0, carbs: 0, fiber: 0 }
    const preview = {
      name,
      kcal: shown.kcal,
      protein_g: shown.protein,
      fat_g: shown.fat,
      carbs_g: shown.carbs,
      fiber_g: shown.fiber,
      items: [{ name, grams, kcal: shown.kcal, protein_g: shown.protein, fat_g: shown.fat, carbs_g: shown.carbs, fiber_g: shown.fiber }],
    }
    logMeal.mutate(
      {
        id: crypto.randomUUID(),
        meal_type: mealType,
        source: target.kind === 'food' ? (source ?? 'search') : 'recipe',
        items: [item],
        preview,
      },
      {
        onSuccess: (result) => {
          if (result.queued) toast.info(t('sync.meal_queued', { name, kcal: number(shown.kcal) }))
          else toast.success(t('food.logged_toast', { name, kcal: number(shown.kcal) }))
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <Sheet open={!!target} onClose={onClose} title={name || t('food.add_meal_title')}>
      <div className="space-y-5 pb-2">
        <Field label={t('food.grams')} htmlFor="log-grams">
          <Input
            id="log-grams"
            type="number"
            inputMode="decimal"
            min={1}
            value={grams || ''}
            onChange={(event) => setGrams(Number(event.target.value))}
            className="text-center font-stat text-xl"
          />
        </Field>

        {target && (
          <div className="flex flex-wrap gap-2">
            {[0.5, 1, 1.5, 2].map((factor) => (
              <button
                key={factor}
                type="button"
                onClick={() => setGrams(Math.round(defaultGrams * factor))}
                className={grams === Math.round(defaultGrams * factor) ? 'chip chip-active' : 'chip'}
              >
                {target.kind === 'recipe'
                  ? t('food.portions_chip', { n: number(factor, factor % 1 ? 1 : 0), grams: number(Math.round(defaultGrams * factor)) })
                  : `${number(Math.round(defaultGrams * factor))} ${t('common.g')}`}
              </button>
            ))}
          </div>
        )}

        {target?.kind === 'food' && <VerifyNote key={target.food.id} food={target.food} />}

        <Field label={t('food.meal_type')}>
          <SegmentedControl
            value={mealType}
            onChange={(value) => setMealType(value)}
            options={MEAL_TYPES.map((value) => ({ value, label: t(`meal.${value}`) }))}
          />
        </Field>

        {macros && (
          <div className="surface-sunken grid grid-cols-5 gap-2 rounded-2xl px-3 py-3 text-center">
            <Preview label={t('today.kcal')} value={number(macros.kcal)} accent />
            <Preview label={t('today.protein')} value={number(macros.protein)} />
            <Preview label={t('today.fat')} value={number(macros.fat)} />
            <Preview label={t('today.carbs')} value={number(macros.carbs)} />
            <Preview label={t('today.fiber')} value={number(macros.fiber)} />
          </div>
        )}

        <Button block loading={logMeal.isPending} onClick={save} disabled={!grams}>
          {t('food.save_meal')}
        </Button>
      </div>
    </Sheet>
  )
}

function Preview({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <p className={`stat-value text-sm ${accent ? 'text-aqua-300' : ''}`}>{value}</p>
      <p className="text-[10px] muted">{label}</p>
    </div>
  )
}
