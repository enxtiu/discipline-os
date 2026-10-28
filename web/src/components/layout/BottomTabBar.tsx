/**
 * Bottom tab bar: Today · Food · [ + / Stats ] · Train · Plan. The centre
 * button is the quick-add entry point (every frequent action is within two
 * taps from here); the Stats tab sits under it as a full 48 px tab the
 * floating button never covers (stage 9). Settings is in the header.
 *
 * Height matches `.tabbar` + `pb-tabbar` in the stylesheet so no page content
 * hides behind it (the floating button included), and it respects the iOS
 * home-indicator inset.
 */
import { NavLink, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { motion } from 'framer-motion'

import {
  IconFood,
  IconPlan,
  IconPlus,
  IconStats,
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

  // Two tabs on the left, the quick-add in the middle, two on the right.
  const left = TABS.slice(0, 2)
  const right = TABS.slice(2)

  return (
    <nav className="tabbar" aria-label={t('a11y.nav_main')}>
      <div className="mx-auto flex max-w-lg items-stretch px-2">
        {left.map((tab) => (
          <TabButton key={tab.to} {...tab} active={location.pathname.startsWith(tab.to)} />
        ))}

        {/* the quick add floats above the bar; the Stats tab below it is clear of it */}
        <div className="relative flex w-20 flex-col items-center justify-end">
          <motion.button
            type="button"
            whileTap={{ scale: 0.92 }}
            onClick={onQuickAdd}
            aria-label={t('nav.quick_add')}
            className="absolute -top-12 z-10 flex h-14 w-14 items-center justify-center rounded-full bg-[rgb(var(--accent))] text-on-accent shadow-glow"
          >
            <IconPlus size={26} />
          </motion.button>
          <TabButton
            to="/stats"
            labelKey="stats"
            Icon={IconStats}
            active={location.pathname.startsWith('/stats')}
            className="mt-2 h-tap w-full flex-none justify-end pb-2 pt-0"
          />
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
  className,
}: {
  to: string
  labelKey: string
  Icon: (props: { size?: number }) => JSX.Element
  active: boolean
  className?: string
}) {
  const { t } = useTranslation()
  return (
    <NavLink to={to} className={clsx('tab', className)} data-active={active} aria-current={active ? 'page' : undefined}>
      <Icon size={22} />
      <span>{t(`nav.${labelKey}`)}</span>
    </NavLink>
  )
}
