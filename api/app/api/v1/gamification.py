"""Gamification (stage 8): the daily score and rings, XP and levels, streaks, quests,
achievements. Device principal only; every number is computed by the server in the
user's timezone (today and yesterday are recomputed on read)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import (
    achievement_out,
    day_score_out,
    level_out,
    level_progress_out,
    rings_out,
    score_component_out,
    streak_out,
    user_quest_out,
)
from app.core.errors import RateLimited
from app.core.rate_limit import limiter
from app.core.tz import iter_days
from app.schemas.common import OkOut
from app.schemas.gamification import (
    AchievementsSeenIn,
    GamificationProfileOut,
    GamificationTodayOut,
    ProfileStatsOut,
    ProfileXpOut,
    QuestClaimOut,
    ScoreHistoryDayOut,
    ScoreHistoryOut,
    XpDayOut,
    XpEventOut,
    XpHistoryDayOut,
    XpHistoryOut,
    XpTodayOut,
)
from app.services.gamification import achievements, engine, quests, score, xp
from app.services.gamification.context import context_for
from app.services.gamification.levels import LEVELS
from app.services.planner.common import check_range
from app.services.planner.common import validation_error as _validation_error

router = APIRouter(prefix="/gamification", tags=["gamification"], dependencies=[Depends(require_device)])

RANGE_DAYS = 366
WRITES_PER_MINUTE = 120
EARLIEST = date(2000, 1, 1)
LATEST = date(2100, 12, 31)
UserQuestId = Annotated[uuid.UUID, Path(alias="user_quest_id", description="id of the user quest (UserQuestOut.id)")]


async def _write_limit(principal: CurrentPrincipal) -> None:
    allowed, retry_after = await limiter.hit(f"gamification:{principal.user_id}", WRITES_PER_MINUTE)
    if not allowed:
        raise RateLimited(retry_after, "Too many requests, please slow down")


Write = [Depends(_write_limit)]


def _range(first: date, last: date) -> None:
    for name, value in (("from", first), ("to", last)):
        if not EARLIEST <= value <= LATEST:
            raise _validation_error(f"{name} must be between {EARLIEST} and {LATEST}")
    check_range(first, last, RANGE_DAYS)


async def _snapshot(principal: CurrentPrincipal, db, *, stages=engine.STAGES) -> engine.GameSnapshot:
    snapshot = await engine.sync_user(db, principal.user_id, stages=stages)
    assert snapshot is not None  # an authenticated device belongs to an active account
    return snapshot


@router.get("/today", response_model=GamificationTodayOut, summary="Today: score, rings, XP, streaks, quests")
async def today(principal: CurrentPrincipal, db: DbSession) -> GamificationTodayOut:
    snapshot = await _snapshot(principal, db)
    ctx, today_score = snapshot.ctx, snapshot.today_score
    unseen = await achievements.unseen(db, principal.user_id)
    return GamificationTodayOut(
        date=ctx.today,
        score=day_score_out(today_score),
        rings=rings_out(score.rings(today_score, ctx.score_weights)),
        xp=XpTodayOut(today=snapshot.xp_today, total=snapshot.xp_total, level=level_progress_out(snapshot.level)),
        streaks=[streak_out(state) for state in snapshot.streaks],
        quests=[user_quest_out(view) for view in snapshot.quests],
        celebrate=[achievement_out(view) for view in unseen],
    )


@router.get("/profile", response_model=GamificationProfileOut, summary="XP, levels, streaks, achievements, totals")
async def profile(principal: CurrentPrincipal, db: DbSession) -> GamificationProfileOut:
    snapshot = await _snapshot(principal, db)
    ctx, metrics = snapshot.ctx, snapshot.metrics or {}
    first = ctx.today - timedelta(days=29)
    per_day = await xp.per_day(db, principal.user_id, first, ctx.today)
    views = await achievements.listing(db, principal.user_id, metrics)
    return GamificationProfileOut(
        xp=ProfileXpOut(
            total=snapshot.xp_total,
            today=snapshot.xp_today,
            level=level_progress_out(snapshot.level),
            last_30_days=[XpDayOut(date=day, xp=per_day.get(day, 0)) for day in iter_days(first, ctx.today)],
        ),
        levels=[level_out(row) for row in LEVELS],
        streaks=[streak_out(state) for state in snapshot.streaks],
        achievements=[achievement_out(view) for view in views],
        stats=ProfileStatsOut(
            meals_logged_total=int(metrics.get("meals_logged_total", 0)),
            workouts_total=int(metrics.get("workouts_total", 0)),
            tasks_completed_total=int(metrics.get("tasks_completed_total", 0)),
            tracked_minutes_total=int(metrics.get("tracked_minutes_total", 0)),
            journal_entries_total=int(metrics.get("journal_entries_total", 0)),
        ),
    )


@router.get("/xp", response_model=XpHistoryOut, summary="XP per day with its events (≤ 366 days)")
async def xp_history(
    principal: CurrentPrincipal,
    db: DbSession,
    first: date = Query(alias="from"),
    last: date = Query(alias="to"),
) -> XpHistoryOut:
    _range(first, last)
    await _snapshot(principal, db, stages=("days",))
    events = await xp.events_by_day(db, principal.user_id, first, last)
    items = []
    for day in iter_days(first, last):
        rows = events.get(day, [])
        items.append(
            XpHistoryDayOut(
                date=day,
                xp=sum(row.points for row in rows),
                events=[XpEventOut(kind=row.kind.value, points=row.points, reason=row.reason) for row in rows],
            )
        )
    return XpHistoryOut(items=items)


@router.get("/scores", response_model=ScoreHistoryOut, summary="Daily scores with their breakdown (≤ 366 days)")
async def score_history(
    principal: CurrentPrincipal,
    db: DbSession,
    first: date = Query(alias="from"),
    last: date = Query(alias="to"),
) -> ScoreHistoryOut:
    _range(first, last)
    snapshot = await _snapshot(principal, db, stages=("days",))
    rows = await score.load_rows(db, principal.user_id, first, last)
    items = []
    for day in iter_days(first, last):
        item = snapshot.computed.get(day) or (score.from_row(rows[day]) if day in rows else None)
        if item is None:
            items.append(ScoreHistoryDayOut(date=day, score=None, provisional=False, components=[]))
        else:
            items.append(
                ScoreHistoryDayOut(
                    date=day,
                    score=item.value,
                    provisional=item.provisional,
                    components=[score_component_out(component) for component in item.components],
                )
            )
    return ScoreHistoryOut(items=items)


@router.post(
    "/achievements/seen", response_model=OkOut, summary="Mark celebrated achievements as seen", dependencies=Write
)
async def achievements_seen(payload: AchievementsSeenIn, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await achievements.mark_seen(db, principal.user_id, payload.ids)
    return OkOut()


@router.post(
    "/quests/{user_quest_id}/claim",
    response_model=QuestClaimOut,
    summary="Claim a completed quest (idempotent; 409 quest_not_completed)",
    dependencies=Write,
)
async def claim_quest(user_quest_id: UserQuestId, principal: CurrentPrincipal, db: DbSession) -> QuestClaimOut:
    ctx = context_for(principal.settings_row, principal.user_id)
    view, awarded = await quests.claim(db, ctx, user_quest_id)
    return QuestClaimOut(quest=user_quest_out(view), xp_awarded=awarded)
