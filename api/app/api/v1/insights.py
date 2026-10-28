"""Insights (stage 10): the one calm observation «Сегодня» shows under its blocks —
read-only, device principal only. Every number and the choice itself come from the
server (``app.services.insights``); the client only renders the sentence."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import today_insight_out
from app.schemas.insights import TodayInsightOut
from app.services import insights
from app.services.gamification.context import context_for

router = APIRouter(prefix="/insights", tags=["insights"], dependencies=[Depends(require_device)])


@router.get(
    "/today",
    response_model=TodayInsightOut,
    summary="At most one observation for today: protein, fibre, water or steps above your 4-week average",
)
async def today_insight(principal: CurrentPrincipal, db: DbSession) -> TodayInsightOut:
    ctx = context_for(principal.settings_row, principal.user_id)
    return today_insight_out(await insights.today_insight(db, ctx))
