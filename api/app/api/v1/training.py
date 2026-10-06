"""Training (stage 4): exercise library, templates, weekly plan, workouts, PRs.

Burned calories are reported by ``/training/today`` and ``/training/burned``
on their own; nothing here changes the food budget.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.presenters import (
    bests_out,
    exercise_out,
    exercise_session_out,
    plan_day_out,
    record_out,
    recurring_out,
    template_out,
    workout_brief_out,
    workout_out,
)
from app.core.tz import today_in
from app.schemas.common import OkOut
from app.schemas.training import (
    BurnedDayOut,
    BurnedOut,
    EquipmentLiteral,
    ExerciseHistoryOut,
    ExerciseIn,
    ExerciseListOut,
    ExerciseOut,
    ExercisePatchIn,
    ExerciseTypeLiteral,
    MuscleLiteral,
    PlanDayIn,
    RecordOut,
    TemplateIn,
    TemplateOut,
    TrainingTodayOut,
    WeekPlanOut,
    WorkoutFinishIn,
    WorkoutListOut,
    WorkoutOut,
    WorkoutPatchIn,
    WorkoutQuickIn,
    WorkoutSetIn,
    WorkoutStartIn,
)
from app.services import training

router = APIRouter(prefix="/training", tags=["training"], dependencies=[Depends(require_device)])

Weekday = Annotated[int, Path(ge=0, le=6, description="0 = Monday … 6 = Sunday")]


def _tz(principal: CurrentPrincipal) -> str | None:
    return principal.settings_row.timezone if principal.settings_row else None


def _week_start(principal: CurrentPrincipal) -> int:
    return principal.settings_row.week_start if principal.settings_row else 1


async def _workout_response(db, workout) -> WorkoutOut:
    return workout_out(await training.workout_view(db, workout))


# --------------------------------------------------------------------------- #
# Exercises
# --------------------------------------------------------------------------- #
@router.get("/exercises", response_model=ExerciseListOut, summary="Search the exercise library and your exercises")
async def list_exercises(
    principal: CurrentPrincipal,
    db: DbSession,
    q: str = Query(default="", max_length=100),
    muscle: MuscleLiteral | None = None,
    equipment: EquipmentLiteral | None = None,
    type: ExerciseTypeLiteral | None = None,
    limit: int = Query(default=training.SEARCH_LIMIT, ge=1, le=200),
) -> ExerciseListOut:
    rows = await training.search_exercises(
        db,
        user_id=principal.user_id,
        query=q,
        muscle_group=muscle,
        equipment=equipment,
        exercise_type=type,
        limit=limit,
    )
    return ExerciseListOut(
        query=q,
        items=[exercise_out(row) for row in rows],
        recent_ids=await training.recent_exercise_ids(db, principal.user_id),
    )


@router.post("/exercises", response_model=ExerciseOut, status_code=201, summary="Create your own exercise")
async def create_exercise(payload: ExerciseIn, principal: CurrentPrincipal, db: DbSession) -> ExerciseOut:
    return exercise_out(await training.create_exercise(db, user_id=principal.user_id, payload=payload))


@router.patch("/exercises/{exercise_id}", response_model=ExerciseOut, summary="Edit or archive your exercise")
async def patch_exercise(
    exercise_id: uuid.UUID, payload: ExercisePatchIn, principal: CurrentPrincipal, db: DbSession
) -> ExerciseOut:
    exercise = await training.update_exercise(db, user_id=principal.user_id, exercise_id=exercise_id, payload=payload)
    return exercise_out(exercise)


@router.get(
    "/exercises/{exercise_id}/history",
    response_model=ExerciseHistoryOut,
    summary="Bests, records and recent sessions of one exercise",
)
async def exercise_history(
    exercise_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    limit: int = Query(default=12, ge=1, le=60),
) -> ExerciseHistoryOut:
    exercise, bests, records, sessions = await training.exercise_history(
        db, user_id=principal.user_id, exercise_id=exercise_id, limit=limit
    )
    return ExerciseHistoryOut(
        exercise=exercise_out(exercise),
        bests=bests_out(bests),
        records=[record_out(record, {exercise.id: exercise}) for record in records],
        sessions=[exercise_session_out(workout, sets) for workout, sets in sessions],
    )


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
@router.get("/templates", response_model=list[TemplateOut], summary="Your workout templates")
async def list_templates(principal: CurrentPrincipal, db: DbSession) -> list[TemplateOut]:
    templates = await training.list_templates(db, principal.user_id)
    exercises = await training.template_exercises(db, principal.user_id, templates)
    return [template_out(template, exercises) for template in templates]


@router.post("/templates", response_model=TemplateOut, status_code=201, summary="Create a template (idempotent by id)")
async def create_template(payload: TemplateIn, principal: CurrentPrincipal, db: DbSession) -> TemplateOut:
    template = await training.create_template(db, user_id=principal.user_id, payload=payload)
    return template_out(template, await training.template_exercises(db, principal.user_id, [template]))


@router.put("/templates/{template_id}", response_model=TemplateOut, summary="Replace a template and its exercises")
async def replace_template(
    template_id: uuid.UUID, payload: TemplateIn, principal: CurrentPrincipal, db: DbSession
) -> TemplateOut:
    template = await training.replace_template(db, user_id=principal.user_id, template_id=template_id, payload=payload)
    return template_out(template, await training.template_exercises(db, principal.user_id, [template]))


@router.delete("/templates/{template_id}", response_model=OkOut, summary="Archive a template")
async def delete_template(template_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await training.archive_template(db, user_id=principal.user_id, template_id=template_id)
    return OkOut()


@router.post(
    "/workouts/{workout_id}/save-as-template/{template_id}",
    response_model=TemplateOut,
    status_code=201,
    summary="Save a workout's exercises and top sets as a template",
)
async def save_as_template(
    workout_id: uuid.UUID,
    template_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    name: str = Query(min_length=1, max_length=160),
) -> TemplateOut:
    template = await training.template_from_workout(
        db, user_id=principal.user_id, workout_id=workout_id, template_id=template_id, name=name.strip()
    )
    return template_out(template, await training.template_exercises(db, principal.user_id, [template]))


# --------------------------------------------------------------------------- #
# Weekly plan
# --------------------------------------------------------------------------- #
async def _week(db, principal: CurrentPrincipal, start: date | None) -> WeekPlanOut:
    today = today_in(_tz(principal))
    first = training.week_start_for(start or today, _week_start(principal))
    days, recurring, templates = await training.week_plan(db, user_id=principal.user_id, start=first, today=today)
    return WeekPlanOut(
        start=first,
        days=[plan_day_out(day) for day in days],
        recurring=[recurring_out(row, templates) for row in recurring],
    )


@router.get("/plan", response_model=WeekPlanOut, summary="A week of the plan with what was actually done")
async def get_plan(
    principal: CurrentPrincipal,
    db: DbSession,
    start: date | None = Query(default=None, description="any day of the wanted week; default: this week"),
) -> WeekPlanOut:
    return await _week(db, principal, start)


@router.put("/plan/weekdays/{weekday}", response_model=WeekPlanOut, summary="Set the recurring plan of a weekday")
async def set_weekday(weekday: Weekday, payload: PlanDayIn, principal: CurrentPrincipal, db: DbSession) -> WeekPlanOut:
    await training.set_weekday_plan(db, user_id=principal.user_id, weekday=weekday, payload=payload)
    return await _week(db, principal, None)


@router.delete("/plan/weekdays/{weekday}", response_model=WeekPlanOut, summary="Clear the recurring plan of a weekday")
async def clear_weekday(weekday: Weekday, principal: CurrentPrincipal, db: DbSession) -> WeekPlanOut:
    await training.clear_weekday_plan(db, user_id=principal.user_id, weekday=weekday)
    return await _week(db, principal, None)


@router.put("/plan/dates/{plan_date}", response_model=WeekPlanOut, summary="Override the plan for one date")
async def set_date(plan_date: date, payload: PlanDayIn, principal: CurrentPrincipal, db: DbSession) -> WeekPlanOut:
    await training.set_date_plan(db, user_id=principal.user_id, plan_date=plan_date, payload=payload)
    return await _week(db, principal, plan_date)


@router.delete("/plan/dates/{plan_date}", response_model=WeekPlanOut, summary="Remove the override for one date")
async def clear_date(plan_date: date, principal: CurrentPrincipal, db: DbSession) -> WeekPlanOut:
    await training.clear_date_plan(db, user_id=principal.user_id, plan_date=plan_date)
    return await _week(db, principal, plan_date)


# --------------------------------------------------------------------------- #
# Today & burned calories
# --------------------------------------------------------------------------- #
@router.get("/today", response_model=TrainingTodayOut, summary="Today's plan, the workout in progress, burned kcal")
async def today(principal: CurrentPrincipal, db: DbSession) -> TrainingTodayOut:
    day = today_in(_tz(principal))
    first = training.week_start_for(day, _week_start(principal))
    days, _recurring, _templates = await training.week_plan(db, user_id=principal.user_id, start=first, today=day)
    plan = next(item for item in days if item.day == day)
    active = await training.active_workout(db, principal.user_id)
    active_brief = (await training.workout_briefs(db, principal.user_id, [active]))[0] if active is not None else None
    done = [brief for brief in plan.workouts if brief.workout.status.value == "completed"]
    burned = await training.burned_by_day(db, user_id=principal.user_id, start=day, end=day)
    return TrainingTodayOut(
        date=day,
        plan=plan_day_out(plan),
        active=workout_brief_out(active_brief) if active_brief is not None else None,
        done=[workout_brief_out(brief) for brief in done],
        burned_kcal=burned.get(day, (0.0, 0))[0],
    )


@router.get("/burned", response_model=BurnedOut, summary="Calories burned in workouts per day (not food budget)")
async def burned(
    principal: CurrentPrincipal,
    db: DbSession,
    days: int = Query(default=7, ge=1, le=90),
) -> BurnedOut:
    end = today_in(_tz(principal))
    start = end - timedelta(days=days - 1)
    per_day = await training.burned_by_day(db, user_id=principal.user_id, start=start, end=end)
    rows = [BurnedDayOut(date=start + timedelta(days=offset), kcal=0.0, workouts=0) for offset in range(days)]
    for row in rows:
        kcal, count = per_day.get(row.date, (0.0, 0))
        row.kcal, row.workouts = round(kcal), count
    total = sum(row.kcal for row in rows)
    return BurnedOut(days=rows, total_kcal=total, average_kcal=round(total / days))


@router.get("/records", response_model=list[RecordOut], summary="Your latest personal records")
async def records(
    principal: CurrentPrincipal, db: DbSession, limit: int = Query(default=10, ge=1, le=50)
) -> list[RecordOut]:
    rows = await training.recent_records(db, user_id=principal.user_id, limit=limit)
    exercises = await training.exercise_names(db, {row.exercise_id for row in rows})
    return [record_out(row, exercises) for row in rows]


# --------------------------------------------------------------------------- #
# Workouts
# --------------------------------------------------------------------------- #
@router.get("/workouts", response_model=WorkoutListOut, summary="Workout history, newest first")
async def list_workouts(
    principal: CurrentPrincipal,
    db: DbSession,
    before: datetime | None = Query(default=None, description="cursor: started_at of the last item seen"),
    limit: int = Query(default=20, ge=1, le=50),
) -> WorkoutListOut:
    briefs, has_more = await training.list_workouts(db, user_id=principal.user_id, before=before, limit=limit)
    return WorkoutListOut(items=[workout_brief_out(brief) for brief in briefs], has_more=has_more)


@router.post("/workouts", response_model=WorkoutOut, status_code=201, summary="Start a workout (idempotent by id)")
async def start_workout(payload: WorkoutStartIn, principal: CurrentPrincipal, db: DbSession) -> WorkoutOut:
    workout = await training.start_workout(db, user_id=principal.user_id, tz=_tz(principal), payload=payload)
    return await _workout_response(db, workout)


@router.post(
    "/workouts/quick",
    response_model=WorkoutOut,
    status_code=201,
    summary="Log a finished timed session (run, ride, swim) in one call",
)
async def quick_workout(payload: WorkoutQuickIn, principal: CurrentPrincipal, db: DbSession) -> WorkoutOut:
    workout = await training.quick_workout(db, user_id=principal.user_id, tz=_tz(principal), payload=payload)
    return await _workout_response(db, workout)


@router.get("/workouts/{workout_id}", response_model=WorkoutOut, summary="One workout with sets, targets and bests")
async def get_workout(workout_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> WorkoutOut:
    workout = await training.get_workout(db, user_id=principal.user_id, workout_id=workout_id)
    return await _workout_response(db, workout)


@router.put(
    "/workouts/{workout_id}/sets/{set_id}",
    response_model=WorkoutOut,
    summary="Log or correct a set (idempotent by set id)",
)
async def put_set(
    workout_id: uuid.UUID, set_id: uuid.UUID, payload: WorkoutSetIn, principal: CurrentPrincipal, db: DbSession
) -> WorkoutOut:
    workout = await training.upsert_set(
        db, user_id=principal.user_id, workout_id=workout_id, set_id=set_id, payload=payload
    )
    return await _workout_response(db, workout)


@router.delete("/workouts/{workout_id}/sets/{set_id}", response_model=WorkoutOut, summary="Remove a set")
async def delete_set(
    workout_id: uuid.UUID, set_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> WorkoutOut:
    workout = await training.delete_set(db, user_id=principal.user_id, workout_id=workout_id, set_id=set_id)
    return await _workout_response(db, workout)


@router.post(
    "/workouts/{workout_id}/finish",
    response_model=WorkoutOut,
    summary="Finish: duration, burned kcal (MET), personal records",
)
async def finish_workout(
    workout_id: uuid.UUID, payload: WorkoutFinishIn, principal: CurrentPrincipal, db: DbSession
) -> WorkoutOut:
    workout = await training.finish_workout(db, user_id=principal.user_id, workout_id=workout_id, payload=payload)
    return await _workout_response(db, workout)


@router.patch("/workouts/{workout_id}", response_model=WorkoutOut, summary="Edit title, notes, rating, duration, kcal")
async def patch_workout(
    workout_id: uuid.UUID, payload: WorkoutPatchIn, principal: CurrentPrincipal, db: DbSession
) -> WorkoutOut:
    workout = await training.patch_workout(db, user_id=principal.user_id, workout_id=workout_id, payload=payload)
    return await _workout_response(db, workout)


@router.delete("/workouts/{workout_id}", response_model=OkOut, summary="Delete (or discard) a workout")
async def delete_workout(workout_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await training.delete_workout(db, user_id=principal.user_id, workout_id=workout_id)
    return OkOut()
