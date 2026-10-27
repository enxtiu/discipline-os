"""Stage 9 — the optional one-way Notion mirror, against a fake Notion (no network).

Every request goes to :class:`tests.stage9_notion_helpers.FakeNotion` through an
``httpx.MockTransport``; the client's clock is a :class:`FakeClock`, so pacing and
``Retry-After`` are asserted without waiting.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text

from app.models import (
    Area,
    Integration,
    IntegrationLink,
    JournalEntry,
    Project,
    ProjectStatus,
    Tag,
    Task,
    TaskStatus,
    task_tags,
)
from tests.stage6_helpers import other_user
from tests.stage8_helpers import (
    NOON,
    TODAY,
    YESTERDAY,
    configure,
    local,
    meal,
    owner_id,
    sleep,
    steps,
    tracked,
    workout,
)
from tests.stage9_notion_helpers import FakeClock, FakeNotion

API = "/api/v1/integrations"
N = f"{API}/notion"
SECRET = "ntn_" + "S3cr3tValue" * 4  # 48 characters, printable, no spaces
OTHER_SECRET = "ntn_" + "AnotherOne99" * 4
ALL = ["areas", "projects", "tasks", "journal", "days"]
TITLES = {"areas": "Области", "projects": "Проекты", "tasks": "Задачи", "journal": "Дневник", "days": "Итоги дня"}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def clock():
    from app.services.notion import client as notion_client

    fake_clock = FakeClock()
    notion_client._clock_override = fake_clock
    yield fake_clock
    notion_client._clock_override = None


@pytest.fixture
def fake(clock):
    from app.services.notion import client as notion_client

    notion = FakeNotion(clock)
    notion.add_token(SECRET)
    notion_client._transport_override = notion.transport()
    yield notion
    notion_client._transport_override = None


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    """Runs see 15 July 2026, 12:00 in Bangkok (the stage-8 helpers' NOON)."""
    from app.services.notion import sync

    monkeypatch.setattr(sync, "now_fn", lambda: NOON)


@pytest.fixture
async def user_id(authed, db) -> uuid.UUID:
    uid = await owner_id(db)
    await configure(db, uid)
    return uid


def page_link(page_id: str) -> str:
    return f"https://www.notion.so/myspace/DisciplineOS-{page_id.replace('-', '')}?pvs=4"


async def connect(client: httpx.AsyncClient, fake: FakeNotion, *, secret: str = SECRET) -> dict[str, Any]:
    page_id = fake.add_page("DisciplineOS", shared_with=(secret,))
    response = await client.post(f"{N}/connect", json={"secret": secret, "page": page_link(page_id)})
    assert response.status_code == 201, response.text
    return response.json()


async def setup_all(client: httpx.AsyncClient, kinds: list[str] | None = None) -> dict[str, Any]:
    response = await client.post(f"{N}/setup", json={"kinds": kinds or ALL})
    assert response.status_code == 200, response.text
    return response.json()


async def sync_now(user_id: uuid.UUID, **kw: Any):
    from app.services.notion import sync

    return await sync.run_sync(user_id, **kw)


async def integration(db, user_id: uuid.UUID) -> Integration:
    db.expire_all()
    return (await db.scalars(select(Integration).where(Integration.user_id == user_id))).one()


async def links(db, user_id: uuid.UUID, entity_type: str | None = None) -> list[IntegrationLink]:
    db.expire_all()
    stmt = select(IntegrationLink).where(IntegrationLink.user_id == user_id)
    if entity_type:
        stmt = stmt.where(IntegrationLink.entity_type == entity_type)
    return list((await db.scalars(stmt)).all())


async def seed(db, user_id: uuid.UUID) -> dict[str, Any]:
    """An area, a project in it, a task with a subtask, tags, notes, a due time and a
    block, a done task, a journal entry and two days of data."""
    area = Area(user_id=user_id, name="Спорт", description="Тело и здоровье", weekly_target_minutes=300, sort_order=9)
    db.add(area)
    await db.flush()
    project = Project(
        user_id=user_id,
        name="Марафон",
        area_id=area.id,
        status=ProjectStatus.ACTIVE,
        outcome="Пробежать 42 км",
        deadline=date(2026, 10, 4),
    )
    db.add(project)
    await db.flush()
    task = Task(
        user_id=user_id,
        title="Купить кроссовки",
        status=TaskStatus.OPEN,
        priority=1,
        project_id=project.id,
        due_date=TODAY + timedelta(days=1),
        due_at=local(TODAY + timedelta(days=1), 18, 30),
        scheduled_start=local(TODAY, 14),
        scheduled_end=local(TODAY, 15, 30),
        estimate_minutes=45,
        notes_md="- [ ] примерить\n- [ ] сравнить цены",
        context="@город",
    )
    db.add(task)
    await db.flush()
    subtask = Task(user_id=user_id, title="Выбрать размер", status=TaskStatus.OPEN, priority=3, parent_id=task.id)
    done = Task(
        user_id=user_id,
        title="Записаться на забег",
        status=TaskStatus.DONE,
        priority=2,
        area_id=area.id,
        completed_at=local(TODAY, 9, 15),
    )
    db.add_all([subtask, done])
    await db.flush()
    tags = [Tag(user_id=user_id, name="бег"), Tag(user_id=user_id, name="покупки")]
    db.add_all(tags)
    await db.flush()
    await db.execute(
        task_tags.insert(),
        [{"task_id": task.id, "tag_id": tag.id, "user_id": user_id} for tag in tags],
    )
    entry = JournalEntry(
        user_id=user_id, entry_date=YESTERDAY, rating=4, mood=5, wins_md="Пробежал 10 км", tags=["бег", "a,b"]
    )
    db.add(entry)
    await db.commit()
    # two days of numbers (food, a workout with burned kcal, steps with active energy, sleep, time)
    for _ in range(3):
        await meal(db, user_id, TODAY, 2000 / 3, protein=50)
    await meal(db, user_id, YESTERDAY, 1800, protein=120)
    await workout(db, user_id, TODAY, kcal_burned=500)
    await steps(db, user_id, TODAY, 9500, active_kcal=320)
    await sleep(db, user_id, TODAY, 450)
    await tracked(db, user_id, TODAY, 95)
    return {
        "area": str(area.id),
        "project": str(project.id),
        "task": str(task.id),
        "subtask": str(subtask.id),
        "done": str(done.id),
        "journal": str(entry.id),
    }


# --------------------------------------------------------------------------- #
# Off until connected
# --------------------------------------------------------------------------- #
async def test_nothing_happens_until_the_owner_connects(authed, user_id, fake, db) -> None:
    from app.services.notion import sync

    listed = await authed.get(API)
    assert listed.status_code == 200
    assert listed.json()["items"] == [
        {"provider": "notion", "status": "disconnected", "connected": False, "last_sync_at": None, "error": None}
    ]
    card = (await authed.get(N)).json()
    assert card["connected"] is False and card["secret_set"] is False and card["page"] is None
    assert card["notion_version"] == "2026-03-11" and card["interval_minutes"] == 10
    assert [kind["database"] for kind in card["kinds"]] == ["none"] * 5
    await sync.sync_job()
    assert fake.requests == []  # the job never talks to Notion without a connection
    assert (await sync_now(user_id)).result == "skipped"
    assert fake.requests == []
    for path, method in ((f"{N}/sync", "post"), (f"{N}/setup", "post")):
        body = {"kinds": ["tasks"]} if path.endswith("setup") else None
        response = await getattr(authed, method)(path, json=body) if body else await authed.post(path)
        assert response.status_code == 409 and response.json()["error"]["code"] == "notion_not_connected"
    assert (await authed.patch(N, json={"kinds": ["tasks"]})).json()["error"]["code"] == "notion_not_connected"


async def test_the_job_is_registered_every_ten_minutes() -> None:
    from app.services.hooks import STAGE_JOBS

    spec = next(spec for spec in STAGE_JOBS if spec.id == "notion_sync")
    assert spec.minutes == 10 and spec.cron_hour is None


# --------------------------------------------------------------------------- #
# Connect
# --------------------------------------------------------------------------- #
async def test_connect_checks_secret_and_page_and_never_returns_the_secret(authed, user_id, fake, db) -> None:
    card = await connect(authed, fake)
    assert card["connected"] is True and card["status"] == "connected" and card["secret_set"] is True
    assert card["bot_name"] == "DisciplineOS" and card["workspace_name"] == "Мой Notion"
    assert card["page"]["title"] == "DisciplineOS" and card["page"]["url"].startswith("https://app.notion.com/p/")
    assert [kind["kind"] for kind in card["kinds"]] == ALL
    assert all(kind["enabled"] and kind["database"] == "none" for kind in card["kinds"])
    assert SECRET not in str(card)
    assert [(r.method, r.path) for r in fake.requests] == [
        ("GET", "/users/me"),
        ("GET", f"/pages/{card['page']['id']}"),
    ]
    assert all(r.version == "2026-03-11" and r.token == SECRET for r in fake.requests)
    row = await integration(db, user_id)
    assert row.secret_encrypted and SECRET not in row.secret_encrypted
    from app.core.secret_box import open_secret

    assert open_secret(row.secret_encrypted) == SECRET
    assert row.status == "connected" and row.generation == 1
    again = (await authed.get(N)).json()
    assert SECRET not in str(again) and again["secret_set"] is True


@pytest.mark.parametrize(
    ("secret", "page", "code"),
    [
        ("short", None, "notion_secret_invalid"),
        ("ntn_ with spaces inside the secret value", None, "notion_secret_invalid"),
        ("ntn_" + "ж" * 30, None, "notion_secret_invalid"),
        (SECRET, "https://example.com/DisciplineOS-0123456789abcdef0123456789abcdef", "notion_page_invalid"),
        (SECRET, "https://www.notion.so/no-id-here", "notion_page_invalid"),
        (SECRET, "javascript:alert(1)", "notion_page_invalid"),
    ],
)
async def test_obvious_mistakes_are_refused_without_a_request(authed, user_id, fake, db, secret, page, code) -> None:
    page_id = fake.add_page(shared_with=(SECRET,))
    response = await authed.post(f"{N}/connect", json={"secret": secret, "page": page or page_link(page_id)})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert fake.requests == []
    assert secret not in response.text


async def test_a_wrong_secret_and_a_page_not_shared(authed, user_id, fake, db) -> None:
    page_id = fake.add_page(shared_with=(SECRET,))
    wrong = await authed.post(f"{N}/connect", json={"secret": "ntn_" + "x" * 40, "page": page_link(page_id)})
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "notion_unauthorized"
    hidden = fake.add_page("Чужая страница")  # exists, but not shared with the connection
    response = await authed.post(f"{N}/connect", json={"secret": SECRET, "page": page_link(hidden)})
    assert response.status_code == 422 and response.json()["error"]["code"] == "notion_page_not_shared"
    fake.pages[page_id]["in_trash"] = True
    trashed = await authed.post(f"{N}/connect", json={"secret": SECRET, "page": page_id})
    assert trashed.status_code == 422 and trashed.json()["error"]["code"] == "notion_page_in_trash"
    assert (await db.scalar(select(func.count()).select_from(Integration))) == 0
    assert (await authed.get(N)).json()["connected"] is False


async def test_notion_down_or_busy_answers_in_words(authed, user_id, fake, db) -> None:
    page_id = fake.add_page(shared_with=(SECRET,))
    fake.fail("GET", "/users/me", 503, "service_unavailable", times=4)
    down = await authed.post(f"{N}/connect", json={"secret": SECRET, "page": page_id})
    assert down.status_code == 503 and down.json()["error"]["code"] == "notion_unavailable"
    fake.fail("GET", "/users/me", 429, "rate_limited", headers={"Retry-After": "120"})
    busy = await authed.post(f"{N}/connect", json={"secret": SECRET, "page": page_id})
    assert busy.status_code == 503 and busy.json()["error"]["code"] == "notion_rate_limited"
    assert busy.json()["error"]["details"]["retry_after"] == 120


@pytest.mark.parametrize(
    "link",
    [
        "https://www.notion.so/myspace/Plan-{hex}",
        "https://www.notion.so/{hex}",
        "https://notion.so/Plan-{hex}?pvs=4#section",
        "https://app.notion.com/p/{hex}",
        "https://app.notion.com/p/Plan-{hex}",
        "https://myspace.notion.site/Plan-{hex}",
        "www.notion.so/myspace/Plan-{hex}",
        "https://www.notion.so/myspace/Inbox-ffffffffffffffffffffffffffffffff?p={hex}&pm=s",
        "{dashed}",
        "{hex}",
        "  {HEX}  ",
    ],
)
def test_page_link_forms(link: str) -> None:
    from app.services.notion.sync import parse_page

    page_id = "1f2e3d4c-5b6a-4987-8a1b-2c3d4e5f6a7b"
    raw = link.format(hex=page_id.replace("-", ""), dashed=page_id, HEX=page_id.replace("-", "").upper())
    assert parse_page(raw) == page_id


# --------------------------------------------------------------------------- #
# Setup: databases under the page
# --------------------------------------------------------------------------- #
async def test_setup_creates_the_databases_with_parent_version_schema_and_relations(authed, user_id, fake, db) -> None:
    card = await connect(authed, fake)
    page_id = card["page"]["id"]
    before = len(fake.requests)
    card = await setup_all(authed)
    created = fake.calls("POST", "/databases")
    assert len(created) == 5
    assert all(r.version == "2026-03-11" and r.token == SECRET for r in fake.requests[before:])
    assert [r.body["title"][0]["text"]["content"] for r in created] == [TITLES[kind] for kind in ALL]
    assert all(r.body["parent"] == {"type": "page_id", "page_id": page_id} for r in created)
    assert all("перезапишутся" in r.body["description"][0]["text"]["content"] for r in created)
    # Russian-first property names, the ID column described, select options in the owner's language
    tasks_props = created[2].body["initial_data_source"]["properties"]
    assert {"Задача", "Статус", "Приоритет", "Проект", "Область", "Срок", "Теги", "Заметки", "ID DisciplineOS"} <= set(
        tasks_props
    )
    assert tasks_props["Задача"] == {"type": "title", "title": {}}
    assert [o["name"] for o in tasks_props["Статус"]["select"]["options"]][:3] == [
        "Входящие",
        "Открыта",
        "Запланирована",
    ]
    assert tasks_props["ID DisciplineOS"]["description"].startswith("Служебный ключ")
    # relations: two-way, to the target's data source id
    areas_ds, projects_ds, tasks_ds = (fake.ds_by_title(TITLES[k]) for k in ("areas", "projects", "tasks"))
    assert tasks_props["Проект"]["relation"] == {
        "data_source_id": projects_ds["id"],
        "type": "dual_property",
        "dual_property": {"synced_property_name": "Задачи"},
    }
    assert (
        created[1].body["initial_data_source"]["properties"]["Область"]["relation"]["data_source_id"] == areas_ds["id"]
    )
    parent = tasks_ds["properties"]["Родительская задача"]  # the self-relation is added after creation
    assert parent["type"] == "relation" and parent["relation"]["data_source_id"] == tasks_ds["id"]
    assert "Подзадачи" in tasks_ds["properties"] and "Задачи" in projects_ds["properties"]
    assert {"Проекты", "Задачи"} <= set(areas_ds["properties"])
    assert [kind["database"] for kind in card["kinds"]] == ["ok"] * 5
    assert all(kind["url"].startswith("https://app.notion.com/p/") for kind in card["kinds"])
    # idempotent: nothing more is created
    count = len(fake.requests)
    again = await setup_all(authed)
    assert len(fake.requests) == count and [k["database"] for k in again["kinds"]] == ["ok"] * 5
    row = await integration(db, user_id)
    tasks_db = row.config["databases"]["tasks"]
    assert tasks_db["lang"] == "ru" and tasks_db["props"]["title"] == "title"
    assert set(tasks_db["relations"]) == {"project", "area", "parent"}


async def test_setup_in_english_and_one_kind_at_a_time(authed, user_id, fake, db) -> None:
    await authed.patch("/api/v1/settings", json={"language": "en"})
    await connect(authed, fake)
    await setup_all(authed, ["tasks"])
    tasks_ds = fake.ds_by_title("Tasks")
    assert {"Task", "Status", "Priority", "Parent task", "Subtasks", "DisciplineOS ID"} <= set(tasks_ds["properties"])
    assert "Project" not in tasks_ds["properties"]  # projects have no database yet
    await setup_all(authed, ["projects"])  # the relation is added once the target exists
    tasks_ds = fake.ds_by_title("Tasks")
    assert tasks_ds["properties"]["Project"]["relation"]["data_source_id"] == fake.ds_by_title("Projects")["id"]
    card = (await authed.get(N)).json()
    assert {kind["kind"]: kind["database"] for kind in card["kinds"]} == {
        "areas": "none",
        "projects": "ok",
        "tasks": "ok",
        "journal": "none",
        "days": "none",
    }


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
async def connected(authed, fake) -> None:
    await connect(authed, fake)
    await setup_all(authed)


async def test_first_sync_creates_every_row_once_with_its_values(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    report = await sync_now(user_id)
    assert report.result == "ok" and report.error is None
    areas = (await db.scalars(select(Area.id).where(Area.user_id == user_id, Area.is_archived.is_(False)))).all()
    totals = report.totals()
    assert report.kinds["areas"].created == len(areas) and report.kinds["projects"].created == 1
    assert report.kinds["tasks"].created == 3 and report.kinds["journal"].created == 1
    assert report.kinds["days"].created == 2  # yesterday and today have data
    assert totals["created"] == len(areas) + 7 and totals["failed"] == totals["pending"] == 0
    creates = fake.calls("POST", "/pages")
    assert len(creates) == totals["created"]  # each row exactly once
    assert all(r.body["parent"]["type"] == "data_source_id" for r in creates)
    # values
    area = fake.find("Области", ids["area"])
    assert fake.value(area, "Название") == "Спорт" and fake.value(area, "Цель в неделю, мин") == 300
    project = fake.find("Проекты", ids["project"])
    assert fake.value(project, "Статус") == "Активный" and fake.value(project, "Область") == [area["id"]]
    assert fake.value(project, "Дедлайн") == {"start": "2026-10-04"}
    task = fake.find("Задачи", ids["task"])
    assert fake.value(task, "Задача") == "Купить кроссовки" and fake.value(task, "Приоритет") == "P1"
    assert fake.value(task, "Статус") == "Открыта" and fake.value(task, "Проект") == [project["id"]]
    assert fake.value(task, "Срок") == {"start": "2026-07-16T18:30:00", "time_zone": "Asia/Bangkok"}
    assert fake.value(task, "Блок времени") == {
        "start": "2026-07-15T14:00:00",
        "end": "2026-07-15T15:30:00",
        "time_zone": "Asia/Bangkok",
    }
    assert sorted(fake.value(task, "Теги")) == ["бег", "покупки"]
    assert fake.value(task, "Заметки") == "- [ ] примерить\n- [ ] сравнить цены"
    assert fake.value(task, "Контекст") == "@город" and fake.value(task, "Оценка, мин") == 45
    subtask = fake.find("Задачи", ids["subtask"])
    assert fake.value(subtask, "Родительская задача") == [task["id"]]  # parents are created first
    done = fake.find("Задачи", ids["done"])
    assert fake.value(done, "Статус") == "Выполнена" and fake.value(done, "Область") == [area["id"]]
    assert fake.value(done, "Дата выполнения") == {"start": "2026-07-15T09:15:00", "time_zone": "Asia/Bangkok"}
    entry = fake.find("Дневник", ids["journal"])
    assert fake.value(entry, "День") == "2026-07-14" and fake.value(entry, "Оценка дня") == 4
    assert fake.value(entry, "Победы") == "Пробежал 10 км"
    assert sorted(fake.value(entry, "Теги")) == ["a b", "бег"]  # no commas in option names
    # the integration row
    row = await integration(db, user_id)
    assert row.last_sync_at is not None and row.counts["result"] == "ok" and row.last_error is None
    assert row.counts["kinds"]["tasks"]["total"] == 3
    assert len(await links(db, user_id)) == totals["created"]


async def test_days_keep_burned_kcal_apart_from_food(authed, user_id, fake, db) -> None:
    await seed(db, user_id)
    from tests.stage8_helpers import run as run_game

    await run_game(db, user_id)  # stores today's and yesterday's scores (and the day's target)
    await connected(authed, fake)
    await sync_now(user_id)
    today = fake.find("Итоги дня", TODAY.isoformat())
    assert fake.value(today, "Дата") == {"start": "2026-07-15"}
    assert fake.value(today, "Съедено, ккал") == 2000  # food only
    assert fake.value(today, "Сожжено на тренировках, ккал") == 500  # its own column
    assert fake.value(today, "Активная энергия (Health), ккал") == 320  # its own column
    assert fake.value(today, "Цель, ккал") == 2000 and fake.value(today, "В зоне цели") is True
    assert fake.value(today, "Белок, г") == 150.0 and fake.value(today, "Шаги") == 9500
    assert fake.value(today, "Сон, ч") == 7.5 and fake.value(today, "Тренировки") == 1
    assert fake.value(today, "Учтённое время, мин") == 95 and fake.value(today, "Задач выполнено") == 1
    assert isinstance(fake.value(today, "Баллы дня"), float)
    yesterday = fake.find("Итоги дня", YESTERDAY.isoformat())
    assert fake.value(yesterday, "Съедено, ккал") == 1800
    assert fake.value(yesterday, "Сожжено на тренировках, ккал") is None  # no workout ≠ 0 burned
    assert fake.value(yesterday, "Шаги") is None and fake.value(yesterday, "В зоне цели") is False
    for page in fake.rows(fake.ds_by_title("Итоги дня")["id"]):
        eaten = fake.value(page, "Съедено, ккал")
        burned = (fake.value(page, "Сожжено на тренировках, ккал") or 0) + (
            fake.value(page, "Активная энергия (Health), ккал") or 0
        )
        assert eaten in (2000, 1800)  # never food ± burned
        assert burned == 0 or eaten not in (2000 - burned, 2000 + burned, 1800 - burned, 1800 + burned)


async def test_an_unchanged_run_sends_nothing_and_an_edit_updates_exactly_that_page(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    count = len(fake.requests)
    report = await sync_now(user_id)
    assert report.result == "ok" and len(fake.requests) == count  # not a single request
    assert report.totals()["unchanged"] == report.totals()["total"] > 0
    # one edit → one PATCH of that page
    task = await db.get(Task, uuid.UUID(ids["task"]))
    task.title = "Купить кроссовки для марафона"
    await db.commit()
    report = await sync_now(user_id)
    new = fake.requests[count:]
    page = fake.find("Задачи", ids["task"])
    assert [(r.method, r.path) for r in new] == [("PATCH", f"/pages/{page['id']}")]
    assert report.kinds["tasks"].updated == 1 and report.totals()["updated"] == 1
    assert fake.value(page, "Задача") == "Купить кроссовки для марафона"


async def test_deleted_and_archived_rows_go_to_the_trash_and_come_back(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    subtask_page = fake.find("Задачи", ids["subtask"])
    project_page = fake.find("Проекты", ids["project"])
    await db.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": ids["subtask"]})
    project = await db.get(Project, uuid.UUID(ids["project"]))
    project.is_archived = True
    await db.commit()
    count = len(fake.requests)
    report = await sync_now(user_id)
    trash_calls = [r for r in fake.requests[count:] if r.body == {"in_trash": True}]
    assert {r.path for r in trash_calls} == {f"/pages/{subtask_page['id']}", f"/pages/{project_page['id']}"}
    assert fake.pages[subtask_page["id"]]["in_trash"] and fake.pages[project_page["id"]]["in_trash"]
    assert report.kinds["tasks"].trashed == 1 and report.kinds["projects"].trashed == 1
    # the task that pointed at the project now has an empty relation (its page is in the trash)
    assert fake.value(fake.find("Задачи", ids["task"]), "Проект") == []
    # an unchanged run does not trash again; restoring the project brings its page back
    count = len(fake.requests)
    await sync_now(user_id)
    assert len(fake.requests) == count
    project.is_archived = False
    await db.commit()
    report = await sync_now(user_id)
    assert fake.pages[project_page["id"]]["in_trash"] is False and report.kinds["projects"].updated == 1
    restore = next(r for r in fake.requests[count:] if r.path == f"/pages/{project_page['id']}")
    assert restore.body["in_trash"] is False and "properties" in restore.body
    assert fake.value(fake.find("Задачи", ids["task"]), "Проект") == [project_page["id"]]
    assert len(fake.rows(fake.ds_by_title("Проекты")["id"])) == 1  # restored, not created again


async def test_a_page_trashed_or_deleted_in_notion_is_brought_back(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    page = fake.find("Задачи", ids["task"])
    page["in_trash"] = True  # the owner moved it to the trash in Notion
    task = await db.get(Task, uuid.UUID(ids["task"]))
    task.priority = 2
    await db.commit()
    await sync_now(user_id)
    assert fake.pages[page["id"]]["in_trash"] is False and fake.value(fake.pages[page["id"]], "Приоритет") == "P2"
    del fake.pages[page["id"]]  # … and then deleted it for good
    task.priority = 3
    await db.commit()
    report = await sync_now(user_id)
    again = fake.find("Задачи", ids["task"])
    assert again["id"] != page["id"] and fake.value(again, "Приоритет") == "P3"
    assert report.kinds["tasks"].created == 1


async def test_retry_after_is_honoured_and_requests_are_paced(authed, user_id, fake, clock, db) -> None:
    await seed(db, user_id)
    await connected(authed, fake)
    fake.fail("POST", "/pages", 429, "rate_limited", headers={"Retry-After": "7"})
    start = len(fake.requests)
    report = await sync_now(user_id)
    assert report.result == "ok"
    assert 7.0 in clock.sleeps
    creates = fake.calls("POST", "/pages")
    assert len([r for r in creates if r.status == 200]) == report.totals()["created"]  # no duplicate
    times = [r.at for r in fake.requests[start:]]
    gaps = [later - earlier for earlier, later in itertools.pairwise(times)]
    assert min(gaps) >= 1 / 3 - 1e-6  # ≤ 3 requests per second
    retried = [i for i, r in enumerate(fake.requests) if r.status == 429]
    assert fake.requests[retried[0] + 1].at - fake.requests[retried[0]].at >= 7.0


async def test_a_long_retry_after_ends_the_run_and_the_next_run_resumes(authed, user_id, fake, clock, db) -> None:
    await seed(db, user_id)
    await connected(authed, fake)
    fake.fail("POST", "/pages", 429, "rate_limited", headers={"Retry-After": "3600"})
    report = await sync_now(user_id)
    assert report.result == "partial" and report.error == "notion_rate_limited"
    assert 3600.0 not in clock.sleeps  # never sleeps that long
    row = await integration(db, user_id)
    assert row.status == "connected" and row.last_error == "notion_rate_limited"
    assert all(link.external_id is not None for link in await links(db, user_id))  # nothing left half-done
    report = await sync_now(user_id)
    assert report.result == "ok" and report.error is None
    assert len(fake.calls("POST", "/pages")) - 1 == report.totals()["total"]


async def test_5xx_is_retried_and_a_lost_create_is_never_duplicated(authed, user_id, fake, clock, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    # an update answered 502 twice is retried (idempotent) and lands
    task = await db.get(Task, uuid.UUID(ids["task"]))
    task.title = "Новое название"
    await db.commit()
    fake.fail("PATCH", "/pages/", 502, "bad_gateway", times=2)
    report = await sync_now(user_id)
    assert report.result == "ok" and fake.value(fake.find("Задачи", ids["task"]), "Задача") == "Новое название"
    # a create saved by Notion but answered 500: not repeated now, looked up next time
    extra = Task(user_id=user_id, title="Новая задача", status=TaskStatus.OPEN, priority=3)
    db.add(extra)
    await db.commit()
    extra_id = str(extra.id)
    fake.fail("POST", "/pages", 500, "internal_server_error", apply=True)
    report = await sync_now(user_id)
    assert report.kinds["tasks"].pending == 1 and report.kinds["tasks"].created == 0
    pending = [link for link in await links(db, user_id, "task") if link.entity_id == extra_id]
    assert pending[0].external_id is None and pending[0].content_hash is None
    count = len(fake.calls("POST", "/pages"))
    report = await sync_now(user_id)
    assert len(fake.calls("POST", "/pages")) == count  # found by its ID property, not created again
    assert any(r.path.endswith("/query") for r in fake.requests[-3:])
    assert report.kinds["tasks"].created == 1 and fake.find("Задачи", extra_id)
    # a create answered 503 with committed_resource_id is adopted at once
    third = Task(user_id=user_id, title="Третья", status=TaskStatus.OPEN, priority=3)
    db.add(third)
    await db.commit()
    third_id = str(third.id)
    fake.fail("POST", "/pages", 503, "service_unavailable", apply=True, commit_id=True)
    report = await sync_now(user_id)
    assert report.kinds["tasks"].created == 1 and report.kinds["tasks"].pending == 0
    page = fake.find("Задачи", third_id)
    assert [link.external_id for link in await links(db, user_id, "task") if link.entity_id == third_id] == [page["id"]]
    # a create refused outright (500, nothing saved) is created by the next run
    fourth = Task(user_id=user_id, title="Четвёртая", status=TaskStatus.OPEN, priority=3)
    db.add(fourth)
    await db.commit()
    fourth_id = str(fourth.id)
    fake.fail("POST", "/pages", 500, "internal_server_error")
    assert (await sync_now(user_id)).kinds["tasks"].pending == 1
    assert (await sync_now(user_id)).kinds["tasks"].created == 1
    assert fake.find("Задачи", fourth_id)


async def test_a_lost_answer_to_a_timeout_is_resolved_without_a_duplicate(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    fake.fail("POST", "/pages", 0, "", exception=httpx.ReadTimeout, apply=True)
    first = await sync_now(user_id)
    assert first.totals()["pending"] == 1
    second = await sync_now(user_id)
    assert second.totals()["pending"] == 0
    total = len(await links(db, user_id))
    for kind in ALL:
        ds = fake.ds_by_title(TITLES[kind])
        rows = fake.rows(ds["id"])
        keys = [fake.value(page, "ID DisciplineOS") for page in rows]
        assert len(keys) == len(set(keys)), kind  # never two pages for one row
    assert sum(len(fake.rows(fake.ds_by_title(TITLES[kind])["id"])) for kind in ALL) == total
    assert fake.find("Области", ids["area"])


async def test_a_run_cut_by_its_budget_continues_next_time(authed, user_id, fake, db) -> None:
    await seed(db, user_id)
    await connected(authed, fake)
    results = []
    for _ in range(20):
        report = await sync_now(user_id, budget=4)
        results.append(report.result)
        if report.result == "ok":
            break
    assert results[0] == "partial" and results[-1] == "ok" and len(results) > 2
    counts = (await integration(db, user_id)).counts
    assert counts["result"] == "ok"
    assert all(kind["state"] == "ok" for kind in counts["kinds"].values())
    created = [r for r in fake.calls("POST", "/pages") if r.status == 200]
    assert len(created) == len(await links(db, user_id))  # every row once, never twice


async def test_two_runs_at_once_make_one(authed, user_id, fake, db) -> None:
    await seed(db, user_id)
    await connected(authed, fake)
    first, second = await asyncio.gather(sync_now(user_id), sync_now(user_id))
    assert sorted([first.result, second.result]) == ["busy", "ok"]
    created = [r for r in fake.calls("POST", "/pages") if r.status == 200]
    assert len(created) == len(await links(db, user_id))


async def test_sync_endpoint_runs_in_the_background_and_reports(authed, user_id, fake, db) -> None:
    from app.services.notion import sync

    await seed(db, user_id)
    await connected(authed, fake)
    lock = await sync.RunLock.acquire(user_id)  # a run in progress (e.g. the job)
    assert lock is not None
    busy = await authed.post(f"{N}/sync")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "notion_sync_running"
    setup_busy = await authed.post(f"{N}/setup", json={"kinds": ["tasks"]})
    assert setup_busy.status_code == 409 and setup_busy.json()["error"]["code"] == "notion_sync_running"
    await lock.release()
    started = await authed.post(f"{N}/sync")
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    assert started.json()["state"] == "running"
    await sync.wait_for_background_runs()
    card = (await authed.get(N)).json()
    assert card["sync"]["state"] == "idle" and card["sync"]["run_id"] == run_id
    assert card["sync"]["result"] == "ok" and card["sync"]["error"] is None
    assert card["sync"]["totals"]["created"] == card["sync"]["totals"]["total"] > 0
    assert card["sync"]["last_sync_at"] is not None and card["sync"]["requests"] > 0
    tasks = next(kind for kind in card["kinds"] if kind["kind"] == "tasks")
    assert tasks["last_run"]["created"] == 3 and tasks["last_run"]["state"] == "ok"


async def test_a_running_run_is_shown_and_an_interrupted_one_is_said_so(authed, user_id, fake, db) -> None:
    from app.services.notion import sync

    await connected(authed, fake)
    lock = await sync.RunLock.acquire(user_id)
    row = await integration(db, user_id)
    row.run_id, row.run_started_at, row.run_finished_at = uuid.uuid4(), NOON, None
    await db.commit()
    assert (await authed.get(N)).json()["sync"]["state"] == "running"
    await lock.release()  # the process died: the lock is gone, the row still says running
    card = (await authed.get(N)).json()
    assert card["sync"]["state"] == "idle" and card["sync"]["result"] == "interrupted"
    started = await authed.post(f"{N}/sync")  # and a new run can start
    assert started.status_code == 202
    await sync.wait_for_background_runs()
    assert (await authed.get(N)).json()["sync"]["result"] == "ok"


async def test_disconnect_wipes_the_secret_and_links_and_stops_the_job(authed, user_id, fake, db) -> None:
    from app.services.notion import sync

    await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    pages_before = len(fake.pages)
    response = await authed.delete(N)
    assert response.status_code == 204
    row = await integration(db, user_id)
    assert row.secret_encrypted is None and row.status == "disconnected" and row.config == {}
    assert await links(db, user_id) == []
    stored = await db.scalar(text("SELECT secret_encrypted FROM integrations WHERE user_id = :u"), {"u": user_id})
    assert stored is None
    count = len(fake.requests)
    await sync.sync_job()
    assert len(fake.requests) == count  # the job does nothing for a disconnected user
    assert len(fake.pages) == pages_before  # Notion content stays
    card = (await authed.get(N)).json()
    assert card["connected"] is False and card["secret_set"] is False and card["page"] is None
    # connecting again starts a fresh mirror (new databases under the page)
    await connect(authed, fake)
    assert [kind["database"] for kind in (await authed.get(N)).json()["kinds"]] == ["none"] * 5


async def test_a_run_in_flight_stops_when_the_owner_disconnects(authed, user_id, fake, db) -> None:
    from app.services.notion import client as notion_client
    from app.services.notion import sync

    await seed(db, user_id)
    await connected(authed, fake)
    original = fake._route
    disconnected = asyncio.Event()

    def route(method, path, token, body):
        result = original(method, path, token, body)
        if method == "POST" and path == "/pages" and not disconnected.is_set():
            disconnected.set()
        return result

    fake._route = route  # type: ignore[method-assign]

    async def disconnect_soon():
        await disconnected.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=__import__("app.main").main.app), base_url="http://t"
        ) as http:
            http.headers["Authorization"] = authed.headers["Authorization"]
            assert (await http.delete(N)).status_code == 204

    report, _ = await asyncio.gather(sync_now(user_id), disconnect_soon())
    assert report.result == "cancelled"
    assert await links(db, user_id) == []  # nothing written after the disconnect
    assert notion_client._transport_override is not None
    assert (await integration(db, user_id)).secret_encrypted is None
    assert sync.is_active(await integration(db, user_id)) is False


async def test_kinds_switched_off_are_left_alone(authed, user_id, fake, db) -> None:
    await seed(db, user_id)
    await connected(authed, fake)
    response = await authed.patch(N, json={"kinds": ["tasks", "projects"]})
    assert response.status_code == 200
    assert {kind["kind"]: kind["enabled"] for kind in response.json()["kinds"]} == {
        "areas": False,
        "projects": True,
        "tasks": True,
        "journal": False,
        "days": False,
    }
    report = await sync_now(user_id)
    assert report.kinds["journal"].state == "skipped" and report.kinds["days"].state == "skipped"
    assert fake.rows(fake.ds_by_title("Дневник")["id"]) == []
    assert len(fake.rows(fake.ds_by_title("Задачи")["id"])) == 3
    # the areas database exists, so task/project relations to areas still resolve to nothing
    # (no area pages were created) — never to a page of another kind
    assert fake.value(fake.rows(fake.ds_by_title("Проекты")["id"])[0], "Область") == []


async def test_a_database_deleted_in_notion_is_marked_missing_and_created_again(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    fake.delete_database("Проекты")
    project = await db.get(Project, uuid.UUID(ids["project"]))
    project.outcome = "Пробежать за 4 часа"
    await db.commit()
    report = await sync_now(user_id)
    assert report.kinds["projects"].state == "missing" and report.error == "notion_database_missing"
    assert report.kinds["tasks"].state == "ok"  # the other kinds go on
    card = (await authed.get(N)).json()
    projects = next(kind for kind in card["kinds"] if kind["kind"] == "projects")
    assert projects["database"] == "missing" and projects["url"] is None
    assert card["sync"]["error"] == "notion_database_missing" and card["status"] == "connected"
    # «Создать заново»: a new database, the task relation re-pointed at it, rows created there
    card = await setup_all(authed, ["projects"])
    assert next(kind for kind in card["kinds"] if kind["kind"] == "projects")["database"] == "ok"
    new_ds = fake.ds_by_title("Проекты")
    tasks_ds = fake.ds_by_title("Задачи")
    assert tasks_ds["properties"]["Проект"]["relation"]["data_source_id"] == new_ds["id"]
    report = await sync_now(user_id)
    assert report.result == "ok" and report.error is None and report.kinds["projects"].created == 1
    page = fake.find("Проекты", ids["project"])
    assert fake.value(page, "Результат") == "Пробежать за 4 часа"
    assert fake.value(fake.find("Задачи", ids["task"]), "Проект") == [page["id"]]


async def test_a_trashed_database_is_missing_too(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    fake.trash_database("Дневник")
    entry = await db.get(JournalEntry, uuid.UUID(ids["journal"]))
    entry.rating = 5
    await db.commit()
    report = await sync_now(user_id)
    assert report.kinds["journal"].state == "missing" and report.kinds["journal"].failed == 0
    fake.trash_database("Итоги дня")
    await meal(db, user_id, TODAY - timedelta(days=3), 900)  # a new day: a create into the trashed database
    report = await sync_now(user_id)
    assert report.kinds["days"].state == "missing"
    card = (await authed.get(N)).json()
    assert {kind["kind"]: kind["database"] for kind in card["kinds"]}["journal"] == "missing"


async def test_a_column_deleted_in_notion_is_put_back(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    fake.delete_column("Задачи", "Статус")
    task = await db.get(Task, uuid.UUID(ids["task"]))
    task.status = TaskStatus.IN_PROGRESS
    await db.commit()
    report = await sync_now(user_id)
    assert report.result == "ok" and report.kinds["tasks"].failed == 0
    tasks_ds = fake.ds_by_title("Задачи")
    assert tasks_ds["properties"]["Статус"]["type"] == "select"
    assert fake.value(fake.find("Задачи", ids["task"]), "Статус") == "В работе"
    # a relation deleted in Notion comes back the same way
    fake.delete_column("Задачи", "Проект")
    task.title = "Ещё одно изменение"
    await db.commit()
    report = await sync_now(user_id)
    assert report.kinds["tasks"].failed == 0
    assert fake.value(fake.find("Задачи", ids["task"]), "Проект") == [fake.find("Проекты", ids["project"])["id"]]


async def test_a_row_notion_refuses_is_counted_and_not_retried_until_it_changes(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    await sync_now(user_id)
    task = await db.get(Task, uuid.UUID(ids["task"]))
    task.title = "Отклонённая правка"
    await db.commit()
    fake.fail("PATCH", "/pages/", 400, "validation_error", times=3)
    report = await sync_now(user_id)
    assert report.kinds["tasks"].failed == 1 and report.result == "ok"
    count = len(fake.requests)
    report = await sync_now(user_id)
    assert report.kinds["tasks"].failed == 1 and len(fake.requests) == count  # not hammered
    task.title = "Исправленная правка"
    await db.commit()
    report = await sync_now(user_id)
    assert report.kinds["tasks"].updated == 1 and report.kinds["tasks"].failed == 0


async def test_long_notes_are_split_and_capped(authed, user_id, fake, db) -> None:
    from app.services.notion.mapping import split_text, utf16_len

    text_value = ("Заметка 🏃 " * 400)[:4500]
    chunks = split_text(text_value)
    assert "".join(chunks) == text_value and all(utf16_len(chunk) <= 2000 for chunk in chunks)
    capped = split_text("я" * 50_000)
    assert len(capped) == 10 and capped[-1].endswith("…") and all(utf16_len(chunk) <= 2000 for chunk in capped)
    task = Task(user_id=user_id, title="Длинная", status=TaskStatus.OPEN, priority=3, notes_md="😀" * 1500)
    db.add(task)
    await db.commit()
    task_id = str(task.id)
    await connected(authed, fake)
    report = await sync_now(user_id)
    assert report.kinds["tasks"].failed == 0
    page = fake.find("Задачи", task_id)
    assert fake.value(page, "Заметки") == "😀" * 1500  # 3 000 UTF-16 units → two text objects
    notes_id = fake.ds_by_title("Задачи")["properties"]["Заметки"]["id"]
    assert len(page["properties"][notes_id]["rich_text"]) == 2


async def test_unauthorized_marks_the_card_and_the_job_waits_for_a_new_secret(authed, user_id, fake, db) -> None:
    from app.services.notion import sync

    await seed(db, user_id)
    await connected(authed, fake)
    del fake.tokens[SECRET]  # the owner refreshed the secret in the Developer portal
    report = await sync_now(user_id)
    assert report.result == "error" and report.error == "notion_unauthorized"
    row = await integration(db, user_id)
    assert row.status == "error" and row.last_error == "notion_unauthorized"
    card = (await authed.get(N)).json()
    assert card["status"] == "error" and card["error"] == "notion_unauthorized"
    count = len(fake.requests)
    await sync.sync_job()
    assert len(fake.requests) == count  # skipped until a new secret is entered
    # a new secret for the same page keeps the databases and links
    fake.add_token(OTHER_SECRET)
    page_id = card["page"]["id"]
    fake.tokens[OTHER_SECRET]["shared"].add(page_id)
    linked = len(await links(db, user_id))
    response = await authed.post(f"{N}/connect", json={"secret": OTHER_SECRET, "page": page_id})
    assert response.status_code == 201 and response.json()["status"] == "connected"
    assert [kind["database"] for kind in response.json()["kinds"]] == ["ok"] * 5
    assert len(await links(db, user_id)) == linked
    report = await sync_now(user_id)
    assert report.result == "ok" and fake.requests[-1].token == OTHER_SECRET


async def test_missing_capabilities_are_reported(authed, user_id, fake, db) -> None:
    await seed(db, user_id)
    await connected(authed, fake)
    fake.fail("POST", "/pages", 403, "restricted_resource", times=1)
    report = await sync_now(user_id)
    assert report.error == "notion_forbidden"
    card = (await authed.get(N)).json()
    assert card["status"] == "error" and card["error"] == "notion_forbidden"
    assert (await sync_now(user_id)).result == "ok"  # once the owner fixes it, the next run goes on
    assert (await authed.get(N)).json()["status"] == "connected"


async def test_a_secret_sealed_with_another_key_is_reported(authed, user_id, fake, db, monkeypatch) -> None:
    from app.core import secret_box
    from app.core.config import get_settings

    await connected(authed, fake)
    monkeypatch.setattr(get_settings(), "integrations_key", "a-completely-different-key-0123456789")
    secret_box._fernet.cache_clear()
    try:
        report = await sync_now(user_id)
        assert report.error == "notion_secret_unreadable"
        assert (await authed.get(N)).json()["error"] == "notion_secret_unreadable"
        setup = await authed.post(f"{N}/setup", json={"kinds": ["tasks"]})
        assert setup.status_code == 422 and setup.json()["error"]["code"] == "notion_secret_unreadable"
    finally:
        secret_box._fernet.cache_clear()


def test_secret_box_uses_hkdf_and_an_own_key_when_set(monkeypatch) -> None:
    from app.core import secret_box
    from app.core.config import get_settings

    secret_box._fernet.cache_clear()
    sealed = secret_box.seal_secret(SECRET)
    assert SECRET not in sealed and secret_box.open_secret(sealed) == SECRET
    assert secret_box.seal_secret(SECRET) != sealed  # random IV every time
    monkeypatch.setattr(get_settings(), "integrations_key", "own-integrations-key-0123456789abcdef")
    secret_box._fernet.cache_clear()
    with pytest.raises(secret_box.SecretUnreadable):
        secret_box.open_secret(sealed)
    own = secret_box.seal_secret(SECRET)
    assert secret_box.open_secret(own) == SECRET
    secret_box._fernet.cache_clear()


# --------------------------------------------------------------------------- #
# Scoping, principals, limits, secrets
# --------------------------------------------------------------------------- #
async def test_each_user_mirrors_only_their_own_rows(authed, user_id, fake, db) -> None:
    ids = await seed(db, user_id)
    await connected(authed, fake)
    fake.add_token(OTHER_SECRET, name="Their bot", workspace="Их Notion")
    async with other_user() as stranger:
        assert (await stranger.get(N)).json()["connected"] is False  # not the owner's connection
        assert (await stranger.delete(N)).status_code == 204  # nothing of the owner's is touched
        assert (await integration(db, user_id)).secret_encrypted is not None
        await connect(stranger, fake, secret=OTHER_SECRET)
        await setup_all(stranger)
        other = (await stranger.get(N)).json()
        assert other["workspace_name"] == "Их Notion"
        stranger_id = (await db.scalars(select(Integration.user_id).where(Integration.user_id != user_id))).one()
        stranger_task = Task(user_id=stranger_id, title="Чужая задача", status=TaskStatus.OPEN, priority=3)
        db.add(stranger_task)
        await db.commit()
        from app.services.notion import sync

        await sync.sync_job()  # both users, one after the other
        mine = {
            r.token for r in fake.writes() if r.body and "parent" in r.body and "data_source_id" in r.body["parent"]
        }
        assert mine == {SECRET, OTHER_SECRET}
        their_tasks_ds = [
            ds
            for ds in fake.data_sources.values()
            if any(p["name"] == "Задача" for p in ds["properties"].values())
            and not fake.databases.get(ds["database_id"], {}).get("in_trash")
        ]
        assert len(their_tasks_ds) == 2
        titles = {ds["id"]: {fake.value(page, "Задача") for page in fake.rows(ds["id"])} for ds in their_tasks_ds}
        assert sorted(len(value) for value in titles.values()) == [1, 3]
        assert {"Чужая задача"} in titles.values()
        assert all("Чужая задача" not in value for value in titles.values() if len(value) == 3)
    assert fake.find("Задачи", ids["task"])
    # a user's links never point at another user's rows
    for link in await links(db, user_id):
        assert link.user_id == user_id


async def test_an_api_token_gets_403_and_writes_are_rate_limited(authed, user_id, fake, client) -> None:
    token = (await authed.post("/api/v1/tokens", json={"name": "auto", "scopes": ["read:all"]})).json()["token"]
    async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as other:
        other.headers["Authorization"] = f"Bearer {token}"
        for method, path, body in (
            ("get", API, None),
            ("get", N, None),
            ("post", f"{N}/connect", {"secret": SECRET, "page": "x"}),
            ("post", f"{N}/sync", None),
            ("delete", N, None),
        ):
            response = await (getattr(other, method)(path, json=body) if body else getattr(other, method)(path))
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["error"]["code"] == "device_only"
    statuses = [(await authed.patch(N, json={"kinds": ["tasks"]})).status_code for _ in range(21)]
    assert statuses[-1] == 429 and set(statuses[:-1]) == {409}


async def test_connect_attempts_are_limited(authed, user_id, fake) -> None:
    statuses = [
        (await authed.post(f"{N}/connect", json={"secret": "bad", "page": "bad"})).status_code for _ in range(7)
    ]
    assert statuses[:6] == [422] * 6 and statuses[6] == 429


@pytest.fixture
def structlog_events(monkeypatch) -> list[str]:
    """Every structlog event of the modules a Notion request passes through, at DEBUG
    (the app's loggers filter below WARNING in tests, so they are swapped here)."""
    import io

    import structlog

    import app.api.deps
    import app.core.errors
    import app.main
    import app.services.notion.client
    import app.services.notion.sync

    events: list[str] = []

    def capture(_logger, method, event_dict):
        events.append(f"{method} {event_dict!r}")
        raise structlog.DropEvent

    for module in (app.services.notion.sync, app.services.notion.client, app.core.errors, app.main, app.api.deps):
        monkeypatch.setattr(
            module,
            "log",
            structlog.wrap_logger(
                structlog.PrintLogger(io.StringIO()),
                processors=[capture],
                wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            ),
        )
    return events


async def test_the_secret_never_appears_in_responses_or_logs(
    authed, user_id, fake, db, caplog, structlog_events, monkeypatch
) -> None:
    from app.services.notion import sync

    # alembic's fileConfig (the conftest migrates in-process) disabled the loggers that
    # existed then; httpx logs every request line — let it, to prove headers never show
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    caplog.set_level(logging.DEBUG)
    await seed(db, user_id)
    bodies: list[str] = []
    page_id = fake.add_page(shared_with=(SECRET,))
    response = await authed.post(f"{N}/connect", json={"secret": SECRET, "page": page_link(page_id)})
    bodies.append(response.text)
    bodies.append((await authed.post(f"{N}/setup", json={"kinds": ALL})).text)
    bodies.append((await authed.post(f"{N}/sync")).text)
    await sync.wait_for_background_runs()
    bodies.append((await authed.get(N)).text)
    bodies.append((await authed.get(API)).text)
    # failures too: a refused secret, a bad page, a bad request body, Notion refusing a write
    bodies.append((await authed.post(f"{N}/connect", json={"secret": SECRET + "x", "page": "nope"})).text)
    bodies.append((await authed.post(f"{N}/connect", json={"secret": SECRET, "page": page_id, "extra": 1})).text)
    bodies.append((await authed.post(f"{N}/connect", json={"secret": SECRET * 30, "page": page_id})).text)
    fake.fail("PATCH", "/data_sources/", 500, "internal_server_error", times=8)
    task = Task(user_id=user_id, title="После ошибки", status=TaskStatus.OPEN, priority=3)
    db.add(task)
    await db.commit()
    fake.fail("POST", "/pages", 400, "validation_error")
    await sync_now(user_id)
    del fake.tokens[SECRET]
    await sync_now(user_id)
    bodies.append((await authed.get(N)).text)
    openapi = (await authed.get("/openapi.json")).text
    assert all(SECRET not in body for body in bodies)
    assert SECRET not in openapi
    assert any("notion_connected" in event for event in structlog_events)
    assert any("notion_sync_finished" in event for event in structlog_events)
    assert any("notion_http_error" in event for event in structlog_events)
    assert all(SECRET not in event for event in structlog_events)
    stdlib = "\n".join(f"{record.name} {record.getMessage()} {record.args!r}" for record in caplog.records)
    assert "api.notion.com" in stdlib  # httpx logs every request line…
    assert SECRET not in stdlib  # … never its headers
    from app.services.notion.client import NotionClient

    assert SECRET not in repr(NotionClient(SECRET))
