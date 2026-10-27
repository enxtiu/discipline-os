/** Create / edit a project: name, outcome, area, start and deadline, colour, status. */
import { useEffect, useId, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, Select, Sheet } from '@/components/ui'
import { errorMessage } from '@/lib/errors'
import { newId, useAreas, useCreateProject, useUpdateProject } from '../queries'
import type { Project, ProjectPatchIn, ProjectStatus } from '../types'
import { planToast } from '../tasks/toast'
import { ColorPicker } from './palette'

const STATUSES: ProjectStatus[] = ['active', 'on_hold', 'completed', 'cancelled', 'archived']

export function ProjectSheet({
  open,
  project,
  defaultAreaId,
  onClose,
}: {
  open: boolean
  /** Edit this project; omit to create one. */
  project?: Project | null
  defaultAreaId?: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const areas = useAreas()
  const create = useCreateProject()
  const update = useUpdateProject()
  const ids = { name: useId(), outcome: useId(), area: useId(), start: useId(), deadline: useId(), status: useId() }
  const [name, setName] = useState('')
  const [outcome, setOutcome] = useState('')
  const [areaId, setAreaId] = useState('')
  const [startDate, setStartDate] = useState('')
  const [deadline, setDeadline] = useState('')
  const [color, setColor] = useState<string | null>(null)
  const [status, setStatus] = useState<ProjectStatus>('active')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName(project?.name ?? '')
    setOutcome(project?.outcome ?? '')
    setAreaId(project?.area?.id ?? defaultAreaId ?? '')
    setStartDate(project?.start_date ?? '')
    setDeadline(project?.deadline ?? '')
    setColor(project?.color ?? null)
    setStatus(project?.status ?? 'active')
    setError(null)
    // re-initialise per opening / project, not on every background refetch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, project?.id, defaultAreaId])

  const areaItems = areas.data?.items ?? []
  const areaOptions =
    project?.area && !areaItems.some((area) => area.id === project.area?.id)
      ? [{ id: project.area.id, name: project.area.name }, ...areaItems]
      : areaItems
  const pending = create.isPending || update.isPending
  const valid = name.trim().length > 0 && name.trim().length <= 200

  const submit = () => {
    if (!valid) return
    setError(null)
    if (project) {
      const patch: ProjectPatchIn = {}
      if (name.trim() !== project.name) patch.name = name.trim()
      if ((outcome.trim() || null) !== project.outcome) patch.outcome = outcome.trim() || null
      if ((areaId || null) !== (project.area?.id ?? null)) patch.area_id = areaId || null
      if ((startDate || null) !== project.start_date) patch.start_date = startDate || null
      if ((deadline || null) !== project.deadline) patch.deadline = deadline || null
      if ((color ?? null) !== (project.color ?? null)) patch.color = color
      if (status !== project.status) patch.status = status
      if (Object.keys(patch).length === 0) {
        onClose()
        return
      }
      update.mutate(
        { id: project.id, patch },
        {
          onSuccess: (result) => {
            planToast.success(result.queued ? t('task.queued') : t('plan.project_saved'))
            onClose()
          },
          onError: (failure) => setError(errorMessage(failure)),
        },
      )
      return
    }
    const id = newId()
    create.mutate(
      {
        id,
        name: name.trim(),
        outcome: outcome.trim() || undefined,
        area_id: areaId || undefined,
        start_date: startDate || undefined,
        deadline: deadline || undefined,
        color: color ?? undefined,
      },
      {
        onSuccess: (result) => {
          onClose()
          if (result.queued) planToast.info(t('task.queued'))
          else {
            planToast.success(t('plan.project_created', { name: name.trim() }))
            navigate(`/plan/projects/${id}`)
          }
        },
        onError: (failure) => setError(errorMessage(failure)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={project ? t('plan.project_edit') : t('plan.project_new')}>
      <div className="space-y-4 pb-2" data-testid="project-sheet">
        <Field label={t('plan.project_name')} htmlFor={ids.name}>
          <Input
            id={ids.name}
            value={name}
            maxLength={200}
            autoFocus={!project}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && submit()}
            data-testid="project-name"
          />
        </Field>
        <Field label={t('plan.outcome')} htmlFor={ids.outcome} hint={t('plan.outcome_hint')}>
          <Input id={ids.outcome} value={outcome} onChange={(event) => setOutcome(event.target.value)} data-testid="project-outcome" />
        </Field>
        <Field label={t('task.area')} htmlFor={ids.area}>
          <Select id={ids.area} value={areaId} onChange={(event) => setAreaId(event.target.value)} data-testid="project-area">
            <option value="">{t('task.no_area')}</option>
            {areaOptions.map((area) => (
              <option key={area.id} value={area.id}>
                {area.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3">
          <Field label={t('plan.start_date')} htmlFor={ids.start}>
            <Input id={ids.start} type="date" className="tnum" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </Field>
          <Field label={t('plan.deadline')} htmlFor={ids.deadline}>
            <Input
              id={ids.deadline}
              type="date"
              className="tnum"
              value={deadline}
              onChange={(event) => setDeadline(event.target.value)}
              data-testid="project-deadline"
            />
          </Field>
        </div>
        <Field label={t('plan.color_label')}>
          <ColorPicker value={color} onChange={setColor} />
        </Field>
        {project && (
          <Field label={t('plan.project_status_label')} htmlFor={ids.status}>
            <Select id={ids.status} value={status} onChange={(event) => setStatus(event.target.value as ProjectStatus)}>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {t(`plan.project_status.${value}`)}
                </option>
              ))}
            </Select>
          </Field>
        )}
        {error && <p className="text-sm text-rose-400">{error}</p>}
        <Button block disabled={!valid} loading={pending} onClick={submit} data-testid="project-save">
          {project ? t('app.save') : t('plan.project_create')}
        </Button>
      </div>
    </Sheet>
  )
}
