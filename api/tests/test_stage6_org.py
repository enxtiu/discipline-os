"""Stage 6: areas, projects, resources, tags; scoping of every row kind; the write limit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Area, Tag, TimeEntry, User, UserStatus
from app.models.user import LanguageCode
from app.services.bootstrap import ensure_defaults
from app.services.settings_service import get_or_create_settings
from tests.stage6_helpers import API, code, complete, get_task, make_task, other_user, patch_task, today, uid

pytestmark = pytest.mark.anyio


async def _post(client: httpx.AsyncClient, path: str, body: dict, status: int = 201) -> dict:
    response = await client.post(f"{API}{path}", json=body)
    assert response.status_code == status, response.text
    return response.json()


async def test_areas_crud_order_and_counts(authed: httpx.AsyncClient, owner) -> None:
    defaults = (await authed.get(f"{API}/areas")).json()["items"]
    assert [area["name"] for area in defaults][:2] == ["Здоровье", "Работа"] and len(defaults) == 7
    area_id = uid()
    sport = await _post(
        authed, "/areas", {"id": area_id, "name": "Спорт", "color": "#FF0000", "weekly_target_minutes": 300}
    )
    assert (sport["name"], sport["color"], sport["weekly_target_minutes"], sport["is_archived"]) == (
        "Спорт",
        "#FF0000",
        300,
        False,
    )
    assert sport["counts"] == {"open_tasks": 0, "active_projects": 0} and sport["week_minutes"] == 0
    replay = await _post(authed, "/areas", {"id": area_id, "name": "Спорт"})
    assert replay["id"] == area_id and replay["created_at"] == sport["created_at"]
    for name in ("спорт", "ЗДОРОВЬЕ"):
        taken = await authed.post(f"{API}/areas", json={"name": name})
        assert taken.status_code == 409 and code(taken) == "name_taken", name
    renamed = await authed.patch(f"{API}/areas/{area_id}", json={"name": "работа"})
    assert renamed.status_code == 409 and code(renamed) == "name_taken"
    same_case = await authed.patch(f"{API}/areas/{area_id}", json={"name": "СПОРТ", "icon": "dumbbell"})
    assert same_case.status_code == 200 and same_case.json()["name"] == "СПОРТ"
    for body in ({"name": ""}, {"name": "x" * 121}, {"name": "x", "color": "red"}, {"name": "x", "icon": "i" * 49},
                 {"name": "x", "weekly_target_minutes": 10081}):  # fmt: skip
        assert (await authed.post(f"{API}/areas", json=body)).status_code == 422, body
    assert (await authed.patch(f"{API}/areas/{area_id}", json={"sort_order": 40000})).status_code == 422
    assert (await authed.patch(f"{API}/areas/{area_id}", json={"name": None})).status_code == 422

    project = await _post(authed, "/projects", {"name": "Марафон", "area_id": area_id})
    await _post(authed, "/projects", {"name": "Пауза", "area_id": area_id, "status": "on_hold"})
    task = await make_task(authed, "пробежка", area_id=area_id)
    await make_task(authed, "шаг", parent_id=task["id"])  # subtasks are not counted
    done = await make_task(authed, "сделано", area_id=area_id)
    await complete(authed, done["id"])
    await _post(authed, "/resources", {"name": "План тренировок", "area_id": area_id})
    async with SessionLocal() as session:  # 45 minutes on today's date, whatever the clock says
        started = datetime.now(UTC) - timedelta(hours=1)
        session.add(
            TimeEntry(
                user_id=owner.id,
                area_id=uuid.UUID(area_id),
                started_at=started,
                ended_at=started + timedelta(minutes=45),
                duration_s=2700,
                local_day=today(),
                source="manual",
            )
        )
        await session.commit()
    detail = (await authed.get(f"{API}/areas/{area_id}")).json()
    assert detail["area"]["counts"] == {"open_tasks": 1, "active_projects": 1}
    assert detail["area"]["week_minutes"] == 45
    assert [item["name"] for item in detail["projects"]] == ["Марафон", "Пауза"]
    assert [item["id"] for item in detail["tasks"]] == [task["id"]]
    assert [item["name"] for item in detail["resources"]] == ["План тренировок"]

    order = [area["id"] for area in defaults[:3]]
    reordered = await authed.post(f"{API}/areas/reorder", json={"ids": [order[2], area_id, order[0], uid()]})
    assert reordered.status_code == 200 and reordered.json() == {"ok": True}
    names = [area["id"] for area in (await authed.get(f"{API}/areas")).json()["items"]]
    assert names[:3] == [order[2], area_id, order[0]]

    archived = await authed.patch(f"{API}/areas/{area_id}", json={"is_archived": True})
    assert archived.json()["is_archived"] is True
    assert area_id not in [a["id"] for a in (await authed.get(f"{API}/areas")).json()["items"]]
    assert area_id in [
        a["id"] for a in (await authed.get(f"{API}/areas", params={"include_archived": True})).json()["items"]
    ]

    dated = await make_task(authed, "с датой", area_id=area_id, due_date=today().isoformat())
    assert (await authed.delete(f"{API}/areas/{area_id}")).status_code == 204
    assert (await authed.get(f"{API}/areas/{area_id}")).status_code == 404
    orphan = await get_task(authed, task["id"])
    assert orphan["area"] is None and orphan["status"] == "inbox"  # nothing left: back to the inbox
    kept = await get_task(authed, dated["id"])
    assert kept["area"] is None and kept["status"] == "open"
    assert (await authed.get(f"{API}/projects/{project['id']}")).json()["project"]["area"] is None
    assert (await authed.delete(f"{API}/areas/{area_id}")).status_code == 404


async def test_projects_status_progress_and_delete(authed: httpx.AsyncClient) -> None:
    yesterday = (today() - timedelta(days=1)).isoformat()
    project_id = uid()
    project = await _post(
        authed, "/projects", {"id": project_id, "name": "Ремонт", "outcome": "кухня готова", "deadline": yesterday}
    )
    replayed = await _post(authed, "/projects", {"id": project_id, "name": "Ремонт"})
    assert replayed["id"] == project_id and replayed["created_at"] == project["created_at"]
    assert len((await authed.get(f"{API}/projects")).json()["items"]) == 1
    assert (project["status"], project["overdue"], project["progress"], project["counts"]) == (
        "active",
        True,
        0.0,
        {"open": 0, "done": 0, "total": 0},
    )
    first = await make_task(authed, "купить плитку", project_id=project["id"])
    second = await make_task(authed, "нанять мастера", project_id=project["id"], due_date=today().isoformat())
    third = await make_task(authed, "выбрать цвет", project_id=project["id"])
    await make_task(authed, "подзадача", parent_id=first["id"])
    skipped = await make_task(authed, "не нужно", project_id=project["id"])
    await patch_task(authed, skipped["id"], status="cancelled")
    await complete(authed, third["id"])
    await authed.post(
        f"{API}/time-entries",
        json={
            "project_id": project["id"],
            "date": (today() - timedelta(days=1)).isoformat(),
            "start": "10:00",
            "end": "11:30",
        },
    )
    detail = (await authed.get(f"{API}/projects/{project['id']}")).json()
    assert detail["project"]["counts"] == {"open": 2, "done": 1, "total": 3}
    assert detail["project"]["progress"] == pytest.approx(1 / 3, abs=1e-4)
    assert detail["project"]["tracked_minutes"] == 90
    assert {item["id"] for item in detail["tasks"]} == {first["id"], second["id"]}
    assert [item["id"] for item in detail["done"]] == [third["id"]]

    completed = (await authed.patch(f"{API}/projects/{project['id']}", json={"status": "completed"})).json()
    assert (
        completed["status"] == "completed" and completed["completed_at"] is not None and completed["overdue"] is False
    )
    listed = (await authed.get(f"{API}/projects")).json()["items"]
    assert project["id"] not in [item["id"] for item in listed]
    assert [
        item["id"] for item in (await authed.get(f"{API}/projects", params={"status": "completed"})).json()["items"]
    ] == [project["id"]]
    active_again = (await authed.patch(f"{API}/projects/{project['id']}", json={"status": "active"})).json()
    assert active_again["completed_at"] is None
    archived = (await authed.patch(f"{API}/projects/{project['id']}", json={"status": "archived"})).json()
    assert archived["is_archived"] is True
    assert [
        item["id"] for item in (await authed.get(f"{API}/projects", params={"status": "archived"})).json()["items"]
    ] == [project["id"]]
    restored = (await authed.patch(f"{API}/projects/{project['id']}", json={"is_archived": False})).json()
    assert (restored["status"], restored["is_archived"]) == ("active", False)
    assert len((await authed.get(f"{API}/projects", params={"status": "all"})).json()["items"]) == 1
    bad = await authed.get(f"{API}/projects", params={"status": "nope"})
    assert bad.status_code == 422
    for body in ({"name": ""}, {"name": "x" * 201}, {"name": "x", "status": "done"}, {"name": "x", "area_id": uid()}):
        assert (await authed.post(f"{API}/projects", json=body)).status_code in (404, 422), body

    other = await _post(authed, "/projects", {"name": "Другой"})
    assert (await authed.post(f"{API}/projects/reorder", json={"ids": [other["id"], project["id"]]})).status_code == 200
    ordered = (await authed.get(f"{API}/projects")).json()["items"]
    assert [item["id"] for item in ordered] == [other["id"], project["id"]]

    assert (await authed.delete(f"{API}/projects/{project['id']}")).status_code == 204
    orphan = await get_task(authed, first["id"])
    assert orphan["project"] is None and orphan["status"] == "inbox"
    assert orphan["subtasks"][0]["project"] is None and orphan["subtasks"][0]["status"] == "open"
    assert (await get_task(authed, second["id"]))["status"] == "open"  # it has a date
    assert (await get_task(authed, third["id"]))["status"] == "done"
    assert (await authed.delete(f"{API}/projects/{project['id']}")).status_code == 404


async def test_resources_and_tags(authed: httpx.AsyncClient) -> None:
    area_id = (await authed.get(f"{API}/areas")).json()["items"][0]["id"]
    resource = await _post(
        authed,
        "/resources",
        {
            "name": "Книги",
            "url": "https://example.com/books",
            "area_id": area_id,
            "tags": ["#Чтение", "чтение", "книги"],
        },
    )
    assert resource["tags"] == ["Чтение", "книги"] and resource["area"]["id"] == area_id
    replayed = await _post(authed, "/resources", {"id": resource["id"], "name": "Книги"})
    assert replayed["id"] == resource["id"] and replayed["created_at"] == resource["created_at"]
    newer = await _post(authed, "/resources", {"name": "Подкасты"})
    listed = (await authed.get(f"{API}/resources")).json()["items"]
    assert [item["id"] for item in listed] == [newer["id"], resource["id"]]
    assert [
        item["id"] for item in (await authed.get(f"{API}/resources", params={"area_id": area_id})).json()["items"]
    ] == [resource["id"]]
    for body in (
        {"name": "x", "url": "ftp://example.com"},
        {"name": "x", "url": "example.com"},
        {"name": "x", "url": "https://" + "a" * 500},
        {"name": "x", "tags": ["a b"]},
        {"name": "x", "tags": [f"t{n}" for n in range(11)]},
    ):
        assert (await authed.post(f"{API}/resources", json=body)).status_code == 422, body
    patched = (await authed.patch(f"{API}/resources/{resource['id']}", json={"tags": [], "url": None})).json()
    assert patched["tags"] == [] and patched["url"] is None
    assert (await authed.delete(f"{API}/resources/{newer['id']}")).status_code == 204
    assert (await authed.delete(f"{API}/resources/{newer['id']}")).status_code == 404

    tag_id = uid()
    focus = await _post(authed, "/tags", {"id": tag_id, "name": "#Чтение", "color": "#00AA00"})
    assert (focus["name"], focus["color"], focus["open_tasks"]) == ("Чтение", "#00AA00", 0)
    assert (await _post(authed, "/tags", {"id": tag_id, "name": "Чтение"}))["id"] == tag_id  # replay
    taken = await authed.post(f"{API}/tags", json={"name": "чтение"})
    assert taken.status_code == 409 and code(taken) == "name_taken"
    invalid = await authed.post(f"{API}/tags", json={"name": "два слова"})
    assert invalid.status_code == 422 and code(invalid) == "invalid_tag"
    task = await make_task(authed, "глубокая работа", tags=["чтение"])
    assert task["tags"][0]["id"] == tag_id
    names = [tag["name"] for tag in (await authed.get(f"{API}/tags")).json()["items"]]
    assert names == sorted(names, key=str.lower) and "Чтение" in names
    clash = await authed.patch(f"{API}/tags/{tag_id}", json={"name": "Фокус"})  # the default "фокус"
    assert clash.status_code == 409 and code(clash) == "name_taken"
    renamed = (await authed.patch(f"{API}/tags/{tag_id}", json={"name": "deep_focus", "color": None})).json()
    assert (renamed["name"], renamed["color"], renamed["open_tasks"]) == ("deep_focus", None, 1)
    assert (await get_task(authed, task["id"]))["tags"][0]["name"] == "deep_focus"
    assert (await authed.delete(f"{API}/tags/{tag_id}")).status_code == 204
    assert (await get_task(authed, task["id"]))["tags"] == []
    assert (await authed.delete(f"{API}/tags/{tag_id}")).status_code == 404


async def test_default_areas_and_tags_follow_the_language() -> None:
    """Defaults are named in the user's language; a later run in the other language adds nothing."""
    async with SessionLocal() as session:
        user = User(display_name="English", status=UserStatus.ACTIVE, is_owner=False, max_devices=3)
        session.add(user)
        await session.flush()
        settings = await get_or_create_settings(session, user)
        settings.language = LanguageCode.EN
        await ensure_defaults(session, user)

        async def names() -> tuple[list[str], list[str]]:
            areas = await session.scalars(select(Area.name).where(Area.user_id == user.id).order_by(Area.sort_order))
            tags = await session.scalars(select(Tag.name).where(Tag.user_id == user.id).order_by(Tag.name))
            return list(areas), list(tags)

        english = await names()
        assert english[0][:3] == ["Health", "Work", "Finance"] and len(english[0]) == 7
        assert english[1] == ["call", "deep-work", "errand", "health", "idea", "quick"]
        settings.language = LanguageCode.RU
        await ensure_defaults(session, user)  # the user's rows already cover every default
        assert await names() == english
        await session.rollback()


async def test_every_row_kind_is_scoped(authed: httpx.AsyncClient) -> None:
    day = today().isoformat()
    area = await _post(authed, "/areas", {"name": "Личное"})
    project = await _post(authed, "/projects", {"name": "Секрет"})
    resource = await _post(authed, "/resources", {"name": "Заметки"})
    tag = await _post(authed, "/tags", {"name": "личное"})
    task = await make_task(authed, "моя задача", project_id=project["id"])
    entry = await _post(
        authed, "/time-entries", {"date": (today() - timedelta(days=1)).isoformat(), "start": "09:00", "end": "10:00"}
    )
    journal = (await authed.put(f"{API}/journal/{day}", json={"id": uid(), "note_md": "моё"})).json()
    async with other_user() as stranger:
        for path in (
            f"/areas/{area['id']}",
            f"/projects/{project['id']}",
            f"/tasks/{task['id']}",
            f"/views/tag/{tag['id']}",
        ):
            assert (await stranger.get(f"{API}{path}")).status_code == 404, path
        for path, body in (
            (f"/areas/{area['id']}", {"name": "x"}),
            (f"/projects/{project['id']}", {"name": "x"}),
            (f"/resources/{resource['id']}", {"name": "x"}),
            (f"/tags/{tag['id']}", {"name": "x"}),
            (f"/time-entries/{entry['id']}", {"description": "x"}),
            (f"/tasks/{task['id']}", {"title": "x"}),
        ):
            assert (await stranger.patch(f"{API}{path}", json=body)).status_code == 404, path
        for path in (
            f"/areas/{area['id']}",
            f"/projects/{project['id']}",
            f"/resources/{resource['id']}",
            f"/tags/{tag['id']}",
            f"/time-entries/{entry['id']}",
            f"/tasks/{task['id']}",
            f"/journal/{day}",
        ):
            assert (await stranger.delete(f"{API}{path}")).status_code == 404, path
        for path, body in (
            ("/areas", {"id": area["id"], "name": "x"}),
            ("/projects", {"id": project["id"], "name": "x"}),
            ("/resources", {"id": resource["id"], "name": "x"}),
            ("/tags", {"id": tag["id"], "name": "x"}),
            ("/tasks", {"id": task["id"], "title": "x"}),
            ("/time-entries", {"id": entry["id"], "date": day, "start": "00:00", "end": "00:05"}),
            ("/timer/start", {"id": entry["id"]}),
        ):
            response = await stranger.post(f"{API}{path}", json=body)
            assert response.status_code == 409 and code(response) == "id_conflict", path
        conflict = await stranger.put(f"{API}/journal/{day}", json={"id": journal["id"], "note_md": "x"})
        assert conflict.status_code == 409 and code(conflict) == "id_conflict"
        for path, body in (
            ("/tasks", {"title": "x", "project_id": project["id"]}),
            ("/tasks", {"title": "x", "area_id": area["id"]}),
            ("/projects", {"name": "x", "area_id": area["id"]}),
            ("/timer/start", {"id": uid(), "task_id": task["id"]}),
            ("/time-entries", {"project_id": project["id"], "date": day, "start": "00:00", "end": "00:05"}),
        ):
            assert (await stranger.post(f"{API}{path}", json=body)).status_code == 404, path
        assert (await stranger.post(f"{API}/timer/stop", json={"entry_id": entry["id"]})).status_code == 404
        assert (await stranger.get(f"{API}/journal/{day}")).json()["entry"] is None
        assert (await stranger.get(f"{API}/tags")).json()["items"] == []
        assert (await stranger.get(f"{API}/projects", params={"status": "all"})).json()["items"] == []
        assert (await stranger.get(f"{API}/resources")).json()["items"] == []
        listed = await stranger.get(f"{API}/time-entries", params={"from": day, "to": day})
        assert listed.json()["items"] == []
    assert (await authed.get(f"{API}/journal/{day}")).json()["entry"]["note_md"] == "моё"


async def test_write_limit(authed: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import planner

    monkeypatch.setattr(planner, "WRITES_PER_MINUTE", 3)
    for n in range(3):
        await make_task(authed, f"задача {n}")
    limited = await authed.post(f"{API}/tasks", json={"title": "четвёртая"})
    assert limited.status_code == 429 and code(limited) == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert (await authed.post(f"{API}/areas", json={"name": "x"})).status_code == 429  # one budget for the planner
    assert (await authed.get(f"{API}/views/inbox")).status_code == 200  # reads are not limited
    for _ in range(5):
        assert (await authed.post(f"{API}/parse", json={"text": "завтра зал"})).status_code == 200  # no write
