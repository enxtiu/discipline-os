/**
 * Today → the day score card (stage 8): the score (0–100, «предварительно»
 * while the day runs) as a ring with the three rings beside it, XP today with
 * the level and its progress, streak flames (with the evening at-risk hint),
 * today's quests with Claim, and the celebration of new achievements.
 *
 * Everything is `GET /gamification/today` as the server computed it; the card
 * refetches after any write in the app (`useGameAutoRefresh`). Light on
 * purpose — it lives in the Today chunk: SVG rings, no charts.
 */
import { lazy, Suspense, useEffect, useRef, useState, type RefObject } from 'react'
import clsx from 'clsx'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Card, ErrorState, ProgressBar, Skeleton } from '@/components/ui'
import { ProgressRing } from '@/components/ui/ProgressRing'
import { IconChevronRight } from '@/components/ui/icons'
import { number } from '@/lib/format'
import { CelebrationPill, CelebrationSheet, useCelebration } from './Celebration'
import { IconFlameLit, IconSnowflake } from './icons'
import { levelTitle, percent01, RING_TONES, streakLength } from './format'
import { ProvisionalPill } from './parts'
import { QuestList, useQuestClaimer } from './QuestList'
import { useGameAutoRefresh, useGameToday } from './queries'
import type { Ring, Streak, XpToday } from './types'
import { useOnceTrue } from '@/lib/lazy'

// stage 9: the score breakdown loads when it is first opened
const ScoreSheet = lazy(() => import('./ScoreSheet').then((module) => ({ default: module.ScoreSheet })))

export function DayScoreCard() {
  const { t } = useTranslation()
  useGameAutoRefresh()
  const query = useGameToday()
  const data = query.data
  const { claim, claimedIds, pendingId } = useQuestClaimer()
  const [sheetOpen, setSheetOpen] = useState(false)
  const sheetUsed = useOnceTrue(sheetOpen)
  // new achievements are celebrated once the score itself has been on screen for a moment —
  // never over the meals and quick actions at the top of Today (the pill opens them any time)
  const scoreRow = useRef<HTMLDivElement>(null)
  const atScore = useDwellInView(scoreRow, !!data)
  const celebration = useCelebration(data?.celebrate, atScore && !sheetOpen)

  if (!data) {
    if (query.isError) {
      // the error box is a framed surface of its own — no card around it
      return (
        <div data-testid="day-score-card">
          <ErrorState message={t('game.load_error')} onRetry={() => void query.refetch()} retryLabel={t('app.retry')} />
        </div>
      )
    }
    return <ScoreCardSkeleton offline={query.fetchStatus === 'paused'} />
  }

  const { score } = data
  // today's quests, plus a weekly one while it waits for its claim (and once claimed here, as «Получено»)
  const quests = data.quests.filter(
    (quest) => quest.kind === 'daily' || quest.status === 'completed' || claimedIds.includes(quest.id),
  )
  const scoreLabel = number(Math.round(score.value))

  return (
    <>
      <Card className="space-y-4" data-testid="day-score-card" data-date={data.date}>
        <div className="flex items-center justify-between gap-3">
          <p className="label">{t('game.score_title')}</p>
          <Link
            to="/stats/achievements"
            className="-my-3 -mr-2 flex min-h-tap items-center gap-0.5 px-2 text-xs muted"
            data-testid="day-score-more"
          >
            {t('game.open_achievements')}
            <IconChevronRight size={14} />
          </Link>
        </div>

        <CelebrationPill items={celebration.pending} onOpen={celebration.open} />

        <div ref={scoreRow} className="flex items-center gap-4">
          <button
            type="button"
            onClick={() => setSheetOpen(true)}
            className="flex shrink-0 flex-col items-center gap-2 rounded-2xl"
            aria-label={t('game.score_open', { score: scoreLabel })}
            data-testid="day-score-button"
          >
            <ProgressRing
              value={score.value}
              max={100}
              size={112}
              thickness={10}
              tone="accent"
              className="[&_svg]:overflow-visible"
              label={
                <span className="text-[34px]" data-testid="day-score-value">
                  {scoreLabel}
                </span>
              }
              sublabel={t('game.score_of')}
            />
            {score.provisional && <ProvisionalPill />}
          </button>
          <div className="min-w-0 flex-1">
            {data.rings.map((ring) => (
              <RingRow key={ring.key} ring={ring} />
            ))}
            <button
              type="button"
              onClick={() => setSheetOpen(true)}
              className="-ml-1 flex min-h-tap items-center gap-0.5 px-1 text-xs font-semibold text-[rgb(var(--accent))]"
            >
              {t('game.score_how')}
              <IconChevronRight size={14} />
            </button>
          </div>
        </div>

        <div className="divider" />
        <LevelBlock xp={data.xp} />
        <div className="divider" />
        <StreakBlock streaks={data.streaks} />
        <div className="divider" />

        <section aria-label={t('game.quests_today')}>
          <p className="label">{t('game.quests_today')}</p>
          {quests.length ? (
            <QuestList quests={quests} onClaim={claim} pendingId={pendingId} testId="today-quests" />
          ) : (
            <p className="pt-2 text-sm muted">{t('game.quests_none')}</p>
          )}
        </section>
      </Card>

      {sheetUsed && (
        <Suspense fallback={null}>
          <ScoreSheet score={score} open={sheetOpen} onClose={() => setSheetOpen(false)} />
        </Suspense>
      )}
      <CelebrationSheet items={celebration.showing} onClose={celebration.close} />
    </>
  )
}

function RingRow({ ring }: { ring: Ring }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2.5 py-1" data-testid="score-ring" data-ring={ring.key} data-value={ring.value}>
      <ProgressRing value={ring.value} max={1} size={30} thickness={4} tone={RING_TONES[ring.key]} glow={false} />
      <span className="min-w-0 flex-1 truncate text-sm">{t(`game.rings.${ring.key}`)}</span>
      <span className="tnum shrink-0 text-sm font-semibold">{percent01(ring.value)}</span>
    </div>
  )
}

function LevelBlock({ xp }: { xp: XpToday }) {
  const { t } = useTranslation()
  const { level } = xp
  return (
    <Link
      to="/stats/achievements"
      className="block space-y-2"
      data-testid="day-level"
      data-level={level.level}
      data-total={xp.total}
    >
      <div className="flex items-center gap-2">
        <span className="tnum shrink-0 rounded-lg bg-[rgb(var(--accent-2))]/15 px-2 py-0.5 text-xs font-semibold text-[rgb(var(--accent-2))]">
          {t('game.level_short', { level: level.level })}
        </span>
        <span className="min-w-0 flex-1 truncate font-semibold">{levelTitle(level)}</span>
      </div>
      <ProgressBar value={level.progress} max={1} tone="violet" />
      <div className="tnum flex items-baseline justify-between gap-3 text-xs">
        <span className="font-semibold text-[rgb(var(--accent-2))]" data-testid="day-xp-today">
          {t('game.xp_today', { xp: number(xp.today) })}
        </span>
        <span className="muted" data-testid="day-xp-next">
          {level.next_min_xp != null
            ? t('game.xp_to_next', { xp: number(level.next_min_xp - xp.total), level: level.level + 1 })
            : t('game.xp_max', { total: number(xp.total) })}
        </span>
      </div>
    </Link>
  )
}

function StreakBlock({ streaks }: { streaks: Streak[] }) {
  const { t } = useTranslation()
  const going = streaks.filter((streak) => streak.current > 0)
  const risky = streaks.filter((streak) => streak.at_risk)
  return (
    <section className="space-y-2.5" data-testid="day-streaks" aria-label={t('game.streaks_title')}>
      <p className="label">{t('game.streaks_title')}</p>
      {going.length ? (
        <ul className="grid grid-cols-3 gap-2">
          {going.map((streak) => (
            <li
              key={streak.kind}
              className="surface-sunken min-w-0 px-2.5 py-2"
              data-testid="streak-chip"
              data-kind={streak.kind}
              data-current={streak.current}
              data-met={streak.today_met}
            >
              <span className="flex items-center gap-1">
                <IconFlameLit
                  lit={streak.today_met}
                  size={16}
                  className={clsx('shrink-0', streak.today_met ? 'text-[rgb(var(--warn))]' : 'text-[rgb(var(--text-muted))]')}
                />
                <span className="tnum truncate text-sm font-semibold">{streakLength(streak, streak.current)}</span>
              </span>
              <span className="mt-0.5 block truncate text-xs muted">{t(`game.streak.name.${streak.kind}`)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm leading-relaxed muted">{t('game.streaks_none')}</p>
      )}
      {risky.length > 0 && <AtRiskHint streaks={risky} />}
    </section>
  )
}

/**
 * After 18:00 (server rule): the streaks whose today does not count yet, and
 * what keeps each going — one box for all of them. Never about eating less.
 */
function AtRiskHint({ streaks }: { streaks: Streak[] }) {
  const { t } = useTranslation()
  const freezes = streaks.some((streak) => streak.freeze_tokens > 0)
  return (
    <div
      className="space-y-1.5 rounded-2xl border border-[rgb(var(--warn))]/40 bg-[rgb(var(--warn))]/10 px-3 py-2.5 text-sm leading-snug"
      data-testid="streak-at-risk"
      data-kinds={streaks.map((streak) => streak.kind).join(',')}
    >
      <p className="flex items-center gap-1.5 font-semibold">
        <IconFlameLit size={16} className="shrink-0 text-[rgb(var(--warn))]" />
        {t('game.streak.at_risk_title')}
      </p>
      <ul className="space-y-1">
        {streaks.map((streak) => (
          <li key={streak.kind} data-kind={streak.kind}>
            <span className="font-medium">
              {t(`game.streak.name.${streak.kind}`)} · <span className="tnum">{streakLength(streak, streak.current)}</span>
            </span>
            <span className="muted"> — {t(`game.streak.action.${streak.kind}`)}</span>
            {streak.freeze_tokens === 0 && <span className="muted"> · {t('game.streak.at_risk_no_freeze')}</span>}
          </li>
        ))}
      </ul>
      {freezes && (
        <p className="flex gap-1.5 text-xs muted">
          <IconSnowflake size={13} className="mt-px shrink-0 text-[rgb(var(--accent))]" />
          {t('game.streak.at_risk_freeze')}
        </p>
      )}
    </div>
  )
}

function ScoreCardSkeleton({ offline }: { offline: boolean }) {
  const { t } = useTranslation()
  return (
    <Card className="space-y-4" data-testid="day-score-card" aria-busy="true">
      <p className="label">{t('game.score_title')}</p>
      <div className="flex items-center gap-4">
        <Skeleton className="h-[112px] w-[112px] rounded-full" />
        <div className="flex-1 space-y-3">
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
      <Skeleton className="h-2 w-full" />
      {offline && <p className="text-xs muted">{t('game.offline')}</p>}
    </Card>
  )
}

/**
 * Has the element been on screen (above the tab bar, mostly visible) for a
 * moment? Goes false again when it leaves, so a celebration that arrives
 * while the user is elsewhere on Today waits until they come back to the card.
 */
function useDwellInView(ref: RefObject<Element>, enabled: boolean, dwellMs = 700): boolean {
  const [inView, setInView] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!enabled || !element || typeof IntersectionObserver === 'undefined') return
    let timer: number | undefined
    const observer = new IntersectionObserver(
      ([entry]) => {
        window.clearTimeout(timer)
        if (entry?.isIntersecting) timer = window.setTimeout(() => setInView(true), dwellMs)
        else setInView(false)
      },
      { rootMargin: '0px 0px -96px 0px', threshold: 0.6 },
    )
    observer.observe(element)
    return () => {
      window.clearTimeout(timer)
      observer.disconnect()
    }
  }, [ref, enabled, dwellMs])
  return inView
}
