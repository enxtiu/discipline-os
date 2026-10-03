"""Recurrence (RRULE) helpers for tasks and reminders.

Tasks store a plain RFC 5545 RRULE string (``FREQ=WEEKLY;BYDAY=MO,WE,FR``) plus
an optional ``recurrence_until``. This module is the only place that knows how
to expand it, so stage 6 can add "complete an instance -> create the next one"
without touching the schema.

Timezones: RRULEs are expanded in the user's timezone (a "every day at 08:00"
rule must stay 08:00 across DST changes), then converted to UTC for storage.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from dateutil.rrule import rrulestr

from app.core.tz import get_zone

__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "RECURRENCE_PRESETS",
    "expand_between",
    "human_hint",
    "next_occurrence",
    "next_occurrences",
    "parse_rule",
    "previous_occurrence",
]

DEFAULT_MAX_ITERATIONS = 1000

# Friendly presets the UI offers; the API always stores the RRULE itself.
RECURRENCE_PRESETS: dict[str, str] = {
    "daily": "FREQ=DAILY",
    "weekdays": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekly": "FREQ=WEEKLY",
    "biweekly": "FREQ=WEEKLY;INTERVAL=2",
    "monthly": "FREQ=MONTHLY",
    "monthly_first": "FREQ=MONTHLY;BYMONTHDAY=1",
    "yearly": "FREQ=YEARLY",
}


def parse_rule(rule: str, dtstart: datetime):
    """RRULE string -> reusable rrule object (raises ``ValueError`` if broken).

    ``dtstart`` must be **naive local time** — see :func:`_localize`. Expanding a
    tz-aware dtstart with dateutil would freeze the UTC offset, so "08:00 daily"
    would drift to 09:00 after a DST switch.
    """
    text = (rule or "").strip()
    if not text:
        raise ValueError("empty recurrence rule")
    # Accept both "FREQ=DAILY" and "RRULE:FREQ=DAILY" (what iOS/Google export)
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    return rrulestr(text, dtstart=dtstart)


def _naive_local(dt: datetime, tz_name: str | None) -> datetime:
    """Any datetime -> naive wall clock in the user's timezone."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(get_zone(tz_name)).replace(tzinfo=None)


def _localize(naive: datetime, tz_name: str | None) -> datetime:
    """Naive wall clock -> tz-aware local datetime (DST correct)."""
    return naive.replace(tzinfo=get_zone(tz_name))


def _as_local(dt: datetime, tz_name: str | None) -> datetime:
    """tz-aware local datetime for ``dt`` (naive input is treated as local)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=get_zone(tz_name))
    return dt.astimezone(get_zone(tz_name))


def next_occurrences(
    rule: str,
    *,
    dtstart: datetime,
    after: datetime | None = None,
    count: int = 10,
    until: date | None = None,
    tz_name: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> list[datetime]:
    """Expand ``rule`` in the user's timezone; returns **UTC** datetimes.

    ``dtstart`` is the anchor (local wall-clock time of the first occurrence).
    """
    anchor_local = _naive_local(dtstart, tz_name)
    rrule = parse_rule(rule, anchor_local)

    # ``recurrence_until`` bounds the series; without it we stop after ~10y.
    bound = until or (anchor_local.date() + timedelta(days=3650))
    end_of_range = datetime.combine(bound, time.max)

    # `after` semantics: None -> start from the first occurrence;
    # an instant that *is* an occurrence -> return the following one.
    if after is None:
        cursor = anchor_local - timedelta(microseconds=1)
        inclusive = True
    else:
        cursor = _naive_local(after, tz_name)
        if cursor < anchor_local:
            cursor = anchor_local - timedelta(microseconds=1)
            inclusive = True
        else:
            inclusive = False

    out: list[datetime] = []
    for index, occurrence in enumerate(rrule.xafter(cursor, inc=inclusive)):
        if index >= max_iterations or len(out) >= count or occurrence > end_of_range:
            break
        out.append(_localize(occurrence, tz_name).astimezone(UTC))
    return out


def next_occurrence(
    rule: str,
    *,
    dtstart: datetime,
    after: datetime | None = None,
    until: date | None = None,
    tz_name: str | None = None,
) -> datetime | None:
    found = next_occurrences(rule, dtstart=dtstart, after=after, count=1, until=until, tz_name=tz_name)
    return found[0] if found else None


def previous_occurrence(
    rule: str,
    *,
    dtstart: datetime,
    before: datetime,
    tz_name: str | None = None,
) -> datetime | None:
    anchor_local = _naive_local(dtstart, tz_name)
    rrule = parse_rule(rule, anchor_local)
    found = rrule.before(_naive_local(before, tz_name), inc=False)
    return _localize(found, tz_name).astimezone(UTC) if found else None


def expand_between(
    rule: str,
    *,
    dtstart: datetime,
    start: datetime,
    end: datetime,
    tz_name: str | None = None,
    limit: int = 500,
) -> list[datetime]:
    """All occurrences in ``[start, end)`` — used by the calendar/month view."""
    anchor_local = _naive_local(dtstart, tz_name)
    rrule = parse_rule(rule, anchor_local)
    results: Iterable[datetime] = rrule.between(_naive_local(start, tz_name), _naive_local(end, tz_name), inc=True)
    out: list[datetime] = []
    for occurrence in results:
        out.append(_localize(occurrence, tz_name).astimezone(UTC))
        if len(out) >= limit:
            break
    return out


def human_hint(rule: str) -> str:
    """Very small helper for logs/CLI (the UI localises properly)."""
    text = (rule or "").strip().upper().replace("RRULE:", "")
    for key, value in RECURRENCE_PRESETS.items():
        if text == value:
            return key
    return text.lower()
