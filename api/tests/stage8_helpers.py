"""Helpers shared by the stage-8 (gamification) tests.

Data is written straight into the tables with explicit local days and instants, and
the engine runs with an injected ``now`` — so every test is independent of the wall
clock (and of the time of day the suite happens to run at).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models import (
    BodyMetric,
    HealthDaily,
    HealthSource,
    JournalEntry,
    MealLog,
    PersonalRecord,
    Project,
    ProjectStatus,
    SleepLog,
    Task,
    TaskStatus,
    TimeEntry,
    User,
    UserSettings,
    WaterLog,
    WeeklyPlan,
    WorkoutLog,
    WorkoutStatus,
    XpEvent,
)

BANGKOK = ZoneInfo("Asia/Bangkok")
BERLIN = ZoneInfo("Europe/Berlin")
# Wednesday 15 July 2026, 12:00 in Bangkok (UTC+7, no DST); the week starts Monday 13 July
NOON = datetime(2026, 7, 15, 5, 0, tzinfo=UTC)
TODAY = date(2026, 7, 15)
YESTERDAY = TODAY - timedelta(days=1)

TARGETS = {
    "target_kcal": 2000,
    "target_protein_g": 150,
    "target_fat_g": 60,
    "target_carbs_g": 200,
    "target_fiber_g": 30,
    "target_water_ml": 2500,
    "target_steps": 8000,
}


def local(day: date, hh: int = 12, mm: int = 0, tz: ZoneInfo = BANGKOK) -> datetime:
    """UTC instant of a local wall-clock time."""
    return datetime.combine(day, time(hh, mm)).replace(tzinfo=tz).astimezone(UTC)


async def owner_id(db) -> uuid.UUID:
    return (await db.execute(select(User.id).where(User.is_owner.is_(True)))).scalar_one()


async def configure(db, user_id: uuid.UUID, **values: Any) -> UserSettings:
    """Fixed manual targets (2000 kcal, floor 1500 for a man) unless overridden."""
    row = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one()
    defaults: dict[str, Any] = {
        "timezone": "Asia/Bangkok",
        "week_start": 1,
        "sex": "male",
        "weight_kg": 80,
        "height_cm": 180,
        "birth_date": date(1990, 1, 1),
        "targets_manual": True,
        "safe_floor_kcal": None,
        "xp_weights": {},
        **TARGETS,
    }
    for key, value in {**defaults, **values}.items():
        setattr(row, key, value)
    await db.commit()
    return row


async def meal(
    db,
    user_id: uuid.UUID,
    day: date,
    kcal: float = 600,
    *,
    protein: float = 40,
    fiber: float = 8,
    hh: int = 12,
    mm: int = 0,
    deleted: bool = False,
) -> uuid.UUID:
    row = MealLog(
        user_id=user_id,
        logged_at=local(day, hh, mm),
        local_day=day,
        kcal=Decimal(str(kcal)),
        protein_g=Decimal(str(protein)),
        fiber_g=Decimal(str(fiber)),
        item_count=1,
        is_deleted=deleted,
    )
    db.add(row)
    await db.commit()
    return row.id


async def eat_day(db, user_id: uuid.UUID, day: date, kcal: float = 2000, *, protein: float = 150, fiber: float = 30):
    """One day of food split over three meals."""
    for index in range(3):
        await meal(db, user_id, day, kcal / 3, protein=protein / 3, fiber=fiber / 3, hh=8 + 5 * index)


async def water(db, user_id: uuid.UUID, day: date, ml: int = 2500) -> None:
    db.add(WaterLog(user_id=user_id, logged_at=local(day, 10), local_day=day, amount_ml=ml))
    await db.commit()


async def steps(db, user_id: uuid.UUID, day: date, count: int, *, active_kcal: float | None = None) -> None:
    db.add(
        HealthDaily(
            user_id=user_id,
            local_day=day,
            steps=count,
            active_kcal=Decimal(str(active_kcal)) if active_kcal is not None else None,
            source=HealthSource.MANUAL,
            ingested_at=local(day, 21),
        )
    )
    await db.commit()


async def workout(
    db,
    user_id: uuid.UUID,
    day: date,
    *,
    hh: int = 18,
    kcal_burned: float | None = None,
    status: WorkoutStatus = WorkoutStatus.COMPLETED,
) -> uuid.UUID:
    row = WorkoutLog(
        user_id=user_id,
        started_at=local(day, hh),
        ended_at=local(day, hh) + timedelta(minutes=50),
        local_day=day,
        status=status,
        kcal_burned=Decimal(str(kcal_burned)) if kcal_burned is not None else None,
        duration_s=3000,
    )
    db.add(row)
    await db.commit()
    return row.id


async def record(db, user_id: uuid.UUID, workout_id: uuid.UUID, exercise_id: uuid.UUID, kind: str, day: date) -> None:
    db.add(
        PersonalRecord(
            user_id=user_id,
            exercise_id=exercise_id,
            kind=kind,
            value=Decimal("100"),
            previous_value=Decimal("90"),
            unit="kg",
            achieved_at=local(day, 19),
            workout_log_id=workout_id,
        )
    )
    await db.commit()


async def exercise(db) -> uuid.UUID:
    from app.models import Equipment, Exercise, MuscleGroup

    row = Exercise(name_en=f"Press {uuid.uuid4().hex[:6]}", muscle_group=MuscleGroup.CHEST, equipment=Equipment.BARBELL)
    db.add(row)
    await db.commit()
    return row.id


async def done_task(
    db,
    user_id: uuid.UUID,
    completed_at: datetime,
    *,
    priority: int = 3,
    parent_id: uuid.UUID | None = None,
    title: str = "задача",
    status: TaskStatus = TaskStatus.DONE,
) -> uuid.UUID:
    row = Task(
        user_id=user_id,
        title=title,
        status=status,
        completed_at=completed_at,
        priority=priority,
        parent_id=parent_id,
    )
    db.add(row)
    await db.commit()
    return row.id


async def open_task(db, user_id: uuid.UUID, *, due: date | None = None, priority: int = 3) -> uuid.UUID:
    row = Task(user_id=user_id, title="открытая", status=TaskStatus.OPEN, due_date=due, priority=priority)
    db.add(row)
    await db.commit()
    return row.id


async def project_done(db, user_id: uuid.UUID, completed_at: datetime) -> uuid.UUID:
    row = Project(user_id=user_id, name=f"Проект {uuid.uuid4().hex[:4]}", status=ProjectStatus.COMPLETED)
    row.completed_at = completed_at
    db.add(row)
    await db.commit()
    return row.id


async def journal(db, user_id: uuid.UUID, day: date, **fields: Any) -> None:
    db.add(JournalEntry(user_id=user_id, entry_date=day, **({"note_md": "день прошёл хорошо"} | fields)))
    await db.commit()


async def sleep(db, user_id: uuid.UUID, day: date, minutes: int = 450) -> None:
    wake = local(day, 7)
    db.add(
        SleepLog(
            user_id=user_id,
            local_day=day,
            bed_at=wake - timedelta(minutes=minutes),
            wake_at=wake,
            duration_min=minutes,
        )
    )
    await db.commit()


async def weigh_in(db, user_id: uuid.UUID, day: date, kg: float = 80.0) -> None:
    db.add(BodyMetric(user_id=user_id, measured_on=day, weight_kg=Decimal(str(kg))))
    await db.commit()


async def tracked(db, user_id: uuid.UUID, day: date, minutes: int, *, hh: int = 9) -> None:
    start = local(day, hh)
    db.add(
        TimeEntry(
            user_id=user_id,
            started_at=start,
            ended_at=start + timedelta(minutes=minutes),
            duration_s=minutes * 60,
            local_day=day,
        )
    )
    await db.commit()


async def plan(db, user_id: uuid.UUID, weekday: int, *, rest: bool = False, title: str | None = "Тренировка") -> None:
    db.add(WeeklyPlan(user_id=user_id, weekday=weekday, is_rest_day=rest, title=None if rest else title))
    await db.commit()


async def run(db, user_id: uuid.UUID, now: datetime = NOON, stages=None):
    """One engine run in its own transaction (like a job); returns the snapshot."""
    from app.db import SessionLocal
    from app.services.gamification import engine

    async with SessionLocal() as session:
        snapshot = await engine.sync_user(session, user_id, now, stages=stages or engine.STAGES)
        await session.commit()
    db.expire_all()
    return snapshot


async def xp_rows(db, user_id: uuid.UUID) -> dict[str, XpEvent]:
    db.expire_all()
    rows = (await db.scalars(select(XpEvent).where(XpEvent.user_id == user_id))).all()
    return {row.dedupe_key: row for row in rows}


async def xp_total(db, user_id: uuid.UUID) -> int:
    return sum(row.points for row in (await xp_rows(db, user_id)).values())


@pytest.fixture(autouse=True)
def _fresh_seed_cache():
    """Every test starts from empty tables: re-check the gamification reference rows."""
    from app.seeds.gamification import reset_seed_cache

    reset_seed_cache()
    yield
    reset_seed_cache()
