/**
 * "My food": a user-owned row with per-100 g macros. The fastest path when the
 * library has no match — the food immediately becomes searchable and ranks
 * above anonymous library rows.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Field, Input, Sheet } from '@/components/ui'
import { useToast } from '@/components/ui/Toast'
import { number } from '@/lib/format'
import { errorMessage } from '@/lib/errors'
import { useCreateFood } from './useNutrition'

export function CustomFoodSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const toast = useToast()
  const createFood = useCreateFood()

  const [nameRu, setNameRu] = useState('')
  const [nameEn, setNameEn] = useState('')
  const [kcal, setKcal] = useState('')
  const [protein, setProtein] = useState('')
  const [fat, setFat] = useState('')
  const [carbs, setCarbs] = useState('')
  const [fiber, setFiber] = useState('')
  const [serving, setServing] = useState('100')

  const valid = (nameRu.trim() || nameEn.trim()) && Number(kcal) > 0

  const reset = () => {
    setNameRu('')
    setNameEn('')
    setKcal('')
    setProtein('')
    setFat('')
    setCarbs('')
    setFiber('')
    setServing('100')
  }

  const save = () => {
    if (!valid) return
    createFood.mutate(
      {
        name_ru: nameRu.trim() || undefined,
        name_en: nameEn.trim() || undefined,
        kcal_per_100g: Number(kcal),
        protein_per_100g: Number(protein) || 0,
        fat_per_100g: Number(fat) || 0,
        carbs_per_100g: Number(carbs) || 0,
        fiber_per_100g: Number(fiber) || 0,
        default_serving_g: Number(serving) || undefined,
      },
      {
        onSuccess: (food) => {
          toast.success(t('food.custom_saved', { name: food.name_ru ?? food.name_en ?? '' }))
          reset()
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('food.custom_title')}>
      <div className="space-y-4 pb-2">
        <Field label={t('food.custom_name_ru')} htmlFor="cf-ru">
          <Input id="cf-ru" value={nameRu} onChange={(event) => setNameRu(event.target.value)} />
        </Field>
        <Field label={t('food.custom_name_en')} htmlFor="cf-en">
          <Input id="cf-en" value={nameEn} onChange={(event) => setNameEn(event.target.value)} />
        </Field>

        <p className="label">{t('food.per_100g')}</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('today.kcal')} htmlFor="cf-kcal">
            <Input id="cf-kcal" type="number" inputMode="decimal" value={kcal} onChange={(event) => setKcal(event.target.value)} />
          </Field>
          <Field label={`${t('today.protein')}, ${t('common.g')}`} htmlFor="cf-p">
            <Input id="cf-p" type="number" inputMode="decimal" value={protein} onChange={(event) => setProtein(event.target.value)} />
          </Field>
          <Field label={`${t('today.fat')}, ${t('common.g')}`} htmlFor="cf-f">
            <Input id="cf-f" type="number" inputMode="decimal" value={fat} onChange={(event) => setFat(event.target.value)} />
          </Field>
          <Field label={`${t('today.carbs')}, ${t('common.g')}`} htmlFor="cf-c">
            <Input id="cf-c" type="number" inputMode="decimal" value={carbs} onChange={(event) => setCarbs(event.target.value)} />
          </Field>
          <Field label={`${t('today.fiber')}, ${t('common.g')}`} htmlFor="cf-fb">
            <Input id="cf-fb" type="number" inputMode="decimal" value={fiber} onChange={(event) => setFiber(event.target.value)} />
          </Field>
          <Field label={`${t('food.serving')}, ${t('common.g')}`} htmlFor="cf-s">
            <Input id="cf-s" type="number" inputMode="decimal" value={serving} onChange={(event) => setServing(event.target.value)} />
          </Field>
        </div>

        <p className="text-xs muted">
          {t('food.custom_hint', { kcal: number(Number(kcal) || 0) })}
        </p>

        <Button block loading={createFood.isPending} onClick={save} disabled={!valid}>
          {t('food.custom_save')}
        </Button>
      </div>
    </Sheet>
  )
}
