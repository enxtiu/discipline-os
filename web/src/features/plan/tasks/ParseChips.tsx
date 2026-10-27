/** What a quick-add line will create, as chips: date/time, block, priority, tags, project… */
import type { ReactNode } from 'react'
import clsx from 'clsx'

import { IconClock } from '@/components/ui/icons'
import { blockLabel, dueLabel, formatMinutes, priorityLabel, relativeDay } from '../format'
import { IconBellSmall, IconCalendar, IconFolder, IconLayers, IconRepeat, IconTag, IconTimer } from '../icons'
import type { ParseResult } from '../types'
import { PRIORITY_TEXT } from './priority'
import { leadLabel } from './reminders'
import { describeRepeat } from './rrule'

interface ChipModel {
  key: string
  kind: string
  label: string
  icon?: ReactNode
  className?: string
}

export function parseChips(parsed: ParseResult, today: string | undefined): ChipModel[] {
  const chips: ChipModel[] = []
  if (parsed.block) {
    chips.push({
      key: 'block',
      kind: 'block',
      label: `${relativeDay(parsed.block.date, today)} ${blockLabel(parsed.block)}`,
      icon: <IconClock size={14} />,
    })
  } else if (parsed.due_date) {
    chips.push({ key: 'date', kind: 'date', label: dueLabel(parsed, today), icon: <IconCalendar size={14} /> })
  }
  if (parsed.priority) {
    chips.push({
      key: 'priority',
      kind: 'priority',
      label: priorityLabel(parsed.priority),
      className: PRIORITY_TEXT[parsed.priority],
    })
  }
  for (const tag of parsed.tags) chips.push({ key: `tag-${tag}`, kind: 'tag', label: `#${tag}`, icon: <IconTag size={14} /> })
  if (parsed.project) {
    chips.push({
      key: 'project',
      kind: 'project',
      label: parsed.project.name,
      icon: parsed.project.kind === 'project' ? <IconFolder size={14} /> : <IconLayers size={14} />,
    })
  }
  if (parsed.context) chips.push({ key: 'context', kind: 'context', label: `@${parsed.context}` })
  if (parsed.recurrence_rule) {
    chips.push({
      key: 'repeat',
      kind: 'recurrence',
      label: describeRepeat(parsed.recurrence_rule, parsed.recurrence_preset),
      icon: <IconRepeat size={14} />,
    })
  }
  parsed.reminders.forEach((reminder, index) =>
    chips.push({
      key: `reminder-${index}`,
      kind: 'reminder',
      label: leadLabel(reminder.lead_minutes),
      icon: <IconBellSmall size={14} />,
    }),
  )
  if (parsed.estimate_minutes) {
    chips.push({
      key: 'estimate',
      kind: 'duration',
      label: formatMinutes(parsed.estimate_minutes),
      icon: <IconTimer size={14} />,
    })
  }
  return chips
}

export function ParseChips({ chips, className }: { chips: ChipModel[]; className?: string }) {
  if (chips.length === 0) return null
  return (
    <ul className={clsx('flex flex-wrap gap-1.5', className)} data-testid="quick-add-chips">
      {chips.map((chip) => (
        <li
          key={chip.key}
          className={clsx('chip px-2.5 py-1 text-xs', chip.className)}
          data-kind={chip.kind}
          data-testid="quick-add-chip"
        >
          {chip.icon}
          <span className="tnum">{chip.label}</span>
        </li>
      ))}
    </ul>
  )
}
