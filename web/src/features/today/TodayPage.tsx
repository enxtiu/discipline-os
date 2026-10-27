/**
 * Today — the home screen and the daily nutrition dashboard.
 *
 * All numbers come from `GET /v1/nutrition/day`, which the server aggregates in
 * the user's timezone: calorie ring vs target, macro rings, water, the meal
 * list grouped by type, and the "burned is not budget" card (workouts since
 * stage 4, from `GET /v1/training/today`), steps, weight and sleep since
 * stage 5 (`GET /v1/health/today`), the planner card and the running timer since
 * stage 6, gamification in stage 8.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'

import { Button, Callout, Card, EmptyState, ProgressBar, Skeleton } from '@/components/ui'
import { ProgressRing } from '@/components/ui/ProgressRing'
import {
  IconCamera,
  IconDroplet,
  IconFlame,
  IconFootprints,
  IconPin,
  IconPlus,
  IconText,
  IconTrash,
} from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { MealLog, MealType, Recipe } from '@/api/types'
import { useAuth } from '@/features/auth/AuthProvider'
import { number, time, today } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { useAddWater, useDay, useDeleteMeal, useRecipes } from '@/features/food/useNutrition'
import { LogSheet } from '@/features/food/LogSheet'
import { PhotoTrigger, useAiCapture } from '@/features/ai/AiCapture'
import { MealPhoto } from '@/features/ai/MealPhoto'
import { TrainingTodayCard } from '@/features/train/TrainingTodayCard'
import { useTrainingToday } from '@/features/train/useTraining'
import { HealthTodayCard } from '@/features/health/HealthTodayCard'
import { SleepSheet, StepsSheet, WeightSheet } from '@/features/health/sheets'
import { useHealthQueueRefresh, useHealthToday } from '@/features/health/useHealth'
import { PlanTodayCard } from '@/features/plan/PlanTodayCard'
import { TimerBar } from '@/features/plan/timer/TimerBar'
import { DayScoreCard } from '@/features/game/DayScoreCard'

const MEAL_ORDER: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack', 'other']

export function TodayPage() {
  const { t } = useTranslation()
  const { session } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()

  const dayQuery = useDay()
  const addWater = useAddWater()
  const deleteMeal = useDeleteMeal()
  const capture = useAiCapture()
  const recipes = useRecipes()
  const training = useTrainingToday()
  const burnedKcal = Math.round(training.data?.burned_kcal ?? 0)
  const health = useHealthToday()
  useHealthQueueRefresh()
  const steps = health.data?.day.steps ?? 0
  const targetSteps = health.data?.day.target_steps ?? 0
  const stepsKcal = health.data?.day.steps_kcal
  const stepsEstimated = !!health.data?.day.steps_kcal_estimated
  const fromHealth = stepsKcal != null && !stepsEstimated
  const [healthSheet, setHealthSheet] = useState<'steps' | 'weight' | 'sleep' | null>(null)
  const [params, setParams] = useSearchParams()
  const action = params.get('action')
  useEffect(() => {
    if (action === 'steps' || action === 'weight' || action === 'sleep') {
      setHealthSheet(action)
      setParams({}, { replace: true })
    }
  }, [action, setParams])
  const [logRecipe, setLogRecipe] = useState<Recipe | null>(null)
  const pinned = (recipes.data?.items ?? []).filter((recipe) => recipe.is_pinned)

  const day = dayQuery.data
  const eaten = day?.totals ?? { kcal: 0, protein_g: 0, fat_g: 0, carbs_g: 0, fiber_g: 0 }
  const waterMl = day?.water_ml ?? 0

  const targets = session?.targets as Record<string, number | null> | undefined
  const targetKcal = targets?.target_kcal ?? null
  const hasTargets = !!targetKcal

  const remaining = targetKcal !== null ? Math.max(0, Math.round(targetKcal - eaten.kcal)) : null
  const progressPercent = targetKcal ? Math.min(100, Math.round((eaten.kcal / targetKcal) * 100)) : 0

  const mealsByType = useMemo(() => {
    const grouped = new Map<MealType, MealLog[]>()
    for (const meal of day?.meals ?? []) {
      grouped.set(meal.meal_type, [...(grouped.get(meal.meal_type) ?? []), meal])
    }
    return grouped
  }, [day])

  const quickWater = () =>
    addWater.mutate(
      { amountMl: 250, id: crypto.randomUUID() },
      {
        onSuccess: () => toast.success(t('today.water_added', { ml: 250 })),
        onError: (error) => toast.error(errorMessage(error)),
      },
    )

  return (
    <div className="space-y-5">
      <TimerBar variant="today" />

      <section className="flex flex-col items-center gap-4">
        {dayQuery.isLoading ? (
          <Skeleton className="h-[196px] w-[196px] rounded-full" />
        ) : (
          <ProgressRing
            value={eaten.kcal}
            max={targetKcal ?? 0}
            size={196}
            thickness={14}
            tone={progressPercent > 100 ? 'warn' : 'accent'}
            label={hasTargets ? number(eaten.kcal) : '—'}
            sublabel={t('today.kcal')}
            name={t('today.kcal')}
            valueText={
              hasTargets
                ? t('a11y.value_of', { value: number(eaten.kcal), target: number(targetKcal), unit: t('common.kcal') })
                : t('a11y.value_unit', { value: number(eaten.kcal), unit: t('common.kcal') })
            }
          >
            {hasTargets ? (
              <span className="mt-1 text-[11px] muted">
                / {number(targetKcal)} {t('common.kcal')}
              </span>
            ) : (
              <span className="mt-1 text-[11px] muted">{t('today.no_target')}</span>
            )}
          </ProgressRing>
        )}

        <div className="grid w-full grid-cols-3 gap-3 text-center">
          <MiniStat
            label={t('today.remaining')}
            value={remaining !== null ? number(remaining) : '—'}
            unit={t('common.kcal')}
            accent
          />
          <MiniStat label={t('today.eaten')} value={number(eaten.kcal)} unit={t('common.kcal')} />
          <MiniStat label={t('today.target')} value={hasTargets ? number(targetKcal) : '—'} unit={t('common.kcal')} />
        </div>
      </section>

      {!hasTargets && (
        <Callout tone="warn" title={t('today.no_target')}>
          <p className="mb-3">{t('onboarding.subtitle')}</p>
          <Button size="sm" onClick={() => window.dispatchEvent(new CustomEvent('dsos:open-onboarding'))}>
            {t('today.setup_targets')}
          </Button>
        </Callout>
      )}

      {hasTargets && (
        <Card className="space-y-4">
          <div className="grid grid-cols-4 gap-3">
            <MacroRing title={t('today.protein')} value={eaten.protein_g} max={targets?.target_protein_g ?? 0} tone="violet" unit={t('common.g')} />
            <MacroRing title={t('today.fat')} value={eaten.fat_g} max={targets?.target_fat_g ?? 0} tone="warn" unit={t('common.g')} />
            <MacroRing title={t('today.carbs')} value={eaten.carbs_g} max={targets?.target_carbs_g ?? 0} tone="accent" unit={t('common.g')} />
            <MacroRing title={t('today.fiber')} value={eaten.fiber_g} max={targets?.target_fiber_g ?? 0} tone="accent" unit={t('common.g')} />
          </div>
          <div className="divider" />
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-sm">
                <span className="flex items-center gap-1.5">
                  <span className="text-aqua-400">
                    <IconDroplet size={16} />
                  </span>
                  {t('today.water')}
                </span>
                <span className="tnum shrink-0 text-right leading-tight muted">
                  {number(waterMl)} / {number(targets?.target_water_ml ?? 0)}&nbsp;{t('common.ml')}
                </span>
              </div>
              <ProgressBar value={waterMl} max={targets?.target_water_ml ?? 1} />
              <Button size="sm" variant="secondary" loading={addWater.isPending} onClick={quickWater}>
                {t('today.quick_water', { ml: 250 })}
              </Button>
            </div>
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-sm">
                <span className="flex items-center gap-1.5">
                  <span className="text-violet-300">
                    <IconFootprints size={16} />
                  </span>
                  {t('today.steps')}
                </span>
                <span className="tnum shrink-0 text-right leading-tight muted" data-testid="steps-today">
                  {number(steps)} / {number(targetSteps)}
                </span>
              </div>
              <ProgressBar value={steps} max={targetSteps || 1} tone="violet" />
              <Button size="sm" variant="secondary" className="min-h-tap" onClick={() => setHealthSheet('steps')}>
                {t('health.steps_enter')}
              </Button>
            </div>
          </div>
        </Card>
      )}

      <Card className="space-y-3">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <IconFlame size={16} className="text-amber-400" />
          {t('today.burned_card')}
        </p>
        <div className="grid grid-cols-2 gap-2">
          <div className="surface-sunken rounded-2xl px-3 py-2.5">
            <p className="text-xs muted">{t('today.burned_workouts')}</p>
            <p className="tnum text-lg font-semibold leading-tight">
              <span data-testid="burned-today">{number(burnedKcal)}</span>
              <span className="ml-1 text-xs font-normal muted">{t('common.kcal')}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={() => setHealthSheet('steps')}
            className="surface-sunken min-h-tap rounded-2xl px-3 py-2.5 text-left"
          >
            <p className="text-xs muted">{fromHealth ? t('today.burned_activity') : t('today.burned_steps')}</p>
            <p className="tnum text-lg font-semibold leading-tight">
              <span data-testid="burned-steps">
                {stepsKcal == null
                  ? '—'
                  : stepsEstimated
                    ? t('health.kcal_estimated', { kcal: number(stepsKcal) })
                    : number(stepsKcal)}
              </span>
              <span className="ml-1 text-xs font-normal muted">{t('common.kcal')}</span>
            </p>
          </button>
        </div>
        <p className="text-xs muted">{t('today.burned_hint')}</p>
        {fromHealth && <p className="text-xs muted">{t('today.burned_overlap')}</p>}
      </Card>

      <HealthTodayCard
        data={health.data}
        loading={health.isLoading}
        tz={session?.settings.timezone}
        onWeight={() => setHealthSheet('weight')}
        onSleep={() => setHealthSheet('sleep')}
      />

      {pinned.length > 0 && (
        <section className="space-y-2" aria-label={t('today.pinned_recipes')}>
          <p className="flex items-center gap-1.5 text-sm font-semibold">
            <IconPin size={15} className="text-[rgb(var(--accent))]" filled />
            {t('today.pinned_recipes')}
          </p>
          <div className="scroll-x">
            {pinned.map((recipe) => (
              <button
                key={recipe.id}
                type="button"
                onClick={() => setLogRecipe(recipe)}
                className="surface-sunken flex min-h-tap shrink-0 flex-col items-start justify-center rounded-2xl px-4 py-2 text-left"
              >
                <span className="max-w-[11rem] truncate text-sm font-medium">{recipe.name}</span>
                <span className="tnum text-xs muted">
                  {t('today.pinned_kcal', {
                    kcal: number(recipe.per_serving.kcal),
                    grams: number(recipe.portion_size_g),
                  })}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="h2">{t('today.meals_title')}</h2>
          <Button size="sm" icon={<IconPlus size={14} />} onClick={() => navigate('/food?action=search')}>
            {t('today.add_meal')}
          </Button>
        </div>

        {dayQuery.isLoading && <Skeleton className="h-24 rounded-2xl" />}

        {!dayQuery.isLoading &&
          MEAL_ORDER.filter((type) => mealsByType.has(type)).map((type) => (
            <Card key={type} className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="label">{t(`meal.${type}`)}</p>
                <span className="tnum text-xs muted">
                  {number((mealsByType.get(type) ?? []).reduce((sum, meal) => sum + meal.kcal, 0))} {t('common.kcal')}
                </span>
              </div>
              {(mealsByType.get(type) ?? []).map((meal) => (
                <div key={meal.id} className="flex items-center gap-3">
                  <span className="tnum w-11 shrink-0 text-xs muted">{time(meal.logged_at, session?.settings.timezone)}</span>
                  {meal.has_photo && <MealPhoto mealId={meal.id} />}
                  <span className="min-w-0 flex-1 truncate text-sm">{(meal.items ?? []).map((item) => item.name).join(', ')}</span>
                  <span className="tnum shrink-0 text-sm">{number(meal.kcal)}</span>
                  <button
                    type="button"
                    className="-my-3.5 -mr-3 flex h-tap w-tap shrink-0 items-center justify-center rounded-2xl muted"
                    aria-label={t('a11y.delete_meal', { name: (meal.items ?? []).map((item) => item.name).join(', ') })}
                    onClick={() => deleteMeal.mutate(meal.id, { onError: (error) => toast.error(errorMessage(error)) })}
                  >
                    <IconTrash size={16} />
                  </button>
                </div>
              ))}
            </Card>
          ))}

        {!dayQuery.isLoading && (day?.logged_meals ?? 0) === 0 && (
          <EmptyState
            icon={<IconCamera size={30} />}
            title={t('today.meals_empty')}
            description={t('today.meals_empty_hint')}
            action={
              <div className="flex gap-2">
                <PhotoTrigger className="btn-primary btn-sm cursor-pointer">
                  <IconCamera size={16} />
                  {t('food.photo')}
                </PhotoTrigger>
                <Button size="sm" variant="secondary" icon={<IconText size={16} />} onClick={capture.openText}>
                  {t('food.text')}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => navigate('/food?action=search')}>
                  {t('food.search')}
                </Button>
              </div>
            }
          />
        )}
      </section>

      <div className="grid gap-3 sm:grid-cols-2">
        <TrainingTodayCard data={training.data} loading={training.isLoading} />
        <PlanTodayCard />
      </div>

      <DayScoreCard />

      <p className="pb-2 text-center text-xs muted">
        {today()} · {number(progressPercent)}%
      </p>

      <LogSheet
        target={logRecipe ? { kind: 'recipe', recipe: logRecipe } : null}
        onClose={() => setLogRecipe(null)}
      />
      <StepsSheet open={healthSheet === 'steps'} onClose={() => setHealthSheet(null)} />
      <WeightSheet open={healthSheet === 'weight'} onClose={() => setHealthSheet(null)} />
      <SleepSheet open={healthSheet === 'sleep'} onClose={() => setHealthSheet(null)} />
    </div>
  )
}

function MiniStat({
  label,
  value,
  unit,
  accent,
}: {
  label: string
  value: string
  unit?: string
  accent?: boolean
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="surface-sunken px-3 py-2.5 text-center"
    >
      <p className="text-[11px] uppercase tracking-wide muted">{label}</p>
      <p className={accent ? 'stat-value text-aqua-300' : 'stat-value'}>
        {value}
        {unit && <span className="ml-1 text-[11px] font-normal muted">{unit}</span>}
      </p>
    </motion.div>
  )
}

function MacroRing({
  title,
  value,
  max,
  tone,
  unit,
}: {
  title: string
  value: number
  max: number
  tone: 'accent' | 'violet' | 'warn'
  unit: string
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col items-center gap-2">
      <ProgressRing
        size={64}
        thickness={6}
        value={value}
        max={max}
        tone={tone}
        glow={false}
        label={number(value)}
        name={title}
        valueText={t('a11y.value_of', { value: number(value), target: number(max), unit })}
      />
      {/* the ring above already says «Белки, 8 из 160 г» */}
      <div className="text-center" aria-hidden="true">
        <p className="text-[11px] font-semibold">{title}</p>
        <p className="text-[10px] muted tnum">
          / {number(max)} {unit}
        </p>
      </div>
    </div>
  )
}
