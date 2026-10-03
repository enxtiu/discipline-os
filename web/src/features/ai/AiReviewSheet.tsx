/**
 * Review screen for AI suggestions (photo or text). NOTHING is saved until the
 * user presses the save button — the AI only proposes.
 *
 * Per item: the product it was matched to in the local library (or "AI
 * estimate" when nothing matched), the grams as an *estimate* with a slider and
 * a number field, the model's confidence, and actions to swap the product,
 * remove the item or — for AI-only items — save the AI numbers as "my food".
 * Items can be added from the regular search.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Badge, Button, Callout, Field, Input, SegmentedControl, Sheet } from '@/components/ui'
import { IconClose, IconPlus, IconRefresh, IconSearch, IconTrash } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { AiAnalysis, AiItem, Food, MatchMethod, MealItemIn, MealType } from '@/api/types'
import { number } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { useCreateFood, useFoodSearch, useLogMeal } from '@/features/food/useNutrition'

const MEAL_TYPES: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack', 'other']

interface Per100 {
  kcal: number
  protein: number
  fat: number
  carbs: number
  fiber: number
}

type Choice = { kind: 'food'; food: Food; method: MatchMethod } | { kind: 'ai' }

interface DraftItem {
  key: string
  ai: AiItem | null // null → added by hand from search
  grams: number
  gramsTouched: boolean
  choice: Choice
  alternatives: Food[]
}

function foodPer100(food: Food): Per100 {
  return {
    kcal: food.kcal_per_100g,
    protein: food.protein_per_100g,
    fat: food.fat_per_100g,
    carbs: food.carbs_per_100g,
    fiber: food.fiber_per_100g,
  }
}

function aiPer100(item: AiItem): Per100 {
  const p = item.ai_per_100g
  return {
    kcal: p.kcal_per_100g,
    protein: p.protein_per_100g,
    fat: p.fat_per_100g,
    carbs: p.carbs_per_100g,
    fiber: p.fiber_per_100g,
  }
}

function per100Of(item: DraftItem): Per100 {
  return item.choice.kind === 'food' ? foodPer100(item.choice.food) : aiPer100(item.ai as AiItem)
}

function toDraft(item: AiItem): DraftItem {
  return {
    key: item.key,
    ai: item,
    grams: Math.round(item.estimated_grams),
    gramsTouched: false,
    choice: item.match
      ? { kind: 'food', food: item.match.food, method: item.match.match_method as MatchMethod }
      : { kind: 'ai' },
    alternatives: item.alternatives ?? [],
  }
}

function guessMealType(): MealType {
  const hour = new Date().getHours()
  if (hour < 11) return 'breakfast'
  if (hour < 16) return 'lunch'
  if (hour < 22) return 'dinner'
  return 'snack'
}

export function AiReviewSheet({
  analysis,
  onClose,
  initialMealType,
}: {
  analysis: AiAnalysis | null
  onClose: () => void
  initialMealType?: MealType
}) {
  const { t, i18n } = useTranslation()
  const toast = useToast()
  const logMeal = useLogMeal()
  const [items, setItems] = useState<DraftItem[]>([])
  const [mealType, setMealType] = useState<MealType>(initialMealType ?? guessMealType())
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    if (analysis) {
      setItems(analysis.items.map(toDraft))
      setMealType(initialMealType ?? guessMealType())
      setAdding(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysis])

  const foodName = (food: Food) =>
    i18n.language === 'en' ? (food.name_en ?? food.name_ru ?? '') : (food.name_ru ?? food.name_en ?? '')

  const totals = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        const base = per100Of(item)
        const factor = (item.grams || 0) / 100
        acc.kcal += base.kcal * factor
        acc.protein += base.protein * factor
        acc.fat += base.fat * factor
        acc.carbs += base.carbs * factor
        return acc
      },
      { kcal: 0, protein: 0, fat: 0, carbs: 0 },
    )
  }, [items])

  const update = (key: string, patch: Partial<DraftItem>) =>
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...patch } : item)))

  const addFood = (food: Food) => {
    setItems((current) => [
      ...current,
      {
        key: `m${crypto.randomUUID()}`,
        ai: null,
        grams: Math.round(food.default_serving_g ?? 100),
        gramsTouched: true,
        choice: { kind: 'food', food, method: 'manual' },
        alternatives: [],
      },
    ])
    setAdding(false)
  }

  const save = () => {
    if (!analysis || items.length === 0) return
    const payload: MealItemIn[] = items.map((item) => {
      const edited =
        item.gramsTouched ||
        (item.ai !== null &&
          (item.choice.kind === 'food' ? item.choice.food.id !== item.ai.match?.food.id : item.ai.match !== null))
      const provenance = item.ai
        ? { confidence: item.ai.confidence, ai_name_raw: item.ai.name_ru, is_edited: edited }
        : { is_edited: true }
      if (item.choice.kind === 'food') {
        return { food_id: item.choice.food.id, grams: item.grams, match_method: item.choice.method, ...provenance }
      }
      const ai = item.ai as AiItem
      const base = aiPer100(ai)
      return {
        item_source: 'ai',
        name: ai.name_ru,
        name_en: ai.name_en,
        grams: item.grams,
        kcal_per_100g: base.kcal,
        protein_per_100g: base.protein,
        fat_per_100g: base.fat,
        carbs_per_100g: base.carbs,
        fiber_per_100g: base.fiber,
        match_method: 'ai',
        ...provenance,
      }
    })
    logMeal.mutate(
      {
        id: crypto.randomUUID(),
        meal_type: mealType,
        source: analysis.kind,
        ai_job_id: analysis.job_id,
        items: payload,
      },
      {
        onSuccess: (meal) => {
          toast.success(t('ai.saved_toast', { n: meal.item_count, kcal: number(meal.kcal) }))
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  const valid = items.length > 0 && items.every((item) => item.grams > 0)

  return (
    <Sheet
      open={!!analysis}
      onClose={onClose}
      size="tall"
      title={analysis?.kind === 'photo' ? t('ai.review_title_photo') : t('ai.review_title_text')}
      footer={
        <div className="space-y-3">
          <SegmentedControl
            value={mealType}
            onChange={(value) => setMealType(value)}
            options={MEAL_TYPES.map((value) => ({ value, label: t(`meal.${value}`) }))}
          />
          <Button block loading={logMeal.isPending} onClick={save} disabled={!valid}>
            {t('ai.save', { kcal: number(totals.kcal) })}
          </Button>
        </div>
      }
    >
      <div className="space-y-3 pb-2">
        <p className="text-xs muted">{t('ai.review_hint')}</p>
        {analysis?.notes && <Callout tone="info">{analysis.notes}</Callout>}
        {analysis && analysis.items.length === 0 && <Callout tone="warn">{t('ai.nothing_found')}</Callout>}

        {items.map((item) => (
          <ReviewItem
            key={item.key}
            item={item}
            foodName={foodName}
            onChange={(patch) => update(item.key, patch)}
            onRemove={() => setItems((current) => current.filter((row) => row.key !== item.key))}
          />
        ))}

        {adding ? (
          <FoodPicker
            title={t('ai.add_item')}
            onPick={addFood}
            onCancel={() => setAdding(false)}
            foodName={foodName}
          />
        ) : (
          <Button variant="secondary" size="sm" icon={<IconPlus size={16} />} onClick={() => setAdding(true)}>
            {t('ai.add_item')}
          </Button>
        )}

        {items.length > 0 && (
          <p className="tnum pt-1 text-xs muted">
            {t('ai.totals', {
              protein: number(totals.protein),
              fat: number(totals.fat),
              carbs: number(totals.carbs),
            })}
          </p>
        )}
      </div>
    </Sheet>
  )
}

function ReviewItem({
  item,
  foodName,
  onChange,
  onRemove,
}: {
  item: DraftItem
  foodName: (food: Food) => string
  onChange: (patch: Partial<DraftItem>) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const createFood = useCreateFood()
  const [swapping, setSwapping] = useState(false)

  const base = per100Of(item)
  const kcal = (base.kcal * item.grams) / 100
  const title = item.choice.kind === 'food' ? foodName(item.choice.food) : (item.ai?.name_ru ?? '')
  const level = item.ai?.confidence_level
  const sliderMax = Math.max(300, Math.ceil(((item.ai?.estimated_grams ?? item.grams) * 2.5) / 50) * 50)
  const renamed = item.ai && item.choice.kind === 'food' && foodName(item.choice.food) !== item.ai.name_ru

  const saveAsMine = () => {
    if (!item.ai) return
    const p = item.ai.ai_per_100g
    createFood.mutate(
      {
        name_ru: item.ai.name_ru,
        name_en: item.ai.name_en,
        kcal_per_100g: p.kcal_per_100g,
        protein_per_100g: p.protein_per_100g,
        fat_per_100g: p.fat_per_100g,
        carbs_per_100g: p.carbs_per_100g,
        fiber_per_100g: p.fiber_per_100g,
        default_serving_g: Math.round(item.ai.estimated_grams),
      },
      {
        onSuccess: (food) => {
          onChange({ choice: { kind: 'food', food, method: 'manual' } })
          toast.success(t('food.custom_saved', { name: food.name_ru ?? food.name_en ?? '' }))
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <div className="card space-y-3 p-3.5" data-testid="ai-item">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold">{title}</p>
          {renamed && <p className="truncate text-xs muted">{t('ai.recognised_as', { name: item.ai?.name_ru })}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {item.choice.kind === 'ai' ? (
              <Badge tone="warn">{t('ai.source_ai')}</Badge>
            ) : (
              <Badge tone="accent">{t('ai.source_library')}</Badge>
            )}
            {level && (
              <Badge tone={level === 'high' ? 'accent' : level === 'medium' ? 'neutral' : 'danger'}>
                {t(`ai.confidence.${level}`)}
              </Badge>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={onRemove}
          aria-label={t('ai.remove_item', { name: title })}
          className="flex h-tap w-10 shrink-0 items-center justify-center muted"
        >
          <IconTrash size={18} />
        </button>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={0}
            max={sliderMax}
            step={5}
            value={item.grams}
            onChange={(event) => onChange({ grams: Number(event.target.value), gramsTouched: true })}
            onPointerDownCapture={(event) => event.stopPropagation()} // don't drag the sheet
            className="h-tap min-w-0 flex-1 accent-[rgb(var(--accent))]"
            aria-label={t('ai.grams_slider', { name: title })}
          />
          <Input
            type="number"
            inputMode="decimal"
            min={1}
            value={item.grams || ''}
            onChange={(event) => onChange({ grams: Number(event.target.value), gramsTouched: true })}
            className="tnum w-20 px-2 text-center"
            aria-label={t('food.grams')}
          />
          <span className="text-xs muted">{t('common.g')}</span>
        </div>
        <p className="tnum flex items-center justify-between text-xs muted">
          <span>
            {item.ai && !item.gramsTouched
              ? t('ai.estimated_grams', { grams: number(item.ai.estimated_grams) })
              : t('ai.grams_set')}
          </span>
          <span className="text-sm font-semibold text-[rgb(var(--text))]">
            {number(kcal)} {t('common.kcal')}
          </span>
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          variant="ghost"
          size="sm"
          icon={<IconRefresh size={15} />}
          onClick={() => setSwapping((value) => !value)}
          aria-expanded={swapping}
        >
          {t('ai.swap')}
        </Button>
        {item.choice.kind === 'ai' && (
          <Button variant="ghost" size="sm" icon={<IconPlus size={15} />} loading={createFood.isPending} onClick={saveAsMine}>
            {t('ai.save_as_mine')}
          </Button>
        )}
      </div>

      {swapping && (
        <FoodPicker
          title={t('ai.swap_title')}
          initial={item.alternatives}
          initialQuery={item.ai?.name_ru ?? ''}
          foodName={foodName}
          aiOption={
            item.ai && item.choice.kind === 'food'
              ? {
                  label: t('ai.use_ai_estimate', { kcal: number(item.ai.ai_per_100g.kcal_per_100g) }),
                  onPick: () => {
                    onChange({ choice: { kind: 'ai' } })
                    setSwapping(false)
                  },
                }
              : undefined
          }
          onPick={(food) => {
            onChange({ choice: { kind: 'food', food, method: 'manual' } })
            setSwapping(false)
          }}
          onCancel={() => setSwapping(false)}
        />
      )}
    </div>
  )
}

function FoodPicker({
  title,
  initial = [],
  initialQuery = '',
  foodName,
  aiOption,
  onPick,
  onCancel,
}: {
  title: string
  initial?: Food[]
  initialQuery?: string
  foodName: (food: Food) => string
  aiOption?: { label: string; onPick: () => void }
  onPick: (food: Food) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(query), 250)
    return () => window.clearTimeout(handle)
  }, [query])
  const search = useFoodSearch(debounced, debounced.trim().length >= 2)
  const results = debounced.trim().length >= 2 ? (search.data?.items ?? []) : initial

  return (
    <div className="surface-sunken space-y-2 rounded-2xl p-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold">{title}</p>
        <button type="button" onClick={onCancel} aria-label={t('app.cancel')} className="flex h-9 w-9 items-center justify-center muted">
          <IconClose size={16} />
        </button>
      </div>
      <Field>
        <div className="relative">
          <IconSearch size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 muted" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={initialQuery || t('food.search_placeholder')}
            className="pl-9"
            autoFocus
          />
        </div>
      </Field>
      <div className="max-h-60 space-y-1 overflow-y-auto">
        {aiOption && (
          <button
            type="button"
            onClick={aiOption.onPick}
            className="flex min-h-tap w-full items-center rounded-xl px-3 text-left text-sm text-amber-300"
          >
            {aiOption.label}
          </button>
        )}
        {results.map((food) => (
          <button
            key={food.id}
            type="button"
            onClick={() => onPick(food)}
            className={clsx(
              'flex min-h-tap w-full items-center justify-between gap-2 rounded-xl px-3 text-left text-sm',
              'hover:bg-[rgb(var(--bg-elevated))]',
            )}
          >
            <span className="truncate">{foodName(food)}</span>
            <span className="tnum shrink-0 text-xs muted">
              {number(food.kcal_per_100g)} {t('common.kcal')}
            </span>
          </button>
        ))}
        {results.length === 0 && <p className="px-3 py-2 text-sm muted">{t('food.results_empty')}</p>}
      </div>
    </div>
  )
}
