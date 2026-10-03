/**
 * Bottom tab bar: Today · Food · [ + ] · Train · Plan, with Stats and Settings
 * reachable from the header. The centre button is the quick-add entry point
 * (every frequent action is within two taps from here).
 *
 * Height matches `.tabbar` + `pb-tabbar` in the stylesheet so no page content
 * hides behind it, and it respects the iOS home-indicator inset.
 */
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { motion } from 'framer-motion'

import {
  IconFood,
  IconPlan,
  IconPlus,
  IconToday,
  IconTrain,
} from '@/components/ui/icons'

const TABS = [
  { to: '/today', labelKey: 'today', Icon: IconToday },
  { to: '/food', labelKey: 'food', Icon: IconFood },
  { to: '/train', labelKey: 'train', Icon: IconTrain },
  { to: '/plan', labelKey: 'plan', Icon: IconPlan },
] as const

export function BottomTabBar({ onQuickAdd }: { onQuickAdd: () => void }) {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()

  // Two tabs on the left, the quick-add in the middle, two on the right.
  const left = TABS.slice(0, 2)
  const right = TABS.slice(2)

  return (
    <nav className="tabbar" aria-label={t('nav.today')}>
      <div className="mx-auto flex max-w-lg items-stretch px-2">
        {left.map((tab) => (
          <TabButton key={tab.to} {...tab} active={location.pathname.startsWith(tab.to)} />
        ))}

        <div className="relative flex w-20 items-center justify-center">
          <motion.button
            type="button"
            whileTap={{ scale: 0.92 }}
            onClick={onQuickAdd}
            aria-label={t('nav.quick_add')}
            className="absolute -top-6 z-10 flex h-14 w-14 items-center justify-center rounded-full bg-[rgb(var(--accent))] text-ink-950 shadow-glow"
          >
            <IconPlus size={26} />
          </motion.button>
          <button
            type="button"
            onClick={() => navigate('/stats')}
            className={clsx('tab pt-6', location.pathname.startsWith('/stats') && 'text-[rgb(var(--accent))]')}
            data-active={location.pathname.startsWith('/stats')}
          >
            {t('nav.stats')}
          </button>
        </div>

        {right.map((tab) => (
          <TabButton key={tab.to} {...tab} active={location.pathname.startsWith(tab.to)} />
        ))}
      </div>
    </nav>
  )
}

function TabButton({
  to,
  labelKey,
  Icon,
  active,
}: {
  to: string
  labelKey: string
  Icon: (props: { size?: number }) => JSX.Element
  active: boolean
}) {
  const { t } = useTranslation()
  return (
    <NavLink to={to} className="tab" data-active={active} aria-current={active ? 'page' : undefined}>
      <Icon size={22} />
      <span>{t(`nav.${labelKey}`)}</span>
    </NavLink>
  )
}
