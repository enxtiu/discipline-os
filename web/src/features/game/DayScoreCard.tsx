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
import { useEffect, useRef, useState, type RefObject } from 'react'
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
import { ScoreSheet } from './ScoreSheet'
import { useGameAutoRefresh, useGameToday } from './queries'
import type { Ring, Streak, XpToday } from './types'

export function DayScoreCard() {
  const { t } = useTranslation()
  useGameAutoRefresh()
  const query = useGameToday()
  const data = query.data
  const { claim, pendingId } = useQuestClaimer()
  const [sheetOpen, setSheetOpen] = useState(false)
  // new achievements are celebrated once the card itself is on screen — never over the top of Today
  const end = useRef<HTMLDivElement>(null)
  const atCard = useInView(end, !!data)
  const celebration = useCelebration(data?.celebrate, atCard)

  if (!data) {
    if (query.isError) {
      return (
        <Card data-testid="day-score-card">
          <ErrorState message={t('game.load_error')} onRetry={() => void query.refetch()} retryLabel={t('app.retry')} />
        </Card>
      )
    }
    return <ScoreCardSkeleton offline={query.fetchStatus === 'paused'} />
  }

  const { score } = data
  const quests = data.quests.filter((quest) => quest.kind === 'daily' || quest.status === 'completed')
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

        <div className="flex items-center gap-4">
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
        <div ref={end} className="h-px" aria-hidden="true" />
      </Card>

      <ScoreSheet score={score} open={sheetOpen} onClose={() => setSheetOpen(false)} />
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
    <Link to="/stats/achievements" className="block space-y-2" data-testid="day-level" data-level={level.level}>
      <div className="flex items-center gap-2">
        <span className="tnum shrink-0 rounded-lg bg-[rgb(var(--accent-2))]/15 px-2 py-0.5 text-xs font-semibold text-[rgb(var(--accent-2))]">
          {t('game.level_short', { level: level.level })}
        </span>
        <span className="min-w-0 flex-1 truncate font-semibold">{levelTitle(level)}</span>
        <span className="tnum shrink-0 text-sm font-semibold text-[rgb(var(--accent-2))]" data-testid="day-xp-today">
          {t('game.xp_today', { xp: number(xp.today) })}
        </span>
      </div>
      <ProgressBar value={level.progress} max={1} tone="violet" />
      <div className="tnum flex flex-wrap justify-between gap-x-3 gap-y-0.5 text-xs muted">
        {level.next_min_xp != null ? (
          <>
            <span data-testid="day-xp-total">
              {t('game.xp_progress', { total: number(xp.total), next: number(level.next_min_xp) })}
            </span>
            <span>{t('game.xp_to_next', { xp: number(level.next_min_xp - xp.total), level: level.level + 1 })}</span>
          </>
        ) : (
          <span data-testid="day-xp-total">{t('game.xp_max', { total: number(xp.total) })}</span>
        )}
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
        <ul className="flex flex-wrap gap-2">
          {going.map((streak) => (
            <li
              key={streak.kind}
              className="inline-flex items-center gap-1.5 rounded-pill bg-[rgb(var(--bg-sunken))] px-3 py-1.5 text-sm"
              data-testid="streak-chip"
              data-kind={streak.kind}
              data-current={streak.current}
            >
              <IconFlameLit
                lit={streak.today_met}
                size={16}
                className={streak.today_met ? 'text-[rgb(var(--warn))]' : 'text-[rgb(var(--text-muted))]'}
              />
              <span className="tnum font-semibold">{streakLength(streak, streak.current)}</span>
              <span className="muted">{t(`game.streak.name.${streak.kind}`)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm leading-relaxed muted">{t('game.streaks_none')}</p>
      )}
      {risky.map((streak) => (
        <AtRiskHint key={streak.kind} streak={streak} />
      ))}
    </section>
  )
}

/** After 18:00 (server rule): today does not count yet — what keeps the streak going. Never about eating less. */
function AtRiskHint({ streak }: { streak: Streak }) {
  const { t } = useTranslation()
  return (
    <div
      className="flex gap-2.5 rounded-2xl border border-[rgb(var(--warn))]/40 bg-[rgb(var(--warn))]/10 px-3 py-2.5 text-sm leading-relaxed"
      data-testid="streak-at-risk"
      data-kind={streak.kind}
    >
      <IconFlameLit size={18} className="mt-0.5 shrink-0 text-[rgb(var(--warn))]" />
      <p>
        {t('game.streak.at_risk', {
          name: t(`game.streak.name.${streak.kind}`),
          length: streakLength(streak, streak.current),
          action: t(`game.streak.action.${streak.kind}`),
        })}{' '}
        <span className="muted">
          {streak.freeze_tokens > 0 ? (
            <>
              <IconSnowflake size={13} className="mb-0.5 mr-0.5 inline" />
              {t('game.streak.at_risk_freeze', { count: streak.freeze_tokens })}
            </>
          ) : (
            t('game.streak.at_risk_no_freeze')
          )}
        </span>
      </p>
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

/** Is the element on screen (above the tab bar)? */
function useInView(ref: RefObject<Element>, enabled: boolean): boolean {
  const [inView, setInView] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!enabled || !element || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(([entry]) => setInView(!!entry?.isIntersecting), {
      rootMargin: '0px 0px -96px 0px',
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref, enabled])
  return inView
}
