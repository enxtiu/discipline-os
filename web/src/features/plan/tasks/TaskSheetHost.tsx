/**
 * Mounted once in the app shell: listens to `openTask` / `newTask` /
 * `quickAdd` (see `../events.ts`) from any screen and shows the matching sheet.
 * The sheets load on first use, so the Today chunk stays light. It also hosts
 * the planner toasts (undo / open actions, see `./toast.tsx`) and keeps task
 * edits waiting in the offline queue visible in every cache (`../pending.ts`).
 */
import { lazy, Suspense, useEffect, useState } from 'react'

import { onNewTask, onOpenTask, onQuickAdd, type QuickAddRequest } from '../events'
import { usePlannerPendingEdits } from '../pending'
import type { TaskIn } from '../types'
import { PlanToastHost } from './toast'

const TaskSheet = lazy(() => import('./TaskSheet').then((m) => ({ default: m.TaskSheet })))
const QuickAddTaskSheet = lazy(() => import('./QuickAddTaskSheet').then((m) => ({ default: m.QuickAddTaskSheet })))

type State =
  | { kind: 'none' }
  | { kind: 'edit'; taskId: string }
  | { kind: 'new'; prefill: Partial<TaskIn> }
  | { kind: 'quick'; request: QuickAddRequest }

const NO_REQUEST: QuickAddRequest = {}

export function TaskSheetHost() {
  usePlannerPendingEdits() // stage 9: edits waiting in the offline queue show at once everywhere
  const [state, setState] = useState<State>({ kind: 'none' })
  // remember which sheets were ever opened so their close animation can play
  const [loaded, setLoaded] = useState({ task: false, quick: false })

  useEffect(() => {
    const offOpen = onOpenTask((taskId) => {
      setLoaded((value) => ({ ...value, task: true }))
      setState({ kind: 'edit', taskId })
    })
    const offNew = onNewTask((prefill) => {
      setLoaded((value) => ({ ...value, task: true }))
      setState({ kind: 'new', prefill })
    })
    const offQuick = onQuickAdd((request) => {
      setLoaded((value) => ({ ...value, quick: true }))
      setState({ kind: 'quick', request })
    })
    return () => {
      offOpen()
      offNew()
      offQuick()
    }
  }, [])

  // once the close animation has played, unmount the sheets: a closed editor
  // must not keep its lists subscribed (every planner write would refetch them)
  useEffect(() => {
    if (state.kind !== 'none') return
    const handle = window.setTimeout(() => setLoaded({ task: false, quick: false }), 700)
    return () => window.clearTimeout(handle)
  }, [state.kind])

  const close = () => setState({ kind: 'none' })

  return (
    <>
      <PlanToastHost />
      <Suspense fallback={null}>
        {loaded.task && (
          <TaskSheet
            open={state.kind === 'edit' || state.kind === 'new'}
            taskId={state.kind === 'edit' ? state.taskId : undefined}
            prefill={state.kind === 'new' ? state.prefill : undefined}
            onClose={close}
          />
        )}
        {loaded.quick && (
          <QuickAddTaskSheet
            open={state.kind === 'quick'}
            request={state.kind === 'quick' ? state.request : NO_REQUEST}
            onClose={close}
          />
        )}
      </Suspense>
    </>
  )
}
