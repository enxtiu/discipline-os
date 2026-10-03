/**
 * Recipe builder: name, servings, ingredients picked through the same ranked
 * search, grams per ingredient. Totals and per-100 g macros are computed by
 * the server on save; the sheet previews them locally while editing.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, Sheet } from '@/components/ui'
import { IconClose, IconPlus, IconSearch } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { Food, RecipeItemIn } from '@/api/types'
import { number } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { useCreateRecipe, useFoodSearch } from './useNutrition'

interface DraftItem extends RecipeItemIn {
  name: string
  kcalPer100: number
}

export function RecipeSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const createRecipe = useCreateRecipe()

  const [name, setName] = useState('')
  const [servings, setServings] = useState(1)
  const [items, setItems] = useState<DraftItem[]>([])
  const [pickerQuery, setPickerQuery] = useState('')
  const [picking, setPicking] = useState(false)

  const search = useFoodSearch(pickerQuery, picking && pickerQuery.trim().length >= 2)

  const totals = useMemo(() => {
    const sum = items.reduce(
      (acc, item) => {
        const factor = item.grams / 100
        acc.kcal += item.kcalPer100 * factor
        return acc
      },
      { kcal: 0 },
    )
    const weight = items.reduce((acc, item) => acc + item.grams, 0)
    return { kcal: Math.round(sum.kcal), weight, per100: weight ? Math.round((sum.kcal / weight) * 100) : 0 }
  }, [items])

  const addIngredient = (food: Food) => {
    setItems((current) => [
      ...current,
      {
        food_id: food.id,
        grams: Math.round(food.default_serving_g ?? 100),
        name: i18n.language === 'en' ? (food.name_en ?? food.name_ru ?? '') : (food.name_ru ?? food.name_en ?? ''),
        kcalPer100: food.kcal_per_100g,
      },
    ])
    setPicking(false)
    setPickerQuery('')
  }

  const save = () => {
    if (!name.trim() || items.length === 0) return
    createRecipe.mutate(
      {
        name: name.trim(),
        servings,
        items: items.map(({ food_id, grams, note }) => ({ food_id, grams, note })),
      },
      {
        onSuccess: (recipe) => {
          toast.success(t('food.recipe_saved', { name: recipe.name, kcal: number(recipe.per_serving.kcal) }))
          setName('')
          setItems([])
          setServings(1)
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('food.recipe_new')}>
      <div className="space-y-5 pb-2">
        <Field label={t('food.recipe_name')} htmlFor="recipe-name">
          <Input
            id="recipe-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t('food.recipe_name_placeholder')}
          />
        </Field>

        <Field label={t('food.servings')} htmlFor="recipe-servings">
          <Input
            id="recipe-servings"
            type="number"
            inputMode="decimal"
            min={1}
            value={servings || ''}
            onChange={(event) => setServings(Number(event.target.value))}
            className="w-24 text-center"
          />
        </Field>

        <div className="space-y-2">
          <p className="label">{t('food.ingredients')}</p>
          {items.length === 0 && <p className="text-sm muted">{t('food.ingredients_empty')}</p>}
          {items.map((item, index) => (
            <div key={item.food_id} className="surface-sunken flex items-center gap-3 rounded-2xl px-3 py-2">
              <span className="min-w-0 flex-1 truncate text-sm">{item.name}</span>
              <input
                type="number"
                inputMode="decimal"
                min={1}
                value={item.grams}
                onChange={(event) =>
                  setItems((current) =>
                    current.map((row, i) => (i === index ? { ...row, grams: Number(event.target.value) } : row)),
                  )
                }
                className="input w-20 text-center text-sm"
                aria-label={t('food.grams')}
              />
              <span className="text-xs muted">{t('common.g')}</span>
              <button
                type="button"
                className="muted"
                onClick={() => setItems((current) => current.filter((_, i) => i !== index))}
                aria-label={t('app.delete')}
              >
                <IconClose size={16} />
              </button>
            </div>
          ))}

          {!picking ? (
            <Button variant="secondary" size="sm" icon={<IconPlus size={16} />} onClick={() => setPicking(true)}>
              {t('food.add_ingredient')}
            </Button>
          ) : (
            <div className="space-y-2">
              <div className="relative">
                <IconSearch size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 muted" />
                <Input
                  value={pickerQuery}
                  onChange={(event) => setPickerQuery(event.target.value)}
                  placeholder={t('food.search_placeholder')}
                  className="pl-9"
                  autoFocus
                />
              </div>
              <div className="max-h-56 space-y-1 overflow-y-auto">
                {(search.data?.items ?? []).map((match) => (
                  <button
                    key={match.id}
                    type="button"
                    onClick={() => addIngredient(match)}
                    className="flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-sm hover:bg-[rgb(var(--bg-elevated))]"
                  >
                    <span className="truncate">{i18n.language === 'en' ? (match.name_en ?? match.name_ru) : (match.name_ru ?? match.name_en)}</span>
                    <span className="tnum shrink-0 text-xs muted">{number(match.kcal_per_100g)} {t('common.kcal')}</span>
                  </button>
                ))}
                {pickerQuery.trim().length >= 2 && search.isSuccess && search.data.items.length === 0 && (
                  <p className="px-3 py-2 text-sm muted">{t('food.results_empty')}</p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="surface-sunken flex items-center justify-between rounded-2xl px-4 py-3 text-sm">
          <span className="muted">
            {t('food.recipe_total')}: <span className="tnum text-[rgb(var(--text))]">{number(totals.kcal)} {t('common.kcal')}</span>
            {' · '}{number(totals.weight)} {t('common.g')}
          </span>
          <span className="tnum">
            {servings ? number(Math.round(totals.kcal / servings)) : 0} {t('common.kcal')}/{t('food.serving_short')}
          </span>
        </div>

        <Button block loading={createRecipe.isPending} onClick={save} disabled={!name.trim() || items.length === 0}>
          {t('food.recipe_save')}
        </Button>
      </div>
    </Sheet>
  )
}
