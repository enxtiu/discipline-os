/**
 * Recipe builder: name, ingredients picked through the same ranked search,
 * grams per ingredient, the cooked weight (yield) and the portion — either
 * "split into N portions" or "one portion weighs X g". Totals, per-100 g of the
 * cooked dish and per-portion macros are computed by the server on save; the
 * sheet previews the same math locally while editing.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, SegmentedControl, Sheet, Toggle } from '@/components/ui'
import { IconClose, IconPlus, IconSearch } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { Food, RecipeItemIn } from '@/api/types'
import { number } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { useCreateRecipe, useFoodSearch } from './useNutrition'

interface DraftItem extends RecipeItemIn {
  key: string
  name: string
  kcalPer100: number
}

type SplitMode = 'servings' | 'grams'

export function RecipeSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const createRecipe = useCreateRecipe()

  const [name, setName] = useState('')
  const [split, setSplit] = useState<SplitMode>('servings')
  const [servings, setServings] = useState(1)
  const [portionGrams, setPortionGrams] = useState(0)
  const [cookedWeight, setCookedWeight] = useState(0)
  const [pinned, setPinned] = useState(false)
  const [items, setItems] = useState<DraftItem[]>([])
  const [pickerQuery, setPickerQuery] = useState('')
  const [picking, setPicking] = useState(false)

  const search = useFoodSearch(pickerQuery, picking && pickerQuery.trim().length >= 2)

  // the same arithmetic the server does (services/recipes.py): per 100 g of the
  // *cooked* dish, one portion = cooked weight / servings or the typed grams
  const preview = useMemo(() => {
    const kcal = items.reduce((acc, item) => acc + (item.kcalPer100 * item.grams) / 100, 0)
    const raw = items.reduce((acc, item) => acc + item.grams, 0)
    const cooked = cookedWeight > 0 ? cookedWeight : raw
    const portion = split === 'grams' && portionGrams > 0 ? portionGrams : servings > 0 ? cooked / servings : cooked
    return {
      kcal: Math.round(kcal),
      raw,
      cooked,
      per100: cooked ? Math.round((kcal / cooked) * 100) : 0,
      portion: Math.round(portion),
      perPortion: cooked ? Math.round((kcal * portion) / cooked) : 0,
    }
  }, [items, cookedWeight, split, portionGrams, servings])

  const addIngredient = (food: Food) => {
    setItems((current) => [
      ...current,
      {
        key: crypto.randomUUID(),
        food_id: food.id,
        grams: Math.round(food.default_serving_g ?? 100),
        name: i18n.language === 'en' ? (food.name_en ?? food.name_ru ?? '') : (food.name_ru ?? food.name_en ?? ''),
        kcalPer100: food.kcal_per_100g,
      },
    ])
    setPicking(false)
    setPickerQuery('')
  }

  const reset = () => {
    setName('')
    setItems([])
    setServings(1)
    setPortionGrams(0)
    setCookedWeight(0)
    setSplit('servings')
    setPinned(false)
  }

  const valid =
    name.trim() &&
    items.length > 0 &&
    items.every((item) => item.grams > 0) &&
    // in "portion weight" mode the weight must be typed: the server would otherwise
    // treat the whole dish as one portion while the preview shows something else
    (split === 'servings' ? servings > 0 : portionGrams >= 1)

  const save = () => {
    if (!valid) return
    createRecipe.mutate(
      {
        id: crypto.randomUUID(),
        name: name.trim(),
        servings: split === 'servings' && servings > 0 ? servings : 1,
        portion_size_g: split === 'grams' && portionGrams > 0 ? portionGrams : undefined,
        cooked_weight_g: cookedWeight > 0 ? cookedWeight : undefined,
        is_pinned: pinned,
        items: items.map(({ food_id, grams, note }) => ({ food_id, grams, note })),
      },
      {
        onSuccess: (recipe) => {
          toast.success(t('food.recipe_saved', { name: recipe.name, kcal: number(recipe.per_serving.kcal) }))
          reset()
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

        <div className="space-y-2">
          <p className="label">{t('food.ingredients')}</p>
          {items.length === 0 && <p className="text-sm muted">{t('food.ingredients_empty')}</p>}
          {items.map((item) => (
            <div key={item.key} className="surface-sunken flex items-center gap-3 rounded-2xl px-3 py-2">
              <span className="min-w-0 flex-1 truncate text-sm">{item.name}</span>
              <input
                type="number"
                inputMode="decimal"
                min={1}
                value={item.grams || ''}
                onChange={(event) =>
                  setItems((current) =>
                    current.map((row) => (row.key === item.key ? { ...row, grams: Number(event.target.value) } : row)),
                  )
                }
                className="input w-20 px-2 text-center text-sm"
                aria-label={t('food.grams')}
              />
              <span className="text-xs muted">{t('common.g')}</span>
              <button
                type="button"
                className="flex h-tap w-8 shrink-0 items-center justify-center muted"
                onClick={() => setItems((current) => current.filter((row) => row.key !== item.key))}
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
                  aria-label={t('food.search')}
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
                    className="flex min-h-tap w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-sm hover:bg-[rgb(var(--bg-elevated))]"
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

        <Field
          label={t('food.cooked_weight')}
          htmlFor="recipe-cooked"
          hint={t('food.cooked_weight_hint', { grams: number(preview.raw) })}
        >
          <Input
            id="recipe-cooked"
            type="number"
            inputMode="decimal"
            min={0}
            value={cookedWeight || ''}
            placeholder={number(preview.raw)}
            onChange={(event) => setCookedWeight(Number(event.target.value))}
            className="tnum"
          />
        </Field>

        <Field label={t('food.split_by')}>
          <SegmentedControl
            value={split}
            onChange={(value) => setSplit(value)}
            options={[
              { value: 'servings', label: t('food.split_servings') },
              { value: 'grams', label: t('food.split_grams') },
            ]}
          />
        </Field>
        {split === 'servings' ? (
          <Field label={t('food.servings')} htmlFor="recipe-servings">
            <Input
              id="recipe-servings"
              type="number"
              inputMode="decimal"
              min={1}
              value={servings || ''}
              onChange={(event) => setServings(Number(event.target.value))}
              className="tnum w-28 text-center"
            />
          </Field>
        ) : (
          <Field label={t('food.portion_grams')} htmlFor="recipe-portion">
            <Input
              id="recipe-portion"
              type="number"
              inputMode="decimal"
              min={1}
              value={portionGrams || ''}
              placeholder={number(preview.portion)}
              onChange={(event) => setPortionGrams(Number(event.target.value))}
              className="tnum w-28 text-center"
            />
          </Field>
        )}

        <div className="surface-sunken space-y-1 rounded-2xl px-4 py-3 text-sm">
          <p className="tnum">
            {t('food.per_portion', { kcal: number(preview.perPortion), grams: number(preview.portion) })}
          </p>
          <p className="tnum text-xs muted">
            {t('food.recipe_total')}: {number(preview.kcal)} {t('common.kcal')} · {number(preview.cooked)} {t('common.g')}
            {' · '}
            {t('food.per_100g_cooked', { kcal: number(preview.per100) })}
          </p>
        </div>

        <div className="flex min-h-tap items-center justify-between gap-3">
          <span className="text-sm">{t('food.pin')}</span>
          <Toggle checked={pinned} onChange={setPinned} label={t('food.pin')} />
        </div>

        <Button block loading={createRecipe.isPending} onClick={save} disabled={!valid}>
          {t('food.recipe_save')}
        </Button>
      </div>
    </Sheet>
  )
}
