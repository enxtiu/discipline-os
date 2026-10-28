/**
 * i18n bootstrap. Russian is the default and the fallback.
 *
 * The initial language is picked in this order:
 *   1. the language stored on the device (localStorage),
 *   2. the browser language,
 *   3. `ru`.
 * Once the device is paired, the server-side `settings.language` wins (the
 * settings screen writes both).
 *
 * Stage 9 (performance): only the active language is downloaded. Each locale is
 * its own chunk, read through a tiny i18next backend; `main.tsx` renders once
 * `i18nReady` has settled (no flash of keys), and `changeLanguage` loads the other
 * locale before it switches (i18next waits for the backend). index.html preloads
 * the chunk the device is about to ask for (`localePreload` in vite.config.ts), so
 * it downloads in parallel with the app code.
 */
import i18n, { type BackendModule } from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

export const SUPPORTED_LANGUAGES = ['ru', 'en'] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]

export const LANGUAGE_STORAGE_KEY = 'dsos.language'

export function isSupported(value: string | null | undefined): value is Language {
  return !!value && (SUPPORTED_LANGUAGES as readonly string[]).includes(value)
}

export function currentLanguage(): Language {
  const value = i18n.resolvedLanguage ?? i18n.language
  return isSupported(value) ? value : 'ru'
}

/** One chunk per locale (the file names are matched by `localePreload` in vite.config.ts). */
const LOCALES: Record<Language, () => Promise<{ default: Record<string, unknown> }>> = {
  ru: () => import('./locales/ru.json'),
  en: () => import('./locales/en.json'),
}

const localeBackend: BackendModule = {
  type: 'backend',
  init() {},
  read(language, _namespace, callback) {
    const load = isSupported(language) ? LOCALES[language] : null
    if (!load) return callback(null, {})
    load().then(
      (module) => callback(null, module.default),
      (error: unknown) => callback(error instanceof Error ? error : new Error(String(error)), false),
    )
  },
}

// Russian is the fallback for every other language; English is complete
// (scripts/check-i18n.mjs), so it never downloads the Russian chunk as well.
const fallbackLng = { en: [], default: ['ru'] }

/** Settles once the active language is loaded (or failed to load: render anyway). */
export const i18nReady: Promise<unknown> = i18n
  .use(localeBackend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng,
    supportedLngs: SUPPORTED_LANGUAGES,
    nonExplicitSupportedLngs: true,
    load: 'languageOnly',
    interpolation: { escapeValue: false },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ['localStorage'],
    },
    returnNull: false,
  })
  .catch(() => undefined)

export default i18n
