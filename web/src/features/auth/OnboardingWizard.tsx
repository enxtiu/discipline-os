/**
 * First-run wizard (4 short steps, one screen each, thumb-friendly).
 *
 * Step 4 shows the *live* target preview computed by the server
 * (`POST /settings/targets/preview`) so the user sees exactly what the body
 * parameters produce — including the safety warnings — before anything is
 * saved. The final action posts everything through `/settings/onboarding`.
 */
import { useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { Badge, Button, Callout, Field, Input, ProgressBar, SegmentedControl, Select, Toggle } from '@/components/ui'
import { endpoints } from '@/api/client'
import type { OnboardingPayload, Targets } from '@/api/types'
import { useAuth } from './AuthProvider'
import { useToast } from '@/components/ui/Toast'
import { errorMessage } from '@/lib/errors'
import { number, toApiTime } from '@/lib/format'
import { detectedTimezone } from '@/lib/device'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/queryClient'

type Step = 0 | 1 | 2 | 3

/** Mirrors `ActivityLiteral` on the API — kept in sync by the i18n gate. */
const ACTIVITY_LEVELS = ['sedentary', 'light', 'moderate', 'active', 'very_active'] as const

/** Step names shown beside the progress bar so the user knows where they are. */
const STEP_LABEL_KEYS = [
  'onboarding.step_basics',
  'onboarding.step_body',
  'onboarding.step_goal',
  'onboarding.step_limits',
] as const

const NOTIFICATION_KEYS = [
  'task_reminders',
  'meal_logging',
  'water',
  'workout',
  'evening_review',
  'streak_at_risk',
] as const

export function OnboardingWizard({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const { session, refreshSession } = useAuth()
  const toast = useToast()
  const queryClient = useQueryClient()

  const [step, setStep] = useState<Step>(0)
  const [draft, setDraft] = useState<OnboardingPayload>({
    language: (session?.settings.language as 'ru' | 'en') ?? 'ru',
    timezone: session?.settings.timezone ?? detectedTimezone(),
    sex: (session?.settings.sex as OnboardingPayload['sex']) ?? 'male',
    birth_date: (session?.settings.birth_date as string) ?? null,
    height_cm: session?.settings.height_cm ?? null,
    weight_kg: session?.settings.weight_kg ?? null,
    body_fat_pct: session?.settings.body_fat_pct ?? null,
    activity_level: (session?.settings.activity_level as OnboardingPayload['activity_level']) ?? 'light',
    goal_mode: (session?.settings.goal_mode as OnboardingPayload['goal_mode']) ?? 'maintain',
    goal_rate_pct: session?.settings.goal_rate_pct ?? 15,
    goal_weight_kg: session?.settings.goal_weight_kg ?? null,
    target_steps: session?.settings.target_steps ?? 8000,
    quiet_hours_start: toApiTime((session?.settings.quiet_hours_start as string) ?? '23:00'),
    quiet_hours_end: toApiTime((session?.settings.quiet_hours_end as string) ?? '07:00'),
    notifications: Object.fromEntries(NOTIFICATION_KEYS.map((key) => [key, true])),
  })

  const update = <K extends keyof OnboardingPayload>(key: K, value: OnboardingPayload[K]) =>
    setDraft((current) => ({ ...current, [key]: value }))

  const previewMutation = useMutation({
    mutationFn: () =>
      endpoints.previewTargets({
        sex: draft.sex ?? null,
        birth_date: draft.birth_date ?? null,
        height_cm: draft.height_cm ?? null,
        weight_kg: draft.weight_kg ?? null,
        body_fat_pct: draft.body_fat_pct ?? null,
        activity_level: draft.activity_level ?? 'light',
        formula: 'auto',
        goal_mode: draft.goal_mode ?? 'maintain',
        goal_rate_pct: draft.goal_mode === 'maintain' ? 0 : (draft.goal_rate_pct ?? 0),
        target_steps: draft.target_steps ?? 8000,
      }),
  })

  const saveMutation = useMutation({
    mutationFn: () => endpoints.onboarding(draft),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings })
      await refreshSession()
      toast.success(t('onboarding.completed'))
      onDone()
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const preview: Targets | null = previewMutation.data ?? null
  const canAdvance = useMemo(() => {
    if (step === 1) return !!draft.height_cm && !!draft.weight_kg
    return true
  }, [step, draft.height_cm, draft.weight_kg])

  return (
    <div className="flex min-h-[100dvh] flex-col px-5 pb-8 pt-10">
      <header className="mx-auto w-full max-w-md">
        <div className="mb-6 flex items-center justify-between gap-3">
          <Badge tone="accent">
            {step + 1} / 4
          </Badge>
          {step > 0 && (
            <button type="button" className="text-sm muted" onClick={() => setStep((step - 1) as Step)}>
              {t('app.back')}
            </button>
          )}
        </div>
        <div className="mb-6 flex items-center gap-3">
          <ProgressBar value={step + 1} max={4} className="flex-1" />
          <span className="label shrink-0">{t(STEP_LABEL_KEYS[step])}</span>
        </div>
        <h1 className="h1">{t('onboarding.title')}</h1>
        <p className="mt-1.5 text-sm muted">{t('onboarding.subtitle')}</p>
      </header>

      <div className="mx-auto mt-6 w-full max-w-md flex-1">
        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -24 }}
            transition={{ duration: 0.25 }}
            className="space-y-5"
          >
            {step === 0 && (
              <>
                <Field label={t('onboarding.sex')}>
                  <SegmentedControl
                    value={draft.sex ?? 'male'}
                    onChange={(value) => update('sex', value)}
                    options={[
                      { value: 'male', label: t('sex.male') },
                      { value: 'female', label: t('sex.female') },
                      { value: 'other', label: t('sex.other') },
                    ]}
                  />
                </Field>
                <Field label={t('onboarding.birth_date')} htmlFor="birth">
                  <Input
                    id="birth"
                    type="date"
                    value={draft.birth_date ?? ''}
                    max={new Date().toISOString().slice(0, 10)}
                    onChange={(event) => update('birth_date', event.target.value || null)}
                  />
                </Field>
                <Field label={t('onboarding.activity')} htmlFor="activity">
                  {/* Five levels with real names: a segmented control would not
                      fit them on a phone, and bare factors mean nothing. */}
                  <Select
                    id="activity"
                    value={draft.activity_level ?? 'light'}
                    onChange={(event) =>
                      update('activity_level', event.target.value as OnboardingPayload['activity_level'])
                    }
                  >
                    {ACTIVITY_LEVELS.map((level) => (
                      <option key={level} value={level}>
                        {t(`activity.${level}`)}
                      </option>
                    ))}
                  </Select>
                </Field>
              </>
            )}

            {step === 1 && (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Field label={`${t('onboarding.height')}, ${t('common.cm')}`} htmlFor="height">
                    <Input
                      id="height"
                      type="number"
                      inputMode="decimal"
                      min={100}
                      max={250}
                      value={draft.height_cm ?? ''}
                      onChange={(event) =>
                        update('height_cm', event.target.value ? Number(event.target.value) : null)
                      }
                    />
                  </Field>
                  <Field label={`${t('onboarding.weight')}, ${t('common.kg')}`} htmlFor="weight">
                    <Input
                      id="weight"
                      type="number"
                      inputMode="decimal"
                      step="0.1"
                      min={30}
                      max={300}
                      value={draft.weight_kg ?? ''}
                      onChange={(event) =>
                        update('weight_kg', event.target.value ? Number(event.target.value) : null)
                      }
                    />
                  </Field>
                </div>
                <Field
                  label={t('onboarding.body_fat')}
                  hint={t('settings.formula_katch')}
                  htmlFor="bodyfat"
                >
                  <Input
                    id="bodyfat"
                    type="number"
                    inputMode="decimal"
                    step="0.1"
                    min={2}
                    max={70}
                    value={draft.body_fat_pct ?? ''}
                    onChange={(event) =>
                      update('body_fat_pct', event.target.value ? Number(event.target.value) : null)
                    }
                  />
                </Field>
              </>
            )}

            {step === 2 && (
              <>
                <Field label={t('onboarding.goal_mode')}>
                  <SegmentedControl
                    value={draft.goal_mode ?? 'maintain'}
                    onChange={(value) => {
                      update('goal_mode', value)
                      if (value === 'maintain') update('goal_rate_pct', 0)
                      else if (!draft.goal_rate_pct) update('goal_rate_pct', 15)
                    }}
                    options={[
                      { value: 'lose', label: t('goal.lose') },
                      { value: 'maintain', label: t('goal.maintain') },
                      { value: 'gain', label: t('goal.gain') },
                    ]}
                  />
                </Field>

                {draft.goal_mode !== 'maintain' && (
                  <Field label={t('onboarding.goal_rate')}>
                    <SegmentedControl
                      value={String(draft.goal_rate_pct ?? 15)}
                      onChange={(value) => update('goal_rate_pct', Number(value))}
                      options={[
                        { value: '10', label: '10%' },
                        { value: '15', label: '15%' },
                        { value: '20', label: '20%' },
                      ]}
                    />
                  </Field>
                )}

                <Field label={`${t('onboarding.target_steps')}`} htmlFor="steps">
                  <Input
                    id="steps"
                    type="number"
                    inputMode="numeric"
                    step="500"
                    min={0}
                    max={60000}
                    value={draft.target_steps ?? 8000}
                    onChange={(event) => update('target_steps', Number(event.target.value))}
                  />
                </Field>

                <Button variant="secondary" block onClick={() => previewMutation.mutate()} loading={previewMutation.isPending}>
                  {t('onboarding.preview_title')}
                </Button>

                {preview && <TargetPreviewCard targets={preview} />}
              </>
            )}

            {step === 3 && (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t('settings.quiet_hours')} htmlFor="quiet-from">
                    <Input
                      id="quiet-from"
                      type="time"
                      value={(draft.quiet_hours_start ?? '23:00').slice(0, 5)}
                      onChange={(event) => update('quiet_hours_start', toApiTime(event.target.value))}
                    />
                  </Field>
                  <Field label="—" htmlFor="quiet-to">
                    <Input
                      id="quiet-to"
                      type="time"
                      value={(draft.quiet_hours_end ?? '07:00').slice(0, 5)}
                      onChange={(event) => update('quiet_hours_end', toApiTime(event.target.value))}
                    />
                  </Field>
                </div>
                <p className="text-xs muted">{t('settings.quiet_hours_hint')}</p>

                <Field label={t('onboarding.notifications')}>
                  <div className="card divide-y divide-[rgb(var(--border))]">
                    {NOTIFICATION_KEYS.map((key) => (
                      <div key={key} className="flex items-center justify-between gap-3 px-4 py-3">
                        <span className="text-sm">{t(`settings.notify_types.${key}`)}</span>
                        <Toggle
                          label={key}
                          checked={draft.notifications?.[key] ?? true}
                          onChange={(value) =>
                            update('notifications', { ...(draft.notifications ?? {}), [key]: value })
                          }
                        />
                      </div>
                    ))}
                  </div>
                </Field>

                <Callout tone="info" title={t('settings.install_app')}>
                  <ol className="list-inside list-decimal space-y-1 text-xs">
                    {(t('settings.install_steps', { returnObjects: true }) as string[]).map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ol>
                </Callout>
              </>
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      <footer className="mx-auto mt-8 w-full max-w-md">
        {step < 3 ? (
          <Button block disabled={!canAdvance} onClick={() => setStep((step + 1) as Step)}>
            {t('app.next')}
          </Button>
        ) : (
          <Button block loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            {t('onboarding.complete')}
          </Button>
        )}
        <button type="button" className="mt-3 w-full text-center text-xs muted" onClick={onDone}>
          {t('app.skip')}
        </button>
      </footer>
    </div>
  )
}

function TargetPreviewCard({ targets }: { targets: Targets }) {
  const { t } = useTranslation()
  return (
    <div className="card space-y-3 p-4">
      <div className="flex items-center justify-between">
        <span className="label">{t('onboarding.preview_title')}</span>
        <Badge tone="accent">{targets.formula_used === 'katch_mcardle' ? 'Katch' : 'Mifflin'}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Metric label={t('onboarding.bmr')} value={number(targets.bmr_kcal)} unit={t('common.kcal')} />
        <Metric label={t('onboarding.tdee')} value={number(targets.tdee_kcal)} unit={t('common.kcal')} />
        <Metric label={t('onboarding.target_kcal')} value={number(targets.target_kcal)} unit={t('common.kcal')} accent />
        <Metric label={t('settings.deficit')} value={number(Math.abs(targets.deficit_pct ?? 0), 1)} unit="%" />
      </div>
      <div className="flex flex-wrap gap-2 text-xs muted">
        <span>Б {number(targets.target_protein_g)} {t('common.g')}</span>
        <span>Ж {number(targets.target_fat_g)} {t('common.g')}</span>
        <span>У {number(targets.target_carbs_g)} {t('common.g')}</span>
        <span>Клетчатка {number(targets.target_fiber_g)} {t('common.g')}</span>
        <span>{t('today.water')} {number(targets.target_water_ml)} {t('common.ml')}</span>
      </div>
      {targets.warnings.length > 0 && (
        <div className="space-y-2">
          {targets.warnings.map((warning) => (
            <Callout key={warning} tone="warn">
              {t(`settings.warnings.${warning}`, { defaultValue: warning })}
            </Callout>
          ))}
        </div>
      )}
      <p className="text-xs muted">{t('settings.safe_floor_hint', { kcal: number(targets.safe_floor_kcal) })}</p>
    </div>
  )
}

function Metric({
  label,
  value,
  unit,
  accent,
}: {
  label: string
  value: string
  unit?: string
  accent?: boolean
}) {
  return (
    <div className="surface-sunken px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wide muted">{label}</p>
      <p className={accent ? 'stat-value text-aqua-300' : 'stat-value'}>
        {value}
        {unit && <span className="ml-1 text-xs font-normal muted">{unit}</span>}
      </p>
    </div>
  )
}
