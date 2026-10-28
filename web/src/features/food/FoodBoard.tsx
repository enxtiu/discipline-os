/**
 * Питание, the pieces a theme may add around the day (`SkinTraits.boardContent`, Ffin-Food):
 * the day switch («Ср, 28 октября» · сегодня), the day-total sticker (calories, the bar,
 * «Осталось N», four macros) and the meal cards (title and time, a kcal sticker, «＋» to add
 * to that meal, one line per food). The same numbers the rest of the page reads from
 * `GET /nutrition/day`; «Эволюция» never renders them. `sk-*` and `data-cat` are the theme's hooks.
 */
import { useTranslation } from 'react-i18next'

import { ProgressBar } from '@/components/ui'
import { IconChevronLeft, IconChevronRight, IconPlus, IconTrash } from '@/components/ui/icons'
import type { DaySummary, MealLog, MealType } from '@/api/types'
import i18n from '@/i18n'
import { number, time } from '@/lib/format'

/** «Ср, 28 октября» — the weekday short, the month in full (the board). */
function dayTitle(day: string): string {
  const date = new Date(`${day}T12:00:00Z`)
  return new Intl.DateTimeFormat(i18n.language === 'en' ? 'en-GB' : 'ru-RU', {
    weekday: 'short',
    day: 'numeric',
    month: 'long',
    timeZone: 'UTC',
  }).format(date)
}

export function DaySwitch({
  day,
  isToday,
  canNext,
  onPrev,
  onNext,
}: {
  day: string
  isToday: boolean
  canNext: boolean
  onPrev: () => void
  onNext: () => void
}) {
  const { t } = useTranslation()
  return (
    <div role="group" aria-label={t('board.day_group')} className="sk-dayswitch flex items-center gap-2.5" data-testid="day-switch">
      <button type="button" className="btn-ghost h-tap w-tap shrink-0 p-0" aria-label={t('board.day_prev')} onClick={onPrev}>
        <IconChevronLeft size={22} />
      </button>
      <div className="flex min-w-0 flex-1 items-center justify-center gap-2.5">
        <time dateTime={day} className="sk-day-label truncate text-lg font-bold first-letter:uppercase">
          {dayTitle(day)}
        </time>
        {isToday && <span className="sk-badge sk-tilt-l sk-label sk-label-xs">{t('board.day_today')}</span>}
      </div>
      <button
        type="button"
        className="btn-ghost h-tap w-tap shrink-0 p-0"
        aria-label={t('board.day_next')}
        onClick={onNext}
        disabled={!canNext}
      >
        <IconChevronRight size={22} />
      </button>
    </div>
  )
}

export function DayTotals({ day, targets }: { day: DaySummary | undefined; targets: Record<string, number | null> | undefined }) {
  const { t } = useTranslation()
  const eaten = day?.totals ?? { kcal: 0, protein_g: 0, fat_g: 0, carbs_g: 0, fiber_g: 0 }
  const target = targets?.target_kcal ?? null
  const left = target != null ? Math.max(0, Math.round(target - eaten.kcal)) : null
  const kcal = t('common.kcal')
  const g = t('common.g')
  const macros = [
    { key: 'protein' as const, title: t('today.protein'), value: eaten.protein_g, max: targets?.target_protein_g ?? 0 },
    { key: 'fat' as const, title: t('today.fat'), value: eaten.fat_g, max: targets?.target_fat_g ?? 0 },
    { key: 'carbs' as const, title: t('today.carbs'), value: eaten.carbs_g, max: targets?.target_carbs_g ?? 0 },
    { key: 'fibre' as const, title: t('today.fiber'), value: eaten.fiber_g, max: targets?.target_fiber_g ?? 0 },
  ]
  return (
    <section className="card sk-tile sk-totals relative" data-cat="kcal" aria-label={t('board.totals')} data-testid="day-totals">
      <div className="flex items-center justify-between gap-2.5">
        <p className="flex min-w-0 flex-col">
          <span className="tnum sk-totals-value" data-testid="day-totals-value">
            {number(eaten.kcal)}
          </span>
          <span className="sk-soft text-sm font-semibold">
            {target != null ? t('today.of_kcal', { kcal: number(target) }) : t('today.no_target')}
          </span>
        </p>
        {left != null && <span className="sk-badge sk-tilt-r sk-label tnum">{t('board.left', { kcal: number(left) })}</span>}
      </div>
      <ProgressBar
        value={eaten.kcal}
        max={target ?? 1}
        tone="kcal"
        className="sk-bar-hero mt-2"
        label={t('today.kcal')}
        valueText={
          target != null
            ? t('a11y.value_of', { value: number(eaten.kcal), target: number(target), unit: kcal })
            : t('a11y.value_unit', { value: number(eaten.kcal), unit: kcal })
        }
      />
      <div className="sk-totals-macros mt-2.5 grid grid-cols-4 gap-2">
        {macros.map((item) => (
          <div key={item.key} className="flex min-w-0 flex-col" data-cat-bar={item.key}>
            <span className="truncate text-[13px] font-bold leading-4">{item.title}</span>
            <span className="tnum sk-soft whitespace-nowrap text-xs font-semibold leading-[14px]">
              {number(item.value)}/{number(item.max)} {g}
            </span>
            <ProgressBar
              value={item.value}
              max={item.max || 1}
              tone={item.key}
              className="sk-bar-mini mt-1"
              label={item.title}
              valueText={t('a11y.value_of', { value: number(item.value), target: number(item.max), unit: g })}
            />
          </div>
        ))}
      </div>
    </section>
  )
}

/** A meal type's card: title + time, the kcal sticker, «＋», and a line per food of each meal. */
export function MealCard({
  type,
  meals,
  index,
  timezone,
  pendingIds,
  canAdd,
  onAdd,
  onDelete,
}: {
  type: MealType
  meals: MealLog[]
  index: number
  timezone: string | undefined
  pendingIds: Set<string>
  canAdd: boolean
  onAdd: () => void
  onDelete: (meal: MealLog) => void
}) {
  const { t } = useTranslation()
  const kcal = meals.reduce((sum, meal) => sum + meal.kcal, 0)
  const title = t(`meal.${type}`)
  return (
    <section className="card sk-meal" aria-label={`${title}, ${number(kcal)} ${t('common.kcal')}`} data-testid="meal-card">
      <div className="sk-meal-head flex items-center gap-2">
        <h2 className="flex min-w-0 flex-1 items-baseline gap-1.5 whitespace-nowrap text-[17px] font-bold leading-[22px]">
          {title}
          {meals[0] && <span className="tnum muted text-[13px] font-semibold">{time(meals[0].logged_at, timezone)}</span>}
        </h2>
        <span className={`sk-badge sk-label sk-label-sm tnum ${index % 2 ? 'sk-tilt-r' : 'sk-tilt-l'}`} data-cat="kcal">
          {number(kcal)} {t('common.kcal')}
        </span>
        {canAdd ? (
          <button type="button" className="sk-meal-add" aria-label={t(`board.add_to.${type}`)} onClick={onAdd}>
            <span aria-hidden="true">
              <IconPlus size={20} strokeWidth={2.6} />
            </span>
          </button>
        ) : (
          <span className="w-2" aria-hidden="true" />
        )}
      </div>
      {meals.map((meal) => (
        <div
          key={meal.id}
          className="sk-meal-row flex items-start"
          data-testid="meal-row"
          data-meal-id={meal.client_uuid}
          data-pending={pendingIds.has(meal.id) ? 'true' : 'false'}
        >
          <ul className="min-w-0 flex-1 space-y-1">
            {(meal.items ?? []).map((item) => (
              <li key={item.id} className="flex items-baseline gap-1.5 text-sm leading-[18px]">
                <span className="min-w-0 truncate font-semibold">{item.name}</span>
                <span className="muted shrink-0 whitespace-nowrap font-medium">
                  {'· '}{number(item.grams)} {t('common.g')}
                </span>
                <span className="tnum ml-auto whitespace-nowrap font-bold">
                  {number(item.kcal)} {t('common.kcal')}
                </span>
              </li>
            ))}
            {pendingIds.has(meal.id) && (
              <li className="text-[11px] text-violet-300" data-testid="meal-pending">
                {t('sync.pending_mark')}
              </li>
            )}
          </ul>
          <button
            type="button"
            className="sk-meal-del -my-3 -mr-2.5 flex h-tap w-tap shrink-0 items-center justify-center rounded-2xl muted"
            aria-label={t('a11y.delete_meal', { name: (meal.items ?? []).map((item) => item.name).join(', ') })}
            onClick={() => onDelete(meal)}
            data-testid="meal-delete"
          >
            <IconTrash size={16} />
          </button>
        </div>
      ))}
    </section>
  )
}
