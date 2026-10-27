"""Local rows → Notion property values, per kind (stage 9).

Rules:

* **Only the server's numbers.** Days come from ``app.services.day_metrics.collect`` and
  the stored daily scores (``app.services.gamification.score``) — exactly what the app
  shows; nothing is recomputed here. Burned calories (workouts, Health's active energy)
  are their own columns and never touch "eaten" or the target.
* **Local wall clock + the user's timezone.** Instants are written as Notion dates with
  ``time_zone`` and a start *without* offset (https://developers.notion.com/reference/
  page-property-values: "When you provide time_zone, include a time in start and end,
  without a UTC offset"); date-only values have no time zone.
* **Notion's limits.** A text object holds at most 2 000 characters
  (https://developers.notion.com/reference/request-limits) — measured here in UTF-16
  code units, as JavaScript counts them, so an emoji never pushes a chunk over; long
  notes are split into several text objects and capped. Select/multi-select option
  names may not contain commas and are at most 100 characters.
* **Missing is not zero.** A day without a meal has an empty «Съедено», not 0.

A :class:`Row` keeps relations as *local* ids (:class:`RelationRef`); the sync engine
turns them into page ids with the links it holds, so a relation is written as soon as
its target page exists.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tz import ensure_utc, get_zone
from app.models import Area, JournalEntry, Project, ProjectStatus, Tag, Task, task_tags
from app.services import day_metrics
from app.services.gamification import score as score_service
from app.services.gamification.context import GameContext

__all__ = [
    "DAYS_BACK",
    "NOTES_MAX_CHUNKS",
    "TEXT_LIMIT",
    "KindRows",
    "RelationRef",
    "Row",
    "content_hash",
    "option_name",
    "rows_for",
    "select_value",
    "split_text",
    "utf16_len",
]

TEXT_LIMIT = 2000  # per text object
NOTES_MAX_CHUNKS = 10  # long notes: at most 10 text objects (≈ 20 000 characters), then "…"
OPTION_MAX = 100
DAYS_BACK = 365  # the days kind mirrors the last year; older day pages are left as they are


# --------------------------------------------------------------------------- #
# Value builders
# --------------------------------------------------------------------------- #
def utf16_len(text: str) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


_CONTROL: dict[int, None] = dict.fromkeys((*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F))


def split_text(text: str | None, *, limit: int = TEXT_LIMIT, max_chunks: int = NOTES_MAX_CHUNKS) -> list[str]:
    """``text`` as chunks of at most ``limit`` UTF-16 units (never splitting a
    character); beyond ``max_chunks`` chunks the text is cut and ends with "…". Control
    characters other than newlines and tabs are dropped."""
    text = (text or "").translate(_CONTROL)
    if not text:
        return []
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0
    truncated = False
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if size + width > limit:
            chunks.append("".join(buffer))
            buffer, size = [], 0
            if len(chunks) >= max_chunks:
                truncated = True
                break
        buffer.append(ch)
        size += width
    if not truncated and buffer:
        chunks.append("".join(buffer))
    if truncated:
        last = chunks[-1]
        while last and utf16_len(last) + 1 > limit:
            last = last[:-1]
        chunks[-1] = last + "…"
    return chunks


def _rich(text: str | None, *, max_chunks: int = NOTES_MAX_CHUNKS) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": chunk}} for chunk in split_text(text, max_chunks=max_chunks)]


def title(text: str | None) -> dict[str, Any]:
    return {"title": _rich((text or "").strip() or "—", max_chunks=1)}


def rich_text(text: str | None) -> dict[str, Any]:
    return {"rich_text": _rich(text)}


def number(value: float | int | None, digits: int = 0) -> dict[str, Any]:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return {"number": None}
    rounded = round(float(value), digits)
    return {"number": int(rounded) if digits == 0 else rounded}


def option_name(raw: str | None) -> str:
    """A select/multi-select option name: no commas, single spaces, ≤ 100 characters."""
    text = " ".join(str(raw or "").replace(",", " ").split())
    return text[:OPTION_MAX].strip()


def select_value(name: str | None) -> dict[str, Any]:
    clean = option_name(name) if name else ""
    return {"select": {"name": clean} if clean else None}


def multi_select(names: list[str]) -> dict[str, Any]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for raw in names:
        clean = option_name(raw)
        if clean and clean.casefold() not in seen:  # option names are unique case-insensitively
            seen.add(clean.casefold())
            out.append({"name": clean})
    return {"multi_select": out[:100]}


def checkbox(value: bool) -> dict[str, Any]:
    return {"checkbox": bool(value)}


def date_value(day: date | None) -> dict[str, Any]:
    return {"date": {"start": day.isoformat()} if day is not None else None}


def _wall(moment: datetime, tz: str) -> str:
    return ensure_utc(moment).astimezone(get_zone(tz)).strftime("%Y-%m-%dT%H:%M:%S")


def instant(moment: datetime | None, tz: str, end: datetime | None = None) -> dict[str, Any]:
    """A date with time in the user's zone (``time_zone`` + local wall clock)."""
    if moment is None:
        return {"date": None}
    value: dict[str, Any] = {"start": _wall(moment, tz), "time_zone": tz}
    if end is not None:
        value["end"] = _wall(end, tz)
    return {"date": value}


@dataclass(frozen=True, slots=True)
class RelationRef:
    """A relation by local ids of the target kind; resolved to page ids by the engine."""

    target: str
    ids: tuple[str, ...]


@dataclass(slots=True)
class Row:
    entity_id: str
    values: dict[str, Any]
    order: tuple[Any, ...] = ()


@dataclass(slots=True)
class KindRows:
    rows: dict[str, Row]
    # which existing links this run is responsible for (days: only the window)
    in_scope: Callable[[str], bool] = field(default=lambda _entity_id: True)


def content_hash(payload: dict[str, Any]) -> str:
    """sha256 of the exact property payload (keys are property ids)."""
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _rel(target: str, *ids: uuid.UUID | None) -> RelationRef:
    return RelationRef(target, tuple(str(value) for value in ids if value is not None))


# --------------------------------------------------------------------------- #
# Kinds
# --------------------------------------------------------------------------- #
async def _areas(db: AsyncSession, ctx: GameContext, lang: str) -> KindRows:
    rows = await db.execute(
        select(
            Area.id, Area.name, Area.description, Area.weekly_target_minutes, Area.sort_order, Area.created_at
        ).where(Area.user_id == ctx.user_id, Area.is_archived.is_(False))
    )
    out: dict[str, Row] = {}
    for area_id, name, description, weekly, sort_order, created in rows:
        key = str(area_id)
        out[key] = Row(
            key,
            {
                "name": title(name),
                "description": rich_text(description),
                "weekly_target": number(weekly),
                "dsos_id": rich_text(key),
            },
            (sort_order or 0, created, key),
        )
    return KindRows(out)


async def _projects(db: AsyncSession, ctx: GameContext, lang: str) -> KindRows:
    rows = await db.execute(
        select(
            Project.id,
            Project.name,
            Project.status,
            Project.area_id,
            Project.outcome,
            Project.start_date,
            Project.deadline,
            Project.completed_at,
            Project.description_md,
            Project.created_at,
        ).where(
            Project.user_id == ctx.user_id,
            Project.is_archived.is_(False),
            Project.status != ProjectStatus.ARCHIVED,
        )
    )
    out: dict[str, Row] = {}
    for project_id, name, status, area_id, outcome, start, deadline, completed, description, created in rows:
        key = str(project_id)
        out[key] = Row(
            key,
            {
                "name": title(name),
                "status": ("option", str(getattr(status, "value", status))),
                "area": _rel("areas", area_id),
                "outcome": rich_text(outcome),
                "start": date_value(start),
                "deadline": date_value(deadline),
                "completed": instant(completed, ctx.tz),
                "description": rich_text(description),
                "dsos_id": rich_text(key),
            },
            (created, key),
        )
    return KindRows(out)


async def _tasks(db: AsyncSession, ctx: GameContext, lang: str) -> KindRows:
    rows = (
        await db.execute(
            select(
                Task.id,
                Task.title,
                Task.status,
                Task.priority,
                Task.project_id,
                Task.area_id,
                Task.parent_id,
                Task.due_date,
                Task.due_at,
                Task.scheduled_start,
                Task.scheduled_end,
                Task.estimate_minutes,
                Task.actual_minutes,
                Task.context,
                Task.recurrence_rule,
                Task.completed_at,
                Task.created_at,
                Task.notes_md,
            ).where(Task.user_id == ctx.user_id, Task.is_archived.is_(False))
        )
    ).all()
    tags: dict[uuid.UUID, list[str]] = {}
    tag_rows = await db.execute(
        select(task_tags.c.task_id, Tag.name)
        .join(Tag, Tag.id == task_tags.c.tag_id)
        .where(task_tags.c.user_id == ctx.user_id, Tag.user_id == ctx.user_id)
        .order_by(Tag.name)
    )
    for task_id, name in tag_rows:
        tags.setdefault(task_id, []).append(name)
    parents = {row.id: row.parent_id for row in rows}

    def depth(task_id: uuid.UUID) -> int:
        level, cursor, seen = 0, parents.get(task_id), {task_id}
        while cursor is not None and cursor not in seen and level < 10:
            seen.add(cursor)
            level += 1
            cursor = parents.get(cursor)
        return level

    out: dict[str, Row] = {}
    for row in rows:
        key = str(row.id)
        due = instant(row.due_at, ctx.tz) if row.due_at is not None else date_value(row.due_date)
        block = instant(row.scheduled_start, ctx.tz, row.scheduled_end) if row.scheduled_start else {"date": None}
        out[key] = Row(
            key,
            {
                "title": title(row.title),
                "status": ("option", str(getattr(row.status, "value", row.status))),
                "priority": ("option", str(int(row.priority or 3))),
                "project": _rel("projects", row.project_id),
                "area": _rel("areas", row.area_id),
                "parent": _rel("tasks", row.parent_id),
                "due": due,
                "block": block,
                "estimate": number(row.estimate_minutes),
                "tracked": number(row.actual_minutes or 0),
                "tags": multi_select(tags.get(row.id, [])),
                "context": rich_text(row.context),
                "recurring": checkbox(bool(row.recurrence_rule)),
                "completed": instant(row.completed_at, ctx.tz),
                "created": instant(row.created_at, ctx.tz),
                "notes": rich_text(row.notes_md),
                "dsos_id": rich_text(key),
            },
            (depth(row.id), row.created_at, key),  # parents first: their page exists for the relation
        )
    return KindRows(out)


async def _journal(db: AsyncSession, ctx: GameContext, lang: str) -> KindRows:
    rows = (
        await db.scalars(
            select(JournalEntry).where(JournalEntry.user_id == ctx.user_id, day_metrics.journal_has_content())
        )
    ).all()
    out: dict[str, Row] = {}
    for entry in rows:
        key = str(entry.id)
        out[key] = Row(
            key,
            {
                "day": title(entry.entry_date.isoformat()),
                "date": date_value(entry.entry_date),
                "rating": number(entry.rating),
                "mood": number(entry.mood),
                "energy": number(entry.energy),
                "wins": rich_text(entry.wins_md),
                "improvements": rich_text(entry.improvements_md),
                "notes": rich_text(entry.note_md),
                "tags": multi_select([str(tag) for tag in (entry.tags or []) if isinstance(tag, str)]),
                "dsos_id": rich_text(key),
            },
            (entry.entry_date, key),
        )
    return KindRows(out)


def _stored_kcal(row: Any) -> tuple[int | None, bool | None, bool]:
    """``(target, in_zone, found)`` from a stored score's kcal component."""
    payload = row.components if isinstance(getattr(row, "components", None), dict) else {}
    for item in payload.get("items") or []:
        if isinstance(item, dict) and item.get("key") == "kcal":
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            target = detail.get("target")
            return (
                (int(target) if isinstance(target, (int, float)) and target > 0 else None),
                (detail.get("state") == "in_zone"),
                True,
            )
    return None, None, False


def _has_data(m: day_metrics.DayMetrics) -> bool:
    return bool(
        m.meals > 0
        or m.water_ml > 0
        or m.steps is not None
        or m.sleep_minutes is not None
        or m.workouts > 0
        or m.tracked_seconds > 0
        or m.tasks_done > 0
        or m.journal
    )


async def _days(db: AsyncSession, ctx: GameContext, lang: str) -> KindRows:
    today = ctx.today
    window_first = today - timedelta(days=DAYS_BACK - 1)

    def in_scope(entity_id: str) -> bool:
        try:
            return window_first <= date.fromisoformat(entity_id) <= today
        except ValueError:
            return False

    first_data = await day_metrics.first_activity_day(db, ctx.user_id, ctx.tz)
    if first_data is None or first_data > today:
        return KindRows({}, in_scope)
    first = max(first_data, window_first)
    # finished time entries only (no ``now``): a running timer would rewrite today's row on every run
    metrics = await day_metrics.collect(db, ctx.user_id, first, today, ctx.tz)
    stored = await score_service.load_rows(db, ctx.user_id, first, today)
    out: dict[str, Row] = {}
    for day, m in metrics.items():
        if not _has_data(m):
            continue
        key = day.isoformat()
        logged = m.meals > 0
        target, in_zone, found = _stored_kcal(stored[day]) if day in stored else (None, None, False)
        if not found:
            target = ctx.zone.target
            in_zone = logged and ctx.zone.contains(m.kcal)
        stored_score = stored.get(day)
        out[key] = Row(
            key,
            {
                "day": title(key),
                "date": date_value(day),
                "kcal": number(m.kcal if logged else None),  # food only — burned kcal never added
                "kcal_target": number(target),
                "in_zone": checkbox(bool(logged and in_zone)),
                "protein": number(m.protein_g if logged else None, 1),
                "water": number(m.water_ml or None),
                "steps": number(m.steps),
                "sleep": number(m.sleep_minutes / 60 if m.sleep_minutes is not None else None, 1),
                "workouts": number(m.workouts),
                "workout_kcal": number(m.workout_kcal if m.workouts > 0 else None),
                "active_kcal": number(m.active_kcal),
                "tracked": number(m.tracked_minutes),
                "tasks_done": number(m.tasks_done),
                "score": number(float(stored_score.score) if stored_score is not None else None, 1),
                "dsos_id": rich_text(key),
            },
            (day,),
        )
    return KindRows(out, in_scope)


BUILDERS = {"areas": _areas, "projects": _projects, "tasks": _tasks, "journal": _journal, "days": _days}


async def rows_for(kind: str, db: AsyncSession, ctx: GameContext, lang: str) -> KindRows:
    """Every row of ``kind`` that should exist in Notion now, keyed by entity id."""
    return await BUILDERS[kind](db, ctx, lang)
