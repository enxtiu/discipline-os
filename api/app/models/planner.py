"""PARA planner: areas, projects, resources, inbox, tasks, time tracking, journal."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import new_id
from app.models.base import (
    Base,
    JsonType,
    TzDateTime,
    UiEnum,
    UserScopedMixin,
    UuidPkMixin,
    UuidType,
    enum_col,
)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class ParaKind(UiEnum):
    PROJECT = "project"
    AREA = "area"
    RESOURCE = "resource"


class ProjectStatus(UiEnum):
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class TaskStatus(UiEnum):
    INBOX = "inbox"  # captured, not yet clarified
    OPEN = "open"
    SCHEDULED = "scheduled"  # has a time block
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class RecurrenceMode(UiEnum):
    NONE = "none"
    RRULE = "rrule"


class TimeEntrySource(UiEnum):
    TIMER = "timer"
    MANUAL = "manual"
    SHORTCUT = "shortcut"
    IMPORT = "import"


class ReminderKind(UiEnum):
    TASK = "task"
    WATER = "water"
    MEAL = "meal"
    WORKOUT = "workout"
    EVENING_REVIEW = "evening_review"
    STREAK = "streak"
    CUSTOM = "custom"


class ReminderStatus(UiEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPPRESSED = "suppressed"


# --------------------------------------------------------------------------- #
# PARA containers
# --------------------------------------------------------------------------- #
class Area(UserScopedMixin, UuidPkMixin, Base):
    """Ongoing responsibility: Health, Work, Finance, Learning, Home…"""

    __tablename__ = "areas"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(48), nullable=True)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weekly_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_areas_user_name"),
        # stage 6 (migration 0002): names are unique case-insensitively
        Index("uq_areas_user_lower_name", "user_id", sa.text("lower(name)"), unique=True),
    )


class Project(UserScopedMixin, UuidPkMixin, Base):
    """A finite outcome with a deadline."""

    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[ProjectStatus] = mapped_column(
        enum_col(ProjectStatus, "project_status"), nullable=False, default=ProjectStatus.ACTIVE
    )
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(48), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    # later stages: Obsidian / Notion round-trip
    external_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_meta: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("ix_projects_area_status", "area_id", "status"),
        Index(
            "uq_projects_external",
            "user_id",
            "external_source",
            "external_id",
            unique=True,
            postgresql_where=sa.text("external_id IS NOT NULL"),
        ),
        Index("ix_projects_user_status_deadline", "user_id", "status", "deadline"),
    )


class Resource(UserScopedMixin, UuidPkMixin, Base):
    """A topic of ongoing interest (PARA "resource")."""

    __tablename__ = "resources"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    notes_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tags: Mapped[list[str]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'[]'::jsonb"))
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_resources_user_archived", "user_id", "is_archived"),)


class Tag(UserScopedMixin, UuidPkMixin, Base):
    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="tag")

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_tags_user_name"),
        # stage 6 (migration 0002): names are unique case-insensitively
        Index("uq_tags_user_lower_name", "user_id", sa.text("lower(name)"), unique=True),
    )


task_tags = sa.Table(
    "task_tags",
    Base.metadata,
    sa.Column("task_id", UuidType, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("tag_id", UuidType, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("user_id", UuidType, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Index("ix_task_tags_tag", "tag_id"),
    sa.Index("ix_task_tags_user", "user_id"),
)


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
class Task(UserScopedMixin, UuidPkMixin, Base):
    """A single actionable item.  ``parent_id`` gives subtask trees (>= 2 levels)."""

    __tablename__ = "tasks"

    client_uuid: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False, unique=True, default=new_id)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    notes_md: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[TaskStatus] = mapped_column(
        enum_col(TaskStatus, "task_status"), nullable=False, default=TaskStatus.OPEN, index=True
    )
    # 1 = P1 (highest) … 4 = P4
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # ---- timing ------------------------------------------------------------
    due_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)  # exact instant
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # local day only
    scheduled_start: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    scheduled_end: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estimate_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)

    # ---- recurrence --------------------------------------------------------
    recurrence_mode: Mapped[RecurrenceMode] = mapped_column(
        enum_col(RecurrenceMode, "recurrence_mode"), nullable=False, default=RecurrenceMode.NONE
    )
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)  # RRULE
    recurrence_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    recurrence_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    series_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---- extras ------------------------------------------------------------
    tags_snapshot: Mapped[list[str]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'[]'::jsonb"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    # pomodoro/energy metadata for later stages
    energy: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1..3
    context: Mapped[str | None] = mapped_column(String(64), nullable=True)  # @home, @phone…

    external_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_meta: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    search_vector_ru: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True, info={"read_only": True})
    search_vector_en: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True, info={"read_only": True})

    # Two FKs point at tasks (parent_id and recurrence_parent_id), so the
    # self-referential relationship must say which one it uses.
    subtasks: Mapped[list[Task]] = relationship(
        "Task",
        back_populates="parent",
        foreign_keys="Task.parent_id",
        cascade="all, delete-orphan",
        lazy="selectin",
        join_depth=2,  # eager-load at most 2 levels of subtasks
    )
    parent: Mapped[Task | None] = relationship(
        "Task",
        back_populates="subtasks",
        foreign_keys="Task.parent_id",
        remote_side="Task.id",
    )
    tags: Mapped[list[Tag]] = relationship(secondary=task_tags, lazy="selectin")

    __table_args__ = (
        CheckConstraint("priority >= 1 AND priority <= 4", name="priority_range"),
        CheckConstraint("energy IS NULL OR (energy >= 1 AND energy <= 3)", name="energy_range"),
        CheckConstraint(
            "scheduled_end IS NULL OR scheduled_start IS NULL OR scheduled_end >= scheduled_start",
            name="schedule_order",
        ),
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_due", "user_id", "due_at"),
        Index("ix_tasks_user_due_date", "user_id", "due_date"),
        Index("ix_tasks_user_scheduled", "user_id", "scheduled_start"),
        Index("ix_tasks_parent", "parent_id"),
        Index("ix_tasks_search_ru", "search_vector_ru", postgresql_using="gin"),
        Index("ix_tasks_search_en", "search_vector_en", postgresql_using="gin"),
        Index(
            "ix_tasks_user_open_priority",
            "user_id",
            "priority",
            "due_date",
            postgresql_where=sa.text("status NOT IN ('done', 'cancelled')"),
        ),
        Index(
            "uq_tasks_external",
            "user_id",
            "external_source",
            "external_id",
            unique=True,
            postgresql_where=sa.text("external_id IS NOT NULL"),
        ),
        # stage 6 (migration 0002): one next instance per series position; done lists
        Index(
            "uq_tasks_recurrence_series",
            "recurrence_parent_id",
            "series_index",
            unique=True,
            postgresql_where=sa.text("recurrence_parent_id IS NOT NULL"),
        ),
        Index("ix_tasks_user_completed", "user_id", "completed_at"),
    )


# --------------------------------------------------------------------------- #
# Time tracking
# --------------------------------------------------------------------------- #
class TimeEntry(UserScopedMixin, UuidPkMixin, Base):
    """A tracked interval.  ``ended_at IS NULL`` == timer currently running.

    The partial unique index below enforces a single running timer per user,
    which makes the "start/stop" API safe under double-taps and multi-device.
    """

    __tablename__ = "time_entries"

    client_uuid: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False, unique=True, default=new_id)

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("areas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[TimeEntrySource] = mapped_column(
        enum_col(TimeEntrySource, "time_entry_source"), nullable=False, default=TimeEntrySource.TIMER
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="time_order"),
        Index("ix_time_entries_user_day", "user_id", "local_day"),
        Index(
            "uq_time_entries_running",
            "user_id",
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL AND is_deleted = false"),
        ),
    )


# --------------------------------------------------------------------------- #
# Journal
# --------------------------------------------------------------------------- #
class JournalEntry(UserScopedMixin, UuidPkMixin, Base):
    """End-of-day note + rating.  One per local day."""

    __tablename__ = "journal_entries"

    client_uuid: Mapped[uuid.UUID] = mapped_column(UuidType, nullable=False, unique=True, default=new_id)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1..5
    mood: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1..5
    energy: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1..5
    wins_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvements_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'[]'::jsonb"))
    prompt_version: Mapped[str | None] = mapped_column(String(24), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "entry_date", name="uq_journal_entries_user_day"),
        CheckConstraint(
            "(rating IS NULL OR (rating >= 1 AND rating <= 5)) AND "
            "(mood IS NULL OR (mood >= 1 AND mood <= 5)) AND "
            "(energy IS NULL OR (energy >= 1 AND energy <= 5))",
            name="scales_range",
        ),
    )


# --------------------------------------------------------------------------- #
# Reminders (delivered by the stage-7 notification scheduler)
# --------------------------------------------------------------------------- #
class Reminder(UserScopedMixin, UuidPkMixin, Base):
    __tablename__ = "reminders"

    kind: Mapped[ReminderKind] = mapped_column(
        enum_col(ReminderKind, "reminder_kind"), nullable=False, default=ReminderKind.TASK
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidType, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=sa.text("'{}'::jsonb"))
    remind_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False, index=True)
    lead_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ReminderStatus] = mapped_column(
        enum_col(ReminderStatus, "reminder_status"), nullable=False, default=ReminderStatus.PENDING
    )
    sent_at: Mapped[datetime | None] = mapped_column(TzDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # stable key used to avoid duplicate notifications (unique)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_reminders_user_dedupe"),
        Index("ix_reminders_due", "status", "remind_at"),
    )
