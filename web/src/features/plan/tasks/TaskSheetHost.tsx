/**
 * Mounted once in the app shell: listens to `openTask` / `newTask` /
 * `quickAdd` (see `../events.ts`) from any screen and shows the matching sheet.
 * The sheets load on first use, so the Today chunk stays light.
 */
import { lazy, Suspense, useEffect, useState } from 'react'

import { onNewTask, onOpenTask, onQuickAdd, type QuickAddRequest } from '../events'
import type { TaskIn } from '../types'

const TaskSheet = lazy(() => import('./TaskSheet').then((m) => ({ default: m.TaskSheet })))
const QuickAddTaskSheet = lazy(() => import('./QuickAddTaskSheet').then((m) => ({ default: m.QuickAddTaskSheet })))

type State =
  | { kind: 'none' }
  | { kind: 'edit'; taskId: string }
  | { kind: 'new'; prefill: Partial<TaskIn> }
  | { kind: 'quick'; request: QuickAddRequest }

export function TaskSheetHost() {
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

  const close = () => setState({ kind: 'none' })

  return (
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
          request={state.kind === 'quick' ? state.request : {}}
          onClose={close}
        />
      )}
    </Suspense>
  )
}
