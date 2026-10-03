/**
 * "Unverified" marker + one-tap confirm for library foods (stage 2b).
 *
 * Curated seed rows added in 2b, USDA/OFF imports and live barcode results
 * arrive unverified. The owner confirms a row with one tap once the numbers
 * look right; everybody else just sees the marker. Confirmed rows get the
 * small "verified" boost in search ranking.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Badge } from '@/components/ui'
import { IconCheck } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import type { Food } from '@/api/types'
import { errorMessage } from '@/lib/errors'
import { useAuth } from '@/features/auth/AuthProvider'
import { useVerifyFood } from './useNutrition'

export function useCanVerify(): boolean {
  const { session } = useAuth()
  return !!session?.user.is_owner
}

/** Compact control for a search-result row: a badge, or a confirm chip for the owner. */
export function VerifyChip({ food, className }: { food: Food; className?: string }) {
  const { t } = useTranslation()
  const toast = useToast()
  const canVerify = useCanVerify()
  const verify = useVerifyFood()

  if (food.verified) return null
  if (!canVerify) {
    return (
      <Badge tone="warn" className={className}>
        {t('food.unverified')}
      </Badge>
    )
  }
  return (
    <button
      type="button"
      className={clsx(
        'chip min-h-[2.25rem] shrink-0 gap-1 border-amber-400/40 px-2.5 text-xs text-amber-300',
        className,
      )}
      aria-label={t('food.verify_aria', { name: food.name_ru ?? food.name_en ?? '' })}
      disabled={verify.isPending}
      onClick={(event) => {
        event.stopPropagation()
        verify.mutate(food.id, {
          onSuccess: () => toast.success(t('food.verified_toast')),
          onError: (error) => toast.error(errorMessage(error)),
        })
      }}
    >
      <IconCheck size={13} />
      {t('food.verify')}
    </button>
  )
}

/** Explanatory line for the log sheet, with the same confirm action. */
export function VerifyNote({ food, onVerified }: { food: Food; onVerified?: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const canVerify = useCanVerify()
  const verify = useVerifyFood()
  // the sheet holds a snapshot of the food: hide the note as soon as it is confirmed
  const [done, setDone] = useState(false)

  if (food.verified || done) return null
  return (
    <div className="surface-sunken flex items-center gap-3 rounded-2xl px-3 py-2.5">
      <p className="min-w-0 flex-1 text-xs muted">
        {t('food.unverified_hint', { source: t(`food.source.${food.source}`) })}
      </p>
      {canVerify && (
        <button
          type="button"
          className="chip min-h-[2.25rem] shrink-0 border-amber-400/40 text-amber-300"
          disabled={verify.isPending}
          onClick={() =>
            verify.mutate(food.id, {
              onSuccess: () => {
                setDone(true)
                toast.success(t('food.verified_toast'))
                onVerified?.()
              },
              onError: (error) => toast.error(errorMessage(error)),
            })
          }
        >
          <IconCheck size={14} />
          {t('food.verify')}
        </button>
      )}
    </div>
  )
}
