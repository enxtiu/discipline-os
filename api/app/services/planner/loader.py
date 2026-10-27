"""Batch loading of everything a task or time-entry list shows.

A list of N tasks costs a fixed number of queries (tags, projects, areas,
parents, subtask counts, reminders) — never one query per task.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Area, Project, Reminder, Tag, Task, TaskStatus, TimeEntry, task_tags
from app.services.planner.common import Clock, RefTuple, ref_map
from app.services.planner.tasks import MAX_DEPTH, task_query

ENTRIES_PER_TASK = 10


@dataclass(slots=True)
class TaskRefs:
    clock: Clock
    tags: dict[uuid.UUID, list[RefTuple]] = field(default_factory=dict)
    projects: dict[uuid.UUID, RefTuple] = field(default_factory=dict)
    areas: dict[uuid.UUID, RefTuple] = field(default_factory=dict)
    parents: dict[uuid.UUID, tuple[str, uuid.UUID | None]] = field(default_factory=dict)
    counts: dict[uuid.UUID, tuple[int, int]] = field(default_factory=dict)  # id -> (done, total)
    reminders: dict[uuid.UUID, list[Reminder]] = field(default_factory=dict)

    def depth(self, task: Task) -> int:
        if task.parent_id is None:
            return 0
        parent = self.parents.get(task.parent_id)
        return 1 if parent is None or parent[1] is None else 2


@dataclass(slots=True)
class EntryRefs:
    clock: Clock
    tasks: dict[uuid.UUID, str] = field(default_factory=dict)
    projects: dict[uuid.UUID, RefTuple] = field(default_factory=dict)
    areas: dict[uuid.UUID, RefTuple] = field(default_factory=dict)


@dataclass(slots=True)
class TaskTree:
    """A task with its subtree, the refs of every node and the latest time entries."""

    root: Task
    children: dict[uuid.UUID, list[Task]]
    refs: TaskRefs
    entries: dict[uuid.UUID, list[TimeEntry]]
    entry_refs: EntryRefs


async def project_area_refs(
    db: AsyncSession, user_id: uuid.UUID, project_ids: Iterable[uuid.UUID | None], area_ids: Iterable[uuid.UUID | None]
) -> tuple[dict[uuid.UUID, RefTuple], dict[uuid.UUID, RefTuple]]:
    pids = {value for value in project_ids if value is not None}
    aids = {value for value in area_ids if value is not None}
    projects: dict[uuid.UUID, RefTuple] = {}
    areas: dict[uuid.UUID, RefTuple] = {}
    if pids:
        rows = await db.execute(
            select(Project.id, Project.name, Project.color).where(Project.id.in_(pids), Project.user_id == user_id)
        )
        projects = ref_map(rows.all())
    if aids:
        rows = await db.execute(
            select(Area.id, Area.name, Area.color).where(Area.id.in_(aids), Area.user_id == user_id)
        )
        areas = ref_map(rows.all())
    return projects, areas


async def load_refs(db: AsyncSession, user_id: uuid.UUID, tasks: Sequence[Task], clock: Clock) -> TaskRefs:
    refs = TaskRefs(clock=clock)
    if not tasks:
        return refs
    ids = list({task.id for task in tasks})
    refs.projects, refs.areas = await project_area_refs(
        db, user_id, (task.project_id for task in tasks), (task.area_id for task in tasks)
    )
    rows = await db.execute(
        select(task_tags.c.task_id, Tag.id, Tag.name, Tag.color)
        .join(Tag, Tag.id == task_tags.c.tag_id)
        .where(task_tags.c.task_id.in_(ids), task_tags.c.user_id == user_id)
        .order_by(func.lower(Tag.name))
    )
    for row in rows:
        refs.tags.setdefault(row.task_id, []).append((row.id, row.name, row.color))
    parent_ids = {task.parent_id for task in tasks if task.parent_id is not None}
    if parent_ids:
        rows = await db.execute(
            select(Task.id, Task.title, Task.parent_id).where(Task.id.in_(parent_ids), Task.user_id == user_id)
        )
        refs.parents = {row.id: (row.title, row.parent_id) for row in rows}
    rows = await db.execute(
        select(
            Task.parent_id,
            func.count().filter(Task.status == TaskStatus.DONE),
            func.count().filter(Task.status != TaskStatus.CANCELLED),
        )
        .where(Task.parent_id.in_(ids), Task.user_id == user_id)
        .group_by(Task.parent_id)
    )
    refs.counts = {row[0]: (int(row[1]), int(row[2])) for row in rows}
    reminders = await db.scalars(
        select(Reminder)
        .where(Reminder.task_id.in_(ids), Reminder.user_id == user_id)
        .order_by(Reminder.remind_at, Reminder.id)
        .execution_options(populate_existing=True)
    )
    for reminder in reminders:
        refs.reminders.setdefault(reminder.task_id, []).append(reminder)
    return refs


async def load_entry_refs(
    db: AsyncSession, user_id: uuid.UUID, entries: Sequence[TimeEntry], clock: Clock
) -> EntryRefs:
    refs = EntryRefs(clock=clock)
    if not entries:
        return refs
    task_ids = {entry.task_id for entry in entries if entry.task_id is not None}
    if task_ids:
        rows = await db.execute(select(Task.id, Task.title).where(Task.id.in_(task_ids), Task.user_id == user_id))
        refs.tasks = {row.id: row.title for row in rows}
    refs.projects, refs.areas = await project_area_refs(
        db, user_id, (entry.project_id for entry in entries), (entry.area_id for entry in entries)
    )
    return refs


async def latest_entries(
    db: AsyncSession, user_id: uuid.UUID, task_ids: Sequence[uuid.UUID], per_task: int = ENTRIES_PER_TASK
) -> dict[uuid.UUID, list[TimeEntry]]:
    if not task_ids:
        return {}
    ranked = (
        select(
            TimeEntry,
            func.row_number()
            .over(partition_by=TimeEntry.task_id, order_by=(TimeEntry.started_at.desc(), TimeEntry.id.desc()))
            .label("rank"),
        )
        .where(TimeEntry.task_id.in_(task_ids), TimeEntry.user_id == user_id, TimeEntry.is_deleted.is_(False))
        .subquery()
    )
    entry = aliased(TimeEntry, ranked)
    rows = await db.scalars(
        select(entry)
        .where(ranked.c.rank <= per_task)
        .order_by(ranked.c.started_at.desc())
        .execution_options(populate_existing=True)
    )
    out: dict[uuid.UUID, list[TimeEntry]] = {}
    for row in rows:
        out.setdefault(row.task_id, []).append(row)
    return out


async def load_tree(db: AsyncSession, user_id: uuid.UUID, task: Task, clock: Clock) -> TaskTree:
    children: dict[uuid.UUID, list[Task]] = {}
    every = [task]
    level = [task.id]
    for _ in range(MAX_DEPTH):
        rows = (
            await db.scalars(
                task_query()
                .where(Task.parent_id.in_(level), Task.user_id == user_id)
                .order_by(Task.sort_order, Task.created_at, Task.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        if not rows:
            break
        for row in rows:
            children.setdefault(row.parent_id, []).append(row)
        every.extend(rows)
        level = [row.id for row in rows]
    refs = await load_refs(db, user_id, every, clock)
    entries = await latest_entries(db, user_id, [row.id for row in every])
    flat = [entry for group in entries.values() for entry in group]
    entry_refs = await load_entry_refs(db, user_id, flat, clock)
    return TaskTree(root=task, children=children, refs=refs, entries=entries, entry_refs=entry_refs)


async def reload_tasks(db: AsyncSession, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> list[Task]:
    """Fresh rows (after bulk updates) in the given order."""
    if not ids:
        return []
    rows = (
        await db.scalars(
            task_query()
            .where(Task.id.in_(list(ids)), Task.user_id == user_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    by_id = {row.id: row for row in rows}
    return [by_id[task_id] for task_id in ids if task_id in by_id]
