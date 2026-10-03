/**
 * The centre "+" of the tab bar.
 *
 * Stage 1 wires the *navigation* half of every action so the habit and the
 * layout are already correct; each action lands on the screen that implements
 * it in a later stage (marked with a stage badge so nothing looks broken).
 */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Sheet, Badge } from '@/components/ui'
import {
  IconCamera,
  IconDroplet,
  IconFootprints,
  IconPlus,
  IconText,
  IconToday,
  IconTrain,
  IconSearch,
} from '@/components/ui/icons'

interface Action {
  key: string
  labelKey: string
  icon: JSX.Element
  to: string
  stage?: number
  primary?: boolean
}

const ACTIONS: Action[] = [
  { key: 'photo', labelKey: 'food.photo', icon: <IconCamera size={22} />, to: '/food?action=photo', stage: 3, primary: true },
  { key: 'text', labelKey: 'food.text', icon: <IconText size={22} />, to: '/food?action=text', stage: 3, primary: true },
  { key: 'search', labelKey: 'food.search', icon: <IconSearch size={22} />, to: '/food?action=search', stage: 2 },
  { key: 'water', labelKey: 'today.add_water', icon: <IconDroplet size={22} />, to: '/food?action=water', stage: 2 },
  { key: 'workout', labelKey: 'train.start', icon: <IconTrain size={22} />, to: '/train?action=start', stage: 4 },
  { key: 'task', labelKey: 'plan.quick_add_placeholder', icon: <IconPlus size={22} />, to: '/plan?action=task', stage: 6 },
  { key: 'steps', labelKey: 'today.steps', icon: <IconFootprints size={22} />, to: '/today?action=steps', stage: 5 },
  { key: 'review', labelKey: 'today.score', icon: <IconToday size={22} />, to: '/today?action=review', stage: 6 },
]

export function QuickAddSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <Sheet open={open} onClose={onClose} title={t('nav.quick_add')}>
      <div className="grid grid-cols-2 gap-3 pb-2">
        {ACTIONS.map((action) => (
          <button
            key={action.key}
            type="button"
            onClick={() => {
              onClose()
              navigate(action.to)
            }}
            className={`flex min-h-[5.5rem] flex-col items-start justify-between gap-2 rounded-2xl border p-4 text-left transition-transform active:scale-[.97] ${
              action.primary
                ? 'border-aqua-400/40 bg-aqua-400/10 text-aqua-200'
                : 'border-[rgb(var(--border))] bg-[rgb(var(--bg-sunken))]'
            }`}
          >
            <span className={action.primary ? 'text-aqua-300' : 'text-[rgb(var(--text-muted))]'}>
              {action.icon}
            </span>
            <span className="flex w-full items-center justify-between gap-2">
              <span className="text-sm font-medium leading-tight">
                {action.key === 'task' ? t('plan.quick_add_placeholder') : t(action.labelKey)}
              </span>
              {action.stage && (
                <Badge tone={action.stage <= 3 ? 'accent' : 'violet'} className="shrink-0">
                  {action.stage}
                </Badge>
              )}
            </span>
          </button>
        ))}
      </div>
    </Sheet>
  )
}
