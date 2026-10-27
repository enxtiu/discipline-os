"""Quick-add natural-language parser for tasks (stage 6), Russian + English.

Pure function, no database and no I/O: the planner service passes the user's
local "now", week start and the names of their active projects/areas, then
turns the result into a task. The client never parses dates itself — it shows
the tokens this module returns (``POST /api/v1/planner/parse``).

    parse_task_text("завтра 18:00 зал #здоровье p2", now=..., week_start=1)
    -> ParsedTask(title="зал", due_date=<tomorrow>, due_time=18:00, tags=["здоровье"], priority=2, ...)

Token offsets are UTF-16 code units (what JavaScript's ``String.slice`` uses),
so the UI can highlight exactly what was recognised.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal

__all__ = ["KnownContainer", "ParsedTask", "Token", "TokenKind", "parse_task_text"]

TokenKind = Literal[
    "date",
    "time",
    "block",
    "duration",
    "priority",
    "tag",
    "project",
    "context",
    "recurrence",
    "reminder",
]


@dataclass(frozen=True, slots=True)
class Token:
    start: int  # UTF-16 code-unit offset into the original text
    end: int  # exclusive
    kind: TokenKind
    text: str  # the original substring


@dataclass(frozen=True, slots=True)
class KnownContainer:
    """An active project or area the ``+name`` syntax may refer to."""

    id: str
    name: str
    kind: Literal["project", "area"]


@dataclass(slots=True)
class ParsedTask:
    title: str
    due_date: date | None = None
    due_time: time | None = None  # local wall clock; None = all-day
    block_date: date | None = None  # local day the time block starts on
    block_start: time | None = None
    block_end: time | None = None  # may be <= block_start: the block ends the next day
    estimate_minutes: int | None = None
    priority: int | None = None  # 1..4, None = not specified
    tags: list[str] = field(default_factory=list)
    container: KnownContainer | None = None
    context: str | None = None  # without the "@"
    recurrence_rule: str | None = None  # RRULE without DTSTART, e.g. "FREQ=WEEKLY;BYDAY=MO"
    recurrence_preset: str | None = None  # daily|weekdays|weekly|biweekly|monthly|yearly|custom
    reminder_leads: list[int] = field(default_factory=list)  # minutes before the anchor
    tokens: list[Token] = field(default_factory=list)


def parse_task_text(
    text: str,
    *,
    now: datetime,
    week_start: int = 1,
    containers: Sequence[KnownContainer] = (),
) -> ParsedTask:
    """Parse a quick-add line.

    ``now`` is the capture instant already converted to the user's timezone
    (tz-aware). ``week_start`` follows ``user_settings.week_start``
    (0 = Sunday, 1 = Monday, … 6 = Saturday).
    """
    # Skeleton: the full ru/en grammar lands in the parser commit of stage 6.
    cleaned = " ".join((text or "").split())
    return ParsedTask(title=cleaned)
