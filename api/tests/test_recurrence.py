"""RRULE expansion (used by task recurrence in stage 6 and reminders in 7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.recurrence import (
    expand_between,
    human_hint,
    next_occurrence,
    next_occurrences,
    parse_rule,
    previous_occurrence,
)

MOSCOW_OFFSET = 3


def moscow(year: int, month: int, day: int, hour: int = 8, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=MOSCOW_OFFSET)))


def test_parse_rule_accepts_rrule_prefix() -> None:
    anchor = moscow(2026, 1, 1)
    assert parse_rule("RRULE:FREQ=DAILY", anchor) is not None
    assert parse_rule("FREQ=DAILY", anchor) is not None
    with pytest.raises(ValueError):
        parse_rule("", anchor)
    with pytest.raises(ValueError):
        parse_rule("FREQ=NONSENSE", anchor)


def test_daily_recurrence_returns_utc_instants() -> None:
    anchor = moscow(2026, 3, 1, 8)
    occurrences = next_occurrences("FREQ=DAILY", dtstart=anchor, count=3)

    assert len(occurrences) == 3
    assert all(o.tzinfo is not None and o.utcoffset() == timedelta(0) for o in occurrences)
    # 08:00 MSK == 05:00 UTC
    assert occurrences[0] == datetime(2026, 3, 1, 5, 0, tzinfo=UTC)
    assert occurrences[2] - occurrences[0] == timedelta(days=2)


def test_weekly_byday() -> None:
    anchor = moscow(2026, 3, 2, 19)  # Monday
    occurrences = next_occurrences("FREQ=WEEKLY;BYDAY=MO,WE,FR", dtstart=anchor, count=4)
    # Mon 2, Wed 4, Fri 6, Mon 9 March
    assert [o.astimezone(timezone(timedelta(hours=3))).day for o in occurrences] == [2, 4, 6, 9]


def test_next_occurrence_is_strictly_after_by_default() -> None:
    anchor = moscow(2026, 3, 2, 19)
    after = moscow(2026, 3, 4, 19)  # exactly on an occurrence (Wednesday)
    following = next_occurrence("FREQ=WEEKLY;BYDAY=MO,WE,FR", dtstart=anchor, after=after)
    assert following.astimezone(timezone(timedelta(hours=3))).day == 6  # Friday


def test_until_bounds_the_series() -> None:
    anchor = moscow(2026, 3, 1, 9)
    occurrences = next_occurrences("FREQ=DAILY", dtstart=anchor, count=10, until=datetime(2026, 3, 3).date())
    assert len(occurrences) == 3  # 1, 2, 3 March


def test_prev_occurrence() -> None:
    anchor = moscow(2026, 3, 1, 9)
    before = moscow(2026, 3, 10, 9)
    previous = previous_occurrence("FREQ=DAILY", dtstart=anchor, before=before)
    assert previous is not None
    assert previous.astimezone(UTC) == datetime(2026, 3, 9, 6, 0, tzinfo=UTC)


def test_expand_between_used_by_calendar_view() -> None:
    anchor = moscow(2026, 3, 1, 9)
    start = moscow(2026, 3, 5, 0)
    end = moscow(2026, 3, 8, 0)
    occurrences = expand_between("FREQ=DAILY", dtstart=anchor, start=start, end=end)
    assert len(occurrences) == 3  # 5, 6, 7 March


def test_biweekly_and_monthly_presets() -> None:
    anchor = moscow(2026, 1, 5, 10)  # Monday
    biweekly = next_occurrences("FREQ=WEEKLY;INTERVAL=2", dtstart=anchor, count=3)
    local = [o.astimezone(timezone(timedelta(hours=3))) for o in biweekly]
    assert [d.day for d in local] == [5, 19, 2] and local[2].month == 2

    monthly = next_occurrences("FREQ=MONTHLY;BYMONTHDAY=1", dtstart=moscow(2026, 1, 1, 10), count=3)
    assert [o.astimezone(timezone(timedelta(hours=3))).month for o in monthly] == [1, 2, 3]


def test_wall_clock_time_survives_dst() -> None:
    """A 08:00 rule stays 08:00 local across a DST switch.

    Europe/Berlin switches to summer time on 2026-03-29 (02:00 -> 03:00). This is
    the classic dateutil trap: expanding the rule with a tz-aware dtstart would
    freeze the +01:00 offset and give 09:00 after the switch. The service expands
    the rule in naive local time and localises each occurrence.
    """
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    anchor = datetime(2026, 3, 27, 8, 0, tzinfo=berlin)
    occurrences = next_occurrences("FREQ=DAILY", dtstart=anchor, count=4, tz_name="Europe/Berlin")

    local = [o.astimezone(berlin) for o in occurrences]
    assert [d.hour for d in local] == [8, 8, 8, 8]
    assert [d.day for d in local] == [27, 28, 29, 30]
    # the UTC offsets differ across the switch, the wall clock does not
    assert {d.utcoffset() for d in local} == {timedelta(hours=1), timedelta(hours=2)}


def test_human_hint_round_trip() -> None:
    assert human_hint("FREQ=DAILY") == "daily"
    assert human_hint("RRULE:FREQ=WEEKLY") == "weekly"
    assert human_hint("FREQ=WEEKLY;BYDAY=TU") == "freq=weekly;byday=tu"
