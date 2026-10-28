/**
 * Files from authenticated endpoints (stage 8 export): `fetch` with the
 * device token, the body read as a stream (so the screen can show progress —
 * the server streams, so there is usually no `Content-Length`), a `Blob`, and
 * then one of two ways to hand it over:
 *
 *  - everywhere: a temporary `<a download>` with an object URL;
 *  - an installed iPhone app (home-screen PWA), where a blob download opens a
 *    viewer with no way back: the share sheet (`navigator.share({files})`,
 *    «Сохранить в Файлы»), feature-detected — desktop never takes this path.
 *
 * The filename is the server's (`Content-Disposition`); errors come back as
 * `ApiError` with the server's code (`rate_limited` on a 429), a lost
 * connection as status 0 (`network`), like `api/client.ts`.
 */
import { ApiError, UNAUTHORIZED_EVENT } from '@/api/client'
import { detectPlatform, getToken } from '@/lib/device'
import { apiPath } from '@/lib/url'

export interface DownloadProgress {
  received: number
  /** null while the server streams without a length */
  total: number | null
}

export interface DownloadedFile {
  blob: Blob
  filename: string
  size: number
}

/** `attachment; filename="disciplineos-2026-10-27.zip"` (or RFC 5987 `filename*=`) → the name. */
export function filenameFrom(header: string | null): string | null {
  if (!header) return null
  const encoded = header.match(/filename\*\s*=\s*(?:UTF-8'[^']*')?("?)([^";]+)\1/i)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[2].trim())
    } catch {
      /* fall through to the plain name */
    }
  }
  const plain = header.match(/filename\s*=\s*("?)([^";]+)\1/i)
  return plain ? plain[2].trim() : null
}

export async function fetchFile(
  path: string,
  options: { signal?: AbortSignal; onProgress?: (progress: DownloadProgress) => void; fallbackName: string },
): Promise<DownloadedFile> {
  const token = getToken()
  let response: Response
  try {
    response = await fetch(apiPath(path), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: 'omit',
      signal: options.signal,
    })
  } catch (cause) {
    if ((cause as Error)?.name === 'AbortError') throw cause
    throw new ApiError(0, { code: 'network', message: 'Cannot reach the server' })
  }

  if (!response.ok) {
    let envelope: { code: string; message: string; details?: Record<string, unknown> | null } | undefined
    try {
      envelope = ((await response.json()) as { error?: typeof envelope })?.error
    } catch {
      envelope = undefined
    }
    const error = new ApiError(
      response.status,
      envelope ?? { code: response.status === 429 ? 'rate_limited' : 'internal_error', message: response.statusText || 'Download failed' },
    )
    if (error.isUnauthorized) window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: error }))
    throw error
  }

  const total = Number(response.headers.get('Content-Length')) || null
  const type = response.headers.get('Content-Type') ?? 'application/octet-stream'
  const filename = filenameFrom(response.headers.get('Content-Disposition')) ?? options.fallbackName
  const parts: Uint8Array[] = []
  let received = 0
  if (response.body && typeof response.body.getReader === 'function') {
    const reader = response.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      if (value) {
        parts.push(value)
        received += value.byteLength
        options.onProgress?.({ received, total })
      }
    }
  } else {
    const buffer = new Uint8Array(await response.arrayBuffer())
    parts.push(buffer)
    received = buffer.byteLength
    options.onProgress?.({ received, total })
  }
  const blob = new Blob(parts, { type })
  return { blob, filename, size: blob.size }
}

/** The ordinary download: a temporary `<a download>` (the object URL is released a minute later). */
export function saveFile(file: DownloadedFile): void {
  const url = URL.createObjectURL(file.blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = file.filename
  anchor.rel = 'noopener'
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** The app runs from the iPhone/iPad home screen (not a Safari tab). */
export function isIosStandalone(): boolean {
  const standalone =
    window.matchMedia?.('(display-mode: standalone)').matches === true ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  return standalone && detectPlatform() === 'ios'
}

/** A `File` the share sheet accepts, or null when this browser cannot share files. */
export function shareableFile(file: DownloadedFile): File | null {
  if (typeof navigator.share !== 'function' || typeof navigator.canShare !== 'function' || typeof File === 'undefined') {
    return null
  }
  const shared = new File([file.blob], file.filename, { type: file.blob.type || 'application/octet-stream' })
  try {
    return navigator.canShare({ files: [shared] }) ? shared : null
  } catch {
    return null
  }
}

export type ShareOutcome = 'shared' | 'cancelled' | 'needs-tap' | 'failed'

/**
 * Opens the share sheet. iOS only allows it right after a tap: when the
 * download took longer than that grace, the answer is `needs-tap` and the
 * screen offers a button that shares the file already in memory.
 */
export async function shareFile(file: File): Promise<ShareOutcome> {
  try {
    await navigator.share({ files: [file], title: file.name })
    return 'shared'
  } catch (error) {
    const name = (error as Error)?.name
    if (name === 'AbortError') return 'cancelled'
    if (name === 'NotAllowedError') return 'needs-tap'
    return 'failed'
  }
}
