"""Shared helpers of the planner services."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.tz import ensure_utc, get_zone, utcnow

__all__ = [
    "FUTURE_TOLERANCE",
    "MAX_TAGS",
    "Clock",
    "RefTuple",
    "advisory_lock",
    "check_range",
    "id_conflict",
    "minutes",
    "normalize_tag",
    "normalize_tags",
    "owned",
    "parse_hhmm",
    "validation_error",
]

# a client clock a few minutes ahead of the server is not an error
FUTURE_TOLERANCE = timedelta(minutes=5)
MAX_TAGS = 10
TAG_NAME = re.compile(r"[\w-]{1,64}")

# (id, name, color) of a project or area, as embedded in other rows
RefTuple = tuple[uuid.UUID, str, str | None]


@dataclass(slots=True, frozen=True)
class Clock:
    """The caller's timezone and "now", captured once per request.

    Every local ↔ instant conversion of the planner goes through here, so a
    request never mixes two different "nows" or two different days.
    """

    tz: str | None
    now: datetime  # aware, UTC

    @classmethod
    def for_tz(cls, tz: str | None, now: datetime | None = None) -> Clock:
        return cls(tz=tz, now=ensure_utc(now) if now is not None else utcnow())

    @property
    def zone(self):
        return get_zone(self.tz)

    @property
    def today(self) -> date:
        return self.now.astimezone(self.zone).date()

    def local(self, instant: datetime) -> datetime:
        return ensure_utc(instant).astimezone(self.zone)

    def local_day(self, instant: datetime) -> date:
        return self.local(instant).date()

    def hhmm(self, instant: datetime) -> str:
        return self.local(instant).strftime("%H:%M")

    def naive(self, instant: datetime) -> datetime:
        """Naive local wall clock of an instant (what RRULE expansion works with)."""
        return self.local(instant).replace(tzinfo=None)

    def at(self, day: date, at: time) -> datetime:
        """Local wall clock → UTC instant (DST-safe through zoneinfo; a time in a
        spring-forward gap resolves with the offset before the switch)."""
        return datetime.combine(day, at).replace(tzinfo=self.zone).astimezone(UTC)

    def from_naive(self, naive: datetime) -> datetime:
        return naive.replace(tzinfo=self.zone).astimezone(UTC)

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        return self.at(day, time.min), self.at(day + timedelta(days=1), time.min)

    def range_bounds(self, first: date, last: date) -> tuple[datetime, datetime]:
        return self.at(first, time.min), self.at(last + timedelta(days=1), time.min)

    def instant(self, value: datetime | None) -> datetime:
        """An instant from the client; a value without an offset is the user's wall clock."""
        if value is None:
            return self.now
        if value.tzinfo is None:
            return value.replace(tzinfo=self.zone).astimezone(UTC)
        return value.astimezone(UTC)


def parse_hhmm(value: str) -> time:
    return time(int(value[:2]), int(value[3:5]))


def minutes(seconds: float | int | None) -> int:
    """Seconds → whole minutes, half up (the same rule as Postgres ``round``)."""
    if not seconds:
        return 0
    return int((float(seconds) / 60) + 0.5)


def validation_error(message: str, **details: Any) -> Unprocessable:
    return Unprocessable(message, code="validation_error", details=details or None)


def id_conflict(what: str = "row") -> Conflict:
    return Conflict(f"This id is already used by another {what}", code="id_conflict")


def check_range(first: date, last: date, max_days: int) -> None:
    """Inclusive local day range: ``last`` ≥ ``first`` and at most ``max_days`` days."""
    if last < first:
        raise validation_error("`to` must not be before `from`")
    if (last - first).days + 1 > max_days:
        raise Unprocessable(
            f"The range is limited to {max_days} days",
            code="range_too_large",
            details={"max_days": max_days},
        )


async def owned(db: AsyncSession, model, user_id: uuid.UUID, row_id: uuid.UUID, what: str, *, lock: bool = False):
    """A row of the caller, or 404 (rows of other users do not exist for you)."""
    stmt = select(model).where(model.id == row_id, model.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    row = await db.scalar(stmt)
    if row is None:
        raise NotFound(f"{what} not found", details={"id": str(row_id)})
    return row


def _lock_id(user_id: uuid.UUID, scope: str) -> int:
    digest = hashlib.blake2b(user_id.bytes + scope.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


async def advisory_lock(db: AsyncSession, user_id: uuid.UUID, scope: str) -> None:
    """Per-user transaction lock (released at commit/rollback)."""
    await db.execute(select(func.pg_advisory_xact_lock(_lock_id(user_id, scope))))


def normalize_tag(raw: str) -> str:
    """``#Здоровье`` → ``Здоровье``: 1..64 letters/digits/``_``/``-`` (Unicode letters allowed)."""
    name = unicodedata.normalize("NFC", raw or "").strip()
    if name.startswith("#"):
        name = name[1:].strip()
    if not TAG_NAME.fullmatch(name):
        raise Unprocessable(
            "A tag is 1–64 letters, digits, '_' or '-'",
            code="invalid_tag",
            details={"tag": (raw or "")[:80]},
        )
    return name


def normalize_tags(raws: Iterable[str], *, limit: int = MAX_TAGS) -> list[str]:
    """Normalised, de-duplicated case-insensitively (first spelling wins), at most ``limit``."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in raws:
        name = normalize_tag(raw)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    if len(out) > limit:
        raise Unprocessable(f"At most {limit} tags", code="too_many_tags", details={"max": limit, "given": len(out)})
    return out


def ref_map(rows: Sequence[Any]) -> dict[uuid.UUID, RefTuple]:
    return {row.id: (row.id, row.name, row.color) for row in rows}
