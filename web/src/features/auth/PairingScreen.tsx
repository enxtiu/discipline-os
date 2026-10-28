/**
 * The only screen reachable without a device token.
 *
 * Deliberately minimal, single-purpose and thumb-reachable: one field, one
 * button, plus a collapsible "how do I get a code" explainer for the server
 * command. Auto-focuses the field and normalises what the user types
 * (lowercase, spaces, missing dash).
 */
import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { useTranslation } from 'react-i18next'

import { Button, Callout, CheckList, Field, Input } from '@/components/ui'
import { IconChevronRight, IconInfo, IconKey, IconShield } from '@/components/ui/icons'
import { useAuth } from './AuthProvider'
import { errorMessage } from '@/lib/errors'
import { suggestDeviceName } from '@/lib/device'
import { APP_VERSION } from '@/lib/build'

export function PairingScreen() {
  const { t } = useTranslation()
  const { pair, isPairing, pairingError } = useAuth()

  const [code, setCode] = useState('')
  const [deviceName, setDeviceName] = useState(() => suggestDeviceName())
  const [showHelp, setShowHelp] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const timeout = window.setTimeout(() => inputRef.current?.focus(), 350)
    return () => window.clearTimeout(timeout)
  }, [])

  const normalized = formatCode(code)
  const isValid = normalized.replace('-', '').length === 8

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!isValid || isPairing) return
    try {
      await pair({ code: normalized, deviceName })
    } catch {
      /* the error is rendered from pairingError */
    }
  }

  return (
    <div className="relative flex min-h-[100dvh] flex-col justify-between px-5 pb-8 pt-14">
      {/* Ambient glow — the only decorative element on this screen. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-72 opacity-70"
        style={{
          background:
            'radial-gradient(60% 100% at 50% 0%, rgb(56 224 200 / .18) 0%, rgb(139 92 246 / .10) 45%, transparent 75%)',
        }}
      />

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        className="relative z-10 mx-auto w-full max-w-md"
      >
        <div className="mb-8 flex flex-col items-center gap-4">
          <LogoMark />
          <div className="text-center">
            <h1 className="h1">{t('auth.pairing_title')}</h1>
            <p className="mt-1.5 text-sm muted">{t('auth.pairing_subtitle')}</p>
          </div>
        </div>

        <form onSubmit={submit} className="flex flex-col gap-5">
          <Field label={t('auth.code_label')} htmlFor="pairing-code">
            <Input
              ref={inputRef}
              id="pairing-code"
              value={code}
              onChange={(event) => setCode(event.target.value.toUpperCase())}
              onBlur={() => setCode((current) => formatCode(current))}
              placeholder={t('auth.code_placeholder')}
              autoComplete="one-time-code"
              autoCapitalize="characters"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="go"
              inputMode="text"
              className="text-center font-stat text-2xl tracking-[0.35em] uppercase"
            />
          </Field>

          {pairingError && (
            <Callout tone="danger">
              {errorMessage(pairingError)}
              {pairingError.details?.limit !== undefined && (
                <span className="mt-1 block text-xs">
                  {t('auth.device_limit', {
                    active: String(pairingError.details.active ?? '?'),
                    limit: String(pairingError.details.limit),
                  })}
                </span>
              )}
            </Callout>
          )}

          <Button type="submit" block loading={isPairing} disabled={!isValid}>
            {isPairing ? t('auth.pairing') : t('auth.pair_button')}
          </Button>

          <button
            type="button"
            onClick={() => setShowAdvanced((value) => !value)}
            aria-expanded={showAdvanced}
            className="mx-auto -my-2 min-h-tap rounded-xl px-3 text-xs muted underline-offset-4 hover:underline"
          >
            {showAdvanced ? t('app.close') : `${t('auth.device_name')}: ${deviceName}`}
          </button>

          {showAdvanced && (
            <Field label={t('auth.device_name')} htmlFor="device-name">
              <Input
                id="device-name"
                value={deviceName}
                onChange={(event) => setDeviceName(event.target.value)}
                maxLength={60}
              />
            </Field>
          )}
        </form>

        <div className="mt-6">
          <button
            type="button"
            onClick={() => setShowHelp((value) => !value)}
            aria-expanded={showHelp}
            className="flex min-h-tap w-full items-center justify-between rounded-2xl border border-[rgb(var(--border))] px-4 py-3 text-left text-sm"
          >
            <span className="flex items-center gap-2">
              <span className="text-[rgb(var(--text-muted))]">
                <IconKey size={18} />
              </span>
              {t('auth.how_to_get_code')}
            </span>
            <IconChevronRight
              size={18}
              className={showHelp ? 'rotate-90 transition-transform' : 'transition-transform'}
            />
          </button>

          {showHelp && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              className="overflow-hidden"
            >
              <div className="mt-3 space-y-3">
                <CheckList items={(t('auth.how_to_steps', { returnObjects: true }) as string[]) ?? []} />
                <Callout tone="info" title={<span>{t('auth.privacy_note')}</span>}>
                  <span className="flex items-center gap-2 text-xs">
                    <IconShield size={14} /> token → localStorage → Bearer header
                  </span>
                </Callout>
              </div>
            </motion.div>
          )}
        </div>
      </motion.div>

      <footer className="relative z-10 mx-auto mt-10 w-full max-w-md text-center text-xs muted">
        <p className="flex items-center justify-center gap-1.5">
          <IconInfo size={13} /> v{APP_VERSION}
        </p>
      </footer>
    </div>
  )
}

/** `ab12cd34` / `AB12 CD34` / `AB12CD34` -> `AB12-CD34` */
export function formatCode(raw: string): string {
  const clean = raw
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '')
    .slice(0, 8)
  if (clean.length <= 4) return clean
  return `${clean.slice(0, 4)}-${clean.slice(4)}`
}

function LogoMark() {
  return (
    <div className="relative">
      <div
        aria-hidden
        className="absolute inset-0 rounded-[22px] blur-xl"
        style={{ background: 'radial-gradient(circle, rgb(56 224 200 / .35), transparent 70%)' }}
      />
      <svg width="72" height="72" viewBox="0 0 64 64" className="relative" aria-hidden="true">
        <rect width="64" height="64" rx="15" fill="#0B1220" stroke="rgba(56,224,200,.35)" />
        <circle cx="32" cy="32" r="18" fill="none" stroke="#1F2C45" strokeWidth="5" />
        <circle
          cx="32"
          cy="32"
          r="18"
          fill="none"
          stroke="#38E0C8"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray="79.2 113.1"
          transform="rotate(-90 32 32)"
          style={{ filter: 'drop-shadow(0 0 6px rgba(56,224,200,.6))' }}
        />
        <circle
          cx="32"
          cy="32"
          r="18"
          fill="none"
          stroke="#8B5CF6"
          strokeWidth="5"
          strokeOpacity=".55"
          strokeLinecap="round"
          strokeDasharray="33.9 158.4"
          strokeDashoffset="-79.2"
          transform="rotate(-90 32 32)"
        />
        <circle cx="32" cy="14" r="4" fill="#FBBF24" />
      </svg>
    </div>
  )
}
