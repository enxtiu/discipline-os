"""Stage 6: quick add (`/tasks/quick`) and `/parse`.

Two layers:

* **Grammar tests** run the real parser (``services.task_parser``, implemented on the
  parser branch) end to end through the endpoints — they describe the contract's
  quick-add grammar and hold once that branch is merged.
* **Mapping tests** replace the parser with a fixed ``ParsedTask`` so the planner's
  mapping (defaults, fallbacks, bounds) is checked independently of the grammar.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Task
from app.services.task_parser import KnownContainer, ParsedTask, Token
from tests.stage6_helpers import API, BANGKOK, code, make_task, today, uid

pytestmark = pytest.mark.anyio


def at(day: date, hh: int, mm: int = 0) -> str:
    return datetime.combine(day, time(hh, mm), tzinfo=BANGKOK).isoformat()


async def _quick(client: httpx.AsyncClient, text: str, status: int = 201, **body) -> dict:
    response = await client.post(f"{API}/tasks/quick", json={"text": text, **body})
    assert response.status_code == status, response.text
    return response.json()


def _check_offsets(text: str, tokens: list[dict]) -> None:
    for token in tokens:  # BMP text: UTF-16 offsets equal Python indices
        assert text[token["start"] : token["end"]] == token["text"], token


# --------------------------------------------------------------------------- #
# grammar (real parser)
# --------------------------------------------------------------------------- #
async def test_quick_add_ru_line(authed: httpx.AsyncClient) -> None:
    now = today()
    text = "завтра 18:00 зал #здоровье p2"
    body = await _quick(authed, text, captured_at=at(now, 10))
    task, parsed = body["task"], body["parsed"]
    assert (task["title"], task["due_date"], task["due_time"], task["priority"], task["source"]) == (
        "зал",
        (now + timedelta(days=1)).isoformat(),
        "18:00",
        2,
        "quick_add",
    )
    assert [tag["name"] for tag in task["tags"]] == ["здоровье"] and task["status"] == "open"
    assert (parsed["title"], parsed["due_date"], parsed["due_time"], parsed["priority"], parsed["tags"]) == (
        "зал",
        (now + timedelta(days=1)).isoformat(),
        "18:00",
        2,
        ["здоровье"],
    )
    assert {(token["kind"], token["text"]) for token in parsed["tokens"]} == {
        ("date", "завтра"),
        ("time", "18:00"),
        ("tag", "#здоровье"),
        ("priority", "p2"),
    }
    _check_offsets(text, parsed["tokens"])


async def test_quick_add_project_block_reminder_recurrence(authed: httpx.AsyncClient) -> None:
    now = today()
    project = (await authed.post(f"{API}/projects", json={"name": "Ремонт"})).json()
    in_project = await _quick(authed, "купить плитку +Ремонт @магазин", captured_at=at(now, 10))
    assert in_project["task"]["project"]["id"] == project["id"] and in_project["task"]["context"] == "магазин"
    assert in_project["parsed"]["project"] == {"id": project["id"], "name": "Ремонт", "kind": "project"}
    assert in_project["task"]["title"] == "купить плитку"
    unknown = await _quick(authed, "позвонить +Неизвестно", captured_at=at(now, 10))
    assert unknown["parsed"]["project"] is None and "+Неизвестно" in unknown["task"]["title"]

    block = await _quick(authed, "завтра 18:00-19:30 бег", captured_at=at(now, 10))
    assert block["task"]["block"]["date"] == (now + timedelta(days=1)).isoformat()
    assert (block["task"]["block"]["start"], block["task"]["block"]["end"], block["task"]["block"]["minutes"]) == (
        "18:00",
        "19:30",
        90,
    )
    assert block["task"]["due_time"] is None and block["task"]["status"] == "scheduled"

    reminder = await _quick(authed, "завтра 18:00 врач напомни за 15 мин", captured_at=at(now, 10))
    assert [item["lead_minutes"] for item in reminder["task"]["reminders"]] == [15]
    assert reminder["parsed"]["reminders"] == [{"lead_minutes": 15}]
    tomorrow = now + timedelta(days=1)
    expected = datetime.combine(tomorrow, time(17, 45), tzinfo=BANGKOK)
    assert datetime.fromisoformat(reminder["task"]["reminders"][0]["remind_at"]) == expected

    estimate = await _quick(authed, "отчёт 30 мин", captured_at=at(now, 10))
    assert (estimate["task"]["estimate_minutes"], estimate["task"]["title"]) == (30, "отчёт")

    weekly = await _quick(authed, "каждый понедельник спортзал", captured_at=at(now, 10))
    assert weekly["task"]["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=MO" and weekly["task"]["is_recurring"] is True
    monday = now + timedelta(days=(0 - now.weekday()) % 7)  # on/after today
    assert weekly["task"]["due_date"] == monday.isoformat()
    assert weekly["parsed"]["recurrence_preset"] == "custom" and weekly["parsed"]["due_date"] is None


async def test_quick_add_en_line_and_title_fallback(authed: httpx.AsyncClient) -> None:
    now = today()
    english = await _quick(authed, "tomorrow 6pm gym #Health p1", captured_at=at(now, 10))
    task = english["task"]
    assert (task["title"], task["due_date"], task["due_time"], task["priority"]) == (
        "gym",
        (now + timedelta(days=1)).isoformat(),
        "18:00",
        1,
    )
    assert [tag["name"] for tag in task["tags"]] == ["health"]  # the existing tag, matched case-insensitively
    only_tokens = await _quick(authed, "завтра p1", captured_at=at(now, 10))
    assert only_tokens["task"]["title"] == "завтра p1"  # nothing left: the raw text is the title
    assert (only_tokens["task"]["due_date"], only_tokens["task"]["priority"]) == (
        (now + timedelta(days=1)).isoformat(),
        1,
    )


async def test_quick_add_recurrence_keeps_the_typed_time(authed: httpx.AsyncClient) -> None:
    now = today()
    monday = now + timedelta(days=(0 - now.weekday()) % 7)  # first occurrence on/after today
    gym = await _quick(authed, "каждый понедельник в 9 спортзал", captured_at=at(now, 10))
    assert gym["parsed"]["due_date"] is None and gym["parsed"]["due_time"] == "09:00"  # the parser invents no date
    task = gym["task"]
    assert (task["title"], task["due_date"], task["due_time"], task["recurrence_rule"]) == (
        "спортзал",
        monday.isoformat(),
        "09:00",
        "FREQ=WEEKLY;BYDAY=MO",
    )
    yoga = await _quick(authed, "каждый день 07:00-07:30 йога", captured_at=at(now, 10))
    block = yoga["task"]["block"]
    assert (block["date"], block["start"], block["end"], yoga["task"]["due_date"]) == (
        now.isoformat(),
        "07:00",
        "07:30",
        None,
    )
    assert (yoga["task"]["recurrence_preset"], yoga["task"]["status"]) == ("daily", "scheduled")
    rent = await _quick(authed, "каждое 1 число оплатить аренду", captured_at=at(now, 10))
    first = now if now.day == 1 else (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    assert (rent["task"]["recurrence_rule"], rent["task"]["recurrence_preset"], rent["task"]["due_date"]) == (
        "FREQ=MONTHLY;BYMONTHDAY=1",
        "custom",
        first.isoformat(),
    )
    run = await _quick(authed, "в 18 на 1.5 ч бег", captured_at=at(now, 10))
    assert run["task"]["block"]["date"] == now.isoformat() and run["task"]["block"]["minutes"] == 90
    assert run["task"]["estimate_minutes"] is None and run["task"]["title"] == "бег"


async def test_parse_endpoint(authed: httpx.AsyncClient) -> None:
    now = today()
    text = "🔥 завтра зал"
    response = await authed.post(f"{API}/parse", json={"text": text, "captured_at": at(now, 10)})
    assert response.status_code == 200, response.text
    parsed = response.json()
    date_tokens = [token for token in parsed["tokens"] if token["kind"] == "date"]
    assert date_tokens, parsed
    date_token = date_tokens[0]
    assert (date_token["start"], date_token["end"], date_token["text"]) == (3, 9, "завтра")  # UTF-16 offsets
    assert parsed["due_date"] == (now + timedelta(days=1)).isoformat() and parsed["title"] == "🔥 зал"
    yesterday = await authed.post(
        f"{API}/parse", json={"text": "завтра зал", "captured_at": at(now - timedelta(days=1), 10)}
    )
    assert yesterday.json()["due_date"] == now.isoformat()  # relative to the capture time
    assert (await authed.get(f"{API}/views/inbox")).json()["items"] == []  # nothing saved


# --------------------------------------------------------------------------- #
# mapping (parser replaced)
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_parser(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []
    result: dict[str, ParsedTask] = {}

    def fake(text: str, *, now: datetime, week_start: int = 1, containers=()) -> ParsedTask:
        calls.append({"text": text, "now": now, "week_start": week_start, "containers": list(containers)})
        parsed = result.get("value") or ParsedTask(title=text)
        return parsed

    from app.services.planner import quick_add

    monkeypatch.setattr(quick_add, "parse_task_text", fake)
    return calls, result


async def test_mapping_of_every_parsed_field(authed: httpx.AsyncClient, fake_parser) -> None:
    calls, result = fake_parser
    now = today()
    project = (await authed.post(f"{API}/projects", json={"name": "Сад"})).json()
    day = now + timedelta(days=2)
    text = "полить сад"
    result["value"] = ParsedTask(
        title="полить",
        due_date=day,
        due_time=None,
        block_date=day,
        block_start=time(7, 0),
        block_end=time(7, 30),
        estimate_minutes=30,
        priority=3,
        tags=["сад", "#bad tag", "Сад"],
        container=KnownContainer(id=project["id"], name="Сад", kind="project"),
        context="улица",
        recurrence_rule="FREQ=WEEKLY;BYDAY=MO,TH",
        recurrence_preset="custom",
        reminder_leads=[10, 10, 20000],
        tokens=[Token(start=7, end=10, kind="project", text="сад")],
    )
    body = await _quick(authed, text, captured_at=at(now, 9, 30))
    task, parsed = body["task"], body["parsed"]
    assert (task["title"], task["due_date"], task["due_time"], task["estimate_minutes"], task["priority"]) == (
        "полить",
        day.isoformat(),
        None,
        30,
        3,
    )
    assert (task["block"]["date"], task["block"]["start"], task["block"]["end"]) == (day.isoformat(), "07:00", "07:30")
    assert task["project"]["id"] == project["id"] and task["context"] == "улица" and task["status"] == "scheduled"
    assert [tag["name"] for tag in task["tags"]] == ["сад"]  # an invalid tag is dropped, duplicates merged
    assert task["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=MO,TH"
    assert [item["lead_minutes"] for item in task["reminders"]] == [10]  # out-of-range lead dropped
    assert parsed["tokens"] == [{"start": 7, "end": 10, "kind": "project", "text": "сад"}]
    assert parsed["block"] == {"date": day.isoformat(), "start": "07:00", "end": "07:30"}
    assert parsed["recurrence_preset"] == "custom" and parsed["reminders"] == [{"lead_minutes": 10}, {"lead_minutes": 10},
                                                                                {"lead_minutes": 20000}]  # fmt: skip
    call = calls[-1]
    assert call["now"].utcoffset() == timedelta(hours=7) and call["now"].hour == 9 and call["now"].minute == 30
    assert call["week_start"] == 1
    kinds = [(container.kind, container.name) for container in call["containers"]]
    assert ("project", "Сад") in kinds and kinds.index(("project", "Сад")) < kinds.index(("area", "Health"))


async def test_mapping_defaults_fallbacks_and_replays(authed: httpx.AsyncClient, fake_parser) -> None:
    _, result = fake_parser
    now = today()
    project = (await authed.post(f"{API}/projects", json={"name": "Дом"})).json()
    parent = await make_task(authed, "переезд")
    view_day = now + timedelta(days=4)
    # nothing recognised: the defaults decide
    plain = await _quick(
        authed,
        "  купить лампочки  ",
        defaults={"project_id": project["id"], "due_date": view_day.isoformat(), "parent_id": parent["id"]},
    )
    task = plain["task"]
    assert (task["title"], task["project"]["id"], task["due_date"], task["parent_id"], task["depth"]) == (
        "купить лампочки",
        project["id"],
        view_day.isoformat(),
        parent["id"],
        1,
    )
    # a time typed without a date lands on the day the user is looking at
    result["value"] = ParsedTask(
        title="созвон", due_date=now, due_time=time(15, 0), tokens=[Token(start=0, end=5, kind="time", text="15:00")]
    )
    timed = await _quick(authed, "15:00 созвон", defaults={"due_date": view_day.isoformat()})
    assert (timed["task"]["due_date"], timed["task"]["due_time"]) == (view_day.isoformat(), "15:00")
    # an explicit date token wins over the default
    result["value"] = ParsedTask(
        title="созвон", due_date=now + timedelta(days=1), tokens=[Token(start=0, end=6, kind="date", text="завтра")]
    )
    explicit = await _quick(authed, "завтра созвон", defaults={"due_date": view_day.isoformat()})
    assert explicit["task"]["due_date"] == (now + timedelta(days=1)).isoformat()
    # a recurrence decides its own first day, the view's default day does not
    result["value"] = ParsedTask(
        title="планёрка",
        due_time=time(9, 30),
        recurrence_rule="FREQ=WEEKLY;BYDAY=MO",
        recurrence_preset="custom",
        tokens=[Token(start=0, end=3, kind="recurrence", text="пн")],
    )
    weekly = await _quick(authed, "пн 9:30 планёрка", defaults={"due_date": view_day.isoformat()})
    monday = now + timedelta(days=(0 - now.weekday()) % 7)
    assert (weekly["task"]["due_date"], weekly["task"]["due_time"]) == (monday.isoformat(), "09:30")
    # empty title: the raw text; an over-long one is cut to 300; bad values are dropped
    result["value"] = ParsedTask(title="", priority=9, estimate_minutes=5000, recurrence_rule="FREQ=SECONDLY")
    fallback = await _quick(authed, "p9 " + "я" * 400)
    assert len(fallback["task"]["title"]) == 300 and fallback["task"]["title"].startswith("p9 я")
    assert (fallback["task"]["priority"], fallback["task"]["estimate_minutes"], fallback["task"]["is_recurring"]) == (
        4,
        None,
        False,
    )
    # a lead time without any date: the task is kept, the reminder is not
    result["value"] = ParsedTask(title="без даты", reminder_leads=[15])
    kept = await _quick(authed, "без даты напомни за 15 мин")
    assert kept["task"]["reminders"] == [] and kept["task"]["title"] == "без даты"
    # the parser failing never costs the line
    result.clear()

    def broken(*_args, **_kwargs):
        raise RuntimeError("grammar bug")

    from app.services.planner import quick_add

    original = quick_add.parse_task_text
    quick_add.parse_task_text = broken
    try:
        survived = await _quick(authed, "  что-то   важное ")
    finally:
        quick_add.parse_task_text = original
    assert survived["task"]["title"] == "что-то важное" and survived["parsed"]["tokens"] == []

    quick_id = uid()
    first = await _quick(authed, "идемпотентно", id=quick_id)
    again = await _quick(authed, "идемпотентно", id=quick_id)
    assert first["task"]["id"] == again["task"]["id"] == quick_id
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Task).where(Task.id == uuid.UUID(quick_id))) == 1


async def test_unparsable_rule_digits_never_cost_the_line(authed: httpx.AsyncClient, fake_parser) -> None:
    """A rule with Unicode digits (``int('²')`` raises) is dropped by quick add and previewed by /parse."""
    _, result = fake_parser
    for rule in ("FREQ=DAILY;COUNT=²", "FREQ=DAILY;COUNT=①", "FREQ=WEEKLY;INTERVAL=٢"):
        result["value"] = ParsedTask(title="зарядка", recurrence_rule=rule, recurrence_preset="custom")
        body = await _quick(authed, "зарядка каждый день")
        assert body["task"]["is_recurring"] is False and body["task"]["title"] == "зарядка", rule
        preview = await authed.post(f"{API}/parse", json={"text": "зарядка каждый день"})
        assert preview.status_code == 200, (rule, preview.text)
        assert preview.json()["recurrence_preset"] == "custom"


async def test_quick_add_validation(authed: httpx.AsyncClient) -> None:
    for body in (
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 501},
        {"text": "x", "captured_at": (datetime.now(BANGKOK) + timedelta(days=2)).isoformat()},
        {"text": "x", "captured_at": "2001-01-01T00:00:00Z"},
        {"text": "x", "defaults": {"due_date": "9999-01-01"}},
        {"text": "x", "extra": True},
        {"text": "nul\u0000"},
    ):
        response = await authed.post(f"{API}/tasks/quick", json=body)
        assert response.status_code == 422 and code(response) == "validation_error", body
    missing = await authed.post(f"{API}/tasks/quick", json={"text": "x", "defaults": {"project_id": uid()}})
    assert missing.status_code == 404
    long_parse = await authed.post(f"{API}/parse", json={"text": "x" * 501})
    assert long_parse.status_code == 422
    empty = await authed.post(f"{API}/parse", json={"text": ""})
    assert empty.status_code == 200 and empty.json()["title"] == "" and empty.json()["tokens"] == []
