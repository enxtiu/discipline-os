/**
 * Plan tab frame: title with search and "+", the running timer, the four
 * primary views (Today · Inbox · Upcoming · Calendar) and a scrollable row of
 * the PARA screens, time log and journal. `?action=task` (the quick-add tile,
 * a home-screen shortcut) opens the natural-language quick add.
 */
import { useEffect } from 'react'
import { NavLink, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { IconButton } from '@/components/ui'
import { IconPlus, IconSearch } from '@/components/ui/icons'
import { TimerBar } from './timer/TimerBar'
import { quickAdd } from './events'
import { useCounts } from './queries'

export function PlanLayout() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const counts = useCounts()

  useEffect(() => {
    if (params.get('action') !== 'task') return
    quickAdd()
    const next = new URLSearchParams(params)
    next.delete('action')
    setParams(next, { replace: true })
  }, [params, setParams])

  const overdue = counts.data?.overdue ?? 0
  const inbox = counts.data?.inbox ?? 0
  const primary = [
    {
      to: '/plan',
      label: t('plan.today'),
      end: true,
      badge: overdue || undefined,
      badgeLabel: t('plan.badge_overdue', { n: overdue }),
      danger: true,
    },
    {
      to: '/plan/inbox',
      label: t('plan.inbox'),
      end: false,
      badge: inbox || undefined,
      badgeLabel: t('plan.badge_inbox', { n: inbox }),
      danger: false,
    },
    { to: '/plan/upcoming', label: t('plan.upcoming'), end: false, badge: undefined, badgeLabel: '', danger: false },
    { to: '/plan/calendar', label: t('plan.calendar'), end: false, badge: undefined, badgeLabel: '', danger: false },
  ]
  const secondary = [
    { to: '/plan/projects', label: t('plan.projects') },
    { to: '/plan/areas', label: t('plan.areas') },
    { to: '/plan/resources', label: t('plan.resources') },
    { to: '/plan/time', label: t('plan.time') },
    { to: '/plan/journal', label: t('plan.journal') },
    { to: '/plan/archive', label: t('plan.archive') },
  ]

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between gap-3">
        <h1 className="h1">{t('plan.title')}</h1>
        <div className="flex items-center gap-2">
          <IconButton
            label={t('plan.search')}
            icon={<IconSearch size={20} />}
            variant="secondary"
            onClick={() => navigate('/plan/search')}
          />
          <IconButton label={t('plan.add_task')} icon={<IconPlus size={22} />} variant="primary" onClick={() => quickAdd()} />
        </div>
      </header>

      <TimerBar />

      <nav className="segmented" aria-label={t('plan.views')} data-testid="plan-views">
        {primary.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            // tabs size to their labels (equal widths truncate «Предстоящие» on a
            // 390 px screen); the count floats in the corner instead of taking width
            className={({ isActive }) =>
              clsx(
                'relative flex min-h-tap flex-auto items-center justify-center whitespace-nowrap rounded-xl px-2 text-[13px] font-medium transition-colors',
                isActive
                  ? 'bg-[rgb(var(--bg-elevated))] text-[rgb(var(--text))] shadow-card'
                  : 'text-[rgb(var(--text-muted))]',
              )
            }
          >
            {item.label}
            {item.badge ? (
              <>
                <span
                  aria-hidden="true"
                  className={clsx(
                    'tnum absolute -top-1.5 right-0 min-w-[1.125rem] rounded-pill px-1 text-center text-[10px] font-semibold leading-[1.125rem]',
                    item.danger ? 'bg-rose-500 text-white' : 'bg-[rgb(var(--accent))] text-on-accent',
                  )}
                >
                  {item.badge}
                </span>
                <span className="sr-only">{item.badgeLabel}</span>
              </>
            ) : null}
          </NavLink>
        ))}
      </nav>

      <nav className="scroll-x" aria-label={t('plan.sections')}>
        {secondary.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => clsx('chip min-h-tap shrink-0 justify-center', isActive && 'chip-active')}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <Outlet />
    </div>
  )
}
