"""Planner (stage 6): PARA containers, tasks, views, quick add, time tracking, journal.

Time rule: requests carry *local* fields only (``due_date`` + ``due_time``, a
``block`` of ``{date, start, end}``, ``{date, start, end}`` for manual time
entries, ``{date, time}`` for absolute reminders). The server converts them
with the user's timezone; responses carry both the instants and the local
fields, so the client never converts between wall clock and instants.

Class names follow the stage-6 contract (the frontend aliases the generated
OpenAPI types by name). The journal's cross-domain digest is
``JournalDaySummaryOut`` because ``DaySummaryOut`` is the nutrition day.
"""

from __future__ import annotations

import uuid
from datetime import date as Date
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------- #
# Shared literals and annotated types
# --------------------------------------------------------------------------- #
TaskStatusLiteral = Literal["inbox", "open", "scheduled", "in_progress", "waiting", "blocked", "done", "cancelled"]
# `scheduled` is derived from a time block and never accepted from the client
TaskStatusInLiteral = Literal["inbox", "open", "in_progress", "waiting", "blocked", "done", "cancelled"]
ProjectStatusLiteral = Literal["active", "on_hold", "completed", "cancelled", "archived"]
ProjectFilterLiteral = Literal["active", "on_hold", "completed", "cancelled", "archived", "open", "all"]
ReminderStatusLiteral = Literal["pending", "sent", "failed", "cancelled", "suppressed"]
TaskSourceLiteral = Literal["manual", "quick_add", "recurrence", "import"]
TimeEntrySourceLiteral = Literal["timer", "manual", "shortcut", "import"]
RecurrencePresetLiteral = Literal["daily", "weekdays", "weekly", "biweekly", "monthly", "yearly", "custom"]
TokenKindLiteral = Literal[
    "date", "time", "block", "duration", "priority", "tag", "project", "context", "recurrence", "reminder"
]
ContainerKindLiteral = Literal["project", "area"]
SummaryPeriodLiteral = Literal["week", "month"]

HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
EARLIEST_DATE = Date(2000, 1, 1)
LATEST_DATE = Date(2100, 12, 31)
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1
SMALLINT_MIN, SMALLINT_MAX = -(2**15), 2**15 - 1


def _sane_date(value: Date) -> Date:
    if not EARLIEST_DATE <= value <= LATEST_DATE:
        raise ValueError(f"date must be between {EARLIEST_DATE} and {LATEST_DATE}")
    return value


def _sane_instant(value: datetime) -> datetime:
    # year 9999 + a few minutes of tolerance would overflow into a 500
    if not EARLIEST_DATE.year <= value.year <= LATEST_DATE.year:
        raise ValueError(f"timestamp must be between {EARLIEST_DATE.year} and {LATEST_DATE.year}")
    return value


def _http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in value):
        raise ValueError("url must be an http(s) address")
    return value


PlannerDate = Annotated[Date, AfterValidator(_sane_date), Field(description="Local calendar day, YYYY-MM-DD")]
PlannerInstant = Annotated[
    datetime,
    AfterValidator(_sane_instant),
    Field(description="ISO-8601 instant; a value without an offset is your local wall clock"),
]
HHMM = Annotated[str, Field(pattern=HHMM_PATTERN, description="Local wall clock, HH:MM (24 h)", examples=["18:00"])]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$", description="#RRGGBB", examples=["#22D3EE"])]
RawTag = Annotated[str, Field(max_length=200, description="tag name; a leading # is removed")]
Url = Annotated[str, Field(max_length=500), AfterValidator(_http_url)]
TaskSortOrder = Annotated[int, Field(ge=INT32_MIN, le=INT32_MAX)]
SmallSortOrder = Annotated[int, Field(ge=SMALLINT_MIN, le=SMALLINT_MAX)]
Scale5 = Annotated[int, Field(ge=1, le=5)]


def _reject_nul(value: Any) -> None:
    """PostgreSQL text cannot hold NUL characters; refuse them before they reach the driver."""
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("text must not contain NUL characters")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_nul(key)
            _reject_nul(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_nul(item)


class PlannerIn(BaseModel):
    """Request models of this module: unknown fields, NaN/∞ and NUL characters are refused."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _no_nul(cls, data: Any) -> Any:
        _reject_nul(data)
        return data


class PlannerOut(BaseModel):
    """Response models of this module (built explicitly by the presenters)."""

    model_config = ConfigDict(populate_by_name=True)


# --------------------------------------------------------------------------- #
# Small shared output shapes
# --------------------------------------------------------------------------- #
class RefOut(PlannerOut):
    """A project or area as embedded in other rows."""

    id: uuid.UUID
    name: str
    color: str | None = None


class TaskRefOut(PlannerOut):
    id: uuid.UUID
    title: str


class TagRefOut(PlannerOut):
    id: uuid.UUID
    name: str
    color: str | None = None


class SubtaskCountsOut(PlannerOut):
    done: int = 0
    total: int = Field(default=0, description="direct subtasks, cancelled ones not counted")


class BlockOut(PlannerOut):
    date: Date = Field(description="local day the block starts on")
    start: str = Field(description="HH:MM local")
    end: str = Field(description="HH:MM local (may be on the next day)")
    minutes: int
    start_at: datetime
    end_at: datetime
    ends_next_day: bool = False


class DayMinutesOut(PlannerOut):
    date: Date
    minutes: int = 0


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
class ReminderLeadIn(PlannerIn):
    """Relative reminder: ``lead_minutes`` before the task's due time / block start / due day 09:00."""

    lead_minutes: int = Field(ge=0, le=10080)


class ReminderAtIn(PlannerIn):
    """Absolute reminder at a local date and time."""

    date: PlannerDate
    time: HHMM


class ReminderIn(RootModel[ReminderLeadIn | ReminderAtIn]):
    """``{"lead_minutes": n}`` or ``{"date": "YYYY-MM-DD", "time": "HH:MM"}``."""


class ReminderOut(PlannerOut):
    id: uuid.UUID
    lead_minutes: int | None = None
    date: Date | None = None
    time: str | None = Field(default=None, description="HH:MM local")
    remind_at: datetime
    status: ReminderStatusLiteral
    sent_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Time entries (needed by TaskDetailOut)
# --------------------------------------------------------------------------- #
class TimeEntryOut(PlannerOut):
    id: uuid.UUID
    task: TaskRefOut | None = None
    project: RefOut | None = None
    area: RefOut | None = None
    description: str | None = None
    note: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_s: int | None = Field(default=None, description="null while running")
    date: Date = Field(description="local day of started_at (entries belong to their start day)")
    start: str = Field(description="HH:MM local")
    end: str | None = Field(default=None, description="HH:MM local, null while running")
    ends_next_day: bool = False
    source: TimeEntrySourceLiteral
    running: bool = False


class RunningTimerOut(PlannerOut):
    entry: TimeEntryOut
    elapsed_s: int
    server_now: datetime


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
class TaskOut(PlannerOut):
    id: uuid.UUID
    title: str
    notes_md: str | None = None
    status: TaskStatusLiteral
    priority: int = Field(description="1 = P1 highest … 4 = P4 (no priority)")
    project: RefOut | None = None
    area: RefOut | None = None
    parent_id: uuid.UUID | None = None
    parent_title: str | None = None
    depth: int = Field(default=0, description="0 root, 1 subtask, 2 sub-subtask")
    due_date: Date | None = None
    due_time: str | None = Field(default=None, description="HH:MM local; null = all-day")
    due_at: datetime | None = None
    block: BlockOut | None = None
    estimate_minutes: int | None = None
    actual_minutes: int = 0
    completed_at: datetime | None = None
    completed_day: Date | None = None
    recurrence_rule: str | None = None
    recurrence_until: Date | None = None
    recurrence_preset: RecurrencePresetLiteral | None = None
    is_recurring: bool = False
    series_id: uuid.UUID | None = None
    series_index: int = 0
    tags: list[TagRefOut] = Field(default_factory=list)
    context: str | None = None
    energy: int | None = None
    is_pinned: bool = False
    sort_order: int = 0
    overdue: bool = False
    subtask_counts: SubtaskCountsOut = Field(default_factory=SubtaskCountsOut)
    reminders: list[ReminderOut] = Field(default_factory=list)
    source: TaskSourceLiteral = "manual"
    created_at: datetime
    updated_at: datetime


class TaskTimeOut(PlannerOut):
    total_minutes: int = 0
    entries: list[TimeEntryOut] = Field(default_factory=list, description="latest 10")


class TaskDetailOut(TaskOut):
    subtasks: list[TaskDetailOut] = Field(default_factory=list)
    time: TaskTimeOut = Field(default_factory=TaskTimeOut)


class BlockIn(PlannerIn):
    date: PlannerDate
    start: HHMM
    end: HHMM = Field(description="end ≤ start means the block ends the next day (≤ 24 h)")


def _task_fields_check(model: Any) -> None:
    """``due_time`` needs a date: on create always, on a patch when the date is cleared in the same request
    (a patch that only sends a time is checked against the stored date by the service)."""
    time_without_date = getattr(model, "due_time", None) is not None and getattr(model, "due_date", None) is None
    if time_without_date and ("due_date" in model.model_fields_set or isinstance(model, TaskIn)):
        raise ValueError("due_time requires due_date")


class TaskIn(PlannerIn):
    id: uuid.UUID | None = Field(default=None, description="client UUID; a replay returns the same task")
    title: str = Field(min_length=1, max_length=300)
    notes_md: str | None = Field(default=None, max_length=20000)
    status: TaskStatusInLiteral | None = None
    priority: int | None = Field(default=None, ge=1, le=4)
    project_id: uuid.UUID | None = None
    area_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    due_date: PlannerDate | None = None
    due_time: HHMM | None = None
    block: BlockIn | None = None
    estimate_minutes: int | None = Field(default=None, ge=1, le=1440)
    recurrence_rule: str | None = Field(default=None, max_length=255, description="RRULE text or a preset key")
    recurrence_until: PlannerDate | None = None
    tags: list[RawTag] = Field(default_factory=list, max_length=100, description="≤ 10, replaces the set")
    context: str | None = Field(default=None, max_length=65, description="a leading @ is removed")
    energy: int | None = Field(default=None, ge=1, le=3)
    is_pinned: bool = False
    sort_order: TaskSortOrder = 0
    reminders: list[ReminderIn] = Field(default_factory=list, max_length=100, description="≤ 5")

    @model_validator(mode="after")
    def _due_time_needs_date(self) -> TaskIn:
        _task_fields_check(self)
        return self


class TaskPatchIn(PlannerIn):
    """Every field optional; ``null`` clears a nullable field."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    notes_md: str | None = Field(default=None, max_length=20000)
    status: TaskStatusInLiteral | None = None
    priority: int | None = Field(default=None, ge=1, le=4)
    project_id: uuid.UUID | None = None
    area_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    due_date: PlannerDate | None = None
    due_time: HHMM | None = None
    block: BlockIn | None = None
    estimate_minutes: int | None = Field(default=None, ge=1, le=1440)
    recurrence_rule: str | None = Field(default=None, max_length=255)
    recurrence_until: PlannerDate | None = None
    tags: list[RawTag] | None = Field(default=None, max_length=100)
    context: str | None = Field(default=None, max_length=65)
    energy: int | None = Field(default=None, ge=1, le=3)
    is_pinned: bool | None = None
    sort_order: TaskSortOrder | None = None
    reminders: list[ReminderIn] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _checks(self) -> TaskPatchIn:
        for name in ("title", "status", "priority", "is_pinned", "sort_order"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        _task_fields_check(self)
        return self


class TaskCompleteIn(PlannerIn):
    completed_at: PlannerInstant | None = None


class TaskPatchResultOut(PlannerOut):
    task: TaskDetailOut
    next_instance: TaskOut | None = None
    removed_next_instance_id: uuid.UUID | None = None


class TaskCompleteOut(PlannerOut):
    task: TaskOut
    next_instance: TaskOut | None = None


class TaskReopenOut(PlannerOut):
    task: TaskOut
    removed_next_instance_id: uuid.UUID | None = None


# --------------------------------------------------------------------------- #
# Quick add & parse
# --------------------------------------------------------------------------- #
QuickText = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=500)]
ParseText = Annotated[str, StringConstraints(strip_whitespace=False, max_length=500)]


class QuickAddDefaultsIn(PlannerIn):
    project_id: uuid.UUID | None = None
    area_id: uuid.UUID | None = None
    due_date: PlannerDate | None = None
    parent_id: uuid.UUID | None = None


class QuickAddIn(PlannerIn):
    id: uuid.UUID | None = None
    text: QuickText = Field(description="the line as typed (offsets in `parsed.tokens` refer to it)")
    captured_at: PlannerInstant | None = Field(default=None, description="when it was typed; default now")
    defaults: QuickAddDefaultsIn | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ParseIn(PlannerIn):
    text: ParseText = ""
    captured_at: PlannerInstant | None = None


class ParseBlockOut(PlannerOut):
    date: Date
    start: str
    end: str


class ParseProjectOut(PlannerOut):
    id: uuid.UUID
    name: str
    kind: ContainerKindLiteral


class ParseReminderOut(PlannerOut):
    lead_minutes: int


class ParseTokenOut(PlannerOut):
    start: int = Field(description="UTF-16 code-unit offset into the original text")
    end: int = Field(description="exclusive")
    kind: TokenKindLiteral
    text: str


class ParseOut(PlannerOut):
    title: str
    due_date: Date | None = None
    due_time: str | None = None
    block: ParseBlockOut | None = None
    estimate_minutes: int | None = None
    priority: int | None = None
    tags: list[str] = Field(default_factory=list)
    project: ParseProjectOut | None = None
    context: str | None = None
    recurrence_rule: str | None = None
    recurrence_preset: RecurrencePresetLiteral | None = None
    reminders: list[ParseReminderOut] = Field(default_factory=list)
    tokens: list[ParseTokenOut] = Field(default_factory=list)


class QuickAddOut(PlannerOut):
    task: TaskDetailOut
    parsed: ParseOut


# --------------------------------------------------------------------------- #
# Areas, projects, resources, tags
# --------------------------------------------------------------------------- #
class AreaCountsOut(PlannerOut):
    open_tasks: int = Field(default=0, description="open root tasks directly in the area")
    active_projects: int = 0


class AreaOut(PlannerOut):
    id: uuid.UUID
    name: str
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    sort_order: int = 0
    is_archived: bool = False
    weekly_target_minutes: int | None = None
    counts: AreaCountsOut = Field(default_factory=AreaCountsOut)
    week_minutes: int = Field(default=0, description="tracked this week (your week start)")
    created_at: datetime
    updated_at: datetime


class AreaIn(PlannerIn):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=20000)
    color: Color | None = None
    icon: str | None = Field(default=None, max_length=48)
    weekly_target_minutes: int | None = Field(default=None, ge=0, le=10080)


class AreaPatchIn(PlannerIn):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=20000)
    color: Color | None = None
    icon: str | None = Field(default=None, max_length=48)
    weekly_target_minutes: int | None = Field(default=None, ge=0, le=10080)
    is_archived: bool | None = None
    sort_order: SmallSortOrder | None = None

    @model_validator(mode="after")
    def _not_null(self) -> AreaPatchIn:
        for name in ("name", "is_archived", "sort_order"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ProjectCountsOut(PlannerOut):
    open: int = 0
    done: int = 0
    total: int = Field(default=0, description="open + done root tasks (cancelled ones not counted)")


class ProjectOut(PlannerOut):
    id: uuid.UUID
    name: str
    outcome: str | None = None
    description_md: str | None = None
    area: RefOut | None = None
    status: ProjectStatusLiteral
    deadline: Date | None = None
    start_date: Date | None = None
    completed_at: datetime | None = None
    color: str | None = None
    icon: str | None = None
    is_archived: bool = False
    sort_order: int = 0
    counts: ProjectCountsOut = Field(default_factory=ProjectCountsOut)
    progress: float = Field(default=0.0, description="done / total of root tasks, 0 when empty")
    tracked_minutes: int = 0
    overdue: bool = Field(default=False, description="deadline before today while active or on hold")
    created_at: datetime
    updated_at: datetime


class ProjectIn(PlannerIn):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    outcome: str | None = Field(default=None, max_length=20000)
    description_md: str | None = Field(default=None, max_length=20000)
    area_id: uuid.UUID | None = None
    status: ProjectStatusLiteral | None = None
    deadline: PlannerDate | None = None
    start_date: PlannerDate | None = None
    color: Color | None = None
    icon: str | None = Field(default=None, max_length=48)


class ProjectPatchIn(PlannerIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    outcome: str | None = Field(default=None, max_length=20000)
    description_md: str | None = Field(default=None, max_length=20000)
    area_id: uuid.UUID | None = None
    status: ProjectStatusLiteral | None = None
    deadline: PlannerDate | None = None
    start_date: PlannerDate | None = None
    color: Color | None = None
    icon: str | None = Field(default=None, max_length=48)
    is_archived: bool | None = None
    sort_order: SmallSortOrder | None = None

    @model_validator(mode="after")
    def _not_null(self) -> ProjectPatchIn:
        for name in ("name", "status", "is_archived", "sort_order"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ResourceOut(PlannerOut):
    id: uuid.UUID
    name: str
    notes_md: str | None = None
    url: str | None = None
    area: RefOut | None = None
    tags: list[str] = Field(default_factory=list)
    is_archived: bool = False
    created_at: datetime
    updated_at: datetime


class ResourceIn(PlannerIn):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    notes_md: str | None = Field(default=None, max_length=20000)
    url: Url | None = None
    area_id: uuid.UUID | None = None
    tags: list[RawTag] = Field(default_factory=list, max_length=100, description="≤ 10")


class ResourcePatchIn(PlannerIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes_md: str | None = Field(default=None, max_length=20000)
    url: Url | None = None
    area_id: uuid.UUID | None = None
    tags: list[RawTag] | None = Field(default=None, max_length=100)
    is_archived: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> ResourcePatchIn:
        for name in ("name", "is_archived"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class TagOut(PlannerOut):
    id: uuid.UUID
    name: str
    color: str | None = None
    open_tasks: int = 0


class TagIn(PlannerIn):
    id: uuid.UUID | None = None
    name: RawTag
    color: Color | None = None


class TagPatchIn(PlannerIn):
    name: RawTag | None = None
    color: Color | None = None

    @model_validator(mode="after")
    def _not_null(self) -> TagPatchIn:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class ReorderIn(PlannerIn):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500, description="your ids in the wanted order")


class AreaListOut(PlannerOut):
    items: list[AreaOut]


class AreaDetailOut(PlannerOut):
    area: AreaOut
    projects: list[ProjectOut]
    tasks: list[TaskOut] = Field(description="open root tasks directly in the area")
    resources: list[ResourceOut]


class ProjectListOut(PlannerOut):
    items: list[ProjectOut]


class ProjectDetailOut(PlannerOut):
    project: ProjectOut
    tasks: list[TaskOut] = Field(description="open root tasks")
    done: list[TaskOut] = Field(description="latest 50 done root tasks")


class ResourceListOut(PlannerOut):
    items: list[ResourceOut]


class TagListOut(PlannerOut):
    items: list[TagOut]


# --------------------------------------------------------------------------- #
# Views & search
# --------------------------------------------------------------------------- #
class CountsOut(PlannerOut):
    date: Date = Field(description="your today")
    inbox: int = 0
    today: int = 0
    overdue: int = 0
    timer_running: bool = False


class OccurrenceBlockOut(PlannerOut):
    start: str
    end: str
    minutes: int


class OccurrenceOut(PlannerOut):
    """A virtual future occurrence of a recurring task — read-only, not a row."""

    task_id: uuid.UUID
    title: str
    date: Date
    due_time: str | None = None
    block: OccurrenceBlockOut | None = None
    priority: int = 4
    project: RefOut | None = None
    area: RefOut | None = None
    virtual: bool = True


class TodayViewOut(PlannerOut):
    date: Date
    overdue: list[TaskOut]
    today: list[TaskOut]
    scheduled: list[TaskOut]
    done: list[TaskOut]
    timer: RunningTimerOut | None = None
    counts: CountsOut


class InboxViewOut(PlannerOut):
    items: list[TaskOut]


class UpcomingDayOut(PlannerOut):
    date: Date
    tasks: list[TaskOut]
    occurrences: list[OccurrenceOut]


class UpcomingViewOut(PlannerOut):
    from_: Date = Field(alias="from")
    to: Date
    days: list[UpcomingDayOut]


class CalendarDayOut(PlannerOut):
    date: Date
    all_day: list[TaskOut]
    blocks: list[TaskOut]
    occurrences: list[OccurrenceOut]
    done_count: int = 0


class CalendarViewOut(PlannerOut):
    from_: Date = Field(alias="from")
    to: Date
    days: list[CalendarDayOut]


class TagViewOut(PlannerOut):
    tag: TagOut
    items: list[TaskOut]


class ArchiveViewOut(PlannerOut):
    projects: list[ProjectOut]
    areas: list[AreaOut]
    resources: list[ResourceOut]
    tasks: list[TaskOut] = Field(description="done and cancelled, latest 100")


class JournalSearchHitOut(PlannerOut):
    date: Date
    snippet: str


class SearchOut(PlannerOut):
    query: str
    tasks: list[TaskOut]
    projects: list[ProjectOut]
    areas: list[AreaOut]
    resources: list[ResourceOut]
    journal: list[JournalSearchHitOut]


# --------------------------------------------------------------------------- #
# Time tracking
# --------------------------------------------------------------------------- #
class TimerStateOut(PlannerOut):
    running: RunningTimerOut | None = None
    server_now: datetime


class TimerStartIn(PlannerIn):
    id: uuid.UUID = Field(description="client UUID of the new entry; a replay returns the current state")
    task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    area_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=300)
    started_at: PlannerInstant | None = Field(default=None, description="≤ 24 h back, ≤ 5 min ahead; default now")


class TimerStartOut(PlannerOut):
    entry: TimeEntryOut = Field(description="the row with the requested id")
    running: RunningTimerOut | None = None
    stopped: TimeEntryOut | None = Field(default=None, description="the timer this start switched off")


class TimerStopIn(PlannerIn):
    entry_id: uuid.UUID | None = None
    ended_at: PlannerInstant | None = None


class TimerStopOut(PlannerOut):
    stopped: TimeEntryOut | None = None
    server_now: datetime


class TimeEntryIn(PlannerIn):
    id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    area_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=20000)
    date: PlannerDate
    start: HHMM
    end: HHMM = Field(description="end ≤ start means the next day")


class TimeEntryPatchIn(PlannerIn):
    task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    area_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=20000)
    date: PlannerDate | None = None
    start: HHMM | None = None
    end: HHMM | None = None

    @model_validator(mode="after")
    def _times_together(self) -> TimeEntryPatchIn:
        sent = {name for name in ("date", "start", "end") if name in self.model_fields_set}
        if sent and (len(sent) != 3 or self.date is None or self.start is None or self.end is None):
            raise ValueError("date, start and end are sent together")
        return self


class TimeEntryListOut(PlannerOut):
    from_: Date = Field(alias="from")
    to: Date
    items: list[TimeEntryOut]
    total_minutes: int = 0
    by_day: list[DayMinutesOut]


class AreaMinutesOut(PlannerOut):
    area: RefOut | None = None
    minutes: int = 0
    target_minutes: int | None = None


class ProjectMinutesOut(PlannerOut):
    project: RefOut | None = None
    minutes: int = 0


class TimeSummaryOut(PlannerOut):
    period: SummaryPeriodLiteral
    from_: Date = Field(alias="from")
    to: Date
    total_minutes: int = 0
    by_area: list[AreaMinutesOut]
    by_project: list[ProjectMinutesOut]
    by_day: list[DayMinutesOut]


# --------------------------------------------------------------------------- #
# Journal
# --------------------------------------------------------------------------- #
class JournalOut(PlannerOut):
    id: uuid.UUID
    date: Date
    note_md: str | None = None
    rating: int | None = None
    mood: int | None = None
    energy: int | None = None
    wins_md: str | None = None
    improvements_md: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JournalIn(PlannerIn):
    """Upsert by (you, date): fields not sent keep their value; ``null`` clears one."""

    id: uuid.UUID | None = Field(default=None, description="used when the entry is first created")
    note_md: str | None = Field(default=None, max_length=20000)
    rating: Scale5 | None = None
    mood: Scale5 | None = None
    energy: Scale5 | None = None
    wins_md: str | None = Field(default=None, max_length=20000)
    improvements_md: str | None = Field(default=None, max_length=20000)
    tags: list[RawTag] | None = Field(default=None, max_length=20)


class JournalAreaMinutesOut(PlannerOut):
    area: RefOut | None = None
    minutes: int = 0


class JournalDaySummaryOut(PlannerOut):
    """Read-only cross-domain digest for the evening review (burned kcal never count as budget)."""

    date: Date
    tasks_done: int = 0
    tasks_done_titles: list[str] = Field(default_factory=list, description="≤ 5")
    tasks_open_due: int = Field(default=0, description="still open, due that day")
    time_minutes: int = 0
    time_by_area: list[JournalAreaMinutesOut] = Field(default_factory=list)
    kcal: int = Field(default=0, description="food logged that day")
    target_kcal: int | None = None
    workouts: int = 0
    workout_kcal: int | None = Field(default=None, description="burned in workouts — shown separately, never budget")
    steps: int | None = None
    sleep_minutes: int | None = None


class JournalDayOut(PlannerOut):
    entry: JournalOut | None = None
    day: JournalDaySummaryOut


class JournalListItemOut(PlannerOut):
    date: Date
    rating: int | None = None
    mood: int | None = None
    energy: int | None = None
    has_note: bool = False


class JournalListOut(PlannerOut):
    items: list[JournalListItemOut]


TaskDetailOut.model_rebuild()
