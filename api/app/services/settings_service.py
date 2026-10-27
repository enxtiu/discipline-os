"""Settings: read/create, partial update, target recalculation.

Rules implemented here
----------------------
* Targets are **derived** by default (``targets_manual = False``). Any change to
  body params / goal recomputes them immediately and stores the result, so
  dashboards never have to compute on read.
* Editing a target by hand flips ``targets_manual`` to ``True`` — after that the
  auto-recalculator keeps its hands off until the user re-enables it.
* A manual ``target_kcal`` below the user's safe floor is rejected unless the
  caller explicitly confirms (``confirm_floor=True``), which also timestamps
  ``safe_floor_confirmed_at``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings as get_app_settings
from app.core.errors import Unprocessable
from app.core.logging import get_logger
from app.core.tz import today_in
from app.models import BodyMetric, User, UserSettings
from app.services.targets import (
    ABSOLUTE_FLOOR_KCAL,
    SAFE_FLOOR_FEMALE,
    SAFE_FLOOR_MALE,
    BodyParams,
    TargetResult,
    age_from_birth_date,
    compute_targets,
)

log = get_logger(__name__)


def _f(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)


async def get_or_create_settings(db: AsyncSession, user: User) -> UserSettings:
    # The request's principal (app/api/deps.py) has usually loaded this row already: a
    # SELECT would hand back that very object (the identity map keeps loaded state), so
    # take it without the round trip (stage 9 perf).
    for item in list(db.identity_map.values()):
        if isinstance(item, UserSettings) and inspect(item).dict.get("user_id") == user.id:
            return item
    row = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    if row is None:
        app_settings = get_app_settings()
        row = UserSettings(
            user_id=user.id,
            timezone=app_settings.default_timezone,
            language=app_settings.default_language,
            week_start=app_settings.default_week_start,
        )
        db.add(row)
        await db.flush()
        log.info("settings_created", user_id=str(user.id))
    return row


def default_floor_for(row: UserSettings) -> int:
    if row.safe_floor_kcal:
        return int(row.safe_floor_kcal)
    if row.sex is not None and getattr(row.sex, "value", row.sex) == "male":
        return SAFE_FLOOR_MALE
    if row.sex is not None and getattr(row.sex, "value", row.sex) == "female":
        return SAFE_FLOOR_FEMALE
    return SAFE_FLOOR_FEMALE


def params_from_settings(row: UserSettings, *, as_of: date) -> BodyParams:
    return BodyParams(
        sex=row.sex,
        age_years=age_from_birth_date(row.birth_date, as_of),
        height_cm=_f(row.height_cm),
        weight_kg=_f(row.weight_kg),
        body_fat_pct=_f(row.body_fat_pct),
        activity_level=row.activity_level,
        formula=row.bmr_formula,
        goal_mode=getattr(row.goal_mode, "value", row.goal_mode) or "maintain",
        goal_rate_pct=int(row.goal_rate_pct or 0),
        protein_g_per_kg=_f(row.goal_protein_g_per_kg) or 2.0,
        fat_min_g_per_kg=_f(row.goal_fat_min_g_per_kg) or 0.8,
        water_ml_per_kg=_f(row.goal_water_ml_per_kg) or 33.0,
        safe_floor_kcal=int(row.safe_floor_kcal) if row.safe_floor_kcal else None,
        target_steps=int(row.target_steps or 8000),
    )


def apply_targets(row: UserSettings, result: TargetResult) -> None:
    row.target_kcal = result.target_kcal
    row.target_protein_g = result.target_protein_g
    row.target_fat_g = result.target_fat_g
    row.target_carbs_g = result.target_carbs_g
    row.target_fiber_g = result.target_fiber_g
    row.target_water_ml = result.target_water_ml


def current_targets(row: UserSettings, *, as_of: date | None = None) -> TargetResult:
    """Return the stored targets, or compute them on the fly if never stored."""
    params = params_from_settings(row, as_of=as_of or date.today())
    if row.target_kcal:
        computed = compute_targets(params, as_of=as_of)
        return TargetResult(
            ok=True,
            formula_used=computed.formula_used,
            bmr_kcal=computed.bmr_kcal,
            tdee_kcal=computed.tdee_kcal,
            target_kcal=int(row.target_kcal),
            target_protein_g=int(row.target_protein_g or 0),
            target_fat_g=int(row.target_fat_g or 0),
            target_carbs_g=int(row.target_carbs_g or 0),
            target_fiber_g=int(row.target_fiber_g or 0),
            target_water_ml=int(row.target_water_ml or 0),
            target_steps=int(row.target_steps or 0),
            safe_floor_kcal=default_floor_for(row),
            deficit_pct=computed.deficit_pct,
            warnings=computed.warnings if not row.targets_manual else [],
        )
    return compute_targets(params, as_of=as_of)


async def recompute_targets(
    db: AsyncSession,
    row: UserSettings,
    *,
    force: bool = False,
    as_of: date | None = None,
) -> TargetResult:
    """Recompute derived targets. No-op when the user pinned targets manually."""
    params = params_from_settings(row, as_of=as_of or date.today())
    result = compute_targets(params, as_of=as_of)
    if result.ok and (force or not row.targets_manual):
        apply_targets(row, result)
    return result


async def sync_profile_weight(db: AsyncSession, row: UserSettings, weight_kg: float | None) -> None:
    """Keep ``settings.weight_kg`` aligned with the newest body metric."""
    if weight_kg is None:
        latest = (
            await db.execute(
                select(BodyMetric.weight_kg)
                .where(BodyMetric.user_id == row.user_id, BodyMetric.weight_kg.is_not(None))
                .order_by(BodyMetric.measured_on.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        weight_kg = _f(latest)
    if weight_kg is not None:
        row.weight_kg = weight_kg
        await recompute_targets(db, row)


def validate_manual_targets(payload: dict[str, Any], row: UserSettings, *, confirm_floor: bool = False) -> list[str]:
    """Guard rails for hand-edited targets. Returns warning codes."""
    warnings: list[str] = []
    floor = default_floor_for(row)
    kcal = payload.get("target_kcal")

    if kcal is not None:
        target_kcal = int(kcal)
        if target_kcal < ABSOLUTE_FLOOR_KCAL:
            raise Unprocessable(
                f"A calorie target below {ABSOLUTE_FLOOR_KCAL} kcal is not allowed.",
                code="target_below_absolute_floor",
                details={"absolute_floor_kcal": ABSOLUTE_FLOOR_KCAL},
            )
        if target_kcal < floor and not confirm_floor:
            raise Unprocessable(
                "This target is below the recommended safe floor. Confirm explicitly to keep it.",
                code="target_below_safe_floor",
                details={"safe_floor_kcal": floor, "requested_kcal": target_kcal},
            )
        if target_kcal < floor <= ABSOLUTE_FLOOR_KCAL:  # pragma: no cover - unreachable guard
            raise Unprocessable("Target is below the absolute floor", code="target_below_absolute_floor")
        if target_kcal < floor:
            warnings.append("below_safe_floor_confirmed")

        # cross-check macros: protein + fat must not blow the budget
        protein = payload.get("target_protein_g")
        fat = payload.get("target_fat_g")
        if protein is not None and fat is not None:
            min_kcal = int(protein) * 4 + int(fat) * 9
            if min_kcal > target_kcal:
                raise Unprocessable(
                    "Protein + fat already exceed the calorie target.",
                    code="macros_exceed_kcal",
                    details={"protein_fat_kcal": min_kcal, "target_kcal": target_kcal},
                )
    return warnings


async def targets_as_dict(db: AsyncSession, row: UserSettings) -> dict[str, Any]:
    today = today_in(row.timezone)
    result = current_targets(row, as_of=today)
    return {
        "bmr_kcal": result.bmr_kcal,
        "tdee_kcal": result.tdee_kcal,
        "target_kcal": result.target_kcal,
        "target_protein_g": result.target_protein_g,
        "target_fat_g": result.target_fat_g,
        "target_carbs_g": result.target_carbs_g,
        "target_fiber_g": result.target_fiber_g,
        "target_water_ml": result.target_water_ml,
        "target_steps": result.target_steps,
        "safe_floor_kcal": default_floor_for(row),
        "deficit_pct": result.deficit_pct,
        "formula_used": result.formula_used,
        "warnings": result.warnings,
        "macros_kcal": result.macros_kcal,
        "is_manual": bool(row.targets_manual),
    }
