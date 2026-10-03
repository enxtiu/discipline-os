/** Build metadata injected at bundle time (vite `define`/env). */
export const APP_VERSION = (import.meta.env.VITE_APP_VERSION as string | undefined) ?? 'dev'
export const APP_ENV = (import.meta.env.MODE as string | undefined) ?? 'development'
export const IS_DEV = import.meta.env.DEV
export const IS_PROD = import.meta.env.PROD
