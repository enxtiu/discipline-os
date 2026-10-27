/**
 * Pickers for time entries and the timer: task (today's and inbox tasks),
 * project and area. Native selects — on iPhone they open the system wheel,
 * one tap away and 48 px tall. An entry's current task/project/area stays
 * selectable even when it is no longer in the lists (done, archived).
 */
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { Field, Select } from '@/components/ui'
import { useAreas, useInbox, useProjects, useTodayView } from '../queries'
import type { Ref, Task, TaskRef } from '../types'

function uniqueOpen(tasks: Task[], seen: Set<string>): Task[] {
  const out: Task[] = []
  for (const task of tasks) {
    if (seen.has(task.id) || task.status === 'done' || task.status === 'cancelled') continue
    seen.add(task.id)
    out.push(task)
  }
  return out
}

export function TaskSelect({
  id,
  value,
  current,
  onChange,
}: {
  id: string
  value: string
  /** The entry's task, kept as an option even if it left the lists. */
  current?: TaskRef | null
  onChange: (task: TaskRef | null) => void
}) {
  const { t } = useTranslation()
  const today = useTodayView()
  const inbox = useInbox()
  const groups = useMemo(() => {
    const seen = new Set<string>()
    const day = today.data
    return [
      { key: 'today', label: t('timer.tasks_today'), tasks: uniqueOpen(day ? [...day.overdue, ...day.scheduled, ...day.today] : [], seen) },
      { key: 'inbox', label: t('timer.tasks_inbox'), tasks: uniqueOpen(inbox.data?.items ?? [], seen) },
    ]
  }, [today.data, inbox.data, t])
  const byId = useMemo(() => {
    const map = new Map<string, TaskRef>()
    for (const group of groups) for (const task of group.tasks) map.set(task.id, { id: task.id, title: task.title })
    if (current) map.set(current.id, current)
    return map
  }, [groups, current])
  const listed = new Set(groups.flatMap((group) => group.tasks.map((task) => task.id)))

  return (
    <Field label={t('timer.task')} htmlFor={id}>
      <Select id={id} value={value} onChange={(event) => onChange(byId.get(event.target.value) ?? null)}>
        <option value="">{t('timer.no_task')}</option>
        {current && !listed.has(current.id) && <option value={current.id}>{current.title}</option>}
        {groups.map((group) =>
          group.tasks.length === 0 ? null : (
            <optgroup key={group.key} label={group.label}>
              {group.tasks.map((task) => (
                <option key={task.id} value={task.id}>
                  {task.title}
                </option>
              ))}
            </optgroup>
          ),
        )}
      </Select>
    </Field>
  )
}

function RefSelect({
  id,
  label,
  empty,
  value,
  current,
  items,
  onChange,
}: {
  id: string
  label: string
  empty: string
  value: string
  current?: Ref | null
  items: Ref[]
  onChange: (ref: Ref | null) => void
}) {
  const options = current && !items.some((item) => item.id === current.id) ? [current, ...items] : items
  return (
    <Field label={label} htmlFor={id}>
      <Select
        id={id}
        value={value}
        onChange={(event) => onChange(options.find((item) => item.id === event.target.value) ?? null)}
      >
        <option value="">{empty}</option>
        {options.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </Select>
    </Field>
  )
}

export function ProjectSelect(props: { id: string; value: string; current?: Ref | null; onChange: (ref: Ref | null) => void }) {
  const { t } = useTranslation()
  const projects = useProjects('open')
  const items = useMemo(
    () => (projects.data?.items ?? []).map((project) => ({ id: project.id, name: project.name, color: project.color })),
    [projects.data],
  )
  return <RefSelect {...props} label={t('timer.project')} empty={t('timer.no_project')} items={items} />
}

export function AreaSelect(props: { id: string; value: string; current?: Ref | null; onChange: (ref: Ref | null) => void }) {
  const { t } = useTranslation()
  const areas = useAreas()
  const items = useMemo(
    () => (areas.data?.items ?? []).map((area) => ({ id: area.id, name: area.name, color: area.color })),
    [areas.data],
  )
  return <RefSelect {...props} label={t('timer.area')} empty={t('timer.no_area')} items={items} />
}
