"""Stage 8: /export/json, /export/csv, /export/backup and ``restore-backup``.

An account with at least one row in every exported table (and a secret in every
place one can hide: token hashes, the recovery phrase hash, pairing codes, push
endpoints and keys, raw AI payloads, raw library payloads) is exported in all three
formats: every section present, nothing secret (asserted on the text), CSV headers,
BOM and formula-safe cells, zip integrity, manifest checksums, streaming in chunks,
the rate limit and the device-only rule. Then the backup goes back in: export →
wipe → restore gives identical rows, a second restore changes nothing, a dry run
writes nothing, an empty account takes a backup, and every refusal (damaged file,
another schema, another user's rows, a non-empty account) writes nothing. The CLI
is run as a real process."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import orjson
import pytest
import sqlalchemy as sa
from sqlalchemy import func, select

from app.models import (
    Achievement,
    AiJob,
    AiJobKind,
    AiJobStatus,
    AiUsageDaily,
    ApiToken,
    Area,
    BodyMetric,
    Device,
    Equipment,
    Exercise,
    Food,
    FoodFavorite,
    FoodRecent,
    FoodSource,
    HealthDaily,
    HealthSource,
    ItemSource,
    JournalEntry,
    MatchMethod,
    MealItem,
    MealLog,
    MealType,
    MuscleGroup,
    NotificationLog,
    PairingCode,
    PersonalRecord,
    Project,
    ProjectStatus,
    PushSubscription,
    Recipe,
    RecipeItem,
    Reminder,
    ReminderKind,
    Resource,
    SavedMealTemplate,
    SleepLog,
    Streak,
    StreakKind,
    Tag,
    Task,
    TaskStatus,
    TimeEntry,
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
from app.services import export
from app.services.export import LIBRARY, SECTIONS
from app.services.restore import RestoreError, read_backup, restore
from tests.stage6_helpers import other_user
from tests.stage8_helpers import (
    NOON,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    local,
    owner_id,
    run,
)

pytestmark = pytest.mark.anyio
E = "/api/v1/export"
DAY = date(2026, 7, 14)  # inside the engine's open window at NOON (15 July, Bangkok)
FORMULA_TITLE = '=HYPERLINK("http://evil.example","click")'
SECRETS = {
    "recovery": "argon2id$SECRET-RECOVERY-HASH",
    "endpoint": "https://push.example.com/send/SECRET-PUSH-ENDPOINT",
    "p256dh": "SECRET-P256DH-KEY",
    "auth": "SECRET-PUSH-AUTH",
    "ai_request": "SECRET-AI-REQUEST-IMAGE-BASE64",
    "ai_response": "SECRET-AI-RAW-RESPONSE",
    "thumb": "/media/ai/SECRET-THUMB.jpg",
    "source_payload": "SECRET-LIBRARY-RAW-PAYLOAD",
    "custom_payload": "SECRET-CUSTOM-RAW-PAYLOAD",
}
RESTORED = [section for section in SECTIONS if section.restore]


# --------------------------------------------------------------------------- #
# An account with a row in every exported table
# --------------------------------------------------------------------------- #
async def _add(db, *rows: Any) -> None:
    db.add_all(rows)
    await db.commit()


async def populate(db, user_id: uuid.UUID, *, tag: str = "") -> dict[str, Any]:
    """One (or more) rows in every section, and the library rows they point at."""
    out: dict[str, Any] = {}
    library_food = Food(
        is_global=True,
        name_ru=f"Гречка библиотечная{tag}",
        name_en="Buckwheat",
        kcal_per_100g=Decimal("343"),
        protein_per_100g=Decimal("13.3"),
        fat_per_100g=Decimal("3.4"),
        carbs_per_100g=Decimal("71.5"),
        fiber_per_100g=Decimal("10"),
        source=FoodSource.SEED,
        source_id=f"seed-buckwheat-{uuid.uuid4().hex[:8]}",
        source_payload={"raw": SECRETS["source_payload"]},
        aliases=["гречка", "ядрица"],
    )
    custom_food = Food(
        user_id=user_id,
        is_global=False,
        name_ru=f"Сырники домашние{tag}",
        barcode=f"46{uuid.uuid4().int % 10**11:011d}",
        kcal_per_100g=Decimal("220.5"),
        protein_per_100g=Decimal("15"),
        fat_per_100g=Decimal("9.5"),
        carbs_per_100g=Decimal("18"),
        fiber_per_100g=Decimal("0.5"),
        source=FoodSource.CUSTOM,
        source_payload={"raw": SECRETS["custom_payload"]},
    )
    library_exercise = Exercise(
        is_global=True,
        name_en=f"Bench Press {uuid.uuid4().hex[:6]}",
        name_ru="Жим лёжа",
        muscle_group=MuscleGroup.CHEST,
        equipment=Equipment.BARBELL,
    )
    custom_exercise = Exercise(
        user_id=user_id,
        is_global=False,
        name_ru=f"Мой жим{tag}",
        muscle_group=MuscleGroup.SHOULDERS,
        equipment=Equipment.DUMBBELL,
    )
    await _add(db, library_food, custom_food, library_exercise, custom_exercise)
    out.update(library_food=library_food.id, custom_food=custom_food.id, library_exercise=library_exercise.id)

    recipe = Recipe(user_id=user_id, name=f"Каша{tag}", servings=Decimal("2"), total_kcal=Decimal("686"))
    meal = MealLog(
        user_id=user_id,
        logged_at=local(DAY, 8),
        local_day=DAY,
        meal_type=MealType.BREAKFAST,
        title=f"Завтрак{tag}",
        kcal=Decimal("2050.25"),
        protein_g=Decimal("150"),
        fiber_g=Decimal("30"),
        item_count=2,
    )
    template = WorkoutTemplate(user_id=user_id, name=f"Грудь{tag}")
    workout = WorkoutLog(
        user_id=user_id,
        started_at=local(DAY, 18),
        ended_at=local(DAY, 19),
        local_day=DAY,
        status=WorkoutStatus.COMPLETED,
        kcal_burned=Decimal("350.5"),
        total_volume_kg=Decimal("1200"),
    )
    area = Area(user_id=user_id, name=f"Спорт{tag}", weekly_target_minutes=120)
    tag_row = Tag(user_id=user_id, name=f"важное{tag}")
    await _add(db, recipe, meal, template, workout, area, tag_row)
    project = Project(user_id=user_id, name=f"Марафон{tag}", area_id=area.id, status=ProjectStatus.ACTIVE)
    await _add(db, project)
    parent = Task(
        user_id=user_id,
        title=FORMULA_TITLE,
        project_id=project.id,
        status=TaskStatus.DONE,
        completed_at=local(DAY, 10),
        priority=1,
    )
    series = Task(user_id=user_id, title=f"Пробежка{tag}", recurrence_mode="rrule", recurrence_rule="FREQ=DAILY")
    await _add(db, parent, series)
    child = Task(user_id=user_id, title=f"Подзадача{tag}", parent_id=parent.id, project_id=project.id)
    instance = Task(
        user_id=user_id, title=f"Пробежка{tag}", recurrence_parent_id=series.id, series_index=1, due_date=DAY
    )
    workout_set = WorkoutSet(
        user_id=user_id,
        workout_log_id=workout.id,
        exercise_id=library_exercise.id,
        reps=8,
        weight_kg=Decimal("80"),
        set_index=1,
        rpe=8.5,
    )
    await _add(db, child, instance, workout_set)
    reminder = Reminder(
        user_id=user_id,
        kind=ReminderKind.TASK,
        task_id=child.id,
        title="Напомнить",
        remind_at=local(DAY, 9),
        dedupe_key=f"task:{child.id}:{DAY}",
    )
    await _add(
        db,
        reminder,
        FoodFavorite(user_id=user_id, food_id=custom_food.id),
        RecipeItem(
            user_id=user_id,
            recipe_id=recipe.id,
            food_id=library_food.id,
            grams=Decimal("200"),
            snapshot_name="Гречка",
            kcal=Decimal("686"),
        ),
        FoodRecent(user_id=user_id, food_id=library_food.id, last_used_at=local(DAY, 8), last_grams=Decimal("150")),
        FoodRecent(user_id=user_id, recipe_id=recipe.id, last_used_at=local(DAY, 8)),
        SavedMealTemplate(
            user_id=user_id, name="Обычный завтрак", meal_type=MealType.BREAKFAST, items=[{"food_id": "x", "g": 1}]
        ),
        MealItem(
            user_id=user_id,
            meal_log_id=meal.id,
            food_id=library_food.id,
            name="Гречка",
            grams=Decimal("150"),
            kcal=Decimal("514.5"),
            item_source=ItemSource.FOOD,
            match_method=MatchMethod.FTS,
            match_score=0.875,
        ),
        MealItem(
            user_id=user_id,
            meal_log_id=meal.id,
            food_id=custom_food.id,
            name="Сырники",
            grams=Decimal("200"),
            kcal=Decimal("441"),
            item_source=ItemSource.FOOD,
            match_method=MatchMethod.MANUAL,
        ),
        WaterLog(user_id=user_id, logged_at=local(DAY, 10), local_day=DAY, amount_ml=2500),
        WorkoutTemplateItem(user_id=user_id, template_id=template.id, exercise_id=library_exercise.id),
        WorkoutTemplateItem(user_id=user_id, template_id=template.id, exercise_id=custom_exercise.id, sort_order=1),
        WeeklyPlan(user_id=user_id, weekday=0, template_id=template.id, title="Грудь", planned_time=time(18, 30)),
        WeeklyPlan(user_id=user_id, plan_date=DAY + timedelta(days=3), is_rest_day=True),
        PersonalRecord(
            user_id=user_id,
            exercise_id=library_exercise.id,
            kind="weight",
            value=Decimal("80"),
            unit="kg",
            achieved_at=local(DAY, 19),
            workout_log_id=workout.id,
            workout_set_id=workout_set.id,
        ),
        BodyMetric(user_id=user_id, measured_on=DAY, weight_kg=Decimal("80.4"), body_fat_pct=Decimal("18.5")),
        SleepLog(
            user_id=user_id,
            local_day=DAY,
            bed_at=local(DAY - timedelta(days=1), 23, 30),
            wake_at=local(DAY, 7, 15),
            duration_min=465,
            quality=4,
        ),
        HealthDaily(
            user_id=user_id,
            local_day=DAY,
            steps=9500,
            active_kcal=Decimal("420.5"),
            source=HealthSource.SHORTCUT,
            raw_payload={"steps": "9 500"},
            ingested_at=local(DAY, 21),
        ),
        Resource(user_id=user_id, name="Программа", url="https://example.com/plan", area_id=area.id),
        TimeEntry(
            user_id=user_id,
            task_id=parent.id,
            project_id=project.id,
            area_id=area.id,
            started_at=local(DAY, 9),
            ended_at=local(DAY, 9, 45),
            duration_s=2700,
            local_day=DAY,
        ),
        JournalEntry(user_id=user_id, entry_date=DAY, note_md="Хороший день; «кавычки», запятые", rating=4),
    )
    await db.execute(sa.insert(task_tags).values(task_id=parent.id, tag_id=tag_row.id, user_id=user_id))
    await db.commit()
    out.update(meal=meal.id, task=parent.id, reminder=reminder.id, workout=workout.id)
    return out


async def account_records(db, user_id: uuid.UUID, device_id: uuid.UUID | None) -> None:
    """The account's own records: secrets live here."""
    user = await db.get(User, user_id)
    user.recovery_phrase_hash = SECRETS["recovery"]
    subscription = PushSubscription(
        user_id=user_id,
        device_id=device_id,
        endpoint=SECRETS["endpoint"],
        p256dh=SECRETS["p256dh"],
        auth=SECRETS["auth"],
    )
    await _add(db, subscription)
    reminder_id = (await db.scalars(select(Reminder.id).where(Reminder.user_id == user_id))).first()
    await _add(
        db,
        NotificationLog(
            user_id=user_id,
            device_id=device_id,
            subscription_id=subscription.id,
            reminder_id=reminder_id,
            kind="task",
            title="Напоминание",
            dedupe_key=f"n:{uuid.uuid4()}",
            status="sent",
        ),
        AiJob(
            user_id=user_id,
            kind=AiJobKind.ANALYZE_MEAL_PHOTO,
            status=AiJobStatus.SUCCEEDED,
            input_text="тарелка гречки",
            input_thumb_path=SECRETS["thumb"],
            request_payload={"image": SECRETS["ai_request"]},
            response_payload={"raw": SECRETS["ai_response"]},
            result={"items": [{"name": "гречка", "grams": 150}]},
            cost_usd=Decimal("0.001234"),
        ),
        AiUsageDaily(user_id=user_id, local_day=DAY, calls=1, cost_usd=Decimal("0.001234")),
    )


async def gamify(db, user_id: uuid.UUID) -> None:
    """XP, streaks, scores and quests from the engine; one achievement for sure."""
    await run(db, user_id, NOON)
    if not (await db.scalars(select(UserAchievement.id).where(UserAchievement.user_id == user_id))).first():
        achievement = (await db.scalars(select(Achievement).order_by(Achievement.code))).first()
        await _add(db, UserAchievement(user_id=user_id, achievement_id=achievement.id, unlocked_at=local(DAY, 20)))


@pytest.fixture
async def account(authed: httpx.AsyncClient, db, device_token: str) -> dict[str, Any]:
    user_id = await owner_id(db)
    await configure(db, user_id)
    ids = await populate(db, user_id)
    device_id = (await db.scalars(select(Device.id).where(Device.user_id == user_id))).first()
    await account_records(db, user_id, device_id)
    created = await authed.post("/api/v1/tokens", json={"name": "Shortcut", "scopes": ["ingest:health"]})
    assert created.status_code == 200, created.text
    from app.services.pairing import create_pairing_code

    issued = await create_pairing_code(db, await db.get(User, user_id), created_by="test")
    await db.commit()
    await gamify(db, user_id)
    db.expire_all()
    secrets = dict(SECRETS)
    secrets["device_token"] = device_token
    secrets["api_token"] = created.json()["token"]
    secrets["pairing_code"] = issued.code
    for model, prefix, columns in (
        (Device, "device", ("token_hash", "token_prefix", "fingerprint_hash")),
        (ApiToken, "api", ("token_hash", "token_prefix")),
    ):
        for column in columns:
            value = await db.scalar(select(getattr(model, column)).where(model.user_id == user_id))
            if value:
                secrets[f"{prefix}_{column}"] = value
    secrets["code_hmac"] = (
        await db.scalars(select(PairingCode.code_hmac).where(PairingCode.user_id == user_id))
    ).first()
    return {"client": authed, "user_id": user_id, "ids": ids, "secrets": secrets}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def download(client: httpx.AsyncClient, kind: str) -> httpx.Response:
    response = await client.get(f"{E}/{kind}")
    assert response.status_code == 200, response.text
    return response


def local_today(tz: str = "Asia/Bangkok") -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz)).date().isoformat()


def assert_no_secret(text: str, secrets: dict[str, str]) -> None:
    for name, value in secrets.items():
        assert value and value not in text, f"secret {name} leaked"
    for column in ("token_hash", "token_prefix", "fingerprint_hash", "recovery_phrase_hash", "code_hmac"):
        assert f'"{column}"' not in text and f",{column}," not in text, column
    for column in ("p256dh", "request_payload", "response_payload", "input_thumb_path", "source_payload"):
        assert column not in text, column


def zip_of(response: httpx.Response) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None  # every member's CRC checks out
    return archive


def section_rows(document: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return document["tables"][name]


async def asgi_get(path: str, token: str) -> tuple[int, dict[bytes, bytes], list[bytes]]:
    """Call the app as a server would and keep every ``http.response.body`` message
    (httpx's ASGI transport joins them, which would hide the streaming)."""
    from app.main import app

    messages: list[dict[str, Any]] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    requested = asyncio.Event()

    async def receive() -> dict[str, Any]:
        if not requested.is_set():
            requested.set()
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # like a server: nothing more until the client goes away
        raise AssertionError("unreachable")

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    chunks = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
    return start["status"], dict(start["headers"]), [chunk for chunk in chunks if chunk]


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
async def test_json_export_has_every_section_and_no_secret(account) -> None:
    client, user_id, secrets = account["client"], account["user_id"], account["secrets"]
    response = await download(client, "json")
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == f'attachment; filename="disciplineos-{local_today()}.json"'
    text = response.content.decode("utf-8")
    document = orjson.loads(response.content)
    assert document["format"] == "disciplineos-export" and document["format_version"] == 1
    assert document["user_id"] == str(user_id) and document["timezone"] == "Asia/Bangkok"
    assert document["schema_version"] == "0003"  # the alembic head
    assert list(document["tables"]) == [section.name for section in SECTIONS]
    assert list(document["library"]) == [item.name for item in LIBRARY]
    for section in SECTIONS:
        rows = section_rows(document, section.name)
        assert rows, f"{section.name} is empty"
        assert document["counts"][section.name] == len(rows)
        assert all(set(row) == {column.name for column in section.columns} for row in rows), section.name
        owner = section.owner
        assert all(row[owner] == str(user_id) for row in rows), section.name
    for item in LIBRARY:
        assert document["library"][item.name], item.name
        assert document["counts"][f"library.{item.name}"] == len(document["library"][item.name])
    assert_no_secret(text, secrets)

    # values: ISO instants in UTC, dates, decimals as numbers, enums as values, JSON as JSON
    meal = next(row for row in section_rows(document, "meal_logs") if row["id"] == str(account["ids"]["meal"]))
    assert meal["kcal"] == 2050.25 and meal["local_day"] == DAY.isoformat() and meal["meal_type"] == "breakfast"
    assert meal["logged_at"] == local(DAY, 8).isoformat()  # 2026-07-14T01:00:00+00:00
    settings = section_rows(document, "settings")[0]
    assert (settings["target_kcal"], settings["quiet_hours_start"], settings["weight_kg"]) == (2000, "23:00:00", 80.0)
    food = next(row for row in section_rows(document, "foods") if row["id"] == str(account["ids"]["custom_food"]))
    assert food["is_global"] is False and food["kcal_per_100g"] == 220.5
    library_food = next(row for row in document["library"]["foods"] if row["id"] == str(account["ids"]["library_food"]))
    assert library_food["is_global"] is True and library_food["aliases"] == ["гречка", "ядрица"]
    assert {row["kind"] for row in section_rows(document, "streaks")} == {kind.value for kind in StreakKind}
    task = next(row for row in section_rows(document, "tasks") if row["id"] == str(account["ids"]["task"]))
    assert task["title"] == FORMULA_TITLE  # JSON keeps text as it is
    assert "search_vector_ru" not in task and "aliases_text" not in food


async def test_the_export_holds_only_your_rows(account, db) -> None:
    async with other_user() as stranger:
        other = (await db.scalars(select(User.id).where(User.is_owner.is_(False)))).one()
        await populate(db, other, tag=" чужое")
        mine = (await download(account["client"], "json")).content.decode()
        assert " чужое" not in mine and str(other) not in mine
        theirs = orjson.loads((await download(stranger, "json")).content)
        assert theirs["user_id"] == str(other)
        assert all(
            row[section.owner] == str(other) for section in SECTIONS for row in section_rows(theirs, section.name)
        )
        assert str(account["user_id"]) not in str(theirs["tables"])
        assert section_rows(theirs, "meal_logs")[0]["title"] == "Завтрак чужое"


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
async def test_csv_zip_one_file_per_table_with_bom_headers_and_safe_cells(account) -> None:
    client, secrets = account["client"], account["secrets"]
    response = await download(client, "csv")
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="disciplineos-{local_today()}.zip"'
    archive = zip_of(response)
    expected = [f"{section.name}.csv" for section in SECTIONS] + [f"library_{item.name}.csv" for item in LIBRARY]
    assert archive.namelist() == [*expected, "README.txt"]
    document = orjson.loads((await download(client, "json")).content)
    everything = ""
    for section in SECTIONS:
        raw = archive.read(f"{section.name}.csv")
        assert raw.startswith(b"\xef\xbb\xbf"), section.name  # UTF-8 BOM: Excel opens it as UTF-8
        text = raw.decode("utf-8-sig")
        everything += text
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == [column.name for column in section.columns], section.name
        assert len(rows) - 1 == len(section_rows(document, section.name)), section.name
        assert rows[0][0] in ("id", "task_id") and ";" not in rows[0][0]
    for item in LIBRARY:
        rows = list(csv.reader(io.StringIO(archive.read(f"library_{item.name}.csv").decode("utf-8-sig"))))
        assert rows[0] == [column.name for column in item.selected()] and len(rows) - 1 == len(
            document["library"][item.name]
        )
    assert_no_secret(everything, secrets)

    tasks = list(csv.DictReader(io.StringIO(archive.read("tasks.csv").decode("utf-8-sig"))))
    risky = next(row for row in tasks if row["id"] == str(account["ids"]["task"]))
    assert risky["title"] == "'" + FORMULA_TITLE  # never runs as a formula in a spreadsheet
    assert risky["completed_at"] == local(DAY, 10).isoformat() and risky["priority"] == "1"
    meals = list(csv.DictReader(io.StringIO(archive.read("meal_logs.csv").decode("utf-8-sig"))))
    meal = next(row for row in meals if row["id"] == str(account["ids"]["meal"]))
    assert (meal["kcal"], meal["local_day"], meal["is_deleted"], meal["notes"]) == (
        "2050.25",
        "2026-07-14",
        "false",
        "",
    )
    journal = list(csv.DictReader(io.StringIO(archive.read("journal_entries.csv").decode("utf-8-sig"))))
    assert journal[0]["note_md"] == "Хороший день; «кавычки», запятые"  # quoted, Cyrillic intact
    settings = next(csv.DictReader(io.StringIO(archive.read("settings.csv").decode("utf-8-sig"))))
    assert orjson.loads(settings["notification_prefs"]) == section_rows(document, "settings")[0]["notification_prefs"]
    readme = archive.read("README.txt").decode()
    assert "UTF-8" in readme and "tasks.csv" in readme and str(account["user_id"]) in readme
    assert readme.startswith("DisciplineOS — выгрузка данных (CSV)")  # the app's language: ru by default
    assert (await client.patch("/api/v1/settings", json={"language": "en"})).status_code == 200
    english = zip_of(await download(client, "csv")).read("README.txt").decode()
    assert english.startswith("DisciplineOS — data export (CSV)") and "Files and rows:" in english


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #
async def test_backup_zip_manifest_counts_and_checksums(account) -> None:
    client, user_id = account["client"], account["user_id"]
    before = datetime.now(UTC)
    response = await download(client, "backup")
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="disciplineos-{local_today()}.zip"'
    archive = zip_of(response)
    assert archive.namelist() == ["data.json", "manifest.json"]
    manifest = orjson.loads(archive.read("manifest.json"))
    data = archive.read("data.json")
    document = orjson.loads(data)
    assert manifest["format"] == "disciplineos-backup" and manifest["format_version"] == 1
    assert manifest["user_id"] == str(user_id) and manifest["schema_version"] == "0003"
    assert manifest["app_version"] and manifest["timezone"] == "Asia/Bangkok"
    created = datetime.fromisoformat(manifest["created_at"])
    assert created.tzinfo is not None and before - timedelta(seconds=5) <= created <= datetime.now(UTC)
    assert manifest["files"] == {"data.json": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}}
    assert manifest["counts"] == document["counts"]
    assert all(manifest["counts"][section.name] >= 1 for section in SECTIONS)
    assert document["format"] == "disciplineos-export" and document["user_id"] == str(user_id)
    assert_no_secret(data.decode(), account["secrets"])


# --------------------------------------------------------------------------- #
# Access: device only, 6 per minute
# --------------------------------------------------------------------------- #
async def test_device_only_and_six_exports_per_minute(account, client) -> None:
    authed, secrets = account["client"], account["secrets"]
    token = {"Authorization": f"Bearer {secrets['api_token']}"}
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as anonymous:
        for kind in ("json", "csv", "backup"):
            refused = await anonymous.get(f"{E}/{kind}", headers=token)
            assert (refused.status_code, refused.json()["error"]["code"]) == (403, "device_only"), kind
            assert (await anonymous.get(f"{E}/{kind}")).status_code == 401
    for kind in ("json", "csv", "backup", "json", "csv", "backup"):  # API-token refusals never counted
        assert (await authed.get(f"{E}/{kind}")).status_code == 200, kind
    limited = await authed.get(f"{E}/json")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.json()["error"]["details"]["retry_after"]) >= 1
    async with other_user() as stranger:  # per user
        assert (await stranger.get(f"{E}/backup")).status_code == 200


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
async def test_exports_stream_in_chunks(account, db, monkeypatch) -> None:
    """Rows are handed over as they are read: with a small flush size the body arrives
    in many chunks, and the zip writer never keeps more than the current chunk."""
    user_id = account["user_id"]
    values = [
        {
            "id": uuid.uuid4(),
            "client_uuid": uuid.uuid4(),
            "user_id": user_id,
            "logged_at": local(DAY - timedelta(days=index % 300), 9),
            "local_day": DAY - timedelta(days=index % 300),
            "amount_ml": 250 + index,
        }
        for index in range(3000)
    ]
    await db.execute(sa.insert(WaterLog.__table__), values)
    await db.commit()
    monkeypatch.setattr(export, "FLUSH_BYTES", 16 * 1024)
    monkeypatch.setattr(export, "BATCH", 200)
    for kind in ("json", "csv", "backup"):
        status, headers, chunks = await asgi_get(f"{E}/{kind}", account["secrets"]["device_token"])
        assert status == 200 and b"content-length" not in headers, kind  # streamed: no length up front
        sizes = [len(chunk) for chunk in chunks]
        assert len(sizes) > 10, (kind, len(sizes))
        assert max(sizes) < 256 * 1024, kind  # no chunk holds the whole export
        body = b"".join(chunks)
        if kind == "json":
            assert len(orjson.loads(body)["tables"]["water_logs"]) == 3001
        else:
            assert zipfile.ZipFile(io.BytesIO(body)).testzip() is None

    sink = export.ZipStream(datetime.now(UTC))
    sink.open("big.txt")
    produced = 0
    for _ in range(200):
        produced += len(sink.write(os.urandom(64 * 1024)))  # incompressible: bytes come out as they go in
        assert len(sink._sink.drain()) == 0  # nothing waits in the sink between writes
    produced += len(sink.close_entry()) + len(sink.finish())
    assert produced > 200 * 64 * 1024


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #
async def save_backup(client: httpx.AsyncClient, path) -> Path:
    path.write_bytes((await download(client, "backup")).content)
    return path


async def wipe(db, user_id: uuid.UUID) -> None:
    """Delete every restorable row of the account (the user, devices and logs stay)."""
    for section in reversed(RESTORED):
        table = section.table
        stmt = sa.delete(table).where(table.c[section.owner] == user_id)
        if section.where is not None:
            stmt = stmt.where(section.where(table))
        await db.execute(stmt)
    await db.commit()


async def counts(db, user_id: uuid.UUID) -> dict[str, int]:
    out = {}
    for section in RESTORED:
        table = section.table
        stmt = select(func.count()).select_from(table).where(table.c[section.owner] == user_id)
        if section.where is not None:
            stmt = stmt.where(section.where(table))
        out[section.name] = int(await db.scalar(stmt))
    await db.commit()
    return out


async def restore_file(path: Path, **options: Any):
    """What the CLI does: one session, commit — or roll back for a dry run."""
    from app.db import SessionLocal

    backup = read_backup(path)
    async with SessionLocal() as session:
        try:
            report = await restore(session, backup, **options)
        except BaseException:
            await session.rollback()
            raise
        if options.get("dry_run"):
            await session.rollback()
        else:
            await session.commit()
    return report


def rewrite_backup(
    source: Path,
    target: Path,
    mutate: Any = None,
    *,
    data: bytes | None = None,
    checksum: bool = True,
) -> Path:
    """A copy of a backup with ``mutate(data, manifest)`` applied (checksum updated
    unless ``checksum=False``), or with ``data.json`` replaced by raw bytes."""
    archive = zipfile.ZipFile(source)
    manifest = orjson.loads(archive.read("manifest.json"))
    document = orjson.loads(archive.read("data.json"))
    if mutate is not None:
        mutate(document, manifest)
    payload = data if data is not None else orjson.dumps(document)
    if checksum:
        manifest["files"]["data.json"] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("data.json", payload)
        out.writestr("manifest.json", orjson.dumps(manifest))
    return target


def restorable_tables(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {section.name: document["tables"][section.name] for section in RESTORED}


async def test_restore_round_trip_and_a_second_restore_changes_nothing(account, db, tmp_path) -> None:
    client, user_id = account["client"], account["user_id"]
    path = await save_backup(client, tmp_path / "backup.zip")
    before = orjson.loads((await download(client, "json")).content)
    expected = await counts(db, user_id)
    total = sum(expected.values())
    await wipe(db, user_id)
    assert sum((await counts(db, user_id)).values()) == 0

    dry = await restore_file(path, dry_run=True)  # reports, writes nothing
    assert (dry.dry_run, dry.inserted, dry.updated, dry.skipped) == (True, total, 0, 0)
    assert sum((await counts(db, user_id)).values()) == 0

    report = await restore_file(path)
    assert (report.source_user_id, report.target_user_id, report.schema_version) == (user_id, user_id, "0003")
    assert (report.inserted, report.updated, report.unchanged, report.skipped) == (total, 0, 0, 0)
    assert {name: item.rows for name, item in report.tables.items()} == expected
    assert await counts(db, user_id) == expected
    assert all(item.same_id == item.referenced for item in report.library.values())

    # identical rows: the export after the restore is the export before the wipe
    after = orjson.loads((await download(client, "json")).content)
    assert restorable_tables(after) == restorable_tables(before)
    assert after["library"] == before["library"]

    # spot checks through the ORM
    db.expire_all()
    meal = await db.get(MealLog, account["ids"]["meal"])
    assert (meal.kcal, meal.local_day, meal.title, meal.user_id) == (Decimal("2050.25"), DAY, "Завтрак", user_id)
    task = await db.get(Task, account["ids"]["task"])
    assert (task.title, task.status, task.completed_at) == (FORMULA_TITLE, TaskStatus.DONE, local(DAY, 10))
    assert task.search_vector_ru is not None  # rebuilt by the trigger
    settings = (await db.scalars(select(UserSettings).where(UserSettings.user_id == user_id))).one()
    assert (settings.target_kcal, settings.timezone, settings.quiet_hours_start) == (2000, "Asia/Bangkok", time(23))
    food = await db.get(Food, account["ids"]["custom_food"])
    assert food.aliases_text == "" and food.source_payload == {}  # the raw payload never left
    tags = (await db.execute(select(task_tags.c.task_id, task_tags.c.tag_id))).all()
    assert [row.task_id for row in tags] == [account["ids"]["task"]]
    assert (await db.scalar(select(func.count()).select_from(Streak).where(Streak.user_id == user_id))) == 7

    again = await restore_file(path)  # idempotent: nothing changes the second time
    assert (again.inserted, again.updated, again.unchanged, again.skipped) == (0, 0, total, 0)
    assert await counts(db, user_id) == expected
    assert restorable_tables(orjson.loads((await download(client, "json")).content)) == restorable_tables(before)


async def test_restore_over_changed_rows_puts_the_backup_values_back(account, db, tmp_path) -> None:
    client, user_id = account["client"], account["user_id"]
    path = await save_backup(client, tmp_path / "backup.zip")
    meal_id = account["ids"]["meal"]
    await db.execute(sa.update(MealLog).where(MealLog.id == meal_id).values(kcal=Decimal("1"), title="изменено"))
    later = Task(user_id=user_id, title="после бэкапа")  # newer than the backup: kept (upserts, not a replace)
    await _add(db, later)
    later_id = later.id
    report = await restore_file(path)
    assert (report.tables["meal_logs"].updated, report.tables["meal_logs"].inserted) == (1, 0)
    assert report.updated == 1 and report.inserted == 0
    db.expire_all()
    meal = await db.get(MealLog, meal_id)
    assert (meal.kcal, meal.title) == (Decimal("2050.25"), "Завтрак")
    assert await db.get(Task, later_id) is not None


async def test_restore_into_an_empty_account_on_another_server(account, db, tmp_path) -> None:
    """The source account is gone (another server); a new owner — with only what a new
    account starts with — takes the backup. A library food this server lacks becomes
    the user's own, an exercise is matched by name, an achievement and a quest by code,
    and the rows of a quest this server does not know at all are skipped."""
    client, source = account["client"], account["user_id"]
    original = await save_backup(client, tmp_path / "backup.zip")
    expected = await counts(db, source)
    document = orjson.loads(zipfile.ZipFile(original).read("data.json"))
    achievement_id = uuid.UUID(document["library"]["achievements"][0]["id"])
    quest_ids = sorted({row["quest_id"] for row in document["tables"]["user_quests"]})
    reseeded, retired = uuid.UUID(quest_ids[0]), quest_ids[1]
    retired_rows = sum(1 for row in document["tables"]["user_quests"] if row["quest_id"] == retired)
    unknown = str(uuid.uuid4())

    def retire(data: dict[str, Any], manifest: dict[str, Any]) -> None:  # a quest this server never had
        next(row for row in data["library"]["quests"] if row["id"] == retired).update(id=unknown, code="retired")
        for row in data["tables"]["user_quests"]:
            row["quest_id"] = unknown if row["quest_id"] == retired else row["quest_id"]

    path = rewrite_backup(original, tmp_path / "other-server.zip", retire)

    await db.execute(sa.delete(User).where(User.id == source))
    await db.execute(sa.delete(Food).where(Food.id == account["ids"]["library_food"]))
    old_exercise = await db.get(Exercise, account["ids"]["library_exercise"])
    name_en = old_exercise.name_en
    await db.execute(sa.delete(Exercise).where(Exercise.id == old_exercise.id))
    twin = Exercise(is_global=True, name_en=name_en, muscle_group=MuscleGroup.CHEST, equipment=Equipment.BARBELL)
    new_achievement_id = uuid.uuid4()
    await db.execute(sa.update(Achievement).where(Achievement.id == achievement_id).values(id=new_achievement_id))
    await db.execute(sa.text("DELETE FROM quests WHERE id = :id"), {"id": reseeded})  # comes back under a new id
    await _add(db, twin)
    twin_id = twin.id
    from app.services.bootstrap import create_owner

    target = (await create_owner(db, display_name="Новый сервер")).user.id
    await db.commit()
    fresh = await counts(db, target)
    assert {name for name, count in fresh.items() if count} == {"settings", "areas", "tags", "streaks"}

    with pytest.raises(RestoreError, match="does not exist here"):
        await restore_file(path)  # the backup's own account is not on this server
    report = await restore_file(path, user_id=target)
    assert (report.source_user_id, report.target_user_id) == (source, target)
    library = report.library
    assert (library["foods"].copied, library["exercises"].matched, library["achievements"].matched) == (1, 1, 1)
    assert (library["quests"].matched, library["quests"].missing) == (1, 1)
    assert report.tables["user_quests"].skipped == retired_rows
    restored = await counts(db, target)
    expected["foods"] += 1  # the library food that is no longer here is now the user's own
    expected["user_quests"] -= retired_rows
    assert restored == expected
    assert report.tables["areas"].merged == 7 and report.tables["streaks"].merged == 7  # the new account's own
    assert report.tables["settings"].merged == 1 and report.tables["tags"].merged == 6

    db.expire_all()
    copy = await db.get(Food, account["ids"]["library_food"])
    assert (copy.user_id, copy.is_global, copy.name_ru) == (target, False, "Гречка библиотечная")
    items = (await db.scalars(select(MealItem).where(MealItem.user_id == target))).all()
    assert account["ids"]["library_food"] in {item.food_id for item in items}
    sets = (await db.scalars(select(WorkoutSet).where(WorkoutSet.user_id == target))).all()
    assert {row.exercise_id for row in sets} == {twin_id}  # pointed at the exercise found by name
    achieved = (await db.scalars(select(UserAchievement).where(UserAchievement.user_id == target))).all()
    assert new_achievement_id in {row.achievement_id for row in achieved}
    projects = (await db.scalars(select(Project).where(Project.user_id == target))).all()
    areas = {area.id: area.name for area in (await db.scalars(select(Area).where(Area.user_id == target))).all()}
    assert all(project.area_id in areas for project in projects if project.area_id)
    assert len(areas) == len(document["tables"]["areas"])  # the defaults were merged, not duplicated
    settings = (await db.scalars(select(UserSettings).where(UserSettings.user_id == target))).one()
    assert (settings.target_kcal, settings.timezone) == (2000, "Asia/Bangkok")  # the backup's values

    again = await restore_file(path, user_id=target)
    assert (again.inserted, again.updated) == (0, 0)
    assert await counts(db, target) == restored


async def test_restore_never_touches_another_users_rows(account, db, tmp_path) -> None:
    client, source = account["client"], account["user_id"]
    path = await save_backup(client, tmp_path / "backup.zip")
    async with other_user():
        other = (await db.scalars(select(User.id).where(User.is_owner.is_(False)))).one()
    await db.commit()
    before, other_before = await counts(db, source), await counts(db, other)
    # an empty account, but the backup's rows still belong to their owner on this server
    with pytest.raises(RestoreError, match="belongs to another account"):
        await restore_file(path, user_id=other)
    assert await counts(db, other) == other_before and await counts(db, source) == before

    # an account with data of its own refuses a foreign backup
    await _add(db, MealLog(user_id=other, logged_at=local(DAY, 8), local_day=DAY, kcal=Decimal(500), item_count=1))
    with pytest.raises(RestoreError, match="is not empty"):
        await restore_file(path, user_id=other)
    with pytest.raises(RestoreError, match="does not exist here"):
        await restore_file(path, user_id=uuid.uuid4())


async def test_damaged_or_foreign_backups_are_refused_and_nothing_is_written(account, db, tmp_path) -> None:
    client, user_id = account["client"], account["user_id"]
    original = await save_backup(client, tmp_path / "backup.zip")
    await wipe(db, user_id)

    def refused(path: Path, match: str) -> None:
        with pytest.raises(RestoreError, match=match):
            read_backup(path)

    refused(tmp_path / "missing.zip", "no such file")
    (tmp_path / "text.zip").write_text("not a zip")
    refused(tmp_path / "text.zip", "not a readable zip")
    with zipfile.ZipFile(tmp_path / "other.zip", "w") as other:
        other.writestr("data.json", "{}")
    refused(tmp_path / "other.zip", "no manifest.json")
    data = zipfile.ZipFile(original).read("data.json")
    damaged = data.replace(b'"Hello"', b'"Hellp"') if b'"Hello"' in data else data[:-3] + b" }\n"
    refused(rewrite_backup(original, tmp_path / "damaged.zip", data=damaged, checksum=False), "checksum mismatch")

    def mutate(path: str, change) -> Path:
        return rewrite_backup(original, tmp_path / path, change)

    refused(mutate("format.zip", lambda d, m: m.update(format="something-else")), "unknown format")
    refused(mutate("version.zip", lambda d, m: m.update(format_version=2)), "unsupported format version")
    refused(mutate("accounts.zip", lambda d, m: d.update(user_id=str(uuid.uuid4()))), "different accounts")
    refused(
        mutate("counts.zip", lambda d, m: m["counts"].update(meal_logs=m["counts"]["meal_logs"] + 1)),
        "the manifest says",
    )
    refused(mutate("table.zip", lambda d, m: d["tables"].pop("tasks")), "tasks is missing")

    # consistent files, but not restorable here: nothing written, whatever fails and wherever
    schema = mutate("schema.zip", lambda d, m: (d.update(schema_version="0002"), m.update(schema_version="0002")))
    with pytest.raises(RestoreError, match="schema version 0002"):
        await restore_file(schema)

    def bad_value(data: dict[str, Any], manifest: dict[str, Any]) -> None:
        data["tables"]["journal_entries"][0]["rating"] = "five"  # late in the order: earlier tables went in

    with pytest.raises(RestoreError, match=r"journal_entries.rating: bad value 'five'"):
        await restore_file(mutate("value.zip", bad_value))

    def unknown_column(data: dict[str, Any], manifest: dict[str, Any]) -> None:
        data["tables"]["water_logs"][0]["temperature_c"] = 4

    with pytest.raises(RestoreError, match=r"water_logs: a row does not match this schema \(missing \[\], unknown"):
        await restore_file(mutate("column.zip", unknown_column))

    def broken_reference(data: dict[str, Any], manifest: dict[str, Any]) -> None:
        data["tables"]["meal_items"][0]["meal_log_id"] = str(uuid.uuid4())  # the database refuses it

    with pytest.raises(RestoreError, match="meal_items: the database refused the rows"):
        await restore_file(mutate("reference.zip", broken_reference))
    assert sum((await counts(db, user_id)).values()) == 0

    assert (await restore_file(original)).inserted > 0  # the untouched backup still restores


# --------------------------------------------------------------------------- #
# The CLI, as a real process
# --------------------------------------------------------------------------- #
def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own interpreter and module
        [sys.executable, "-m", "app.cli", "restore-backup", *args],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "NO_COLOR": "1"},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


async def test_cli_restore_backup(account, db, tmp_path) -> None:
    client, user_id = account["client"], account["user_id"]
    path = await save_backup(client, tmp_path / "disciplineos-2026-10-01.zip")
    expected = await counts(db, user_id)
    total = sum(expected.values())
    await wipe(db, user_id)

    missing = cli(str(tmp_path / "nope.zip"))
    assert missing.returncode == 1 and "no such file" in missing.stdout + missing.stderr
    bad_id = cli(str(path), "--user-id", "not-a-uuid")
    assert bad_id.returncode == 1 and "--user-id is not a UUID" in bad_id.stdout + bad_id.stderr

    dry = cli(str(path), "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "DRY RUN" in dry.stdout and f"would restore: {total} inserted, 0 updated" in dry.stdout
    assert f"account {user_id} (the backup's own)" in dry.stdout and "meal_logs" in dry.stdout
    assert sum((await counts(db, user_id)).values()) == 0

    real = cli(str(path))
    assert real.returncode == 0, real.stdout + real.stderr
    assert f"restored: {total} inserted, 0 updated, 0 unchanged, 0 skipped" in real.stdout
    assert await counts(db, user_id) == expected

    again = cli(str(path))
    assert again.returncode == 0 and f"restored: 0 inserted, 0 updated, {total} unchanged" in again.stdout
