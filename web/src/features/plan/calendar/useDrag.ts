/**
 * One drag hook for the calendar: time-grid blocks, their resize handles and
 * month chips.
 *
 * * **Mouse / pen** (Pointer Events): the drag starts as soon as the pointer
 *   moves a few pixels with the button held; a press without movement stays a
 *   plain click. Escape cancels.
 * * **Touch** (Touch Events, iOS Safari): a long-press (≈ 300 ms) picks the
 *   item up. Moving before that cancels the pick-up and the page scrolls as
 *   usual. Once picked up, every `touchmove` is cancelled so the page does not
 *   scroll under the finger. iOS Safari only honours `preventDefault()` from a
 *   non-passive `touchmove` listener that already existed when the touch
 *   began, so one window listener lives as long as the hook and decides per
 *   event from the drag state.
 * * A drop never also counts as a tap (which would open the editor or a new
 *   task): a touch drop cancels its `touchend`, which suppresses the
 *   synthetic click; after a mouse drop the click that follows `mouseup` (on
 *   the common ancestor of where the press started and ended) is swallowed.
 * * Touch events keep targeting the element the touch started on, so that
 *   element must stay mounted until the finger lifts: consumers leave it in
 *   place (dimmed) and draw a separate preview where it would land.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, TouchEvent as ReactTouchEvent } from 'react'

export interface DragPoint {
  x: number
  y: number
}

export type DragSource = 'mouse' | 'touch'

export interface DragOptions<T> {
  /** Picked up (after the long-press on touch, after a few pixels with a mouse). Return `false` to refuse. */
  onStart?: (item: T, start: DragPoint, source: DragSource) => boolean | void
  /** Pointer moved while dragging (client coordinates). Called once right after `onStart`, too. */
  onMove: (item: T, point: DragPoint) => void
  /** Released over `point`. */
  onDrop: (item: T, point: DragPoint) => void
  /** Escape, `touchcancel`/`pointercancel`, or the hook unmounted mid-drag. */
  onCancel?: (item: T) => void
  /** Touch hold before the pick-up (ms). */
  longPressMs?: number
  /** Touch movement (px) during the hold that means "scroll", not "drag". */
  touchSlop?: number
  /** Mouse movement (px) that turns a press into a drag. */
  mouseSlop?: number
}

/** Literal classes for a draggable surface: no text selection or iOS callout on long-press. */
export const DRAG_SURFACE = 'select-none [-webkit-touch-callout:none]'

interface DragState<T> {
  phase: 'idle' | 'pending' | 'dragging'
  source: DragSource | null
  item: T | null
  start: DragPoint
  last: DragPoint
  touchId: number | null
  pointerId: number | null
  timer: number | null
}

const ORIGIN: DragPoint = { x: 0, y: 0 }

function idle<T>(): DragState<T> {
  return { phase: 'idle', source: null, item: null, start: ORIGIN, last: ORIGIN, touchId: null, pointerId: null, timer: null }
}

function distance(a: DragPoint, b: DragPoint): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function findTouch(list: TouchList, id: number | null): Touch | null {
  for (let index = 0; index < list.length; index += 1) {
    const touch = list.item(index)
    if (touch && touch.identifier === id) return touch
  }
  return null
}

/** Swallow the click that follows a mouse drop (capture phase, once, within 250 ms). */
function swallowNextClick(): void {
  const swallow = (event: MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    remove()
  }
  const timeout = window.setTimeout(() => remove(), 250)
  const remove = () => {
    window.clearTimeout(timeout)
    window.removeEventListener('click', swallow, true)
  }
  window.addEventListener('click', swallow, true)
}

export interface DragBindings {
  onPointerDown: (event: ReactPointerEvent) => void
  onTouchStart: (event: ReactTouchEvent) => void
  onContextMenu: (event: ReactMouseEvent) => void
}

export function useDrag<T>(options: DragOptions<T>) {
  const optionsRef = useRef(options)
  useEffect(() => {
    optionsRef.current = options
  })
  const stateRef = useRef<DragState<T>>(idle<T>())
  const detachMouseRef = useRef<(() => void) | null>(null)
  const [dragging, setDragging] = useState<T | null>(null)

  // Stable controller: only refs and the state setter, safe in window listeners.
  const controllerRef = useRef<{
    reset: () => void
    activate: (point: DragPoint) => void
    finish: (point: DragPoint, cancelled: boolean) => void
  } | null>(null)
  if (controllerRef.current === null) {
    const reset = () => {
      const state = stateRef.current
      if (state.timer !== null) window.clearTimeout(state.timer)
      detachMouseRef.current?.()
      detachMouseRef.current = null
      stateRef.current = idle<T>()
      setDragging(null)
    }
    const activate = (point: DragPoint) => {
      const state = stateRef.current
      if (state.item === null || state.source === null) return
      const accepted = optionsRef.current.onStart?.(state.item, state.start, state.source)
      if (accepted === false) {
        reset()
        return
      }
      state.phase = 'dragging'
      state.last = point
      setDragging(state.item)
      if (state.source === 'touch') navigator.vibrate?.(10)
      optionsRef.current.onMove(state.item, point)
    }
    const finish = (point: DragPoint, cancelled: boolean) => {
      const state = stateRef.current
      const item = state.item
      const source = state.source
      const wasDragging = state.phase === 'dragging'
      reset()
      if (!wasDragging || item === null) return
      if (source === 'mouse') swallowNextClick()
      if (cancelled) optionsRef.current.onCancel?.(item)
      else optionsRef.current.onDrop(item, point)
    }
    controllerRef.current = { reset, activate, finish }
  }
  const controller = controllerRef.current

  // Touch: listeners registered for the hook's whole life (non-passive, see above).
  useEffect(() => {
    const onTouchStart = (event: TouchEvent) => {
      // a second finger (pinch-zoom) never drags
      if (event.touches.length > 1 && stateRef.current.phase === 'pending') controller.reset()
    }
    const onTouchMove = (event: TouchEvent) => {
      const state = stateRef.current
      if (state.source !== 'touch' || state.phase === 'idle') return
      const touch = findTouch(event.touches, state.touchId) ?? findTouch(event.changedTouches, state.touchId)
      if (!touch) return
      const point = { x: touch.clientX, y: touch.clientY }
      if (state.phase === 'pending') {
        if (distance(point, state.start) > (optionsRef.current.touchSlop ?? 8)) controller.reset()
        else state.last = point
        return
      }
      if (event.cancelable) event.preventDefault()
      state.last = point
      if (state.item !== null) optionsRef.current.onMove(state.item, point)
    }
    const onTouchEnd = (event: TouchEvent) => {
      const state = stateRef.current
      if (state.source !== 'touch' || state.phase === 'idle') return
      const touch = findTouch(event.changedTouches, state.touchId)
      if (!touch) return // another finger lifted
      // after a drag: no synthetic mouse events / click on the item
      if (state.phase === 'dragging' && event.cancelable) event.preventDefault()
      controller.finish({ x: touch.clientX, y: touch.clientY }, event.type === 'touchcancel')
    }
    window.addEventListener('touchstart', onTouchStart, { passive: true })
    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('touchend', onTouchEnd, { passive: false })
    window.addEventListener('touchcancel', onTouchEnd, { passive: false })
    return () => {
      window.removeEventListener('touchstart', onTouchStart)
      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('touchend', onTouchEnd)
      window.removeEventListener('touchcancel', onTouchEnd)
      const state = stateRef.current
      if (state.phase === 'dragging' && state.item !== null) optionsRef.current.onCancel?.(state.item)
      controller.reset()
    }
  }, [controller])

  const bind = useCallback(
    (item: T): DragBindings => ({
      onPointerDown: (event) => {
        if (event.pointerType === 'touch' || event.button !== 0) return // touch takes the long-press path
        if (stateRef.current.phase !== 'idle') return // a nested handle already claimed it
        event.preventDefault() // no text selection / native drag while moving
        const point = { x: event.clientX, y: event.clientY }
        stateRef.current = { ...idle<T>(), phase: 'pending', source: 'mouse', item, start: point, last: point, pointerId: event.pointerId }

        const onMove = (move: PointerEvent) => {
          const state = stateRef.current
          if (move.pointerId !== state.pointerId) return
          const next = { x: move.clientX, y: move.clientY }
          if (state.phase === 'pending') {
            if (distance(next, state.start) >= (optionsRef.current.mouseSlop ?? 4)) controller.activate(next)
            return
          }
          if (state.phase === 'dragging' && state.item !== null) {
            state.last = next
            optionsRef.current.onMove(state.item, next)
          }
        }
        const onUp = (up: PointerEvent) => {
          if (up.pointerId !== stateRef.current.pointerId) return
          controller.finish({ x: up.clientX, y: up.clientY }, false)
        }
        const onCancel = (cancel: PointerEvent) => {
          if (cancel.pointerId !== stateRef.current.pointerId) return
          controller.finish(stateRef.current.last, true)
        }
        const onKey = (key: KeyboardEvent) => {
          if (key.key === 'Escape') controller.finish(stateRef.current.last, true)
        }
        window.addEventListener('pointermove', onMove)
        window.addEventListener('pointerup', onUp)
        window.addEventListener('pointercancel', onCancel)
        window.addEventListener('keydown', onKey)
        detachMouseRef.current = () => {
          window.removeEventListener('pointermove', onMove)
          window.removeEventListener('pointerup', onUp)
          window.removeEventListener('pointercancel', onCancel)
          window.removeEventListener('keydown', onKey)
        }
      },
      onTouchStart: (event) => {
        if (event.touches.length !== 1) return
        if (stateRef.current.phase !== 'idle') return // a nested handle already claimed it
        const touch = event.changedTouches[0]
        if (!touch) return
        const point = { x: touch.clientX, y: touch.clientY }
        const state: DragState<T> = {
          ...idle<T>(),
          phase: 'pending',
          source: 'touch',
          item,
          start: point,
          last: point,
          touchId: touch.identifier,
        }
        state.timer = window.setTimeout(() => {
          if (stateRef.current !== state || state.phase !== 'pending') return
          state.timer = null
          controller.activate(state.last)
        }, optionsRef.current.longPressMs ?? 300)
        stateRef.current = state
      },
      onContextMenu: (event) => {
        // Android fires the context menu on a long-press; iOS shows nothing for plain elements
        if (stateRef.current.source === 'touch') event.preventDefault()
      },
    }),
    [controller],
  )

  const cancel = useCallback(() => controller.finish(stateRef.current.last, true), [controller])

  return { dragging, bind, cancel }
}
