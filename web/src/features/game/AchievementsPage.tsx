/**
 * /stats/achievements (stage 8): the level card with the 30-level ladder, XP
 * of the last 30 days (small SVG bars — no chart library needed here),
 * streaks with freeze tokens and the weekly workout streak explained, daily
 * and weekly quests with Claim, the achievements grid by category (unlocked
 * first, locked with progress, secret ones masked as «???») and how XP works.
 *
 * Reads `GET /gamification/profile` (+ `/today` for the quests and new
 * achievements to celebrate). Lazy route, one chunk.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { Card, ErrorState, IconButton, ProgressBar, Sheet, SkeletonCard } from '@/components/ui'
import { ProgressRing } from '@/components/ui/ProgressRing'
import { IconChevronLeft, IconChevronRight } from '@/components/ui/icons'
import { number } from '@/lib/format'
import { CelebrationSheet, useCelebration } from './Celebration'
import {
  achievementDescription,
  achievementName,
  achievementProgress,
  levelTitle,
  RARITY_STYLES,
  shortDate,
  shortDay,
  streakLength,
} from './format'
import { IconFlameLit, IconSnowflake } from './icons'
import { AchievementBadge, RarityPill } from './parts'
import { QuestList, useQuestClaimer } from './QuestList'
import { useGameAutoRefresh, useGameProfile, useGameToday } from './queries'
import { FREEZE, milestoneXp, STREAK_MILESTONES, XP_RULES } from './rules'
import type { Achievement, AchievementCategory, Level, ProfileXp, Streak, UserQuest, XpDay } from './types'

const CATEGORIES: AchievementCategory[] = [
  'nutrition',
  'training',
  'activity',
  'planner',
  'review',
  'streaks',
  'levels',
  'special',
]
const LADDER_AROUND = { before: 1, after: 3 }

export default function AchievementsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  useGameAutoRefresh()
  const profile = useGameProfile()
  const today = useGameToday()
  const { claim, pendingId } = useQuestClaimer()
  const celebration = useCelebration(today.data?.celebrate, true)
  const [detail, setDetail] = useState<Achievement | null>(null)

  // back to wherever the user came from; a deep link goes back to Today
  const back = () => ((window.history.state?.idx ?? 0) > 0 ? navigate(-1) : navigate('/today'))

  return (
    <div className="space-y-5" data-testid="achievements-page">
      <header className="flex items-center gap-3">
        <IconButton label={t('app.back')} icon={<IconChevronLeft size={20} />} variant="secondary" onClick={back} />
        <h1 className="h1 min-w-0 flex-1 truncate">{t('game.page.title')}</h1>
      </header>

      {profile.isPending ? (
        <>
          <SkeletonCard lines={4} />
          <SkeletonCard lines={3} />
          <SkeletonCard lines={5} />
        </>
      ) : profile.isError ? (
        <ErrorState message={t('game.page.error')} onRetry={() => void profile.refetch()} retryLabel={t('app.retry')} />
      ) : (
        <>
          <LevelCard xp={profile.data.xp} levels={profile.data.levels} />
          <XpCard days={profile.data.xp.last_30_days} />
          <StreaksCard streaks={profile.data.streaks} />
          <QuestsCard
            quests={today.data?.quests}
            failed={today.isError && !today.data}
            onRetry={() => void today.refetch()}
            onClaim={claim}
            pendingId={pendingId}
          />
          <AchievementsGrid achievements={profile.data.achievements} onOpen={setDetail} />
          <XpRulesCard levels={profile.data.levels} />
        </>
      )}

      <nav className="grid grid-cols-2 gap-2 pb-2" aria-label={t('game.page.title')}>
        <Link to="/today" className="btn-secondary min-h-tap px-3 text-sm" data-testid="achievements-to-today">
          <IconChevronLeft size={16} />
          <span className="truncate">{t('game.page.to_today')}</span>
        </Link>
        <Link to="/stats" className="btn-secondary min-h-tap px-3 text-sm" data-testid="achievements-to-stats">
          <span className="truncate">{t('game.page.to_stats')}</span>
          <IconChevronRight size={16} />
        </Link>
      </nav>

      <AchievementSheet item={detail} onClose={() => setDetail(null)} />
      <CelebrationSheet items={celebration.showing} onClose={celebration.close} />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Level
// --------------------------------------------------------------------------- //
function LevelCard({ xp, levels }: { xp: ProfileXp; levels: Level[] }) {
  const { t } = useTranslation()
  const { level } = xp
  const [all, setAll] = useState(false)
  const shown = all
    ? levels
    : levels.filter((row) => row.level >= level.level - LADDER_AROUND.before && row.level <= level.level + LADDER_AROUND.after)

  return (
    <Card className="space-y-4" data-testid="level-card" data-level={level.level} data-total={xp.total}>
      <div className="flex items-center gap-4">
        <ProgressRing
          value={level.progress}
          max={1}
          size={88}
          thickness={8}
          tone="violet"
          className="[&_svg]:overflow-visible"
          label={<span className="text-[30px]">{level.level}</span>}
          sublabel={t('game.page.level_word')}
        />
        <div className="min-w-0 flex-1">
          <p className="text-xl font-semibold leading-tight">{levelTitle(level)}</p>
          <p className="mt-0.5 text-sm muted">{t('game.level_of', { level: level.level, max: levels.length })}</p>
          <p className="tnum mt-1 text-sm font-semibold text-[rgb(var(--accent-2))]">
            {t('game.page.total_xp', { xp: number(xp.total) })}
          </p>
        </div>
      </div>
      <div className="space-y-1.5">
        <ProgressBar value={level.progress} max={1} tone="violet" />
        <div className="tnum flex flex-wrap justify-between gap-x-3 text-xs muted">
          {level.next_min_xp != null ? (
            <>
              <span>{t('game.xp_progress', { total: number(xp.total), next: number(level.next_min_xp) })}</span>
              <span>{t('game.xp_to_next', { xp: number(level.next_min_xp - xp.total), level: level.level + 1 })}</span>
            </>
          ) : (
            <span>{t('game.xp_max', { total: number(xp.total) })}</span>
          )}
        </div>
      </div>

      <section className="space-y-1" aria-label={t('game.page.ladder')}>
        <p className="label">{t('game.page.ladder')}</p>
        <ol className="divide-y divide-[rgb(var(--border))]/60" data-testid="level-ladder">
          {shown.map((row) => {
            const current = row.level === level.level
            const reached = row.level <= level.level
            return (
              <li
                key={row.level}
                className={clsx('flex items-center gap-3 py-2', current && 'font-semibold')}
                data-current={current}
                aria-label={t('game.page.ladder_label', { level: row.level, title: levelTitle(row), xp: number(row.min_xp) })}
              >
                <span
                  className={clsx(
                    'tnum flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-semibold',
                    current
                      ? 'bg-[rgb(var(--accent-2))] text-white'
                      : reached
                        ? 'bg-[rgb(var(--accent-2))]/15 text-[rgb(var(--accent-2))]'
                        : 'bg-[rgb(var(--bg-sunken))] text-[rgb(var(--text-muted))]',
                  )}
                >
                  {row.level}
                </span>
                <span className={clsx('min-w-0 flex-1 truncate text-sm', !reached && 'muted')}>{levelTitle(row)}</span>
                <span className="tnum shrink-0 text-xs muted">{number(row.min_xp)} XP</span>
              </li>
            )
          })}
        </ol>
        <button
          type="button"
          onClick={() => setAll((value) => !value)}
          className="flex min-h-tap w-full items-center justify-center gap-1 text-sm font-semibold text-[rgb(var(--accent-2))]"
          aria-expanded={all}
          data-testid="level-ladder-toggle"
        >
          {all ? t('game.page.ladder_less') : t('game.page.ladder_all', { count: levels.length })}
        </button>
      </section>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// XP — the last 30 days
// --------------------------------------------------------------------------- //
const BARS = { width: 300, height: 88, gap: 2 }

function XpCard({ days }: { days: XpDay[] }) {
  const { t } = useTranslation()
  const total = days.reduce((sum, day) => sum + day.xp, 0)
  const best = Math.max(0, ...days.map((day) => day.xp))
  const scale = Math.max(1, best)
  const bar = (BARS.width - BARS.gap * (days.length - 1)) / Math.max(1, days.length)

  return (
    <Card className="space-y-3" data-testid="xp-card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="h2">{t('game.page.xp_30')}</h2>
        <span className="tnum text-sm font-semibold text-[rgb(var(--accent-2))]" data-testid="xp-30-total">
          {t('game.page.xp_30_total', { xp: number(total) })}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${BARS.width} ${BARS.height}`}
        preserveAspectRatio="none"
        className="h-24 w-full"
        role="img"
        aria-label={t('game.page.xp_chart_label', { xp: number(total) })}
        data-testid="xp-bars"
      >
        {days.map((day, index) => {
          const height = day.xp > 0 ? Math.max(4, (day.xp / scale) * (BARS.height - 2)) : 2
          const last = index === days.length - 1
          return (
            <rect
              key={day.date}
              x={index * (bar + BARS.gap)}
              y={BARS.height - height}
              width={bar}
              height={height}
              rx={1.5}
              className={clsx(
                last
                  ? 'fill-[rgb(var(--accent-2))]'
                  : day.xp > 0
                    ? 'fill-[rgb(var(--accent-2))]/55'
                    : 'fill-[rgb(var(--border))]',
              )}
              data-xp={day.xp}
            >
              <title>{`${shortDay(day.date)}: ${number(day.xp)} XP`}</title>
            </rect>
          )
        })}
      </svg>
      <div className="tnum flex justify-between text-[11px] muted">
        <span>{days.length ? shortDay(days[0].date) : ''}</span>
        {best > 0 && <span>{t('game.page.xp_30_best', { xp: number(best) })}</span>}
        <span>{t('game.page.today')}</span>
      </div>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Streaks
// --------------------------------------------------------------------------- //
function StreaksCard({ streaks }: { streaks: Streak[] }) {
  const { t } = useTranslation()
  return (
    <Card className="space-y-2" data-testid="streaks-card">
      <h2 className="h2">{t('game.page.streaks')}</h2>
      <ul className="divide-y divide-[rgb(var(--border))]/60">
        {streaks.map((streak) => (
          <StreakRow key={streak.kind} streak={streak} />
        ))}
      </ul>
      <div className="space-y-2 pt-1 text-xs leading-relaxed muted">
        <p className="flex gap-2">
          <IconSnowflake size={14} className="mt-0.5 shrink-0 text-[rgb(var(--accent))]" />
          <span>{t('game.streak.freeze_rule', { perWeek: FREEZE.perWeek, max: FREEZE.max })}</span>
        </p>
        <p>{t('game.streak.workout_rule')}</p>
      </div>
    </Card>
  )
}

function StreakRow({ streak }: { streak: Streak }) {
  const { t } = useTranslation()
  const going = streak.current > 0
  const milestone = streak.kind === 'overall' ? milestoneXp(streak.next_milestone) : null
  return (
    <li
      className="space-y-1.5 py-3"
      data-testid="streak-row"
      data-kind={streak.kind}
      data-current={streak.current}
      data-longest={streak.longest}
      data-freeze={streak.freeze_tokens}
    >
      <div className="flex items-center gap-3">
        <IconFlameLit
          lit={streak.today_met}
          size={22}
          className={clsx('shrink-0', going ? 'text-[rgb(var(--warn))]' : 'text-[rgb(var(--text-muted))]')}
        />
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-medium leading-snug">{t(`game.streak.name.${streak.kind}`)}</p>
          <p className="text-xs leading-snug muted">{t(`game.streak.about.${streak.kind}`)}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tnum text-lg font-semibold leading-tight">{streakLength(streak, streak.current)}</p>
          <p className="tnum text-[11px] muted">
            {t('game.streak.longest', { length: streakLength(streak, streak.longest) })}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-[34px] text-xs muted">
        {streak.unit === 'week' && streak.period ? (
          <span className={clsx(streak.today_met && 'text-[rgb(var(--accent))]')}>
            {streak.period.required === 0
              ? t('game.streak.week_rest')
              : t('game.streak.week_progress', { done: streak.period.done, required: streak.period.required })}
          </span>
        ) : (
          <span className={clsx(streak.today_met && 'text-[rgb(var(--accent))]')}>
            {streak.today_met ? t('game.streak.today_met') : t('game.streak.today_pending')}
          </span>
        )}
        <span className="inline-flex items-center gap-1" data-testid="streak-freeze">
          <span className="inline-flex" aria-hidden="true">
            {Array.from({ length: FREEZE.max }, (_, index) => (
              <IconSnowflake
                key={index}
                size={13}
                className={index < streak.freeze_tokens ? 'text-[rgb(var(--accent))]' : 'opacity-35'}
              />
            ))}
          </span>
          {t('game.streak.freeze', { count: streak.freeze_tokens, max: FREEZE.max })}
        </span>
        {milestone != null && streak.next_milestone != null && (
          <span>{t('game.streak.next_milestone', { days: streak.next_milestone, xp: milestone })}</span>
        )}
      </div>
    </li>
  )
}

// --------------------------------------------------------------------------- //
// Quests
// --------------------------------------------------------------------------- //
function QuestsCard({
  quests,
  failed,
  onRetry,
  onClaim,
  pendingId,
}: {
  quests: UserQuest[] | undefined
  failed: boolean
  onRetry: () => void
  onClaim: (quest: UserQuest) => void
  pendingId?: string
}) {
  const { t } = useTranslation()
  const daily = (quests ?? []).filter((quest) => quest.kind === 'daily')
  const weekly = (quests ?? []).filter((quest) => quest.kind === 'weekly')
  return (
    <Card className="space-y-3" data-testid="quests-card">
      <h2 className="h2">{t('game.page.quests')}</h2>
      {failed ? (
        <ErrorState message={t('game.page.quests_error')} onRetry={onRetry} retryLabel={t('app.retry')} />
      ) : !quests ? (
        <div className="space-y-2">
          <div className="skeleton h-12" />
          <div className="skeleton h-12" />
        </div>
      ) : !quests.length ? (
        <p className="text-sm muted">{t('game.quests_none')}</p>
      ) : (
        <>
          {daily.length > 0 && (
            <section aria-label={t('game.quests_daily')}>
              <p className="label">{t('game.quests_daily')}</p>
              <QuestList quests={daily} onClaim={onClaim} pendingId={pendingId} testId="quests-daily" />
            </section>
          )}
          {weekly.length > 0 && (
            <section aria-label={t('game.quests_weekly')}>
              <p className="label">{t('game.quests_weekly')}</p>
              <QuestList quests={weekly} onClaim={onClaim} pendingId={pendingId} testId="quests-weekly" />
            </section>
          )}
        </>
      )}
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Achievements
// --------------------------------------------------------------------------- //
function AchievementsGrid({ achievements, onOpen }: { achievements: Achievement[]; onOpen: (item: Achievement) => void }) {
  const { t } = useTranslation()
  const unlocked = achievements.filter((item) => item.unlocked).length
  return (
    <section className="space-y-4" data-testid="achievements-grid" aria-label={t('game.page.achievements')}>
      <header className="flex items-baseline justify-between gap-3">
        <h2 className="h2">{t('game.page.achievements')}</h2>
        <span className="tnum text-sm muted" data-testid="achievements-count">
          {t('game.page.achievements_count', { unlocked, total: achievements.length })}
        </span>
      </header>
      {CATEGORIES.map((category) => {
        // the server's order: unlocked first (newest first), then locked in catalogue order
        const items = achievements.filter((item) => item.category === category)
        if (!items.length) return null
        return (
          <div key={category} className="space-y-2" data-testid="achievement-category" data-category={category}>
            <p className="flex items-baseline justify-between gap-3">
              <span className="label">{t(`game.category.${category}`)}</span>
              <span className="tnum text-xs muted">
                {t('game.page.category_count', {
                  unlocked: items.filter((item) => item.unlocked).length,
                  total: items.length,
                })}
              </span>
            </p>
            <div className="grid grid-cols-3 gap-2">
              {items.map((item) => (
                <AchievementTile key={item.id} item={item} onOpen={onOpen} />
              ))}
            </div>
          </div>
        )
      })}
    </section>
  )
}

function AchievementTile({ item, onOpen }: { item: Achievement; onOpen: (item: Achievement) => void }) {
  const { t } = useTranslation()
  const progress = !item.unlocked ? item.progress : null
  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      className={clsx(
        'card flex min-h-[8.5rem] flex-col items-center gap-2 px-2 pb-2.5 pt-3 text-center transition-transform active:scale-[.97]',
        !item.unlocked && 'shadow-none',
      )}
      data-testid="achievement-tile"
      data-code={item.code}
      data-unlocked={item.unlocked}
      data-masked={item.masked}
    >
      <AchievementBadge item={item} size={48} />
      <span className={clsx('line-clamp-2 text-xs font-medium leading-tight', !item.unlocked && 'muted')}>
        {achievementName(item)}
      </span>
      <span className="mt-auto w-full">
        {item.unlocked ? (
          <span className={clsx('text-[10px] font-semibold uppercase tracking-wide', RARITY_STYLES[item.rarity].text)}>
            {t(`game.rarity.${item.rarity}`)}
          </span>
        ) : progress ? (
          <span className="block space-y-1" data-testid="achievement-progress">
            <ProgressBar value={progress.value} max={progress.target} tone="violet" />
            <span className="tnum block text-[10px] muted">{achievementProgress(item)}</span>
          </span>
        ) : (
          <span className="text-[10px] muted">{t('game.page.secret_label')}</span>
        )}
      </span>
    </button>
  )
}

function AchievementSheet({ item, onClose }: { item: Achievement | null; onClose: () => void }) {
  const { t } = useTranslation()
  const description = item ? (item.masked ? t('game.page.secret_hint') : achievementDescription(item)) : null
  return (
    <Sheet open={!!item} onClose={onClose} title={item ? achievementName(item) : undefined}>
      {item && (
        <div className="flex flex-col items-center gap-3 pb-4 text-center" data-testid="achievement-sheet">
          <AchievementBadge item={item} size={80} glow />
          <div className="flex flex-wrap items-center justify-center gap-2">
            <RarityPill rarity={item.rarity} />
            <span className="text-xs muted">{t(`game.category.${item.category}`)}</span>
          </div>
          {description && <p className="max-w-xs text-sm leading-relaxed muted">{description}</p>}
          <p className="tnum text-lg font-semibold text-[rgb(var(--accent-2))]">{t('game.celebrate.xp', { xp: item.xp_reward })}</p>
          {item.unlocked ? (
            <p className="text-sm text-[rgb(var(--accent))]">{t('game.page.unlocked_on', { date: shortDate(item.unlocked_at) })}</p>
          ) : item.progress ? (
            <div className="w-full max-w-xs space-y-1.5">
              <ProgressBar value={item.progress.value} max={item.progress.target} tone="violet" />
              <p className="tnum text-sm muted">{achievementProgress(item)}</p>
            </div>
          ) : (
            <p className="text-sm muted">{t('game.page.locked')}</p>
          )}
        </div>
      )}
    </Sheet>
  )
}

// --------------------------------------------------------------------------- //
// How XP works
// --------------------------------------------------------------------------- //
function XpRulesCard({ levels }: { levels: Level[] }) {
  const { t } = useTranslation()
  const r = XP_RULES
  const groups = [
    {
      key: 'nutrition',
      text: t('game.rules.nutrition', {
        meal: r.meal.points,
        meals: r.meal.perDay,
        protein: r.protein,
        fiber: r.fiber,
        water: r.water,
        kcal: r.kcalZone,
      }),
    },
    {
      key: 'movement',
      text: t('game.rules.movement', {
        steps: r.steps,
        workout: r.workout.points,
        workouts: r.workout.perDay,
        record: r.record.points,
        records: r.record.perDay,
      }),
    },
    {
      key: 'planner',
      text: t('game.rules.planner', {
        ...r.task,
        project: r.project,
        minutes: r.timer.minutes,
        timerCap: r.timer.cap,
      }),
    },
    { key: 'review', text: t('game.rules.review', { review: r.review, sleep: r.sleep, weight: r.weight }) },
    {
      key: 'bonus',
      text: t('game.rules.bonus', {
        list: STREAK_MILESTONES.map(([days, xp]) => t('game.rules.streak_item', { days, xp })).join(', '),
      }),
    },
  ] as const
  const ladder = levels.length
    ? `${levels
        .slice(0, 5)
        .map((row) => number(row.min_xp))
        .join(' · ')} … ${number(levels[levels.length - 1].min_xp)}`
    : ''
  return (
    <Card className="space-y-3" data-testid="xp-rules">
      <h2 className="h2">{t('game.page.how_xp')}</h2>
      <p className="text-sm leading-relaxed muted">{t('game.rules.intro')}</p>
      <dl className="space-y-2.5">
        {groups.map((group) => (
          <div key={group.key} data-testid="xp-rule" data-group={group.key}>
            <dt className="label">{t(`game.rules.group.${group.key}`)}</dt>
            <dd className="tnum mt-0.5 text-sm leading-relaxed">{group.text}</dd>
          </div>
        ))}
      </dl>
      {ladder && <p className="tnum text-sm leading-relaxed">{t('game.rules.levels', { count: levels.length, list: ladder })}</p>}
      <div className="space-y-2 text-xs leading-relaxed muted">
        <p>{t('game.rules.floor_note')}</p>
        <p>{t('game.rules.burned_note')}</p>
        <p>{t('game.rules.window_note')}</p>
      </div>
    </Card>
  )
}
