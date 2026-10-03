"""Recipes: CRUD with denormalised per-100 g and per-serving macros."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import recipe_out
from app.core.logging import get_logger
from app.schemas.common import DeletedOut
from app.schemas.food import RecipeIn, RecipeListOut, RecipeOut, RecipePatchIn
from app.services import recipes

log = get_logger(__name__)
router = APIRouter(prefix="/recipes", tags=["recipes"], dependencies=[Depends(require_device)])


@router.get("", response_model=RecipeListOut, summary="List my recipes (pinned first)")
async def index(principal: CurrentPrincipal, db: DbSession) -> RecipeListOut:
    rows = await recipes.list_recipes(db, principal.user_id)
    return RecipeListOut(items=[recipe_out(row) for row in rows])


@router.post("", response_model=RecipeOut, status_code=201, summary="Create a recipe")
async def create(payload: RecipeIn, principal: CurrentPrincipal, db: DbSession) -> RecipeOut:
    recipe = await recipes.create_recipe(db, user_id=principal.user_id, payload=payload)
    return recipe_out(recipe)


@router.get("/{recipe_id}", response_model=RecipeOut, summary="One recipe")
async def get(recipe_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> RecipeOut:
    return recipe_out(await recipes.get_recipe(db, principal.user_id, recipe_id))


@router.patch("/{recipe_id}", response_model=RecipeOut, summary="Edit a recipe (macros recomputed)")
async def patch(recipe_id: uuid.UUID, payload: RecipePatchIn, principal: CurrentPrincipal, db: DbSession) -> RecipeOut:
    return recipe_out(await recipes.update_recipe(db, user_id=principal.user_id, recipe_id=recipe_id, payload=payload))


@router.delete("/{recipe_id}", response_model=DeletedOut, summary="Delete a recipe")
async def delete(recipe_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> DeletedOut:
    await recipes.delete_recipe(db, user_id=principal.user_id, recipe_id=recipe_id)
    return DeletedOut(id=recipe_id)
