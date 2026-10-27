"""Stage 6: recurring tasks — rules, presets, exactly-once next instances, reopen, subtree, DST."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models import Task
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
