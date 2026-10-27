"""Task recurrence: RRULE validation, canonical form, presets and bounded expansion.

Rules are expanded on the **naive local wall clock** (``recurrence.parse_rule``),
so "every day" stays the same local day and time across DST switches.

Why the grammar is a safe subset of RFC 5545: dateutil iterates period by period
until it finds a match, so a rule that can never match (``BYMONTH=13``,
``FREQ=DAILY;INTERVAL=7;BYDAY=MO`` from a Tuesday, ``INTERVAL=0``…) loops for
seconds or forever. Tasks only need day-level rules, so the accepted parts are
limited to combinations that always produce occurrences — or, for yearly rules,
are cheap to exhaust:

* ``FREQ`` = DAILY | WEEKLY | MONTHLY | YEARLY, ``INTERVAL`` 1..999,
  ``COUNT`` 1..1000, ``UNTIL`` (moved into ``recurrence_until``), ``WKST``;
* DAILY: ``BYDAY``/``BYMONTH``/``BYMONTHDAY`` filters only with INTERVAL 1;
* WEEKLY: plain ``BYDAY`` only;
* MONTHLY: ``BYDAY`` (ordinals ±1..5), ``BYMONTHDAY`` (±29..31 only with
  INTERVAL 1), ``BYMONTH`` only with INTERVAL 1, ``BYSETPOS`` with plain
  weekdays (|pos| ≤ 4 × weekdays, so every month has a match);
* YEARLY: ``BYMONTH``, ``BYMONTHDAY``, ``BYDAY`` (ordinals ±1..53, ±1..5 with
  BYMONTH);
* ordinal weekdays never combine with ``BYMONTHDAY``; a BYMONTH × BYMONTHDAY
  pair must exist on some calendar (Feb 29 counts); no time-of-day parts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.core.errors import Unprocessable
from app.services.recurrence import RECURRENCE_PRESETS, parse_rule

__all__ = [
    "CONTRACT_PRESETS",
    "Rule",
    "first_on_or_after",
    "next_after",
    "occurrences_between",
    "parse_stored",
    "preset_of",
    "validate_rule",
]

CONTRACT_PRESETS = ("daily", "weekdays", "weekly", "biweekly", "monthly", "yearly")
_ORDER = ("FREQ", "INTERVAL", "BYMONTH", "BYMONTHDAY", "BYDAY", "BYSETPOS", "COUNT", "WKST")
_ALLOWED = {*_ORDER, "UNTIL"}
_FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
# ASCII digits only: ``\d`` and ``str.isdigit`` also accept "٣", "²" or "①" (and ``int`` chokes on the last two)
_BYDAY_ITEM = re.compile(r"([+-]?[0-9]{1,2})?(MO|TU|WE|TH|FR|SA|SU)")
_UNTIL = re.compile(r"([0-9]{8})(T[0-9]{6}Z?)?")
_INT = re.compile(r"[+-]?[0-9]{1,4}")
_COUNT = re.compile(r"[0-9]{1,4}")
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _invalid(message: str, rule: str | None = None) -> Unprocessable:
    details = {"rule": rule[:255]} if rule else None
    return Unprocessable(message, code="invalid_rrule", details=details)


@dataclass(slots=True, frozen=True)
class Rule:
    """A validated rule: ``text`` is the canonical RRULE stored on the task."""

    text: str
    parts: tuple[tuple[str, str], ...]
    until: date | None = None  # an UNTIL found in the input (moved to recurrence_until)

    @property
    def count(self) -> int | None:
        value = dict(self.parts).get("COUNT")
        return int(value) if value is not None else None

    @property
    def expansion_text(self) -> str:
        """The rule without COUNT: the series position is tracked by ``series_index``."""
        return ";".join(f"{key}={value}" for key, value in self.parts if key != "COUNT")

    @property
    def preset(self) -> str:
        return preset_of(self.text) or "custom"


def _ints(name: str, value: str, low: int, high: int, *, nonzero: bool = True) -> list[int]:
    items = value.split(",")
    if not items or len(items) > 31:
        raise _invalid(f"{name}: invalid value")
    out: list[int] = []
    for item in items:
        if not _INT.fullmatch(item):
            raise _invalid(f"{name}: invalid value {item!r}")
        number = int(item)
        if (nonzero and number == 0) or not low <= abs(number) <= high or (not nonzero and not low <= number <= high):
            raise _invalid(f"{name}: {number} is out of range")
        if number not in out:
            out.append(number)
    return sorted(out)


def _canonical(raw: str) -> tuple[tuple[tuple[str, str], ...], date | None]:
    text = (raw or "").strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    text = text.upper()
    if not text or any(ch.isspace() for ch in text) or ":" in text:
        raise _invalid("A recurrence is one RRULE line, e.g. FREQ=WEEKLY;BYDAY=MO", raw)
    found: dict[str, str] = {}
    for part in text.split(";"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if not sep or not value:
            raise _invalid(f"Malformed RRULE part {part!r}", raw)
        if key == "DTSTART" or key not in _ALLOWED:
            raise _invalid(f"RRULE part {key} is not supported for tasks", raw)
        if key in found:
            raise _invalid(f"RRULE part {key} appears twice", raw)
        found[key] = value

    freq = found.get("FREQ")
    if freq not in _FREQS:
        raise _invalid("FREQ must be DAILY, WEEKLY, MONTHLY or YEARLY", raw)
    interval = 1
    if "INTERVAL" in found:
        interval = _ints("INTERVAL", found["INTERVAL"], 1, 999)[0] if "," not in found["INTERVAL"] else 0
        if interval < 1:
            raise _invalid("INTERVAL must be one number 1..999", raw)
    count = found.get("COUNT")
    if count is not None and (not _COUNT.fullmatch(count) or not 1 <= int(count) <= 1000):
        raise _invalid("COUNT must be 1..1000", raw)
    until: date | None = None
    if "UNTIL" in found:
        match = _UNTIL.fullmatch(found["UNTIL"])
        try:
            until = datetime.strptime(match.group(1), "%Y%m%d").date() if match else None
        except ValueError:
            until = None
        if until is None or not date(2000, 1, 1) <= until <= date(2100, 12, 31):
            raise _invalid("UNTIL must be a date like 20271231", raw)
    if "WKST" in found and found["WKST"] not in _DAYS:
        raise _invalid("WKST must be a weekday code", raw)

    months = _ints("BYMONTH", found["BYMONTH"], 1, 12, nonzero=False) if "BYMONTH" in found else []
    monthdays = _ints("BYMONTHDAY", found["BYMONTHDAY"], 1, 31) if "BYMONTHDAY" in found else []
    setpos = _ints("BYSETPOS", found["BYSETPOS"], 1, 366) if "BYSETPOS" in found else []
    plain_days: list[str] = []
    ordinal_days: list[tuple[int, str]] = []
    if "BYDAY" in found:
        items = found["BYDAY"].split(",")
        if len(items) > 20:
            raise _invalid("BYDAY lists too many days", raw)
        for item in items:
            match = _BYDAY_ITEM.fullmatch(item)
            if not match:
                raise _invalid(f"BYDAY: invalid value {item!r}", raw)
            if match.group(1) is None:
                if match.group(2) not in plain_days:
                    plain_days.append(match.group(2))
                continue
            number = int(match.group(1))
            limit = 5 if freq == "MONTHLY" or months else 53
            if number == 0 or abs(number) > limit or freq not in ("MONTHLY", "YEARLY"):
                raise _invalid(f"BYDAY: ordinal {item!r} is not valid here", raw)
            if (number, match.group(2)) not in ordinal_days:
                ordinal_days.append((number, match.group(2)))

    filters = bool(months or monthdays or plain_days or ordinal_days or setpos)
    if freq == "DAILY" and interval > 1 and filters:
        raise _invalid("A daily rule with INTERVAL > 1 cannot have BY… parts", raw)
    if freq == "DAILY" and (ordinal_days or setpos):
        raise _invalid("Ordinal weekdays and BYSETPOS need FREQ=MONTHLY", raw)
    if freq == "WEEKLY" and (months or monthdays or ordinal_days or setpos):
        raise _invalid("A weekly rule only takes BYDAY", raw)
    if freq == "MONTHLY" and interval > 1 and (months or any(abs(day) > 28 for day in monthdays)):
        raise _invalid("With INTERVAL > 1 a monthly rule needs days 1..28 and no BYMONTH", raw)
    if ordinal_days and monthdays:
        raise _invalid("Ordinal weekdays cannot be combined with BYMONTHDAY", raw)
    if setpos:
        if freq != "MONTHLY" or not plain_days or ordinal_days or monthdays:
            raise _invalid("BYSETPOS needs FREQ=MONTHLY with plain BYDAY weekdays", raw)
        if any(abs(pos) > 4 * len(plain_days) for pos in setpos):
            raise _invalid("BYSETPOS is larger than the weekdays of a month", raw)
    if monthdays:
        pool = months or list(range(1, 13))
        if not any(abs(day) <= _MONTH_DAYS[month - 1] for day in monthdays for month in pool):
            raise _invalid("This rule never matches a calendar day", raw)

    parts: list[tuple[str, str]] = [("FREQ", freq)]
    if interval > 1:
        parts.append(("INTERVAL", str(interval)))
    if months:
        parts.append(("BYMONTH", ",".join(map(str, months))))
    if monthdays:
        parts.append(("BYMONTHDAY", ",".join(map(str, monthdays))))
    if plain_days or ordinal_days:
        days = [day for day in _DAYS if day in plain_days]
        days += [f"{n}{day}" for n, day in sorted(ordinal_days, key=lambda item: (_DAYS.index(item[1]), item[0]))]
        parts.append(("BYDAY", ",".join(days)))
    if setpos:
        parts.append(("BYSETPOS", ",".join(map(str, setpos))))
    if "COUNT" in found:
        parts.append(("COUNT", str(int(found["COUNT"]))))
    if found.get("WKST") not in (None, "MO"):
        parts.append(("WKST", found["WKST"]))
    return tuple(parts), until


_PRESET_TEXT: dict[str, str] = {}


def preset_of(text: str | None) -> str | None:
    """Preset key of a canonical rule (``None`` for no rule, ``custom`` handled by callers)."""
    if not text:
        return None
    if not _PRESET_TEXT:
        for key in CONTRACT_PRESETS:
            parts, _ = _canonical(RECURRENCE_PRESETS[key])
            _PRESET_TEXT[";".join(f"{k}={v}" for k, v in parts)] = key
    return _PRESET_TEXT.get(text)


def validate_rule(raw: str) -> Rule:
    """RRULE text or a preset key → :class:`Rule` (422 ``invalid_rrule`` otherwise)."""
    key = (raw or "").strip().lower()
    source = RECURRENCE_PRESETS[key] if key in CONTRACT_PRESETS else raw
    try:
        parts, until = _canonical(source)
    except ValueError as exc:  # a conversion the checks above missed is still a bad rule, never a 500
        raise _invalid(f"Unparsable recurrence rule: {exc}", raw) from None
    rule = Rule(text=";".join(f"{k}={v}" for k, v in parts), parts=parts, until=until)
    try:  # final word: dateutil must accept it
        parse_rule(rule.expansion_text, datetime(2026, 1, 1))
    except Exception as exc:  # dateutil raises ValueError, TypeError, AttributeError…
        raise _invalid(f"Unparsable recurrence rule: {exc}", raw) from None
    return rule


def parse_stored(text: str) -> Rule:
    parts, until = _canonical(text)
    return Rule(text=text, parts=parts, until=until)


def _rrule(rule: Rule, dtstart: datetime, bound: datetime | None = None):
    text = rule.expansion_text
    if bound is not None:
        text = f"{text};UNTIL={bound:%Y%m%dT%H%M%S}"
    return parse_rule(text, dtstart)


def first_on_or_after(rule: Rule, day: date, until: date | None = None) -> date | None:
    """First occurrence on/after ``day`` with the rule anchored at ``day`` 00:00."""
    start = datetime.combine(day, time.min)
    found = _rrule(rule, start).after(start, inc=True)
    if found is None or (until is not None and found.date() > until):
        return None
    return found.date()


def next_after(rule: Rule, anchor: datetime, today: date, until: date | None = None) -> datetime | None:
    """First occurrence strictly after ``anchor`` (naive local) on a local date after ``today``."""
    cursor = max(anchor, datetime.combine(today, time.max))
    found = _rrule(rule, anchor).after(cursor, inc=False)
    if found is None or (until is not None and found.date() > until):
        return None
    return found


def occurrences_between(
    rule: Rule,
    anchor: datetime,
    *,
    first: date,
    last: date,
    after: date,
    until: date | None = None,
    remaining: int | None = None,
    limit: int = 400,
) -> list[date]:
    """Dates of the occurrences in ``[first, last]`` that fall after ``after``.

    ``remaining`` (from COUNT) counts the occurrences after ``after`` in order,
    including ones before ``first``.
    """
    lo = after + timedelta(days=1)
    hi = min(last, until) if until is not None else last
    if hi < lo or hi < first or (remaining is not None and remaining <= 0):
        return []
    bound = datetime.combine(hi, time.max)
    found = _rrule(rule, anchor, bound).between(max(anchor, datetime.combine(lo, time.min)), bound, inc=True)
    dates: list[date] = []
    for occurrence in found:
        day = occurrence.date()
        if day < lo or occurrence <= anchor:
            continue
        if remaining is not None:
            if remaining <= 0:
                break
            remaining -= 1
        if day >= first:
            dates.append(day)
            if len(dates) >= limit:
                break
    return dates
