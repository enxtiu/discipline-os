/**
 * The centre "+" of the tab bar.
 *
 * Stage 1 wired the *navigation* half of every action; each lands on the screen
 * that implements it. Delivered stages show no badge; actions of later stages
 * keep their stage number so nothing looks broken. Stage 3: photo opens the
 * camera directly, text and barcode open their sheets in place. Stage 6: the
 * task tile opens the natural-language quick add right here, the review tile
 * the daily journal.
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Sheet, Badge } from '@/components/ui'
import { useSkinTraits } from '@/lib/appearance'
import { useRecents } from '@/features/food/useNutrition'
import { recentChoices, useRelog } from '@/features/food/useRelog'
import { useTrainingToday } from '@/features/train/useTraining'
import { PhotoTrigger, useAiCapture } from '@/features/ai/AiCapture'
import { quickAdd } from '@/features/plan/events'
import {
  IconCamera,
  IconScan,
  IconDroplet,
  IconFootprints,
  IconPlus,
  IconText,
  IconToday,
  IconTrain,
  IconSearch,
} from '@/components/ui/icons'
import { number } from '@/lib/format'

interface Action {
  key: string
  labelKey: string
  icon: JSX.Element
  to: string
  stage?: number
  primary?: boolean
  /** stage 10: the sticker colour a theme may give the action's icon (`data-cat`) */
  cat?: 'kcal' | 'water' | 'steps' | 'score'
}

const ACTIONS: Action[] = [
  { key: 'photo', labelKey: 'food.photo', icon: <IconCamera size={22} />, to: '/food?action=photo', stage: 3, primary: true },
  { key: 'text', labelKey: 'food.text', icon: <IconText size={22} />, to: '/food?action=text', stage: 3, primary: true },
  { key: 'barcode', labelKey: 'food.barcode', icon: <IconScan size={22} />, to: '/food?action=barcode', stage: 3, cat: 'kcal' },
  { key: 'search', labelKey: 'food.search', icon: <IconSearch size={22} />, to: '/food?action=search', stage: 2, cat: 'kcal' },
  { key: 'water', labelKey: 'today.add_water', icon: <IconDroplet size={22} />, to: '/food?action=water', stage: 2, cat: 'water' },
  { key: 'workout', labelKey: 'train.start', icon: <IconTrain size={22} />, to: '/train?action=start', stage: 4, cat: 'steps' },
  { key: 'task', labelKey: 'plan.add_task', icon: <IconPlus size={22} />, to: '/plan?action=task', stage: 6 },
  { key: 'steps', labelKey: 'today.steps', icon: <IconFootprints size={22} />, to: '/today?action=steps', stage: 5, cat: 'steps' },
  { key: 'review', labelKey: 'plan.journal_review', icon: <IconToday size={22} />, to: '/plan/journal', stage: 6, cat: 'score' },
]

export function QuickAddSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const { boardContent } = useSkinTraits() // stage 10: the ИИ tags, the plan tag, the recents
  const navigate = useNavigate()
  const capture = useAiCapture()

  // literal classes (Tailwind purges built ones); sk-qa* are hooks for the themes
  const tileClass = (action: Action) =>
    `sk-qa-${action.key} relative flex min-h-[5.5rem] cursor-pointer flex-col items-start justify-between gap-2 rounded-2xl border p-4 text-left transition-transform active:scale-[.97] ${
      action.primary
        ? 'sk-qa sk-qa-primary border-aqua-400/40 bg-aqua-400/10 text-aqua-200'
        : 'sk-qa border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))]'
    }`

  const tileBody = (action: Action) => (
    <>
      <span className={action.primary ? 'sk-qa-icon text-aqua-300' : 'sk-qa-icon text-[rgb(var(--text-muted))]'}>{action.icon}</span>
      <span className="flex w-full items-center justify-between gap-2">
        <span className="text-sm font-medium leading-tight">
          {t(action.labelKey)}
        </span>
        {boardContent && action.primary && (
          <span className={`sk-ai-tag sk-ai-tag-${action.key}`} aria-hidden="true">
            {t('board.ai')}
          </span>
        )}
        {boardContent && action.key === 'workout' && <PlanTag />}
        {action.stage && action.stage > 6 && (
          <Badge tone="violet" className="shrink-0">
            {action.stage}
          </Badge>
        )}
      </span>
    </>
  )

  return (
    <Sheet open={open} onClose={onClose} title={t('nav.quick_add')}>
      <div className="sk-qa-grid grid grid-cols-2 gap-3 pb-2">
        {ACTIONS.map((action) =>
          // the camera opens straight from this tap (iOS needs a direct gesture)
          action.key === 'photo' ? (
            <PhotoTrigger key={action.key} className={tileClass(action)} onTrigger={onClose}>
              {tileBody(action)}
            </PhotoTrigger>
          ) : (
            <button
              key={action.key}
              type="button"
              onClick={() => {
                onClose()
                if (action.key === 'text') capture.openText()
                else if (action.key === 'barcode') capture.openBarcode()
                else if (action.key === 'task') quickAdd()
                else navigate(action.to)
              }}
              className={tileClass(action)}
              data-cat={action.cat}
            >
              {tileBody(action)}
            </button>
          ),
        )}
      </div>
      {boardContent && <QuickRecents onDone={onClose} />}
    </Sheet>
  )
}

/** The planned workout's name on the «Начать тренировку» tile («по плану: ноги»). */
function PlanTag() {
  const { t } = useTranslation()
  const today = useTrainingToday()
  const name = today.data?.plan.template?.name
  if (!name) return null
  return <span className="sk-qa-tag">{t('board.plan_tag', { name: name.toLowerCase() })}</span>
}

/** «Недавнее · запись в одно касание»: the last foods, logged again with their last grams. */
function QuickRecents({ onDone }: { onDone: () => void }) {
  const { t, i18n } = useTranslation()
  const recents = useRecents()
  const relog = useRelog()
  const choices = recentChoices(recents.data?.items, i18n.language).slice(0, 3)
  if (choices.length === 0) return null
  return (
    <section className="sk-recent-block" aria-labelledby="qa-recents">
      <h3 id="qa-recents" className="sk-recent-title">
        {t('board.recents_title')}
      </h3>
      <div className="sk-recent-grid">
        {choices.map((choice) => (
          <button
            key={choice.recent.id}
            type="button"
            className="sk-recent"
            aria-label={t('board.recent_again', { name: choice.name, kcal: number(choice.kcal) })}
            onClick={() => relog(choice, onDone)}
          >
            <span className="sk-recent-name">{choice.name}</span>
            <span className="sk-recent-foot">
              <span className="tnum">
                {number(choice.kcal)} {t('common.kcal')}
              </span>
              <span aria-hidden="true" className="sk-recent-plus">
                <IconPlus size={12} strokeWidth={3} />
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}
