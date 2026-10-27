/**
 * The score explanation sheet (tap the score on Today): every component with
 * its value, weight and the server's detail; excluded ones greyed with the
 * reason. The calorie line explains the target zone, the safe floor (below it a
 * finished day scores 0) and that burned calories never count as food.
 */
import type { ReactNode } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { ProgressBar, Sheet } from '@/components/ui'
import { number } from '@/lib/format'
import { sleepDuration } from '@/features/health/format'
import { percent01 } from './format'
import { ProvisionalPill } from './parts'
import type { DayScore, ScoreComponent } from './types'

export function ScoreSheet({ score, open, onClose }: { score: DayScore | undefined; open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  return (
    <Sheet open={open && !!score} onClose={onClose} title={t('game.sheet.title')} size="tall">
      {score && (
        <div className="space-y-4 pb-2" data-testid="score-sheet">
          <div className="flex items-end gap-3">
            <p className="tnum text-4xl font-semibold leading-none">
              {number(Math.round(score.value))}
              <span className="ml-1.5 text-base font-normal muted">{t('game.score_of')}</span>
            </p>
            {score.provisional && <ProvisionalPill className="mb-0.5" />}
          </div>
          {score.provisional && <p className="text-sm leading-relaxed muted">{t('game.sheet.provisional_note')}</p>}

          <section className="space-y-1">
            <p className="label">{t('game.sheet.parts')}</p>
            <ul className="divide-y divide-[rgb(var(--border))]/60">
              {score.components.map((component) => (
                <ComponentRow key={component.key} component={component}>
                  {component.key === 'kcal' && !component.excluded && (
                    <KcalExplainer component={component} provisional={score.provisional} />
                  )}
                </ComponentRow>
              ))}
            </ul>
          </section>
          <p className="text-xs leading-relaxed muted">{t('game.sheet.weights_note')}</p>
        </div>
      )}
    </Sheet>
  )
}

function KcalExplainer({ component, provisional }: { component: ScoreComponent; provisional: boolean }) {
  const { t } = useTranslation()
  const { target, zone_low: low, zone_high: high, floor } = component.detail
  return (
    <div className="surface-sunken mt-2 space-y-2 px-4 py-3 text-sm leading-relaxed" data-testid="score-kcal-zone">
      {target != null && low != null && high != null && (
        <p className="font-medium">
          {t('game.sheet.kcal_zone', { target: number(target), low: number(low), high: number(high) })}
        </p>
      )}
      {floor != null && <p className="muted">{t('game.sheet.kcal_floor', { floor: number(floor) })}</p>}
      {provisional && <p className="muted">{t('game.sheet.kcal_progress_note')}</p>}
      <p className="muted">{t('game.sheet.kcal_burned')}</p>
    </div>
  )
}

function ComponentRow({ component, children }: { component: ScoreComponent; children?: ReactNode }) {
  const { t } = useTranslation()
  const { excluded } = component
  return (
    <li
      className={clsx('space-y-1.5 py-3', excluded && 'opacity-60')}
      data-testid="score-component"
      data-key={component.key}
      data-excluded={excluded}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[15px] font-medium">{t(`game.component.${component.key}`)}</span>
        {excluded ? (
          <span className="text-xs muted">{t('game.sheet.excluded')}</span>
        ) : (
          <span className="tnum text-sm">
            <span className="font-semibold">{percent01(component.value)}</span>
            <span className="ml-2 text-xs muted">{t('game.sheet.weight', { weight: number(component.weight) })}</span>
          </span>
        )}
      </div>
      {!excluded && <ProgressBar value={component.value} max={1} tone={component.key === 'kcal' ? 'accent' : 'violet'} />}
      <p className="text-xs leading-relaxed muted">{componentDetail(component, t)}</p>
      {children}
    </li>
  )
}

/** The server's detail in words; the state decides the sentence. */
function componentDetail(component: ScoreComponent, t: TFunction): string {
  const d = component.detail
  const amount = number(d.amount ?? 0)
  const target = number(d.target ?? 0)
  if (d.state === 'no_target') return t('game.detail.no_target')
  switch (component.key) {
    case 'kcal':
      switch (d.state) {
        case 'no_meals':
          return t('game.detail.kcal_no_meals')
        case 'progress':
          return t('game.detail.kcal_progress', { amount, low: number(d.zone_low ?? 0) })
        case 'in_zone':
          return t('game.detail.kcal_in_zone', { amount })
        case 'above_zone':
          return t('game.detail.kcal_above_zone', { amount })
        case 'below_zone':
          return t('game.detail.kcal_below_zone', { amount })
        case 'below_floor':
          return t('game.detail.kcal_below_floor', { amount })
        default:
          return t('game.detail.kcal_amount', { amount })
      }
    case 'protein':
    case 'fiber':
      return d.state === 'no_meals'
        ? t('game.detail.macro_no_meals')
        : t('game.detail.macro', { amount: number(d.amount ?? 0, 0), target })
    case 'water':
      return t('game.detail.water', { amount, target })
    case 'activity':
      if (d.state === 'workout') return t('game.detail.workout')
      if (d.state === 'no_data') return t('game.detail.steps_none', { target })
      return t('game.detail.steps', { amount, target })
    case 'tasks':
      if (d.state === 'no_tasks') return t('game.detail.tasks_none')
      return t('game.detail.tasks', { done: number(d.done ?? 0), open: number(d.open_due ?? 0) })
    case 'sleep':
      if (d.state === 'no_data') return t('game.detail.sleep_none')
      return t('game.detail.sleep', { duration: sleepDuration(d.amount), target: sleepDuration(d.target) })
    case 'review':
      return d.has_entry ? t('game.detail.review_done') : t('game.detail.review_none')
  }
}
