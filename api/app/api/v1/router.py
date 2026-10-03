"""Version 1 API surface.

Stage 1 mounts ``meta``, ``auth``, ``devices``, ``tokens`` and ``settings``.
Later stages append their routers here — the prefix and the auth model stay the
same, so the client's generated types simply grow.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import ai, auth, devices, foods, meta, nutrition, recipes, settings, tokens

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

__all__ = ["api_router"]
