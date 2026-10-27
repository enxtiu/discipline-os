"""Stats (stage 8): the series behind the charts — nutrition, activity, body, planner,
progress — for one calendar period in your timezone. Device principal only; every
number (targets, the zone, day boundaries, averages) comes from the server."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.gamification import Sync as GameSync
from app.api.v1.planner import sane_day
from app.api.v1.presenters import (
    activity_stats_out,
    body_stats_out,
    nutrition_stats_out,
    planner_stats_out,
    progress_stats_out,
)
from app.schemas.stats import (
    ActivityStatsOut,
    BodyStatsOut,
    NutritionStatsOut,
    PeriodLiteral,
    PlannerStatsOut,
    ProgressStatsOut,
)
from app.services import stats
from app.services.gamification import engine
from app.services.gamification.context import GameContext, context_for

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[Depends(require_device)])

# /stats/progress refreshes the open window first (today and yesterday, as
# /gamification/scores does), and the streak rows whose freeze dates the calendar shows
PROGRESS_SYNC = ("days", "streaks")

Period = Annotated[
    PeriodLiteral,
    Query(description="calendar period containing `date`: week (your week start), month, quarter or year"),
]
Anchor = Annotated[date | None, Query(alias="date", description="any day of the period; default: your today")]


def _span(principal: CurrentPrincipal, period: str, anchor: date | None) -> tuple[GameContext, stats.Span]:
    sane_day(anchor)
    ctx = context_for(principal.settings_row, principal.user_id)
    return ctx, stats.span_for(period, anchor or ctx.today, ctx.week_start, ctx.today)


@router.get("/nutrition", response_model=NutritionStatsOut, summary="Food and water per day, zone, averages")
async def nutrition(
    principal: CurrentPrincipal, db: DbSession, period: Period = "week", anchor: Anchor = None
) -> NutritionStatsOut:
    ctx, span = _span(principal, period, anchor)
    return nutrition_stats_out(await stats.nutrition(db, ctx, span))


@router.get("/activity", response_model=ActivityStatsOut, summary="Steps, workouts, burned kcal, volume, records")
async def activity(
    principal: CurrentPrincipal, db: DbSession, period: Period = "week", anchor: Anchor = None
) -> ActivityStatsOut:
    ctx, span = _span(principal, period, anchor)
    return activity_stats_out(await stats.activity(db, ctx, span))


@router.get("/body", response_model=BodyStatsOut, summary="Weight with a 7-day mean, 30-day change, sleep")
async def body(
    principal: CurrentPrincipal, db: DbSession, period: Period = "week", anchor: Anchor = None
) -> BodyStatsOut:
    ctx, span = _span(principal, period, anchor)
    return body_stats_out(await stats.body(db, ctx, span))


@router.get("/planner", response_model=PlannerStatsOut, summary="Tasks done/created, tracked time, areas, projects")
async def planner(
    principal: CurrentPrincipal, db: DbSession, period: Period = "week", anchor: Anchor = None
) -> PlannerStatsOut:
    ctx, span = _span(principal, period, anchor)
    return planner_stats_out(await stats.planner(db, ctx, span))


@router.get(
    "/progress",
    response_model=ProgressStatsOut,
    summary="Daily scores, XP per day, streak calendar",
    dependencies=GameSync,  # runs the engine with writes: the gamification GETs' per-user budget
)
async def progress(
    principal: CurrentPrincipal, db: DbSession, period: Period = "week", anchor: Anchor = None
) -> ProgressStatsOut:
    ctx, span = _span(principal, period, anchor)
    await engine.sync_user(db, principal.user_id, ctx.now, stages=PROGRESS_SYNC)
    return progress_stats_out(await stats.progress(db, ctx, span))
