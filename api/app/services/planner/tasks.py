"""Tasks: CRUD, status rules, subtasks, due/blocks, recurrence, reminders, tags.

* **Status.** ``inbox`` until a task gets a project, area, parent, due date or
  block (then ``open``); a block turns ``inbox``/``open`` into ``scheduled`` and
  removing it turns ``scheduled`` back into ``open``. ``scheduled`` is derived,
  never accepted.
* **Subtasks.** At most two levels below a root (``task_too_deep``), ≤ 100
  children per parent; a subtask inherits project/area from its parent unless
  they are given. A task can never become its own ancestor.
* **Recurrence.** The first task of a series is its root (``series_index`` 0,
  ``recurrence_parent_id`` NULL); instances point at the root. Completing (or
  cancelling — "skip") an instance creates the next one exactly once: the row
  lock serialises double taps and the unique (root, index) index makes it
  idempotent even across instances. Reopening removes a next instance nobody
  touched yet.
* **Reminders.** Relative (``lead_minutes`` before the due time, the block start
  or 09:00 of the due day) or absolute (a local date and time); stored in
  ``reminders`` for the stage-7 scheduler. Closing a task cancels them,
  reopening restores them.
* **Tags** are created on the fly and matched case-insensitively.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.core.errors import NotFound, Unprocessable
from app.core.ids import new_id
from app.core.logging import get_logger
from app.models import (
    Area,
    Project,
    RecurrenceMode,
    Reminder,
    ReminderKind,
    ReminderStatus,
    Tag,
    Task,
    TaskStatus,
    task_tags,
)
from app.schemas.planner import LATEST_DATE
from app.services.planner import recur
from app.services.planner.common import (
    FUTURE_TOLERANCE,
    Clock,
    advisory_lock,
    id_conflict,
    normalize_tags,
    owned,
    parse_hhmm,
    validation_error,
)

log = get_logger(__name__)

MAX_DEPTH = 2
MAX_CHILDREN = 100
# Giving a task a parent (a move or a new subtask) takes this per-user lock before any row
# lock: two crossing moves (A under B ‖ B under A) would otherwise both pass the cycle check
# or deadlock on each other's rows, and depth/children checks could race a concurrent change.
TREE_LOCK = "planner-tree"
MAX_REMINDERS = 5
UNTOUCHED = timedelta(seconds=2)
DEFAULT_PRIORITY = 4
DUE_DAY_REMINDER_AT = time(9, 0)
MAX_BLOCK = timedelta(hours=24)
CLOSED = (TaskStatus.DONE, TaskStatus.CANCELLED)
TASK_LOAD = (raiseload(Task.subtasks), raiseload(Task.tags), raiseload(Task.parent))


def task_query():
    """``select(Task)`` without the model's eager subtask/tag loading (loaders batch those)."""
    return select(Task).options(*TASK_LOAD)


def is_open(task: Task) -> bool:
    return task.status not in CLOSED


async def get_task(db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, *, lock: bool = False) -> Task:
    stmt = task_query().where(Task.id == task_id, Task.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    task = await db.scalar(stmt)
    if task is None:
        raise NotFound("Task not found", details={"id": str(task_id)})
    return task


# --------------------------------------------------------------------------- #
# Local fields ↔ instants
# --------------------------------------------------------------------------- #
def block_instants(day: date, start: str, end: str, clock: Clock) -> tuple[datetime, datetime]:
    """A local block → instants; ``end`` ≤ ``start`` ends the next day.

    The 24-hour limit is wall clock: a 00:00–00:00 block on a fall-back day is one whole
    day although it lasts 25 real hours (``minutes`` shows the real length). A block that
    ends before it starts in real time — inside a spring-forward gap — is refused.
    """
    start_t, end_t = parse_hhmm(start), parse_hhmm(end)
    end_day = day + timedelta(days=1) if end_t <= start_t else day
    start_at, end_at = clock.at(day, start_t), clock.at(end_day, end_t)
    wall = datetime.combine(end_day, end_t) - datetime.combine(day, start_t)
    if not (start_at < end_at and wall <= MAX_BLOCK):
        raise Unprocessable(
            "A block lasts more than 0 minutes and at most 24 hours",
            code="invalid_block",
            details={"date": day.isoformat(), "start": start, "end": end},
        )
    return start_at, end_at


def local_due_time(task: Task, clock: Clock) -> time | None:
    return clock.local(task.due_at).time().replace(tzinfo=None) if task.due_at is not None else None


def recurrence_anchor(task: Task, clock: Clock) -> datetime | None:
    """This instance's local due as a naive wall clock: the due date with the due time
    (or the block start time, or 00:00); without a due date, the block start."""
    if task.due_date is not None:
        if task.due_at is not None:
            at = clock.local(task.due_at).time()
        elif task.scheduled_start is not None:
            at = clock.local(task.scheduled_start).time()
        else:
            at = time.min
        return datetime.combine(task.due_date, at.replace(tzinfo=None))
    if task.scheduled_start is not None:
        return clock.naive(task.scheduled_start)
    return None


def reminder_anchor(task: Task, clock: Clock) -> datetime | None:
    """Instant a relative reminder counts back from: due time, else block start, else 09:00 of the due day."""
    if task.due_at is not None:
        return task.due_at
    if task.scheduled_start is not None:
        return task.scheduled_start
    if task.due_date is not None:
        return clock.at(task.due_date, DUE_DAY_REMINDER_AT)
    return None


def has_container(task: Task) -> bool:
    return any(
        value is not None
        for value in (task.project_id, task.area_id, task.parent_id, task.due_date, task.scheduled_start)
    )


def derive_status(
    task: Task, explicit: TaskStatus | None, previous: TaskStatus | None, *, gave_attribute: bool
) -> TaskStatus:
    if explicit is not None:
        status = explicit
    elif previous is None:
        status = TaskStatus.OPEN if has_container(task) else TaskStatus.INBOX
    else:
        status = previous
        if status == TaskStatus.INBOX and gave_attribute:
            status = TaskStatus.OPEN
    if status in (TaskStatus.INBOX, TaskStatus.OPEN) and task.scheduled_start is not None:
        status = TaskStatus.SCHEDULED
    if status == TaskStatus.SCHEDULED and task.scheduled_start is None:
        status = TaskStatus.OPEN
    return status


def _context(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lstrip("@").strip()
    if len(text) > 64:
        raise validation_error("context is at most 64 characters")
    return text or None


def _rule_or_none(raw: str | None) -> recur.Rule | None:
    if raw is None or not raw.strip():
        return None
    return recur.validate_rule(raw)


def _earliest(*values: date | None) -> date | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _never_occurs(rule: recur.Rule) -> Unprocessable:
    return Unprocessable(
        "This recurrence has no occurrence from today on", code="invalid_rrule", details={"rule": rule.text}
    )


# --------------------------------------------------------------------------- #
# Tree helpers
# --------------------------------------------------------------------------- #
async def task_depth(db: AsyncSession, task: Task) -> int:
    if task.parent_id is None:
        return 0
    grand = await db.scalar(select(Task.parent_id).where(Task.id == task.parent_id))
    return 1 if grand is None else 2


async def descendant_ids(db: AsyncSession, user_id: uuid.UUID, root_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    level = list(root_ids)
    for _ in range(MAX_DEPTH + 1):  # one level more than allowed: never trust old data blindly
        if not level:
            break
        level = list(
            (await db.scalars(select(Task.id).where(Task.parent_id.in_(level), Task.user_id == user_id))).all()
        )
        out.extend(level)
    return out


async def _subtree_height(db: AsyncSession, task_id: uuid.UUID) -> int:
    children = select(Task.id).where(Task.parent_id == task_id)
    if not await db.scalar(select(children.exists())):
        return 0
    grandchildren = select(Task.id).where(Task.parent_id.in_(children))
    return 2 if await db.scalar(select(grandchildren.exists())) else 1


async def _check_children(db: AsyncSession, parent_id: uuid.UUID) -> None:
    count = await db.scalar(select(func.count()).select_from(Task).where(Task.parent_id == parent_id))
    if (count or 0) >= MAX_CHILDREN:
        raise Unprocessable(
            f"A task has at most {MAX_CHILDREN} subtasks", code="too_many_subtasks", details={"max": MAX_CHILDREN}
        )


def _too_deep(message: str = "Subtasks go at most two levels deep") -> Unprocessable:
    return Unprocessable(message, code="task_too_deep", details={"max_depth": MAX_DEPTH})


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #
async def tag_ids_for(db: AsyncSession, user_id: uuid.UUID, names: Sequence[str]) -> list[uuid.UUID]:
    """Ids of the named tags (case-insensitive), creating the missing ones."""
    if not names:
        return []

    async def lookup(wanted: Sequence[str]) -> dict[str, uuid.UUID]:
        rows = await db.execute(
            select(Tag.id, Tag.name).where(
                Tag.user_id == user_id, func.lower(Tag.name).in_([name.lower() for name in wanted])
            )
        )
        return {row.name.lower(): row.id for row in rows}

    found = await lookup(names)
    missing = [name for name in names if name.lower() not in found]
    if missing:
        await db.execute(
            pg_insert(Tag)
            .values([{"id": new_id(), "user_id": user_id, "name": name, "kind": "tag"} for name in missing])
            .on_conflict_do_nothing()
        )
        found.update(await lookup(missing))
    out: list[uuid.UUID] = []
    for name in names:
        tag_id = found.get(name.lower())
        if tag_id is None:  # Python and PostgreSQL disagree on lower() for an exotic letter
            tag_id = await db.scalar(
                select(Tag.id).where(Tag.user_id == user_id, func.lower(Tag.name) == func.lower(name)).limit(1)
            )
        if tag_id is not None and tag_id not in out:
            out.append(tag_id)
    return out


async def set_task_tags(db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> None:
    stmt = delete(task_tags).where(task_tags.c.task_id == task_id)
    if tag_ids:
        stmt = stmt.where(task_tags.c.tag_id.not_in(tag_ids))
    await db.execute(stmt)
    if tag_ids:
        await db.execute(
            pg_insert(task_tags)
            .values([{"task_id": task_id, "tag_id": tag_id, "user_id": user_id} for tag_id in tag_ids])
            .on_conflict_do_nothing()
        )


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
ReminderKey = tuple[Any, ...]


def reminder_defs(items: Iterable[Any]) -> list[dict[str, Any]]:
    """Request reminders (``ReminderIn`` or plain dicts) → de-duplicated payload dicts, ≤ 5."""
    out: list[dict[str, Any]] = []
    seen: set[ReminderKey] = set()
    for item in items:
        value = getattr(item, "root", item)
        if not isinstance(value, dict):
            value = value.model_dump(mode="json")
        if "lead_minutes" in value and value["lead_minutes"] is not None:
            payload: dict[str, Any] = {"lead_minutes": int(value["lead_minutes"])}
        else:
            payload = {"date": str(value["date"]), "time": str(value["time"])}
        key = _reminder_key(payload)
        if key not in seen:
            seen.add(key)
            out.append(payload)
    if len(out) > MAX_REMINDERS:
        raise Unprocessable(
            f"At most {MAX_REMINDERS} reminders per task",
            code="too_many_reminders",
            details={"max": MAX_REMINDERS, "given": len(out)},
        )
    return out


def _reminder_key(payload: dict[str, Any]) -> ReminderKey:
    if "lead_minutes" in payload:
        return ("lead", int(payload["lead_minutes"]))
    return ("at", str(payload.get("date")), str(payload.get("time")))


def _no_anchor() -> Unprocessable:
    return Unprocessable("A reminder before the due time needs a due date or a block", code="invalid_reminder")


def remind_at(task: Task, payload: dict[str, Any], clock: Clock, anchor: datetime | None) -> datetime | None:
    if "lead_minutes" in payload:
        if anchor is None:
            return None
        return anchor - timedelta(minutes=int(payload["lead_minutes"]))
    return clock.at(date.fromisoformat(payload["date"]), parse_hhmm(payload["time"]))


def _reminder_row(task: Task, payload: dict[str, Any], at: datetime, status: ReminderStatus) -> dict[str, Any]:
    reminder_id = new_id()
    return {
        "id": reminder_id,
        "user_id": task.user_id,
        "kind": ReminderKind.TASK,
        "task_id": task.id,
        "title": task.title[:200],
        "payload": payload,
        "remind_at": at,
        "lead_minutes": int(payload.get("lead_minutes", 0)),
        "status": status,
        "dedupe_key": f"task:{task.id}:{reminder_id}",
    }


def _active_status(task: Task) -> ReminderStatus:
    """Reminders of an open task are pending; a closed task keeps them cancelled (reopen restores)."""
    return ReminderStatus.PENDING if is_open(task) else ReminderStatus.CANCELLED


async def replace_reminders(db: AsyncSession, task: Task, defs: list[dict[str, Any]], clock: Clock) -> None:
    """The task's active reminders become exactly ``defs`` (sent ones stay as history)."""
    anchor = reminder_anchor(task, clock)
    if anchor is None and any("lead_minutes" in item for item in defs):
        raise _no_anchor()
    state = _active_status(task)
    current = (await db.scalars(select(Reminder).where(Reminder.task_id == task.id, Reminder.status == state))).all()
    by_key = {_reminder_key(row.payload or {}): row for row in current}
    wanted = {_reminder_key(item): item for item in defs}
    stale = [row.id for key, row in by_key.items() if key not in wanted]
    if stale:
        await db.execute(delete(Reminder).where(Reminder.id.in_(stale)))
    rows = []
    for key, payload in wanted.items():
        at = remind_at(task, payload, clock, anchor)
        assert at is not None  # checked above
        if key in by_key:
            by_key[key].remind_at = at
            by_key[key].title = task.title[:200]
        else:
            rows.append(_reminder_row(task, payload, at, state))
    if rows:
        await db.execute(pg_insert(Reminder).values(rows))
    await db.flush()


async def recompute_reminders(db: AsyncSession, task: Task, clock: Clock, *, strict: bool = True) -> None:
    """After a due/block/title change: pending reminders follow (relative ones need an anchor)."""
    pending = (
        await db.scalars(select(Reminder).where(Reminder.task_id == task.id, Reminder.status == ReminderStatus.PENDING))
    ).all()
    if not pending:
        return
    anchor = reminder_anchor(task, clock)
    for row in pending:
        at = remind_at(task, row.payload or {}, clock, anchor)
        if at is None:
            if strict:
                raise _no_anchor()
            continue
        row.remind_at = at
        row.title = task.title[:200]
    await db.flush()


async def _copy_reminders(
    db: AsyncSession, sources: dict[uuid.UUID, uuid.UUID], tasks: dict[uuid.UUID, Task], shift: timedelta, clock: Clock
) -> None:
    """Reminder definitions of ``old id → new id`` tasks: relative ones recomputed, absolute ones shifted."""
    if not sources:
        return
    rows = (await db.scalars(select(Reminder).where(Reminder.task_id.in_(list(sources))))).all()
    out: list[dict[str, Any]] = []
    seen: set[tuple[uuid.UUID, ReminderKey]] = set()
    for row in rows:
        target = tasks[sources[row.task_id]]
        payload = dict(row.payload or {})
        if "lead_minutes" not in payload and "date" in payload:
            shifted = date.fromisoformat(payload["date"]) + shift
            if shifted > LATEST_DATE:
                continue  # past the last supported day: not copied
            payload["date"] = shifted.isoformat()
        key = (target.id, _reminder_key(payload))
        if key in seen or ("lead_minutes" not in payload and "date" not in payload):
            continue
        at = remind_at(target, payload, clock, reminder_anchor(target, clock))
        if at is None:
            continue
        seen.add(key)
        out.append(_reminder_row(target, payload, at, ReminderStatus.PENDING))
    if out:
        await db.execute(pg_insert(Reminder).values(out))


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
def _replayed(existing: Task, user_id: uuid.UUID, parent_id: uuid.UUID | None) -> Task:
    if existing.user_id != user_id or existing.parent_id != parent_id:
        raise id_conflict("task")
    return existing


async def create_task(db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, payload, source: str = "manual") -> Task:
    """``payload`` is a validated ``TaskIn``. Idempotent by ``payload.id``."""
    if payload.id is not None:
        existing = await db.scalar(task_query().where(Task.id == payload.id))
        if existing is not None:
            return _replayed(existing, user_id, payload.parent_id)
    fields = payload.model_fields_set
    if payload.parent_id is not None:
        await advisory_lock(db, user_id, TREE_LOCK)
    if payload.project_id is not None:
        await owned(db, Project, user_id, payload.project_id, "Project")
    if payload.area_id is not None:
        await owned(db, Area, user_id, payload.area_id, "Area")
    parent: Task | None = None
    if payload.parent_id is not None:
        parent = await get_task(db, user_id, payload.parent_id)
        if await task_depth(db, parent) + 1 > MAX_DEPTH:
            raise _too_deep()
        await _check_children(db, parent.id)

    task_id = payload.id or new_id()
    task = Task(id=task_id, user_id=user_id)  # transient: only used to derive values
    task.project_id = payload.project_id if parent is None or "project_id" in fields else parent.project_id
    task.area_id = payload.area_id if parent is None or "area_id" in fields else parent.area_id
    task.parent_id = parent.id if parent is not None else None
    task.title = payload.title
    task.due_date = payload.due_date
    task.due_at = clock.at(payload.due_date, parse_hhmm(payload.due_time)) if payload.due_time else None
    task.scheduled_start = task.scheduled_end = None
    if payload.block is not None:
        task.scheduled_start, task.scheduled_end = block_instants(
            payload.block.date, payload.block.start, payload.block.end, clock
        )
    rule = _rule_or_none(payload.recurrence_rule)
    until = _earliest(payload.recurrence_until, rule.until if rule else None)
    if rule is not None and task.due_date is None and task.scheduled_start is None:
        task.due_date = recur.first_on_or_after(rule, clock.today, until)
        if task.due_date is None:
            raise _never_occurs(rule)
    tags = normalize_tags(payload.tags)
    defs = reminder_defs(payload.reminders)
    explicit = TaskStatus(payload.status) if payload.status else None
    status = derive_status(task, explicit, None, gave_attribute=False)
    task.status = status
    if defs and reminder_anchor(task, clock) is None and any("lead_minutes" in item for item in defs):
        raise _no_anchor()

    values = {
        "id": task_id,
        "client_uuid": task_id,
        "user_id": user_id,
        "title": payload.title,
        "notes_md": payload.notes_md,
        "status": status,
        "priority": payload.priority or DEFAULT_PRIORITY,
        "project_id": task.project_id,
        "area_id": task.area_id,
        "parent_id": task.parent_id,
        "due_at": task.due_at,
        "due_date": task.due_date,
        "scheduled_start": task.scheduled_start,
        "scheduled_end": task.scheduled_end,
        "all_day": False,
        "estimate_minutes": payload.estimate_minutes,
        "actual_minutes": 0,
        "completed_at": clock.now if status in CLOSED else None,
        "recurrence_mode": RecurrenceMode.RRULE if rule else RecurrenceMode.NONE,
        "recurrence_rule": rule.text if rule else None,
        "recurrence_until": until,
        "recurrence_parent_id": None,
        "series_index": 0 if rule else None,
        "sort_order": payload.sort_order,
        "is_pinned": payload.is_pinned,
        "is_archived": False,
        "source": source,
        "energy": payload.energy,
        "context": _context(payload.context),
    }
    inserted = (
        await db.execute(pg_insert(Task).values(**values).on_conflict_do_nothing().returning(Task.id))
    ).scalar_one_or_none()
    if inserted is None:  # a concurrent replay of the same id won the race
        existing = await db.scalar(task_query().where(Task.id == task_id).execution_options(populate_existing=True))
        if existing is None:
            raise id_conflict("task")
        return _replayed(existing, user_id, payload.parent_id)
    created = await get_task(db, user_id, task_id)
    if tags:
        await set_task_tags(db, user_id, task_id, await tag_ids_for(db, user_id, tags))
    if defs:
        await replace_reminders(db, created, defs, clock)
    log.info("task_created", task_id=str(task_id), source=source, recurring=bool(rule))
    return created


# --------------------------------------------------------------------------- #
# Close / reopen / next instance
# --------------------------------------------------------------------------- #
def _series(task: Task) -> tuple[uuid.UUID, int]:
    """(root id, index of the next instance)."""
    return task.recurrence_parent_id or task.id, (task.series_index or 0) + 1


async def existing_next_instance(db: AsyncSession, task: Task) -> Task | None:
    root_id, index = _series(task)
    return await db.scalar(
        task_query().where(
            Task.recurrence_parent_id == root_id, Task.series_index == index, Task.user_id == task.user_id
        )
    )


def _shifted_values(source: Task, shift: timedelta, clock: Clock) -> dict[str, Any]:
    due_date = source.due_date + shift if source.due_date is not None else None
    due_time = local_due_time(source, clock)
    due_at = clock.at(due_date, due_time) if due_date is not None and due_time is not None else None
    start = end = None
    if source.scheduled_start is not None and source.scheduled_end is not None:
        start = clock.from_naive(clock.naive(source.scheduled_start) + shift)
        end = start + (source.scheduled_end - source.scheduled_start)  # duration preserved
    return {"due_date": due_date, "due_at": due_at, "scheduled_start": start, "scheduled_end": end}


def _copy_values(source: Task, new_task_id: uuid.UUID, shift: timedelta, clock: Clock) -> dict[str, Any]:
    now = clock.now
    values = {
        "id": new_task_id,
        "client_uuid": new_task_id,
        "user_id": source.user_id,
        "title": source.title,
        "notes_md": source.notes_md,
        "priority": source.priority,
        "project_id": source.project_id,
        "area_id": source.area_id,
        "parent_id": source.parent_id,
        "all_day": False,
        "estimate_minutes": source.estimate_minutes,
        "actual_minutes": 0,
        "completed_at": None,
        "recurrence_mode": RecurrenceMode.NONE,
        "recurrence_rule": None,
        "recurrence_until": None,
        "recurrence_parent_id": None,
        "series_index": None,
        "sort_order": source.sort_order,
        "is_pinned": False,
        "is_archived": False,
        "source": "recurrence",
        "energy": source.energy,
        "context": source.context,
        "created_at": now,
        "updated_at": now,
        **_shifted_values(source, shift, clock),
    }
    values["status"] = TaskStatus.SCHEDULED if values["scheduled_start"] is not None else TaskStatus.OPEN
    return values


def _within_range(values: dict[str, Any], clock: Clock) -> bool:
    """Drops a shifted due day or block that lies past the last day the API accepts
    (a stored date must always be accepted back); ``False`` when it dropped one."""
    kept = True
    if values["due_date"] is not None and values["due_date"] > LATEST_DATE:
        values.update(due_date=None, due_at=None)
        kept = False
    start = values["scheduled_start"]
    if start is not None and clock.local_day(start) > LATEST_DATE:
        values.update(scheduled_start=None, scheduled_end=None, status=TaskStatus.OPEN)
        kept = False
    return kept


async def _copy_tags(db: AsyncSession, user_id: uuid.UUID, mapping: dict[uuid.UUID, uuid.UUID]) -> None:
    if not mapping:
        return
    rows = (
        await db.execute(select(task_tags.c.task_id, task_tags.c.tag_id).where(task_tags.c.task_id.in_(list(mapping))))
    ).all()
    if rows:
        await db.execute(
            pg_insert(task_tags)
            .values([{"task_id": mapping[row.task_id], "tag_id": row.tag_id, "user_id": user_id} for row in rows])
            .on_conflict_do_nothing()
        )


async def _create_next_instance(db: AsyncSession, task: Task, clock: Clock) -> Task | None:
    existing = await existing_next_instance(db, task)
    if existing is not None:
        return existing
    if not task.recurrence_rule:
        return None
    rule = recur.parse_stored(task.recurrence_rule)
    root_id, index = _series(task)
    if rule.count is not None and index >= rule.count:
        return None  # COUNT reached: the series is complete
    anchor = recurrence_anchor(task, clock) or datetime.combine(clock.today, time.min)
    occurrence = recur.next_after(rule, anchor, clock.today, task.recurrence_until)
    if occurrence is None:
        return None  # past recurrence_until: the series ends here
    shift = timedelta(days=(occurrence.date() - anchor.date()).days)
    next_id = new_id()
    values = _copy_values(task, next_id, shift, clock)
    if not _within_range(values, clock):
        return None  # its due day or block would lie past the supported dates: the series ends here
    values.update(
        recurrence_mode=RecurrenceMode.RRULE,
        recurrence_rule=task.recurrence_rule,
        recurrence_until=task.recurrence_until,
        recurrence_parent_id=root_id,
        series_index=index,
        is_pinned=False,
    )
    if values["due_date"] is None and values["scheduled_start"] is None:
        values["due_date"] = occurrence.date()
    inserted = (
        await db.execute(pg_insert(Task).values(**values).on_conflict_do_nothing().returning(Task.id))
    ).scalar_one_or_none()
    if inserted is None:  # another request created this position of the series first
        return await existing_next_instance(db, task)
    created = await get_task(db, task.user_id, next_id)
    mapping = {task.id: next_id}
    targets = {next_id: created}
    await _copy_subtree(db, task, next_id, shift, clock, mapping, targets)
    await _copy_tags(db, task.user_id, mapping)
    await _copy_reminders(db, mapping, targets, shift, clock)
    log.info("task_next_instance", task_id=str(task.id), next_id=str(next_id), index=index)
    return created


async def _copy_subtree(
    db: AsyncSession,
    source: Task,
    new_root_id: uuid.UUID,
    shift: timedelta,
    clock: Clock,
    mapping: dict[uuid.UUID, uuid.UUID],
    targets: dict[uuid.UUID, Task],
) -> None:
    """Copy the subtree of ``source`` under the new instance, every copy open again."""
    level = {source.id: new_root_id}
    for _ in range(MAX_DEPTH):
        children = (
            await db.scalars(
                task_query()
                .where(Task.parent_id.in_(list(level)), Task.user_id == source.user_id)
                .order_by(Task.sort_order, Task.created_at)
            )
        ).all()
        if not children:
            return
        rows = []
        next_level: dict[uuid.UUID, uuid.UUID] = {}
        for child in children:
            child_id = new_id()
            values = _copy_values(child, child_id, shift, clock)
            _within_range(values, clock)  # a step dated past the supported dates is copied without that date
            values["parent_id"] = level[child.parent_id]
            rows.append(values)
            next_level[child.id] = child_id
            mapping[child.id] = child_id
        await db.execute(pg_insert(Task).values(rows))
        created = (await db.scalars(task_query().where(Task.id.in_(list(next_level.values()))))).all()
        targets.update({row.id: row for row in created})
        level = next_level


async def close_task(
    db: AsyncSession, task: Task, status: TaskStatus, completed_at: datetime, clock: Clock
) -> Task | None:
    """Done or cancelled: open descendants follow, reminders are cancelled, a recurring
    task gets its next instance (once). Returns the next instance, if any."""
    task.status = status
    task.completed_at = completed_at
    await db.flush()
    descendants = await descendant_ids(db, task.user_id, [task.id])
    if descendants:
        await db.execute(
            update(Task)
            .where(Task.id.in_(descendants), Task.user_id == task.user_id, Task.status.not_in(CLOSED))
            .values(status=status, completed_at=completed_at)
            .execution_options(synchronize_session="fetch")
        )
    await db.execute(
        update(Reminder)
        .where(
            Reminder.task_id.in_([task.id, *descendants]),
            Reminder.user_id == task.user_id,
            Reminder.status == ReminderStatus.PENDING,
        )
        .values(status=ReminderStatus.CANCELLED)
        .execution_options(synchronize_session="fetch")
    )
    if task.recurrence_rule:
        return await _create_next_instance(db, task, clock)
    return None


async def _untouched(db: AsyncSession, instance: Task) -> bool:
    if instance.status in CLOSED or instance.updated_at - instance.created_at > UNTOUCHED:
        return False
    ids = await descendant_ids(db, instance.user_id, [instance.id])
    if not ids:
        return True
    rows = (await db.execute(select(Task.created_at, Task.updated_at, Task.status).where(Task.id.in_(ids)))).all()
    return all(row.status not in CLOSED and row.updated_at - row.created_at <= UNTOUCHED for row in rows)


async def reopen_task(db: AsyncSession, task: Task, explicit: TaskStatus | None, clock: Clock) -> uuid.UUID | None:
    """Back to open: removes the next instance this completion created if nobody touched it."""
    task.completed_at = None
    task.status = derive_status(task, explicit, None, gave_attribute=False)
    await db.flush()
    removed: uuid.UUID | None = None
    following = await existing_next_instance(db, task)
    if following is not None and await _untouched(db, following):
        await db.execute(
            delete(Task)
            .where(Task.id == following.id, Task.user_id == task.user_id)
            .execution_options(synchronize_session=False)
        )
        db.expunge(following)
        removed = following.id
    await db.execute(
        update(Reminder)
        .where(Reminder.task_id == task.id, Reminder.status == ReminderStatus.CANCELLED)
        .values(status=ReminderStatus.PENDING)
        .execution_options(synchronize_session="fetch")
    )
    await recompute_reminders(db, task, clock, strict=False)
    return removed


def _completion_time(value: datetime | None, clock: Clock) -> datetime:
    when = clock.instant(value)
    if when > clock.now + FUTURE_TOLERANCE:
        raise validation_error("completed_at cannot be in the future")
    return when


# --------------------------------------------------------------------------- #
# Update
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PatchResult:
    task: Task
    next_instance: Task | None = None
    removed_next_instance_id: uuid.UUID | None = None


async def update_task(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, task_id: uuid.UUID, payload
) -> PatchResult:
    fs = payload.model_fields_set
    if "parent_id" in fs and payload.parent_id is not None:
        await advisory_lock(db, user_id, TREE_LOCK)  # before the row lock, see TREE_LOCK
    task = await get_task(db, user_id, task_id, lock=True)
    gave_attribute = False
    timing_changed = "title" in fs

    if "project_id" in fs:
        if payload.project_id is not None:
            await owned(db, Project, user_id, payload.project_id, "Project")
            gave_attribute = True
        task.project_id = payload.project_id
    if "area_id" in fs:
        if payload.area_id is not None:
            await owned(db, Area, user_id, payload.area_id, "Area")
            gave_attribute = True
        task.area_id = payload.area_id
    if "parent_id" in fs and payload.parent_id != task.parent_id:
        if payload.parent_id is None:
            task.parent_id = None
        else:
            parent = await get_task(db, user_id, payload.parent_id)
            if parent.id == task.id or parent.id in await descendant_ids(db, user_id, [task.id]):
                raise _too_deep("A task cannot become its own subtask")
            if await task_depth(db, parent) + 1 + await _subtree_height(db, task.id) > MAX_DEPTH:
                raise _too_deep()
            await _check_children(db, parent.id)
            task.parent_id = parent.id
            gave_attribute = True
            if "project_id" not in fs:
                task.project_id = parent.project_id
            if "area_id" not in fs:
                task.area_id = parent.area_id

    for name in ("title", "notes_md", "priority", "energy", "is_pinned", "sort_order", "estimate_minutes"):
        if name in fs:
            setattr(task, name, getattr(payload, name))
    if "context" in fs:
        task.context = _context(payload.context)

    if "due_date" in fs or "due_time" in fs:
        new_date = payload.due_date if "due_date" in fs else task.due_date
        if "due_time" in fs:
            new_time = parse_hhmm(payload.due_time) if payload.due_time else None
        elif new_date is None:
            new_time = None  # removing the date removes its time
        else:
            new_time = local_due_time(task, clock)
        if new_time is not None and new_date is None:
            raise validation_error("due_time requires due_date")
        task.due_date = new_date
        task.due_at = clock.at(new_date, new_time) if new_date is not None and new_time is not None else None
        gave_attribute = gave_attribute or (new_date is not None and "due_date" in fs)
        timing_changed = True
    if "block" in fs:
        if payload.block is None:
            task.scheduled_start = task.scheduled_end = None
        else:
            task.scheduled_start, task.scheduled_end = block_instants(
                payload.block.date, payload.block.start, payload.block.end, clock
            )
            gave_attribute = True
        timing_changed = True

    if "recurrence_rule" in fs or "recurrence_until" in fs:
        until = payload.recurrence_until if "recurrence_until" in fs else task.recurrence_until
        if "recurrence_rule" in fs:
            rule = _rule_or_none(payload.recurrence_rule)
            if rule is None:
                task.recurrence_rule = None
                task.recurrence_mode = RecurrenceMode.NONE
                if "recurrence_until" not in fs:
                    until = None
            else:
                task.recurrence_rule = rule.text
                task.recurrence_mode = RecurrenceMode.RRULE
                until = _earliest(until, rule.until)
                if task.series_index is None:
                    task.series_index = 0
        task.recurrence_until = until
    if task.recurrence_rule and task.due_date is None and task.scheduled_start is None:
        rule = recur.parse_stored(task.recurrence_rule)
        task.due_date = recur.first_on_or_after(rule, clock.today, task.recurrence_until)
        if task.due_date is None:
            raise _never_occurs(rule)
        timing_changed = gave_attribute = True

    if "tags" in fs:
        names = normalize_tags(payload.tags or [])
        await set_task_tags(db, user_id, task.id, await tag_ids_for(db, user_id, names))

    explicit = TaskStatus(payload.status) if "status" in fs and payload.status else None
    was_closed = task.status in CLOSED
    closing = explicit in CLOSED and not was_closed
    reopening = was_closed and explicit is not None and explicit not in CLOSED
    result = PatchResult(task=task)
    if reopening:
        result.removed_next_instance_id = await reopen_task(db, task, explicit, clock)
    elif was_closed:
        if explicit is not None:
            task.status = explicit  # done ↔ cancelled
    elif not closing:
        task.status = derive_status(task, explicit, task.status, gave_attribute=gave_attribute)
    await db.flush()

    if "reminders" in fs:
        await replace_reminders(db, task, reminder_defs(payload.reminders or []), clock)
    elif timing_changed:
        await recompute_reminders(db, task, clock)

    if closing:
        assert explicit is not None
        result.next_instance = await close_task(db, task, explicit, clock.now, clock)
    await db.flush()
    return result


# --------------------------------------------------------------------------- #
# Complete / reopen endpoints, delete
# --------------------------------------------------------------------------- #
async def complete(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, task_id: uuid.UUID, completed_at: datetime | None
) -> tuple[Task, Task | None]:
    task = await get_task(db, user_id, task_id, lock=True)
    when = _completion_time(completed_at, clock)
    if task.status == TaskStatus.DONE:
        return task, await existing_next_instance(db, task)  # a replay: never a second instance
    if task.status == TaskStatus.CANCELLED:
        task.status = TaskStatus.DONE
        if completed_at is not None:
            task.completed_at = when
        await db.flush()
        following = await existing_next_instance(db, task)
        if following is None and task.recurrence_rule:
            following = await _create_next_instance(db, task, clock)
        return task, following
    following = await close_task(db, task, TaskStatus.DONE, when, clock)
    await db.flush()
    return task, following


async def reopen(
    db: AsyncSession, *, user_id: uuid.UUID, clock: Clock, task_id: uuid.UUID
) -> tuple[Task, uuid.UUID | None]:
    task = await get_task(db, user_id, task_id, lock=True)
    if task.status not in CLOSED:
        return task, None
    removed = await reopen_task(db, task, None, clock)
    await db.flush()
    return task, removed


async def delete_task(db: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
    """Subtasks, reminders and tag links go with it (FK cascade); time entries stay, task cleared."""
    result = await db.execute(
        delete(Task).where(Task.id == task_id, Task.user_id == user_id).execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        raise NotFound("Task not found", details={"id": str(task_id)})
