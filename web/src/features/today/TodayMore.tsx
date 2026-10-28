/**
 * Сегодня, below the rest (stage 10 — the unified «Сегодня» of every theme, boards
 * Ffin-TodayMore / Dfin-TodayMore): «План дня», «Повторить» and the one calm insight.
 * Each block is its own component and hides itself when it has nothing to show (the plan
 * always has the meals still to log). The `sk-*` classes and `data-state` are hooks for
 * the themes (styles/skins/*.css); «Эволюция» draws them with the shared classes.
 */
import { DayPlan } from './DayPlan'
import { RepeatFoods } from './RepeatFoods'
import { TodayInsight } from './TodayInsight'

export function TodayMore() {
  return (
    <div className="space-y-5" data-testid="today-more">
      <DayPlan />
      <RepeatFoods />
      <TodayInsight />
    </div>
  )
}
