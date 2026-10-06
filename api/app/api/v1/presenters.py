"""Turn ORM rows into API schemas (single place, so shapes stay consistent)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from app.api.deps import Principal
from app.core.tz import local_day, utcnow
from app.models import (
    ApiToken,
    Device,
    Food,
    FoodFavorite,
    FoodRecent,
    MealLog,
    MealType,
    Recipe,
    User,
    UserSettings,
    WaterLog,
)
from app.schemas.ai import AiAnalysisOut, AiItemOut, AiMatchOut, AiPer100Out, AiUsageOut
from app.schemas.auth import ApiTokenOut, DeviceOut, SessionOut, UserSummaryOut
from app.schemas.food import (
    DaySummaryOut,
    FavoriteOut,
    FoodMatchOut,
    FoodOut,
    MacrosOut,
    MealLogOut,
    MealTypeTotalOut,
    RecentOut,
    RecipeBriefOut,
    RecipeOut,
    WaterLogOut,
)
from app.schemas.training import (
    BestsOut,
    BurnedDayOut,
    BurnedOut,
    ExerciseOut,
    ExerciseSessionOut,
    PlanDayOut,
    PreviousSetOut,
    RecordOut,
    RecurringDayOut,
    TemplateBriefOut,
    TemplateItemOut,
    TemplateOut,
    WorkoutBriefOut,
    WorkoutExerciseOut,
    WorkoutOut,
    WorkoutSetOut,
)
from app.services import training as training_service
from app.services.ai_meal import AiBudget, MealAnalysis, ResolvedItem, confidence_level
from app.services.food_search import FoodHit


def device_out(device: Device, *, current_id: uuid.UUID | None = None) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    out.is_current = bool(current_id and device.id == current_id)
    return out


def api_token_out(token: ApiToken) -> ApiTokenOut:
    return ApiTokenOut.model_validate(token)


def user_summary(user: User) -> UserSummaryOut:
    return UserSummaryOut.model_validate(user)


def build_session(principal: Principal, row: UserSettings, targets: dict[str, Any]) -> SessionOut:
    return SessionOut(
        user=user_summary(principal.user),
        settings=row,
        device=device_out(principal.device, current_id=principal.device.id) if principal.device else None,
        api_token=api_token_out(principal.api_token) if principal.api_token else None,
        auth_kind=principal.kind,
        scopes=sorted(principal.scopes),
        server_time=utcnow(),
        targets=targets,
    )


# --------------------------------------------------------------------------- #
# Nutrition (stage 2)
# --------------------------------------------------------------------------- #
def food_out(food: Food, *, favorites: dict[uuid.UUID, float | None] | None = None) -> FoodOut:
    out = FoodOut.model_validate(food)
    if favorites is not None:
        out.is_favorite = food.id in favorites
        out.favorite_grams = favorites.get(food.id)
    return out


def food_match_out(hit: FoodHit, *, favorites: dict[uuid.UUID, float | None]) -> FoodMatchOut:
    payload = food_out(hit.food, favorites=favorites).model_dump()
    payload["score"] = round(hit.score, 4)
    payload["match_method"] = hit.match_method
    return FoodMatchOut.model_validate(payload)


def favorite_out(fav: FoodFavorite, food: Food, *, favorites: dict[uuid.UUID, float | None]) -> FavoriteOut:
    return FavoriteOut(
        id=fav.id,
        food_id=fav.food_id,
        default_grams=fav.default_grams,
        sort_order=fav.sort_order,
        food=food_out(food, favorites=favorites),
    )


def recent_out(
    rec: FoodRecent,
    *,
    foods: dict[uuid.UUID, Food],
    recipes: dict[uuid.UUID, Recipe],
    favorites: dict[uuid.UUID, float | None],
) -> RecentOut:
    return RecentOut(
        id=rec.id,
        food=food_out(foods[rec.food_id], favorites=favorites) if rec.food_id and rec.food_id in foods else None,
        recipe=recipe_brief(recipes[rec.recipe_id]) if rec.recipe_id and rec.recipe_id in recipes else None,
        last_grams=rec.last_grams,
        use_count=rec.use_count,
        last_used_at=rec.last_used_at,
    )


def recipe_brief(recipe: Recipe) -> RecipeBriefOut:
    return RecipeBriefOut.model_validate(recipe)


def recipe_out(recipe: Recipe) -> RecipeOut:
    payload = {name: getattr(recipe, name) for name in RecipeOut.model_fields if name != "per_serving"}
    # one portion = portion_size_g of the cooked dish (the same grams the log
    # sheet writes for "1 portion"); computed from the unrounded totals
    base = float(recipe.cooked_weight_g or 0) or float(recipe.total_raw_weight_g or 0) or 100.0
    factor = float(recipe.portion_size_g or 0) / base
    payload["per_serving"] = MacrosOut(
        kcal=round(float(recipe.total_kcal) * factor, 2),
        protein_g=round(float(recipe.total_protein_g) * factor, 2),
        fat_g=round(float(recipe.total_fat_g) * factor, 2),
        carbs_g=round(float(recipe.total_carbs_g) * factor, 2),
        fiber_g=round(float(recipe.total_fiber_g) * factor, 2),
    )
    return RecipeOut.model_validate(payload)


def meal_out(meal: MealLog) -> MealLogOut:
    out = MealLogOut.model_validate(meal)
    out.has_photo = bool(meal.photo_thumb_path)
    return out


def water_out(row: WaterLog) -> WaterLogOut:
    return WaterLogOut.model_validate(row)


def day_summary_out(day: date, meals: list[MealLog], water_logs: list[WaterLog]) -> DaySummaryOut:
    totals = MacrosOut()
    by_type: dict[str, MealTypeTotalOut] = {kind.value: MealTypeTotalOut(meal_type=kind.value) for kind in MealType}
    for meal in meals:
        totals.kcal += float(meal.kcal)
        totals.protein_g += float(meal.protein_g)
        totals.fat_g += float(meal.fat_g)
        totals.carbs_g += float(meal.carbs_g)
        totals.fiber_g += float(meal.fiber_g)
        bucket = by_type[meal.meal_type.value]
        bucket.meals += 1
        bucket.kcal += float(meal.kcal)
        bucket.protein_g += float(meal.protein_g)
        bucket.fat_g += float(meal.fat_g)
        bucket.carbs_g += float(meal.carbs_g)
        bucket.fiber_g += float(meal.fiber_g)
    return DaySummaryOut(
        date=day,
        totals=MacrosOut(
            kcal=round(totals.kcal, 2),
            protein_g=round(totals.protein_g, 2),
            fat_g=round(totals.fat_g, 2),
            carbs_g=round(totals.carbs_g, 2),
            fiber_g=round(totals.fiber_g, 2),
        ),
        by_meal_type=[by_type[kind.value] for kind in MealType if by_type[kind.value].meals],
        meals=[meal_out(meal) for meal in meals],
        water_ml=sum(row.amount_ml for row in water_logs),
        water_logs=[water_out(row) for row in water_logs],
        logged_meals=len(meals),
    )


# --------------------------------------------------------------------------- #
# AI meal logging (stage 3)
# --------------------------------------------------------------------------- #
def ai_usage_out(budget: AiBudget) -> AiUsageOut:
    return AiUsageOut(
        enabled=budget.enabled,
        provider=budget.provider,
        calls_today=budget.my_calls_today,
        daily_limit=budget.daily_limit,
        remaining=budget.remaining,
    )


def ai_item_out(index: int, resolved: ResolvedItem, *, favorites: dict[uuid.UUID, float | None]) -> AiItemOut:
    item = resolved.item
    factor = item.estimated_grams / 100
    chosen = resolved.match
    return AiItemOut(
        key=f"i{index}",
        name_ru=item.name_ru,
        name_en=item.name_en,
        estimated_grams=round(item.estimated_grams, 1),
        confidence=round(item.confidence, 2),
        confidence_level=confidence_level(item.confidence),
        kcal=round(item.kcal_per_100g * factor, 1),
        protein=round(item.protein_per_100g * factor, 1),
        fat=round(item.fat_per_100g * factor, 1),
        carbs=round(item.carbs_per_100g * factor, 1),
        fiber=round(item.fiber_per_100g * factor, 1),
        ai_per_100g=AiPer100Out(
            kcal_per_100g=item.kcal_per_100g,
            protein_per_100g=item.protein_per_100g,
            fat_per_100g=item.fat_per_100g,
            carbs_per_100g=item.carbs_per_100g,
            fiber_per_100g=item.fiber_per_100g,
        ),
        kcal_adjusted=item.kcal_adjusted,
        match=(
            AiMatchOut(
                food=food_out(chosen.food, favorites=favorites),
                match_method=chosen.match_method,
                score=round(chosen.score, 3),
            )
            if chosen
            else None
        ),
        alternatives=[food_out(hit.food, favorites=favorites) for hit in resolved.alternatives],
    )


def ai_analysis_out(analysis: MealAnalysis) -> AiAnalysisOut:
    job = analysis.job
    return AiAnalysisOut(
        job_id=job.id,
        kind="photo" if analysis.kind == "photo" else "text",
        provider=job.provider or "",
        model=job.model or "",
        prompt_version=analysis.prompt_label,
        took_ms=analysis.took_ms,
        items=[
            ai_item_out(index, resolved, favorites=analysis.favorites) for index, resolved in enumerate(analysis.items)
        ],
        notes=analysis.notes,
        thumbnail_kept=analysis.thumbnail_kept,
        usage=ai_usage_out(analysis.budget),
    )


# --------------------------------------------------------------------------- #
# Training (stage 4)
# --------------------------------------------------------------------------- #
def _num(value) -> float | None:
    return float(value) if value is not None else None


def exercise_out(exercise) -> ExerciseOut:
    out = ExerciseOut.model_validate(exercise)
    out.is_custom = exercise.user_id is not None
    out.log_mode = training_service.log_mode(exercise)  # type: ignore[assignment]
    return out


def bests_out(bests: training_service.Bests | None) -> BestsOut:
    if bests is None:
        return BestsOut()
    return BestsOut(
        weight_kg=bests.weight_kg,
        est_1rm_kg=bests.est_1rm_kg,
        reps=bests.reps,
        volume_kg=bests.volume_kg,
        duration_s=bests.duration_s,
        distance_m=bests.distance_m,
    )


def record_out(record, exercises: dict, *, day: date | None = None, tz: str | None = None) -> RecordOut:
    exercise = exercises.get(record.exercise_id)
    return RecordOut(
        id=record.id,
        exercise_id=record.exercise_id,
        exercise_name_ru=exercise.name_ru if exercise else None,
        exercise_name_en=exercise.name_en if exercise else None,
        kind=record.kind,
        value=float(record.value),
        previous_value=_num(record.previous_value),
        unit=record.unit,
        reps=record.reps,
        achieved_at=record.achieved_at,
        local_day=day or local_day(record.achieved_at, tz),
        workout_id=record.workout_log_id,
        set_id=record.workout_set_id,
    )


def template_item_out(item, exercises: dict) -> TemplateItemOut | None:
    exercise = exercises.get(item.exercise_id)
    if exercise is None:
        return None
    return TemplateItemOut(
        id=item.id,
        exercise=exercise_out(exercise),
        sort_order=item.sort_order,
        target_sets=item.target_sets,
        target_reps=item.target_reps,
        target_reps_min=item.target_reps_min,
        target_reps_max=item.target_reps_max,
        target_weight_kg=_num(item.target_weight_kg),
        target_duration_s=item.target_duration_s,
        target_distance_m=item.target_distance_m,
        rest_seconds=item.rest_seconds,
        superset_group=item.superset_group,
        notes=item.notes,
    )


def template_out(template, exercises: dict) -> TemplateOut:
    items = [template_item_out(item, exercises) for item in template.items]
    return TemplateOut(
        id=template.id,
        name=template.name,
        description=template.description,
        kind=template.kind.value,
        estimated_minutes=template.estimated_minutes,
        color=template.color,
        use_count=template.use_count,
        items=[item for item in items if item is not None],
        updated_at=template.updated_at,
    )


def template_brief(template) -> TemplateBriefOut:
    return TemplateBriefOut(
        id=template.id,
        name=template.name,
        kind=template.kind.value,
        exercise_count=len(template.items),
        estimated_minutes=template.estimated_minutes,
        color=template.color,
    )


def workout_brief_out(brief: training_service.WorkoutBrief) -> WorkoutBriefOut:
    workout = brief.workout
    return WorkoutBriefOut(
        id=workout.id,
        title=workout.title,
        kind=workout.kind.value,
        status=workout.status.value,
        template_id=workout.template_id,
        started_at=workout.started_at,
        ended_at=workout.ended_at,
        local_day=workout.local_day,
        duration_s=workout.duration_s,
        total_sets=workout.total_sets,
        total_reps=workout.total_reps,
        total_volume_kg=float(workout.total_volume_kg or 0),
        kcal_burned=_num(workout.kcal_burned),
        kcal_method=workout.kcal_method.value,
        pr_count=workout.pr_count,
        exercise_names=brief.exercise_names,
    )


def workout_out(view: training_service.WorkoutView) -> WorkoutOut:
    workout = view.workout
    names = [
        view.exercises[exercise_id].name_ru or view.exercises[exercise_id].name_en or "" for exercise_id in view.order
    ]
    brief = workout_brief_out(training_service.WorkoutBrief(workout=workout, exercise_names=names[:4]))
    record_sets = view.record_set_ids
    targets = {}
    if view.template is not None:
        template_exercises = {exercise_id: view.exercises[exercise_id] for exercise_id in view.exercises}
        for item in view.template.items:
            if item.exercise_id not in targets:
                target = template_item_out(item, template_exercises)
                if target is not None:
                    targets[item.exercise_id] = target
    grouped: dict = {exercise_id: [] for exercise_id in view.order}
    for row in sorted(workout.sets, key=lambda r: (r.sort_order, r.set_index, r.created_at)):
        if row.exercise_id in grouped:
            grouped[row.exercise_id].append(row)
    exercises = []
    for exercise_id in view.order:
        previous_day, previous = view.previous.get(exercise_id, (None, []))
        sets = []
        for row in grouped[exercise_id]:
            out = WorkoutSetOut.model_validate(row)
            out.is_pr = row.id in record_sets
            sets.append(out)
        exercises.append(
            WorkoutExerciseOut(
                exercise=exercise_out(view.exercises[exercise_id]),
                target=targets.get(exercise_id),
                sets=sets,
                previous=[
                    PreviousSetOut(
                        reps=row.reps,
                        weight_kg=_num(row.weight_kg),
                        duration_s=row.duration_s,
                        distance_m=row.distance_m,
                        is_warmup=row.is_warmup,
                    )
                    for row in previous
                ],
                previous_day=previous_day,
                bests=bests_out(view.bests.get(exercise_id)),
            )
        )
    return WorkoutOut(
        **brief.model_dump(),
        notes_md=workout.notes_md,
        rating=workout.rating,
        template_name=view.template.name if view.template is not None else None,
        exercises=exercises,
        records=[record_out(record, view.exercises, day=workout.local_day) for record in view.records],
        body_weight_kg=view.body_weight_kg,
    )


def plan_day_out(day: training_service.PlanDay) -> PlanDayOut:
    row = day.row
    return PlanDayOut(
        date=day.day,
        weekday=day.day.weekday(),
        source=day.source,  # type: ignore[arg-type]
        template=template_brief(day.template) if day.template is not None else None,
        is_rest_day=bool(row and row.is_rest_day),
        title=row.title if row is not None else None,
        planned_time=row.planned_time if row is not None else None,
        is_today=day.is_today,
        workouts=[workout_brief_out(brief) for brief in day.workouts],
    )


def recurring_out(row, templates: dict) -> RecurringDayOut | None:
    if row is None:
        return None
    template = templates.get(row.template_id) if row.template_id else None
    return RecurringDayOut(
        weekday=row.weekday,
        template=template_brief(template) if template is not None else None,
        is_rest_day=row.is_rest_day,
        title=row.title,
        planned_time=row.planned_time,
    )


def exercise_session_out(workout, sets) -> ExerciseSessionOut:
    working = [row for row in sets if not row.is_warmup]
    loaded = [row for row in working if row.weight_kg and row.reps]
    top = max(loaded, key=lambda r: (float(r.weight_kg), r.reps), default=None)
    e1rm = [float(row.est_1rm_kg) for row in working if row.est_1rm_kg is not None]
    return ExerciseSessionOut(
        workout_id=workout.id,
        local_day=workout.local_day,
        sets=len(working),
        top_weight_kg=_num(top.weight_kg) if top is not None else None,
        top_reps=top.reps if top is not None else None,
        est_1rm_kg=max(e1rm) if e1rm else None,
        volume_kg=round(sum(float(row.weight_kg or 0) * (row.reps or 0) for row in working), 2),
        reps=sum(row.reps or 0 for row in working),
        duration_s=max((row.duration_s or 0 for row in working), default=0) or None,
        distance_m=max((row.distance_m or 0 for row in working), default=0) or None,
    )


def burned_out(start: date, days: int, per_day: dict) -> BurnedOut:
    """Zero-filled days, rounded kcal; the average is over the whole window."""
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        kcal, count = per_day.get(day, (0.0, 0))
        rows.append(BurnedDayOut(date=day, kcal=round(kcal), workouts=count))
    total = sum(row.kcal for row in rows)
    return BurnedOut(days=rows, total_kcal=total, average_kcal=round(total / days) if days else 0)
