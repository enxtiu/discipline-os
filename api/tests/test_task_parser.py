"""Quick-add natural-language parser (ru + en): grammar, ambiguities, offsets, titles.

Pure unit tests: no database, no network, no clock. ``NOW`` is a Tuesday so that every
weekday expectation below can be checked by counting on a calendar:

    Tue 27 Oct 2026 (today) · Wed 28 · Thu 29 · Fri 30 · Sat 31 · Sun 1 Nov · Mon 2 · Tue 3
"""

from __future__ import annotations

import itertools
import random
import time as clock
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.recurrence import RECURRENCE_PRESETS, parse_rule
from app.services.task_parser import KnownContainer, ParsedTask, Token, parse_task_text


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override of the conftest fixture of the same name.

    These are pure unit tests. The shared fixture truncates every table after each test, which
    costs far more than the ~600 parser cases themselves; this module never touches the database.
    """


TZ = ZoneInfo("Asia/Bangkok")
NOW = datetime(2026, 10, 27, 15, 30, tzinfo=TZ)  # Tuesday


def d(month: int, day: int, year: int = 2026) -> date:
    return date(year, month, day)


TODAY, TOMORROW = d(10, 27), d(10, 28)
WED, THU, FRI, SAT, SUN = d(10, 28), d(10, 29), d(10, 30), d(10, 31), d(11, 1)
MON, TUE = d(11, 2), d(11, 3)


def at(hour: int = 15, minute: int = 30, second: int = 0, *, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=TZ)


def parse(text: str, now: datetime = NOW, week_start: int = 1, containers=()) -> ParsedTask:
    return parse_task_text(text, now=now, week_start=week_start, containers=containers)


def kinds(result: ParsedTask) -> list[str]:
    return [token.kind for token in result.tokens]


def js_slice(text: str, token: Token) -> str:
    """What ``text.slice(start, end)`` returns in JavaScript (UTF-16 code units)."""
    units = text.encode("utf-16-le", errors="surrogatepass")
    return units[token.start * 2 : token.end * 2].decode("utf-16-le", errors="surrogatepass")


def assert_invariants(text: str, result: ParsedTask) -> None:
    """Properties that must hold for any input."""
    units = len(text.encode("utf-16-le", errors="surrogatepass")) // 2
    previous_end = 0
    for token in result.tokens:
        assert 0 <= token.start < token.end <= units, (text, token)
        assert token.start >= previous_end, ("tokens overlap or are unsorted", text, result.tokens)
        assert js_slice(text, token) == token.text, (text, token)
        previous_end = token.end
    assert isinstance(result.title, str)
    assert result.estimate_minutes is None or 1 <= result.estimate_minutes <= 1440, text
    assert all(0 <= lead <= 10080 for lead in result.reminder_leads), text
    assert len(result.tags) <= 10 and len(result.reminder_leads) <= 5, text
    assert result.priority is None or 1 <= result.priority <= 4, text
    if result.block_start is None:
        assert result.block_end is None and result.block_date is None, text
    else:
        assert result.block_end is not None and result.due_time is None, text
    assert (result.recurrence_rule is None) == (result.recurrence_preset is None), text


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        # today / tomorrow / the day after
        ("сегодня", TODAY),
        ("завтра", TOMORROW),
        ("послезавтра", THU),
        ("today", TODAY),
        ("tomorrow", TOMORROW),
        ("tmr", TOMORROW),
        ("tmrw", TOMORROW),
        ("day after tomorrow", THU),
        ("ЗАВТРА", TOMORROW),
        ("Tomorrow", TOMORROW),
        ("на завтра", TOMORROW),
        ("до завтра", TOMORROW),
        ("by tomorrow", TOMORROW),
        ("until tomorrow", TOMORROW),
        ("till friday", FRI),
        ("until friday", FRI),
        ("by May 12", date(2027, 5, 12)),
        # weekdays: always the next such day, strictly after today
        ("пн", MON),
        ("понедельник", MON),
        ("в понедельник", MON),
        ("в следующий понедельник", MON),
        ("во вторник", TUE),
        ("вт", TUE),
        ("в среду", WED),
        ("ср", WED),
        ("в четверг", THU),
        ("чт", THU),
        ("в пятницу", FRI),
        ("пт", FRI),
        ("в субботу", SAT),
        ("сб", SAT),
        ("воскресенье", SUN),
        ("вс", SUN),
        ("до пятницы", FRI),
        ("к пятнице", FRI),
        ("mon", MON),
        ("monday", MON),
        ("on monday", MON),
        ("next monday", MON),
        ("tuesday", TUE),
        ("wed", WED),
        ("thu", THU),
        ("by friday", FRI),
        ("sat", SAT),
        ("Sunday", SUN),
        # relative offsets
        ("через 3 дня", d(10, 30)),
        ("через два дня", THU),
        ("через 1 день", TOMORROW),
        ("in 3 days", d(10, 30)),
        ("in two days", THU),
        ("in a day", TOMORROW),
        ("через неделю", d(11, 3)),
        ("через 2 недели", d(11, 10)),
        ("in a week", d(11, 3)),
        ("in 2 weeks", d(11, 10)),
        ("in three weeks", d(11, 17)),
        ("через месяц", d(11, 27)),
        ("через 2 месяца", d(12, 27)),
        ("in a month", d(11, 27)),
        ("in 2 months", d(12, 27)),
        ("через год", date(2027, 10, 27)),
        ("in a year", date(2027, 10, 27)),
        # weekend / next week (week_start = 1, Monday)
        ("на выходных", SAT),
        ("в выходные", SAT),
        ("this weekend", SAT),
        ("on the weekend", SAT),
        ("на следующей неделе", MON),
        ("next week", MON),
    ],
)
def test_date_phrases_resolve_against_today(phrase: str, expected: date) -> None:
    for text, title in ((f"{phrase} зал", "зал"), (f"{phrase} gym", "gym"), (f"зал {phrase}", "зал")):
        result = parse(text)
        assert result.due_date == expected, text
        assert result.due_time is None and result.block_start is None, text
        assert result.title == title, text
        assert kinds(result) == ["date"], text
        assert [token.text for token in result.tokens] == [phrase], text  # "на завтра", "by friday" are one token
        assert_invariants(text, result)


def test_date_phrase_titles_are_exact() -> None:
    assert parse("завтра зал").title == "зал"
    assert parse("tomorrow gym").title == "gym"
    assert parse("gym on monday").title == "gym"
    assert parse("зал в понедельник").title == "зал"


def test_weekday_never_resolves_to_today() -> None:
    monday = at(day=d(11, 2))
    assert parse("понедельник зал", now=monday).due_date == d(11, 9)
    assert parse("monday gym", now=monday).due_date == d(11, 9)
    assert parse("в пн зал", now=monday).due_date == d(11, 9)
    assert parse("сегодня зал", now=monday).due_date == d(11, 2)
    assert parse("вторник зал", now=monday).due_date == d(11, 3)
    assert parse("вторник зал").due_date == TUE  # today is Tuesday: the next Tuesday


@pytest.mark.parametrize(
    ("week_start", "expected"),
    [(1, MON), (0, SUN), (6, SAT), (2, TUE), (3, WED), (5, d(10, 30)), (4, d(10, 29))],
)
def test_next_week_is_the_first_day_of_next_week(week_start: int, expected: date) -> None:
    for text in ("на следующей неделе зал", "next week gym"):
        assert parse(text, week_start=week_start).due_date == expected, (text, week_start)


def test_next_week_when_today_is_the_first_day_of_the_week() -> None:
    sunday = at(day=SUN)
    assert parse("next week gym", now=sunday, week_start=0).due_date == d(11, 8)
    assert parse("next week gym", now=sunday, week_start=1).due_date == d(11, 2)  # Mon 2 Nov is the next week's start


@pytest.mark.parametrize(
    ("today", "expected"),
    [(TODAY, SAT), (d(10, 31), d(10, 31)), (d(11, 1), d(11, 7)), (d(11, 2), d(11, 7)), (d(10, 30), SAT)],
)
def test_weekend_is_the_coming_saturday(today: date, expected: date) -> None:
    now = at(day=today)
    assert parse("на выходных зал", now=now).due_date == expected
    assert parse("this weekend gym", now=now).due_date == expected


@pytest.mark.parametrize(
    ("text", "now", "expected"),
    [
        # a day-and-month already past this year rolls to next year
        ("12 мая зал", NOW, date(2027, 5, 12)),
        ("12 May gym", NOW, date(2027, 5, 12)),
        ("May 12 gym", NOW, date(2027, 5, 12)),
        ("12.05 зал", NOW, date(2027, 5, 12)),
        # ... and stays in this year while it is ahead
        ("12 мая зал", at(day=d(1, 15)), d(5, 12)),
        ("12.05 зал", at(day=d(1, 15)), d(5, 12)),
        ("5 декабря зал", NOW, d(12, 5)),
        ("Dec 5 gym", NOW, d(12, 5)),
        # today is not past; yesterday is
        ("27.10 зал", NOW, TODAY),
        ("26.10 зал", NOW, date(2027, 10, 26)),
        ("27 октября зал", NOW, TODAY),
        ("26 октября зал", NOW, date(2027, 10, 26)),
        # a full date is taken as written, even in the past
        ("12.05.2027 зал", NOW, date(2027, 5, 12)),
        ("12.05.2026 зал", NOW, d(5, 12)),
        ("12.05.27 зал", NOW, date(2027, 5, 12)),
        ("2027-05-12 gym", NOW, date(2027, 5, 12)),
        ("2026-01-02 gym", NOW, d(1, 2)),
        ("12 мая 2028 зал", NOW, date(2028, 5, 12)),
        ("12 мая 2028 года зал", NOW, date(2028, 5, 12)),
        ("May 12, 2028 gym", NOW, date(2028, 5, 12)),
        ("12th May gym", NOW, date(2027, 5, 12)),
        ("1 янв зал", NOW, date(2027, 1, 1)),
        ("на 12 мая зал", NOW, date(2027, 5, 12)),
        ("до 12 мая зал", NOW, date(2027, 5, 12)),
        ("on May 12 gym", NOW, date(2027, 5, 12)),
        # 29 February waits for the next leap year
        ("29.02 зал", NOW, date(2028, 2, 29)),
        ("29 февраля зал", NOW, date(2028, 2, 29)),
        ("29.02.2028 зал", NOW, date(2028, 2, 29)),
        # month arithmetic clamps to the end of the month
        ("через месяц зал", at(day=d(1, 31)), d(2, 28)),
        ("in a month gym", at(day=date(2028, 1, 31)), date(2028, 2, 29)),
        ("через год зал", at(day=date(2028, 2, 29)), date(2029, 2, 28)),
    ],
)
def test_explicit_dates_and_year_rollover(text: str, now: datetime, expected: date) -> None:
    result = parse(text, now=now)
    assert result.due_date == expected, text
    assert kinds(result) == ["date"], text
    assert result.title in ("зал", "gym"), text


@pytest.mark.parametrize(
    "text",
    [
        "31.04 зал",  # April has 30 days
        "32.01 зал",
        "00.05 зал",
        "12.13 зал",
        "29.02.2027 зал",  # not a leap year
        "2027-02-30 gym",
        "1.5 кг мяса",  # a quantity, not 1 May
        "2.5 литра воды",
        "версия 1.2.3",
        "12.5.2027x",
        "30 февраля зал",
    ],
)
def test_impossible_or_ambiguous_dates_stay_in_the_title(text: str) -> None:
    result = parse(text)
    assert result.due_date is None
    assert result.tokens == []
    assert result.title == text


@pytest.mark.parametrize(
    "text",
    [
        "tomorrow's meeting",
        "today’s report",
        "через день",  # "in a day" or "every other day": stay silent
        "вчера зал",
        "https://example.com/12.05 прочитать",
        "https://example.com/2027-05-12/tomorrow",
        "www.site.ru/p1 открыть",
        "www.example.com/tomorrow читать",
        "mail ivan.12.05@mail.ru",
    ],
)
def test_phrases_that_are_not_dates(text: str) -> None:
    result = parse(text)
    assert result.due_date is None and result.tokens == [], text
    assert result.title == text


# --------------------------------------------------------------------------- #
# Times of day
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("в 18:00", time(18, 0)),
        ("18:00", time(18, 0)),
        ("в 18", time(18, 0)),
        ("в 9:05", time(9, 5)),
        ("в 09:05", time(9, 5)),
        ("в 0:30", time(0, 30)),
        ("в 23:59", time(23, 59)),
        ("в 18 часов", time(18, 0)),
        ("в 2 часа", time(2, 0)),
        ("в 9 утра", time(9, 0)),
        ("в 9:30 утра", time(9, 30)),
        ("9 утра", time(9, 0)),
        ("в 12 утра", time(0, 0)),
        ("в 7 вечера", time(19, 0)),
        ("в 7:15 вечера", time(19, 15)),
        ("7 вечера", time(19, 0)),
        ("в 11 вечера", time(23, 0)),
        ("в 3 дня", time(15, 0)),
        ("в 12 дня", time(12, 0)),
        ("в 1 час дня", time(13, 0)),
        ("в 12 ночи", time(0, 0)),
        ("в 1 ночи", time(1, 0)),
        ("в 11 ночи", time(23, 0)),
        ("в полдень", time(12, 0)),
        ("в полночь", time(0, 0)),
        ("at 6pm", time(18, 0)),
        ("at 6 pm", time(18, 0)),
        ("6:30pm", time(18, 30)),
        ("6pm", time(18, 0)),
        ("at 6:30 PM", time(18, 30)),
        ("at 6 p.m.", time(18, 0)),
        ("at 9am", time(9, 0)),
        ("at 12am", time(0, 0)),
        ("at 12pm", time(12, 0)),
        ("at 12:30am", time(0, 30)),
        ("at 18", time(18, 0)),
        ("at 18:45", time(18, 45)),
        ("at noon", time(12, 0)),
        ("at midnight", time(0, 0)),
        ("до 18:00", time(18, 0)),
        ("until 6pm", time(18, 0)),
        ("till 18:30", time(18, 30)),
        ("к 18:00", time(18, 0)),
        ("by 6pm", time(18, 0)),
        ("в 18 ч", time(18, 0)),
    ],
)
def test_time_forms(phrase: str, expected: time) -> None:
    for text in (f"{phrase} созвон", f"созвон {phrase}"):
        result = parse(text)
        assert result.due_time == expected, text
        assert result.block_start is None and result.estimate_minutes is None, text
        assert result.title == "созвон", text
        assert kinds(result) == ["time"], text
        assert [token.text for token in result.tokens] == [phrase], text  # "до 18:00", "at 6pm" are one token
        assert_invariants(text, result)
    explicit = parse(f"{phrase} завтра созвон")
    assert explicit.due_date == TOMORROW
    assert explicit.due_time == expected
    assert explicit.title == "созвон"
    assert kinds(explicit) == ["time", "date"]


@pytest.mark.parametrize(
    "text",
    [
        "25:00 зал",
        "12:60 зал",
        "24:00 зал",
        "в 25 зал",
        "в 24 зал",
        "at 25 gym",
        "at 13pm gym",
        "at 0am gym",
        "18:00pm gym",
        "в 18:00:30 зал",
        "в 18.30 зал",
        "10:30:45 лог",
        "купить 18 яблок",
        "зал 18",
        "порядок 5:1",
        "курс 3.12:30 зал",  # the tail of a decimal number
        "a/12:30 зал",
    ],
)
def test_invalid_or_bare_numbers_are_not_times(text: str) -> None:
    result = parse(text)
    assert result.due_time is None and result.block_start is None, text
    assert result.title == text


@pytest.mark.parametrize(
    ("now", "text", "expected_day"),
    [
        (at(15, 29, 59), "в 15:30 зал", TODAY),  # a second ahead: still today
        (at(15, 30, 0), "в 15:30 зал", TOMORROW),  # equal is not ahead
        (at(15, 30, 1), "в 15:30 зал", TOMORROW),
        (at(15, 30), "в 15:31 зал", TODAY),
        (at(15, 30), "в 15:29 зал", TOMORROW),
        (at(0, 0), "в 0:01 зал", TODAY),
        (at(23, 59), "в 0:00 зал", TOMORROW),
        (at(23, 59), "в 23:59 зал", TOMORROW),
        (at(6, 0), "в 9 утра зал", TODAY),
        (at(10, 0), "в 9 утра зал", TOMORROW),
        (at(10, 0), "at 6pm gym", TODAY),
        (at(19, 0), "at 6pm gym", TOMORROW),
    ],
)
def test_time_without_a_date_means_today_if_ahead_else_tomorrow(now: datetime, text: str, expected_day: date) -> None:
    result = parse(text, now=now)
    base = now.date()
    assert result.due_date == base + (expected_day - TODAY), (text, now)
    assert result.due_time is not None


def test_an_explicit_date_is_never_moved_by_the_time_rule() -> None:
    result = parse("сегодня в 9 зал")  # 09:00 is already past, but the user said today
    assert result.due_date == TODAY and result.due_time == time(9, 0)
    result = parse("завтра в 9 зал")
    assert result.due_date == TOMORROW


def test_in_3_days_and_at_3_pm_are_different_things() -> None:
    assert parse("через 3 дня зал").due_date == d(10, 30)
    assert parse("через 3 дня зал").due_time is None
    afternoon = parse("в 3 дня зал")
    assert afternoon.due_time == time(15, 0)
    assert afternoon.due_date == TOMORROW  # 15:00 is already behind 15:30
    both = parse("через 3 дня в 3 дня зал")
    assert both.due_date == d(10, 30) and both.due_time == time(15, 0)
    assert kinds(both) == ["date", "time"]
    assert parse("каждые 3 дня зал").due_time is None
    assert parse("на 3 дня зал").tokens == []


@pytest.mark.parametrize(
    "text",
    [
        "раз в 2 часа пить воду",
        "положить на 3 дня",
        "через час позвонить",
    ],
)
def test_phrases_that_look_like_times_but_are_not(text: str) -> None:
    result = parse(text)
    assert result.due_time is None, text


@pytest.mark.parametrize(
    "text",
    [
        "пн-пт зарядка",
        "пн\u2013пт зарядка",
        "каждый пн-пт зарядка",
        "mon-fri gym",
        "Mon\u2013Fri gym",
        "завтра-послезавтра зал",
        "12.05-14.05 отпуск",
        "12 мая-14 мая отпуск",
        "30м-1ч зал",
        "5-10 мин зал",
        "e-mail-завтра",
    ],
)
def test_words_glued_by_a_hyphen_are_a_range_or_a_compound_not_a_token(text: str) -> None:
    result = parse(text)
    assert result.tokens == [], text
    assert result.title == text


# --------------------------------------------------------------------------- #
# Blocks (time ranges, time + duration) and estimates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("phrase", "start", "end"),
    [
        ("18:00-19:30", time(18, 0), time(19, 30)),
        ("18:00 - 19:30", time(18, 0), time(19, 30)),
        ("18:00–19:30", time(18, 0), time(19, 30)),  # en dash
        ("18:00—19:30", time(18, 0), time(19, 30)),  # em dash
        ("18:00 до 19:30", time(18, 0), time(19, 30)),
        ("в 18:00-19:30", time(18, 0), time(19, 30)),
        ("с 18 до 19:30", time(18, 0), time(19, 30)),
        ("с 18:00 до 19:30", time(18, 0), time(19, 30)),
        ("с 9 до 11", time(9, 0), time(11, 0)),
        ("в 10-12", time(10, 0), time(12, 0)),
        ("с 18 по 20", time(18, 0), time(20, 0)),
        ("с 7 до 9 вечера", time(19, 0), time(21, 0)),
        ("с 9 утра до 12 дня", time(9, 0), time(12, 0)),
        ("с 10 до 2 дня", time(10, 0), time(14, 0)),
        ("с 9 до 5 вечера", time(9, 0), time(17, 0)),
        ("from 6pm to 7:30pm", time(18, 0), time(19, 30)),
        ("from 6 to 7:30pm", time(18, 0), time(19, 30)),
        ("from 18:00 to 19:30", time(18, 0), time(19, 30)),
        ("6pm-7:30pm", time(18, 0), time(19, 30)),
        ("6-7pm", time(18, 0), time(19, 0)),
        ("9am-5pm", time(9, 0), time(17, 0)),
        ("9-5pm", time(9, 0), time(17, 0)),
        ("11-1pm", time(11, 0), time(13, 0)),
        ("at 6pm-7pm", time(18, 0), time(19, 0)),
        ("12pm-1pm", time(12, 0), time(13, 0)),
        # the end (or the start) without a marker borrows the other end's am/pm
        ("from 6pm to 7", time(18, 0), time(19, 0)),
        ("с 6 вечера до 8", time(18, 0), time(20, 0)),
        ("9am-5", time(9, 0), time(17, 0)),
        ("6pm-8", time(18, 0), time(20, 0)),
    ],
)
def test_time_ranges_become_blocks(phrase: str, start: time, end: time) -> None:
    text = f"{phrase} зал"
    result = parse(text)
    assert (result.block_start, result.block_end) == (start, end), text
    assert result.due_time is None, "a block replaces due_time"
    assert result.title == "зал", text
    assert kinds(result) == ["block"], text
    assert [token.text for token in result.tokens] == [phrase], text
    assert result.block_date == result.due_date and result.due_date is not None
    assert_invariants(text, result)


@pytest.mark.parametrize(
    ("phrase", "start", "end"),
    [
        ("22:00-01:00", time(22, 0), time(1, 0)),
        ("с 22 до 24", time(22, 0), time(0, 0)),
        ("с 23:30 до 0:30", time(23, 30), time(0, 30)),
        ("с 11 вечера до 1 ночи", time(23, 0), time(1, 0)),
        ("from 11pm to 1am", time(23, 0), time(1, 0)),
        ("11pm-1", time(23, 0), time(1, 0)),  # "1" borrows pm (13:00) only if that is the shorter block
    ],
)
def test_a_block_that_ends_before_it_starts_runs_into_the_next_day(phrase: str, start: time, end: time) -> None:
    result = parse(f"{phrase} сон")
    assert (result.block_start, result.block_end) == (start, end)
    assert result.block_end <= result.block_start  # the consumer reads this as "ends the next day"
    assert result.title == "сон"


def test_blocks_land_on_the_explicit_date_or_the_implied_day() -> None:
    explicit = parse("завтра с 18 до 19:30 зал")
    assert explicit.block_date == TOMORROW and explicit.due_date == TOMORROW
    assert kinds(explicit) == ["date", "block"]
    later_today = parse("18:00-19:30 зал")  # 18:00 is ahead of 15:30
    assert later_today.block_date == TODAY and later_today.due_date == TODAY
    already_past = parse("10:00-11:00 зал")
    assert already_past.block_date == TOMORROW
    weekday = parse("в пятницу с 10 до 12 зал")
    assert weekday.block_date == FRI and weekday.due_date == FRI
    assert (weekday.block_start, weekday.block_end) == (time(10, 0), time(12, 0))


@pytest.mark.parametrize(
    ("text", "start", "minutes"),
    [
        ("в 18 на 1.5 ч зал", time(18, 0), 90),
        ("в 18 на 1,5 часа зал", time(18, 0), 90),
        ("at 6pm for 45 min gym", time(18, 0), 45),
        ("at 6pm for an hour gym", time(18, 0), 60),
        ("at 6pm for half an hour gym", time(18, 0), 30),
        ("6pm for 2h gym", time(18, 0), 120),
        ("18:00 1ч зал", time(18, 0), 60),
        ("18:00 30м зал", time(18, 0), 30),
        ("зал 18:00 полчаса", time(18, 0), 30),
        ("в 9 утра на 2 часа зал", time(9, 0), 120),
        ("завтра в 19:30 на 1h30m зал", time(19, 30), 90),
    ],
)
def test_time_plus_duration_is_a_block(text: str, start: time, minutes: int) -> None:
    result = parse(text)
    assert result.block_start == start, text
    end = datetime.combine(date.min, start) + timedelta(minutes=minutes)
    assert result.block_end == end.time(), text
    assert result.due_time is None and result.estimate_minutes is None, text
    assert {"time", "duration"} <= set(kinds(result)), text
    assert result.title in ("зал", "gym"), text


def test_a_block_that_crosses_midnight_via_duration() -> None:
    result = parse("в 23:00 на 2 часа сон")
    assert result.block_start == time(23, 0)
    assert result.block_end == time(1, 0)
    assert parse("в 18:00 на 24 часа").block_end == time(18, 0)  # exactly 24 h: end == start


@pytest.mark.parametrize(
    ("phrase", "minutes"),
    [
        ("30м", 30),
        ("30 мин", 30),
        ("30 минут", 30),
        ("1 минуту", 1),
        ("1ч", 60),
        ("1 ч", 60),
        ("1.5 ч", 90),
        ("1,5 часа", 90),
        ("2 часа", 120),
        ("5 часов", 300),
        ("0.5ч", 30),
        ("45m", 45),
        ("45 min", 45),
        ("45 minutes", 45),
        ("2h", 120),
        ("2 hours", 120),
        ("1.5h", 90),
        ("1h30m", 90),
        ("1h 30m", 90),
        ("1h30", 90),
        ("1ч30м", 90),
        ("1 час 30 минут", 90),
        ("2 hours 15 minutes", 135),
        ("на 20 минут", 20),
        ("на 2 часа", 120),
        ("for 20 min", 20),
        ("for 2 hours", 120),
        ("полчаса", 30),
        ("на полчаса", 30),
        ("полтора часа", 90),
        ("на полтора часа", 90),
        ("на час", 60),
        ("один час", 60),
        ("одну минуту", 1),
        ("два часа", 120),
        ("две минуты", 2),
        ("пять минут", 5),
        ("десять минут", 10),
        ("двадцать минут", 20),
        ("one hour", 60),
        ("an hour", 60),
        ("a minute", 1),
        ("two hours", 120),
        ("ten minutes", 10),
        ("half an hour", 30),
        ("half hour", 30),
        ("1440 мин", 1440),
        ("24h", 1440),
    ],
)
def test_a_duration_alone_is_an_estimate(phrase: str, minutes: int) -> None:
    for text, title in ((f"{phrase} зал", "зал"), (f"зал {phrase}", "зал"), (f"завтра зал {phrase}", "зал")):
        result = parse(text)
        assert result.estimate_minutes == minutes, text
        assert result.due_time is None and result.block_start is None, text
        assert result.title == title, text
        assert kinds(result).count("duration") == 1, text
        assert [token.text for token in result.tokens if token.kind == "duration"] == [phrase], text
        assert_invariants(text, result)


@pytest.mark.parametrize(
    "text",
    [
        "25h gym",  # over 24 h
        "1441 мин зал",
        "0m gym",
        "0 мин зал",
        "5mb файл",
        "10 м кабеля",  # a spaced one-letter unit is metres
        "купить 2 кг мяса",
        "через 30 мин позвонить",
        "через 2 часа позвонить",
        "in 30 minutes call",
        "за 30 мин сделать",
        "каждые 2 часа пить воду",
        "every 2 hours drink",
        "через полчаса позвонить",
        "после 30 минут",
        "час пик",
        "час назад",
        "an hour ago",
        "классный час",
        "в 30 минут уложиться",
        "1,5м зал",  # 1.5 m, not 5 minutes
        "2.5м зал",
    ],
)
def test_phrases_that_are_not_estimates(text: str) -> None:
    result = parse(text)
    assert result.estimate_minutes is None and result.block_start is None, text
    assert "duration" not in kinds(result), text


def test_a_number_before_a_deadline_time_is_not_a_range() -> None:
    result = parse("купить 2 до 18:00")
    assert result.block_start is None
    assert result.due_time == time(18, 0) and result.title == "купить 2"
    assert [token.text for token in result.tokens] == ["до 18:00"]
    result = parse("Do 3 until 6pm")
    assert result.block_start is None and result.due_time == time(18, 0) and result.title == "Do 3"
    assert [token.text for token in result.tokens] == ["until 6pm"]


def test_a_range_plus_a_duration_keeps_the_duration_as_the_estimate() -> None:
    result = parse("18:00-20:00 на 1.5 ч зал")
    assert (result.block_start, result.block_end) == (time(18, 0), time(20, 0))
    assert result.estimate_minutes == 90
    assert kinds(result) == ["block", "duration"]


def test_dates_and_estimates_combine() -> None:
    result = parse("завтра зал 1ч")
    assert result.due_date == TOMORROW and result.estimate_minutes == 60 and result.due_time is None
    assert result.title == "зал"


# --------------------------------------------------------------------------- #
# Recurrence
# --------------------------------------------------------------------------- #
RECURRENCE_CASES = [
    # daily
    ("каждый день", "FREQ=DAILY", "daily"),
    ("ежедневно", "FREQ=DAILY", "daily"),
    ("every day", "FREQ=DAILY", "daily"),
    ("daily", "FREQ=DAILY", "daily"),
    ("everyday", "FREQ=DAILY", "daily"),
    ("раз в день", "FREQ=DAILY", "daily"),
    # weekdays / weekend
    ("по будням", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"),
    ("в будни", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"),
    ("каждый будний день", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"),
    ("every weekday", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"),
    ("weekdays", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"),
    ("каждый пн, вт, ср, чт, пт", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"),
    ("по выходным", "FREQ=WEEKLY;BYDAY=SA,SU", "custom"),
    ("каждые выходные", "FREQ=WEEKLY;BYDAY=SA,SU", "custom"),
    ("every weekend", "FREQ=WEEKLY;BYDAY=SA,SU", "custom"),
    ("weekends", "FREQ=WEEKLY;BYDAY=SA,SU", "custom"),
    # weekly
    ("каждую неделю", "FREQ=WEEKLY", "weekly"),
    ("каждая неделя", "FREQ=WEEKLY", "weekly"),
    ("еженедельно", "FREQ=WEEKLY", "weekly"),
    ("every week", "FREQ=WEEKLY", "weekly"),
    ("weekly", "FREQ=WEEKLY", "weekly"),
    ("раз в неделю", "FREQ=WEEKLY", "weekly"),
    # named weekdays
    ("каждый понедельник", "FREQ=WEEKLY;BYDAY=MO", "custom"),
    ("каждый вторник", "FREQ=WEEKLY;BYDAY=TU", "custom"),
    ("каждую среду", "FREQ=WEEKLY;BYDAY=WE", "custom"),
    ("каждый четверг", "FREQ=WEEKLY;BYDAY=TH", "custom"),
    ("каждую пятницу", "FREQ=WEEKLY;BYDAY=FR", "custom"),
    ("каждую субботу", "FREQ=WEEKLY;BYDAY=SA", "custom"),
    ("каждое воскресенье", "FREQ=WEEKLY;BYDAY=SU", "custom"),
    ("каждая пятница", "FREQ=WEEKLY;BYDAY=FR", "custom"),
    ("каждый пн", "FREQ=WEEKLY;BYDAY=MO", "custom"),
    ("каждые пн и чт", "FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
    ("каждые чт и пн", "FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
    ("каждый понедельник и четверг", "FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
    ("каждые пн, ср и пт", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "custom"),
    ("каждый понедельник, среду и пятницу", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "custom"),
    ("каждые пн/чт", "FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
    ("по вторникам и четвергам", "FREQ=WEEKLY;BYDAY=TU,TH", "custom"),
    ("по пн, ср", "FREQ=WEEKLY;BYDAY=MO,WE", "custom"),
    ("every monday", "FREQ=WEEKLY;BYDAY=MO", "custom"),
    ("every Monday", "FREQ=WEEKLY;BYDAY=MO", "custom"),
    ("each friday", "FREQ=WEEKLY;BYDAY=FR", "custom"),
    ("every mon, thu", "FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
    ("every mon and thu", "FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
    ("every tuesday and thursday", "FREQ=WEEKLY;BYDAY=TU,TH", "custom"),
    ("every Monday, Wednesday and Friday", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "custom"),
    ("every Monday, Wednesday, and Friday", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "custom"),
    ("mondays and thursdays", "FREQ=WEEKLY;BYDAY=MO,TH", "custom"),
    ("on fridays", "FREQ=WEEKLY;BYDAY=FR", "custom"),
    ("every other monday", "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO", "custom"),
    ("каждый второй понедельник", "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO", "custom"),
    # every N weeks
    ("каждые 2 недели", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("каждые две недели", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("каждую вторую неделю", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("раз в две недели", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("every 2 weeks", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("every two weeks", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("every other week", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("every second week", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("biweekly", "FREQ=WEEKLY;INTERVAL=2", "biweekly"),
    ("каждые 3 недели", "FREQ=WEEKLY;INTERVAL=3", "custom"),
    # monthly
    ("каждый месяц", "FREQ=MONTHLY", "monthly"),
    ("ежемесячно", "FREQ=MONTHLY", "monthly"),
    ("раз в месяц", "FREQ=MONTHLY", "monthly"),
    ("every month", "FREQ=MONTHLY", "monthly"),
    ("monthly", "FREQ=MONTHLY", "monthly"),
    ("каждые 2 месяца", "FREQ=MONTHLY;INTERVAL=2", "custom"),
    ("каждое 15 число", "FREQ=MONTHLY;BYMONTHDAY=15", "custom"),
    ("каждое 15-е число", "FREQ=MONTHLY;BYMONTHDAY=15", "custom"),
    ("каждого 15 числа", "FREQ=MONTHLY;BYMONTHDAY=15", "custom"),
    ("15 числа каждого месяца", "FREQ=MONTHLY;BYMONTHDAY=15", "custom"),
    ("каждое 1 число", "FREQ=MONTHLY;BYMONTHDAY=1", "custom"),  # monthly_first is not a contract preset
    ("каждое 31 число", "FREQ=MONTHLY;BYMONTHDAY=31", "custom"),
    ("every 15th", "FREQ=MONTHLY;BYMONTHDAY=15", "custom"),
    ("every 1st", "FREQ=MONTHLY;BYMONTHDAY=1", "custom"),
    ("every 2nd of the month", "FREQ=MONTHLY;BYMONTHDAY=2", "custom"),
    ("every 3rd day of every month", "FREQ=MONTHLY;BYMONTHDAY=3", "custom"),
    ("on the 21st of every month", "FREQ=MONTHLY;BYMONTHDAY=21", "custom"),
    # yearly
    ("каждый год", "FREQ=YEARLY", "yearly"),
    ("ежегодно", "FREQ=YEARLY", "yearly"),
    ("every year", "FREQ=YEARLY", "yearly"),
    ("yearly", "FREQ=YEARLY", "yearly"),
    ("annually", "FREQ=YEARLY", "yearly"),
    # every N days
    ("каждые 3 дня", "FREQ=DAILY;INTERVAL=3", "custom"),
    ("каждые три дня", "FREQ=DAILY;INTERVAL=3", "custom"),
    ("каждые 10 дней", "FREQ=DAILY;INTERVAL=10", "custom"),
    ("каждый второй день", "FREQ=DAILY;INTERVAL=2", "custom"),
    ("every 3 days", "FREQ=DAILY;INTERVAL=3", "custom"),
    ("every three days", "FREQ=DAILY;INTERVAL=3", "custom"),
    ("every other day", "FREQ=DAILY;INTERVAL=2", "custom"),
]


@pytest.mark.parametrize(("phrase", "rule", "preset"), RECURRENCE_CASES)
def test_recurrence_forms(phrase: str, rule: str, preset: str) -> None:
    for text in (f"{phrase} зал", f"зал {phrase}", f"{phrase.upper()} зал"):
        result = parse(text)
        assert result.recurrence_rule == rule, text
        assert result.recurrence_preset == preset, text
        assert result.title == "зал", text
        assert kinds(result) == ["recurrence"], text
        assert [token.text.lower() for token in result.tokens] == [phrase.lower()], text
        assert result.due_date is None, "a recurrence does not choose the first date, the service does"
        assert_invariants(text, result)


def test_every_recurrence_rule_is_accepted_by_the_recurrence_service() -> None:
    anchor = datetime(2026, 10, 28, 9, 0)  # naive local, like the service passes it
    for phrase, rule, _ in RECURRENCE_CASES:
        assert parse(phrase).recurrence_rule == rule
        assert "DTSTART" not in rule and "COUNT" not in rule
        occurrences = list(itertools.islice(parse_rule(rule, anchor), 4))
        assert len(occurrences) == 4, rule


def test_recurrence_rules_expand_to_the_expected_days() -> None:
    anchor = datetime(2026, 10, 26, 9, 0)  # a Monday
    monthday = list(itertools.islice(parse_rule(parse("каждое 15 число").recurrence_rule, anchor), 3))
    assert [x.day for x in monthday] == [15, 15, 15]
    biweekly = list(itertools.islice(parse_rule(parse("every other week").recurrence_rule, anchor), 3))
    assert [(b - a).days for a, b in itertools.pairwise(biweekly)] == [14, 14]
    mon_thu = list(itertools.islice(parse_rule(parse("каждые пн и чт").recurrence_rule, anchor), 4))
    assert [x.weekday() for x in mon_thu] == [0, 3, 0, 3]
    every_three = list(itertools.islice(parse_rule(parse("каждые 3 дня").recurrence_rule, anchor), 3))
    assert [(b - a).days for a, b in itertools.pairwise(every_three)] == [3, 3]


def test_presets_match_the_recurrence_service_tables() -> None:
    contract = ("daily", "weekdays", "weekly", "biweekly", "monthly", "yearly")
    for key in contract:
        phrase_rule = next(rule for _, rule, preset in RECURRENCE_CASES if preset == key)
        assert phrase_rule == RECURRENCE_PRESETS[key], key
    # every non-preset rule is "custom", whatever the service tables say
    custom_rules = {rule for _, rule, preset in RECURRENCE_CASES if preset == "custom"}
    assert custom_rules.isdisjoint({RECURRENCE_PRESETS[key] for key in contract})
    # the service also knows "monthly_first"; the API enumeration does not, so the parser says "custom"
    assert RECURRENCE_PRESETS["monthly_first"] == "FREQ=MONTHLY;BYMONTHDAY=1"
    assert parse("каждое 1 число").recurrence_preset == "custom"


def test_recurrence_never_sets_the_date_but_an_explicit_time_still_applies() -> None:
    result = parse("каждый понедельник в 9 зал")
    assert result.recurrence_rule == "FREQ=WEEKLY;BYDAY=MO"
    assert result.due_time == time(9, 0)
    assert result.due_date is None  # the service picks the first Monday
    assert kinds(result) == ["recurrence", "time"]
    assert result.title == "зал"
    daily = parse("every day at 6pm gym")
    assert daily.due_time == time(18, 0) and daily.due_date is None
    block = parse("каждую пятницу 18:00-19:30 зал")
    assert (block.block_start, block.block_end) == (time(18, 0), time(19, 30))
    assert block.block_date is None and block.due_date is None


def test_recurrence_with_an_explicit_date_keeps_both() -> None:
    result = parse("каждый понедельник зал завтра")
    assert result.recurrence_rule == "FREQ=WEEKLY;BYDAY=MO"
    assert result.due_date == TOMORROW and result.title == "зал"
    other = parse("every day gym tomorrow")
    assert other.recurrence_rule == "FREQ=DAILY" and other.due_date == TOMORROW


def test_a_weekday_inside_a_recurrence_is_not_also_a_date() -> None:
    result = parse("каждый понедельник зал")
    assert result.due_date is None
    assert parse("every monday gym").due_date is None
    assert parse("в понедельник зал").recurrence_rule is None


@pytest.mark.parametrize(
    "text",
    [
        "ежемесячный отчёт",  # an adjective: a one-off report about months stays a one-off
        "ежедневный отчёт",
        "каждый вечер зал",
        "каждый раз",
        "раз в 2 часа зал",
        "every other thing",
        "каждое 32 число",
        "каждые 0 дней",
        "every 1000 days",
    ],
)
def test_phrases_that_are_not_recurrences(text: str) -> None:
    assert parse(text).recurrence_rule is None, text


def test_the_first_recurrence_wins_and_the_second_stays_in_the_title() -> None:
    result = parse("каждый день зал еженедельно")
    assert result.recurrence_rule == "FREQ=DAILY"
    assert result.title == "зал еженедельно"


# --------------------------------------------------------------------------- #
# Priority, tags, context
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("token", "priority"),
    [
        ("p1", 1),
        ("p2", 2),
        ("p3", 3),
        ("p4", 4),
        ("P1", 1),
        ("P3", 3),
        ("!1", 1),
        ("!2", 2),
        ("!3", 3),
        ("!4", 4),
        ("р" + "1", 1),  # Cyrillic er typed on a Russian keyboard
        ("Р" + "2", 2),
        ("р" + "4", 4),
    ],
)
def test_priority_forms(token: str, priority: int) -> None:
    for text in (
        f"зал {token}",
        f"{token} зал",
        f"зал {token} вечером",
        f"зал {token}.",
        f"зал {token},",
        f"({token}) зал",
    ):
        result = parse(text)
        assert result.priority == priority, text
        assert kinds(result) == ["priority"], text
        assert result.tokens[0].text == token
        assert result.title.startswith("зал"), text
        assert_invariants(text, result)


@pytest.mark.parametrize(
    "text",
    [
        "mp3 плеер",
        "mp4 конвертер",
        "p5 зал",
        "p0 зал",
        "зал p12",
        "зал p",
        "зал !5",
        "Wow!1",
        "zip_p1",
        "x-p1",
        "a.p1",
        "iphone15p2",
        "p1.5 версия",
        "С1 зал",  # Cyrillic С1 (not р)
        "1p зал",
    ],
)
def test_priority_must_stand_alone(text: str) -> None:
    result = parse(text)
    assert result.priority is None, text
    assert "priority" not in kinds(result)


def test_only_the_first_priority_counts() -> None:
    result = parse("зал p1 p3")
    assert result.priority == 1
    assert result.title == "зал p3"
    assert kinds(result) == ["priority"]


@pytest.mark.parametrize(
    ("text", "tags", "title"),
    [
        ("зал #здоровье", ["здоровье"], "зал"),
        ("#здоровье зал", ["здоровье"], "зал"),
        ("зал #deep-work", ["deep-work"], "зал"),
        ("зал #a_b_1", ["a_b_1"], "зал"),
        ("зал #2024plan", ["2024plan"], "зал"),
        ("зал #Работа", ["Работа"], "зал"),  # the spelling is kept
        ("зал #Home #work", ["Home", "work"], "зал"),
        ("зал #дом, купить", ["дом"], "зал, купить"),
        ("зал #дом.", ["дом"], "зал"),
        ("зал #дом!", ["дом"], "зал!"),
        ("зал #работа- сегодня", ["работа"], "зал"),
        ("#a #b #c зал", ["a", "b", "c"], "зал"),
        ("зал #Work #work #WORK", ["Work"], "зал"),  # repeated tags are consumed, listed once
        ("зал #日本語", ["日本語"], "зал"),
    ],
)
def test_tags(text: str, tags: list[str], title: str) -> None:
    result = parse(text)
    assert result.tags == tags, text
    assert result.title == title, text
    assert set(kinds(result)) >= {"tag"}
    assert_invariants(text, result)


@pytest.mark.parametrize(
    "text",
    [
        "зал #1",
        "зал #123",
        "зал #-",
        "зал #_",
        "зал #",
        "зал a#b",
        "c# программирование",
        "зал#дом",
        "зал #tag@x",
        "зал #tag/x",
        "зал (#дом)",
        "зал http://x.y/#frag",
    ],
)
def test_things_that_are_not_tags(text: str) -> None:
    result = parse(text)
    assert result.tags == [], text
    assert "tag" not in kinds(result)


def test_tags_are_capped_at_ten_and_the_rest_stays_in_the_title() -> None:
    text = "зал " + " ".join(f"#t{i}" for i in range(12))
    result = parse(text)
    assert result.tags == [f"t{i}" for i in range(10)]
    assert result.title == "зал #t10 #t11"
    assert kinds(result) == ["tag"] * 10


def test_a_tag_longer_than_64_chars_is_not_a_tag() -> None:
    assert parse("зал #" + "я" * 64).tags == ["я" * 64]
    assert parse("зал #" + "я" * 65).tags == []


@pytest.mark.parametrize(
    ("text", "context", "title"),
    [
        ("зал @дом", "дом", "зал"),
        ("зал @phone", "phone", "зал"),
        ("@office зал", "office", "зал"),
        ("зал @home-office", "home-office", "зал"),
        ("зал @Home", "Home", "зал"),
        ("зал @дом, потом", "дом", "зал, потом"),
        ("зал @home @work", "home", "зал @work"),  # one context per task: the second stays
    ],
)
def test_context(text: str, context: str, title: str) -> None:
    result = parse(text)
    assert result.context == context, text
    assert result.title == title, text
    assert kinds(result)[0] == "context" or "context" in kinds(result)


@pytest.mark.parametrize(
    "text",
    [
        "напиши ivan@mail.ru письмо",
        "ivan@mail.ru",
        "зал a@b",
        "зал @1",
        "зал @",
        "зал @-",
        "зал@дом",
    ],
)
def test_emails_and_junk_are_not_contexts(text: str) -> None:
    result = parse(text)
    assert result.context is None, text
    assert "context" not in kinds(result)


# --------------------------------------------------------------------------- #
# Projects and areas (+Name)
# --------------------------------------------------------------------------- #
P_KITCHEN = KnownContainer("p-kitchen", "Новая кухня", "project")
P_NEW = KnownContainer("p-new", "Новая", "project")
P_REPAIR = KnownContainer("p-repair", "Ремонт", "project")
A_REPAIR = KnownContainer("a-repair", "Ремонт", "area")
A_REPAIR_FLAT = KnownContainer("a-flat", "Ремонт квартиры", "area")
A_HEALTH = KnownContainer("a-health", "Здоровье", "area")
A_MINE = KnownContainer("a-mine", "Мой проект", "area")
P_TREE = KnownContainer("p-tree", "Ёлка", "project")
CONTAINERS = [A_REPAIR, A_HEALTH, P_NEW, P_REPAIR, A_MINE, P_KITCHEN, P_TREE, A_REPAIR_FLAT]


@pytest.mark.parametrize(
    ("text", "expected", "title"),
    [
        ("зал +Ремонт", P_REPAIR, "зал"),
        ("+Ремонт зал", P_REPAIR, "зал"),
        ("зал +ремонт", P_REPAIR, "зал"),  # case-insensitive
        ("зал +РЕМОНТ", P_REPAIR, "зал"),
        ("зал +Здоровье", A_HEALTH, "зал"),  # an area, when no project has the name
        ("+Новая кухня ремонт", P_KITCHEN, "ремонт"),  # the longest name wins
        ("+новая кухня", P_KITCHEN, "+новая кухня"),  # nothing else left: the title is the whole text
        ("+Новая ремонт кухня", P_NEW, "ремонт кухня"),
        ("+Новая  кухня зал", P_KITCHEN, "зал"),  # spaces inside the name are flexible
        ("+[Мой проект] зал", A_MINE, "зал"),
        ("зал +[мой   проект]", A_MINE, "зал"),
        ("+[Новая кухня]", P_KITCHEN, "+[Новая кухня]"),
        ("зал +Ремонт, потом", P_REPAIR, "зал, потом"),
        ("зал +Ремонт.", P_REPAIR, "зал"),
        ("зал +елка", P_TREE, "зал"),  # е = ё
        ("зал +Ёлка", P_TREE, "зал"),
        # projects before areas, even when the area's name is longer
        ("+Ремонт квартиры зал", P_REPAIR, "квартиры зал"),
        ("зал +Ремонт +Здоровье", P_REPAIR, "зал +Здоровье"),  # one container per task
        ("зал +Неизвестный +Здоровье", A_HEALTH, "зал +Неизвестный"),
    ],
)
def test_project_and_area_matching(text: str, expected: KnownContainer, title: str) -> None:
    result = parse(text, containers=CONTAINERS)
    assert result.container == expected, text
    assert result.title == title, text
    assert "project" in kinds(result)
    assert_invariants(text, result)


@pytest.mark.parametrize(
    "text",
    [
        "зал +Неизвестный",
        "зал +[Нет такого]",
        "зал +Ремонтик",  # a longer word is not the project
        "зал +Ремонт-2",
        "зал +",
        "зал + Ремонт",
        "c++ зал",
        "зал+Ремонт",
        "2+2 зал",
        "зал +[Ремонт",
    ],
)
def test_unknown_names_stay_in_the_title(text: str) -> None:
    result = parse(text, containers=CONTAINERS)
    assert result.container is None, text
    assert "project" not in kinds(result)
    assert result.title == text


def test_a_plus_after_a_number_with_a_space_is_still_a_container() -> None:
    result = parse("1 +Ремонт", containers=CONTAINERS)
    assert result.container == P_REPAIR and result.title == "1"


def test_no_containers_means_no_project_tokens() -> None:
    result = parse("зал +Ремонт")
    assert result.container is None and result.tokens == []
    assert result.title == "зал +Ремонт"


def test_container_names_may_contain_things_that_look_like_tokens() -> None:
    odd = KnownContainer("p-odd", "Ремонт завтра p1 #дом", "project")
    result = parse("+Ремонт завтра p1 #дом зал", containers=[odd])
    assert result.container == odd
    assert kinds(result) == ["project"]
    assert result.due_date is None and result.priority is None and result.tags == []
    assert result.title == "зал"


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("phrase", "leads"),
    [
        ("напомнить за 15 мин", [15]),
        ("напомни за 15 минут", [15]),
        ("напомни за 15 м", [15]),
        ("напомни за час", [60]),
        ("напомни за 1 час", [60]),
        ("напомни за 2 часа", [120]),
        ("напомни за 2ч", [120]),
        ("напомни за 1,5 часа", [90]),
        ("напомни за полчаса", [30]),
        ("напомни за пол часа", [30]),
        ("напомни за полтора часа", [90]),
        ("напомни за день", [1440]),
        ("напомни за 1 день", [1440]),
        ("напомни за 2 дня", [2880]),
        ("напомни за неделю", [10080]),
        ("напомни за 7 дней", [10080]),
        ("напомни за 1440 мин", [1440]),
        ("напомни за 0 мин", [0]),
        ("напомни мне за 10 минут", [10]),
        ("напоминание за 5 мин", [5]),
        ("напомнить за три часа", [180]),
        ("remind 30m before", [30]),
        ("remind me 1h before", [60]),
        ("remind me 1 hour before", [60]),
        ("remind me 1 day before", [1440]),
        ("remind me 2 hours before", [120]),
        ("remind me 15 minutes before", [15]),
        ("remind me an hour before", [60]),
        ("remind me half an hour before", [30]),
        ("remind me 1 week before", [10080]),
        ("remind me 30 min", [30]),
        ("reminder 10m before", [10]),
        ("Remind Me 1H Before", [60]),
    ],
)
def test_reminders(phrase: str, leads: list[int]) -> None:
    for text in (f"{phrase} зал", f"зал {phrase}", f"завтра в 9 зал {phrase}"):
        result = parse(text)
        assert result.reminder_leads == leads, text
        assert [token.text for token in result.tokens if token.kind == "reminder"] == [phrase], text
        assert result.estimate_minutes is None, "the number inside a reminder is not an estimate"
        assert result.title == "зал", text
        assert_invariants(text, result)


@pytest.mark.parametrize(
    "text",
    [
        "напомни за 2 недели",  # over a week
        "remind me 8 days before",
        "напомни позвонить маме",
        "напомни за зал",
        "remind me to call mom",
        "remind me at 5pm",
        "remind me in 30 minutes",
        "за 15 мин до встречи",  # no trigger word
        "reminder",
        "напомни",
    ],
)
def test_phrases_that_are_not_reminders(text: str) -> None:
    result = parse(text)
    assert result.reminder_leads == [], text
    assert "reminder" not in kinds(result)


def test_several_reminders_are_listed_in_order_without_duplicates() -> None:
    result = parse("зал напомни за 15 мин и remind me 1h before и напомни за 15 минут")
    assert result.reminder_leads == [15, 60]
    assert kinds(result).count("reminder") == 2
    assert result.title == "зал и и напомни за 15 минут"


def test_reminders_are_capped_at_five() -> None:
    text = "зал " + " ".join(f"remind {n}m before" for n in (5, 10, 15, 20, 25, 30, 35))
    result = parse(text)
    assert result.reminder_leads == [5, 10, 15, 20, 25]
    assert result.title == "зал remind 30m before remind 35m before"


def test_the_reminder_phrase_beats_the_duration_inside_it() -> None:
    result = parse("созвон 30 мин напомни за 10 мин")
    assert result.estimate_minutes == 30
    assert result.reminder_leads == [10]
    assert kinds(result) == ["duration", "reminder"]


# --------------------------------------------------------------------------- #
# The full sample from the contract, orders, case
# --------------------------------------------------------------------------- #
def test_contract_example() -> None:
    text = "завтра 18:00 зал #здоровье p2 +Ремонт напомни за 15 мин"
    result = parse(text, containers=[P_REPAIR])
    assert result.title == "зал"
    assert result.due_date == TOMORROW and result.due_time == time(18, 0)
    assert result.priority == 2 and result.tags == ["здоровье"]
    assert result.container == P_REPAIR and result.reminder_leads == [15]
    assert [(t.start, t.end, t.kind, t.text) for t in result.tokens] == [
        (0, 6, "date", "завтра"),
        (7, 12, "time", "18:00"),
        (17, 26, "tag", "#здоровье"),
        (27, 29, "priority", "p2"),
        (30, 37, "project", "+Ремонт"),
        (38, 55, "reminder", "напомни за 15 мин"),
    ]


def test_english_example() -> None:
    result = parse("tomorrow at 6pm for 45 min gym #health p1 @phone remind me 30m before", containers=[P_REPAIR])
    assert result.title == "gym"
    assert result.due_date == TOMORROW
    assert (result.block_start, result.block_end) == (time(18, 0), time(18, 45))
    assert result.priority == 1 and result.tags == ["health"] and result.context == "phone"
    assert result.reminder_leads == [30]
    assert kinds(result) == ["date", "time", "duration", "tag", "priority", "context", "reminder"]


RU_CHUNKS = ["завтра", "в 18:30", "зал", "#здоровье", "p2", "+Ремонт", "напомни за 15 мин", "@дом", "на 1 час"]
EN_CHUNKS = ["tomorrow", "at 6:30pm", "gym", "#health", "p2", "+Ремонт", "remind me 15m before", "@home", "for 1 hour"]


@pytest.mark.parametrize("chunks", [RU_CHUNKS, EN_CHUNKS])
def test_any_order_gives_the_same_fields(chunks: list[str]) -> None:
    rng = random.Random(6)
    baseline = None
    for _ in range(60):
        order = chunks[:]
        rng.shuffle(order)
        text = " ".join(order)
        result = parse(text, containers=[P_REPAIR])
        signature = (
            result.due_date,
            result.due_time,
            result.block_date,
            result.block_start,
            result.block_end,
            result.estimate_minutes,
            result.priority,
            tuple(result.tags),
            result.container,
            result.context,
            tuple(result.reminder_leads),
            result.title,
            sorted(kinds(result)),
        )
        baseline = baseline or signature
        assert signature == baseline, text
        assert result.title in ("зал", "gym")
        assert_invariants(text, result)
    assert baseline[0] == TOMORROW
    assert baseline[3] == time(18, 30)
    assert baseline[4] == time(19, 30)


@pytest.mark.parametrize(
    "text",
    [
        "ЗАВТРА В 18:00 ЗАЛ P2 #Здоровье НАПОМНИ ЗА 15 МИН",
        "Завтра В 18:00 Зал p2 #Здоровье Напомни За 15 Мин",
        "TOMORROW AT 6PM GYM P2 #Здоровье REMIND ME 15M BEFORE",
        "tOmOrRoW aT 6Pm gym P2 #Здоровье rEmInD mE 15M bEfOrE",
    ],
)
def test_matching_is_case_insensitive(text: str) -> None:
    result = parse(text)
    assert result.due_date == TOMORROW
    assert result.due_time == time(18, 0)
    assert result.priority == 2
    assert result.tags == ["Здоровье"]
    assert result.reminder_leads == [15]
    assert result.title in ("ЗАЛ", "Зал", "GYM", "gym")


# --------------------------------------------------------------------------- #
# Tokens: UTF-16 offsets, order, overlaps
# --------------------------------------------------------------------------- #
def test_offsets_are_utf16_code_units_after_an_emoji() -> None:
    result = parse("😀 завтра зал")
    assert [(t.start, t.end, t.kind, t.text) for t in result.tokens] == [(3, 9, "date", "завтра")]
    assert "😀 завтра зал".encode("utf-16-le")[6:18].decode("utf-16-le") == "завтра"
    assert result.title == "😀 зал"


def test_an_emoji_glued_to_the_end_of_a_token_is_not_counted_in_it() -> None:
    text = "завтра😀 зал"
    result = parse(text)
    assert [(t.start, t.end, t.text) for t in result.tokens] == [(0, 6, "завтра")]
    assert result.title == "😀 зал"
    text = "зал 😀#дом p1"  # a tag glued to an emoji is not a tag (needs whitespace), the priority is
    result = parse(text)
    assert result.tags == [] and [(t.start, t.end) for t in result.tokens] == [(11, 13)]


def test_a_token_that_contains_an_emoji_spans_its_utf16_length() -> None:
    home = KnownContainer("p-home", "🏠 Дом", "project")
    text = "зал +🏠 Дом завтра"
    result = parse(text, containers=[home])
    assert [(t.start, t.end, t.kind, t.text) for t in result.tokens] == [
        (4, 11, "project", "+🏠 Дом"),  # + (1) + house (2) + space (1) + 3 letters
        (12, 18, "date", "завтра"),
    ]
    assert result.container == home and result.title == "зал"


def test_offsets_after_several_astral_characters() -> None:
    text = "😀😀 #здоровье 💪 p2 зал"
    result = parse(text)
    assert [(t.start, t.end, t.kind) for t in result.tokens] == [(5, 14, "tag"), (18, 20, "priority")]
    for token in result.tokens:
        assert js_slice(text, token) == token.text
    assert result.title == "😀😀 💪 зал"


def test_offsets_with_zwj_sequences_and_variation_selectors() -> None:
    family = "👨‍👩‍👧"  # 3 astral characters + 2 joiners = 8 code units
    text = f"{family} p1 зал"
    result = parse(text)
    assert [(t.start, t.end) for t in result.tokens] == [(9, 11)]
    assert js_slice(text, result.tokens[0]) == "p1"
    flag = "🏋️‍♂️"
    text = f"{flag} завтра в 18:00 {flag} зал"
    result = parse(text)
    for token in result.tokens:
        assert js_slice(text, token) == token.text
    assert result.title == f"{flag} {flag} зал"


def test_emoji_between_tokens_and_inside_the_title() -> None:
    text = "зал 💪 завтра 🔥 #здоровье 🚀 в 18:00"
    result = parse(text)
    assert kinds(result) == ["date", "tag", "time"]
    assert [t.text for t in result.tokens] == ["завтра", "#здоровье", "в 18:00"]
    assert result.title == "зал 💪 🔥 🚀"
    assert_invariants(text, result)


def test_tokens_are_sorted_and_never_overlap_for_a_wide_corpus() -> None:
    corpus = [text for text, *_ in RECURRENCE_CASES] + [
        "завтра с 18 до 19:30 зал #здоровье p2 +Ремонт напомни за 15 мин каждый понедельник",
        "из-за дождя 12.05 в 3 дня на 2 часа remind me 1h before",
        "mon 6pm-7pm for 1h at 6 pm",
        "18:00-19:30 18:00-19:30 завтра завтра завтра p1 p2 p3 #a #a @b @c",
        "в 18 на 1.5 ч в 19 на 2 ч",
        "через 3 дня в 3 дня через 5 недель",
    ]
    for text in corpus:
        assert_invariants(text, parse(text, containers=CONTAINERS))


def test_only_the_first_date_time_and_estimate_are_used_the_others_stay_in_the_title() -> None:
    result = parse("завтра послезавтра зал")
    assert result.due_date == TOMORROW
    assert result.title == "послезавтра зал"
    result = parse("в 9 в 10 зал")
    assert result.due_time == time(9, 0)
    assert result.title == "в 10 зал"
    result = parse("30 мин 40 мин зал")
    assert result.estimate_minutes == 30
    assert result.title == "40 мин зал"


def test_a_block_wins_over_a_lone_time() -> None:
    result = parse("в 9 зал 18:00-19:30")
    assert (result.block_start, result.block_end) == (time(18, 0), time(19, 30))
    assert result.due_time is None
    assert result.title == "в 9 зал"


# --------------------------------------------------------------------------- #
# Title
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("завтра, 18:00, зал", "зал"),
        ("зал (завтра)", "зал"),
        ("зал [завтра в 18:00]", "зал"),
        ("«завтра» зал", "зал"),
        ('зал "завтра"', "зал"),
        ("зал - завтра - 18:00", "зал"),
        ("зал, завтра.", "зал"),
        ("зал завтра!", "зал!"),
        ("зал, , завтра, , сегодня", "зал, сегодня"),
        ("купить - завтра - молоко", "купить - молоко"),
        ("до завтра купить хлеб", "купить хлеб"),
        ("by tomorrow call mom", "call mom"),
        ("в завтра магазин", "магазин"),
        ("купить завтра молоко", "купить молоко"),
        ("купить   молоко\tзавтра\nв магазине", "купить молоко в магазине"),
        ("в магазин завтра", "в магазин"),
        ("завтра в магазин", "в магазин"),
        ("на тренировку в пятницу", "на тренировку"),
        ("к врачу в 15:00", "к врачу"),
        ("купить молоко на завтра", "купить молоко"),
        ("купить молоко до завтра", "купить молоко"),
        ("report by friday", "report"),
        ("call mom on monday", "call mom"),
        ("meeting at 5pm", "meeting"),
        ("work until friday", "work"),
        ("meet at tomorrow", "meet"),
        ("позвонить маме в 18:00 по поводу поездки", "позвонить маме по поводу поездки"),
        ("  завтра   зал   ", "зал"),
        ("Позвонить маме.", "Позвонить маме."),
        ("Купить молоко?", "Купить молоко?"),
        ("- купить молоко", "купить молоко"),
        ("• купить молоко", "купить молоко"),
        ("-5 градусов проверить", "-5 градусов проверить"),
        (".bashrc настроить", ".bashrc настроить"),
        ("завтра. Купить хлеб", "Купить хлеб"),
        ("зал | завтра", "зал"),
        ("зал: завтра", "зал"),
        ("зал ; завтра ; p1", "зал"),
        ("p1 !! зал", "!! зал"),
    ],
)
def test_title_cleanup(text: str, title: str) -> None:
    assert parse(text).title == title, text


def test_a_title_with_nothing_left_is_the_whole_text_and_the_tokens_still_describe_it() -> None:
    for text, expected_kind in (
        ("завтра", "date"),
        ("p1", "priority"),
        ("#тег", "tag"),
        ("в 18:00", "time"),
        ("30 мин", "duration"),
    ):
        result = parse(text)
        assert result.title == text
        assert kinds(result) == [expected_kind]
    result = parse("  завтра ,  18:00  ")
    assert result.title == "завтра , 18:00"  # whitespace collapsed
    assert kinds(result) == ["date", "time"]
    assert parse("(завтра)").title == "(завтра)"


def test_text_without_tokens_is_returned_as_it_was_typed() -> None:
    for text in ("Позвонить маме", "Купить 2 кг мяса и 3 яблока", "read ch. 5, then rest", "1 + 2 = 3", "100%"):
        result = parse(text)
        assert result.title == text
        assert result.tokens == []
        assert result.due_date is None and result.due_time is None and result.tags == []


# --------------------------------------------------------------------------- #
# Edges and robustness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["", " ", "   \n\t  ", " ", None])
def test_empty_and_blank_input(text) -> None:
    result = parse(text)
    assert result == ParsedTask(title="")
    assert result.tokens == []


def test_a_500_char_input() -> None:
    plain = "а" * 500
    result = parse(plain)
    assert result.title == plain and result.tokens == []
    body = ("купить молоко и хлеб " * 30)[:440].strip()
    long_line = body + " завтра в 18:00 #дом p1"
    assert 440 <= len(long_line) <= 500
    result = parse(long_line)
    assert result.due_date == TOMORROW and result.due_time == time(18, 0)
    assert result.tags == ["дом"] and result.priority == 1
    assert result.title == body
    assert kinds(result) == ["date", "time", "tag", "priority"]
    assert_invariants(long_line, result)
    tokens_only = ("завтра " * 70)[:500]
    result = parse(tokens_only)
    assert len(result.tokens) == 1  # the first date wins, the rest stays text
    assert_invariants(tokens_only, result)


def test_hostile_shapes_are_fast_and_safe() -> None:
    shapes = [
        "1" + " " * 480 + "x",
        "1 " * 250,
        "каждый " + ", ".join(["пн"] * 120),
        "с 1 до " * 70,
        "1-" * 250,
        "1:" * 250,
        "#a " * 160,
        "+a " * 160,
        "один " * 100,
        "в " * 250,
        "every " * 80,
        "1." * 250,
        "a" * 500,
        "1" * 500,
        "😀" * 250,
        "(" * 250 + ")" * 250,
    ]
    for text in shapes:
        began = clock.perf_counter()
        result = parse(text, containers=CONTAINERS)
        assert clock.perf_counter() - began < 0.25, text[:40]
        assert_invariants(text, result)


def test_a_200_char_line_parses_in_under_two_milliseconds() -> None:
    line = (
        "завтра в 18:00-19:30 зал #здоровье p2 +Ремонт напомни за 15 мин каждый понедельник и четверг "
        "купить молоко хлеб сыр 2 кг мяса и позвонить маме по поводу поездки на выходных в пятницу вечером ok"
        " и ещё немного обычного текста без разметки"
    )[:200]
    assert len(line) == 200
    timings = []
    for _ in range(80):  # the best of many runs: other processes share this machine
        began = clock.perf_counter()
        parse(line, containers=CONTAINERS)
        timings.append(clock.perf_counter() - began)
    assert min(timings) < 0.002, f"{min(timings) * 1000:.2f} ms"


def test_parsing_is_deterministic_and_has_no_side_effects() -> None:
    containers = list(CONTAINERS)
    text = "завтра 18:00 зал #здоровье p2 +Ремонт напомни за 15 мин каждый понедельник"
    first = parse(text, containers=containers)
    for _ in range(5):
        assert parse(text, containers=containers) == first
    assert containers == CONTAINERS
    assert parse(text, containers=tuple(containers)) == first
    assert parse(text, containers=iter(containers)) == first


def test_a_naive_now_works_too() -> None:
    result = parse("завтра в 9 зал", now=datetime(2026, 10, 27, 15, 30))
    assert result.due_date == TOMORROW and result.due_time == time(9, 0)


def test_the_instant_is_not_converted_only_its_local_fields_are_read() -> None:
    utc_evening = datetime(2026, 10, 27, 20, 0, tzinfo=ZoneInfo("UTC"))
    tokyo_night = utc_evening.astimezone(ZoneInfo("Asia/Tokyo"))  # 05:00 on the 28th
    assert parse("сегодня зал", now=utc_evening).due_date == d(10, 27)
    assert parse("сегодня зал", now=tokyo_night).due_date == d(10, 28)
    assert parse("в 6 зал", now=tokyo_night).due_date == d(10, 28)  # 06:00 is still ahead of 05:00


def test_random_noise_never_breaks_an_invariant() -> None:
    fragments = [
        "завтра",
        "tomorrow",
        "в",
        "на",
        "at",
        "by",
        "18:00",
        "19:30",
        "-",
        "–",
        "до",
        "с",
        "6pm",
        "7:30pm",
        "9",
        "12",
        "15",
        "30",
        "1.5",
        "1,5",
        "ч",
        "мин",
        "m",
        "h",
        "часа",
        "дня",
        "утра",
        "вечера",
        "ночи",
        "#тег",
        "#a-b",
        "@дом",
        "@home",
        "+Ремонт",
        "+[Мой проект]",
        "+Новая",
        "p1",
        "!2",
        "р" + "3",
        "каждый",
        "каждые",
        "every",
        "other",
        "день",
        "неделю",
        "недели",
        "понедельник",
        "пн",
        "вт",
        "mon",
        "thu",
        "и",
        "and",
        ",",
        ".",
        ":",
        ";",
        "12.05",
        "12.05.2027",
        "2027-05-12",
        "мая",
        "May",
        "через",
        "in",
        "days",
        "week",
        "напомни",
        "за",
        "remind",
        "me",
        "before",
        "полчаса",
        "полтора",
        "час",
        "an",
        "hour",
        "half",
        "ежедневно",
        "по",
        "будням",
        "выходных",
        "😀",
        "(",
        ")",
        "[",
        "]",
        '"',
        "«",
        "»",
        "\n",
        "\t",
        "  ",
        "0",
        "24",
        "25",
        "999",
        "10000",
        "http://x.y/1",
        "a@b.cc",
        "—",
        "+",
        "#",
        "@",
        "!",
        "p",
        "ё",
        "ß",
        "İ",
        "ſat",
        "ſun",
        "K",
        " ",
    ]
    rng = random.Random(20261027)
    for _ in range(1500):
        text = rng.choice([" ", "", ", "]).join(rng.choice(fragments) for _ in range(rng.randint(1, 40)))
        if rng.random() < 0.25:
            text = text[: rng.randint(0, len(text))]
        result = parse(
            text, now=at(rng.randint(0, 23), rng.randint(0, 59)), week_start=rng.randint(0, 6), containers=CONTAINERS
        )
        assert_invariants(text, result)
        assert parse(text, now=NOW, containers=CONTAINERS) == parse(text, now=NOW, containers=CONTAINERS)
