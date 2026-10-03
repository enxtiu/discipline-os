"""Timezone helpers.

Rule of the house: **the database only ever stores UTC**, every "day" is
computed in the *user's* timezone (``settings.timezone``, auto-detected on the
device, overridable).

All helpers are DST-safe: a local midnight is built through ``zoneinfo``, which
resolves the correct UTC offset for that specific date.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "day_bounds_utc",
    "day_range_utc",
    "ensure_utc",
    "get_zone",
    "iter_days",
    "local_day",
    "local_day_start",
    "parse_iso_date",
    "to_timezone",
    "today_in",
    "utcnow",
]

UTC = UTC
_FALLBACK = UTC


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(tz=UTC)


def ensure_utc(value: datetime) -> datetime:
    """Naive datetimes are interpreted as UTC (never as local time)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_zone(tz_name: str | None) -> ZoneInfo | timezone:
    if not tz_name:
        return _FALLBACK
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return _FALLBACK


def to_timezone(value: datetime, tz_name: str | None) -> datetime:
    return ensure_utc(value).astimezone(get_zone(tz_name))


def local_day(value: datetime, tz_name: str | None) -> date:
    """Which calendar day does this instant belong to, for the user?"""
    return to_timezone(value, tz_name).date()


def today_in(tz_name: str | None) -> date:
    return to_timezone(utcnow(), tz_name).date()


def local_day_start(day: date, tz_name: str | None) -> datetime:
    """Local wall-clock 00:00 of ``day`` (DST aware)."""
    zone = get_zone(tz_name)
    naive = datetime.combine(day, time.min)
    return naive.replace(tzinfo=zone)


def day_bounds_utc(day: date, tz_name: str | None) -> tuple[datetime, datetime]:
    """``[start, end)`` UTC instants covering the local day ``day``."""
    zone = get_zone(tz_name)
    start_local = datetime.combine(day, time.min).replace(tzinfo=zone)
    end_local = datetime.combine(day + timedelta(days=1), time.min).replace(tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def day_range_utc(start_day: date, end_day: date, tz_name: str | None) -> tuple[datetime, datetime]:
    """``[start, end)`` UTC instants covering the inclusive local day range."""
    start, _ = day_bounds_utc(start_day, tz_name)
    _, end = day_bounds_utc(end_day, tz_name)
    return start, end


def iter_days(start_day: date, end_day: date):
    cursor = start_day
    while cursor <= end_day:
        yield cursor
        cursor += timedelta(days=1)


def parse_iso_date(value: str) -> date:
    """``YYYY-MM-DD`` (accepts full ISO timestamps by truncating)."""
    text = value.strip()
    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    return date.fromisoformat(text)
