/** Start a workout and open it; a workout already running is opened instead. */
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { useToast } from '@/components/ui/Toast'
import { ApiError } from '@/api/client'
import { errorMessage } from '@/lib/errors'
import { useStartWorkout } from './useTraining'

export function useStart() {
  const start = useStartWorkout()
  const navigate = useNavigate()
  const toast = useToast()
  const { t } = useTranslation()
  return {
    pending: start.isPending,
    run: (templateId?: string, title?: string) =>
      start.mutate(
        { id: crypto.randomUUID(), template_id: templateId, title },
        {
          onSuccess: (workout) => navigate(`/train/workout/${workout.id}`),
          onError: (error) => {
            const active = error instanceof ApiError ? (error.details as { workout_id?: string } | undefined) : undefined
            if (error instanceof ApiError && error.code === 'workout_in_progress' && active?.workout_id) {
              toast.info(t('train.continue_running'))
              navigate(`/train/workout/${active.workout_id}`)
              return
            }
            toast.error(errorMessage(error))
          },
        },
      ),
  }
}

