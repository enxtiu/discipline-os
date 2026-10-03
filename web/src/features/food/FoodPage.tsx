/**
 * Food hub: search → log in two taps, plus recents, favorites, today's meals
 * and recipes. Everything on this page writes through the same endpoints the
 * Today dashboard reads, so the two never disagree.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { Button, Card, EmptyState, Input, Skeleton } from '@/components/ui'
import {
  IconClock,
  IconPlus,
  IconSearch,
  IconStar,
  IconTrash,
} from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { Food, MealLog, MealType, Recipe } from '@/api/types'
import { number, time } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { useAuth } from '@/features/auth/AuthProvider'
import { CustomFoodSheet } from './CustomFoodSheet'
import { LogSheet, type LogTarget } from './LogSheet'
import { RecipeSheet } from './RecipeSheet'
import {
  useAddWater,
  useDay,
  useDeleteMeal,
  useFavorites,
  useFoodSearch,
  useRecents,
  useRecipes,
  useToggleFavorite,
} from './useNutrition'

const MEAL_ORDER: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack', 'other']

export function FoodPage() {
  const { t, i18n } = useTranslation()
  const { session } = useAuth()
  const toast = useToast()
  const [params, setParams] = useSearchParams()

  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [target, setTarget] = useState<LogTarget | null>(null)
  const [recipeSheet, setRecipeSheet] = useState(false)
  const [customSheet, setCustomSheet] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)

  const day = useDay()
  const recents = useRecents()
  const favorites = useFavorites()
  const recipes = useRecipes()
  const deleteMeal = useDeleteMeal()
  const addWater = useAddWater()
  const toggleFavorite = useToggleFavorite()
  const search = useFoodSearch(debounced, debounced.trim().length >= 2)

  // one-hand typing: search settles 250 ms after the last key
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(query), 250)
    return () => window.clearTimeout(handle)
  }, [query])

  // deep links: /food?action=search|water|recipe|custom
  useEffect(() => {
    const action = params.get('action')
    if (!action) return
    if (action === 'search') searchRef.current?.focus()
    if (action === 'water') {
      addWater.mutate(
        { amountMl: 250, id: crypto.randomUUID() },
        {
          onSuccess: () => toast.success(t('today.water_added', { ml: 250 })),
          onError: (error) => toast.error(errorMessage(error)),
        },
      )
    }
    if (action === 'recipe') setRecipeSheet(true)
    if (action === 'custom') setCustomSheet(true)
    setParams({}, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params])

  const searching = debounced.trim().length >= 2
  const name = (food: Food) =>
    i18n.language === 'en' ? (food.name_en ?? food.name_ru ?? '') : (food.name_ru ?? food.name_en ?? '')

  const mealsByType = useMemo(() => {
    const grouped = new Map<MealType, MealLog[]>()
    for (const meal of day.data?.meals ?? []) {
      grouped.set(meal.meal_type, [...(grouped.get(meal.meal_type) ?? []), meal])
    }
    return grouped
  }, [day.data])

  return (
    <div className="space-y-5">
      <div className="relative">
        <IconSearch size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 muted" />
        <Input
          ref={searchRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('food.search_placeholder')}
          className="pl-10"
          autoComplete="off"
        />
      </div>

      {searching ? (
        <section className="space-y-2">
          {search.isPending && <Skeleton className="h-16 rounded-2xl" />}
          {(search.data?.items ?? []).map((match) => (
            <button
              key={match.id}
              type="button"
              onClick={() => setTarget({ kind: 'food', food: match })}
              className="surface-sunken flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{name(match)}</span>
                <span className="block truncate text-xs muted">
                  {match.brand ? `${match.brand} · ` : ''}
                  {number(match.kcal_per_100g)} {t('common.kcal')} / 100 {t('common.g')}
                </span>
              </span>
              <span
                role="button"
                tabIndex={0}
                aria-label={match.is_favorite ? t('food.unfavorite') : t('food.favorite')}
                className={match.is_favorite ? 'text-amber-300' : 'muted'}
                onClick={(event) => {
                  event.stopPropagation()
                  toggleFavorite.mutate({ foodId: match.id, favorite: match.is_favorite, grams: 100 })
                }}
                onKeyDown={(event) => event.key === 'Enter' && event.stopPropagation()}
              >
                <IconStar size={18} />
              </span>
            </button>
          ))}
          {search.isSuccess && (search.data?.items.length ?? 0) === 0 && (
            <EmptyState
              icon={<IconSearch size={28} />}
              title={t('food.results_empty')}
              description={t('food.results_empty_hint')}
              action={
                <Button size="sm" variant="secondary" onClick={() => setCustomSheet(true)}>
                  {t('food.custom_title')}
                </Button>
              }
            />
          )}
          <p className="px-1 text-right text-[11px] muted">
            {search.data ? `${search.data.items.length} · ${search.data.took_ms} ms` : ''}
          </p>
        </section>
      ) : (
        <>
          {recents.data && recents.data.items.length > 0 && (
            <section className="space-y-2">
              <p className="label flex items-center gap-1.5">
                <IconClock size={13} /> {t('food.recents')}
              </p>
              <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
                {recents.data.items.map((recent) =>
                  recent.food ? (
                    <button
                      key={recent.id}
                      type="button"
                      className="chip shrink-0"
                      onClick={() => setTarget({ kind: 'food', food: recent.food as Food })}
                    >
                      {name(recent.food as Food)}
                    </button>
                  ) : recent.recipe ? (
                    <button
                      key={recent.id}
                      type="button"
                      className="chip shrink-0"
                      onClick={() => setTarget({ kind: 'recipe', recipe: recent.recipe as Recipe })}
                    >
                      {recent.recipe.name}
                    </button>
                  ) : null,
                )}
              </div>
            </section>
          )}

          {favorites.data && favorites.data.length > 0 && (
            <section className="space-y-2">
              <p className="label flex items-center gap-1.5">
                <IconStar size={13} /> {t('food.favorites')}
              </p>
              <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
                {favorites.data.map((favorite) => (
                  <button
                    key={favorite.id}
                    type="button"
                    className="chip chip-active shrink-0"
                    onClick={() => setTarget({ kind: 'food', food: favorite.food })}
                  >
                    {name(favorite.food)}
                  </button>
                ))}
              </div>
            </section>
          )}

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="h2">{t('today.title')}</h2>
              <span className="tnum text-sm muted">
                {number(day.data?.totals.kcal ?? 0)} {t('common.kcal')}
              </span>
            </div>

            {day.isLoading && <Skeleton className="h-24 rounded-2xl" />}

            {MEAL_ORDER.filter((type) => mealsByType.has(type)).map((type) => (
              <Card key={type} className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="label">{t(`meal.${type}`)}</p>
                  <span className="tnum text-xs muted">
                    {number(
                      (mealsByType.get(type) ?? []).reduce((sum, meal) => sum + meal.kcal, 0),
                    )}{' '}
                    {t('common.kcal')}
                  </span>
                </div>
                {(mealsByType.get(type) ?? []).map((meal) => (
                  <div key={meal.id} className="flex items-center gap-3">
                    <span className="tnum w-11 shrink-0 text-xs muted">
                      {time(meal.logged_at, session?.settings.timezone)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm">
                      {(meal.items ?? []).map((item) => item.name).join(', ')}
                    </span>
                    <span className="tnum shrink-0 text-sm">{number(meal.kcal)}</span>
                    <button
                      type="button"
                      className="muted"
                      aria-label={t('app.delete')}
                      onClick={() =>
                        deleteMeal.mutate(meal.id, { onError: (error) => toast.error(errorMessage(error)) })
                      }
                    >
                      <IconTrash size={16} />
                    </button>
                  </div>
                ))}
              </Card>
            ))}

            {!day.isLoading && (day.data?.logged_meals ?? 0) === 0 && (
              <EmptyState
                icon={<IconPlus size={28} />}
                title={t('today.meals_empty')}
                description={t('today.meals_empty_hint_search')}
                action={
                  <Button size="sm" onClick={() => searchRef.current?.focus()}>
                    {t('food.search')}
                  </Button>
                }
              />
            )}
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="h2">{t('food.recipes')}</h2>
              <Button size="sm" variant="secondary" icon={<IconPlus size={14} />} onClick={() => setRecipeSheet(true)}>
                {t('food.recipe_new')}
              </Button>
            </div>
            {(recipes.data?.items ?? []).length === 0 ? (
              <p className="text-sm muted">{t('food.recipes_empty')}</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                {(recipes.data?.items ?? []).map((recipe) => (
                  <Card key={recipe.id} className="space-y-1">
                    <button type="button" className="text-left" onClick={() => setTarget({ kind: 'recipe', recipe })}>
                      <p className="text-sm font-semibold">{recipe.name}</p>
                      <p className="tnum text-xs muted">
                        {number(recipe.per_serving.kcal)} {t('common.kcal')} / {t('food.serving_short')} ·{' '}
                        {number(recipe.kcal_per_100g)} / 100 {t('common.g')}
                      </p>
                    </button>
                  </Card>
                ))}
              </div>
            )}
          </section>

          <div className="flex gap-2">
            <Button variant="secondary" size="sm" icon={<IconPlus size={14} />} onClick={() => setCustomSheet(true)}>
              {t('food.custom_title')}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => searchRef.current?.focus()}
            >
              {t('food.add_meal_title')}
            </Button>
          </div>

          <p className="text-xs muted">{t('food.subtitle_planned')}</p>
        </>
      )}

      <LogSheet target={target} onClose={() => setTarget(null)} />
      <RecipeSheet open={recipeSheet} onClose={() => setRecipeSheet(false)} />
      <CustomFoodSheet open={customSheet} onClose={() => setCustomSheet(false)} />
    </div>
  )
}
