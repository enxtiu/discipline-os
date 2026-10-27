/**
 * `<html lang>` follows the UI language, so screen readers pick the right
 * voice and pronunciation (VoiceOver reads Russian text with an English voice
 * otherwise) and the browser hyphenates and spell-checks in that language.
 */
import i18n from 'i18next'

function apply(language: string | undefined): void {
  if (typeof document === 'undefined') return
  const lang = (language ?? '').slice(0, 2).toLowerCase()
  document.documentElement.lang = lang === 'en' ? 'en' : 'ru'
}

export function syncDocumentLanguage(): void {
  apply(i18n.resolvedLanguage ?? i18n.language)
  i18n.on('languageChanged', apply)
}
