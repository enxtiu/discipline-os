"""Stage 6: recurring tasks — rules, presets, exactly-once next instances, reopen, subtree, DST."""

from __future__ import annotations

import asyncio
import os
import time as timer
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models import Task
from app.schemas.planner import LATEST_DATE
from app.services.planner import recur
from app.services.recurrence import parse_rule
from tests.stage6_helpers import (
    API,
    BANGKOK,
    code,
    complete,
    get_task,
    make_task,
    patch_task,
    set_timezone,
    today,
    uid,
)

pytestmark = pytest.mark.anyio


def next_weekday(after: date, weekday: int) -> date:
    """First ``weekday`` (Monday = 0) strictly after ``after``."""
    return after + timedelta(days=(weekday - after.weekday() - 1) % 7 + 1)


def next_dst_change(tz: ZoneInfo, after: date) -> date:
    """First day after ``after`` whose UTC offset differs from the day before."""
    day = after
    while True:
        before = datetime(day.year, day.month, day.day, 12, tzinfo=tz).utcoffset()
        following = day + timedelta(days=1)
        if datetime(following.year, following.month, following.day, 12, tzinfo=tz).utcoffset() != before:
            return following
        day = following


async def _series_count(task_id: str) -> int:
    async with SessionLocal() as session:
        root = uuid.UUID(task_id)
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Task)
                .where((Task.id == root) | (Task.recurrence_parent_id == root), Task.parent_id.is_(None))
            )
        )


async def _age(task_id: str, hours: int = 1) -> None:
    """Pretend a task was created a while ago (a later edit then counts as a touch)."""
    async with SessionLocal() as session:
        await session.execute(
            update(Task)
            .where(Task.id == uuid.UUID(task_id))
            .values(
                created_at=Task.created_at - timedelta(hours=hours), updated_at=Task.updated_at - timedelta(hours=hours)
            )
        )
        await session.commit()


async def _age_tree(task_id: str, hours: int = 1) -> None:
    """``_age`` for a next instance and the subtree copied with it (they keep one creation time)."""
    root = uuid.UUID(task_id)
    children = select(Task.id).where(Task.parent_id == root)
    async with SessionLocal() as session:
        await session.execute(
            update(Task)
            .where((Task.id == root) | Task.parent_id.in_([root]) | Task.parent_id.in_(children))
            .values(
                created_at=Task.created_at - timedelta(hours=hours), updated_at=Task.updated_at - timedelta(hours=hours)
            )
        )
        await session.commit()


async def test_rules_presets_and_validation(authed: httpx.AsyncClient) -> None:
    day = (today() + timedelta(days=1)).isoformat()
    expectations = {
        "daily": ("FREQ=DAILY", "daily"),
        "weekdays": ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"),
        "weekly": ("FREQ=WEEKLY", "weekly"),
        "biweekly": ("FREQ=WEEKLY;INTERVAL=2", "biweekly"),
        "Monthly": ("FREQ=MONTHLY", "monthly"),
        "yearly": ("FREQ=YEARLY", "yearly"),
        "RRULE:freq=weekly;byday=th,mo": ("FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
        "FREQ=WEEKLY;INTERVAL=1;WKST=MO": ("FREQ=WEEKLY", "weekly"),
        "FREQ=MONTHLY;BYDAY=-1FR": ("FREQ=MONTHLY;BYDAY=-1FR", "custom"),
    }
    for raw, (rule, preset) in expectations.items():
        task = await make_task(authed, raw, due_date=day, recurrence_rule=raw)
        assert (task["recurrence_rule"], task["recurrence_preset"], task["is_recurring"]) == (rule, preset, True), raw
        assert task["series_id"] == task["id"] and task["series_index"] == 0
    with_until = await make_task(authed, "до конца года", due_date=day, recurrence_rule="FREQ=DAILY;UNTIL=20271231")
    assert (with_until["recurrence_rule"], with_until["recurrence_until"]) == ("FREQ=DAILY", "2027-12-31")
    for bad in (
        "DTSTART:20260101T000000\nRRULE:FREQ=DAILY",
        "FREQ=DAILY;COUNT=1001",
        "FREQ=DAILY;INTERVAL=0",
        "FREQ=DAILY;BYMONTH=13",
        "FREQ=HOURLY",
        "FREQ=DAILY;BYHOUR=9",
        "FREQ=DAILY;INTERVAL=7;BYDAY=MO",
        "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30",
        "FREQ=WEEKLY;BYDAY=MO;BYSETPOS=2",
        "every day",
        "monthly_first",
    ):
        response = await authed.post(f"{API}/tasks", json={"title": "x", "due_date": day, "recurrence_rule": bad})
        assert response.status_code == 422 and code(response) == "invalid_rrule", bad


async def test_non_ascii_digits_are_an_invalid_rule(authed: httpx.AsyncClient) -> None:
    """Unicode digits pass ``str.isdigit`` / ``\\d`` but are not RRULE digits (² and ① even crash ``int``)."""
    day = (today() + timedelta(days=1)).isoformat()
    plain = await make_task(authed, "без повтора", due_date=day)
    for bad in (
        "FREQ=DAILY;COUNT=²",
        "FREQ=DAILY;COUNT=①",
        "FREQ=DAILY;COUNT=٣",
        "FREQ=WEEKLY;INTERVAL=٢",
        "FREQ=MONTHLY;BYMONTHDAY=١٥",
        "FREQ=MONTHLY;BYDAY=٢MO",
        "FREQ=YEARLY;BYMONTH=٣",
        "FREQ=DAILY;UNTIL=٢٠٢٧١٢٣١",
    ):
        created = await authed.post(f"{API}/tasks", json={"title": "x", "due_date": day, "recurrence_rule": bad})
        assert created.status_code == 422 and code(created) == "invalid_rrule", (bad, created.text)
        patched = await authed.patch(f"{API}/tasks/{plain['id']}", json={"recurrence_rule": bad})
        assert patched.status_code == 422 and code(patched) == "invalid_rrule", (bad, patched.text)
    assert (await get_task(authed, plain["id"]))["is_recurring"] is False


async def test_first_occurrence_without_a_date(authed: httpx.AsyncClient) -> None:
    now = today()
    daily = await make_task(authed, "каждый день", recurrence_rule="daily")
    assert daily["due_date"] == now.isoformat() and daily["status"] == "open"
    target = now + timedelta(days=2)
    code_ = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[target.weekday()]
    weekly = await make_task(authed, "по дню недели", recurrence_rule=f"FREQ=WEEKLY;BYDAY={code_}")
    assert weekly["due_date"] == target.isoformat()
    plain = await make_task(authed, "задача")
    made_recurring = await patch_task(authed, plain["id"], recurrence_rule=f"FREQ=WEEKLY;BYDAY={code_}")
    assert made_recurring["task"]["due_date"] == target.isoformat() and made_recurring["task"]["status"] == "open"


async def test_until_in_the_past_has_no_first_occurrence(authed: httpx.AsyncClient) -> None:
    response = await authed.post(
        f"{API}/tasks",
        json={"title": "x", "recurrence_rule": "daily", "recurrence_until": (today() - timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 422 and code(response) == "invalid_rrule"


async def test_series_never_leaves_the_supported_dates(authed: httpx.AsyncClient) -> None:
    """Server-derived dates stay within 2000–2100 (what the API accepts back): past it the series ends."""
    leap = "FREQ=YEARLY;INTERVAL=999;BYMONTH=2;BYMONTHDAY=29"  # first occurrence from today: 4024-02-29
    far = await authed.post(f"{API}/tasks", json={"title": "високосный", "recurrence_rule": leap})
    assert far.status_code == 422 and code(far) == "invalid_rrule", far.text
    plain = await make_task(authed, "без даты")
    made = await authed.patch(f"{API}/tasks/{plain['id']}", json={"recurrence_rule": leap})
    assert made.status_code == 422 and code(made) == "invalid_rrule", made.text

    await set_timezone(authed, "America/Los_Angeles")
    rare = await make_task(
        authed,
        "раз в 991 год",
        due_date="2071-12-31",
        due_time="23:30",
        recurrence_rule="FREQ=YEARLY;INTERVAL=991;BYMONTH=12;BYMONTHDAY=31",
    )
    assert (await complete(authed, rare["id"]))["next_instance"] is None  # 3062: the series ends

    last = await make_task(
        authed,
        "последние дни",
        due_date="2100-12-30",
        due_time="23:30",
        recurrence_rule="daily",
        reminders=[{"lead_minutes": 30}, {"date": "2100-12-31", "time": "08:00"}],
    )
    await make_task(authed, "шаг позже срока", parent_id=last["id"], due_date="2100-12-31")
    nxt = (await complete(authed, last["id"]))["next_instance"]
    assert (nxt["due_date"], nxt["due_time"]) == ("2100-12-31", "23:30")
    assert [item["lead_minutes"] for item in nxt["reminders"]] == [30]  # 2101-01-01 08:00 is not copied
    detail = await get_task(authed, nxt["id"])
    assert [(sub["title"], sub["due_date"]) for sub in detail["subtasks"]] == [("шаг позже срока", None)]
    echo = await authed.patch(f"{API}/tasks/{nxt['id']}", json={"title": "эхо", "due_date": nxt["due_date"]})
    assert echo.status_code == 200, echo.text  # every server-derived date can be sent back
    assert (await complete(authed, nxt["id"]))["next_instance"] is None
    block = await make_task(
        authed, "блок", block={"date": "2100-12-31", "start": "23:00", "end": "01:00"}, recurrence_rule="daily"
    )
    assert (await complete(authed, block["id"]))["next_instance"] is None


async def test_completion_creates_exactly_one_next_instance(authed: httpx.AsyncClient) -> None:
    now = today()
    project = (await authed.post(f"{API}/projects", json={"name": "Здоровье"})).json()
    root = await make_task(
        authed,
        "зарядка",
        notes_md="10 минут",
        priority=2,
        project_id=project["id"],
        tags=["утро"],
        context="дом",
        energy=1,
        estimate_minutes=10,
        due_date=now.isoformat(),
        due_time="07:00",
        recurrence_rule="daily",
    )
    done = await complete(authed, root["id"])
    nxt = done["next_instance"]
    assert done["task"]["status"] == "done" and nxt is not None
    assert (nxt["due_date"], nxt["due_time"]) == ((now + timedelta(days=1)).isoformat(), "07:00")
    assert (nxt["series_id"], nxt["series_index"], nxt["source"], nxt["status"]) == (
        root["id"],
        1,
        "recurrence",
        "open",
    )
    assert (nxt["title"], nxt["notes_md"], nxt["priority"], nxt["context"], nxt["energy"], nxt["estimate_minutes"]) == (
        "зарядка",
        "10 минут",
        2,
        "дом",
        1,
        10,
    )
    assert nxt["project"]["id"] == project["id"] and [tag["name"] for tag in nxt["tags"]] == ["утро"]
    assert nxt["recurrence_rule"] == "FREQ=DAILY" and nxt["is_recurring"] is True

    replay = await complete(authed, root["id"])
    assert replay["next_instance"]["id"] == nxt["id"]  # a replay never creates a second one
    patched = await patch_task(authed, root["id"], status="done")
    assert patched["next_instance"] is None
    assert await _series_count(root["id"]) == 2

    following = await complete(authed, nxt["id"])
    assert following["next_instance"]["series_index"] == 2
    assert following["next_instance"]["series_id"] == root["id"]
    assert await _series_count(root["id"]) == 3


async def test_concurrent_completes(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "полив", due_date=today().isoformat(), recurrence_rule="daily")
    first, second = await asyncio.gather(
        authed.post(f"{API}/tasks/{task['id']}/complete", json={}),
        authed.post(f"{API}/tasks/{task['id']}/complete", json={}),
    )
    assert first.status_code == second.status_code == 200
    ids = {first.json()["next_instance"]["id"], second.json()["next_instance"]["id"]}
    assert len(ids) == 1 and await _series_count(task["id"]) == 2
    via_patch = await make_task(authed, "через patch", due_date=today().isoformat(), recurrence_rule="daily")
    responses = await asyncio.gather(
        *(authed.patch(f"{API}/tasks/{via_patch['id']}", json={"status": "done"}) for _ in range(3))
    )
    assert all(response.status_code == 200 for response in responses)
    assert await _series_count(via_patch["id"]) == 2


async def test_rule_shapes(authed: httpx.AsyncClient) -> None:
    base = today() + timedelta(days=14)
    friday = next_weekday(base, 4)
    weekdays = await make_task(authed, "будни", due_date=friday.isoformat(), recurrence_rule="weekdays")
    assert (await complete(authed, weekdays["id"]))["next_instance"]["due_date"] == (
        friday + timedelta(days=3)
    ).isoformat()

    monday = next_weekday(base, 0)
    mo_th = await make_task(authed, "пн и чт", due_date=monday.isoformat(), recurrence_rule="FREQ=WEEKLY;BYDAY=MO,TH")
    nxt = (await complete(authed, mo_th["id"]))["next_instance"]
    assert nxt["due_date"] == (monday + timedelta(days=3)).isoformat()
    assert (await complete(authed, nxt["id"]))["next_instance"]["due_date"] == (monday + timedelta(days=7)).isoformat()

    biweekly = await make_task(authed, "раз в две недели", due_date=base.isoformat(), recurrence_rule="biweekly")
    assert (await complete(authed, biweekly["id"]))["next_instance"]["due_date"] == (
        base + timedelta(days=14)
    ).isoformat()

    jan31 = date(today().year + 1, 1, 31)
    monthly = await make_task(authed, "31-е", due_date=jan31.isoformat(), recurrence_rule="FREQ=MONTHLY;BYMONTHDAY=31")
    march = (await complete(authed, monthly["id"]))["next_instance"]
    assert march["due_date"] == date(jan31.year, 3, 31).isoformat()  # February has no 31st
    assert (await complete(authed, march["id"]))["next_instance"]["due_date"] == date(jan31.year, 5, 31).isoformat()

    leap = next(year for year in range(today().year + 1, today().year + 9) if year % 4 == 0)
    yearly = await make_task(authed, "29 февраля", due_date=date(leap, 2, 29).isoformat(), recurrence_rule="yearly")
    assert (await complete(authed, yearly["id"]))["next_instance"]["due_date"] == date(leap + 4, 2, 29).isoformat()
    plain_yearly = await make_task(authed, "годовщина", due_date=date(leap, 6, 1).isoformat(), recurrence_rule="yearly")
    assert (await complete(authed, plain_yearly["id"]))["next_instance"]["due_date"] == date(leap + 1, 6, 1).isoformat()


async def test_overdue_until_count_and_cancel(authed: httpx.AsyncClient) -> None:
    now = today()
    overdue = await make_task(
        authed, "просрочено", due_date=(now - timedelta(days=5)).isoformat(), recurrence_rule="daily"
    )
    assert overdue["overdue"] is True
    nxt = (await complete(authed, overdue["id"]))["next_instance"]
    assert nxt["due_date"] == (now + timedelta(days=1)).isoformat()  # skips to after today
    weekday_code = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[(now - timedelta(days=1)).weekday()]
    weekly = await make_task(
        authed,
        "раз в неделю",
        due_date=(now - timedelta(days=22)).isoformat(),
        recurrence_rule=f"FREQ=WEEKLY;BYDAY={weekday_code}",
    )
    assert (await complete(authed, weekly["id"]))["next_instance"]["due_date"] == (now + timedelta(days=6)).isoformat()

    last = now + timedelta(days=3)
    until = await make_task(
        authed, "до даты", due_date=last.isoformat(), recurrence_rule="daily", recurrence_until=last.isoformat()
    )
    assert (await complete(authed, until["id"]))["next_instance"] is None  # until ends the series
    counted = await make_task(authed, "дважды", due_date=now.isoformat(), recurrence_rule="FREQ=DAILY;COUNT=2")
    second = (await complete(authed, counted["id"]))["next_instance"]
    assert second is not None and (await complete(authed, second["id"]))["next_instance"] is None

    skipped = await make_task(authed, "пропустить", due_date=now.isoformat(), recurrence_rule="daily")
    cancelled = await patch_task(authed, skipped["id"], status="cancelled")
    assert cancelled["task"]["status"] == "cancelled" and cancelled["next_instance"] is not None  # a skip moves on
    stopped = await make_task(authed, "больше не повторять", due_date=now.isoformat(), recurrence_rule="daily")
    plain = await patch_task(authed, stopped["id"], recurrence_rule=None)
    assert plain["task"]["is_recurring"] is False and plain["task"]["recurrence_preset"] is None
    assert (await complete(authed, stopped["id"]))["next_instance"] is None


async def test_reopen_removes_only_an_untouched_next_instance(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "читать", due_date=today().isoformat(), recurrence_rule="daily")
    nxt = (await complete(authed, task["id"]))["next_instance"]
    reopened = await authed.post(f"{API}/tasks/{task['id']}/reopen")
    assert reopened.status_code == 200
    body = reopened.json()
    assert body["task"]["status"] == "open" and body["task"]["completed_at"] is None
    assert body["removed_next_instance_id"] == nxt["id"]
    assert (await authed.get(f"{API}/tasks/{nxt['id']}")).status_code == 404
    again = (await complete(authed, task["id"]))["next_instance"]
    assert again["id"] != nxt["id"] and again["series_index"] == 1
    idempotent = (await authed.post(f"{API}/tasks/{again['id']}/reopen")).json()
    assert idempotent["removed_next_instance_id"] is None and idempotent["task"]["status"] == "open"

    await _age(again["id"])
    await patch_task(authed, again["id"], title="читать (изменено)")  # a touch
    via_patch = await patch_task(authed, task["id"], status="open")
    assert via_patch["removed_next_instance_id"] is None
    assert (await get_task(authed, again["id"]))["title"] == "читать (изменено)"
    redone = await complete(authed, task["id"])
    assert redone["next_instance"]["id"] == again["id"] and await _series_count(task["id"]) == 2


async def _prepared_series(authed: httpx.AsyncClient, title: str) -> tuple[dict, dict]:
    """A weekly series with one step, completed: (the done task, its next instance, aged as if made earlier)."""
    task = await make_task(authed, title, due_date=today().isoformat(), recurrence_rule="weekly")
    await make_task(authed, f"{title}: шаг", parent_id=task["id"])
    nxt = (await complete(authed, task["id"]))["next_instance"]
    await _age_tree(nxt["id"])
    return task, nxt


async def test_reopen_keeps_a_next_instance_the_user_prepared(authed: httpx.AsyncClient) -> None:
    """A subtask, a tag, a reminder, tracked time, a removed or a moved-in step all count as touching the
    next instance: reopening the completed one used to delete it with the user's additions."""

    async def reopen_keeps(task: dict, nxt: dict) -> None:
        reopened = await authed.post(f"{API}/tasks/{task['id']}/reopen")
        assert reopened.status_code == 200 and reopened.json()["removed_next_instance_id"] is None
        assert (await authed.get(f"{API}/tasks/{nxt['id']}")).status_code == 200

    task, nxt = await _prepared_series(authed, "подзадача")
    step = await make_task(authed, "подготовить слайды", parent_id=nxt["id"])
    await reopen_keeps(task, nxt)
    assert (await get_task(authed, step["id"]))["parent_id"] == nxt["id"]

    task, nxt = await _prepared_series(authed, "тег")
    await patch_task(authed, nxt["id"], tags=["работа"])
    await reopen_keeps(task, nxt)

    task, nxt = await _prepared_series(authed, "напоминание")
    await patch_task(authed, nxt["id"], reminders=[{"lead_minutes": 30}])
    await reopen_keeps(task, nxt)

    task, nxt = await _prepared_series(authed, "время")
    copied_step = (await get_task(authed, nxt["id"]))["subtasks"][0]
    entry = {"task_id": copied_step["id"], "date": (today() - timedelta(days=1)).isoformat()}
    logged = await authed.post(f"{API}/time-entries", json={**entry, "start": "10:00", "end": "10:30"})
    assert logged.status_code == 201, logged.text
    await reopen_keeps(task, nxt)

    task, nxt = await _prepared_series(authed, "удалённый шаг")
    copied_step = (await get_task(authed, nxt["id"]))["subtasks"][0]
    assert (await authed.delete(f"{API}/tasks/{copied_step['id']}")).status_code == 204
    await reopen_keeps(task, nxt)

    task, nxt = await _prepared_series(authed, "перенесённая")
    loose = await make_task(authed, "отдельная задача")
    await patch_task(authed, loose["id"], parent_id=nxt["id"])
    await reopen_keeps(task, nxt)

    task, nxt = await _prepared_series(authed, "без изменений")  # the edit form saved what was there
    await patch_task(authed, nxt["id"], tags=[], reminders=[], title=nxt["title"])
    reopened = (await authed.post(f"{API}/tasks/{task['id']}/reopen")).json()
    assert reopened["removed_next_instance_id"] == nxt["id"]


async def test_reopen_racing_a_new_subtask_never_loses_it(authed: httpx.AsyncClient) -> None:
    """A subtask added to the next instance while the completed one is reopened: kept, or refused with 404."""
    for _ in range(8):
        task = await make_task(authed, "серия", due_date=today().isoformat(), recurrence_rule="daily")
        nxt = (await complete(authed, task["id"]))["next_instance"]
        step_id = uid()
        created, reopened = await asyncio.gather(
            authed.post(f"{API}/tasks", json={"id": step_id, "title": "шаг", "parent_id": nxt["id"]}),
            authed.post(f"{API}/tasks/{task['id']}/reopen"),
        )
        assert reopened.status_code == 200, reopened.text
        if created.status_code == 201:
            assert reopened.json()["removed_next_instance_id"] is None
            assert (await authed.get(f"{API}/tasks/{step_id}")).status_code == 200
        else:
            assert created.status_code == 404, created.text
            assert reopened.json()["removed_next_instance_id"] == nxt["id"]


async def test_subtree_reminders_and_times_are_copied(authed: httpx.AsyncClient) -> None:
    day = today() + timedelta(days=2)
    root = await make_task(
        authed,
        "уборка",
        due_date=day.isoformat(),
        due_time="18:00",
        block={"date": day.isoformat(), "start": "17:00", "end": "18:30"},
        recurrence_rule="weekly",
        reminders=[{"lead_minutes": 30}, {"date": (day - timedelta(days=1)).isoformat(), "time": "20:00"}],
    )
    kitchen = await make_task(authed, "кухня", parent_id=root["id"], sort_order=1, tags=["дом"])
    await make_task(authed, "плита", parent_id=kitchen["id"])
    await make_task(authed, "ванная", parent_id=root["id"], sort_order=2)
    await complete(authed, kitchen["id"])
    nxt = (await complete(authed, root["id"]))["next_instance"]
    week = day + timedelta(days=7)
    assert (nxt["due_date"], nxt["due_time"]) == (week.isoformat(), "18:00")
    assert (nxt["block"]["date"], nxt["block"]["start"], nxt["block"]["end"], nxt["block"]["minutes"]) == (
        week.isoformat(),
        "17:00",
        "18:30",
        90,
    )
    assert nxt["status"] == "scheduled" and nxt["subtask_counts"] == {"done": 0, "total": 2}
    reminders = sorted(nxt["reminders"], key=lambda item: item["remind_at"])
    assert (reminders[0]["date"], reminders[0]["time"]) == ((week - timedelta(days=1)).isoformat(), "20:00")
    assert reminders[1]["lead_minutes"] == 30
    assert datetime.fromisoformat(reminders[1]["remind_at"]) == datetime(
        week.year, week.month, week.day, 10, 30, tzinfo=UTC
    )
    detail = await get_task(authed, nxt["id"])
    assert [(sub["title"], sub["status"]) for sub in detail["subtasks"]] == [("кухня", "open"), ("ванная", "open")]
    assert detail["subtasks"][0]["subtasks"][0]["title"] == "плита"
    assert [tag["name"] for tag in detail["subtasks"][0]["tags"]] == ["дом"]
    assert detail["subtasks"][0]["id"] != kitchen["id"] and detail["subtasks"][0]["source"] == "recurrence"

    moved = await patch_task(authed, nxt["id"], due_date=(week + timedelta(days=1)).isoformat())
    relative = next(item for item in moved["task"]["reminders"] if item["lead_minutes"] == 30)
    shifted = week + timedelta(days=1)
    assert datetime.fromisoformat(relative["remind_at"]) == datetime(
        shifted.year, shifted.month, shifted.day, 10, 30, tzinfo=UTC
    )  # recomputed on the due change


async def test_block_only_series_and_dst(authed: httpx.AsyncClient) -> None:
    day = today() + timedelta(days=3)
    block_only = await make_task(
        authed, "йога", block={"date": day.isoformat(), "start": "07:00", "end": "08:00"}, recurrence_rule="daily"
    )
    assert block_only["due_date"] is None
    nxt = (await complete(authed, block_only["id"]))["next_instance"]
    assert nxt["due_date"] is None
    assert (nxt["block"]["date"], nxt["block"]["start"]) == ((day + timedelta(days=1)).isoformat(), "07:00")

    await set_timezone(authed, "Europe/Berlin")
    berlin = ZoneInfo("Europe/Berlin")
    switch = next_dst_change(berlin, datetime.now(berlin).date() + timedelta(days=2))
    eve = switch - timedelta(days=1)
    task = await make_task(authed, "08:00 всегда", due_date=eve.isoformat(), due_time="08:00", recurrence_rule="daily")
    nxt = (await complete(authed, task["id"]))["next_instance"]
    assert (nxt["due_date"], nxt["due_time"]) == (switch.isoformat(), "08:00")
    before = datetime.fromisoformat(task["due_at"])
    after = datetime.fromisoformat(nxt["due_at"])
    expected_gap = timedelta(days=1) - (
        datetime(switch.year, switch.month, switch.day, 12, tzinfo=berlin).utcoffset()
        - datetime(eve.year, eve.month, eve.day, 12, tzinfo=berlin).utcoffset()
    )
    assert after - before == expected_gap  # same wall clock, 23 or 25 hours later


async def test_completed_at_and_instance_of_instance(authed: httpx.AsyncClient) -> None:
    task = await make_task(authed, "отчёт", due_date=today().isoformat(), recurrence_rule="weekly")
    # just after the local midnight (the UTC day is still yesterday) and never in the future
    late = datetime.now(BANGKOK).replace(hour=0, minute=1, second=0, microsecond=0)
    done = await complete(authed, task["id"], completed_at=late.isoformat())
    assert done["task"]["completed_day"] == today().isoformat()
    assert datetime.fromisoformat(done["task"]["completed_at"]) == late.astimezone(UTC)
    future = await authed.post(
        f"{API}/tasks/{done['next_instance']['id']}/complete",
        json={"completed_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
    )
    assert future.status_code == 422
    nan = await authed.post(f"{API}/tasks/{task['id']}/complete", json={"completed_at": "9999-12-31T23:59:59Z"})
    assert nan.status_code == 422


# --------------------------------------------------------------------------- #
# expansion cost (dateutil walks every period from dtstart)
# --------------------------------------------------------------------------- #
SHAPES = (
    "FREQ=DAILY",
    "FREQ=DAILY;INTERVAL=3",
    "FREQ=DAILY;INTERVAL=17",
    "FREQ=DAILY;BYDAY=MO,WE,FR",
    "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=29",
    "FREQ=WEEKLY",
    "FREQ=WEEKLY;INTERVAL=2",
    "FREQ=WEEKLY;INTERVAL=3;BYDAY=MO,SU",
    "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,SA;WKST=SU",
    "weekdays",
    "FREQ=MONTHLY",
    "FREQ=MONTHLY;INTERVAL=5",
    "FREQ=MONTHLY;BYMONTHDAY=31",
    "FREQ=MONTHLY;BYMONTHDAY=-1",
    "FREQ=MONTHLY;BYDAY=-1FR",
    "FREQ=MONTHLY;INTERVAL=2;BYDAY=2TU",
    "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1",
    "FREQ=MONTHLY;BYMONTH=1,7",
    "FREQ=YEARLY",
    "FREQ=YEARLY;INTERVAL=4",
    "FREQ=YEARLY;BYMONTH=3",
    "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
    "FREQ=YEARLY;BYDAY=20MO",
    "FREQ=YEARLY;BYMONTHDAY=15",
    "FREQ=YEARLY;INTERVAL=3;BYMONTH=2;BYMONTHDAY=29",
)


def naive_between(rule, anchor, *, first, last, after, until=None, remaining=None):
    """The plain expansion: dateutil from the series' own anchor through every period."""
    lo = after + timedelta(days=1)
    hi = min(last, until or LATEST_DATE, LATEST_DATE)
    if hi < lo or hi < first or (remaining is not None and remaining <= 0):
        return []
    bound = datetime.combine(hi, time.max)
    expansion = parse_rule(f"{rule.expansion_text};UNTIL={bound:%Y%m%dT%H%M%S}", anchor)
    dates = []
    for occurrence in expansion.between(max(anchor, datetime.combine(lo, time.min)), bound, inc=True):
        if occurrence.date() < lo or occurrence <= anchor:
            continue
        if remaining is not None:
            if remaining <= 0:
                break
            remaining -= 1
        if occurrence.date() >= first:
            dates.append(occurrence.date())
    return dates


async def test_expansion_matches_the_plain_expansion() -> None:
    """Starting the expansion near the window yields exactly the occurrences of the series' own anchor."""
    anchors = (datetime(2004, 1, 31, 7, 30), datetime(2008, 2, 29), datetime(2011, 6, 18, 23, 45))
    for raw in SHAPES:
        rule = recur.validate_rule(raw)
        for anchor in anchors:
            for gap, span in ((40, 20), (400, 62), (1500, 62), (3000, 31)):
                after = anchor.date() + timedelta(days=gap)
                for first, remaining in ((after - timedelta(days=9), None), (after + timedelta(days=200), None),
                                         (after, 3), (after + timedelta(days=100), 40)):  # fmt: skip
                    last = first + timedelta(days=span)
                    window = {"first": first, "last": last, "after": after, "remaining": remaining}
                    assert recur.occurrences_between(rule, anchor, **window) == naive_between(rule, anchor, **window), (
                        raw,
                        anchor,
                        window,
                    )
                today = anchor.date() + timedelta(days=gap)
                expected = parse_rule(
                    f"{rule.expansion_text};UNTIL={datetime.combine(LATEST_DATE, time.max):%Y%m%dT%H%M%S}", anchor
                ).after(datetime.combine(today, time.max))
                assert recur.next_after(rule, anchor, today) == expected, (raw, anchor, today)


def _busy() -> bool:
    try:
        return os.getloadavg()[0] > (os.cpu_count() or 1)
    except OSError:  # pragma: no cover - no load average on this platform
        return False


async def test_expansion_cost_does_not_grow_with_the_anchor_age() -> None:
    """A series anchored in 2000 viewed in 2100 used to cost one dateutil iteration per day since 2000."""
    anchor = datetime(2000, 1, 1, 7, 0)
    window = {"first": date(2100, 11, 1), "last": date(2100, 12, 31), "after": date(2026, 10, 27)}
    slow: list[tuple[str, float]] = []
    for raw in SHAPES:
        rule = recur.validate_rule(raw)
        best = float("inf")
        for _ in range(2):  # the better of two runs: a shared box has hiccups
            started = timer.perf_counter()
            dates = recur.occurrences_between(rule, anchor, **window)
            recur.next_after(rule, anchor, date(2100, 11, 1))
            best = min(best, timer.perf_counter() - started)
        assert all(window["first"] <= day <= window["last"] for day in dates), raw
        if best > 0.05:
            slow.append((raw, round(best * 1000)))
    if slow and _busy():
        pytest.skip(f"the box is busy, timings not meaningful: {slow}")
    assert not slow, f"expansions slower than 50 ms (ms): {slow}"
    daily = recur.occurrences_between(recur.validate_rule("daily"), anchor, **window)
    assert daily == [date(2100, 11, 1) + timedelta(days=n) for n in range(61)]


async def test_calendar_of_an_old_series_far_ahead(authed: httpx.AsyncClient) -> None:
    """The virtual occurrences of a series overdue since 2000 are right in a calendar of 2100."""
    daily = await make_task(authed, "с 2000 года", due_date="2000-01-01", due_time="07:00", recurrence_rule="daily")
    monthly = await make_task(authed, "последний день", due_date="2000-01-31", recurrence_rule="FREQ=MONTHLY")
    view = await authed.get(f"{API}/views/calendar", params={"from": "2100-11-01", "to": "2100-12-31"})
    assert view.status_code == 200, view.text

    def virtual(task_id: str) -> list[str]:
        return [day["date"] for day in view.json()["days"] for item in day["occurrences"] if item["task_id"] == task_id]

    assert virtual(daily["id"]) == [(date(2100, 11, 1) + timedelta(days=n)).isoformat() for n in range(61)]
    assert virtual(monthly["id"]) == ["2100-12-31"]  # November has no 31st


async def test_expansion_starts_near_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load-independent: dateutil is started close to the window, not at a series anchor from 2000."""
    starts: list[datetime] = []
    original = recur.parse_rule

    def spy(text: str, dtstart: datetime):
        starts.append(dtstart)
        return original(text, dtstart)

    monkeypatch.setattr(recur, "parse_rule", spy)
    anchor = datetime(2000, 1, 1, 7, 0)
    for raw in SHAPES:
        rule = recur.validate_rule(raw)
        starts.clear()
        recur.occurrences_between(
            rule, anchor, first=date(2100, 11, 1), last=date(2100, 12, 31), after=date(2026, 10, 27)
        )
        recur.next_after(rule, anchor, date(2100, 11, 1))
        assert starts and min(starts) >= datetime(2095, 1, 1), (raw, starts)  # within INTERVAL periods of it
        starts.clear()
        recur.occurrences_between(  # COUNT counts from the day after today: start near today, not in 2000
            rule, anchor, first=date(2100, 11, 1), last=date(2100, 12, 31), after=date(2026, 10, 27), remaining=5
        )
        assert starts and min(starts) >= datetime(2021, 1, 1), (raw, starts)
