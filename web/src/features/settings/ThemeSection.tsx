/**
 * Настройки → Оформление (stage 10): the theme, the light / dark / system mode, the
 * theme's own parameters and two switches for every theme — with a live preview.
 *
 * Every theme has the same screens and features; only the look differs. Today two can
 * be chosen («Эволюция», the default, and «Наклейки»); the others are shown as «Скоро».
 * Per device (lib/appearance.ts — localStorage, applied before the first paint); the
 * change repaints the whole app at once, nothing is sent to the server yet.
 */
import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'

import { Card, ProgressBar, Row, Rows, SegmentedControl, Toggle } from '@/components/ui'
import { IconCheck } from '@/components/ui/icons'
import { useAuth } from '@/features/auth/AuthProvider'
import { useDayView } from '@/features/food/useNutrition'
import {
  GROUND_HEX,
  GROUNDS,
  setAppearance,
  systemReducedMotion,
  useAppearance,
  useResolvedTheme,
  type Ground,
  type Mode,
  type ReservedSkin,
  type Skin,
} from '@/lib/appearance'
import { number } from '@/lib/format'

/** The gallery, A–I as in the design boards: the colours each mini preview is drawn with. */
interface GalleryItem {
  id: Skin | ReservedSkin
  letter: string
  available: boolean
  colors: { ground: string; card: string; text: string; accent: string }
}

const GALLERY: GalleryItem[] = [
  { id: 'forge', letter: 'A', available: false, colors: { ground: '#0F1012', card: '#18191C', text: '#F3F1EC', accent: '#FF5A1F' } },
  { id: 'clarity', letter: 'B', available: false, colors: { ground: '#F3F4EF', card: '#FFFFFF', text: '#17191C', accent: '#1F6F54' } },
  { id: 'pulse', letter: 'C', available: false, colors: { ground: '#0F0C1A', card: '#1A1630', text: '#F4F2FF', accent: '#C8F23C' } },
  { id: 'notebook', letter: 'D', available: false, colors: { ground: '#F3F4F1', card: '#FFFFFF', text: '#1D2230', accent: '#2347A6' } },
  { id: 'ios', letter: 'E', available: false, colors: { ground: '#F2F2F7', card: '#FFFFFF', text: '#000000', accent: '#0062CC' } },
  { id: 'stickers', letter: 'F', available: true, colors: { ground: '#E8EAFB', card: '#FFFFFF', text: '#111111', accent: '#FFD84D' } },
  { id: 'evolution', letter: 'G', available: true, colors: { ground: '#0B1220', card: '#111A2B', text: '#E8EEF7', accent: '#38E0C8' } },
  { id: 'gentle', letter: 'H', available: false, colors: { ground: '#EFF2F7', card: '#FFFFFF', text: '#1E2433', accent: '#3554D1' } },
  { id: 'glass', letter: 'I', available: false, colors: { ground: '#070B14', card: '#121826', text: '#F2F5FA', accent: '#4DA3FF' } },
]

export function ThemeSection() {
  const { t } = useTranslation()
  const appearance = useAppearance()
  const theme = useResolvedTheme()
  const stickers = appearance.skin === 'stickers'

  return (
    <div className="space-y-5 pb-6" data-testid="theme-section" data-skin={appearance.skin}>
      <h1 className="h1">{t('look.title')}</h1>

      <Preview />

      <Group title={t('look.skin_title')} id="look-skin">
        <ThemeGallery value={appearance.skin} onChange={(skin) => setAppearance({ skin })} />
      </Group>

      <Group title={t('look.mode_title')} id="look-mode">
        <SegmentedControl<Mode>
          label={t('look.mode_title')}
          value={appearance.mode}
          onChange={(mode) => setAppearance({ mode })}
          options={[
            { value: 'light', label: t('look.mode.light') },
            { value: 'dark', label: t('look.mode.dark') },
            { value: 'system', label: t('look.mode.system') },
          ]}
        />
      </Group>

      {stickers && (
        <Group title={t('look.params_title')} id="look-params">
          <Card className="sk-params space-y-3" data-testid="theme-stickers-params">
            <section className="space-y-2" aria-labelledby="look-ground">
              <h3 id="look-ground" className="text-xs font-medium muted">
                {t('look.ground_title')}
              </h3>
              <div className="grid grid-cols-4 gap-2" role="group" aria-labelledby="look-ground">
                {GROUNDS.map((ground) => (
                  <GroundSwatch
                    key={ground}
                    ground={ground}
                    pressed={appearance.ground === ground}
                    onPress={() => setAppearance({ ground })}
                  />
                ))}
              </div>
              {theme === 'dark' && <p className="text-xs muted">{t('look.ground_night')}</p>}
            </section>
            <div className="divider" />
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-[15px] font-medium">{t('look.tilt')}</p>
                <p className="text-xs muted">{t('look.tilt_hint')}</p>
              </div>
              <Toggle label={t('look.tilt')} checked={appearance.tilt} onChange={(tilt) => setAppearance({ tilt })} />
            </div>
            <div className="divider" />
            <div className="flex items-center gap-3">
              <p id="look-shadow" className="min-w-0 flex-1 text-[15px] font-medium">
                {t('look.shadow_title')}
              </p>
              <div className="segmented w-[13rem] shrink-0" role="group" aria-labelledby="look-shadow">
                {(['hard', 'soft'] as const).map((shadow) => (
                  <button
                    key={shadow}
                    type="button"
                    aria-pressed={appearance.shadow === shadow}
                    data-active={appearance.shadow === shadow}
                    onClick={() => setAppearance({ shadow })}
                    data-testid={`look-shadow-${shadow}`}
                  >
                    {t(`look.shadow.${shadow}`)}
                  </button>
                ))}
              </div>
            </div>
          </Card>
        </Group>
      )}

      <Group title={t('look.global_title')} id="look-global">
        <Rows>
          <Row
            label={t('look.large_text')}
            hint={t('look.large_text_hint')}
            trailing={
              <Toggle
                label={t('look.large_text')}
                checked={appearance.largeText}
                onChange={(largeText) => setAppearance({ largeText })}
              />
            }
          />
          <Row
            label={t('look.reduce_motion')}
            hint={systemReducedMotion() ? t('look.reduce_motion_system') : t('look.reduce_motion_hint')}
            trailing={
              <Toggle
                label={t('look.reduce_motion')}
                checked={appearance.reduceMotion}
                onChange={(reduceMotion) => setAppearance({ reduceMotion })}
              />
            }
          />
        </Rows>
      </Group>

      <p className="text-xs muted">{t('look.device_note')}</p>
    </div>
  )
}

function Group({ title, id, children }: { title: string; id: string; children: ReactNode }) {
  return (
    <section className="space-y-2" aria-labelledby={id}>
      <h2 id={id} className="label sk-group-title">
        {title}
      </h2>
      {children}
    </section>
  )
}

// --------------------------------------------------------------------------- //
// Preview: a tiny «Сегодня» in the current look (today's real numbers)
// --------------------------------------------------------------------------- //
function Preview() {
  const { t } = useTranslation()
  const { session } = useAuth()
  const day = useDayView().data
  const targets = session?.targets as Record<string, number | null> | undefined
  const target = targets?.target_kcal ?? null
  const eaten = day?.totals ?? { kcal: 0, protein_g: 0, fat_g: 0, carbs_g: 0, fiber_g: 0 }
  const left = target != null ? Math.max(0, Math.round(target - eaten.kcal)) : null
  const g = t('common.g')
  const kcal = t('common.kcal')

  return (
    <section className="space-y-2" aria-labelledby="look-preview">
      <h2 id="look-preview" className="label sk-group-title">
        {t('look.preview_title')}
      </h2>
      <div
        className="sk-preview grid grid-cols-[1.4fr_1fr] gap-2.5 rounded-card border border-[rgb(var(--border))] bg-[rgb(var(--bg))] p-3"
        data-testid="theme-preview"
      >
        <div className="card sk-tile min-w-0 space-y-1 px-2.5 py-2" data-cat="kcal">
          <p className="tnum truncate text-sm font-semibold">
            {left != null ? t('today.left_kcal', { kcal: number(left) }) : t('today.no_target')}
          </p>
          <p className="sk-soft tnum truncate text-xs muted">
            {number(eaten.kcal)} {target != null ? t('today.of_kcal', { kcal: number(target) }) : kcal}
          </p>
          <ProgressBar value={eaten.kcal} max={target ?? 1} tone="kcal" className="sk-bar-mini mt-1.5" />
        </div>
        <div className="card sk-tile min-w-0 space-y-1.5 px-2.5 py-2">
          <PreviewMacro label={t('today.protein')} value={eaten.protein_g} max={targets?.target_protein_g ?? null} unit={g} tone="protein" />
          <PreviewMacro label={t('today.fat')} value={eaten.fat_g} max={targets?.target_fat_g ?? null} unit={g} tone="fat" />
        </div>
      </div>
    </section>
  )
}

function PreviewMacro({
  label,
  value,
  max,
  unit,
  tone,
}: {
  label: string
  value: number
  max: number | null
  unit: string
  tone: 'protein' | 'fat'
}) {
  return (
    <div className="min-w-0 space-y-1">
      <p className="flex items-baseline justify-between gap-1.5 text-xs">
        <span className="truncate font-semibold">{label}</span>
        <span className="tnum shrink-0 muted">
          {number(value)}
          {max != null ? `/${number(max)}` : ''} {unit}
        </span>
      </p>
      <ProgressBar value={value} max={max ?? 1} tone={tone} className="sk-bar-mini h-1.5" />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// «Тема»: a radio group of nine tiles (arrows move and choose, as any radio group)
// --------------------------------------------------------------------------- //
function ThemeGallery({ value, onChange }: { value: Skin; onChange: (skin: Skin) => void }) {
  const { t } = useTranslation()
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const checkedIndex = Math.max(0, GALLERY.findIndex((item) => item.id === value))

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = (index + 1) % GALLERY.length
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        next = (index - 1 + GALLERY.length) % GALLERY.length
        break
      case 'Home':
        next = 0
        break
      case 'End':
        next = GALLERY.length - 1
        break
      default:
        return
    }
    event.preventDefault()
    refs.current[next]?.focus()
    const item = GALLERY[next]
    if (item.available) onChange(item.id as Skin)
  }

  return (
    <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={t('look.skin_title')} data-testid="theme-gallery">
      {GALLERY.map((item, index) => {
        const checked = item.id === value
        return (
          <button
            key={item.id}
            ref={(element) => {
              refs.current[index] = element
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-disabled={item.available ? undefined : true}
            tabIndex={index === checkedIndex ? 0 : -1}
            onClick={() => item.available && onChange(item.id as Skin)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={clsx(
              'sk-choice relative flex min-h-[3.25rem] min-w-0 rounded-xl border-2 p-[5px] text-left',
              checked ? 'border-[rgb(var(--accent))] ring-2 ring-[rgb(var(--accent))]' : 'border-[rgb(var(--border))]',
              !item.available && 'cursor-default',
            )}
            style={{ background: item.colors.ground }}
            data-skin-option={item.id}
            data-available={item.available}
          >
            <ThemeMini item={item} name={t(`look.skins.${item.id}`)} />
            {checked && (
              <span
                aria-hidden="true"
                className="sk-check absolute -right-2 -top-2 flex h-[22px] w-[22px] items-center justify-center rounded-full border-2 border-[rgb(var(--bg))] bg-[rgb(var(--accent))] text-on-accent"
              >
                <IconCheck size={12} strokeWidth={3} />
              </span>
            )}
            {!item.available && (
              <span className="sk-soon absolute -right-1.5 -top-2 rounded-md border border-[#111111] bg-white px-1 text-[10px] font-semibold leading-[14px] text-[#111111]">
                {t('look.soon')}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/** The theme's own colours: its card on its ground, its name in its text colour, an accent bar. */
function ThemeMini({ item, name }: { item: GalleryItem; name: string }) {
  const { card, text, accent } = item.colors
  const stickers = item.id === 'stickers'
  return (
    <span
      className="flex min-w-0 flex-1 flex-col items-start justify-center gap-1 rounded-[7px] px-2"
      style={{ background: card, border: stickers ? `1.5px solid ${text}` : undefined }}
    >
      <span className="max-w-full truncate text-xs font-semibold leading-[14px]" style={{ color: text }}>
        {name}
      </span>
      <span
        aria-hidden="true"
        className="block w-[34px] rounded-full"
        style={{
          height: stickers ? 6 : 4,
          background: accent,
          border: stickers ? `1.5px solid ${text}` : undefined,
        }}
      />
    </span>
  )
}

function GroundSwatch({ ground, pressed, onPress }: { ground: Ground; pressed: boolean; onPress: () => void }) {
  const { t } = useTranslation()
  const name = t(`look.ground.${ground}`)
  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={name}
      onClick={onPress}
      className={clsx(
        'sk-swatch flex min-h-[3.75rem] min-w-0 flex-col items-center justify-center gap-1 rounded-xl border px-1',
        pressed ? 'border-2 border-[rgb(var(--accent))] px-[3px]' : 'border-[rgb(var(--border))]',
      )}
      data-ground-option={ground}
    >
      <span
        aria-hidden="true"
        className="flex h-[26px] w-[26px] items-center justify-center rounded-full border-2 border-[#111111]"
        style={{ background: GROUND_HEX[ground] }}
      >
        {pressed && <IconCheck size={14} strokeWidth={3} className="text-[#111111]" />}
      </span>
      <span className={clsx('max-w-full truncate text-xs', pressed ? 'font-semibold' : 'font-medium')}>{name}</span>
    </button>
  )
}
