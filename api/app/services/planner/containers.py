"""PARA containers: areas, projects, resources and tags.

* Area and tag names are unique per user case-insensitively (``name_taken``);
  a replayed create (same client id) returns the row instead.
* Deleting an area or a project keeps its tasks: they lose the container, and
  open root tasks left without area/project/date/block go back to the inbox.
* Statistics (open tasks, active projects, tracked minutes) are computed in
  batches for a whole list.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.ids import new_id
from app.models import Area, Project, ProjectStatus, Resource, Tag, Task, TaskStatus, TimeEntry, task_tags
from app.services.planner.common import Clock, RefTuple, id_conflict, normalize_tag, normalize_tags, owned
from app.services.planner.tasks import CLOSED

OPEN_PROJECT = (ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD)


def week_start_for(day: date, week_start: int) -> date:
    """First day of ``day``'s week; ``week_start`` follows settings (0 = Sunday … 6 = Saturday)."""
    first_weekday = (week_start - 1) % 7  # Python: Monday = 0
    return day - timedelta(days=(day.weekday() - first_weekday) % 7)


def entry_seconds(now: datetime):
    """Seconds of a time entry; a running one counts up to ``now``."""
    from sqlalchemy import DateTime, literal

    running = func.greatest(0, func.extract("epoch", literal(now, DateTime(timezone=True)) - TimeEntry.started_at))
    return func.coalesce(TimeEntry.duration_s, running)


def _name_taken(name: str) -> Conflict:
    return Conflict("This name is already used", code="name_taken", details={"name": name})


async def _insert_once(db: AsyncSession, row, model, user_id: uuid.UUID, what: str):
    """Insert a client-keyed row; a concurrent replay returns the winner, a name race → name_taken."""
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = await db.get(model, row.id, populate_existing=True)
        if existing is None:
            raise _name_taken(getattr(row, "name", "")) from None
        if existing.user_id != user_id:
            raise id_conflict(what) from None
        return existing
    return row


async def _replay(db: AsyncSession, model, row_id: uuid.UUID | None, user_id: uuid.UUID, what: str):
    if row_id is None:
        return None
    existing = await db.get(model, row_id)
    if existing is None:
        return None
    if existing.user_id != user_id:
        raise id_conflict(what)
    return existing


async def reorder(db: AsyncSession, model, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
    """``sort_order`` = position; ids that are not yours (or no longer exist) are ignored."""
    order = {row_id: index for index, row_id in enumerate(dict.fromkeys(ids))}
    await db.execute(
        update(model)
        .where(model.id.in_(list(order)), model.user_id == user_id)
        .values(sort_order=case(order, value=model.id))
        .execution_options(synchronize_session="fetch")
    )


async def _orphans_to_inbox(db: AsyncSession, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
    if not ids:
        return
    await db.execute(
        update(Task)
        .where(
            Task.id.in_(list(ids)),
            Task.user_id == user_id,
            Task.parent_id.is_(None),
            Task.project_id.is_(None),
            Task.area_id.is_(None),
            Task.due_date.is_(None),
            Task.scheduled_start.is_(None),
            Task.status.not_in((*CLOSED, TaskStatus.INBOX)),
        )
        .values(status=TaskStatus.INBOX)
        .execution_options(synchronize_session="fetch")
    )


# --------------------------------------------------------------------------- #
# Areas
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class AreaStats:
    open_tasks: dict[uuid.UUID, int] = field(default_factory=dict)
    active_projects: dict[uuid.UUID, int] = field(default_factory=dict)
    week_minutes: dict[uuid.UUID, float] = field(default_factory=dict)  # seconds, rounded by the presenter


async def area_stats(
    db: AsyncSession, user_id: uuid.UUID, areas: Sequence[Area], clock: Clock, week_start: int
) -> AreaStats:
    stats = AreaStats()
    ids = [area.id for area in areas]
    if not ids:
        return stats
    rows = await db.execute(
        select(Task.area_id, func.count())
        .where(Task.area_id.in_(ids), Task.user_id == user_id, Task.parent_id.is_(None), Task.status.not_in(CLOSED))
        .group_by(Task.area_id)
    )
    stats.open_tasks = {row[0]: int(row[1]) for row in rows}
    rows = await db.execute(
        select(Project.area_id, func.count())
        .where(
            Project.area_id.in_(ids),
            Project.user_id == user_id,
            Project.status == ProjectStatus.ACTIVE,
            Project.is_archived.is_(False),
        )
        .group_by(Project.area_id)
    )
    stats.active_projects = {row[0]: int(row[1]) for row in rows}
    first = week_start_for(clock.today, week_start)
    rows = await db.execute(
        select(TimeEntry.area_id, func.sum(entry_seconds(clock.now)))
        .where(
            TimeEntry.area_id.in_(ids),
            TimeEntry.user_id == user_id,
            TimeEntry.is_deleted.is_(False),
            TimeEntry.local_day.between(first, first + timedelta(days=6)),
        )
        .group_by(TimeEntry.area_id)
    )
    stats.week_minutes = {row[0]: float(row[1] or 0) for row in rows}
    return stats


async def list_areas(db: AsyncSession, user_id: uuid.UUID, *, include_archived: bool = False) -> list[Area]:
    stmt = select(Area).where(Area.user_id == user_id)
    if not include_archived:
        stmt = stmt.where(Area.is_archived.is_(False))
    return list((await db.scalars(stmt.order_by(Area.sort_order, func.lower(Area.name), Area.id))).all())


async def _check_area_name(db: AsyncSession, user_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> None:
    stmt = select(Area.id).where(Area.user_id == user_id, func.lower(Area.name) == func.lower(name))
    if exclude is not None:
        stmt = stmt.where(Area.id != exclude)
    if await db.scalar(stmt.limit(1)) is not None:
        raise _name_taken(name)


async def create_area(db: AsyncSession, *, user_id: uuid.UUID, payload) -> Area:
    existing = await _replay(db, Area, payload.id, user_id, "area")
    if existing is not None:
        return existing
    await _check_area_name(db, user_id, payload.name)
    top = await db.scalar(select(func.max(Area.sort_order)).where(Area.user_id == user_id))
    area = Area(
        id=payload.id or new_id(),
        user_id=user_id,
        name=payload.name,
        description=payload.description,
        color=payload.color,
        icon=payload.icon,
        weekly_target_minutes=payload.weekly_target_minutes,
        sort_order=min((top or 0) + 1, 32767),
        is_archived=False,
    )
    return await _insert_once(db, area, Area, user_id, "area")


async def update_area(db: AsyncSession, *, user_id: uuid.UUID, area_id: uuid.UUID, payload) -> Area:
    area = await owned(db, Area, user_id, area_id, "Area")
    fs = payload.model_fields_set
    if "name" in fs and payload.name != area.name:
        await _check_area_name(db, user_id, payload.name, exclude=area.id)
    for name in ("name", "description", "color", "icon", "weekly_target_minutes", "is_archived", "sort_order"):
        if name in fs:
            setattr(area, name, getattr(payload, name))
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise _name_taken(payload.name or "") from None
    return area


async def delete_area(db: AsyncSession, *, user_id: uuid.UUID, area_id: uuid.UUID) -> None:
    area = await owned(db, Area, user_id, area_id, "Area")
    affected = (await db.scalars(select(Task.id).where(Task.area_id == area.id, Task.user_id == user_id))).all()
    await db.execute(delete(Area).where(Area.id == area.id).execution_options(synchronize_session=False))
    db.expunge(area)
    await db.flush()
    await _orphans_to_inbox(db, user_id, affected)


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ProjectStats:
    today: date
    counts: dict[uuid.UUID, tuple[int, int]] = field(default_factory=dict)  # (open, done) root tasks
    tracked: dict[uuid.UUID, float] = field(default_factory=dict)  # seconds
    areas: dict[uuid.UUID, RefTuple] = field(default_factory=dict)


async def project_stats(
    db: AsyncSession, user_id: uuid.UUID, projects: Sequence[Project], clock: Clock
) -> ProjectStats:
    stats = ProjectStats(today=clock.today)
    ids = [project.id for project in projects]
    if not ids:
        return stats
    rows = await db.execute(
        select(
            Task.project_id,
            func.count().filter(Task.status.not_in(CLOSED)),
            func.count().filter(Task.status == TaskStatus.DONE),
        )
        .where(Task.project_id.in_(ids), Task.user_id == user_id, Task.parent_id.is_(None))
        .group_by(Task.project_id)
    )
    stats.counts = {row[0]: (int(row[1]), int(row[2])) for row in rows}
    rows = await db.execute(
        select(TimeEntry.project_id, func.sum(entry_seconds(clock.now)))
        .where(TimeEntry.project_id.in_(ids), TimeEntry.user_id == user_id, TimeEntry.is_deleted.is_(False))
        .group_by(TimeEntry.project_id)
    )
    stats.tracked = {row[0]: float(row[1] or 0) for row in rows}
    area_ids = {project.area_id for project in projects if project.area_id is not None}
    if area_ids:
        rows = await db.execute(
            select(Area.id, Area.name, Area.color).where(Area.id.in_(area_ids), Area.user_id == user_id)
        )
        stats.areas = {row.id: (row.id, row.name, row.color) for row in rows}
    return stats


async def list_projects(
    db: AsyncSession, user_id: uuid.UUID, *, status: str = "open", area_id: uuid.UUID | None = None
) -> list[Project]:
    stmt = select(Project).where(Project.user_id == user_id)
    if status == "open":
        stmt = stmt.where(Project.status.in_(OPEN_PROJECT), Project.is_archived.is_(False))
    elif status == "archived":
        stmt = stmt.where(or_(Project.status == ProjectStatus.ARCHIVED, Project.is_archived.is_(True)))
    elif status in ("active", "on_hold"):
        stmt = stmt.where(Project.status == ProjectStatus(status), Project.is_archived.is_(False))
    elif status != "all":
        stmt = stmt.where(Project.status == ProjectStatus(status))
    if area_id is not None:
        stmt = stmt.where(Project.area_id == area_id)
    return list((await db.scalars(stmt.order_by(Project.sort_order, func.lower(Project.name), Project.id))).all())


def _apply_project_status(project: Project, status: ProjectStatus, now: datetime) -> None:
    if status == ProjectStatus.COMPLETED:
        if project.status != ProjectStatus.COMPLETED or project.completed_at is None:
            project.completed_at = now
    else:
        project.completed_at = None
    if status == ProjectStatus.ARCHIVED:
        project.is_archived = True
    project.status = status


async def create_project(db: AsyncSession, *, user_id: uuid.UUID, payload, clock: Clock) -> Project:
    existing = await _replay(db, Project, payload.id, user_id, "project")
    if existing is not None:
        return existing
    if payload.area_id is not None:
        await owned(db, Area, user_id, payload.area_id, "Area")
    project = Project(
        id=payload.id or new_id(),
        user_id=user_id,
        name=payload.name,
        outcome=payload.outcome,
        description_md=payload.description_md,
        area_id=payload.area_id,
        status=ProjectStatus.ACTIVE,
        deadline=payload.deadline,
        start_date=payload.start_date,
        color=payload.color,
        icon=payload.icon,
        is_archived=False,
        sort_order=0,
    )
    if payload.status:
        _apply_project_status(project, ProjectStatus(payload.status), clock.now)
    return await _insert_once(db, project, Project, user_id, "project")


async def update_project(
    db: AsyncSession, *, user_id: uuid.UUID, project_id: uuid.UUID, payload, clock: Clock
) -> Project:
    project = await owned(db, Project, user_id, project_id, "Project")
    fs = payload.model_fields_set
    if "area_id" in fs and payload.area_id is not None:
        await owned(db, Area, user_id, payload.area_id, "Area")
    for name in (
        "name",
        "outcome",
        "description_md",
        "area_id",
        "deadline",
        "start_date",
        "color",
        "icon",
        "sort_order",
    ):
        if name in fs:
            setattr(project, name, getattr(payload, name))
    if "is_archived" in fs:
        project.is_archived = payload.is_archived
        if not payload.is_archived and project.status == ProjectStatus.ARCHIVED and "status" not in fs:
            project.status = ProjectStatus.ACTIVE  # restored from the archive
    if "status" in fs:
        _apply_project_status(project, ProjectStatus(payload.status), clock.now)
    await db.flush()
    return project


async def delete_project(db: AsyncSession, *, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
    project = await owned(db, Project, user_id, project_id, "Project")
    affected = (await db.scalars(select(Task.id).where(Task.project_id == project.id, Task.user_id == user_id))).all()
    await db.execute(delete(Project).where(Project.id == project.id).execution_options(synchronize_session=False))
    db.expunge(project)
    await db.flush()
    await _orphans_to_inbox(db, user_id, affected)


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #
async def list_resources(
    db: AsyncSession, user_id: uuid.UUID, *, area_id: uuid.UUID | None = None, include_archived: bool = False
) -> list[Resource]:
    stmt = select(Resource).where(Resource.user_id == user_id)
    if area_id is not None:
        stmt = stmt.where(Resource.area_id == area_id)
    if not include_archived:
        stmt = stmt.where(Resource.is_archived.is_(False))
    return list((await db.scalars(stmt.order_by(Resource.created_at.desc(), Resource.id.desc()))).all())


async def create_resource(db: AsyncSession, *, user_id: uuid.UUID, payload) -> Resource:
    existing = await _replay(db, Resource, payload.id, user_id, "resource")
    if existing is not None:
        return existing
    if payload.area_id is not None:
        await owned(db, Area, user_id, payload.area_id, "Area")
    resource = Resource(
        id=payload.id or new_id(),
        user_id=user_id,
        name=payload.name,
        notes_md=payload.notes_md,
        url=payload.url,
        area_id=payload.area_id,
        tags=normalize_tags(payload.tags),
        is_archived=False,
    )
    return await _insert_once(db, resource, Resource, user_id, "resource")


async def update_resource(db: AsyncSession, *, user_id: uuid.UUID, resource_id: uuid.UUID, payload) -> Resource:
    resource = await owned(db, Resource, user_id, resource_id, "Resource")
    fs = payload.model_fields_set
    if "area_id" in fs and payload.area_id is not None:
        await owned(db, Area, user_id, payload.area_id, "Area")
    for name in ("name", "notes_md", "url", "area_id", "is_archived"):
        if name in fs:
            setattr(resource, name, getattr(payload, name))
    if "tags" in fs:
        resource.tags = normalize_tags(payload.tags or [])
    await db.flush()
    return resource


async def delete_resource(db: AsyncSession, *, user_id: uuid.UUID, resource_id: uuid.UUID) -> None:
    result = await db.execute(
        delete(Resource)
        .where(Resource.id == resource_id, Resource.user_id == user_id)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        raise NotFound("Resource not found", details={"id": str(resource_id)})


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #
async def tag_open_counts(db: AsyncSession, user_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not tag_ids:
        return {}
    rows = await db.execute(
        select(task_tags.c.tag_id, func.count())
        .join(Task, Task.id == task_tags.c.task_id)
        .where(task_tags.c.tag_id.in_(list(tag_ids)), task_tags.c.user_id == user_id, Task.status.not_in(CLOSED))
        .group_by(task_tags.c.tag_id)
    )
    return {row[0]: int(row[1]) for row in rows}


async def list_tags(db: AsyncSession, user_id: uuid.UUID) -> list[Tag]:
    return list(
        (await db.scalars(select(Tag).where(Tag.user_id == user_id).order_by(func.lower(Tag.name), Tag.id))).all()
    )


async def _check_tag_name(db: AsyncSession, user_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None) -> None:
    stmt = select(Tag.id).where(Tag.user_id == user_id, func.lower(Tag.name) == func.lower(name))
    if exclude is not None:
        stmt = stmt.where(Tag.id != exclude)
    if await db.scalar(stmt.limit(1)) is not None:
        raise _name_taken(name)


async def create_tag(db: AsyncSession, *, user_id: uuid.UUID, payload) -> Tag:
    existing = await _replay(db, Tag, payload.id, user_id, "tag")
    if existing is not None:
        return existing
    name = normalize_tag(payload.name)
    await _check_tag_name(db, user_id, name)
    tag = Tag(id=payload.id or new_id(), user_id=user_id, name=name, color=payload.color, kind="tag")
    return await _insert_once(db, tag, Tag, user_id, "tag")


async def update_tag(db: AsyncSession, *, user_id: uuid.UUID, tag_id: uuid.UUID, payload) -> Tag:
    tag = await owned(db, Tag, user_id, tag_id, "Tag")
    fs = payload.model_fields_set
    if "name" in fs:
        name = normalize_tag(payload.name or "")
        if name != tag.name:
            await _check_tag_name(db, user_id, name, exclude=tag.id)
            tag.name = name
    if "color" in fs:
        tag.color = payload.color
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        raise _name_taken(tag.name) from None
    return tag


async def delete_tag(db: AsyncSession, *, user_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    result = await db.execute(
        delete(Tag).where(Tag.id == tag_id, Tag.user_id == user_id).execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        raise NotFound("Tag not found", details={"id": str(tag_id)})
