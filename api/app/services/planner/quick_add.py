"""Quick add: one typed line → a task, through the ru/en parser (``services.task_parser``).

The parser is pure: it gets the capture instant in the user's timezone, the week
start and the active projects/areas, and returns local dates/times and tokens.
This module maps its result onto a normal task create (``source = quick_add``):

* explicit tokens win over the client's ``defaults``; a time typed without a
  date takes the default date (the day the user is looking at) when there is one;
* a recurrence without a date starts on its first occurrence on/after today;
* a quick add never loses the line: parsed values outside the task bounds are
  dropped, and an empty title falls back to the raw text.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Unprocessable
from app.core.logging import get_logger
from app.models import Area, Project, Task
from app.schemas.planner import TaskIn
from app.services.planner import recur
from app.services.planner.common import MAX_TAGS, Clock, normalize_tag, validation_error
from app.services.planner.containers import OPEN_PROJECT
from app.services.planner.tasks import create_task
from app.services.task_parser import KnownContainer, ParsedTask, parse_task_text

log = get_logger(__name__)

CAPTURE_PAST = timedelta(days=400)
CAPTURE_FUTURE = timedelta(days=1)


async def known_containers(db: AsyncSession, user_id: uuid.UUID) -> list[KnownContainer]:
    """Active projects first, then areas — the order the parser prefers on equal names."""
    projects = (
        await db.execute(
            select(Project.id, Project.name)
            .where(Project.user_id == user_id, Project.status.in_(OPEN_PROJECT), Project.is_archived.is_(False))
            .order_by(Project.sort_order, Project.name)
        )
    ).all()
    areas = (
        await db.execute(
            select(Area.id, Area.name)
            .where(Area.user_id == user_id, Area.is_archived.is_(False))
            .order_by(Area.sort_order, Area.name)
        )
    ).all()
    return [KnownContainer(id=str(row.id), name=row.name, kind="project") for row in projects] + [
        KnownContainer(id=str(row.id), name=row.name, kind="area") for row in areas
    ]


def capture_instant(value: datetime | None, clock: Clock) -> datetime:
    when = clock.instant(value)
    if when > clock.now + CAPTURE_FUTURE or when < clock.now - CAPTURE_PAST:
        raise validation_error("captured_at must be within the last 400 days")
    return when


async def parse(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, week_start: int, text: str, captured_at: datetime | None
) -> ParsedTask:
    when = capture_instant(captured_at, clock)
    containers = await known_containers(db, user_id)
    try:
        parsed = parse_task_text(text, now=clock.local(when), week_start=week_start, containers=containers)
    except Exception:  # the parser must never cost the user the line they typed
        log.exception("task_parser_failed", length=len(text))
        parsed = ParsedTask(title=" ".join(text.split()))
    if not (parsed.title or "").strip():
        parsed = replace(parsed, title=" ".join(text.split()))
    return parsed


def preset_for(parsed: ParsedTask) -> str | None:
    if not parsed.recurrence_rule:
        return None
    try:
        return recur.validate_rule(parsed.recurrence_rule).preset
    except AppError:
        return parsed.recurrence_preset or "custom"


def _clean_tags(names: list[str]) -> list[str]:
    out: list[str] = []
    for name in names:
        try:
            tag = normalize_tag(name)
        except AppError:
            continue
        if tag.lower() not in {item.lower() for item in out}:
            out.append(tag)
    return out[:MAX_TAGS]


def task_fields(parsed: ParsedTask, text: str, payload) -> dict:
    """``ParsedTask`` + the client's defaults → ``TaskIn`` fields."""
    defaults = payload.defaults
    kinds = {token.kind for token in parsed.tokens}
    data: dict = {"title": (parsed.title.strip() or text.strip())[:300]}
    if payload.id is not None:
        data["id"] = payload.id
    if parsed.container is not None:
        key = "project_id" if parsed.container.kind == "project" else "area_id"
        data[key] = uuid.UUID(parsed.container.id)
    elif defaults is not None:
        if defaults.project_id is not None:
            data["project_id"] = defaults.project_id
        if defaults.area_id is not None:
            data["area_id"] = defaults.area_id
    if defaults is not None and defaults.parent_id is not None:
        data["parent_id"] = defaults.parent_id

    due_date, due_time = parsed.due_date, parsed.due_time
    block_date = parsed.block_date or parsed.due_date
    default_day = defaults.due_date if defaults is not None else None
    if default_day is not None and "date" not in kinds:
        if due_date is not None or parsed.block_start is not None:
            # a time typed without a date: on the day the user is looking at
            due_date = default_day if due_date is not None else None
            block_date = default_day
        elif not parsed.recurrence_rule:
            due_date = default_day
    if due_date is not None:
        data["due_date"] = due_date
        if due_time is not None:
            data["due_time"] = due_time.strftime("%H:%M")
    if parsed.block_start is not None and parsed.block_end is not None and block_date is not None:
        data["block"] = {
            "date": block_date,
            "start": parsed.block_start.strftime("%H:%M"),
            "end": parsed.block_end.strftime("%H:%M"),
        }
    if parsed.estimate_minutes is not None and 1 <= parsed.estimate_minutes <= 1440:
        data["estimate_minutes"] = parsed.estimate_minutes
    if parsed.priority is not None and 1 <= parsed.priority <= 4:
        data["priority"] = parsed.priority
    tags = _clean_tags(list(parsed.tags))
    if tags:
        data["tags"] = tags
    if parsed.context and len(parsed.context.strip().lstrip("@")) <= 64:
        data["context"] = parsed.context
    if parsed.recurrence_rule:
        try:
            data["recurrence_rule"] = recur.validate_rule(parsed.recurrence_rule).text
        except AppError:
            log.warning("quick_add_rule_dropped", rule=parsed.recurrence_rule[:120])
    leads = [lead for lead in parsed.reminder_leads if 0 <= lead <= 10080]
    if leads:
        data["reminders"] = [{"lead_minutes": lead} for lead in dict.fromkeys(leads)][:5]
    return data


async def quick_add(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, week_start: int, payload
) -> tuple[Task, ParsedTask]:
    parsed = await parse(
        db, user_id=user_id, clock=clock, week_start=week_start, text=payload.text, captured_at=payload.captured_at
    )
    data = task_fields(parsed, payload.text, payload)
    try:
        task_in = TaskIn.model_validate(data)
    except ValidationError as exc:
        raise Unprocessable(
            "The parsed task is invalid",
            code="validation_error",
            details={"errors": [{"loc": list(map(str, error["loc"])), "msg": error["msg"]} for error in exc.errors()]},
        ) from None
    if "reminders" in data and "due_date" not in data and "block" not in data and not task_in.recurrence_rule:
        task_in = task_in.model_copy(update={"reminders": []})  # a lead time needs a date: keep the task, drop it
    task = await create_task(db, user_id=user_id, clock=clock, payload=task_in, source="quick_add")
    return task, parsed
