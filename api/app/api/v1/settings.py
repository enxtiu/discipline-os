"""Settings: profile, body parameters, goal, targets, onboarding."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, require_scope
from app.core.logging import get_logger
from app.core.tz import today_in
from app.models import BodyMetric
from app.schemas.settings import (
    BodyParamsOut,
    OnboardingIn,
    SettingsEnvelopeOut,
    SettingsUpdateIn,
    TargetPreviewIn,
    TargetsIn,
    TargetsOut,
)
from app.services.appearance import apply_appearance, check_appearance_base
from app.services.bootstrap import DEFAULT_NOTIFICATION_PREFS
from app.services.entitlements import FEATURE_APPEARANCE_SYNC, require_feature
from app.services.settings_service import (
    current_targets,
    default_floor_for,
    get_or_create_settings,
    params_from_settings,
    recompute_targets,
    targets_as_dict,
    validate_manual_targets,
)
from app.services.targets import BodyParams, age_from_birth_date, compute_targets

log = get_logger(__name__)
# Settings are owner-level: a narrowly scoped token (e.g. "ingest:health") must
# not read or change them. Devices hold the "*" scope, so they always pass.
router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_scope("read:all"))])


def _targets_out(result, *, is_manual: bool = False) -> TargetsOut:
    return TargetsOut(
        bmr_kcal=result.bmr_kcal,
        tdee_kcal=result.tdee_kcal,
        target_kcal=result.target_kcal,
        target_protein_g=result.target_protein_g,
        target_fat_g=result.target_fat_g,
        target_carbs_g=result.target_carbs_g,
        target_fiber_g=result.target_fiber_g,
        target_water_ml=result.target_water_ml,
        target_steps=result.target_steps,
        safe_floor_kcal=result.safe_floor_kcal,
        deficit_pct=result.deficit_pct,
        formula_used=result.formula_used,
        warnings=result.warnings,
        macros_kcal=result.macros_kcal,
        is_manual=is_manual,
    )


@router.get("", response_model=SettingsEnvelopeOut, summary="Current settings + computed targets")
async def read_settings(principal: CurrentPrincipal, db: DbSession) -> SettingsEnvelopeOut:
    row = await get_or_create_settings(db, principal.user)
    targets = await targets_as_dict(db, row)
    return SettingsEnvelopeOut(settings=_settings_dict(row), targets=TargetsOut(**targets))


@router.patch("", response_model=SettingsEnvelopeOut, summary="Partial update (only sent fields change)")
async def update_settings(payload: SettingsUpdateIn, principal: CurrentPrincipal, db: DbSession) -> SettingsEnvelopeOut:
    row = await get_or_create_settings(db, principal.user)
    data = payload.model_dump(exclude_unset=True)

    # stage 10: the account's appearance. Checked before anything is written (a conflict writes
    # nothing, not even the other fields of the request); the row is locked so two devices saving
    # at once are compared one after the other.
    sets_appearance = "appearance" in payload.model_fields_set
    data.pop("appearance", None)
    data.pop("base", None)
    if sets_appearance:
        require_feature(principal.user, FEATURE_APPEARANCE_SYNC)
    if sets_appearance or payload.base is not None:
        await _lock_settings(db, row)
        check_appearance_base(row, payload.base, payload.appearance, sets=sets_appearance)

    floor_confirm = bool(data.pop("confirm_floor", False))
    requested_floor = data.get("safe_floor_kcal")
    current_floor = row.safe_floor_kcal or default_floor_for(row)
    if requested_floor is not None and requested_floor < current_floor and not floor_confirm:
        from app.core.errors import Unprocessable

        raise Unprocessable(
            "Lowering the safe floor requires explicit confirmation.",
            code="target_below_safe_floor",
            details={"current_floor_kcal": current_floor, "requested_kcal": requested_floor},
        )
    if "safe_floor_kcal" in data:
        from app.core.tz import utcnow

        row.safe_floor_confirmed_at = utcnow() if floor_confirm else row.safe_floor_confirmed_at

    # Weight edits also start/refresh the weight history for today.
    new_weight = data.pop("weight_kg", None)
    if new_weight is not None:
        await _upsert_today_weight(db, principal.user.id, float(new_weight), row.timezone)

    body_keys = {
        "birth_date",
        "sex",
        "height_cm",
        "body_fat_pct",
        "activity_level",
        "bmr_formula",
        "goal_mode",
        "goal_rate_pct",
        "goal_weight_kg",
        "goal_protein_g_per_kg",
        "goal_fat_min_g_per_kg",
        "goal_water_ml_per_kg",
        "target_steps",
    }
    targets_manual_flag = data.pop("targets_manual", None)
    if targets_manual_flag is not None:
        row.targets_manual = bool(targets_manual_flag)

    touched_calc_inputs = new_weight is not None
    for key, value in data.items():
        # empty string -> NULL for nullable fields
        if value == "" and key in {"body_fat_pct", "goal_weight_kg", "birth_date", "sex"}:
            value = None
        setattr(row, key, value)
        if key in body_keys:
            touched_calc_inputs = True

    if sets_appearance:
        apply_appearance(row, payload.appearance)

    if touched_calc_inputs:
        await recompute_targets(db, row)
    await db.flush()
    targets = await targets_as_dict(db, row)
    log.info(
        "settings_updated",
        user_id=str(principal.user_id),
        fields=sorted([*data.keys(), *(["appearance"] if sets_appearance else [])]),
    )
    return SettingsEnvelopeOut(settings=_settings_dict(row), targets=TargetsOut(**targets))


@router.put("/targets", response_model=TargetsOut, summary="Set targets by hand (disables auto)")
async def set_targets(payload: TargetsIn, principal: CurrentPrincipal, db: DbSession) -> TargetsOut:
    row = await get_or_create_settings(db, principal.user)
    data = payload.model_dump(exclude_unset=True)
    confirm = bool(data.pop("confirm_floor", False))
    warnings = validate_manual_targets(data, row, confirm_floor=confirm)
    for key, value in data.items():
        setattr(row, key, value)
    row.targets_manual = True
    if confirm:
        from app.core.tz import utcnow

        row.safe_floor_confirmed_at = utcnow()
    await db.flush()
    base = current_targets(row)
    result = replace(base, warnings=sorted(set(base.warnings) | set(warnings)))
    log.info("targets_set_manual", user_id=str(principal.user_id), fields=sorted(data.keys()))
    return _targets_out(result, is_manual=True)


@router.post("/targets/recompute", response_model=TargetsOut, summary="Re-enable auto targets and recompute")
async def recompute(principal: CurrentPrincipal, db: DbSession) -> TargetsOut:
    row = await get_or_create_settings(db, principal.user)
    row.targets_manual = False
    result = await recompute_targets(db, row, force=True)
    await db.flush()
    return _targets_out(result, is_manual=False)


@router.post("/targets/preview", response_model=TargetsOut, summary="Stateless target preview (onboarding)")
async def preview_targets(payload: TargetPreviewIn, principal: CurrentPrincipal, db: DbSession) -> TargetsOut:
    row = await get_or_create_settings(db, principal.user)
    today = today_in(row.timezone)
    age = payload.age_years or age_from_birth_date(payload.birth_date, today)
    params = BodyParams(
        sex=payload.sex,  # type: ignore[arg-type]
        age_years=age,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        body_fat_pct=payload.body_fat_pct,
        activity_level=payload.activity_level,  # type: ignore[arg-type]
        formula=payload.formula,  # type: ignore[arg-type]
        goal_mode=payload.goal_mode,
        goal_rate_pct=payload.goal_rate_pct,
        protein_g_per_kg=payload.protein_g_per_kg,
        fat_min_g_per_kg=payload.fat_min_g_per_kg,
        water_ml_per_kg=payload.water_ml_per_kg,
        safe_floor_kcal=payload.safe_floor_kcal,
        target_steps=payload.target_steps,
    )
    result = compute_targets(params, as_of=today)
    return _targets_out(result, is_manual=False)


@router.get("/body-params", response_model=BodyParamsOut, summary="Body parameters as the app sees them")
async def body_params(principal: CurrentPrincipal, db: DbSession) -> BodyParamsOut:
    row = await get_or_create_settings(db, principal.user)
    today = today_in(row.timezone)
    params = params_from_settings(row, as_of=today)
    lean = None
    bmi = None
    if params.weight_kg and params.body_fat_pct:
        lean = round(params.weight_kg * (1 - params.body_fat_pct / 100), 1)
    if params.weight_kg and params.height_cm:
        bmi = round(params.weight_kg / ((params.height_cm / 100) ** 2), 1)
    return BodyParamsOut(
        sex=getattr(params.sex, "value", params.sex),
        birth_date=row.birth_date,
        age_years=params.age_years,
        height_cm=params.height_cm,
        weight_kg=params.weight_kg,
        body_fat_pct=params.body_fat_pct,
        activity_level=getattr(params.activity_level, "value", params.activity_level),
        formula=getattr(params.formula, "value", params.formula),
        lean_body_mass_kg=lean,
        bmi=bmi,
    )


@router.post("/onboarding", response_model=SettingsEnvelopeOut, summary="Apply the first-run wizard")
async def onboarding(payload: OnboardingIn, principal: CurrentPrincipal, db: DbSession) -> SettingsEnvelopeOut:
    row = await get_or_create_settings(db, principal.user)
    row.language = payload.language  # type: ignore[assignment]
    if payload.timezone:
        row.timezone = payload.timezone
    row.sex = payload.sex  # type: ignore[assignment]
    row.birth_date = payload.birth_date
    row.height_cm = payload.height_cm
    row.body_fat_pct = payload.body_fat_pct
    row.activity_level = payload.activity_level  # type: ignore[assignment]
    row.goal_mode = payload.goal_mode  # type: ignore[assignment]
    row.goal_rate_pct = payload.goal_rate_pct
    row.goal_weight_kg = payload.goal_weight_kg
    row.target_steps = payload.target_steps
    row.quiet_hours_start = payload.quiet_hours_start
    row.quiet_hours_end = payload.quiet_hours_end
    if payload.notifications:
        # keep what is already stored (stage 7: reminder times), the wizard only sends on/off
        merged = {**DEFAULT_NOTIFICATION_PREFS, **(row.notification_prefs or {})}
        merged.update(payload.notifications)
        row.notification_prefs = merged
    if payload.weight_kg is not None:
        await _upsert_today_weight(db, principal.user.id, float(payload.weight_kg), row.timezone)
    row.onboarding_completed = True
    await recompute_targets(db, row, force=not row.targets_manual)
    await db.flush()
    log.info("onboarding_completed", user_id=str(principal.user_id))
    targets = await targets_as_dict(db, row)
    return SettingsEnvelopeOut(settings=_settings_dict(row), targets=TargetsOut(**targets))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _upsert_today_weight(db, user_id, weight_kg: float, timezone: str) -> None:
    """One weigh-in per local day: update today's row instead of duplicating."""
    today = today_in(timezone)
    existing = (
        await db.execute(select(BodyMetric).where(BodyMetric.user_id == user_id, BodyMetric.measured_on == today))
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            BodyMetric(
                user_id=user_id,
                measured_on=today,
                weight_kg=weight_kg,
                source="settings",
            )
        )
    else:
        existing.weight_kg = weight_kg
    # keep the profile cache in sync
    from app.models import UserSettings

    row = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    if row is not None:
        row.weight_kg = weight_kg


async def _lock_settings(db, row) -> None:
    """Lock this user's settings row until the transaction ends and re-read it (compare-and-set)."""
    from app.models import UserSettings

    await db.execute(
        select(UserSettings)
        .where(UserSettings.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _settings_dict(row) -> dict:
    from app.schemas.auth import UserSettingsOut

    return UserSettingsOut.model_validate(row).model_dump()
