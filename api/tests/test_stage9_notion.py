"""Stage 9 — the optional one-way Notion mirror, against a fake Notion (no network).

Every request goes to :class:`tests.stage9_notion_helpers.FakeNotion` through an
``httpx.MockTransport``; the client's clock is a :class:`FakeClock`, so pacing and
``Retry-After`` are asserted without waiting.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal
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
from tests.stage8_helpers import NOON, TODAY, YESTERDAY, configure, local, meal, owner_id, sleep, steps, tracked, workout
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
    assert [(r.method, r.path) for r in fake.requests] == [("GET", "/users/me"), ("GET", f"/pages/{card['page']['id']}")]
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
    assert [o["name"] for o in tasks_props["Статус"]["select"]["options"]][:3] == ["Входящие", "Открыта", "Запланирована"]
    assert tasks_props["ID DisciplineOS"]["description"].startswith("Служебный ключ")
    # relations: two-way, to the target's data source id
    areas_ds, projects_ds, tasks_ds = (fake.ds_by_title(TITLES[k]) for k in ("areas", "projects", "tasks"))
    assert tasks_props["Проект"]["relation"] == {
        "data_source_id": projects_ds["id"],
        "type": "dual_property",
        "dual_property": {"synced_property_name": "Задачи"},
    }
    assert created[1].body["initial_data_source"]["properties"]["Область"]["relation"]["data_source_id"] == areas_ds["id"]
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
