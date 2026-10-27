"""Quick-add natural-language parser for tasks (stage 6), Russian + English.

Pure function, no database and no I/O: the planner service passes the user's
local "now", week start and the names of their active projects/areas, then
turns the result into a task. The client never parses dates itself — it shows
the tokens this module returns (``POST /api/v1/planner/parse``).

    parse_task_text("завтра 18:00 зал #здоровье p2", now=..., week_start=1)
    -> ParsedTask(title="зал", due_date=<tomorrow>, due_time=18:00, tags=["здоровье"], priority=2, ...)

Token offsets are UTF-16 code units (what JavaScript's ``String.slice`` uses),
so the UI can highlight exactly what was recognised.

How it works — three passes over the original string, so every offset stays valid:

1. *Sigil tokens* claim their characters first: URLs and e-mail addresses (protected, never a
   token), ``+project``, ``#tag``, ``@context``, ``p1``/``!1``/``р1``. A project name may itself
   contain digits or weekday names, so nothing temporal may look inside a claimed span.
2. *Temporal tokens* (reminders, recurrence, time ranges, dates, times, durations) are produced
   as candidates by independent recognisers; the **longest candidate wins an overlap**
   (``напомни за 15 мин`` beats the ``15 мин`` inside it, ``с 18 до 19:30`` beats ``до 19:30``).
   A recogniser that is not sure stays silent and the text simply stays in the title — the
   parser never guesses (``1.5 кг``, ``10 м``, ``пн-пт``, ``через 2 часа`` are left alone).
3. *Assembly* turns the winners into fields. The first date / time / duration / recurrence /
   priority / context / project wins and later duplicates stay in the title; a time range, or a
   time plus a duration, becomes a block; a time without a date lands on today or tomorrow
   relative to ``now``; the title is the original text minus the token spans, tidied.

Things the planner service must know:

* A recurrence never invents a date: ``каждый понедельник в 9`` gives ``due_time=09:00`` with
  ``due_date=None`` (the service picks the first occurrence and fills it in). The same holds
  for a block (``block_date`` is ``None`` then).
* Without a recurrence ``due_date`` is always set when a time or a block is (explicit date, or
  today/tomorrow). For a block, ``block_date == due_date`` and ``due_time`` is ``None``;
  ``block_end <= block_start`` means the block ends the next day.
* ``estimate_minutes`` is set by a duration on its own (or next to a time *range*); a duration
  that completes a lone time into a block is consumed by the block.
* ``recurrence_preset`` is one of the six contract presets or ``custom``. The rule
  ``FREQ=MONTHLY;BYMONTHDAY=1`` equals the service-side ``monthly_first`` entry, which the API
  enumeration does not list, so it is reported as ``custom`` (see ``_CONTRACT_PRESETS``).
* The output is always valid ``TaskIn`` material: at most 10 tags and 5 reminders (extra ones
  stay in the title), tag/context ≤ 64 chars, estimate 1..1440, reminder lead 0..10080. Only the
  title length is not enforced here (the text limit is 500, the title limit 300).
"""

from __future__ import annotations

import calendar
import re
from bisect import bisect_left
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, cast

from app.services.recurrence import RECURRENCE_PRESETS

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


# --------------------------------------------------------------------------- #
# Limits (they mirror the TaskIn validation so a parse is always saveable)
# --------------------------------------------------------------------------- #
_MAX_TAGS = 10
_MAX_REMINDERS = 5
_MAX_NAME_LEN = 64  # tag / context
_MAX_REMINDER_LEAD = 10080  # minutes (one week)
_MAX_ESTIMATE = 1440  # minutes (24 h)

# The presets the contract lists for ParseOut/TaskOut. ``RECURRENCE_PRESETS`` also holds
# ``monthly_first`` (the 1st of the month) which the API enumeration does not know — a rule
# equal to it is reported as "custom" so the UI never receives an unlisted preset key.
_CONTRACT_PRESETS = ("daily", "weekdays", "weekly", "biweekly", "monthly", "yearly")

_BYDAY = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")  # index = Python weekday (Monday = 0)


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
def _table(*groups: tuple[int, str]) -> dict[str, int]:
    """``(value, "space separated spellings")`` pairs -> ``{spelling: value}``."""
    return {word: value for value, words in groups for word in words.split()}


_RU_NUMBERS = _table(
    (1, "один одна одну одно"),
    (2, "два две"),
    (3, "три"),
    (4, "четыре"),
    (5, "пять"),
    (6, "шесть"),
    (7, "семь"),
    (8, "восемь"),
    (9, "девять"),
    (10, "десять"),
    (11, "одиннадцать"),
    (12, "двенадцать"),
    (13, "тринадцать"),
    (14, "четырнадцать"),
    (15, "пятнадцать"),
    (16, "шестнадцать"),
    (17, "семнадцать"),
    (18, "восемнадцать"),
    (19, "девятнадцать"),
    (20, "двадцать"),
    (30, "тридцать"),
    (40, "сорок"),
    (50, "пятьдесят"),
    (60, "шестьдесят"),
)
_EN_NUMBERS = _table(
    (1, "one"),
    (2, "two"),
    (3, "three"),
    (4, "four"),
    (5, "five"),
    (6, "six"),
    (7, "seven"),
    (8, "eight"),
    (9, "nine"),
    (10, "ten"),
    (11, "eleven"),
    (12, "twelve"),
    (13, "thirteen"),
    (14, "fourteen"),
    (15, "fifteen"),
    (16, "sixteen"),
    (17, "seventeen"),
    (18, "eighteen"),
    (19, "nineteen"),
    (20, "twenty"),
    (30, "thirty"),
    (40, "forty"),
    (50, "fifty"),
    (60, "sixty"),
)
_NUMBER_WORDS = {**_RU_NUMBERS, **_EN_NUMBERS}

# "every second day", "каждую вторую неделю", "every other week"
_ORDINALS = _table(
    (2, "второй вторую второе other second"),
    (3, "третий третью третье third"),
    (4, "четвертый четвёртый четвертую четвёртую fourth"),
    (5, "пятый пятую fifth"),
)

_MONTHS_RU = _table(
    (1, "января январь янв"),
    (2, "февраля февраль февр фев"),
    (3, "марта март мар"),
    (4, "апреля апрель апр"),
    (5, "мая май"),
    (6, "июня июнь июн"),
    (7, "июля июль июл"),
    (8, "августа август авг"),
    (9, "сентября сентябрь сент сен"),
    (10, "октября октябрь окт"),
    (11, "ноября ноябрь нояб ноя"),
    (12, "декабря декабрь дек"),
)
_MONTHS_EN = _table(
    (1, "january jan"),
    (2, "february feb"),
    (3, "march mar"),
    (4, "april apr"),
    (5, "may"),
    (6, "june jun"),
    (7, "july jul"),
    (8, "august aug"),
    (9, "september sept sep"),
    (10, "october oct"),
    (11, "november nov"),
    (12, "december dec"),
)
_MONTHS = {**_MONTHS_RU, **_MONTHS_EN}

# Weekdays, Monday = 0 (Python's ``date.weekday()``).
_WD_NOM = _table(
    (0, "понедельник"), (1, "вторник"), (2, "среда"), (3, "четверг"), (4, "пятница"), (5, "суббота"), (6, "воскресенье")
)
_WD_ACC = _table((2, "среду"), (4, "пятницу"), (5, "субботу"))  # "в среду"; the other days equal the nominative
_WD_GEN_DAT = _table(  # "до пятницы", "к пятнице"
    (0, "понедельника понедельнику"),
    (1, "вторника вторнику"),
    (2, "среды среде"),
    (3, "четверга четвергу"),
    (4, "пятницы пятнице"),
    (5, "субботы субботе"),
    (6, "воскресенья воскресенью"),
)
_WD_PLURAL_RU = _table(  # "по понедельникам"
    (0, "понедельникам"),
    (1, "вторникам"),
    (2, "средам"),
    (3, "четвергам"),
    (4, "пятницам"),
    (5, "субботам"),
    (6, "воскресеньям"),
)
_WD_ABBR_RU = _table((0, "пн"), (1, "вт"), (2, "ср"), (3, "чт"), (4, "пт"), (5, "сб"), (6, "вс"))
_WD_EN = _table(
    (0, "monday"), (1, "tuesday"), (2, "wednesday"), (3, "thursday"), (4, "friday"), (5, "saturday"), (6, "sunday")
)
_WD_EN_ABBR = _table((0, "mon"), (1, "tues tue"), (2, "wed"), (3, "thurs thur thu"), (4, "fri"), (5, "sat"), (6, "sun"))
_WD_EN_PLURAL = _table(
    (0, "mondays"),
    (1, "tuesdays"),
    (2, "wednesdays"),
    (3, "thursdays"),
    (4, "fridays"),
    (5, "saturdays"),
    (6, "sundays"),
)
_WD_ALL = {
    **_WD_NOM,
    **_WD_ACC,
    **_WD_GEN_DAT,
    **_WD_PLURAL_RU,
    **_WD_ABBR_RU,
    **_WD_EN,
    **_WD_EN_ABBR,
    **_WD_EN_PLURAL,
}

# Temporal prepositions that may be stranded right in front of a removed token ("report by" + friday).
# Deliberately short: "to", "for", "с", "за" ... are too often real words of the title.
_DANGLING_PREPOSITIONS = frozenset({"в", "во", "на", "к", "до", "at", "on", "by", "until", "till"})
# Words in front of a bare duration/time that make it something else ("через 30 мин" = in 30 minutes).
_NOT_A_DURATION_AFTER = frozenset(
    {
        "через",
        "за",
        "в",
        "во",
        "спустя",
        "после",
        "каждые",
        "каждый",
        "каждую",
        "каждое",
        "раз",
        "in",
        "after",
        "every",
        "each",
        "within",
    }
)


# --------------------------------------------------------------------------- #
# Regex building blocks
# --------------------------------------------------------------------------- #
def _alt(*tables: dict[str, Any]) -> str:
    """Alternation of every key of the given tables, longest first."""
    words = {word for table in tables for word in table}
    return "(?:" + "|".join(re.escape(word) for word in sorted(words, key=lambda w: (-len(w), w))) + ")"


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Word edges. A word glued to another word by a hyphen or en dash ("пн-пт", "mon-fri", "e-mail") is
# part of a range or a compound, not a standalone token, so the parser stays silent.
_L = r"(?<!\w)(?<!\w[-\u2013])"
_R = r"(?!\w)(?![-\u2013]\w)"
# Number edges: also not the tail/head of 1.5 / 10:30 / a/b / a-b / 12.05-14.05
_LN = r"(?<![\w:/\-])(?<![0-9][.,])"
_RN = r"(?!\w|[.,:][0-9]|[-\u2013]\w)"

_H = r"(?:[01]?[0-9]|2[0-3])"  # hour 0..23
_H_END = r"(?:[01]?[0-9]|2[0-4])"  # the end of a range may say 24 ("с 22 до 24")
_MM = r"[0-5][0-9]"
_AP = r"(?:a\.?m\.?|p\.?m\.?)"  # am / pm / a.m. / p.m.
_DP = r"(?:утра|дня|вечера|ночи)"  # Russian part of day after an hour
_CLOCK_WORD = r"час(?:а|ов)?"  # "в 18 часов", "в 2 часа"

_NUMW = "|".join(re.escape(w) for w in sorted(_NUMBER_WORDS, key=lambda w: (-len(w), w)))
_NUM_INT = rf"(?:[0-9]{{1,4}}|{_NUMW})"
_NUM_DEC = rf"(?:[0-9]{{1,4}}(?:[.,][0-9]{{1,2}})?|{_NUMW})"

_HOUR_UNIT = r"(?:hours|hour|hrs|hr|h|часов|часа|час|ч)"
_MIN_UNIT = r"(?:minutes|minute|mins|min|m|минуты|минуту|минута|минут|мин|м)"
_UNIT_END = r"(?![^\W\d_])"  # a unit must not run into more letters ("1h30m" is fine, "5mb" is not)

_DASHES = "-\u2013\u2014"  # hyphen-minus, en dash, em dash (as a separator between words)
_LEAD_STRIP = re.compile(rf"^(?:[\s,;:|]+|[{_DASHES}\u2022*\u00b7]+(?=\s))+")
_TRAIL_STRIP = re.compile(rf"(?:[\s,;:|]+|(?<=\s)[{_DASHES}]+)+$")
_LEAD_STRIP_CUT = re.compile(rf"^(?:[\s,;:|]+|[{_DASHES}\u2022*\u00b7]+(?=\s)|\.+(?=\s))+")
_TRAIL_STRIP_CUT = re.compile(rf"(?:[\s,;:|.]+|(?<=\s)[{_DASHES}]+)+$")
_DASH_RUN = re.compile(rf"(?:^|\s)[{_DASHES}]+(?=\s[{_DASHES}]+(?:\s|$))")
_PAIRS_EMPTY = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}|«\s*»|“\s*”|\"\s*\"")


# --------------------------------------------------------------------------- #
# Compiled recognisers
# --------------------------------------------------------------------------- #
_PROTECTED_RX = _rx(r"(?<!\w)(?:https?://|www\.)[^\s<>]+|[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_TAG_RX = re.compile(r"(?<!\S)#([\w\-]+)")
_CONTEXT_RX = re.compile(r"(?<!\S)@([\w\-]+)")
# "p2", "P2", "!2" and the Cyrillic "р2" (\u0440/\u0420) that a Russian keyboard types for "p"
_PRIORITY_RX = re.compile(r"(?<![^\s(\[{\"'«“,;])(?:[pP\u0440\u0420]|!)([1-4])(?![^\s)\]}\"'»”,.;:!?])(?!\.[0-9])")
_TAG_AFTER_OK = re.compile(r"(?:$|[\s,;:!?)\]}»”\"'…–—]|\.(?:$|\s))")

# -- reminders ---------------------------------------------------------------
_REM_UNIT_RU = r"(?:минут[аыу]?|мин|м|часов|часа|час|ч|дней|дня|день|дн|д|недель|недели|неделю|неделя|нед|сутки|суток)"
_REM_RU_RX = _rx(
    _L
    + r"(?:напомни(?:ть)?|напоминани[еяю]|напоминай(?:те)?)(?:\s+мне)?\s+за\s+"
    + r"(?:(?P<half>полчаса|пол[\s-]?часа)|(?P<oneh>полтора\s+часа|полторы\s+часа)"
    + rf"|(?:(?P<n>{_NUM_DEC})\s*)?(?P<u>{_REM_UNIT_RU}))"
    + _R
)
_REM_UNIT_EN = r"(?:minutes|minute|mins|min|m|hours|hour|hrs|hr|h|days|day|d|weeks|week|w)"
_REM_EN_RX = _rx(
    _L
    + r"(?:remind(?:\s+me)?|reminder)\s+"
    + r"(?:(?P<half>half\s+an?\s+hour|half[\s-]?hour)"
    + rf"|(?P<n>{_NUM_DEC}|an?)\s*(?P<u>{_REM_UNIT_EN}){_UNIT_END})"
    + r"(?:\s+(?:before|prior))?"
    + _R
)
_REM_UNIT_MINUTES: dict[str, int] = {
    **dict.fromkeys(("мин", "минут", "минута", "минуту", "минуты", "м", "m", "min", "mins", "minute", "minutes"), 1),
    **dict.fromkeys(("час", "часа", "часов", "ч", "h", "hr", "hrs", "hour", "hours"), 60),
    **dict.fromkeys(("день", "дня", "дней", "дн", "д", "сутки", "суток", "d", "day", "days"), 1440),
    **dict.fromkeys(("неделя", "неделю", "недели", "недель", "нед", "w", "week", "weeks"), 10080),
}
_REM_SINGULAR_ONLY = frozenset({"час", "день", "неделя", "неделю", "минута", "минуту", "сутки", "суток"})

# -- recurrence --------------------------------------------------------------
_REC_FIXED: tuple[tuple[re.Pattern[str], str], ...] = (
    (_rx(_L + r"(?:ежедневно|daily|everyday)" + _R), "FREQ=DAILY"),
    (_rx(_L + r"(?:еженедельно|weekly)" + _R), "FREQ=WEEKLY"),
    (_rx(_L + r"(?:ежемесячно|monthly)" + _R), "FREQ=MONTHLY"),
    (_rx(_L + r"(?:ежегодно|yearly|annually)" + _R), "FREQ=YEARLY"),
    (_rx(_L + r"(?:biweekly|fortnightly)" + _R), "FREQ=WEEKLY;INTERVAL=2"),
    (
        _rx(
            _L
            + r"(?:по\s+(?:будням|будним\s+дням|рабочим\s+дням)|в\s+будни"
            + r"|(?:каждый|каждые)\s+(?:будний\s+день|рабочий\s+день|будни)"
            + r"|every\s+(?:week\s?day|workday|business\s+day)|(?:on\s+)?(?:week\s?days|workdays))"
            + _R
        ),
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    ),
    (
        _rx(_L + r"(?:по\s+выходным|(?:каждые|каждый)\s+(?:выходные|уикенд)|every\s+weekend|(?:on\s+)?weekends)" + _R),
        "FREQ=WEEKLY;BYDAY=SA,SU",
    ),
)
_REC_RU_UNIT_RX = _rx(
    _L
    + r"(?:(?:каждый|каждая|каждую|каждое|каждые)|раз\s+в)\s+"
    + rf"(?:(?P<n>{_NUM_INT}|{_alt(_ORDINALS)})\s+)?"
    + r"(?P<u>дней|дня|день|дни|недель|недели|неделю|неделя|месяцев|месяца|месяц|лет|года|год)"
    + _R
)
_REC_EN_UNIT_RX = _rx(
    _L + rf"(?:every|each)\s+(?:(?P<n>{_NUM_INT}|{_alt(_ORDINALS)})\s+)?(?P<u>days?|weeks?|months?|years?)" + _R
)
_WD_JOIN_RU = r"(?:\s*,\s*и\s+|\s*,\s*|\s+и\s+|\s*/\s*|\s*&\s*)"
_WD_JOIN_EN = r"(?:\s*,\s*and\s+|\s*,\s*|\s+and\s+|\s*/\s*|\s*&\s*)"
_WD_ITEM_RU = _alt(_WD_NOM, _WD_ACC, _WD_ABBR_RU)
_WD_ITEM_PO_RU = _alt(_WD_PLURAL_RU, _WD_ABBR_RU)
_WD_ITEM_EN = _alt(_WD_EN, _WD_EN_ABBR)
_WD_ITEM_PL_EN = _alt(_WD_EN_PLURAL)
_REC_WD_RU_RX = _rx(
    _L
    + r"(?:каждый|каждая|каждую|каждое|каждые)\s+(?:(?P<ord>второй|вторую|второе)\s+)?"
    + rf"(?P<list>{_WD_ITEM_RU}(?:{_WD_JOIN_RU}{_WD_ITEM_RU})*)"
    + _R
)
_REC_WD_PO_RX = _rx(_L + rf"по\s+(?P<list>{_WD_ITEM_PO_RU}(?:{_WD_JOIN_RU}{_WD_ITEM_PO_RU})*)" + _R)
_REC_WD_EN_RX = _rx(
    _L
    + r"(?:every|each)\s+(?:(?P<ord>other|second)\s+)?"
    + rf"(?P<list>{_WD_ITEM_EN}(?:{_WD_JOIN_EN}{_WD_ITEM_EN})*)"
    + _R
)
_REC_WD_PL_EN_RX = _rx(_L + rf"(?:on\s+)?(?P<list>{_WD_ITEM_PL_EN}(?:{_WD_JOIN_EN}{_WD_ITEM_PL_EN})*)" + _R)
_REC_MONTHDAY_RXS: tuple[re.Pattern[str], ...] = (
    _rx(_L + r"каждо(?:е|го)\s+(?P<d>[0-9]{1,2})(?:-(?:е|го|ое|ого|й))?\s+числ[ао](?:\s+(?:каждого\s+)?месяца)?" + _R),
    _rx(_L + r"(?P<d>[0-9]{1,2})(?:-(?:е|го))?\s+числа\s+каждого\s+месяца" + _R),
    _rx(_L + r"every\s+(?P<d>[0-9]{1,2})(?:st|nd|rd|th)(?:\s+(?:day\s+)?of\s+(?:the|every|each)\s+month)?" + _R),
    _rx(_L + r"(?:on\s+)?the\s+(?P<d>[0-9]{1,2})(?:st|nd|rd|th)\s+of\s+(?:every|each)\s+month" + _R),
)
_REL_OFFSETS = _table(
    (0, "сегодня today"),
    (1, "завтра tomorrow tmr tmrw"),
    (2, "послезавтра"),
)
_REL_OFFSETS["day after tomorrow"] = 2
_UNIT_FREQ = (
    ("дн", "DAILY"),
    ("ден", "DAILY"),
    ("day", "DAILY"),
    ("нед", "WEEKLY"),
    ("week", "WEEKLY"),
    ("мес", "MONTHLY"),
    ("month", "MONTHLY"),
    ("лет", "YEARLY"),
    ("год", "YEARLY"),
    ("year", "YEARLY"),
)

# -- time ranges (blocks) ------------------------------------------------------
_RANGE_DASH = r"[-\u2010\u2011\u2012\u2013\u2014]"  # hyphen, hyphen, non-breaking hyphen, figure/en/em dash


def _tp(n: str, hour: str = _H) -> str:
    """One end of a time range: hour[:minutes] [час] [am/pm | утра/дня/вечера/ночи]."""
    return (
        rf"(?P<h{n}>{hour})(?::(?P<m{n}>{_MM}))?"
        rf"(?:\s+{_CLOCK_WORD}(?!\w))?"
        rf"(?:\s*(?P<ap{n}>{_AP})(?!\w)|\s+(?P<dp{n}>{_DP})(?!\w))?"
    )


_RANGE_RX = _rx(
    _LN
    + r"(?P<lead>(?:с|со|from|в|во|на|at)\s+)?"
    + _tp("1")
    + rf"(?:(?P<sepw>\s+(?:до|по|to|until|till)\s+)|(?P<sepd>\s*{_RANGE_DASH}\s*))"
    + _tp("2", _H_END)
    + _RN
    + r"(?!-[0-9])"
)

# -- single times ----------------------------------------------------------------
_TIME_PRE = r"(?P<pre>(?:в|во|на|к|до|at|by|until|till)\s+)?"
_TIME_COLON_RX = _rx(
    _LN + _TIME_PRE + rf"(?P<h>{_H}):(?P<m>{_MM})(?:\s*(?P<ap>{_AP})(?!\w)|\s+(?P<dp>{_DP})(?!\w))?" + _RN
)
_TIME_AMPM_RX = _rx(_LN + _TIME_PRE + rf"(?P<h>[0-9]{{1,2}})\s*(?P<ap>{_AP})(?!\w)" + _RN)
_TIME_DAYPART_RX = _rx(
    _LN + r"(?P<pre>(?:в|во)\s+)?" + rf"(?P<h>{_H})(?:\s*{_CLOCK_WORD})?\s+(?P<dp>{_DP})(?!\w)" + _RN
)
_TIME_HOUR_RX = _rx(_LN + r"(?P<pre>(?:в|во|at)\s+)" + rf"(?P<h>{_H})(?:\s*{_CLOCK_WORD}|\s*ч(?!\w))?" + _RN)
_TIME_WORD_RX = _rx(_L + r"(?P<pre>(?:в|во|at)\s+)?(?P<w>полдень|полночь|noon|midnight)" + _R)

# -- durations ---------------------------------------------------------------------
_DUR_PRE = r"(?P<pre>(?:на|for)\s+)?"
_DUR_HOURS_RX = _rx(
    _LN
    + _DUR_PRE
    + rf"(?P<h>[0-9]{{1,4}}(?:[.,][0-9]{{1,2}})?)\s*{_HOUR_UNIT}{_UNIT_END}"
    + rf"(?:\s*(?P<hm>[0-9]{{1,3}})\s*{_MIN_UNIT}{_UNIT_END})?"
    + _R
)
_DUR_H_M_RX = _rx(_LN + _DUR_PRE + r"(?P<h>[0-9]{1,2})(?:h|ч)(?P<m>[0-5][0-9])" + _R)  # 1h30, 1ч30
# "30м" / "45m" glue the one-letter unit to the number ("10 м кабеля" is metres); words may be spaced
_DUR_MINUTES_RX = _rx(
    _LN
    + _DUR_PRE
    + r"(?P<m>[0-9]{1,4})(?:\s*(?:minutes|minute|mins|min|минуты|минуту|минута|минут|мин)|[mм])"
    + _UNIT_END
    + _R
)
_DUR_WORDS_RX = _rx(_L + _DUR_PRE + rf"(?P<w>{_NUMW})\s+(?:(?P<hu>{_HOUR_UNIT})|(?P<mu>{_MIN_UNIT})){_UNIT_END}" + _R)
_DUR_SPECIAL_RX = _rx(
    _L
    + _DUR_PRE
    + r"(?:(?P<half>полчаса|пол[\s-]?часа|half\s+an?\s+hour|half[\s-]?hour)"
    + r"|(?P<oneh>полтора\s+часа|полторы\s+часа)"
    + r"|(?P<hour>an?\s+hour|час)|(?P<minute>an?\s+(?:minute|min)))"
    + _R
    + r"(?!\s+(?:назад|ago|later|спустя))"
)

# -- dates -------------------------------------------------------------------------
_REL_WORD_RX = _rx(
    _L
    + r"(?P<pre>(?:на|до|by|until|till)\s+)?"
    + r"(?P<w>day\s+after\s+tomorrow|послезавтра|сегодня|завтра|today|tomorrow|tmrw|tmr)"
    + _R
    + r"(?!['’]s(?!\w))"
)
_REL_RU_RX = _rx(
    _L
    + rf"через\s+(?:(?P<n>{_NUM_INT})\s+)?"
    + r"(?P<u>дней|дня|день|дни|недель|недели|неделю|неделя|месяцев|месяца|месяц|лет|года|год)"
    + _R
)
_REL_EN_RX = _rx(_L + rf"in\s+(?P<n>{_NUM_INT}|an?)\s+(?P<u>days?|weeks?|months?|years?)" + _R)
_WEEKEND_RX = _rx(
    _L
    + r"(?:на\s+(?:этих\s+)?выходных|на\s+выходные|в\s+выходные|this\s+weekend"
    + r"|(?:on|at|over)\s+the\s+weekend)"
    + _R
)
_NEXT_WEEK_RX = _rx(_L + r"(?:(?:на|в)\s+следующ(?:ей|ую)\s+недел(?:е|ю)|на\s+след\.?\s+недел(?:е|ю)|next\s+week)" + _R)
_WEEKDAY_RX = _rx(
    _L
    + r"(?:(?P<p1>(?:в|во|на)\s+(?:следующ(?:ий|ую|ее)\s+)?)(?P<w1>"
    + _alt(_WD_NOM, _WD_ACC, _WD_ABBR_RU)
    + r")"
    + r"|(?P<p2>(?:до|к)\s+)(?P<w2>"
    + _alt(_WD_GEN_DAT, _WD_ABBR_RU)
    + r")"
    + r"|(?P<p3>(?:on|by|until|till|next)\s+)(?P<w3>"
    + _alt(_WD_EN, _WD_EN_ABBR)
    + r")"
    + r"|(?P<w4>"
    + _alt(_WD_NOM, _WD_ABBR_RU, _WD_EN, _WD_EN_ABBR)
    + r"))"
    + _R
)
_DATE_PRE = r"(?P<pre>(?:на|к|до|on|by|until|till)\s+)?"
_YEAR_SUFFIX = r"(?:\s*(?:года|г\.?)(?!\w))?"
_DATE_DAY_MONTH_RX = _rx(
    _LN
    + _DATE_PRE
    + r"(?P<d>[0-3]?[0-9])(?:st|nd|rd|th|-(?:го|е|ое))?\s+(?:of\s+)?"
    + rf"(?P<mon>{_alt(_MONTHS)})(?:,?\s+(?P<y>20[0-9]{{2}}){_YEAR_SUFFIX})?"
    + _R
)
_DATE_MONTH_DAY_RX = _rx(
    _LN
    + r"(?P<pre>(?:on|by|until|till)\s+)?"
    + rf"(?P<mon>{_alt(_MONTHS_EN)})\s+(?P<d>[0-3]?[0-9])(?:st|nd|rd|th)?"
    + r"(?:,?\s+(?P<y>20[0-9]{2}))?"
    + _RN
)
_DATE_NUMERIC_RX = _rx(
    _LN
    + _DATE_PRE
    + r"(?:(?P<d1>[0-3]?[0-9])\.(?P<m1>0[1-9]|1[0-2])\.(?P<y1>20[0-9]{2}|[0-9]{2})"
    + r"|(?P<d2>[0-3]?[0-9])\.(?P<m2>[0-9]{1,2})\.(?P<y2>20[0-9]{2})"
    + r"|(?P<d3>[0-3]?[0-9])\.(?P<m3>0[1-9]|1[0-2]))"
    + _RN
)
_DATE_ISO_RX = _rx(_LN + _DATE_PRE + r"(?P<y>20[0-9]{2})-(?P<m>0[1-9]|1[0-2])-(?P<d>0[1-9]|[12][0-9]|3[01])" + _RN)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _Cand:
    """A recognised span (Python string indices) with its parsed value."""

    start: int
    end: int
    kind: str
    value: Any = None


_KIND_RANK = {"block": 0, "time": 1, "date": 2, "recurrence": 3, "reminder": 4, "duration": 5}


def _matches(rx: re.Pattern[str], text: str) -> Iterator[re.Match[str]]:
    """Every match of ``rx``, including ones that start inside an earlier match."""
    pos = 0
    while pos <= len(text):
        found = rx.search(text, pos)
        if found is None:
            return
        yield found
        pos = found.start() + 1


def _lookup(table: dict[str, int], word: str) -> int | None:
    """Table lookup that survives exotic case folding (``ſat``, ``İ``): unknown words are just ``None``."""
    for variant in (word.lower(), word.casefold()):
        if variant in table:
            return table[variant]
    return None


def _decimal(token: str) -> Decimal | None:
    word = _lookup(_NUMBER_WORDS, token)
    if word is not None:
        return Decimal(word)
    try:
        return Decimal(token.replace(",", "."))
    except InvalidOperation:
        return None


def _integer(token: str | None) -> int | None:
    if token is None:
        return None
    value = _decimal(token)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)


def _round_minutes(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _prev_word(text: str, index: int) -> str:
    """The lower-cased word that ends right before ``index`` (spaces skipped), or ``""``."""
    end = index
    while end > 0 and text[end - 1].isspace():
        end -= 1
    begin = end
    while begin > 0 and (text[begin - 1].isalnum() or text[begin - 1] == "_"):
        begin -= 1
    return text[begin:end].lower()


def _add_months(day: date, months: int) -> date:
    year, month0 = divmod(day.year * 12 + day.month - 1 + months, 12)
    month = month0 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _next_weekday(today: date, weekday: int) -> date:
    """The next such weekday strictly after today (today never counts)."""
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def _week_start_of(day: date, week_start: int) -> date:
    first = (week_start - 1) % 7  # Python weekday index of the first day of the week
    return day - timedelta(days=(day.weekday() - first) % 7)


def _day_month(day: int, month: int, year: int | None, today: date) -> date | None:
    """``12 May`` -> the next 12 May on or after today; with a year the date is taken as is."""
    if year is not None:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for candidate_year in range(today.year, today.year + 9):  # 29 Feb needs up to 8 years
        try:
            found = date(candidate_year, month, day)
        except ValueError:
            continue
        if found >= today:
            return found
    return None


def _clock(hour: int, minute: int, ap: str | None, dp: str | None) -> time | None:
    """Wall-clock time from the written hour and an optional am/pm or Russian part of day."""
    if ap:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ap.lower().startswith("p") else 0)
    elif dp and hour <= 12:
        part = dp.lower()
        if part == "утра":
            hour = 0 if hour == 12 else hour
        elif part == "дня":
            hour = hour + 12 if 1 <= hour <= 6 else hour  # 12 дня = noon, 3 дня = 15:00, 10 дня = 10:00
        elif part == "вечера":
            hour = 0 if hour == 12 else (hour + 12 if hour >= 1 else hour)  # 7 вечера = 19:00; 12 вечера = midnight
        elif part == "ночи":
            hour = 0 if hour == 12 else (hour + 12 if hour >= 9 else hour)  # 12 ночи = 00:00, 11 ночи = 23:00
    if not 0 <= hour <= 23:
        return None
    return time(hour, minute)


def _opposite_half(value: time) -> time:
    return time((value.hour + 12) % 24, value.minute)


def _minutes_between(start: time, end: time) -> int:
    """Length of a block from ``start`` to ``end``; equal times mean a full 24 h."""
    gap = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return gap % 1440 or 1440


# --------------------------------------------------------------------------- #
# Pass 1 — sigil tokens
# --------------------------------------------------------------------------- #
def _is_free(claimed: bytearray, start: int, end: int) -> bool:
    return claimed.find(1, start, end) == -1


def _claim(claimed: bytearray, start: int, end: int) -> None:
    claimed[start:end] = b"\x01" * (end - start)


def _protect_urls_and_emails(text: str, claimed: bytearray) -> None:
    if "@" not in text and "://" not in text and "www." not in text.lower():
        return
    for found in _PROTECTED_RX.finditer(text):
        _claim(claimed, found.start(), found.end())


def _name_ok(name: str) -> bool:
    return 0 < len(name) <= _MAX_NAME_LEN and any(ch.isalpha() for ch in name)


def _scan_named(rx: re.Pattern[str], text: str, claimed: bytearray) -> list[tuple[int, int, str]]:
    """``#tag`` / ``@context`` spans: Unicode letters, digits, ``_`` and ``-``, at least one letter."""
    out: list[tuple[int, int, str]] = []
    for found in rx.finditer(text):
        raw = found.group(1)
        name = raw.rstrip("-")  # "#работа- купить": the dash is a separator, not part of the tag
        start, end = found.start(), found.start() + 1 + len(name)
        if not _name_ok(name) or not _is_free(claimed, start, end):
            continue
        if name == raw and not _TAG_AFTER_OK.match(text, end):
            continue  # "#tag@x", "#tag/x": glued to something else, leave it alone
        out.append((start, end, name))
    return out


def _fold(text: str) -> str:
    """Case- and ё/е-insensitive comparison key (people type "елка" for "Ёлка")."""
    return text.casefold().replace("ё", "е")


def _match_container_at(text: str, pos: int, words: list[str], folded: list[str]) -> int | None:
    """End index when the container name (case-insensitive, flexible spaces) starts at ``pos``."""
    index = pos
    size = len(text)
    for number, word in enumerate(words):
        if _fold(text[index : index + len(word)]) != folded[number]:
            return None
        index += len(word)
        if number < len(words) - 1:
            gap = index
            while gap < size and text[gap].isspace():
                gap += 1
            if gap == index:
                return None
            index = gap
    if index < size and not (text[index].isspace() or text[index] in ",;:.!?)]}»”\"'"):
        return None
    return index


def _scan_container(
    text: str, containers: Sequence[KnownContainer], claimed: bytearray
) -> tuple[int, int, KnownContainer] | None:
    """The first ``+Name`` / ``+[Long name]`` that names a known project (preferred) or area."""
    if "+" not in text or not containers:
        return None
    ranked: list[tuple[list[str], list[str], KnownContainer]] = []
    for container in containers:
        words = (container.name or "").split()
        if words:
            ranked.append((words, [_fold(word) for word in words], container))
    # projects before areas, then the longest name first (the sort is stable for equal lengths)
    ranked.sort(key=lambda item: (item[2].kind != "project", -len(" ".join(item[0]))))
    by_name: dict[str, KnownContainer] = {}
    for _, folded, container in ranked:
        by_name.setdefault(" ".join(folded), container)
    size = len(text)
    for plus in (i for i, ch in enumerate(text) if ch == "+"):
        if (plus > 0 and not text[plus - 1].isspace()) or plus + 1 >= size or text[plus + 1].isspace():
            continue
        if text[plus + 1] == "[":
            close = text.find("]", plus + 2)
            if close == -1 or "\n" in text[plus:close]:
                continue
            hit = by_name.get(_fold(" ".join(text[plus + 2 : close].split())))
            if hit is not None and _is_free(claimed, plus, close + 1):
                return plus, close + 1, hit
            continue
        for words, folded, container in ranked:
            end = _match_container_at(text, plus + 1, words, folded)
            if end is not None and _is_free(claimed, plus, end):
                return plus, end, container
    return None


def _scan_priority(text: str, claimed: bytearray) -> tuple[int, int, int] | None:
    for found in _PRIORITY_RX.finditer(text):
        if _is_free(claimed, found.start(), found.end()):
            return found.start(), found.end(), int(found.group(1))
    return None


# --------------------------------------------------------------------------- #
# Pass 2 — temporal candidates
# --------------------------------------------------------------------------- #
def _reminder_lead(found: re.Match[str]) -> int | None:
    groups = found.groupdict()
    if groups.get("half"):
        return 30
    if groups.get("oneh"):
        return 90
    unit = (groups.get("u") or "").lower()
    per_unit = _REM_UNIT_MINUTES.get(unit)
    raw = groups.get("n")
    if per_unit is None:
        return None
    if raw is None:
        if unit not in _REM_SINGULAR_ONLY:
            return None
        amount = Decimal(1)
    elif raw.lower() in ("a", "an"):
        amount = Decimal(1)
    else:
        parsed = _decimal(raw)
        if parsed is None:
            return None
        amount = parsed
    lead = _round_minutes(amount * per_unit)
    return lead if 0 <= lead <= _MAX_REMINDER_LEAD else None


def _scan_reminders(text: str) -> list[_Cand]:
    out: list[_Cand] = []
    for rx in (_REM_RU_RX, _REM_EN_RX):
        for found in _matches(rx, text):
            lead = _reminder_lead(found)
            if lead is not None:
                out.append(_Cand(found.start(), found.end(), "reminder", lead))
    return out


def _rule(freq: str, interval: int = 1, byday: Sequence[int] | None = None, monthday: int | None = None) -> str:
    parts = [f"FREQ={freq}"]
    if interval > 1:
        parts.append(f"INTERVAL={interval}")
    if byday:
        parts.append("BYDAY=" + ",".join(_BYDAY[i] for i in sorted(set(byday))))
    if monthday:
        parts.append(f"BYMONTHDAY={monthday}")
    return ";".join(parts)


def _freq_of(unit: str) -> str | None:
    lowered = unit.lower()
    for prefix, freq in _UNIT_FREQ:
        if lowered.startswith(prefix):
            return freq
    return None


def _weekday_list(raw: str) -> list[int]:
    indexes = (_lookup(_WD_ALL, word) for word in re.findall(r"[^\W\d_]+", raw))
    return [index for index in indexes if index is not None]  # _rule() puts them in calendar order


def _scan_recurrence(text: str) -> list[_Cand]:
    out: list[_Cand] = []

    def add(found: re.Match[str], rule: str | None) -> None:
        if rule is not None:
            out.append(_Cand(found.start(), found.end(), "recurrence", rule))

    for rx, rule in _REC_FIXED:
        for found in _matches(rx, text):
            add(found, rule)
    for rx in (_REC_RU_UNIT_RX, _REC_EN_UNIT_RX):
        for found in _matches(rx, text):
            raw = found.group("n")
            interval = 1 if raw is None else _lookup(_ORDINALS, raw) or _integer(raw)
            freq = _freq_of(found.group("u"))
            if freq is not None and interval is not None and 1 <= interval <= 999:
                add(found, _rule(freq, interval))
    for rx in (_REC_WD_RU_RX, _REC_WD_EN_RX, _REC_WD_PO_RX, _REC_WD_PL_EN_RX):
        for found in _matches(rx, text):
            days = _weekday_list(found.group("list"))
            if days:
                add(found, _rule("WEEKLY", 2 if found.groupdict().get("ord") else 1, days))
    for rx in _REC_MONTHDAY_RXS:
        for found in _matches(rx, text):
            day = int(found.group("d"))
            if 1 <= day <= 31:
                add(found, _rule("MONTHLY", monthday=day))
    return out


def _range_pieces(found: re.Match[str]) -> tuple[time, time] | None:
    """Start/end of a ``18:00-19:30`` / ``с 18 до 19:30`` / ``from 6pm to 7:30pm`` range."""
    lead = (found.group("lead") or "").strip().lower()
    word_sep = found.group("sepw")
    ends = []
    for n in ("1", "2"):
        minute_text = found.group(f"m{n}")
        hour = int(found.group(f"h{n}"))
        ends.append((hour, int(minute_text or 0), found.group(f"ap{n}"), found.group(f"dp{n}"), minute_text))
    (h1, m1, ap1, dp1, colon1), (h2, m2, ap2, dp2, colon2) = ends
    strong1 = colon1 is not None or bool(ap1 or dp1)  # "strong" = the end says more than a bare hour
    strong2 = colon2 is not None or bool(ap2 or dp2)
    if lead in ("с", "со", "from"):
        pass  # "с 18 до 19" / "from 6 to 7": bare hours are fine behind an explicit "from"
    elif lead:  # в / во / на / at
        if word_sep and not (strong1 or strong2):
            return None
    elif word_sep:
        if not (strong1 and strong2) or word_sep.strip().lower() == "по":
            return None  # "18:00 до 19:30" yes; "купить 2 до 18:00" no ("до 18:00" alone is a deadline)
    elif not (strong1 or strong2):
        return None  # "10-12" without any marker is not a time range
    if h1 == 24:
        return None
    if h2 == 24:  # "с 22 до 24"
        if m2:
            return None
        h2 = 0
    first = _clock(h1, m1, ap1, dp1)
    last = _clock(h2, m2, ap2, dp2)
    if first is None or last is None:
        return None
    # An end without am/pm/part-of-day inherits the other end's marker; of the two halves of the
    # day the one that makes the shorter block wins ("9-5pm" is 09:00-17:00, "6-7pm" 18:00-19:00).
    if (ap2 or dp2) and not (ap1 or dp1) and h1 <= 12:
        inherited = _clock(h1, m1, ap2, dp2) or first
        first = min((inherited, _opposite_half(inherited)), key=lambda option: _minutes_between(option, last))
    elif (ap1 or dp1) and not (ap2 or dp2) and h2 <= 12:
        inherited = _clock(h2, m2, ap1, dp1) or last
        last = min((inherited, _opposite_half(inherited)), key=lambda option: _minutes_between(first, option))
    return first, last


def _scan_blocks(text: str) -> list[_Cand]:
    out: list[_Cand] = []
    for found in _matches(_RANGE_RX, text):
        pair = _range_pieces(found)
        if pair is not None:
            out.append(_Cand(found.start(), found.end(), "block", pair))
    return out


def _relative_date(unit: str, count: int, today: date) -> date | None:
    freq = _freq_of(unit)
    try:
        if freq == "DAILY":
            return today + timedelta(days=count)
        if freq == "WEEKLY":
            return today + timedelta(weeks=count)
        if freq == "MONTHLY":
            return _add_months(today, count)
        if freq == "YEARLY":
            return _add_months(today, 12 * count)
    except (OverflowError, ValueError):
        return None
    return None


def _scan_dates(text: str, today: date, week_start: int) -> list[_Cand]:
    out: list[_Cand] = []

    def add(found: re.Match[str], value: date | None) -> None:
        if value is not None:
            out.append(_Cand(found.start(), found.end(), "date", value))

    for found in _matches(_REL_WORD_RX, text):
        offset = _lookup(_REL_OFFSETS, " ".join(found.group("w").split()))
        if offset is not None:
            add(found, today + timedelta(days=offset))
    for found in _matches(_REL_RU_RX, text):
        raw = found.group("n")
        unit = found.group("u")
        if raw is None and not unit.lower().startswith(("нед", "мес", "год", "лет")):
            continue  # "через день" is ambiguous (in a day / every other day): stay silent
        count = 1 if raw is None else _integer(raw)
        if count is not None and count >= 0:
            add(found, _relative_date(unit, count, today))
    for found in _matches(_REL_EN_RX, text):
        raw = found.group("n").lower()
        count = 1 if raw in ("a", "an") else _integer(raw)
        if count is not None and count >= 0:
            add(found, _relative_date(found.group("u"), count, today))
    for found in _matches(_WEEKEND_RX, text):
        add(found, today + timedelta(days=(5 - today.weekday()) % 7))
    for found in _matches(_NEXT_WEEK_RX, text):
        add(found, _week_start_of(today, week_start) + timedelta(days=7))
    for found in _matches(_WEEKDAY_RX, text):
        word = next(found.group(name) for name in ("w1", "w2", "w3", "w4") if found.group(name))
        weekday = _lookup(_WD_ALL, word)
        if weekday is not None:
            add(found, _next_weekday(today, weekday))
    for rx in (_DATE_DAY_MONTH_RX, _DATE_MONTH_DAY_RX):
        for found in _matches(rx, text):
            month = _lookup(_MONTHS, found.group("mon"))
            if month is not None:
                year = int(found.group("y")) if found.group("y") else None
                add(found, _day_month(int(found.group("d")), month, year, today))
    for found in _matches(_DATE_NUMERIC_RX, text):
        which = next(n for n in ("1", "2", "3") if found.group(f"d{n}"))
        year_text = found.groupdict().get(f"y{which}")
        year = None
        if year_text:
            year = int(year_text) + (2000 if len(year_text) == 2 else 0)
        add(found, _day_month(int(found.group(f"d{which}")), int(found.group(f"m{which}")), year, today))
    for found in _matches(_DATE_ISO_RX, text):
        add(found, _day_month(int(found.group("d")), int(found.group("m")), int(found.group("y")), today))
    return out


def _scan_times(text: str) -> list[_Cand]:
    out: list[_Cand] = []

    def add(found: re.Match[str], value: time | None) -> None:
        if value is not None and _prev_word(text, found.start()) != "раз":  # "раз в 2 часа" = every 2 hours
            out.append(_Cand(found.start(), found.end(), "time", value))

    for found in _matches(_TIME_COLON_RX, text):
        add(found, _clock(int(found.group("h")), int(found.group("m")), found.group("ap"), found.group("dp")))
    for found in _matches(_TIME_AMPM_RX, text):
        add(found, _clock(int(found.group("h")), 0, found.group("ap"), None))
    for found in _matches(_TIME_DAYPART_RX, text):
        dp = found.group("dp")
        if dp.lower() == "дня" and not found.group("pre"):
            continue  # "через 3 дня" / "на 3 дня" are not 15:00; only "в 3 дня" is
        add(found, _clock(int(found.group("h")), 0, None, dp))
    for found in _matches(_TIME_HOUR_RX, text):
        add(found, _clock(int(found.group("h")), 0, None, None))
    for found in _matches(_TIME_WORD_RX, text):
        add(found, time(12, 0) if found.group("w").lower() in ("полдень", "noon") else time(0, 0))
    return out


def _duration_minutes(found: re.Match[str], kind: str) -> int | None:
    groups = found.groupdict()
    if kind == "hours":
        hours = _decimal(groups["h"])
        if hours is None:
            return None
        total = hours * 60 + (Decimal(groups["hm"]) if groups.get("hm") else 0)
    elif kind == "h_m":
        total = Decimal(groups["h"]) * 60 + Decimal(groups["m"])
    elif kind == "minutes":
        total = Decimal(groups["m"])
    elif kind == "words":
        amount = _decimal(groups["w"])
        if amount is None:
            return None
        total = amount * (60 if groups.get("hu") else 1)
    else:  # special
        if groups.get("half"):
            total = Decimal(30)
        elif groups.get("oneh"):
            total = Decimal(90)
        elif groups.get("minute"):
            total = Decimal(1)
        else:
            if groups["hour"].lower() == "час" and not groups.get("pre"):
                return None  # a bare "час" is far too often "через час" / "час пик": needs "на час"
            total = Decimal(60)
    minutes = _round_minutes(total)
    return minutes if 1 <= minutes <= _MAX_ESTIMATE else None


def _scan_durations(text: str) -> list[_Cand]:
    out: list[_Cand] = []
    for rx, kind in (
        (_DUR_HOURS_RX, "hours"),
        (_DUR_H_M_RX, "h_m"),
        (_DUR_MINUTES_RX, "minutes"),
        (_DUR_WORDS_RX, "words"),
        (_DUR_SPECIAL_RX, "special"),
    ):
        for found in _matches(rx, text):
            minutes = _duration_minutes(found, kind)
            if minutes is None:
                continue
            if found.group("pre") is None and _prev_word(text, found.start()) in _NOT_A_DURATION_AFTER:
                continue  # "через 30 мин", "за 2 часа", "каждые 2 часа": not an estimate
            out.append(_Cand(found.start(), found.end(), "duration", minutes))
    return out


def _select(cands: list[_Cand], claimed: bytearray) -> list[_Cand]:
    """Longest candidate wins an overlap; ties go to the more specific kind, then the earlier one."""
    accepted: list[_Cand] = []
    for cand in sorted(cands, key=lambda c: (c.start - c.end, _KIND_RANK[c.kind], c.start)):
        if _is_free(claimed, cand.start, cand.end):
            _claim(claimed, cand.start, cand.end)
            accepted.append(cand)
    accepted.sort(key=lambda c: c.start)
    return accepted


# --------------------------------------------------------------------------- #
# Pass 3 — title
# --------------------------------------------------------------------------- #
def _has_content(piece: str) -> bool:
    return any(ch.isalnum() for ch in piece)


def _build_title(text: str, spans: list[tuple[int, int]]) -> str:
    pieces: list[str] = []
    previous = 0
    for start, end in spans:
        pieces.append(text[previous:start])
        previous = end
    pieces.append(text[previous:])
    last = len(pieces) - 1

    # A preposition stranded in front of a removed token ("report by" + [friday]) leaves with it —
    # but only when it sits at the edge of the title right before the removed span, so
    # "завтра в магазин" keeps its "в".
    filled = [i for i, piece in enumerate(pieces) if _has_content(piece)]
    if filled:
        tail = filled[-1]
        if tail < last:
            words = pieces[tail].split()
            while words and words[-1].lower() in _DANGLING_PREPOSITIONS:
                words.pop()
            pieces[tail] = " ".join(words)
        head = filled[0]
        if head < last and all(word.lower() in _DANGLING_PREPOSITIONS for word in pieces[head].split()):
            pieces[head] = ""

    title = " ".join(" ".join(pieces).split())
    if spans:
        before = None
        while before != title:  # empty brackets / quotes left behind by a removed token
            before = title
            title = " ".join(_PAIRS_EMPTY.sub(" ", title).split())
        title = re.sub(r"\s*([,;])(?:\s*[,;])+", r"\1", title)  # "a, , b" -> "a, b"
        title = re.sub(r"\s+([,;:.!?])", r"\1", title)  # "a , b" -> "a, b"
        title = _DASH_RUN.sub("", title)  # "a - - b" -> "a - b"
    # Stray separators at the edges; a dot too once something was cut out ("зал, завтра." -> "зал").
    lead, trail = (_LEAD_STRIP_CUT, _TRAIL_STRIP_CUT) if spans else (_LEAD_STRIP, _TRAIL_STRIP)
    return trail.sub("", lead.sub("", title)).strip()


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def _utf16_offsets(text: str) -> list[int]:
    """Indices of astral characters; each one is two UTF-16 code units."""
    return [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]


def _preset_for(rule: str) -> str:
    for key in _CONTRACT_PRESETS:
        if RECURRENCE_PRESETS.get(key) == rule:
            return key
    return "custom"


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
    source = text if isinstance(text, str) else ""
    if not source.strip():
        return ParsedTask(title="")
    today = now.date()
    clock_now = now.time()
    claimed = bytearray(len(source))

    # -- pass 1: sigils ------------------------------------------------------
    _protect_urls_and_emails(source, claimed)
    container_hit = _scan_container(source, containers, claimed)
    if container_hit:
        _claim(claimed, container_hit[0], container_hit[1])
    tag_hits: list[tuple[int, int, str]] = []
    tags: list[str] = []
    for start, end, name in _scan_named(_TAG_RX, source, claimed):
        if name.casefold() not in {tag.casefold() for tag in tags}:
            if len(tags) >= _MAX_TAGS:
                continue  # the 11th distinct tag stays in the title
            tags.append(name)
        _claim(claimed, start, end)  # a repeated "#Work #work" is consumed, listed once
        tag_hits.append((start, end, name))
    context_hits = _scan_named(_CONTEXT_RX, source, claimed)
    context_hit = context_hits[0] if context_hits else None
    if context_hit:
        _claim(claimed, context_hit[0], context_hit[1])
    priority_hit = _scan_priority(source, claimed)
    if priority_hit:
        _claim(claimed, priority_hit[0], priority_hit[1])

    # -- pass 2: temporal candidates -------------------------------------------
    cands: list[_Cand] = []
    for scan in (_scan_reminders, _scan_recurrence, _scan_blocks, _scan_times, _scan_durations):
        cands.extend(scan(source))
    cands.extend(_scan_dates(source, today, week_start))
    chosen = _select([c for c in cands if _is_free(claimed, c.start, c.end)], claimed)

    first: dict[str, _Cand] = {}
    reminders: list[_Cand] = []
    for cand in chosen:
        if cand.kind == "reminder":
            if cand.value not in {r.value for r in reminders} and len(reminders) < _MAX_REMINDERS:
                reminders.append(cand)
        else:
            first.setdefault(cand.kind, cand)
    date_c, block_c = first.get("date"), first.get("block")
    dur_c, rec_c = first.get("duration"), first.get("recurrence")
    time_c = None if block_c else first.get("time")

    # -- assembly -----------------------------------------------------------------
    result = ParsedTask(title="")
    start_time = block_c.value[0] if block_c else (time_c.value if time_c else None)
    due_date: date | None = date_c.value if date_c else None
    if start_time is not None and due_date is None and rec_c is None:
        due_date = today if start_time > clock_now else today + timedelta(days=1)
    result.due_date = due_date
    if block_c:
        result.block_date = due_date
        result.block_start, result.block_end = block_c.value
        if dur_c:
            result.estimate_minutes = dur_c.value
    elif time_c and dur_c:
        result.block_date = due_date
        result.block_start = time_c.value
        result.block_end = (datetime.combine(date.min, time_c.value) + timedelta(minutes=dur_c.value)).time()
    elif time_c:
        result.due_time = time_c.value
    elif dur_c:
        result.estimate_minutes = dur_c.value
    if rec_c:
        result.recurrence_rule = rec_c.value
        result.recurrence_preset = _preset_for(rec_c.value)
    result.reminder_leads = [r.value for r in reminders]
    result.priority = priority_hit[2] if priority_hit else None
    result.context = context_hit[2] if context_hit else None
    result.container = container_hit[2] if container_hit else None
    result.tags = tags

    # -- tokens ---------------------------------------------------------------------
    spans: list[tuple[int, int, str]] = []
    used = [c for c in (date_c, block_c, time_c, dur_c, rec_c, *reminders) if c is not None]
    spans.extend((c.start, c.end, c.kind) for c in used)
    spans.extend((start, end, "tag") for start, end, _ in tag_hits)
    if context_hit:
        spans.append((context_hit[0], context_hit[1], "context"))
    if priority_hit:
        spans.append((priority_hit[0], priority_hit[1], "priority"))
    if container_hit:
        spans.append((container_hit[0], container_hit[1], "project"))
    spans.sort()
    astral = _utf16_offsets(source)

    def u16(index: int) -> int:
        return index + bisect_left(astral, index)

    result.tokens = [Token(u16(s), u16(e), cast(TokenKind, kind), source[s:e]) for s, e, kind in spans]
    title = _build_title(source, [(s, e) for s, e, _ in spans])
    result.title = title or " ".join(source.split())
    return result
