"""Notifications (stage 7): preferences, quiet hours, messages, the dispatcher and the daily planner.

Two scheduler jobs (registered in ``app/services/hooks.py``):

* ``dispatch_notifications`` (every minute) delivers due ``reminders`` rows — task
  reminders created by the planner (stage 6) and the smart daily reminders below.
  Each row goes through three steps: *claim* (a short transaction: ``FOR UPDATE
  SKIP LOCKED``, every check, then a lease in ``payload`` and one attempt counted),
  *send* (no transaction, no lock — bounded by the push sender's deadline), *book*
  (a short transaction: results, status, history). A task edit therefore never
  waits for a push service, and an overlapping run skips a leased row. Users are
  served side by side (``DISPATCH_CONCURRENCY`` reminders at once, each user's in
  order), so one user's slow push service never delays anybody else.
* ``plan_daily_reminders`` (every 15 min) creates today's smart reminders for
  every user with push on, at their local times (DST-safe), idempotent by
  ``dedupe_key``, never in the past and never inside quiet hours.

Every push carries a TTL (``reminder_ttl``): the push service may keep it for an
offline phone only until the reminder goes stale and never into the next quiet
hours; the service worker drops one that still arrives after ``expires_at``.

Smart reminders re-check their condition **at send time**, so nobody gets a
pointless nudge: meal (nothing logged in the window), water (behind the
pro-rata share of the target), workout (planned today, not started), evening
review (no journal entry yet), streak at risk (stage 8: a streak of ≥ 3 days/weeks
not counted yet today, after 18:00 — planned at the first minute from 18:00 that is
not inside the quiet hours, :func:`streak_slot`). Meal reminders are about *logging*:
no message ever suggests eating less or praises skipping a meal (see ``MESSAGES``).
"""

from __future__ import annotations

import asyncio
import math
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.tz import ensure_utc, get_zone, utcnow
from app.db import session_scope
from app.models import (
    JournalEntry,
    MealLog,
    NotificationLog,
    Reminder,
    ReminderKind,
    ReminderStatus,
    Task,
    TaskStatus,
    User,
    UserSettings,
    UserStatus,
    WaterLog,
    WeeklyPlan,
    WorkoutLog,
    WorkoutStatus,
    WorkoutTemplate,
)
from app.schemas.notifications import (
    MAX_MEAL_TIMES,
    MAX_WATER_TIMES,
    NotificationPrefsIn,
    normalise_hhmm,
    normalise_times,
)
from app.services import push
from app.services.settings_service import current_targets

log = get_logger(__name__)

CATEGORIES: tuple[str, ...] = (
    "task_reminders",
    "meal_logging",
    "water",
    "workout",
    "evening_review",
    "streak_at_risk",
)
DEFAULT_MEAL_TIMES: tuple[str, ...] = ("13:00", "19:30")
DEFAULT_WATER_TIMES: tuple[str, ...] = ("12:00", "16:00", "20:00")
DEFAULT_WORKOUT_TIME = "18:00"
DEFAULT_EVENING_REVIEW_TIME = "21:30"
STREAK_REMINDER_FROM = time(18, 0)  # stage 8: streaks are "at risk" from 18:00; one evening nudge

STALE_AFTER = timedelta(minutes=60)  # a reminder more than an hour late is dropped, not sent
NUDGE_TTL = timedelta(minutes=30)  # a daily nudge is worth less: a push service keeps it at most this long
MAX_ATTEMPTS = 3  # transient push failures are retried on the next runs, then `failed`
BATCH_LIMIT = 500
DISPATCH_CONCURRENCY = 4  # reminders in flight at once (different users side by side)
LEASE = timedelta(minutes=2)  # a claimed row is left alone this long (> push.SEND_DEADLINE_SECONDS)
LEASE_KEY = "_sending"  # reminders.payload while its push is in flight: {"token": …, "until": ISO}
_ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
WATER_DAY = (time(8, 0), time(22, 0))  # the water target is spread over this part of the day
PLANNER_INTERVAL = timedelta(minutes=15)

CATEGORY_BY_KIND: dict[ReminderKind, str] = {
    ReminderKind.TASK: "task_reminders",
    ReminderKind.MEAL: "meal_logging",
    ReminderKind.WATER: "water",
    ReminderKind.WORKOUT: "workout",
    ReminderKind.EVENING_REVIEW: "evening_review",
    ReminderKind.STREAK: "streak_at_risk",
}
SMART_KINDS = frozenset(
    {ReminderKind.MEAL, ReminderKind.WATER, ReminderKind.WORKOUT, ReminderKind.EVENING_REVIEW, ReminderKind.STREAK}
)
CLOSED_TASK = (TaskStatus.DONE, TaskStatus.CANCELLED)


# --------------------------------------------------------------------------- #
# Message catalogue (ru default, en). Short and specific; never about eating less.
# --------------------------------------------------------------------------- #
MESSAGES: dict[str, dict[str, str]] = {
    "ru": {
        "task.now": "Сейчас",
        "task.soon": "Через {lead} · {time}",
        "task.tomorrow": "Завтра · {time}",
        "task.on_day": "{date} · {time}",
        "task.overdue_at": "Срок прошёл · {time}",
        "task.due_today": "Срок сегодня",
        "task.due_tomorrow": "Срок завтра",
        "task.due_on": "Срок: {date}",
        "task.overdue": "Срок прошёл",
        "task.plain": "Напоминание",
        "lead.min": "{m} мин",
        "lead.h": "{h} ч",
        "lead.h_min": "{h} ч {m} мин",
        "meal.title": "Дневник питания",
        "meal.first": "Запишите, что вы ели сегодня, — это займёт минуту",
        "meal.since": "Запишите приёмы пищи с {time}",
        "water.title": "Вода",
        "water.body": "Вода: осталось {left} мл до цели",
        "water.generic": "Отметьте выпитую воду",
        "workout.title": "Тренировка по плану",
        "workout.body": "Сегодня: {name}. Ещё не начата",
        "workout.generic": "На сегодня запланирована тренировка",
        "review.title": "Итоги дня",
        "review.body": "Как прошёл день? Заполните итоги",
        "test.title": "Проверка уведомлений",
        "test.body": "Всё работает: уведомления приходят на это устройство",
        "streak.title": "Сохраните серию",
        "streak.one": "{name}: {count} подряд — сегодня ещё не засчитано",
        "streak.one_week": "{name}: {count} подряд — эта неделя ещё не засчитана",
        "streak.many": "{names} — сегодня ещё не засчитано",
        "streak.generic": "Серия ждёт сегодняшней отметки",
        "streak.overall": "Счёт дня",
        "streak.meal_logging": "Дневник питания",
        "streak.water": "Вода",
        "streak.steps": "Шаги",
        "streak.tasks": "Задачи",
        "streak.review": "Итоги дня",
        "streak.workout": "Тренировки",
        "unit.day.one": "{n} день",
        "unit.day.few": "{n} дня",
        "unit.day.many": "{n} дней",
        "unit.week.one": "{n} неделя",
        "unit.week.few": "{n} недели",
        "unit.week.many": "{n} недель",
    },
    "en": {
        "task.now": "Now",
        "task.soon": "In {lead} · {time}",
        "task.tomorrow": "Tomorrow · {time}",
        "task.on_day": "{date} · {time}",
        "task.overdue_at": "Overdue · {time}",
        "task.due_today": "Due today",
        "task.due_tomorrow": "Due tomorrow",
        "task.due_on": "Due {date}",
        "task.overdue": "Overdue",
        "task.plain": "Reminder",
        "lead.min": "{m} min",
        "lead.h": "{h} h",
        "lead.h_min": "{h} h {m} min",
        "meal.title": "Food log",
        "meal.first": "Log what you have eaten today — it takes a minute",
        "meal.since": "Log your meals since {time}",
        "water.title": "Water",
        "water.body": "Water: {left} ml left to your goal",
        "water.generic": "Log the water you have had",
        "workout.title": "Planned workout",
        "workout.body": "Today: {name}. Not started yet",
        "workout.generic": "A workout is planned for today",
        "review.title": "Daily review",
        "review.body": "How did your day go? Fill in your review",
        "test.title": "Test notification",
        "test.body": "It works: notifications reach this device",
        "streak.title": "Keep your streak",
        "streak.one": "{name}: {count} in a row — today isn't counted yet",
        "streak.one_week": "{name}: {count} in a row — this week isn't counted yet",
        "streak.many": "{names} — not counted yet today",
        "streak.generic": "Your streak is waiting for today",
        "streak.overall": "Daily score",
        "streak.meal_logging": "Food log",
        "streak.water": "Water",
        "streak.steps": "Steps",
        "streak.tasks": "Tasks",
        "streak.review": "Daily review",
        "streak.workout": "Workouts",
        "unit.day.one": "{n} day",
        "unit.day.few": "{n} days",
        "unit.day.many": "{n} days",
        "unit.week.one": "{n} week",
        "unit.week.few": "{n} weeks",
        "unit.week.many": "{n} weeks",
    },
}
MONTHS = {
    "ru": ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}


def language_of(settings: UserSettings | None) -> str:
    value = getattr(getattr(settings, "language", None), "value", getattr(settings, "language", None))
    return value if value in MESSAGES else "ru"


def _t(lang: str, key: str, **values: Any) -> str:
    return MESSAGES[lang][key].format(**values)


def _day_label(day: date, lang: str) -> str:
    month = MONTHS[lang][day.month - 1]
    return f"{day.day} {month}" if lang == "ru" else f"{month} {day.day}"


def _lead_label(minutes: int, lang: str) -> str:
    if minutes < 60:
        return _t(lang, "lead.min", m=minutes)
    hours, rest = divmod(minutes, 60)
    return _t(lang, "lead.h", h=hours) if rest == 0 else _t(lang, "lead.h_min", h=hours, m=rest)


def _ml(value: int, lang: str) -> str:
    text = f"{value:,}"
    return text.replace(",", " ") if lang == "ru" else text


# --------------------------------------------------------------------------- #
# Preferences (user_settings.notification_prefs — flat JSONB, legacy keys kept)
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class Prefs:
    task_reminders: bool = True
    meal_logging: bool = True
    water: bool = True
    workout: bool = True
    evening_review: bool = True
    streak_at_risk: bool = True
    meal_times: tuple[str, ...] = DEFAULT_MEAL_TIMES
    water_times: tuple[str, ...] = DEFAULT_WATER_TIMES
    workout_time: str = DEFAULT_WORKOUT_TIME
    evening_review_time: str = DEFAULT_EVENING_REVIEW_TIME

    def enabled(self, category: str) -> bool:
        return bool(getattr(self, category, True))


def read_prefs(raw: Any) -> Prefs:
    """Typed view of the JSONB; anything malformed falls back to its default (never a 500)."""
    raw = raw if isinstance(raw, dict) else {}
    values: dict[str, Any] = {}
    for category in CATEGORIES:
        if isinstance(raw.get(category), bool):
            values[category] = raw[category]
    for key, limit in (("meal_times", MAX_MEAL_TIMES), ("water_times", MAX_WATER_TIMES)):
        if isinstance(raw.get(key), list):
            try:
                values[key] = tuple(normalise_times([str(item) for item in raw[key]])[:limit])
            except ValueError:
                pass
    for key in ("workout_time", "evening_review_time"):
        if isinstance(raw.get(key), str):
            try:
                values[key] = normalise_hhmm(raw[key])
            except ValueError:
                pass
    return Prefs(**values)


def merge_prefs(raw: Any, update: NotificationPrefsIn) -> dict[str, Any]:
    merged = dict(raw) if isinstance(raw, dict) else {}
    for key, value in update.model_dump(exclude_unset=True).items():
        if value is not None:
            merged[key] = list(value) if isinstance(value, (list, tuple)) else value
    return merged


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def local_instant(day: date, at: time, tz: str | None) -> datetime:
    """UTC instant of a local wall-clock time. DST-safe: a time inside a spring-forward
    gap lands just after it (02:30 → 03:30), an ambiguous one takes the first occurrence."""
    return datetime.combine(day, at).replace(tzinfo=get_zone(tz)).astimezone(UTC)


@dataclass(slots=True, frozen=True)
class QuietHours:
    enabled: bool
    start: time
    end: time

    @classmethod
    def of(cls, settings: UserSettings | None) -> QuietHours:
        if settings is None:
            return cls(False, time(23, 0), time(7, 0))
        return cls(bool(settings.quiet_hours_enabled), settings.quiet_hours_start, settings.quiet_hours_end)

    def contains(self, local: time) -> bool:
        """Is this local wall-clock time quiet? Ranges may cross midnight; start == end = none."""
        if not self.enabled or self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end

    def active_at(self, instant: datetime, tz: str | None) -> bool:
        return self.contains(ensure_utc(instant).astimezone(get_zone(tz)).time())

    def end_after(self, instant: datetime, tz: str | None) -> datetime:
        """The UTC instant at which the quiet period containing ``instant`` ends (DST-safe)."""
        local = ensure_utc(instant).astimezone(get_zone(tz))
        day = local.date()
        if self.start > self.end and local.time() >= self.start:  # the evening part of an overnight range
            day += timedelta(days=1)
        return local_instant(day, self.end, tz)

    def next_start(self, instant: datetime, tz: str | None) -> datetime | None:
        """The first UTC instant after ``instant`` at which quiet hours begin (DST-safe);
        ``None`` when there are none."""
        if not self.enabled or self.start == self.end:
            return None
        moment = ensure_utc(instant)
        day = moment.astimezone(get_zone(tz)).date()
        start = local_instant(day, self.start, tz)
        return start if start > moment else local_instant(day + timedelta(days=1), self.start, tz)


# --------------------------------------------------------------------------- #
# Per-user context
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class UserContext:
    user_id: uuid.UUID
    settings: UserSettings | None
    prefs: Prefs
    tz: str
    lang: str
    quiet: QuietHours
    now: datetime

    @property
    def today(self) -> date:
        return self.now.astimezone(get_zone(self.tz)).date()


async def user_context(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> UserContext | None:
    user = await db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        return None
    settings = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    return UserContext(
        user_id=user_id,
        settings=settings,
        prefs=read_prefs(settings.notification_prefs if settings else None),
        tz=(settings.timezone if settings else None) or get_settings().default_timezone,
        lang=language_of(settings),
        quiet=QuietHours.of(settings),
        now=now,
    )


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class TaskView:
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    status: TaskStatus
    due_at: datetime | None
    due_date: date | None
    scheduled_start: datetime | None


async def load_task(db: AsyncSession, task_id: uuid.UUID | None) -> TaskView | None:
    if task_id is None:
        return None
    row = (
        await db.execute(
            select(
                Task.id, Task.user_id, Task.title, Task.status, Task.due_at, Task.due_date, Task.scheduled_start
            ).where(Task.id == task_id)
        )
    ).first()
    return TaskView(*row) if row is not None else None


def task_body(task: TaskView, *, now: datetime, tz: str, lang: str) -> str:
    zone = get_zone(tz)
    today = now.astimezone(zone).date()
    anchor = task.due_at or task.scheduled_start
    if anchor is not None:
        local = ensure_utc(anchor).astimezone(zone)
        at = local.strftime("%H:%M")
        seconds = (ensure_utc(anchor) - now).total_seconds()
        if -120 <= seconds < 60:  # the dispatcher runs every minute: "now" includes a little lateness
            return _t(lang, "task.now")
        if seconds < 0:
            return _t(lang, "task.overdue_at", time=at)
        if local.date() == today:
            return _t(lang, "task.soon", lead=_lead_label(math.ceil(seconds / 60), lang), time=at)
        if local.date() == today + timedelta(days=1):
            return _t(lang, "task.tomorrow", time=at)
        return _t(lang, "task.on_day", date=_day_label(local.date(), lang), time=at)
    if task.due_date is not None:
        if task.due_date == today:
            return _t(lang, "task.due_today")
        if task.due_date == today + timedelta(days=1):
            return _t(lang, "task.due_tomorrow")
        if task.due_date < today:
            return _t(lang, "task.overdue")
        return _t(lang, "task.due_on", date=_day_label(task.due_date, lang))
    return _t(lang, "task.plain")


def task_message(task: TaskView, *, now: datetime, tz: str, lang: str) -> push.PushMessage:
    return push.PushMessage(
        title=task.title,
        body=task_body(task, now=now, tz=tz, lang=lang),
        url=f"/plan?task={task.id}",
        tag=f"task:{task.id}",  # a newer reminder for the same task replaces the older one
        kind="task",
        ref_id=str(task.id),
        urgency="high",
    )


def meal_message(lang: str, since: str | None) -> push.PushMessage:
    body = _t(lang, "meal.since", time=since) if since else _t(lang, "meal.first")
    return push.PushMessage(title=_t(lang, "meal.title"), body=body, url="/food?action=search", tag="meal", kind="meal")


def water_message(lang: str, left_ml: int) -> push.PushMessage:
    return push.PushMessage(
        title=_t(lang, "water.title"),
        body=_t(lang, "water.body", left=_ml(left_ml, lang)),
        url="/today",
        tag="water",
        kind="water",
    )


def workout_message(lang: str, name: str | None) -> push.PushMessage:
    body = _t(lang, "workout.body", name=name) if name else _t(lang, "workout.generic")
    return push.PushMessage(title=_t(lang, "workout.title"), body=body, url="/train", tag="workout", kind="workout")


def review_message(lang: str) -> push.PushMessage:
    return push.PushMessage(
        title=_t(lang, "review.title"),
        body=_t(lang, "review.body"),
        url="/plan/journal",
        tag="evening_review",
        kind="evening_review",
    )


def test_message(lang: str) -> push.PushMessage:
    """Sent on request, quiet hours or not; the user is waiting for it, so the service worker
    never treats it as expired (the push service still keeps it only a minute)."""
    return push.PushMessage(
        title=_t(lang, "test.title"),
        body=_t(lang, "test.body"),
        url="/settings/notifications",
        tag="test",
        kind="test",
        ttl=push.TEST_TTL_SECONDS,
        urgency="high",
        expires=False,
    )


def reminder_ttl(ctx: UserContext, remind_at: datetime, *, smart: bool) -> int:
    """Seconds a push service may keep this reminder for a phone that is offline (RFC 8030 TTL).

    Until the reminder goes stale (``STALE_AFTER`` past its time: what the dispatcher itself
    still sends), at most ``NUDGE_TTL`` for a daily nudge, and never into the next quiet
    hours — a phone that comes back online at 03:00 gets nothing rather than last
    evening's reminder with its "in 15 min" text."""
    deadline = ensure_utc(remind_at) + STALE_AFTER
    if smart:
        deadline = min(deadline, ctx.now + NUDGE_TTL)
    quiet_start = ctx.quiet.next_start(ctx.now, ctx.tz)
    if quiet_start is not None:
        deadline = min(deadline, quiet_start)
    return max(0, int((deadline - ctx.now).total_seconds()))


STREAK_URLS: dict[str, str] = {
    "overall": "/today",
    "meal_logging": "/food?action=search",
    "water": "/today",
    "steps": "/today?action=steps",
    "tasks": "/plan",
    "review": "/plan/journal",
    "workout": "/train",
}
STREAKS_LISTED = 3


def _plural(n: int) -> str:
    """Russian plural form (1 день · 2 дня · 5 дней · 21 день); English uses one/many."""
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


def _count_label(n: int, unit: str, lang: str) -> str:
    form = _plural(n) if lang == "ru" else ("one" if n == 1 else "many")
    return _t(lang, f"unit.{unit}.{form}", n=n)


def streak_message(lang: str, streaks: list[Any]) -> push.PushMessage:
    """Evening reminder for streaks at risk (``kind``, ``current``, ``unit``): about the
    streak only — never about food amounts."""
    top = streaks[0]
    if len(streaks) == 1:
        key = "streak.one_week" if top.unit == "week" else "streak.one"
        body = _t(lang, key, name=_t(lang, f"streak.{top.kind.value}"), count=_count_label(top.current, top.unit, lang))
    else:
        names = ", ".join(
            f"{_t(lang, f'streak.{item.kind.value}')} ({_count_label(item.current, item.unit, lang)})"
            for item in streaks[:STREAKS_LISTED]
        )
        body = _t(lang, "streak.many", names=names)
    return push.PushMessage(
        title=_t(lang, "streak.title"),
        body=body,
        url=STREAK_URLS.get(top.kind.value, "/today"),
        tag="streak",
        kind="streak",
    )


def _generic_text(kind: ReminderKind, lang: str) -> tuple[str, str]:
    """Title/body stored on a planned smart reminder (history shows them if it is never sent)."""
    return {
        ReminderKind.MEAL: (_t(lang, "meal.title"), _t(lang, "meal.first")),
        ReminderKind.WATER: (_t(lang, "water.title"), _t(lang, "water.generic")),
        ReminderKind.WORKOUT: (_t(lang, "workout.title"), _t(lang, "workout.generic")),
        ReminderKind.EVENING_REVIEW: (_t(lang, "review.title"), _t(lang, "review.body")),
        ReminderKind.STREAK: (_t(lang, "streak.title"), _t(lang, "streak.generic")),
    }.get(kind, (_t(lang, "task.plain"), ""))


# --------------------------------------------------------------------------- #
# Conditions (evaluated at send time)
# --------------------------------------------------------------------------- #
def _follow_streak_slot(row: Reminder, slot: tuple[date, str], quiet: QuietHours) -> tuple[date, str]:
    """The streak slot follows the quiet hours: when they changed since the row was
    planned, the row moves to today's slot (rescheduled when that is later, sent now
    when it has passed) instead of being dropped as "prefs changed"."""
    at = streak_slot(quiet)
    if at is None or at == slot[1]:
        return slot
    row.payload = {**(row.payload if isinstance(row.payload, dict) else {}), "time": at}
    return slot[0], at


def _slot_of(reminder: Reminder) -> tuple[date, str] | None:
    payload = reminder.payload if isinstance(reminder.payload, dict) else {}
    try:
        return date.fromisoformat(str(payload["date"])), normalise_hhmm(str(payload["time"]))
    except (KeyError, ValueError):
        return None


def streak_slot(quiet: QuietHours) -> str | None:
    """The streak reminder's time: the first minute from 18:00 (when streaks become "at
    risk") that is not inside the quiet hours — ``None`` when the whole evening is quiet
    (then there is no streak reminder that day). Still one per day."""
    start = STREAK_REMINDER_FROM.hour * 60 + STREAK_REMINDER_FROM.minute
    for minute in range(start, 24 * 60):
        at = time(minute // 60, minute % 60)
        if not quiet.contains(at):
            return at.strftime("%H:%M")
    return None


def configured_slots(kind: ReminderKind, prefs: Prefs, quiet: QuietHours) -> tuple[str, ...]:
    if kind is ReminderKind.MEAL:
        return prefs.meal_times
    if kind is ReminderKind.WATER:
        return prefs.water_times
    if kind is ReminderKind.WORKOUT:
        return (prefs.workout_time,)
    if kind is ReminderKind.EVENING_REVIEW:
        return (prefs.evening_review_time,)
    if kind is ReminderKind.STREAK:
        at = streak_slot(quiet)
        return (at,) if at is not None else ()
    return ()


async def meal_window_empty(db: AsyncSession, user_id: uuid.UUID, day: date, since: str | None, tz: str) -> bool:
    """Nothing logged that day (first reminder) / since the previous reminder time."""
    query = (
        select(func.count())
        .select_from(MealLog)
        .where(MealLog.user_id == user_id, MealLog.local_day == day, MealLog.is_deleted.is_(False))
    )
    if since is not None:
        query = query.where(MealLog.logged_at >= local_instant(day, _hhmm(since), tz))
    return int(await db.scalar(query) or 0) == 0


async def water_left(db: AsyncSession, ctx: UserContext, day: date) -> int | None:
    """Millilitres still missing to the daily target when today's water is below the
    pro-rata share for this hour (target × elapsed share of 08:00–22:00); else ``None``."""
    if ctx.settings is None:
        return None
    target = current_targets(ctx.settings, as_of=day).target_water_ml
    if not target:
        return None
    drunk = int(
        await db.scalar(
            select(func.coalesce(func.sum(WaterLog.amount_ml), 0)).where(
                WaterLog.user_id == ctx.user_id, WaterLog.local_day == day
            )
        )
        or 0
    )
    start, end = (local_instant(day, bound, ctx.tz) for bound in WATER_DAY)
    share = min(1.0, max(0.0, (ctx.now - start) / (end - start)))
    if drunk >= target * share:
        return None
    return max(int(target) - drunk, 0)


async def planned_workout(db: AsyncSession, user_id: uuid.UUID, day: date) -> tuple[WeeklyPlan, str | None] | None:
    """Today's plan (a dated override beats the weekday plan) if it is a workout with reminders on."""
    rows = (
        await db.scalars(
            select(WeeklyPlan).where(
                WeeklyPlan.user_id == user_id,
                or_(
                    WeeklyPlan.plan_date == day,
                    (WeeklyPlan.weekday == day.weekday()) & WeeklyPlan.plan_date.is_(None),
                ),
            )
        )
    ).all()
    row = next((r for r in rows if r.plan_date == day), None) or next((r for r in rows if r.plan_date is None), None)
    if row is None or row.is_rest_day or not row.reminder_enabled:
        return None
    name = row.title
    if row.template_id is not None:
        template = await db.scalar(
            select(WorkoutTemplate).where(
                WorkoutTemplate.id == row.template_id,
                WorkoutTemplate.user_id == user_id,
                WorkoutTemplate.is_archived.is_(False),
            )
        )
        if template is not None:
            name = name or template.name
    if row.template_id is None and not row.title:
        return None
    return row, name


async def workout_started(db: AsyncSession, user_id: uuid.UUID, day: date) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(WorkoutLog)
        .where(
            WorkoutLog.user_id == user_id,
            WorkoutLog.local_day == day,
            WorkoutLog.is_deleted.is_(False),
            WorkoutLog.status.in_((WorkoutStatus.IN_PROGRESS, WorkoutStatus.COMPLETED)),
        )
    )
    return int(count or 0) > 0


async def journal_written(db: AsyncSession, user_id: uuid.UUID, day: date) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(JournalEntry)
        .where(JournalEntry.user_id == user_id, JournalEntry.entry_date == day)
    )
    return int(count or 0) > 0


SmartBuilder = Callable[[AsyncSession, Reminder, UserContext, date, str], Awaitable[push.PushMessage | None]]


async def _build_meal(
    db: AsyncSession, row: Reminder, ctx: UserContext, day: date, slot: str
) -> push.PushMessage | None:
    earlier = [value for value in ctx.prefs.meal_times if value < slot]
    since = earlier[-1] if earlier else None
    if not await meal_window_empty(db, ctx.user_id, day, since, ctx.tz):
        return None
    return meal_message(ctx.lang, since)


async def _build_water(
    db: AsyncSession, row: Reminder, ctx: UserContext, day: date, slot: str
) -> push.PushMessage | None:
    left = await water_left(db, ctx, day)
    return water_message(ctx.lang, left) if left else None


async def _build_workout(
    db: AsyncSession, row: Reminder, ctx: UserContext, day: date, slot: str
) -> push.PushMessage | None:
    plan = await planned_workout(db, ctx.user_id, day)
    if plan is None or await workout_started(db, ctx.user_id, day):
        return None
    return workout_message(ctx.lang, plan[1])


async def _build_review(
    db: AsyncSession, row: Reminder, ctx: UserContext, day: date, slot: str
) -> push.PushMessage | None:
    return None if await journal_written(db, ctx.user_id, day) else review_message(ctx.lang)


async def _build_streak(
    db: AsyncSession, row: Reminder, ctx: UserContext, day: date, slot: str
) -> push.PushMessage | None:
    """Stage 8: only streaks of ≥ 3 whose today's (this week's) condition is not met yet
    and that are at risk now (after 18:00 local). The message names the streak — never
    food amounts, never "eat less"."""
    from app.services.gamification.streaks import NOTIFY_MIN_STREAK, at_risk

    risky = await at_risk(db, ctx.user_id, ctx.now, min_count=NOTIFY_MIN_STREAK)
    return streak_message(ctx.lang, risky) if risky else None


SMART_BUILDERS: dict[ReminderKind, SmartBuilder] = {
    ReminderKind.MEAL: _build_meal,
    ReminderKind.WATER: _build_water,
    ReminderKind.WORKOUT: _build_workout,
    ReminderKind.EVENING_REVIEW: _build_review,
    ReminderKind.STREAK: _build_streak,
}


# --------------------------------------------------------------------------- #
# Notification log
# --------------------------------------------------------------------------- #
async def write_log(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    dedupe_key: str,
    kind: str,
    title: str,
    body: str | None,
    payload: dict[str, Any],
    status: str,
    now: datetime,
    scheduled_for: datetime | None = None,
    reminder_id: uuid.UUID | None = None,
    error: str | None = None,
    quiet: bool = False,
    report: push.DeliveryReport | None = None,
) -> None:
    """One row per reminder (``reminder:{id}``): later attempts update it in place; the
    first ``scheduled_for`` and the quiet-hours flag are kept."""
    first = report.first_success if report is not None else None
    values = {
        "kind": kind[:48],
        "title": push.clip_text(title or "—", 200),
        "body": body,
        "payload": payload,
        "status": status,
        "sent_at": now if status == "sent" else None,
        "error": error,
        "device_id": first.device_id if first else None,
        "subscription_id": first.subscription_id if first else None,
        "updated_at": now,
    }
    stmt = pg_insert(NotificationLog).values(
        id=new_id(),
        user_id=user_id,
        reminder_id=reminder_id,
        dedupe_key=dedupe_key,
        scheduled_for=scheduled_for,
        quiet_hours_suppressed=quiet,
        created_at=now,
        **values,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "dedupe_key"],
        set_={
            **values,
            "quiet_hours_suppressed": NotificationLog.quiet_hours_suppressed | stmt.excluded.quiet_hours_suppressed,
        },
    )
    await db.execute(stmt)


async def history(db: AsyncSession, user_id: uuid.UUID, *, limit: int = 50) -> list[NotificationLog]:
    rows = await db.scalars(
        select(NotificationLog)
        .where(NotificationLog.user_id == user_id)
        .order_by(NotificationLog.updated_at.desc(), NotificationLog.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


async def log_test(
    db: AsyncSession, *, user_id: uuid.UUID, message: push.PushMessage, report: push.DeliveryReport, now: datetime
) -> None:
    if report.sent:
        status, error = "sent", None
    elif report.results:
        status, error = "failed", f"delivery_failed:{report.summary()}"
    else:
        status, error = "suppressed", "no_subscription"
    await write_log(
        db,
        user_id=user_id,
        dedupe_key=f"test:{new_id()}",
        kind="test",
        title=message.title,
        body=message.body,
        payload=message.data(now),
        status=status,
        now=now,
        scheduled_for=now,
        error=error,
        report=report,
    )


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class DispatchReport:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, outcome: str) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + 1

    def get(self, outcome: str) -> int:
        return self.counts.get(outcome, 0)

    @property
    def total(self) -> int:
        return sum(value for key, value in self.counts.items() if key != "skipped")


def _close(row: Reminder, status: ReminderStatus, reason: str) -> str:
    row.status = status
    row.last_error = reason
    return f"{status.value}:{reason}"


async def _suppress(
    db: AsyncSession, row: Reminder, display: push.PushMessage, reason: str, now: datetime, *, quiet: bool = False
) -> str:
    await write_log(
        db,
        user_id=row.user_id,
        dedupe_key=f"reminder:{row.id}",
        kind=row.kind.value,
        title=display.title,
        body=display.body,
        payload=display.data(),
        status="suppressed",
        now=now,
        scheduled_for=row.remind_at,
        reminder_id=row.id,
        error=reason,
        quiet=quiet,
    )
    return _close(row, ReminderStatus.SUPPRESSED, reason)


@dataclass(slots=True)
class Claim:
    """A reminder leased for sending: everything the send and the booking need."""

    reminder_id: uuid.UUID
    user_id: uuid.UUID
    kind: ReminderKind
    remind_at: datetime
    attempts: int
    token: str
    message: push.PushMessage
    targets: list[push.Target]


def _lease_of(row: Reminder) -> tuple[str, datetime] | None:
    """``(token, until)`` of the lease a run put on the row while sending, if any."""
    lease = row.payload.get(LEASE_KEY) if isinstance(row.payload, dict) else None
    if not isinstance(lease, dict) or not isinstance(lease.get("until"), str):
        return None
    if not _ISO_UTC.fullmatch(lease["until"]):
        return None
    return str(lease.get("token") or ""), datetime.strptime(lease["until"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _without_lease(payload: Any) -> dict[str, Any]:
    return {key: value for key, value in (payload or {}).items() if key != LEASE_KEY}


async def claim_reminder(db: AsyncSession, reminder_id: uuid.UUID, now: datetime) -> Claim | str:
    """Step 1 (short transaction): lock the due row (``FOR UPDATE SKIP LOCKED``), decide.

    Returns the outcome when the reminder ends here (cancelled, suppressed, deferred,
    rescheduled, skipped); otherwise counts the attempt, leases the row (``LEASE_KEY``
    in ``payload``) and returns the ``Claim`` — the caller commits before sending."""
    row = await db.scalar(
        select(Reminder)
        .where(Reminder.id == reminder_id, Reminder.status == ReminderStatus.PENDING, Reminder.remind_at <= now)
        .with_for_update(skip_locked=True)
    )
    if row is None:  # another run holds it, or it changed meanwhile
        return "skipped"
    lease = _lease_of(row)
    if lease is not None and lease[1] > now:  # another run is sending it right now
        return "skipped"
    interrupted = lease is not None  # a run died while sending it: its lease lapsed
    if interrupted:
        row.payload = _without_lease(row.payload)
    ctx = await user_context(db, row.user_id, now)
    if ctx is None:
        return _close(row, ReminderStatus.CANCELLED, "user_inactive")

    category = CATEGORY_BY_KIND.get(row.kind)
    if category is not None and not ctx.prefs.enabled(category):
        return _close(row, ReminderStatus.CANCELLED, "category_off")

    smart = row.kind in SMART_KINDS
    task: TaskView | None = None
    slot: tuple[date, str] | None = None
    if row.kind is ReminderKind.TASK:
        task = await load_task(db, row.task_id)
        if task is None or task.user_id != row.user_id:
            return _close(row, ReminderStatus.CANCELLED, "task_missing")
        if task.status in CLOSED_TASK:
            return _close(row, ReminderStatus.CANCELLED, "task_closed")
        display = task_message(task, now=now, tz=ctx.tz, lang=ctx.lang)
    elif smart:
        slot = _slot_of(row)
        if slot is not None and row.kind is ReminderKind.STREAK:
            slot = _follow_streak_slot(row, slot, ctx.quiet)
        if slot is not None and configured_slots(row.kind, ctx.prefs, ctx.quiet):
            if slot[1] not in configured_slots(row.kind, ctx.prefs, ctx.quiet):
                return _close(row, ReminderStatus.CANCELLED, "prefs_changed")
            expected = local_instant(slot[0], _hhmm(slot[1]), ctx.tz)
            if expected > now + timedelta(minutes=1):  # the timezone changed since it was planned
                row.remind_at = expected
                return "rescheduled"
        display = push.PushMessage(
            title=row.title, body=row.body or "", url="/today", tag=row.kind.value, kind=row.kind.value
        )
    else:  # custom
        payload = row.payload if isinstance(row.payload, dict) else {}
        display = push.PushMessage(
            title=row.title,
            body=row.body or "",
            url=str(payload.get("url") or "/today"),
            tag=f"reminder:{row.id}",
            kind=row.kind.value,
            ref_id=str(row.id),
        )

    if now - row.remind_at > STALE_AFTER:
        return await _suppress(db, row, display, "stale", now)

    if ctx.quiet.active_at(now, ctx.tz):
        if smart:  # a daily nudge is pointless after the quiet hours: drop it
            return await _suppress(db, row, display, "quiet_hours", now, quiet=True)
        await write_log(
            db,
            user_id=row.user_id,
            dedupe_key=f"reminder:{row.id}",
            kind=row.kind.value,
            title=display.title,
            body=display.body,
            payload=display.data(),
            status="deferred",
            now=now,
            scheduled_for=row.remind_at,
            reminder_id=row.id,
            error="quiet_hours",
            quiet=True,
        )
        row.remind_at = ctx.quiet.end_after(now, ctx.tz)
        row.last_error = "quiet_hours"
        return "deferred"

    if smart:
        day, at = slot if slot is not None else (ctx.today, "00:00")
        message = await SMART_BUILDERS[row.kind](db, row, ctx, day, at)
        if message is None:
            return _close(row, ReminderStatus.CANCELLED, "not_needed")
    else:
        message = display

    if not push.is_configured():
        return await _suppress(db, row, message, "push_disabled", now)
    try:
        push.vapid()
    except push.PushNotConfigured:  # keys set but unreadable: same as no keys, not a retry loop
        return await _suppress(db, row, message, "push_disabled", now)
    subscriptions = await push.active_subscriptions(db, row.user_id, now=now)
    if not subscriptions:
        return await _suppress(db, row, message, "no_subscription", now)
    if interrupted and (row.attempts or 0) >= MAX_ATTEMPTS:  # that was its last attempt
        error = "delivery_failed:interrupted"
        await _book_log(db, row.user_id, row.id, row.kind, row.remind_at, message, "failed", error, now)
        return _close(row, ReminderStatus.FAILED, error)

    message.ttl = reminder_ttl(ctx, row.remind_at, smart=smart)
    token = new_id().hex
    row.attempts = (row.attempts or 0) + 1
    row.payload = {**_without_lease(row.payload), LEASE_KEY: {"token": token, "until": push.iso_utc(now + LEASE)}}
    return Claim(
        reminder_id=row.id,
        user_id=row.user_id,
        kind=row.kind,
        remind_at=row.remind_at,
        attempts=row.attempts,
        token=token,
        message=message,
        targets=[push.Target.of(sub) for sub in subscriptions],
    )


async def _book_log(
    db: AsyncSession,
    user_id: uuid.UUID,
    reminder_id: uuid.UUID,
    kind: ReminderKind,
    remind_at: datetime,
    message: push.PushMessage,
    status: str,
    error: str | None,
    now: datetime,
    report: push.DeliveryReport | None = None,
    *,
    linked: bool = True,
) -> None:
    """The reminder's history row (``linked=False``: the reminder was deleted meanwhile).
    Its payload is what was pushed (``sent_at``/``expires_at`` only when this run sent it)."""
    await write_log(
        db,
        user_id=user_id,
        dedupe_key=f"reminder:{reminder_id}",
        kind=kind.value,
        title=message.title,
        body=message.body,
        payload=message.data(now if report is not None else None),
        status=status,
        now=now,
        scheduled_for=remind_at,
        reminder_id=reminder_id if linked else None,
        error=error,
        report=report,
    )


async def book_delivery(db: AsyncSession, claim: Claim, report: push.DeliveryReport, now: datetime) -> str:
    """Step 3 (short transaction): book the results, the reminder's status and its history row.

    Tolerant of what happened during the send: a deleted subscription is skipped, a
    deleted reminder only loses its link in the history, and a reminder the planner
    moved meanwhile keeps its new time (it is not marked sent)."""
    await push.record(db, report, now=now)
    if report.sent:
        status, error = "sent", None
    elif report.transient and claim.attempts < MAX_ATTEMPTS:
        status, error = "retrying", f"retrying:{report.summary()}"
    else:
        status, error = "failed", f"delivery_failed:{report.summary()}"
    row = await db.scalar(select(Reminder).where(Reminder.id == claim.reminder_id).with_for_update())
    lease = _lease_of(row) if row is not None else None
    if row is not None and lease is not None and lease[0] == claim.token:
        row.payload = _without_lease(row.payload)
        if row.status == ReminderStatus.PENDING and row.remind_at == claim.remind_at:
            if status == "sent":
                row.status = ReminderStatus.SENT
                row.sent_at = now
                row.last_error = None
            elif status == "retrying":
                row.last_error = error
            else:
                row.status = ReminderStatus.FAILED
                row.last_error = error
    await _book_log(
        db,
        claim.user_id,
        claim.reminder_id,
        claim.kind,
        claim.remind_at,
        claim.message,
        status,
        error,
        now,
        report,
        linked=row is not None,
    )
    return status


async def process_reminder(reminder_id: uuid.UUID, now: datetime) -> str:
    """Claim, send, book one due reminder (each step its own short transaction, the send
    in none). Returns the outcome."""
    async with session_scope() as db:
        claim = await claim_reminder(db, reminder_id, now)
    if isinstance(claim, str):
        return claim
    report = await push.send(claim.targets, claim.message, now=now)
    async with session_scope() as db:
        return await book_delivery(db, claim, report, now)


async def _process_safely(reminder_id: uuid.UUID, now: datetime) -> str:
    try:
        return await process_reminder(reminder_id, now)
    except Exception as exc:  # one broken row never stops the run
        log.exception("reminder_dispatch_failed", reminder_id=str(reminder_id), error=type(exc).__name__)
        return "error"


async def dispatch_due(now: datetime | None = None, *, limit: int = BATCH_LIMIT) -> DispatchReport:
    """Deliver every due reminder. Idempotent and safe when two runs overlap.

    Users are served side by side, each user's reminders in order, with at most
    ``DISPATCH_CONCURRENCY`` reminders in flight. ``now`` pins the clock (tests); by
    default each reminder is judged at the moment it is processed."""
    report = DispatchReport()
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(Reminder.id, Reminder.user_id)
                .where(Reminder.status == ReminderStatus.PENDING, Reminder.remind_at <= (now or utcnow()))
                .order_by(Reminder.remind_at)
                .limit(limit)
            )
        ).all()
    queues: dict[uuid.UUID, list[uuid.UUID]] = {}
    for reminder_id, user_id in rows:
        queues.setdefault(user_id, []).append(reminder_id)
    slots = asyncio.Semaphore(max(1, DISPATCH_CONCURRENCY))

    async def serve(reminder_ids: list[uuid.UUID]) -> None:
        for reminder_id in reminder_ids:
            async with slots:
                outcome = await _process_safely(reminder_id, now or utcnow())
            report.add(outcome.split(":", 1)[0])

    await asyncio.gather(*(serve(reminder_ids) for reminder_ids in queues.values()))
    return report


# --------------------------------------------------------------------------- #
# Daily planner (smart reminders)
# --------------------------------------------------------------------------- #
async def wanted_slots(db: AsyncSession, ctx: UserContext, day: date) -> list[tuple[ReminderKind, str, str]]:
    """``(kind, dedupe_key, HH:MM)`` the user's prefs ask for on ``day``."""
    prefs = ctx.prefs
    slots: list[tuple[ReminderKind, str, str]] = []
    if prefs.meal_logging:
        slots += [(ReminderKind.MEAL, f"meal:{day.isoformat()}:{at}", at) for at in prefs.meal_times]
    if prefs.water:
        slots += [(ReminderKind.WATER, f"water:{day.isoformat()}:{at}", at) for at in prefs.water_times]
    if prefs.workout and await planned_workout(db, ctx.user_id, day) is not None:
        slots.append((ReminderKind.WORKOUT, f"workout:{day.isoformat()}", prefs.workout_time))
    if prefs.evening_review:
        slots.append((ReminderKind.EVENING_REVIEW, f"evening_review:{day.isoformat()}", prefs.evening_review_time))
    if prefs.streak_at_risk:
        from app.services.gamification.streaks import NOTIFY_MIN_STREAK, has_streak

        at = streak_slot(ctx.quiet)  # the first minute from 18:00 outside the quiet hours
        if at is not None and await has_streak(db, ctx.user_id, NOTIFY_MIN_STREAK):  # nothing to protect → no row
            slots.append((ReminderKind.STREAK, f"streak:{day.isoformat()}", at))
    return slots


async def plan_for_user(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
    """Create today's smart reminders (idempotent). Returns how many rows were new."""
    ctx = await user_context(db, user_id, now)
    if ctx is None:
        return 0
    day = ctx.today
    rows = []
    for kind, key, at in await wanted_slots(db, ctx, day):
        remind_at = local_instant(day, _hhmm(at), ctx.tz)
        if remind_at <= now or ctx.quiet.active_at(remind_at, ctx.tz):
            continue
        title, body = _generic_text(kind, ctx.lang)
        rows.append(
            {
                "id": new_id(),
                "user_id": user_id,
                "kind": kind,
                "task_id": None,
                "title": title,
                "body": body,
                "payload": {"date": day.isoformat(), "time": at},
                "remind_at": remind_at,
                "lead_minutes": 0,
                "status": ReminderStatus.PENDING,
                "attempts": 0,
                "dedupe_key": key,
                "created_at": now,
                "updated_at": now,
            }
        )
    if not rows:
        return 0
    stmt = (
        pg_insert(Reminder)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
        .returning(Reminder.id)
    )
    return len((await db.execute(stmt)).all())


async def plan_daily_reminders(now: datetime | None = None) -> int:
    """Smart reminders for every user with push on (each user in their own transaction)."""
    now = now or utcnow()
    if not push.is_configured():
        return 0
    async with session_scope() as db:
        user_ids = await push.users_with_push(db)
    created = 0
    for user_id in user_ids:
        try:
            async with session_scope() as db:
                created += await plan_for_user(db, user_id, now)
        except Exception as exc:
            log.exception("reminder_planning_failed", user_id=str(user_id), error=type(exc).__name__)
    return created


async def prune_smart_reminders(
    db: AsyncSession, user_id: uuid.UUID, now: datetime, *, keep_matching_prefs: bool = True
) -> int:
    """Delete future pending smart reminders the prefs no longer ask for (or all of them,
    when the user has no push left). They were never sent, so nothing is lost; the
    planner may create the same slot again later."""
    rows = (
        await db.scalars(
            select(Reminder).where(
                Reminder.user_id == user_id,
                Reminder.status == ReminderStatus.PENDING,
                Reminder.kind.in_(tuple(SMART_KINDS)),
                Reminder.remind_at > now,
            )
        )
    ).all()
    if not rows:
        return 0
    keep: set[str] = set()
    if keep_matching_prefs:
        ctx = await user_context(db, user_id, now)
        days = {slot[0] for slot in map(_slot_of, rows) if slot is not None}
        if ctx is not None:
            for day in days:
                keep |= {key for _, key, _ in await wanted_slots(db, ctx, day)}
    doomed = [row.id for row in rows if row.dedupe_key not in keep]
    if doomed:
        await db.execute(delete(Reminder).where(Reminder.id.in_(doomed), Reminder.user_id == user_id))
    return len(doomed)


async def on_prefs_changed(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> None:
    now = now or utcnow()
    await prune_smart_reminders(db, user_id, now)
    if push.is_configured() and await push.count_active(db, user_id):
        await plan_for_user(db, user_id, now)


async def on_subscriptions_changed(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> None:
    """Push turned on → plan today right away; the last device turned off → drop pending smart rows."""
    now = now or utcnow()
    if push.is_configured() and await push.count_active(db, user_id):
        await plan_for_user(db, user_id, now)
    else:
        await prune_smart_reminders(db, user_id, now, keep_matching_prefs=False)


# --------------------------------------------------------------------------- #
# Scheduler entry points (registered in app/services/hooks.py)
# --------------------------------------------------------------------------- #
_last_planned_at: datetime | None = None


async def planner_job() -> None:
    global _last_planned_at
    _last_planned_at = utcnow()  # set first: a failing run is retried by its own schedule, not every minute
    created = await plan_daily_reminders()
    if created:
        log.info("smart_reminders_planned", created=created)


async def dispatch_job() -> None:
    # right after a (re)start the 15-minute planner has not run yet: plan first
    if _last_planned_at is None or utcnow() - _last_planned_at >= PLANNER_INTERVAL + timedelta(minutes=1):
        try:
            await planner_job()
        except Exception as exc:  # planning trouble must never hold up delivery
            log.exception("reminder_planning_failed", error=type(exc).__name__)
    report = await dispatch_due()
    if report.total:
        log.info("notifications_dispatched", **report.counts)
