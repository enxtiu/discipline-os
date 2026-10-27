/**
 * Live quick-add preview: the server parses the line (`POST /parse`, no write)
 * ≈250 ms after the last keystroke; a newer keystroke aborts the older request.
 * Highlights use the token offsets the server returns (UTF-16 = `String.slice`).
 */
import { useEffect, useState } from 'react'

import { isOffline } from '@/lib/errors'
import { planApi } from '../api'
import type { ParseResult, ParseToken, TokenKind } from '../types'

export interface ParsePreview {
  /** The text this result was computed for (may lag one keystroke behind). */
  text: string
  result: ParseResult
}

export type ParseStatus = 'idle' | 'loading' | 'offline' | 'error'

export function useParsePreview(text: string, enabled: boolean, delayMs = 250) {
  const [preview, setPreview] = useState<ParsePreview | null>(null)
  const [status, setStatus] = useState<ParseStatus>('idle')

  useEffect(() => {
    const value = text
    if (!enabled || !value.trim()) {
      setPreview(null)
      setStatus('idle')
      return
    }
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      setStatus('offline')
      return
    }
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      setStatus('loading')
      planApi
        .parse(value, undefined, controller.signal)
        .then((result) => {
          setPreview({ text: value, result })
          setStatus('idle')
        })
        .catch((error: unknown) => {
          if ((error as Error | null)?.name === 'AbortError') return
          setStatus(isOffline(error) ? 'offline' : 'error')
        })
    }, delayMs)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
  }, [text, enabled, delayMs])

  return { preview, status }
}

export interface Segment {
  text: string
  kind?: TokenKind
}

/**
 * Split `text` into plain and token segments. Tokens computed for an older
 * text are kept only where that stretch is unchanged, so typing at the end of
 * the line does not make the highlights flicker.
 */
export function highlightSegments(text: string, preview: ParsePreview | null): Segment[] {
  if (!preview || !text) return text ? [{ text }] : []
  const exact = preview.text === text
  const tokens = preview.result.tokens
    .filter((token: ParseToken) => token.start >= 0 && token.end <= text.length && token.start < token.end)
    .filter(
      (token) =>
        exact ||
        text.slice(token.start, token.end).toLowerCase() === preview.text.slice(token.start, token.end).toLowerCase(),
    )
    .sort((a, b) => a.start - b.start)
  const out: Segment[] = []
  let position = 0
  for (const token of tokens) {
    if (token.start < position) continue // overlapping token: keep the first
    if (token.start > position) out.push({ text: text.slice(position, token.start) })
    out.push({ text: text.slice(token.start, token.end), kind: token.kind })
    position = token.end
  }
  if (position < text.length) out.push({ text: text.slice(position) })
  return out
}
