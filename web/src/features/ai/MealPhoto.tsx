/**
 * The opted-in 400 px thumbnail of a meal logged from a photo (stage 3).
 * Fetched with the bearer token (an <img src> cannot send it) and shown small.
 */
import { useTranslation } from 'react-i18next'

import { useMealPhoto } from './useAi'

export function MealPhoto({ mealId, size = 36 }: { mealId: string; size?: number }) {
  const { t } = useTranslation()
  const photo = useMealPhoto(mealId, true)
  if (!photo.data) return <span className="shrink-0 rounded-lg bg-[rgb(var(--border))]/50" style={{ width: size, height: size }} />
  return (
    <img
      src={photo.data}
      alt={t('ai.meal_photo_alt')}
      width={size}
      height={size}
      className="shrink-0 rounded-lg object-cover"
      style={{ width: size, height: size }}
    />
  )
}
