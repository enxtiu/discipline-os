"""Planner (stage 6): PARA containers — areas, projects, resources, tags."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.api.deps import CurrentPrincipal, DbSession, require_device
from app.api.v1.planner import Write, area_list, clock_for, project_list, resource_list, task_list, week_start_of
from app.api.v1.presenters import area_out, project_out, tag_out
from app.models import Area, Project, Task
from app.schemas.common import OkOut
from app.schemas.planner import (
    AreaDetailOut,
    AreaIn,
    AreaListOut,
    AreaOut,
    AreaPatchIn,
    ProjectDetailOut,
    ProjectFilterLiteral,
    ProjectIn,
    ProjectListOut,
    ProjectOut,
    ProjectPatchIn,
    ReorderIn,
    ResourceIn,
    ResourceListOut,
    ResourceOut,
    ResourcePatchIn,
    TagIn,
    TagListOut,
    TagOut,
    TagPatchIn,
)
from app.services.planner import containers
from app.services.planner.common import owned
from app.services.planner.tasks import CLOSED, task_query

router = APIRouter(prefix="/planner", tags=["planner"], dependencies=[Depends(require_device)])

PROJECT_DONE_TASKS = 50
AreaId = Annotated[uuid.UUID, Path(alias="id", description="area id")]
ProjectId = Annotated[uuid.UUID, Path(alias="id", description="project id")]
ResourceId = Annotated[uuid.UUID, Path(alias="id", description="resource id")]
TagId = Annotated[uuid.UUID, Path(alias="id", description="tag id")]


async def _area(db, principal: CurrentPrincipal, area) -> AreaOut:
    stats = await containers.area_stats(db, principal.user_id, [area], clock_for(principal), week_start_of(principal))
    return area_out(area, stats)


async def _project(db, principal: CurrentPrincipal, project) -> ProjectOut:
    return project_out(project, await containers.project_stats(db, principal.user_id, [project], clock_for(principal)))


# --------------------------------------------------------------------------- #
# Areas
# --------------------------------------------------------------------------- #
@router.get("/areas", response_model=AreaListOut, summary="Your areas (sort order, name)")
async def list_areas(principal: CurrentPrincipal, db: DbSession, include_archived: bool = False) -> AreaListOut:
    rows = await containers.list_areas(db, principal.user_id, include_archived=include_archived)
    return AreaListOut(items=await area_list(db, principal, rows, clock_for(principal)))


@router.post("/areas", response_model=AreaOut, status_code=201, summary="Create an area", dependencies=Write)
async def create_area(payload: AreaIn, principal: CurrentPrincipal, db: DbSession) -> AreaOut:
    return await _area(db, principal, await containers.create_area(db, user_id=principal.user_id, payload=payload))


@router.post("/areas/reorder", response_model=OkOut, summary="Set the order of your areas", dependencies=Write)
async def reorder_areas(payload: ReorderIn, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await containers.reorder(db, Area, principal.user_id, payload.ids)
    return OkOut()


@router.get("/areas/{id}", response_model=AreaDetailOut, summary="An area with its projects, tasks, resources")
async def get_area(area_id: AreaId, principal: CurrentPrincipal, db: DbSession) -> AreaDetailOut:
    clock = clock_for(principal)
    area = await owned(db, Area, principal.user_id, area_id, "Area")
    projects = await containers.list_projects(db, principal.user_id, status="open", area_id=area.id)
    rows = list(
        (
            await db.scalars(
                task_query()
                .where(
                    Task.user_id == principal.user_id,
                    Task.area_id == area.id,
                    Task.parent_id.is_(None),
                    Task.status.not_in(CLOSED),
                )
                .order_by(Task.is_pinned.desc(), Task.sort_order, Task.priority, Task.created_at)
            )
        ).all()
    )
    resources = await containers.list_resources(db, principal.user_id, area_id=area.id)
    return AreaDetailOut(
        area=await _area(db, principal, area),
        projects=await project_list(db, principal, projects, clock),
        tasks=await task_list(db, principal, rows, clock),
        resources=await resource_list(db, principal, resources),
    )


@router.patch("/areas/{id}", response_model=AreaOut, summary="Edit or archive an area", dependencies=Write)
async def patch_area(area_id: AreaId, payload: AreaPatchIn, principal: CurrentPrincipal, db: DbSession) -> AreaOut:
    area = await containers.update_area(db, user_id=principal.user_id, area_id=area_id, payload=payload)
    return await _area(db, principal, area)


@router.delete(
    "/areas/{id}",
    status_code=204,
    response_class=Response,
    summary="Delete an area (its projects, tasks and resources stay)",
    dependencies=Write,
)
async def delete_area(area_id: AreaId, principal: CurrentPrincipal, db: DbSession) -> Response:
    await containers.delete_area(db, user_id=principal.user_id, area_id=area_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
@router.get("/projects", response_model=ProjectListOut, summary="Your projects (default: active and on hold)")
async def list_projects(
    principal: CurrentPrincipal,
    db: DbSession,
    status: ProjectFilterLiteral = "open",
    area_id: uuid.UUID | None = None,
) -> ProjectListOut:
    rows = await containers.list_projects(db, principal.user_id, status=status, area_id=area_id)
    return ProjectListOut(items=await project_list(db, principal, rows, clock_for(principal)))


@router.post("/projects", response_model=ProjectOut, status_code=201, summary="Create a project", dependencies=Write)
async def create_project(payload: ProjectIn, principal: CurrentPrincipal, db: DbSession) -> ProjectOut:
    project = await containers.create_project(
        db, user_id=principal.user_id, payload=payload, clock=clock_for(principal)
    )
    return await _project(db, principal, project)


@router.post("/projects/reorder", response_model=OkOut, summary="Set the order of your projects", dependencies=Write)
async def reorder_projects(payload: ReorderIn, principal: CurrentPrincipal, db: DbSession) -> OkOut:
    await containers.reorder(db, Project, principal.user_id, payload.ids)
    return OkOut()


@router.get("/projects/{id}", response_model=ProjectDetailOut, summary="A project with its tasks")
async def get_project(project_id: ProjectId, principal: CurrentPrincipal, db: DbSession) -> ProjectDetailOut:
    clock = clock_for(principal)
    project = await owned(db, Project, principal.user_id, project_id, "Project")
    base = (Task.user_id == principal.user_id, Task.project_id == project.id, Task.parent_id.is_(None))
    open_rows = list(
        (
            await db.scalars(
                task_query()
                .where(*base, Task.status.not_in(CLOSED))
                .order_by(Task.is_pinned.desc(), Task.sort_order, Task.priority, Task.created_at)
            )
        ).all()
    )
    done_rows = list(
        (
            await db.scalars(
                task_query()
                .where(*base, Task.status == CLOSED[0])
                .order_by(Task.completed_at.desc().nulls_last(), Task.updated_at.desc())
                .limit(PROJECT_DONE_TASKS)
            )
        ).all()
    )
    return ProjectDetailOut(
        project=await _project(db, principal, project),
        tasks=await task_list(db, principal, open_rows, clock),
        done=await task_list(db, principal, done_rows, clock),
    )


@router.patch(
    "/projects/{id}",
    response_model=ProjectOut,
    summary="Edit a project (completing it sets completed_at, archiving hides it)",
    dependencies=Write,
)
async def patch_project(
    project_id: ProjectId, payload: ProjectPatchIn, principal: CurrentPrincipal, db: DbSession
) -> ProjectOut:
    project = await containers.update_project(
        db, user_id=principal.user_id, project_id=project_id, payload=payload, clock=clock_for(principal)
    )
    return await _project(db, principal, project)


@router.delete(
    "/projects/{id}",
    status_code=204,
    response_class=Response,
    summary="Delete a project (its tasks stay; root tasks left without a home go back to the inbox)",
    dependencies=Write,
)
async def delete_project(project_id: ProjectId, principal: CurrentPrincipal, db: DbSession) -> Response:
    await containers.delete_project(db, user_id=principal.user_id, project_id=project_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #
@router.get("/resources", response_model=ResourceListOut, summary="Your resources (newest first)")
async def list_resources(
    principal: CurrentPrincipal,
    db: DbSession,
    area_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> ResourceListOut:
    rows = await containers.list_resources(db, principal.user_id, area_id=area_id, include_archived=include_archived)
    return ResourceListOut(items=await resource_list(db, principal, rows))


async def _resource(db, principal: CurrentPrincipal, resource) -> ResourceOut:
    return (await resource_list(db, principal, [resource]))[0]


@router.post("/resources", response_model=ResourceOut, status_code=201, summary="Create a resource", dependencies=Write)
async def create_resource(payload: ResourceIn, principal: CurrentPrincipal, db: DbSession) -> ResourceOut:
    resource = await containers.create_resource(db, user_id=principal.user_id, payload=payload)
    return await _resource(db, principal, resource)


@router.patch("/resources/{id}", response_model=ResourceOut, summary="Edit or archive a resource", dependencies=Write)
async def patch_resource(
    resource_id: ResourceId, payload: ResourcePatchIn, principal: CurrentPrincipal, db: DbSession
) -> ResourceOut:
    resource = await containers.update_resource(db, user_id=principal.user_id, resource_id=resource_id, payload=payload)
    return await _resource(db, principal, resource)


@router.delete(
    "/resources/{id}",
    status_code=204,
    response_class=Response,
    summary="Delete a resource",
    dependencies=Write,
)
async def delete_resource(resource_id: ResourceId, principal: CurrentPrincipal, db: DbSession) -> Response:
    await containers.delete_resource(db, user_id=principal.user_id, resource_id=resource_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #
async def _tag(db, principal: CurrentPrincipal, tag) -> TagOut:
    counts = await containers.tag_open_counts(db, principal.user_id, [tag.id])
    return tag_out(tag, counts.get(tag.id, 0))


@router.get("/tags", response_model=TagListOut, summary="Your tags with their open task counts")
async def list_tags(principal: CurrentPrincipal, db: DbSession) -> TagListOut:
    rows = await containers.list_tags(db, principal.user_id)
    counts = await containers.tag_open_counts(db, principal.user_id, [row.id for row in rows])
    return TagListOut(items=[tag_out(row, counts.get(row.id, 0)) for row in rows])


@router.post("/tags", response_model=TagOut, status_code=201, summary="Create a tag", dependencies=Write)
async def create_tag(payload: TagIn, principal: CurrentPrincipal, db: DbSession) -> TagOut:
    return await _tag(db, principal, await containers.create_tag(db, user_id=principal.user_id, payload=payload))


@router.patch("/tags/{id}", response_model=TagOut, summary="Rename or recolour a tag", dependencies=Write)
async def patch_tag(tag_id: TagId, payload: TagPatchIn, principal: CurrentPrincipal, db: DbSession) -> TagOut:
    tag = await containers.update_tag(db, user_id=principal.user_id, tag_id=tag_id, payload=payload)
    return await _tag(db, principal, tag)


@router.delete(
    "/tags/{id}",
    status_code=204,
    response_class=Response,
    summary="Delete a tag (tasks keep, the tag is removed from them)",
    dependencies=Write,
)
async def delete_tag(tag_id: TagId, principal: CurrentPrincipal, db: DbSession) -> Response:
    await containers.delete_tag(db, user_id=principal.user_id, tag_id=tag_id)
    return Response(status_code=204)
