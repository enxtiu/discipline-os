"""Version 1 API surface.

Stage 1 mounts ``meta``, ``auth``, ``devices``, ``tokens`` and ``settings``.
Later stages append their routers here — the prefix and the auth model stay the
same, so the client's generated types simply grow.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    auth,
    devices,
    foods,
    health,
    ingest,
    meta,
    nutrition,
    planner,
    planner_org,
    planner_time,
    recipes,
    settings,
    tokens,
    training,
)
from app.api.v1 import gamification as gamification_api
from app.api.v1 import notifications as notifications_api
from app.api.v1 import push as push_api

api_router = APIRouter()

api_router.include_router(meta.router)
api_router.include_router(auth.router)
api_router.include_router(devices.router)
api_router.include_router(tokens.router)
api_router.include_router(settings.router)

# --- stage 2: nutrition core ---------------------------------------------------
api_router.include_router(foods.router)
api_router.include_router(nutrition.router)
api_router.include_router(recipes.router)

# --- stage 3: AI logging --------------------------------------------------------
api_router.include_router(ai.router)

# --- stage 4: training ----------------------------------------------------------
api_router.include_router(training.router)

# --- stage 5: health ingest -------------------------------------------------------
api_router.include_router(health.router)
api_router.include_router(ingest.router)

# --- stage 6: planner -------------------------------------------------------------
api_router.include_router(planner.router)
api_router.include_router(planner_org.router)
api_router.include_router(planner_time.router)

# --- stage 7: notifications ------------------------------------------------------
api_router.include_router(push_api.router)
api_router.include_router(notifications_api.router)

# --- stage 8: gamification -------------------------------------------------------
api_router.include_router(gamification_api.router)

__all__ = ["api_router"]
