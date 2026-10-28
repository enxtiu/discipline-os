/**
 * Quests with progress and a claim button — used on the Today card (today's
 * quests) and on the achievements screen (daily + weekly). Progress and status
 * come from the server; claiming is idempotent there (a repeated claim → 0 XP).
 */
import { useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { Button, ProgressBar } from '@/components/ui'
import { IconCheck } from '@/components/ui/icons'
import { useToast } from '@/components/ui/Toast'
import { ApiError } from '@/api/client'
import { errorMessage } from '@/lib/errors'
import { GameIcon } from './icons'
import { questProgress, questTitle } from './format'
import { gameKeys, useClaimQuest } from './queries'
import type { UserQuest } from './types'

const CLAIM_ERRORS = new Set(['quest_not_completed', 'quest_expired', 'quest_not_found'])

/**
 * Claim with toasts: «+20 XP» on success, an honest note for a repeated claim.
 * `claimedIds` — quests claimed on this screen, so a list that only shows quests
 * waiting for a claim (weekly ones on Today) can keep them as «Получено».
 */
export function useQuestClaimer() {
  const { t } = useTranslation()
  const toast = useToast()
  const queryClient = useQueryClient()
  const mutation = useClaimQuest()
  const [claimedIds, setClaimedIds] = useState<string[]>([])
  const claim = (quest: UserQuest) =>
    mutation.mutate(quest, {
      onSuccess: (result) => {
        setClaimedIds((ids) => (ids.includes(result.quest.id) ? ids : [...ids, result.quest.id]))
        if (result.xp_awarded > 0) {
          toast.success(t('game.claim_toast', { xp: result.xp_awarded, title: questTitle(result.quest) }))
        } else {
          toast.info(t('game.claim_again'))
        }
      },
      onError: (error) => {
        toast.error(
          error instanceof ApiError && CLAIM_ERRORS.has(error.code) ? t(`game.errors.${error.code}`) : errorMessage(error),
        )
        void queryClient.invalidateQueries({ queryKey: gameKeys.all }) // the quest moved on: show where it is
      },
    })
  return { claim, claimedIds, pendingId: mutation.isPending ? mutation.variables?.id : undefined }
}

export function QuestList({
  quests,
  onClaim,
  pendingId,
  testId,
}: {
  quests: UserQuest[]
  onClaim: (quest: UserQuest) => void
  pendingId?: string
  testId?: string
}) {
  return (
    <ul className="divide-y divide-[rgb(var(--border))]/60" data-testid={testId}>
      {quests.map((quest) => (
        <QuestRow key={quest.id} quest={quest} onClaim={onClaim} claiming={pendingId === quest.id} />
      ))}
    </ul>
  )
}

function QuestRow({ quest, onClaim, claiming }: { quest: UserQuest; onClaim: (quest: UserQuest) => void; claiming: boolean }) {
  const { t } = useTranslation()
  const title = questTitle(quest)
  const claimed = quest.status === 'claimed'
  return (
    <li
      className="flex min-h-[3.75rem] items-center gap-3 py-2.5"
      data-testid="quest-row"
      data-quest={quest.code}
      data-status={quest.status}
    >
      <span
        className={clsx(
          'flex h-10 w-10 shrink-0 items-center justify-center rounded-xl',
          claimed
            ? 'bg-[rgb(var(--accent))]/10 text-[rgb(var(--accent))]'
            : 'bg-[rgb(var(--accent-2))]/10 text-[rgb(var(--accent-2))]',
        )}
      >
        {claimed ? <IconCheck size={20} /> : <GameIcon icon={quest.icon} size={20} />}
      </span>
      <div className="min-w-0 flex-1">
        <p className={clsx('text-sm font-medium leading-snug', claimed && 'muted')}>{title}</p>
        <div className="mt-1.5 flex items-center gap-2">
          <ProgressBar value={quest.progress} max={quest.target} tone={claimed ? 'accent' : 'violet'} />
          <span className="tnum shrink-0 text-xs muted" data-testid="quest-progress">
            {questProgress(quest)} · {t('game.quest_reward', { xp: quest.xp_reward })}
          </span>
        </div>
        {quest.status === 'active' && quest.progress >= quest.target && (
          // the amount is reached, but the server credits nutrition quests only on a day with food
          // logged at or above the safe floor (water, protein…) — say why it is not «Получить» yet
          <p className="mt-1 text-xs muted" data-testid="quest-needs-food">
            {t('game.quest_needs_food')}
          </p>
        )}
      </div>
      {quest.status === 'completed' && (
        <Button
          className="shrink-0 px-3.5"
          loading={claiming}
          onClick={() => onClaim(quest)}
          aria-label={t('game.claim_label', { xp: quest.xp_reward, title })}
          data-testid="quest-claim"
        >
          {t('game.claim')}
        </Button>
      )}
      {claimed && (
        <span className="flex shrink-0 items-center gap-1 text-xs font-semibold text-[rgb(var(--accent))]">
          <IconCheck size={14} />
          {t('game.claimed')}
        </span>
      )}
      {quest.status === 'expired' && <span className="shrink-0 text-xs muted">{t('game.expired')}</span>}
    </li>
  )
}
