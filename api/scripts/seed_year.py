#!/usr/bin/env python
"""Seed the owner account with a year of realistic history (stage-9 performance pass).

    cd api && DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/dsos_s9perf \\
        SECRET_KEY=… .venv/bin/python scripts/seed_year.py [--days 365] [--seed 9]

Needs a migrated database with the reference data (``python -m app.cli import-foods
--source seed``, ``python -m app.seeds.exercises``, ``python -m app.cli seed``). Creates the
owner when missing (Europe/Moscow, Russian, onboarded with computed targets) and writes N
days (ending today, local time) of what a disciplined user produces:

* meals with 2–4 library items each (≈ 2 100 kcal a day), water, six recipes (two pinned),
  favourites and recents;
* three workouts a week from three templates (4 exercises × 4 sets, slowly progressing
  weights, personal records), the weekly plan, daily steps, sleep, weigh-ins;
* PARA: areas, projects (most of them completed during the year), tags, ≈ 7 tasks a day
  with subtasks, time blocks, tags and reminders, three recurring series, an inbox, an
  upcoming week, a few overdue tasks; time entries and the journal on most days.

Everything is written in bulk with explicit local days and UTC instants (the same rules the
services follow), then the gamification engine runs once over the whole history — what the
nightly jobs would have done during the year (daily scores, XP, streaks, achievements — all
of them already seen —, quests). Deterministic for a given ``--seed``; refuses to run on an account that already has
meals (re-create the database instead).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time as clock
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `app` from api/

from sqlalchemy import func, insert, select, update

from app.db import SessionLocal
from app.models import (
    Area,
    BodyMetric,
    Exercise,
    ExerciseType,
    Food,
    FoodFavorite,
    FoodRecent,
    HealthDaily,
    HealthSource,
    ItemSource,
    JournalEntry,
    KcalMethod,
    MatchMethod,
    MealItem,
    MealLog,
    MealSource,
    MealType,
    MuscleGroup,
    PersonalRecord,
    Project,
    ProjectStatus,
    Recipe,
    RecipeItem,
    RecurrenceMode,
    Reminder,
    ReminderKind,
    ReminderStatus,
    SleepLog,
    Tag,
    Task,
    TaskStatus,
    TimeEntry,
    TimeEntrySource,
    User,
    UserAchievement,
    UserSettings,
    WaterLog,
    WeeklyPlan,
    WorkoutLog,
    WorkoutSet,
    WorkoutStatus,
    WorkoutTemplate,
    WorkoutTemplateItem,
    task_tags,
)
from app.services.bootstrap import create_owner
from app.services.settings_service import get_or_create_settings, recompute_targets

TZ_NAME = "Europe/Moscow"
D = Decimal


def q2(value: float) -> Decimal:
    return D(str(round(value, 2)))


@dataclass(slots=True)
class Ctx:
    user_id: uuid.UUID
    tz: ZoneInfo
    today: date
    now: datetime
    rng: random.Random

    def at(self, day: date, hh: int, mm: int = 0) -> datetime:
        """UTC instant of a local wall-clock time on ``day``."""
        hh, mm = divmod(hh * 60 + mm, 60)
        extra, hh = divmod(hh, 24)
        local = datetime.combine(day + timedelta(days=extra), time(hh, mm), tzinfo=self.tz)
        return local.astimezone(UTC)

    def past(self, instant: datetime) -> bool:
        return instant <= self.now


class Batch:
    """Rows per table, inserted in FK order with executemany (one statement per 2 000 rows).

    An executemany INSERT takes its column list from the first row, so every row of a
    table must carry the same keys (checked here); rows keep their order, so a row may
    reference one added before it (subtasks, recurring instances).
    """

    def __init__(self) -> None:
        self.rows: dict[Any, list[dict[str, Any]]] = defaultdict(list)

    def add(self, table: Any, **values: Any) -> dict[str, Any]:
        if table is not task_tags and "id" not in values:
            values["id"] = uuid.uuid4()
        rows = self.rows[table]
        if rows and rows[0].keys() != values.keys():
            raise ValueError(f"{table}: keys {sorted(values)} differ from {sorted(rows[0])}")
        rows.append(values)
        return values

    async def flush(self, db, order: list[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in order:
            rows = self.rows.pop(table, [])
            target = table if table is task_tags else table.__table__
            for start in range(0, len(rows), 2000):
                await db.execute(insert(target), rows[start : start + 2000])
            counts[target.name] = len(rows)
        assert not self.rows, f"tables left out of the insert order: {list(self.rows)}"
        return counts


# --------------------------------------------------------------------------- #
# Account
# --------------------------------------------------------------------------- #
async def prepare_owner(db) -> tuple[User, UserSettings]:
    result = await create_owner(db, display_name="Фёдор", language="ru", timezone=TZ_NAME, with_pairing_code=False)
    user = result.user
    row = await get_or_create_settings(db, user)
    row.language = "ru"  # type: ignore[assignment]
    row.timezone = TZ_NAME
    row.sex = "male"  # type: ignore[assignment]
    row.birth_date = date(1991, 5, 14)
    row.height_cm = 181
    row.weight_kg = 79.5
    row.activity_level = "moderate"  # type: ignore[assignment]
    row.goal_mode = "lose"  # type: ignore[assignment]
    row.goal_rate_pct = 10
    row.goal_weight_kg = 77
    row.target_steps = 9000
    row.onboarding_completed = True
    await recompute_targets(db, row, force=True)
    await db.flush()
    return user, row


# --------------------------------------------------------------------------- #
# Nutrition
# --------------------------------------------------------------------------- #
MEALS = (  # type, local start minute, spread (min), kcal share
    (MealType.BREAKFAST, 7 * 60 + 30, 90, 0.26),
    (MealType.LUNCH, 12 * 60 + 40, 80, 0.34),
    (MealType.SNACK, 16 * 60 + 10, 60, 0.10),
    (MealType.DINNER, 18 * 60 + 50, 100, 0.30),
)


def item_values(ctx: Ctx, meal_id: uuid.UUID, food: Food, grams: float, order: int) -> dict[str, Any]:
    factor = grams / 100
    return {
        "user_id": ctx.user_id,
        "meal_log_id": meal_id,
        "food_id": food.id,
        "name": food.name_ru or food.name_en or "?",
        "name_en": food.name_en,
        "brand": food.brand,
        "grams": q2(grams),
        "quantity": D(1),
        "unit": "g",
        "kcal": q2(float(food.kcal_per_100g) * factor),
        "protein_g": q2(float(food.protein_per_100g) * factor),
        "fat_g": q2(float(food.fat_per_100g) * factor),
        "carbs_g": q2(float(food.carbs_per_100g) * factor),
        "fiber_g": q2(float(food.fiber_per_100g) * factor),
        "item_source": ItemSource.FOOD,
        "match_method": MatchMethod.MANUAL,
        "sort_order": order,
    }


def seed_nutrition(ctx: Ctx, batch: Batch, days: list[date], foods: list[Food]) -> None:
    rng = ctx.rng
    usable = [food for food in foods if float(food.kcal_per_100g) >= 20]
    staples = rng.sample(usable, min(45, len(usable)))  # people eat the same few dozen foods
    favourites = staples[:12]
    recents: dict[uuid.UUID, tuple[datetime, int, float]] = {}

    for order, food in enumerate(favourites):
        batch.add(FoodFavorite, user_id=ctx.user_id, food_id=food.id, sort_order=order, default_grams=q2(150))

    for day in days:
        daily_kcal = rng.gauss(2150, 220)
        for meal_type, start, spread, share in MEALS:
            if meal_type == MealType.SNACK and rng.random() < 0.35:
                continue
            logged_at = ctx.at(day, 0, start + rng.randint(0, spread))
            if not ctx.past(logged_at):
                continue
            meal_id = uuid.uuid4()
            picks = rng.sample(staples if rng.random() < 0.85 else usable, rng.randint(2, 4))
            target = daily_kcal * share
            items = []
            for index, food in enumerate(picks):
                portion = target * rng.uniform(0.6, 1.4) / len(picks)
                grams = min(450.0, max(15.0, portion / float(food.kcal_per_100g) * 100))
                items.append(item_values(ctx, meal_id, food, round(grams), index))
                seen = recents.get(food.id)
                recents[food.id] = (logged_at, (seen[1] if seen else 0) + 1, round(grams))
            totals = {
                key: sum((item[key] for item in items), D(0))
                for key in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")
            }
            batch.add(
                MealLog,
                id=meal_id,
                client_uuid=meal_id,
                user_id=ctx.user_id,
                logged_at=logged_at,
                local_day=day,
                meal_type=meal_type,
                source=rng.choice((MealSource.SEARCH, MealSource.SEARCH, MealSource.RECENT, MealSource.FAVORITE)),
                item_count=len(items),
                created_at=logged_at,
                updated_at=logged_at,
                **totals,
            )
            for item in items:
                batch.add(MealItem, created_at=logged_at, updated_at=logged_at, **item)
        for _ in range(rng.randint(5, 9)):
            logged_at = ctx.at(day, 8, rng.randint(0, 13 * 60))
            if ctx.past(logged_at):
                water_id = uuid.uuid4()
                batch.add(
                    WaterLog,
                    id=water_id,
                    client_uuid=water_id,
                    user_id=ctx.user_id,
                    logged_at=logged_at,
                    local_day=day,
                    amount_ml=rng.choice((250, 250, 300, 330, 500)),
                    created_at=logged_at,
                    updated_at=logged_at,
                )

    for food_id, (last_used, count, grams) in recents.items():
        batch.add(
            FoodRecent,
            user_id=ctx.user_id,
            food_id=food_id,
            use_count=count,
            last_used_at=last_used,
            last_grams=q2(grams),
        )

    names = ("Овсянка с ягодами", "Курица с рисом", "Гречка с говядиной", "Салат с тунцом", "Омлет", "Суп-пюре")
    for index, name in enumerate(names):
        recipe_id = uuid.uuid4()
        picks = rng.sample(staples, rng.randint(3, 5))
        rows = []
        for order, food in enumerate(picks):
            grams = float(rng.randint(60, 250))
            row = item_values(ctx, recipe_id, food, grams, order)
            rows.append(
                {
                    "user_id": ctx.user_id,
                    "recipe_id": recipe_id,
                    "food_id": food.id,
                    "grams": row["grams"],
                    "sort_order": order,
                    "snapshot_name": row["name"],
                    **{key: row[key] for key in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")},
                }
            )
        raw = float(sum(row["grams"] for row in rows))
        cooked = round(raw * 0.9, 2)
        total = {
            key: float(sum(row[key] for row in rows)) for key in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")
        }
        batch.add(
            Recipe,
            id=recipe_id,
            user_id=ctx.user_id,
            name=name,
            total_raw_weight_g=q2(raw),
            cooked_weight_g=q2(cooked),
            servings=D(2),
            portion_size_g=q2(cooked / 2),
            total_kcal=q2(total["kcal"]),
            total_protein_g=q2(total["protein_g"]),
            total_fat_g=q2(total["fat_g"]),
            total_carbs_g=q2(total["carbs_g"]),
            total_fiber_g=q2(total["fiber_g"]),
            kcal_per_100g=q2(total["kcal"] / cooked * 100),
            protein_per_100g=q2(total["protein_g"] / cooked * 100),
            fat_per_100g=q2(total["fat_g"] / cooked * 100),
            carbs_per_100g=q2(total["carbs_g"] / cooked * 100),
            fiber_per_100g=q2(total["fiber_g"] / cooked * 100),
            is_pinned=index < 2,
            use_count=rng.randint(3, 30),
        )
        for row in rows:
            batch.add(RecipeItem, **row)


# --------------------------------------------------------------------------- #
# Training & health
# --------------------------------------------------------------------------- #
SPLIT = {  # template name, muscle groups, weekday (0 = Monday)
    "Грудь и трицепс": ((MuscleGroup.CHEST, MuscleGroup.CHEST, MuscleGroup.TRICEPS, MuscleGroup.SHOULDERS), 0),
    "Спина и бицепс": ((MuscleGroup.BACK, MuscleGroup.BACK, MuscleGroup.BICEPS, MuscleGroup.BICEPS), 2),
    "Ноги": ((MuscleGroup.QUADS, MuscleGroup.HAMSTRINGS, MuscleGroup.GLUTES, MuscleGroup.CALVES), 4),
}


def seed_training(ctx: Ctx, batch: Batch, days: list[date], exercises: list[Exercise]) -> None:
    rng = ctx.rng
    by_muscle: dict[MuscleGroup, list[Exercise]] = defaultdict(list)
    for exercise in exercises:
        by_muscle[exercise.muscle_group].append(exercise)
    fallback = [exercise for exercise in exercises if exercise.exercise_type == ExerciseType.STRENGTH] or exercises

    templates: dict[int, tuple[uuid.UUID, str, list[Exercise]]] = {}
    for name, (groups, weekday) in SPLIT.items():
        template_id = uuid.uuid4()
        chosen: list[Exercise] = []
        for group in groups:
            pool = [exercise for exercise in by_muscle.get(group, []) if exercise not in chosen] or fallback
            chosen.append(rng.choice(pool))
        batch.add(WorkoutTemplate, id=template_id, user_id=ctx.user_id, name=name, estimated_minutes=60, use_count=150)
        for order, exercise in enumerate(chosen):
            batch.add(
                WorkoutTemplateItem,
                user_id=ctx.user_id,
                template_id=template_id,
                exercise_id=exercise.id,
                sort_order=order,
                target_sets=4,
                target_reps_min=6,
                target_reps_max=10,
            )
        templates[weekday] = (template_id, name, chosen)
    for weekday in range(7):
        plan = templates.get(weekday)
        batch.add(
            WeeklyPlan,
            user_id=ctx.user_id,
            weekday=weekday,
            template_id=plan[0] if plan else None,
            title=plan[1] if plan else None,
            planned_time=time(19, 0) if plan else None,
            is_rest_day=plan is None,
        )

    best: dict[uuid.UUID, float] = {}
    base: dict[uuid.UUID, float] = {}
    weight = 82.0
    for offset, day in enumerate(days):
        progress = 1 + offset / len(days) * 0.18
        # --- health -------------------------------------------------------------
        steps = max(1500, int(rng.gauss(9300, 2600)))
        ingested = ctx.at(day, 22)
        if day == ctx.today:
            steps = int(steps * min(1.0, max(0.1, (ctx.now - ctx.at(day, 7)).total_seconds() / 54000)))
            ingested = ctx.now - timedelta(minutes=20)
        batch.add(
            HealthDaily,
            user_id=ctx.user_id,
            local_day=day,
            steps=steps,
            active_kcal=q2(steps * 0.042),
            distance_m=int(steps * 0.76),
            source=HealthSource.SHORTCUT,
            ingested_at=ingested,
        )
        if day < ctx.today or ctx.now > ctx.at(day, 8):
            bed = ctx.at(day - timedelta(days=1), 23, rng.randint(-40, 70))
            wake = ctx.at(day, 7, rng.randint(0, 60))
            sleep_id = uuid.uuid4()
            batch.add(
                SleepLog,
                id=sleep_id,
                client_uuid=sleep_id,
                user_id=ctx.user_id,
                local_day=day,
                bed_at=bed,
                wake_at=wake,
                duration_min=int((wake - bed).total_seconds() // 60),
                quality=rng.randint(3, 5),
            )
        weight += rng.gauss(-0.012, 0.18)
        if rng.random() < 0.6:
            metric_id = uuid.uuid4()
            batch.add(
                BodyMetric,
                id=metric_id,
                client_uuid=metric_id,
                user_id=ctx.user_id,
                measured_on=day,
                measured_at=ctx.at(day, 7, 40),
                weight_kg=q2(weight),
            )
        # --- workouts -------------------------------------------------------------
        plan = templates.get(day.weekday())
        started = ctx.at(day, 19, rng.randint(-30, 40))
        if plan is None or rng.random() > 0.9 or not ctx.past(started + timedelta(minutes=70)):
            continue
        template_id, title, chosen = plan
        workout_id = uuid.uuid4()
        duration = rng.randint(50, 75) * 60
        totals = {"sets": 0, "reps": 0, "volume": 0.0, "prs": 0}
        for order, exercise in enumerate(chosen):
            start_weight = base.setdefault(exercise.id, rng.choice((30.0, 40.0, 50.0, 60.0, 70.0)))
            for set_index in range(1, 5):
                reps = rng.randint(6, 10)
                kg = round(start_weight * progress * rng.uniform(0.95, 1.05) / 2.5) * 2.5
                set_id = uuid.uuid4()
                batch.add(
                    WorkoutSet,
                    id=set_id,
                    client_uuid=set_id,
                    user_id=ctx.user_id,
                    workout_log_id=workout_id,
                    exercise_id=exercise.id,
                    sort_order=order * 10 + set_index,
                    set_index=set_index,
                    reps=reps,
                    weight_kg=q2(kg),
                    rpe=float(rng.randint(7, 9)),
                    est_1rm_kg=q2(kg * (1 + reps / 30)),
                    created_at=started,
                    updated_at=started,
                )
                totals["sets"] += 1
                totals["reps"] += reps
                totals["volume"] += reps * kg
                if kg > best.get(exercise.id, 0):
                    if exercise.id in best:
                        batch.add(
                            PersonalRecord,
                            user_id=ctx.user_id,
                            exercise_id=exercise.id,
                            kind="weight",
                            value=q2(kg),
                            previous_value=q2(best[exercise.id]),
                            unit="kg",
                            reps=reps,
                            achieved_at=started + timedelta(minutes=10 * order),
                            workout_log_id=workout_id,
                            workout_set_id=set_id,
                            is_celebrated=True,
                        )
                        totals["prs"] += 1
                    best[exercise.id] = kg
        batch.add(
            WorkoutLog,
            id=workout_id,
            client_uuid=workout_id,
            user_id=ctx.user_id,
            template_id=template_id,
            title=title,
            kind=ExerciseType.STRENGTH,
            status=WorkoutStatus.COMPLETED,
            started_at=started,
            ended_at=started + timedelta(seconds=duration),
            local_day=day,
            duration_s=duration,
            rating=rng.randint(3, 5),
            total_sets=totals["sets"],
            total_reps=totals["reps"],
            total_volume_kg=q2(totals["volume"]),
            pr_count=totals["prs"],
            kcal_burned=q2(5.0 * weight * duration / 3600),
            kcal_method=KcalMethod.MET,
            created_at=started,
            updated_at=started + timedelta(seconds=duration),
        )


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #
TAGS = ("срочно", "звонок", "дом", "офис", "чтение", "спорт", "покупки", "идея", "ждать", "фокус")
TITLES = (
    "Позвонить в банк",
    "Ответить на письма",
    "Подготовить отчёт",
    "Купить продукты",
    "Оплатить счета",
    "Созвон с командой",
    "Разобрать заметки",
    "Прочитать главу книги",
    "Записаться к врачу",
    "Обновить резюме",
    "Сделать бэкап",
    "Спланировать неделю",
    "Ревью задач проекта",
    "Написать план тренировок",
    "Почистить почту",
    "Встреча с клиентом",
    "Заказать подарок",
    "Проверить бюджет",
    "Убрать на рабочем столе",
    "Посмотреть лекцию",
)
SERIES = (  # title, rule, local due weekday filter, area index (default areas: health, work, finance, …, home)
    ("Медитация 10 минут", "FREQ=DAILY", None, 0),
    ("План на день", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", {0, 1, 2, 3, 4}, 1),
    ("Уборка дома", "FREQ=WEEKLY;BYDAY=SA", {5}, 4),
)


def task_row(ctx: Ctx, batch: Batch, **values: Any) -> dict[str, Any]:
    """A task with every column the seed uses (same keys for every row of the batch)."""
    task_id = values.pop("id", None) or uuid.uuid4()
    created = values.pop("created_at", ctx.now)
    defaults: dict[str, Any] = {
        "id": task_id,
        "client_uuid": task_id,
        "user_id": ctx.user_id,
        "title": "",
        "status": TaskStatus.OPEN,
        "priority": 3,
        "notes_md": None,
        "project_id": None,
        "area_id": None,
        "parent_id": None,
        "due_date": None,
        "due_at": None,
        "scheduled_start": None,
        "scheduled_end": None,
        "estimate_minutes": None,
        "completed_at": None,
        "recurrence_mode": RecurrenceMode.NONE,
        "recurrence_rule": None,
        "recurrence_parent_id": None,
        "series_index": None,
        "tags_snapshot": [],
        "sort_order": 0,
        "created_at": created,
        "updated_at": values.get("completed_at") or created,
    }
    unknown = values.keys() - defaults.keys()
    if unknown:
        raise ValueError(f"task_row: add {sorted(unknown)} to the defaults")
    return batch.add(Task, **{**defaults, **values})


def seed_planner(
    ctx: Ctx, batch: Batch, days: list[date], areas: list[dict[str, Any]], tags: list[dict[str, Any]]
) -> None:
    """``areas``/``tags``: the owner's default rows (``bootstrap.ensure_defaults``); missing tags are added."""
    rng = ctx.rng
    first = days[0]
    have = {tag["name"].casefold() for tag in tags}
    tags = tags + [
        batch.add(Tag, user_id=ctx.user_id, name=name, created_at=ctx.at(first, 9))
        for name in TAGS
        if name.casefold() not in have
    ]

    projects: list[dict[str, Any]] = []
    for index in range(14):
        start = first + timedelta(days=index * 25)
        done = start + timedelta(days=rng.randint(20, 60))
        finished = done < ctx.today - timedelta(days=3)
        projects.append(
            batch.add(
                Project,
                user_id=ctx.user_id,
                name=f"Проект {index + 1}: {rng.choice(('запуск', 'ремонт', 'курс', 'отчёт', 'переезд', 'аудит'))}",
                area_id=areas[index % len(areas)]["id"],
                status=ProjectStatus.COMPLETED if finished else ProjectStatus.ACTIVE,
                start_date=start,
                deadline=done,
                completed_at=ctx.at(done, 18) if finished else None,
                sort_order=index,
                created_at=ctx.at(start, 9),
                updated_at=ctx.at(done, 18) if finished else ctx.at(start, 9),
            )
        )

    def project_on(day: date) -> dict[str, Any] | None:
        live = [p for p in projects if p["start_date"] <= day and (p["completed_at"] is None or p["deadline"] >= day)]
        return rng.choice(live) if live and rng.random() < 0.7 else None

    def tag_it(task: dict[str, Any]) -> None:
        if rng.random() < 0.4:
            chosen = rng.sample(tags, rng.randint(1, 2))
            task["tags_snapshot"] = [tag["name"] for tag in chosen]
            for tag in chosen:
                batch.add(
                    task_tags, task_id=task["id"], tag_id=tag["id"], user_id=ctx.user_id, created_at=task["created_at"]
                )

    def remind(task: dict[str, Any], at: datetime) -> None:
        reminder_id = uuid.uuid4()
        fired = ctx.past(at)
        closed = task["status"] in (TaskStatus.DONE, TaskStatus.CANCELLED)
        status = ReminderStatus.SENT if fired else (ReminderStatus.CANCELLED if closed else ReminderStatus.PENDING)
        batch.add(
            Reminder,
            id=reminder_id,
            user_id=ctx.user_id,
            kind=ReminderKind.TASK,
            task_id=task["id"],
            title=task["title"][:200],
            payload={"lead_minutes": 15},
            remind_at=at,
            lead_minutes=15,
            status=status,
            sent_at=at if fired else None,
            attempts=1 if fired else 0,
            dedupe_key=f"task:{task['id']}:{reminder_id}",
        )

    done_on: dict[date, list[dict[str, Any]]] = defaultdict(list)
    recent = ctx.today - timedelta(days=10)
    for day in days:
        for _ in range(rng.randint(4, 9)):
            created = ctx.at(day, 8, rng.randint(0, 11 * 60))
            if not ctx.past(created):
                continue
            project = project_on(day)
            due_day = day + timedelta(days=rng.choice((0, 0, 0, 1, 1, 2, 3, 5)))
            timed = rng.random() < 0.25
            due_at = ctx.at(due_day, rng.randint(10, 19)) if timed else None
            block = rng.random() < 0.2
            block_start = ctx.at(due_day, rng.randint(9, 17)) if block else None
            roll = rng.random()
            if day < recent:
                status = TaskStatus.DONE if roll < 0.92 else TaskStatus.CANCELLED
            else:
                status = TaskStatus.DONE if roll < 0.62 else TaskStatus.OPEN
            completed = None
            if status != TaskStatus.OPEN:
                completed = min(
                    ctx.at(due_day, rng.randint(10, 21), rng.randint(0, 59)), ctx.now - timedelta(minutes=5)
                )
                completed = max(completed, created + timedelta(minutes=10))
            task = task_row(
                ctx,
                batch,
                title=rng.choice(TITLES),
                notes_md="Детали в заметке" if rng.random() < 0.15 else None,
                status=status,
                priority=rng.choice((1, 2, 2, 3, 3, 3, 4)),
                project_id=project["id"] if project else None,
                area_id=None if project else rng.choice(areas)["id"],
                due_date=due_day,
                due_at=due_at,
                scheduled_start=block_start,
                scheduled_end=block_start + timedelta(minutes=rng.choice((30, 60, 90))) if block_start else None,
                estimate_minutes=rng.choice((None, 15, 30, 45, 60)),
                completed_at=completed,
                created_at=created,
            )
            tag_it(task)
            if due_at is not None:
                remind(task, due_at - timedelta(minutes=15))
            if completed is not None and status == TaskStatus.DONE:
                done_on[completed.astimezone(ctx.tz).date()].append(task)
            if rng.random() < 0.15:
                for index in range(rng.randint(2, 4)):
                    sub_done = status == TaskStatus.DONE or (status == TaskStatus.OPEN and rng.random() < 0.4)
                    task_row(
                        ctx,
                        batch,
                        title=f"Шаг {index + 1}",
                        parent_id=task["id"],
                        project_id=task["project_id"],
                        area_id=task["area_id"],
                        status=TaskStatus.DONE if sub_done else TaskStatus.OPEN,
                        completed_at=completed or (created + timedelta(hours=1) if sub_done else None),
                        sort_order=index,
                        created_at=created,
                    )

    # recurring series: one instance per completion, the newest one open (due today or next)
    for title, rule, weekdays, area_index in SERIES:
        root_id = uuid.uuid4()
        eligible = [day for day in days if weekdays is None or day.weekday() in weekdays]
        index = 0
        for position, day in enumerate(eligible):
            last = position == len(eligible) - 1
            done_at = ctx.at(day, 7, rng.randint(30, 600))
            completing = not last and ctx.past(done_at) and rng.random() < 0.88
            if not (completing or last):
                continue
            task_row(
                ctx,
                batch,
                id=root_id if index == 0 else None,
                title=title,
                status=TaskStatus.DONE if completing else TaskStatus.OPEN,
                priority=2,
                area_id=areas[area_index]["id"],
                due_date=day,
                recurrence_mode=RecurrenceMode.RRULE,
                recurrence_rule=rule,
                recurrence_parent_id=None if index == 0 else root_id,
                series_index=index,
                completed_at=done_at if completing else None,
                created_at=ctx.at(day, 0, 5),
            )
            index += 1

    # the current backlog: inbox, upcoming, someday
    for index in range(22):
        task_row(
            ctx,
            batch,
            title=f"Идея: {rng.choice(TITLES).lower()}",
            status=TaskStatus.INBOX,
            created_at=ctx.now - timedelta(days=rng.randint(0, 20), hours=index),
        )
    for index in range(28):
        due_day = ctx.today + timedelta(days=rng.randint(1, 14))
        task = task_row(
            ctx,
            batch,
            title=rng.choice(TITLES),
            priority=rng.choice((1, 2, 3, 3, 4)),
            project_id=rng.choice(projects[-4:])["id"],
            due_date=due_day,
            due_at=ctx.at(due_day, 11 + index % 7) if index % 3 == 0 else None,
            created_at=ctx.now - timedelta(days=rng.randint(0, 6)),
        )
        tag_it(task)
        if task["due_at"] is not None:
            remind(task, task["due_at"] - timedelta(minutes=15))
    for _ in range(18):
        task_row(
            ctx,
            batch,
            title=rng.choice(TITLES),
            area_id=rng.choice(areas)["id"],
            created_at=ctx.now - timedelta(days=rng.randint(10, 200)),
        )

    # time entries: what was worked on, mostly on completed tasks
    for day in days:
        if day.weekday() == 6 and rng.random() < 0.7:
            continue
        minute = 9 * 60 + rng.randint(0, 50)
        for _ in range(rng.randint(2, 4)):
            length = rng.randint(20, 95)
            start = ctx.at(day, 0, minute)
            end = start + timedelta(minutes=length)
            if not ctx.past(end):
                break
            task = rng.choice(done_on[day]) if done_on.get(day) else None
            entry_id = uuid.uuid4()
            batch.add(
                TimeEntry,
                id=entry_id,
                client_uuid=entry_id,
                user_id=ctx.user_id,
                task_id=task["id"] if task else None,
                project_id=task["project_id"] if task else None,
                area_id=(task["area_id"] if task else rng.choice(areas)["id"]),
                description=None if task else "Фокус-сессия",
                started_at=start,
                ended_at=end,
                duration_s=length * 60,
                local_day=day,
                source=TimeEntrySource.TIMER,
                created_at=end,
                updated_at=end,
            )
            minute += length + rng.randint(10, 90)

    for day in days:
        written = ctx.at(day, 21, 40)
        if rng.random() < 0.82 and ctx.past(written):
            entry_id = uuid.uuid4()
            batch.add(
                JournalEntry,
                id=entry_id,
                client_uuid=entry_id,
                user_id=ctx.user_id,
                entry_date=day,
                note_md="Хороший день: сделал главное, тренировка по плану."
                if rng.random() < 0.5
                else "Устал, но держу режим.",
                rating=rng.randint(3, 5),
                mood=rng.randint(2, 5),
                energy=rng.randint(2, 5),
                wins_md="- закрыл задачи\n- вода по плану",
                created_at=written,
                updated_at=written,
            )


# --------------------------------------------------------------------------- #
async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=365, help="days of history, ending today (default 365)")
    parser.add_argument("--seed", type=int, default=9, help="random seed (deterministic data)")
    parser.add_argument("--no-game", action="store_true", help="skip the gamification run")
    args = parser.parse_args()

    started = clock.perf_counter()
    async with SessionLocal() as db:
        user, _settings = await prepare_owner(db)
        if await db.scalar(select(func.count(MealLog.id)).where(MealLog.user_id == user.id)):
            print("✗ the owner already has meals — seed a freshly created database", file=sys.stderr)
            return 1
        foods = list(
            (await db.scalars(select(Food).where(Food.is_global.is_(True), Food.is_archived.is_(False)))).all()
        )
        exercises = list((await db.scalars(select(Exercise).where(Exercise.is_global.is_(True)))).all())
        if len(foods) < 50 or len(exercises) < 10:
            print(
                "✗ reference data missing: import-foods --source seed, app.seeds.exercises, app.cli seed",
                file=sys.stderr,
            )
            return 1

        tz = ZoneInfo(TZ_NAME)
        now = datetime.now(UTC)
        today = now.astimezone(tz).date()
        ctx = Ctx(user_id=user.id, tz=tz, today=today, now=now, rng=random.Random(args.seed))  # noqa: S311 - test data, not secrets
        days = [today - timedelta(days=offset) for offset in range(args.days - 1, -1, -1)]

        batch = Batch()
        seed_nutrition(ctx, batch, days, foods)
        seed_training(ctx, batch, days, exercises)
        areas = [
            {"id": row.id, "name": row.name}
            for row in await db.scalars(select(Area).where(Area.user_id == user.id).order_by(Area.sort_order))
        ]
        tags = [
            {"id": row.id, "name": row.name}
            for row in await db.scalars(select(Tag).where(Tag.user_id == user.id).order_by(Tag.name))
        ]
        seed_planner(ctx, batch, days, areas, tags)
        counts = await batch.flush(
            db,
            [
                FoodFavorite,
                MealLog,
                MealItem,
                WaterLog,
                FoodRecent,
                Recipe,
                RecipeItem,
                WorkoutTemplate,
                WorkoutTemplateItem,
                WeeklyPlan,
                WorkoutLog,
                WorkoutSet,
                PersonalRecord,
                HealthDaily,
                SleepLog,
                BodyMetric,
                Tag,
                Project,
                Task,
                task_tags,
                Reminder,
                TimeEntry,
                JournalEntry,
            ],
        )
        await db.commit()
        print(f"✓ {args.days} days for {user.display_name} ({TZ_NAME}) in {clock.perf_counter() - started:.1f} s")
        print("  " + ", ".join(f"{name}={count}" for name, count in counts.items()))

    if not args.no_game:
        from app.services.gamification import engine

        game_started = clock.perf_counter()
        # the whole backlog in the engine's own chunks (a request finalises at most 60
        # days), as `app.cli backfill-scores` does: a year of use is a finished history
        snapshot, finalised = await engine.sync_fully(user.id)
        async with SessionLocal() as db:
            # a year of use: every unlock has been shown and celebrated already
            await db.execute(
                update(UserAchievement).where(UserAchievement.user_id == user.id).values(seen=True, celebrated=True)
            )
            await db.commit()
        assert snapshot is not None
        print(
            f"✓ gamification over the history in {clock.perf_counter() - game_started:.1f} s: "
            f"{finalised} days finalised, {snapshot.xp_total} XP, level {snapshot.level.current.level}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
