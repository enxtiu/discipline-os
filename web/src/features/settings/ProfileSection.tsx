/**
 * Profile & goals. Every value here feeds the calorie math; the targets block
 * shows the *server-computed* result plus the safety warnings, and lets the
 * user pin targets manually or hand control back to the calculator.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  Badge,
  Button,
  Callout,
  Card,
  Field,
  Input,
  Rows,
  Row,
  SegmentedControl,
  Select,
  SkeletonCard,
  Toggle,
} from '@/components/ui'
import { endpoints } from '@/api/client'
import type { SettingsUpdate, Targets } from '@/api/types'
import { queryKeys } from '@/lib/queryClient'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { number, toApiTime, fromApiTime } from '@/lib/format'
import { useAuth } from '@/features/auth/AuthProvider'

export function ProfileSection() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const { refreshSession } = useAuth()

  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: () => endpoints.settings() })
  const bodyQuery = useQuery({ queryKey: queryKeys.bodyParams, queryFn: () => endpoints.bodyParams() })

  const [draft, setDraft] = useState<SettingsUpdate>({})
  const [floorConfirm, setFloorConfirm] = useState(false)

  useEffect(() => {
    if (!settingsQuery.data) return
    const settings = settingsQuery.data.settings
    setDraft((current) => ({
      ...current,
      birth_date: (settings.birth_date as string) ?? null,
      sex: settings.sex as SettingsUpdate['sex'],
      height_cm: settings.height_cm ?? null,
      weight_kg: settings.weight_kg ?? null,
      body_fat_pct: settings.body_fat_pct ?? null,
      activity_level: settings.activity_level as SettingsUpdate['activity_level'],
      bmr_formula: settings.bmr_formula as SettingsUpdate['bmr_formula'],
      goal_mode: settings.goal_mode as SettingsUpdate['goal_mode'],
      goal_rate_pct: settings.goal_rate_pct,
      goal_weight_kg: settings.goal_weight_kg ?? null,
      goal_protein_g_per_kg: settings.goal_protein_g_per_kg,
      goal_fat_min_g_per_kg: settings.goal_fat_min_g_per_kg,
      goal_water_ml_per_kg: settings.goal_water_ml_per_kg,
      target_steps: settings.target_steps,
      safe_floor_kcal: settings.safe_floor_kcal ?? undefined,
      keep_photo_thumbnail: settings.keep_photo_thumbnail,
    }))
  }, [settingsQuery.data])

  const saveMutation = useMutation({
    mutationFn: (payload: SettingsUpdate) => endpoints.updateSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings })
      await queryClient.invalidateQueries({ queryKey: queryKeys.bodyParams })
      await refreshSession()
      setFloorConfirm(false)
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const targetsMutation = useMutation({
    mutationFn: () => endpoints.recomputeTargets(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings })
      await refreshSession()
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const setTargetsMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => endpoints.setTargets(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings })
      await refreshSession()
      toast.success(t('app.saved'))
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  if (settingsQuery.isLoading) return <SkeletonCard lines={6} />
  if (settingsQuery.isError || !settingsQuery.data) return null

  const { settings, targets } = settingsQuery.data
  const body = bodyQuery.data

  const save = () =>
    saveMutation.mutate({
      ...draft,
      confirm_floor: floorConfirm,
    })

  return (
    <div className="space-y-5 pb-6">
      <h1 className="h1">{t('settings.sections.profile')}</h1>

      <Card className="space-y-4">
        <p className="label">{t('settings.body_title')}</p>
        <Field label={t('onboarding.sex')}>
          <SegmentedControl
            value={(draft.sex as string) ?? 'other'}
            onChange={(value) => setDraft({ ...draft, sex: value as SettingsUpdate['sex'] })}
            options={[
              { value: 'male', label: t('sex.male') },
              { value: 'female', label: t('sex.female') },
              { value: 'other', label: t('sex.other') },
            ]}
          />
        </Field>
        <Field label={t('onboarding.birth_date')} htmlFor="birth2">
          <Input
            id="birth2"
            type="date"
            value={(draft.birth_date as string) ?? ''}
            onChange={(event) => setDraft({ ...draft, birth_date: event.target.value || null })}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={`${t('onboarding.height')}, ${t('common.cm')}`}>
            <Input
              type="number"
              inputMode="decimal"
              value={draft.height_cm ?? ''}
              onChange={(event) =>
                setDraft({ ...draft, height_cm: event.target.value ? Number(event.target.value) : null })
              }
            />
          </Field>
          <Field label={`${t('onboarding.weight')}, ${t('common.kg')}`}>
            <Input
              type="number"
              inputMode="decimal"
              step="0.1"
              value={draft.weight_kg ?? ''}
              onChange={(event) =>
                setDraft({ ...draft, weight_kg: event.target.value ? Number(event.target.value) : null })
              }
            />
          </Field>
        </div>
        <Field label={t('onboarding.body_fat')} hint={t('settings.formula_katch')}>
          <Input
            type="number"
            inputMode="decimal"
            step="0.1"
            value={draft.body_fat_pct ?? ''}
            onChange={(event) =>
              setDraft({ ...draft, body_fat_pct: event.target.value ? Number(event.target.value) : null })
            }
          />
        </Field>
        <Field label={t('onboarding.activity')}>
          <Select
            value={(draft.activity_level as string) ?? 'light'}
            onChange={(event) =>
              setDraft({ ...draft, activity_level: event.target.value as SettingsUpdate['activity_level'] })
            }
          >
            {['sedentary', 'light', 'moderate', 'active', 'very_active'].map((level) => (
              <option key={level} value={level}>
                {t(`activity.${level}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t('settings.formula')}>
          <SegmentedControl
            value={(draft.bmr_formula as string) ?? 'auto'}
            onChange={(value) => setDraft({ ...draft, bmr_formula: value as SettingsUpdate['bmr_formula'] })}
            options={[
              { value: 'auto', label: t('settings.formula_auto') },
              { value: 'mifflin', label: 'Mifflin' },
              { value: 'katch', label: 'Katch' },
            ]}
          />
        </Field>
        {body && (
          <div className="flex flex-wrap gap-2 text-xs muted">
            {body.bmi && <span>BMI {number(body.bmi, 1)}</span>}
            {body.lean_body_mass_kg && (
              <span>
                LBM {number(body.lean_body_mass_kg, 1)} {t('common.kg')}
              </span>
            )}
            {body.age_years && <span>{body.age_years} y</span>}
          </div>
        )}
      </Card>

      <Card className="space-y-4">
        <p className="label">{t('settings.goal_title')}</p>
        <SegmentedControl
          value={(draft.goal_mode as string) ?? 'maintain'}
          onChange={(value) => setDraft({ ...draft, goal_mode: value as SettingsUpdate['goal_mode'] })}
          options={[
            { value: 'lose', label: t('goal.lose') },
            { value: 'maintain', label: t('goal.maintain') },
            { value: 'gain', label: t('goal.gain') },
          ]}
        />
        {draft.goal_mode !== 'maintain' && (
          <Field label={t('onboarding.goal_rate')}>
            <SegmentedControl
              value={String(draft.goal_rate_pct ?? 15)}
              onChange={(value) => setDraft({ ...draft, goal_rate_pct: Number(value) })}
              options={[
                { value: '10', label: '10%' },
                { value: '15', label: '15%' },
                { value: '20', label: '20%' },
              ]}
            />
          </Field>
        )}
        <div className="grid grid-cols-3 gap-3">
          <Field label={`${t('today.protein')}, ${t('common.g')}/${t('common.kg')}`}>
            <Input
              type="number"
              step="0.1"
              value={draft.goal_protein_g_per_kg ?? 2}
              onChange={(event) => setDraft({ ...draft, goal_protein_g_per_kg: Number(event.target.value) })}
            />
          </Field>
          <Field label={`${t('today.fat')}, ${t('common.g')}/${t('common.kg')}`}>
            <Input
              type="number"
              step="0.1"
              value={draft.goal_fat_min_g_per_kg ?? 0.8}
              onChange={(event) => setDraft({ ...draft, goal_fat_min_g_per_kg: Number(event.target.value) })}
            />
          </Field>
          <Field label={`${t('today.water')}, ${t('common.ml')}/${t('common.kg')}`}>
            <Input
              type="number"
              value={draft.goal_water_ml_per_kg ?? 33}
              onChange={(event) => setDraft({ ...draft, goal_water_ml_per_kg: Number(event.target.value) })}
            />
          </Field>
        </div>
        <Field label={t('onboarding.target_steps')}>
          <Input
            type="number"
            step="500"
            value={draft.target_steps ?? 8000}
            onChange={(event) => setDraft({ ...draft, target_steps: Number(event.target.value) })}
          />
        </Field>
      </Card>

      <TargetsCard
        targets={targets}
        onRecompute={() => targetsMutation.mutate()}
        recomputing={targetsMutation.isPending}
        onManual={(payload) => setTargetsMutation.mutate(payload)}
        saving={setTargetsMutation.isPending}
        onFloorChange={(value) => setDraft({ ...draft, safe_floor_kcal: value })}
        currentFloor={(draft.safe_floor_kcal as number) ?? targets.safe_floor_kcal}
        confirm={floorConfirm}
        onConfirm={setFloorConfirm}
      />

      <div className="sticky bottom-28 z-10">
        <Button block loading={saveMutation.isPending} onClick={save}>
          {t('app.save')}
        </Button>
      </div>

      <Rows>
        <Row
          label={t('settings.keep_thumbnail')}
          hint={t('settings.keep_thumbnail_hint')}
          trailing={
            <Toggle
              label={t('settings.keep_thumbnail')}
              checked={!!draft.keep_photo_thumbnail}
              onChange={(value) => setDraft({ ...draft, keep_photo_thumbnail: value })}
            />
          }
        />
      </Rows>

      <p className="text-xs muted">
        {t('settings.quiet_hours')}: {fromApiTime(settings.quiet_hours_start as string)} –{' '}
        {fromApiTime(settings.quiet_hours_end as string)} ({settings.timezone}, {toApiTime('')}
        {` ${settings.timezone}`})
      </p>
    </div>
  )
}

function TargetsCard({
  targets,
  onRecompute,
  recomputing,
  onManual,
  saving,
  onFloorChange,
  currentFloor,
  confirm,
  onConfirm,
}: {
  targets: Targets
  onRecompute: () => void
  recomputing: boolean
  onManual: (payload: Record<string, unknown>) => void
  saving: boolean
  onFloorChange: (value: number) => void
  currentFloor: number
  confirm: boolean
  onConfirm: (value: boolean) => void
}) {
  const { t } = useTranslation()
  const [edit, setEdit] = useState(false)
  const [values, setValues] = useState({
    target_kcal: targets.target_kcal ?? 0,
    target_protein_g: targets.target_protein_g ?? 0,
    target_fat_g: targets.target_fat_g ?? 0,
    target_carbs_g: targets.target_carbs_g ?? 0,
    target_fiber_g: targets.target_fiber_g ?? 0,
    target_water_ml: targets.target_water_ml ?? 0,
  })

  const belowFloor = values.target_kcal > 0 && values.target_kcal < currentFloor

  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="label">{t('settings.targets_title')}</p>
        <Badge tone={targets.is_manual ? 'warn' : 'accent'}>
          {targets.is_manual ? t('settings.targets_manual') : t('settings.targets_auto')}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Metric label={t('settings.bmr')} value={targets.bmr_kcal} />
        <Metric label={t('settings.tdee')} value={targets.tdee_kcal} />
        <Metric label={t('onboarding.target_kcal')} value={targets.target_kcal} accent />
        <Metric
          label={(targets.deficit_pct ?? 0) >= 0 ? t('settings.deficit') : t('settings.surplus')}
          value={targets.deficit_pct !== null && targets.deficit_pct !== undefined ? Math.abs(targets.deficit_pct) : null}
          unit="%"
          digits={1}
        />
      </div>

      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <MacroPill label={t('today.protein')} value={targets.target_protein_g} />
        <MacroPill label={t('today.fat')} value={targets.target_fat_g} />
        <MacroPill label={t('today.carbs')} value={targets.target_carbs_g} />
        <MacroPill label={t('today.fiber')} value={targets.target_fiber_g} />
        <MacroPill label={t('today.water')} value={targets.target_water_ml} unit={t('common.ml')} />
        <MacroPill label={t('today.steps')} value={targets.target_steps} />
      </div>

      {targets.warnings.length > 0 && (
        <div className="space-y-2">
          {targets.warnings.map((warning) => (
            <Callout key={warning} tone={warning.includes('aggressive') || warning.includes('low_body') ? 'danger' : 'warn'}>
              {t(`settings.warnings.${warning}`, { defaultValue: warning })}
            </Callout>
          ))}
        </div>
      )}

      <Field
        label={t('settings.safe_floor')}
        hint={t('settings.safe_floor_hint', { kcal: number(targets.safe_floor_kcal) })}
      >
        <Input
          type="number"
          inputMode="numeric"
          value={currentFloor}
          onChange={(event) => onFloorChange(Number(event.target.value))}
        />
      </Field>
      {currentFloor < targets.safe_floor_kcal && (
        <Callout tone="danger" title={t('settings.safe_floor_warning')}>
          <label className="flex min-h-tap items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={confirm}
              onChange={(event) => onConfirm(event.target.checked)}
              className="h-5 w-5 accent-rose-500"
            />
            {t('settings.confirm_floor')}
          </label>
        </Callout>
      )}

      {edit ? (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('common.kcal')}>
              <Input
                type="number"
                value={values.target_kcal}
                onChange={(event) => setValues({ ...values, target_kcal: Number(event.target.value) })}
              />
            </Field>
            <Field label={`${t('today.protein')}, ${t('common.g')}`}>
              <Input
                type="number"
                value={values.target_protein_g}
                onChange={(event) => setValues({ ...values, target_protein_g: Number(event.target.value) })}
              />
            </Field>
            <Field label={`${t('today.fat')}, ${t('common.g')}`}>
              <Input
                type="number"
                value={values.target_fat_g}
                onChange={(event) => setValues({ ...values, target_fat_g: Number(event.target.value) })}
              />
            </Field>
            <Field label={`${t('today.carbs')}, ${t('common.g')}`}>
              <Input
                type="number"
                value={values.target_carbs_g}
                onChange={(event) => setValues({ ...values, target_carbs_g: Number(event.target.value) })}
              />
            </Field>
            <Field label={`${t('today.fiber')}, ${t('common.g')}`}>
              <Input
                type="number"
                value={values.target_fiber_g}
                onChange={(event) => setValues({ ...values, target_fiber_g: Number(event.target.value) })}
              />
            </Field>
            <Field label={`${t('today.water')}, ${t('common.ml')}`}>
              <Input
                type="number"
                value={values.target_water_ml}
                onChange={(event) => setValues({ ...values, target_water_ml: Number(event.target.value) })}
              />
            </Field>
          </div>
          {belowFloor && (
            <Callout tone="danger" title={t('settings.safe_floor_warning')}>
              <label className="flex min-h-tap items-center gap-3 text-sm">
                <input
                  type="checkbox"
                  checked={confirm}
                  onChange={(event) => onConfirm(event.target.checked)}
                  className="h-5 w-5 accent-rose-500"
                />
                {t('settings.confirm_floor')}
              </label>
            </Callout>
          )}
          <div className="flex gap-2">
            <Button
              block
              loading={saving}
              onClick={() => onManual({ ...values, confirm_floor: confirm })}
              disabled={belowFloor && !confirm}
            >
              {t('app.save')}
            </Button>
            <Button block variant="secondary" onClick={() => setEdit(false)}>
              {t('app.cancel')}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button block variant="secondary" onClick={() => setEdit(true)}>
            {t('app.edit')}
          </Button>
          {targets.is_manual && (
            <Button block loading={recomputing} onClick={onRecompute}>
              {t('settings.recompute')}
            </Button>
          )}
        </div>
      )}
    </Card>
  )
}

function Metric({
  label,
  value,
  unit,
  accent,
  digits = 0,
}: {
  label: string
  value: number | null | undefined
  unit?: string
  accent?: boolean
  digits?: number
}) {
  return (
    <div className="surface-sunken px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wide muted">{label}</p>
      <p className={accent ? 'stat-value text-aqua-300' : 'stat-value'}>
        {number(value ?? null, digits)}
        {unit && <span className="ml-1 text-[11px] font-normal muted">{unit}</span>}
      </p>
    </div>
  )
}

function MacroPill({ label, value, unit = 'g' }: { label: string; value: number | null | undefined; unit?: string }) {
  return (
    <div className="surface-sunken px-2 py-1.5">
      <p className="text-[10px] uppercase tracking-wide muted">{label}</p>
      <p className="tnum text-sm font-semibold">
        {number(value)}
        <span className="ml-0.5 text-[10px] muted">{unit}</span>
      </p>
    </div>
  )
}
