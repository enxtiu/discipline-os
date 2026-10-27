"""Stage 6: views — today (midnight in the user's timezone), inbox, upcoming, calendar, tag, archive,
counts, search (ru/en stemming + partial words)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import httpx
import pytest

from tests.stage6_helpers import (
    API,
    BANGKOK,
    code,
    complete,
    make_task,
    other_user,
    patch_task,
    today,
    uid,
)

pytestmark = pytest.mark.anyio


def local(day, hh: int, mm: int = 0) -> str:
    return datetime.combine(day, time(hh, mm), tzinfo=BANGKOK).isoformat()


def ids(items) -> list[str]:
    return [item["id"] for item in items]


async def test_today_buckets_follow_the_local_day(authed: httpx.AsyncClient) -> None:
    now = today()
    yesterday, tomorrow = now - timedelta(days=1), now + timedelta(days=1)
    overdue = await make_task(authed, "просрочено", due_date=yesterday.isoformat())
    due = await make_task(authed, "сегодня", due_date=now.isoformat())
    due_and_block = await make_task(
        authed,
        "сегодня с блоком",
        due_date=now.isoformat(),
        block={"date": now.isoformat(), "start": "15:00", "end": "16:00"},
    )
    block_elsewhere = await make_task(
        authed,
        "сегодня, блок завтра",
        due_date=now.isoformat(),
        block={"date": tomorrow.isoformat(), "start": "09:00", "end": "10:00"},
    )
    early = await make_task(authed, "после полуночи", block={"date": now.isoformat(), "start": "00:30", "end": "01:00"})
    late_yesterday = await make_task(
        authed, "вчера поздно", block={"date": yesterday.isoformat(), "start": "23:45", "end": "00:15"}
    )
    done_early = await make_task(authed, "сделано в 00:01")  # just after midnight, never ahead of the clock
    await complete(authed, done_early["id"], completed_at=local(now, 0, 1))
    done_yesterday = await make_task(authed, "сделано вчера 23:50")
    await complete(authed, done_yesterday["id"], completed_at=local(yesterday, 23, 50))
    parent = await make_task(authed, "проект дома", due_date=tomorrow.isoformat())
    sub = await make_task(authed, "шаг сегодня", parent_id=parent["id"], due_date=now.isoformat())

    view = (await authed.get(f"{API}/views/today")).json()
    assert view["date"] == now.isoformat()
    assert ids(view["overdue"]) == [overdue["id"]]
    assert set(ids(view["today"])) == {due["id"], block_elsewhere["id"], sub["id"]}
    assert ids(view["scheduled"]) == [early["id"], due_and_block["id"]]  # by start
    assert ids(view["done"]) == [done_early["id"]]
    assert late_yesterday["id"] not in ids(view["scheduled"])
    step = next(item for item in view["today"] if item["id"] == sub["id"])
    assert (step["parent_title"], step["depth"]) == ("проект дома", 1)
    assert view["counts"] == {"date": now.isoformat(), "inbox": 0, "today": 5, "overdue": 1, "timer_running": False}
    assert view["timer"] is None

    other_day = (await authed.get(f"{API}/views/today", params={"date": yesterday.isoformat()})).json()
    assert other_day["overdue"] == []  # overdue only on your today
    assert ids(other_day["scheduled"]) == [late_yesterday["id"]] and ids(other_day["done"]) == [done_yesterday["id"]]
    tomorrow_view = (await authed.get(f"{API}/views/today", params={"date": tomorrow.isoformat()})).json()
    assert ids(tomorrow_view["today"]) == [parent["id"]] and ids(tomorrow_view["scheduled"]) == [block_elsewhere["id"]]
    bad = await authed.get(f"{API}/views/today", params={"date": "9999-12-31"})
    assert bad.status_code == 422


async def test_today_sorting_and_timer(authed: httpx.AsyncClient) -> None:
    day = today().isoformat()
    low = await make_task(authed, "p4", due_date=day)
    timed_late = await make_task(authed, "p2 21:00", due_date=day, due_time="21:00", priority=2)
    timed_early = await make_task(authed, "p2 08:00", due_date=day, due_time="08:00", priority=2)
    untimed = await make_task(authed, "p2 без времени", due_date=day, priority=2)
    pinned = await make_task(authed, "закреплено", due_date=day, is_pinned=True)
    top = await make_task(authed, "p1", due_date=day, priority=1)
    started = await authed.post(f"{API}/timer/start", json={"id": uid(), "task_id": top["id"]})
    assert started.status_code == 200, started.text
    view = (await authed.get(f"{API}/views/today")).json()
    assert ids(view["today"]) == [
        pinned["id"],
        top["id"],
        timed_early["id"],
        timed_late["id"],
        untimed["id"],
        low["id"],
    ]
    assert view["timer"]["entry"]["task"]["id"] == top["id"] and view["counts"]["timer_running"] is True
    assert view["timer"]["elapsed_s"] >= 0 and view["timer"]["server_now"]


async def test_inbox_counts_and_overdue_flag(authed: httpx.AsyncClient) -> None:
    first = await make_task(authed, "первая")
    second = await make_task(authed, "вторая")
    await make_task(authed, "с датой", due_date=today().isoformat())
    assert ids((await authed.get(f"{API}/views/inbox")).json()["items"]) == [second["id"], first["id"]]  # newest first
    await complete(authed, first["id"])
    assert ids((await authed.get(f"{API}/views/inbox")).json()["items"]) == [second["id"]]
    past = datetime.now(BANGKOK) - timedelta(minutes=5)
    late = await make_task(authed, "уже прошло", due_date=past.date().isoformat(), due_time=past.strftime("%H:%M"))
    assert late["overdue"] is True
    future = datetime.now(BANGKOK) + timedelta(minutes=30)
    ahead = await make_task(
        authed, "ещё впереди", due_date=future.date().isoformat(), due_time=future.strftime("%H:%M")
    )
    assert ahead["overdue"] is False
    counts = (await authed.get(f"{API}/counts")).json()
    assert counts["date"] == today().isoformat() and counts["inbox"] == 1 and counts["timer_running"] is False


async def test_upcoming_with_occurrences(authed: httpx.AsyncClient) -> None:
    now = today()
    daily = await make_task(authed, "витамины", due_date=now.isoformat(), due_time="09:00", recurrence_rule="daily")
    weekly = await make_task(
        authed,
        "уборка",
        due_date=(now + timedelta(days=2)).isoformat(),
        block={"date": (now + timedelta(days=2)).isoformat(), "start": "10:00", "end": "11:30"},
        recurrence_rule="weekly",
    )
    plain = await make_task(authed, "отчёт", due_date=(now + timedelta(days=3)).isoformat())
    span = await make_task(
        authed,
        "доклад",
        due_date=(now + timedelta(days=5)).isoformat(),
        block={"date": (now + timedelta(days=4)).isoformat(), "start": "14:00", "end": "15:00"},
    )
    view = (await authed.get(f"{API}/views/upcoming")).json()
    assert view["from"] == now.isoformat() and view["to"] == (now + timedelta(days=13)).isoformat()
    assert len(view["days"]) == 14 and [day["date"] for day in view["days"]][1] == (now + timedelta(days=1)).isoformat()
    days = {day["date"]: day for day in view["days"]}

    def on(offset: int) -> dict:
        return days[(now + timedelta(days=offset)).isoformat()]

    assert ids(on(0)["tasks"]) == [daily["id"]] and on(0)["occurrences"] == []  # the real instance, no copy
    assert [item["title"] for item in on(1)["occurrences"]] == ["витамины"]
    occurrence = on(1)["occurrences"][0]
    assert occurrence == {
        "task_id": daily["id"],
        "title": "витамины",
        "date": (now + timedelta(days=1)).isoformat(),
        "due_time": "09:00",
        "block": None,
        "priority": 4,
        "project": None,
        "area": None,
        "virtual": True,
    }
    assert all(len(on(n)["occurrences"]) >= 1 for n in range(1, 14))
    assert ids(on(2)["tasks"]) == [weekly["id"]]
    weekly_copy = next(item for item in on(9)["occurrences"] if item["task_id"] == weekly["id"])
    assert weekly_copy["block"] == {"start": "10:00", "end": "11:30", "minutes": 90}
    assert not any(item["task_id"] == weekly["id"] for item in on(2)["occurrences"])
    assert ids(on(3)["tasks"]) == [plain["id"]]
    assert ids(on(4)["tasks"]) == [span["id"]] and ids(on(5)["tasks"]) == [span["id"]]  # block day and due day
    short = (
        await authed.get(f"{API}/views/upcoming", params={"from": (now + timedelta(days=1)).isoformat(), "days": 1})
    ).json()
    assert len(short["days"]) == 1 and short["days"][0]["occurrences"][0]["task_id"] == daily["id"]
    for params in ({"days": 0}, {"days": 32}, {"from": "not-a-date"}):
        assert (await authed.get(f"{API}/views/upcoming", params=params)).status_code == 422


async def test_calendar_range_blocks_and_occurrences(authed: httpx.AsyncClient) -> None:
    now = today()
    first, last = now - timedelta(days=2), now + timedelta(days=10)
    block = await make_task(authed, "блок", block={"date": now.isoformat(), "start": "08:00", "end": "09:00"})
    all_day = await make_task(authed, "весь день", due_date=now.isoformat(), due_time="19:00")
    done = await make_task(authed, "сделано", due_date=now.isoformat())
    await complete(authed, done["id"])
    cancelled = await make_task(authed, "отменено", due_date=now.isoformat())
    await patch_task(authed, cancelled["id"], status="cancelled")
    overdue_series = await make_task(
        authed, "каждые 3 дня", due_date=(now - timedelta(days=2)).isoformat(), recurrence_rule="FREQ=DAILY;INTERVAL=3"
    )
    limited = await make_task(
        authed,
        "до четверга",
        due_date=now.isoformat(),
        recurrence_rule="daily",
        recurrence_until=(now + timedelta(days=3)).isoformat(),
    )
    counted = await make_task(authed, "трижды", due_date=now.isoformat(), recurrence_rule="FREQ=DAILY;COUNT=3")
    response = await authed.get(f"{API}/views/calendar", params={"from": first.isoformat(), "to": last.isoformat()})
    assert response.status_code == 200, response.text
    view = response.json()
    assert (view["from"], view["to"], len(view["days"])) == (first.isoformat(), last.isoformat(), 13)
    days = {day["date"]: day for day in view["days"]}
    today_cell = days[now.isoformat()]
    assert ids(today_cell["blocks"]) == [block["id"]]
    assert {item["id"] for item in today_cell["all_day"]} >= {all_day["id"], done["id"], limited["id"], counted["id"]}
    assert cancelled["id"] not in ids(today_cell["all_day"])
    assert next(item for item in today_cell["all_day"] if item["id"] == done["id"])["status"] == "done"
    assert today_cell["done_count"] == 1

    def virtual(task_id: str) -> list[str]:
        return sorted(day["date"] for day in view["days"] for item in day["occurrences"] if item["task_id"] == task_id)

    # an overdue series: nothing before today can still happen
    assert virtual(overdue_series["id"]) == [(now + timedelta(days=d)).isoformat() for d in (1, 4, 7, 10)]
    assert virtual(limited["id"]) == [(now + timedelta(days=d)).isoformat() for d in (1, 2, 3)]
    assert virtual(counted["id"]) == [(now + timedelta(days=d)).isoformat() for d in (1, 2)]  # COUNT=3 incl. this one
    too_long = await authed.get(
        f"{API}/views/calendar", params={"from": first.isoformat(), "to": (first + timedelta(days=62)).isoformat()}
    )
    assert too_long.status_code == 422 and code(too_long) == "range_too_large"
    exactly = await authed.get(
        f"{API}/views/calendar", params={"from": first.isoformat(), "to": (first + timedelta(days=61)).isoformat()}
    )
    assert exactly.status_code == 200 and len(exactly.json()["days"]) == 62
    backwards = await authed.get(f"{API}/views/calendar", params={"from": last.isoformat(), "to": first.isoformat()})
    assert backwards.status_code == 422
    assert (await authed.get(f"{API}/views/calendar", params={"from": first.isoformat()})).status_code == 422
    huge = await authed.get(f"{API}/views/calendar", params={"from": "9999-12-30", "to": "9999-12-31"})
    assert huge.status_code == 422


async def test_tag_view_and_archive(authed: httpx.AsyncClient) -> None:
    open_task = await make_task(authed, "открыта", tags=["дом"])
    done_task = await make_task(authed, "закрыта", tags=["дом"])
    await complete(authed, done_task["id"])
    tag_id = open_task["tags"][0]["id"]
    view = (await authed.get(f"{API}/views/tag/{tag_id}")).json()
    assert view["tag"]["name"] == "дом" and view["tag"]["open_tasks"] == 1 and ids(view["items"]) == [open_task["id"]]
    with_done = (await authed.get(f"{API}/views/tag/{tag_id}", params={"include_done": True})).json()
    assert ids(with_done["items"]) == [open_task["id"], done_task["id"]]
    async with other_user() as stranger:
        assert (await stranger.get(f"{API}/views/tag/{tag_id}")).status_code == 404
    assert (await authed.get(f"{API}/views/tag/{uid()}")).status_code == 404

    project = (await authed.post(f"{API}/projects", json={"name": "Старый проект"})).json()
    await authed.patch(f"{API}/projects/{project['id']}", json={"status": "completed"})
    area = (await authed.post(f"{API}/areas", json={"name": "Хобби"})).json()
    await authed.patch(f"{API}/areas/{area['id']}", json={"is_archived": True})
    resource = (await authed.post(f"{API}/resources", json={"name": "Статья"})).json()
    await authed.patch(f"{API}/resources/{resource['id']}", json={"is_archived": True})
    cancelled = await make_task(authed, "не нужно")
    await patch_task(authed, cancelled["id"], status="cancelled")
    archive = (await authed.get(f"{API}/views/archive")).json()
    assert ids(archive["projects"]) == [project["id"]] and archive["projects"][0]["status"] == "completed"
    assert ids(archive["areas"]) == [area["id"]] and ids(archive["resources"]) == [resource["id"]]
    assert set(ids(archive["tasks"])) == {done_task["id"], cancelled["id"]}


async def test_search_ru_en_partial_and_other_kinds(authed: httpx.AsyncClient) -> None:
    bought = await make_task(authed, "Купил молоко и хлеб")
    english = await make_task(authed, "Buying groceries for the week")
    tree = await make_task(authed, "Нарядить ёлку")
    noted = await make_task(authed, "Позвонить", notes_md="обсудить ремонт кухни")
    finished = await make_task(authed, "Купить подарок")
    await complete(authed, finished["id"])
    await authed.post(f"{API}/projects", json={"name": "Ремонт кухни", "outcome": "новая плитка"})
    await authed.post(f"{API}/resources", json={"name": "Идеи для кухни", "url": "https://example.com/kitchen"})
    await authed.put(
        f"{API}/journal/{today().isoformat()}", json={"note_md": "Сегодня наконец-то обсудили ремонт кухни с мастером."}
    )

    async def search(q: str, **params) -> dict:
        response = await authed.get(f"{API}/search", params={"q": q, **params})
        assert response.status_code == 200, response.text
        return response.json()

    assert ids((await search("купить"))["tasks"]) == [bought["id"]]  # «купить» finds «Купил» (stemming)
    with_done = await search("купить", include_done=True)
    assert ids(with_done["tasks"]) == [bought["id"], finished["id"]]  # open first
    assert ids((await search("buy"))["tasks"]) == [english["id"]]
    assert ids((await search("мол"))["tasks"]) == [bought["id"]]  # partial word
    assert ids((await search("елку"))["tasks"]) == [tree["id"]]  # ё = е
    kitchen = await search("кухни")
    assert ids(kitchen["tasks"]) == [noted["id"]]  # notes are searched too
    assert [item["name"] for item in kitchen["projects"]] == ["Ремонт кухни"]
    assert [item["name"] for item in kitchen["resources"]] == ["Идеи для кухни"]
    assert kitchen["journal"][0]["date"] == today().isoformat() and "кухни" in kitchen["journal"][0]["snippet"]
    assert (await search("Health"))["areas"][0]["name"] == "Health"
    percent = await search("100%")
    assert percent["tasks"] == []
    for bad in ("к", " к ", "x" * 101):
        assert (await authed.get(f"{API}/search", params={"q": bad})).status_code == 422
    async with other_user() as stranger:
        assert (await stranger.get(f"{API}/search", params={"q": "купить"})).json()["tasks"] == []


async def test_list_views_have_no_n_plus_one(authed: httpx.AsyncClient) -> None:
    """The number of SQL statements of a list view does not grow with the number of tasks."""
    from sqlalchemy import event

    from app.db import engine

    statements: list[str] = []

    def count(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    day = today().isoformat()
    project = (await authed.post(f"{API}/projects", json={"name": "Много"})).json()

    async def fill(n: int) -> None:
        for index in range(n):
            task = await make_task(
                authed,
                f"задача {index}",
                due_date=day,
                project_id=project["id"],
                tags=[f"tag{index % 3}"],
                reminders=[{"lead_minutes": 10}],
            )
            await make_task(authed, f"шаг {index}", parent_id=task["id"], due_date=day)

    async def measure(path: str, params: dict | None = None) -> int:
        statements.clear()
        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            response = await authed.get(f"{API}{path}", params=params)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        assert response.status_code == 200, response.text
        return len(statements)

    views = [
        ("/views/today", None),
        ("/views/upcoming", None),
        ("/views/calendar", {"from": day, "to": day}),
        (f"/projects/{project['id']}", None),
        ("/search", {"q": "задача"}),
    ]
    await fill(3)
    small = [await measure(path, params) for path, params in views]
    await fill(27)
    large = [await measure(path, params) for path, params in views]
    assert large == small, list(zip([path for path, _ in views], small, large, strict=True))
