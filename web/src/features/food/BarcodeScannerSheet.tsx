/**
 * Barcode scanning with the camera (stage 3).
 *
 * Uses @zxing/browser on a <video> preview — Safari has no BarcodeDetector,
 * zxing works everywhere getUserMedia does. EAN-13/EAN-8/UPC only (what food
 * packaging carries), which keeps decoding fast on a phone.
 *
 * A found code goes to `GET /v1/foods/barcode/{code}`: local library first,
 * then one live Open Food Facts lookup. If the camera is unavailable (denied,
 * desktop without camera) the same lookup works with typed digits.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { BrowserMultiFormatReader, type IScannerControls } from '@zxing/browser'
import { BarcodeFormat, DecodeHintType } from '@zxing/library'

import { Button, Callout, Field, Input, Sheet } from '@/components/ui'
import type { Food, FoodBarcode } from '@/api/types'
import { endpoints } from '@/api/client'
import { errorMessage } from '@/lib/errors'

type ScanState =
  | { kind: 'scanning' }
  | { kind: 'looking'; code: string }
  | { kind: 'miss'; code: string }
  | { kind: 'unavailable'; code: string }
  | { kind: 'camera_error' }

const HINTS = new Map<DecodeHintType, unknown>([
  [DecodeHintType.POSSIBLE_FORMATS, [BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A, BarcodeFormat.UPC_E]],
])

export default function BarcodeScannerSheet({
  open,
  onClose,
  onFound,
  onCreateFood,
}: {
  open: boolean
  onClose: () => void
  onFound: (food: Food, code: string) => void
  onCreateFood: (code: string) => void
}) {
  const { t } = useTranslation()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const controlsRef = useRef<IScannerControls | null>(null)
  const busyRef = useRef(false)
  // bumped by every stop: a start still waiting for the camera sees it and lets go
  const sessionRef = useRef(0)
  // latest callbacks without restarting the camera when the parent re-renders
  const onFoundRef = useRef(onFound)
  const onCreateFoodRef = useRef(onCreateFood)
  useEffect(() => {
    onFoundRef.current = onFound
    onCreateFoodRef.current = onCreateFood
  })
  const [state, setState] = useState<ScanState>({ kind: 'scanning' })
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)

  const stopCamera = useCallback(() => {
    sessionRef.current += 1
    controlsRef.current?.stop()
    controlsRef.current = null
  }, [])

  const lookup = useCallback(
    async (code: string) => {
      if (busyRef.current) return
      busyRef.current = true
      setError(null)
      setState({ kind: 'looking', code })
      try {
        const result: FoodBarcode = await endpoints.barcodeFood(code)
        if (result.food) {
          stopCamera()
          onFoundRef.current(result.food, result.barcode)
          return
        }
        if (result.lookup === 'invalid') {
          setState({ kind: 'scanning' }) // misread: keep scanning
        } else {
          stopCamera()
          setState({ kind: result.lookup === 'unavailable' ? 'unavailable' : 'miss', code: result.barcode })
        }
      } catch (caught) {
        setError(errorMessage(caught))
        setState({ kind: 'scanning' })
      } finally {
        busyRef.current = false
      }
    },
    [stopCamera],
  )

  const startCamera = useCallback(async () => {
    stopCamera()
    const session = sessionRef.current
    setState({ kind: 'scanning' })
    if (!videoRef.current || !navigator.mediaDevices?.getUserMedia) {
      setState({ kind: 'camera_error' })
      return
    }
    try {
      const reader = new BrowserMultiFormatReader(HINTS, { delayBetweenScanAttempts: 150 })
      const controls = await reader.decodeFromConstraints(
        { video: { facingMode: { ideal: 'environment' } }, audio: false },
        videoRef.current,
        (result) => {
          if (result && !busyRef.current) void lookup(result.getText())
        },
      )
      if (session !== sessionRef.current) {
        controls.stop() // the sheet closed (or restarted) while the camera was opening
        return
      }
      controlsRef.current = controls
    } catch {
      if (session === sessionRef.current) setState({ kind: 'camera_error' })
    }
  }, [lookup, stopCamera])

  useEffect(() => {
    if (!open) {
      stopCamera()
      return
    }
    setManual('')
    setError(null)
    // the <video> mounts with the sheet; start on the next frame
    const handle = window.requestAnimationFrame(() => void startCamera())
    return () => {
      window.cancelAnimationFrame(handle)
      stopCamera()
    }
  }, [open, startCamera, stopCamera])

  const digits = manual.replace(/\D/g, '')

  return (
    <Sheet open={open} onClose={onClose} title={t('scan.title')} size="tall">
      <div className="space-y-4 pb-2">
        {/* the <video> stays mounted so "scan again" can restart the camera at once */}
        <div
          className={clsx(
            'relative overflow-hidden rounded-2xl bg-black',
            (state.kind === 'camera_error' || state.kind === 'miss' || state.kind === 'unavailable') && 'hidden',
          )}
          style={{ aspectRatio: '4 / 3' }}
        >
            <video ref={videoRef} className="h-full w-full object-cover" muted playsInline autoPlay data-testid="scan-video" />
            <div className="pointer-events-none absolute inset-x-8 top-1/2 h-24 -translate-y-1/2 rounded-xl border-2 border-[rgb(var(--accent))]/80" />
            <p className="absolute inset-x-0 bottom-0 bg-black/55 px-3 py-2 text-center text-xs text-white" aria-live="polite">
            {state.kind === 'looking' ? t('scan.looking', { code: state.code }) : t('scan.hint')}
          </p>
        </div>

        {state.kind === 'camera_error' && <Callout tone="warn">{t('scan.camera_error')}</Callout>}
        {state.kind === 'miss' && (
          <Callout tone="info" title={t('scan.miss_title', { code: state.code })}>
            {t('scan.miss_hint')}
          </Callout>
        )}
        {state.kind === 'unavailable' && <Callout tone="warn">{t('scan.unavailable')}</Callout>}
        {error && <Callout tone="danger">{error}</Callout>}

        {(state.kind === 'miss' || state.kind === 'unavailable') && (
          <div className="flex flex-wrap gap-2">
            {state.kind === 'miss' && <Button onClick={() => onCreateFoodRef.current(state.code)}>{t('scan.create_food')}</Button>}
            <Button variant="secondary" onClick={() => void startCamera()}>
              {t('scan.again')}
            </Button>
          </div>
        )}

        <Field label={t('scan.manual_label')} htmlFor="barcode-manual">
          <div className="flex gap-2">
            <Input
              id="barcode-manual"
              inputMode="numeric"
              autoComplete="off"
              value={manual}
              onChange={(event) => setManual(event.target.value)}
              placeholder="4600000000000"
              className="tnum"
            />
            <Button variant="secondary" disabled={digits.length < 8} onClick={() => void lookup(digits)}>
              {t('scan.find')}
            </Button>
          </div>
        </Field>
      </div>
    </Sheet>
  )
}
