/**
 * One place that owns the stage-3 capture flows, mounted once in the app shell
 * so every entry point (quick-add sheet, Today, Food page, deep links) opens the
 * same thing:
 *
 *   photo   → hidden <input type=file capture> → downscale → analyse → review
 *   text    → text sheet (keyboard dictation works) → parse → review
 *   barcode → camera scanner → log sheet, or "create my food" with the code
 *
 * The photo input is triggered through <label htmlFor={PHOTO_INPUT_ID}>: iOS
 * Safari only opens the camera from a direct user gesture, which a label tap
 * is (a programmatic .click() after navigation is not).
 */
import { createContext, lazy, Suspense, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Callout, Field, Sheet, Spinner } from '@/components/ui'
import { useToast } from '@/components/ui/Toast'
import type { AiAnalysis, Food } from '@/api/types'
import { errorMessage } from '@/lib/errors'
import { useAuth } from '@/features/auth/AuthProvider'
import { CustomFoodSheet } from '@/features/food/CustomFoodSheet'
import { LogSheet } from '@/features/food/LogSheet'
import { AiReviewSheet } from './AiReviewSheet'
import { useAnalyzePhoto, useAppConfig, useParseText } from './useAi'

// zxing is ~400 KB: load it only when the scanner opens
const BarcodeScannerSheet = lazy(() => import('@/features/food/BarcodeScannerSheet'))

export const PHOTO_INPUT_ID = 'ai-photo-input'

interface AiCaptureApi {
  openText: () => void
  openBarcode: () => void
  /** for deep links; on iOS prefer a <label htmlFor={PHOTO_INPUT_ID}> */
  openPhoto: () => void
  aiAvailable: boolean
}

const AiCaptureContext = createContext<AiCaptureApi | null>(null)

export function useAiCapture(): AiCaptureApi {
  const value = useContext(AiCaptureContext)
  if (!value) throw new Error('useAiCapture outside AiCaptureProvider')
  return value
}

export function AiCaptureProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const toast = useToast()
  const { session } = useAuth()
  const config = useAppConfig()
  const analyzePhoto = useAnalyzePhoto()
  const parseText = useParseText()
  const inputRef = useRef<HTMLInputElement>(null)

  const [analysis, setAnalysis] = useState<AiAnalysis | null>(null)
  const [busy, setBusy] = useState<'photo' | null>(null)
  const [textOpen, setTextOpen] = useState(false)
  const [text, setText] = useState('')
  const [scanOpen, setScanOpen] = useState(false)
  const [scanned, setScanned] = useState<Food | null>(null)
  const [unknownCode, setUnknownCode] = useState<string | null>(null)

  const aiAvailable = config.data ? Boolean(config.data.features?.ai_meal_photo) : true

  const onPhoto = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = '' // the same photo can be picked again
    if (!file) return
    setBusy('photo')
    analyzePhoto.mutate(
      { file, keepThumbnail: !!session?.settings.keep_photo_thumbnail },
      {
        onSuccess: (result) => setAnalysis(result),
        onError: (error) => toast.error(errorMessage(error)),
        onSettled: () => setBusy(null),
      },
    )
  }

  const submitText = () => {
    const value = text.trim()
    if (!value) return
    parseText.mutate(
      { text: value },
      {
        onSuccess: (result) => {
          setTextOpen(false)
          setText('')
          setAnalysis(result)
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  const openPhoto = useCallback(() => inputRef.current?.click(), [])
  const openText = useCallback(() => setTextOpen(true), [])
  const openBarcode = useCallback(() => setScanOpen(true), [])
  const api = useMemo(
    () => ({ openPhoto, openText, openBarcode, aiAvailable }),
    [openPhoto, openText, openBarcode, aiAvailable],
  )

  return (
    <AiCaptureContext.Provider value={api}>
      {children}

      <input
        ref={inputRef}
        id={PHOTO_INPUT_ID}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={onPhoto}
        data-testid="ai-photo-input"
      />

      <Sheet open={busy === 'photo'} onClose={() => undefined} title={t('ai.analyzing_title')}>
        <div className="flex flex-col items-center gap-3 py-8 text-center" role="status" aria-live="polite">
          <Spinner size={32} />
          <p className="text-sm muted">{t('ai.analyzing_hint')}</p>
        </div>
      </Sheet>

      <Sheet open={textOpen} onClose={() => setTextOpen(false)} title={t('ai.text_title')}>
        <div className="space-y-4 pb-2">
          {!aiAvailable && <Callout tone="warn">{t('errors.ai_disabled')}</Callout>}
          <Field label={t('ai.text_label')} htmlFor="ai-text" hint={t('ai.text_hint')}>
            <textarea
              id="ai-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={4}
              maxLength={1000}
              placeholder={t('ai.text_placeholder')}
              className="input min-h-[7rem] resize-none leading-relaxed"
            />
          </Field>
          <Button block loading={parseText.isPending} onClick={submitText} disabled={!text.trim() || !aiAvailable}>
            {t('ai.text_submit')}
          </Button>
        </div>
      </Sheet>

      <AiReviewSheet analysis={analysis} onClose={() => setAnalysis(null)} />

      {scanOpen && (
        <Suspense fallback={null}>
          <BarcodeScannerSheet
            open={scanOpen}
            onClose={() => setScanOpen(false)}
            onFound={(food) => {
              setScanOpen(false)
              setScanned(food)
            }}
            onCreateFood={(code) => {
              setScanOpen(false)
              setUnknownCode(code)
            }}
          />
        </Suspense>
      )}
      <LogSheet
        target={scanned ? { kind: 'food', food: scanned } : null}
        onClose={() => setScanned(null)}
        source="barcode"
      />
      <CustomFoodSheet
        open={unknownCode !== null}
        barcode={unknownCode ?? undefined}
        onClose={() => setUnknownCode(null)}
        onCreated={(food) => setScanned(food)}
      />
    </AiCaptureContext.Provider>
  )
}

/** Anything that should open the camera: renders as a label for the hidden input. */
export function PhotoTrigger({
  className,
  children,
  onTrigger,
}: {
  className?: string
  children: ReactNode
  onTrigger?: () => void
}) {
  return (
    <label
      htmlFor={PHOTO_INPUT_ID}
      className={className}
      onClick={onTrigger}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onTrigger?.()
          document.getElementById(PHOTO_INPUT_ID)?.click()
        }
      }}
    >
      {children}
    </label>
  )
}
