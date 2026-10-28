/**
 * The browser tab / window title names the current section («План · DisciplineOS»),
 * so a screen reader announces where a navigation landed and the history list
 * tells the screens apart (WCAG 2.4.2).
 */
import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

const SECTIONS: [prefix: string, key: string][] = [
  ['/today', 'nav.today'],
  ['/food', 'nav.food'],
  ['/train', 'nav.train'],
  ['/health', 'health.title'],
  ['/plan', 'nav.plan'],
  ['/stats', 'nav.stats'],
  ['/settings', 'nav.settings'],
]

export function useDocumentTitle(): void {
  const { pathname } = useLocation()
  const { t, i18n } = useTranslation()
  useEffect(() => {
    const section = SECTIONS.find(([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`))
    document.title = section ? `${t(section[1])} · DisciplineOS` : 'DisciplineOS'
  }, [pathname, t, i18n.resolvedLanguage])
}
