/**
 * Settings hub. Sections are separate components further down the file tree to
 * keep this screen a simple, scannable list.
 */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Card, Row, Rows } from '@/components/ui'
import {
  IconBell,
  IconChevronRight,
  IconFootprints,
  IconGlobe,
  IconKey,
  IconPalette,
  IconPhone,
  IconRefresh,
  IconUser,
} from '@/components/ui/icons'
import { useAuth } from '@/features/auth/AuthProvider'

export function SettingsPage() {
  const { t } = useTranslation()
  const { session } = useAuth()

  const sections = [
    { to: '/settings/profile', icon: <IconUser size={18} />, label: t('settings.sections.profile'), hint: session?.settings.target_kcal ? `${session.settings.target_kcal} ${t('common.kcal')}` : t('today.no_target') },
    { to: '/settings/appearance', icon: <IconPalette size={18} />, label: t('settings.sections.appearance') },
    { to: '/settings/notifications', icon: <IconBell size={18} />, label: t('settings.sections.notifications') },
    { to: '/settings/devices', icon: <IconPhone size={18} />, label: t('settings.sections.devices') },
    { to: '/settings/shortcut', icon: <IconFootprints size={18} />, label: t('settings.sections.shortcut'), hint: t('settings.shortcut_hint') },
    { to: '/settings/tokens', icon: <IconKey size={18} />, label: t('settings.sections.tokens') },
    { to: '/settings/data', icon: <IconGlobe size={18} />, label: t('settings.sections.data') },
    { to: '/settings/integrations', icon: <IconRefresh size={18} />, label: t('settings.sections.integrations'), hint: t('settings.integrations_hint') },
  ]

  return (
    <div className="space-y-4">
      <h1 className="h1">{t('settings.title')}</h1>

      <Rows>
        {sections.map((section) => (
          <Link key={section.to} to={section.to} className="block">
            <Row
              icon={section.icon}
              label={section.label}
              hint={section.hint}
              trailing={<IconChevronRight size={18} className="text-[rgb(var(--text-muted))]" />}
            />
          </Link>
        ))}
      </Rows>

      <Card className="space-y-2">
        <p className="label">{t('settings.sections.about')}</p>
        <div className="flex items-center justify-between text-sm">
          <span className="muted">{t('settings.about_env')}</span>
          <span className="tnum">{session?.user.display_name}</span>
        </div>
      </Card>
    </div>
  )
}
