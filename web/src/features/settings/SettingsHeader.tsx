/** Small back-navigation header used by the settings sub-pages. */
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { IconButton } from '@/components/ui'
import { IconChevronLeft } from '@/components/ui/icons'

export function SettingsHeader({ title }: { title: string }) {
  const navigate = useNavigate()
  const { t } = useTranslation()
  return (
    <div className="mb-3 flex items-center gap-3">
      <IconButton
        label={t('app.back')}
        icon={<IconChevronLeft size={20} />}
        variant="secondary"
        className="h-9 w-9 min-h-9 rounded-xl"
        onClick={() => navigate(-1)}
      />
      <span className="text-sm muted">{title}</span>
    </div>
  )
}
