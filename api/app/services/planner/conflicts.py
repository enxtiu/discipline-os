"""Field-level compare-and-set for edits made on several devices (stage 9).

An edit may carry ``base``: the values it started from, for the fields it
changes. Before anything is written, every field listed in ``base`` is compared
with the value the API shows for the row now (both normalised the same way);
when one differs the request answers **409 ``edit_conflict``** with
``details = {"fields": [...], "current": <the row as the API shows it>}`` and
nothing is written. Rules:

* Only fields listed in ``base`` are compared: two devices editing different
  fields of a row merge naturally; an edit without ``base`` is last-write-wins.
* A field whose stored value already *is* the value this request sets never
  conflicts — a replay of an edit that went through (response lost) or the same
  change made on two devices is not a conflict.
* Values are compared the way the response shows them: text without
  surrounding whitespace, ``""`` = ``null``, CRLF = LF, Unicode NFC; times as
  ``HH:MM`` in your timezone; tags as a set, case-insensitive, ``#`` ignored;
  reminders as a set of definitions; a recurrence preset key as its RRULE;
  ``scheduled`` as ``open`` (it is derived from the block); colours case-insensitive.

The caller locks the row first (and keeps the planner's lock order), presents
it, compares, and only then runs the normal update in the same transaction.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.core.errors import AppError, Conflict
from app.services.planner import recur

Normaliser = Callable[[Any], Any]

__all__ = ["check_base", "edit_conflict"]


# --------------------------------------------------------------------------- #
# Normalisers (accept request objects, response objects and plain JSON values)
# --------------------------------------------------------------------------- #
def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        root = getattr(value, "root", None)
        return _plain(root) if root is not None else value.model_dump(mode="json")
    return value


def text(value: Any) -> str | None:
    value = _plain(value)
    if value is None:
        return None
    out = unicodedata.normalize("NFC", str(value)).replace("\r\n", "\n").replace("\r", "\n").strip()
    return out or None


def context(value: Any) -> str | None:
    out = text(value)
    return (out.lstrip("@").strip() or None) if out else None


def color(value: Any) -> str | None:
    out = text(value)
    return out.upper() if out else None


def same(value: Any) -> Any:
    return _plain(value)


def day(value: Any) -> str | None:
    value = _plain(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def hhmm(value: Any) -> str | None:
    value = _plain(value)
    if value is None or value == "":
        return None
    parts = str(value).strip().split(":")
    try:
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    except (IndexError, ValueError):
        return str(value)


def ident(value: Any) -> str | None:
    value = _plain(value)
    if value is None or value == "":
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return str(value)


def tags(value: Any) -> list[str]:
    value = _plain(value)
    out: set[str] = set()
    for item in value or []:
        name = text(item) or ""
        name = name.lstrip("#").strip().casefold()
        if name:
            out.add(name)
    return sorted(out)


def task_status(value: Any) -> str | None:
    value = _plain(value)
    return "open" if value == "scheduled" else value


def block(value: Any) -> list[str | None] | None:
    value = _plain(value)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        value = {
            "date": getattr(value, "date", None),
            "start": getattr(value, "start", None),
            "end": getattr(value, "end", None),
        }
    return [day(value.get("date")), hhmm(value.get("start")), hhmm(value.get("end"))]


def rrule(value: Any) -> str | None:
    raw = text(value)
    if raw is None:
        return None
    try:
        return recur.validate_rule(raw).text
    except AppError:
        return raw


def reminders(value: Any) -> list[list[Any]]:
    out: set[tuple[Any, ...]] = set()
    for item in _plain(value) or []:
        item = _plain(item)
        if not isinstance(item, Mapping):
            continue
        if item.get("lead_minutes") is not None:
            out.add(("lead", int(item["lead_minutes"])))
        elif item.get("date") is not None and item.get("time") is not None:
            out.add(("at", day(item["date"]), hhmm(item["time"])))
    return [list(key) for key in sorted(out, key=lambda key: tuple(str(part) for part in key))]


TASK: dict[str, Normaliser] = {
    "title": text,
    "notes_md": text,
    "status": task_status,
    "priority": same,
    "project_id": ident,
    "area_id": ident,
    "parent_id": ident,
    "due_date": day,
    "due_time": hhmm,
    "block": block,
    "estimate_minutes": same,
    "recurrence_rule": rrule,
    "recurrence_until": day,
    "tags": tags,
    "context": context,
    "energy": same,
    "is_pinned": same,
    "sort_order": same,
    "reminders": reminders,
}
AREA: dict[str, Normaliser] = {
    "name": text,
    "description": text,
    "color": color,
    "icon": text,
    "weekly_target_minutes": same,
    "is_archived": same,
    "sort_order": same,
}
PROJECT: dict[str, Normaliser] = {
    "name": text,
    "outcome": text,
    "description_md": text,
    "area_id": ident,
    "status": same,
    "deadline": day,
    "start_date": day,
    "color": color,
    "icon": text,
    "is_archived": same,
    "sort_order": same,
}
RESOURCE: dict[str, Normaliser] = {
    "name": text,
    "notes_md": text,
    "url": text,
    "area_id": ident,
    "tags": tags,
    "is_archived": same,
}
TIME_ENTRY: dict[str, Normaliser] = {
    "task_id": ident,
    "project_id": ident,
    "area_id": ident,
    "description": text,
    "note": text,
    "date": day,
    "start": hhmm,
    "end": hhmm,
}
JOURNAL: dict[str, Normaliser] = {
    "note_md": text,
    "rating": same,
    "mood": same,
    "energy": same,
    "wins_md": text,
    "improvements_md": text,
    "tags": tags,
}


# --------------------------------------------------------------------------- #
# What each row "is" for a comparison: the response's fields under the request's names
# --------------------------------------------------------------------------- #
def _ref_id(ref: Any) -> Any:
    return getattr(ref, "id", None) if ref is not None else None


def task_values(out: Any) -> dict[str, Any]:
    """A ``TaskOut``/``TaskDetailOut`` under the names of ``TaskPatchIn``."""
    return {
        "title": out.title,
        "notes_md": out.notes_md,
        "status": out.status,
        "priority": out.priority,
        "project_id": _ref_id(out.project),
        "area_id": _ref_id(out.area),
        "parent_id": out.parent_id,
        "due_date": out.due_date,
        "due_time": out.due_time,
        "block": out.block,
        "estimate_minutes": out.estimate_minutes,
        "recurrence_rule": out.recurrence_rule,
        "recurrence_until": out.recurrence_until,
        "tags": [tag.name for tag in out.tags],
        "context": out.context,
        "energy": out.energy,
        "is_pinned": out.is_pinned,
        "sort_order": out.sort_order,
        "reminders": [
            {"lead_minutes": item.lead_minutes, "date": item.date, "time": item.time}
            for item in out.reminders
            if item.status == "pending"
        ],
    }


def area_values(out: Any) -> dict[str, Any]:
    return {name: getattr(out, name) for name in AREA}


def project_values(out: Any) -> dict[str, Any]:
    values = {name: getattr(out, name) for name in PROJECT if name != "area_id"}
    values["area_id"] = _ref_id(out.area)
    return values


def resource_values(out: Any) -> dict[str, Any]:
    values = {name: getattr(out, name) for name in RESOURCE if name != "area_id"}
    values["area_id"] = _ref_id(out.area)
    return values


def time_entry_values(out: Any, *, task_id: Any, project_id: Any, area_id: Any) -> dict[str, Any]:
    """Ids from the row itself (an entry keeps them even when a name is not shown)."""
    return {
        "task_id": task_id,
        "project_id": project_id,
        "area_id": area_id,
        "description": out.description,
        "note": out.note,
        "date": out.date,
        "start": out.start,
        "end": out.end,
    }


def journal_values(out: Any | None) -> dict[str, Any]:
    """No entry yet: every field is empty."""
    if out is None:
        return dict.fromkeys(JOURNAL)
    return {name: getattr(out, name) for name in JOURNAL}


KINDS: dict[str, dict[str, Normaliser]] = {
    "task": TASK,
    "area": AREA,
    "project": PROJECT,
    "resource": RESOURCE,
    "time_entry": TIME_ENTRY,
    "journal": JOURNAL,
}


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #
def conflicting_fields(
    kind: str, base: BaseModel, current: Mapping[str, Any], patch: BaseModel, patch_fields: Iterable[str]
) -> list[str]:
    """Fields of ``base`` whose current value differs from it and is not already what ``patch`` sets."""
    normalisers = KINDS[kind]
    sets = set(patch_fields)
    out: list[str] = []
    for name in sorted(base.model_fields_set, key=list(normalisers).index):
        normalise = normalisers[name]
        now = normalise(current.get(name))
        if normalise(getattr(base, name)) == now:
            continue
        if name in sets and normalise(getattr(patch, name)) == now:
            continue  # already the value this request sets: a replay, or the same change elsewhere
        out.append(name)
    return out


def edit_conflict(fields: list[str], current: BaseModel | None) -> Conflict:
    return Conflict(
        "Changed on another device since this edit started",
        code="edit_conflict",
        details={"fields": fields, "current": current.model_dump(mode="json", by_alias=True) if current else None},
    )


def check_base(
    kind: str,
    payload: BaseModel,
    current: Mapping[str, Any],
    current_out: BaseModel | None,
    *,
    ignore: Iterable[str] = ("base",),
) -> None:
    """Raise ``edit_conflict`` when ``payload.base`` no longer matches ``current`` (see the module doc)."""
    base = getattr(payload, "base", None)
    if base is None:
        return
    patch_fields = payload.model_fields_set - set(ignore)
    fields = conflicting_fields(kind, base, current, payload, patch_fields)
    if fields:
        raise edit_conflict(fields, current_out)
