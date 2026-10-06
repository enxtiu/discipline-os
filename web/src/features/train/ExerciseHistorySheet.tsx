/** One exercise: all-time bests, records and the last sessions. */
import { useTranslation } from 'react-i18next'

import { Sheet, Skeleton } from '@/components/ui'
import { IconStar } from '@/components/ui/icons'
import type { Bests, LogMode } from '@/api/types'
import { dayLabel, number } from '@/lib/format'
import { clock, exerciseName, km, recordValue, weight } from './format'
import { useExerciseHistory } from './useTraining'

function bestTiles(bests: Bests, mode: LogMode, t: (key: string) => string): { label: string; value: string }[] {
  const kg = t('common.kg')
  if (mode === 'weight_reps') {
    return [
      { label: t('train.best_weight'), value: bests.weight_kg != null ? `${weight(bests.weight_kg)} ${kg}` : '—' },
      {
        label: t('train.best_e1rm'),
        value: bests.est_1rm_kg != null ? `${weight(Math.round(bests.est_1rm_kg * 2) / 2)} ${kg}` : '—',
      },
      { label: t('train.best_volume'), value: bests.volume_kg != null ? `${number(bests.volume_kg)} ${kg}` : '—' },
    ]
  }
  if (mode === 'reps') return [{ label: t('train.best_reps'), value: bests.reps != null ? number(bests.reps) : '—' }]
  if (mode === 'duration') {
    return [{ label: t('train.best_duration'), value: bests.duration_s != null ? clock(bests.duration_s) : '—' }]
  }
  return [
    { label: t('train.best_distance'), value: km(bests.distance_m) },
    { label: t('train.best_duration'), value: bests.duration_s != null ? clock(bests.duration_s) : '—' },
  ]
}

export function ExerciseHistorySheet({ exerciseId, onClose }: { exerciseId: string | null; onClose: () => void }) {
  const { t } = useTranslation()
  const history = useExerciseHistory(exerciseId)
  const data = history.data
  const mode = (data?.exercise.log_mode ?? 'weight_reps') as LogMode

  return (
    <Sheet open={!!exerciseId} onClose={onClose} title={data ? exerciseName(data.exercise) : t('train.exercise')}>
      {!data ? (
        <div className="space-y-2 pb-2">
          <Skeleton className="h-16" />
          <Skeleton className="h-10" />
        </div>
      ) : (
        <div className="space-y-4 pb-2">
          <p className="text-sm muted">
            {t(`train.muscle.${data.exercise.muscle_group}`)}, {t(`train.equipment.${data.exercise.equipment}`).toLowerCase()}
          </p>
          <div className="grid grid-cols-3 gap-2">
            {bestTiles(data.bests, mode, t).map((tile) => (
              <div key={tile.label} className="surface-sunken rounded-2xl p-3">
                <p className="text-xs muted">{tile.label}</p>
                <p className="tnum mt-0.5 text-base font-semibold">{tile.value}</p>
              </div>
            ))}
          </div>

          {data.records.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-sm font-semibold">{t('train.records')}</p>
              {data.records.slice(0, 5).map((record) => (
                <div key={record.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <IconStar size={14} filled className="shrink-0 text-violet-300" />
                    <span className="truncate">{t(`train.record_kind.${record.kind}`)}</span>
                  </span>
                  <span className="tnum shrink-0">
                    {recordValue(record)} <span className="text-xs muted">{dayLabel(record.achieved_at.slice(0, 10))}</span>
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="space-y-1.5">
            <p className="text-sm font-semibold">{t('train.sessions')}</p>
            {data.sessions.length === 0 && <p className="text-sm muted">{t('train.no_sessions')}</p>}
            {data.sessions.map((session) => (
              <div key={session.workout_id} className="flex items-center justify-between gap-3 text-sm">
                <span className="muted">{dayLabel(session.local_day)}</span>
                <span className="tnum">
                  {mode === 'weight_reps' && session.top_weight_kg != null
                    ? t('train.session_line', {
                        sets: session.sets,
                        top: `${weight(session.top_weight_kg)} × ${session.top_reps ?? 0}`,
                      })
                    : mode === 'cardio'
                      ? [km(session.distance_m), session.duration_s ? clock(session.duration_s) : null]
                          .filter(Boolean)
                          .join(', ')
                      : mode === 'duration'
                        ? clock(session.duration_s ?? 0)
                        : t('train.session_reps', { sets: session.sets, reps: session.reps })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Sheet>
  )
}
