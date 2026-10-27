"""Stage extension points.

Later stages must not touch the scheduler wiring: they simply register their
jobs here (or call :func:`register_job` at import time from their own module).

Stage 3  -> ``ai_housekeeping`` (nightly): unsaved photo thumbnails, old AI payloads
Stage 5  -> nothing (the Shortcut pushes; no polling needed)
Stage 7  -> ``dispatch_notifications`` (every minute), ``plan_daily_reminders``
Stage 8  -> ``recompute_daily_score``, ``evaluate_achievements``, quest rotation
Stage 2  -> periodic Open Food Facts delta import
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass(slots=True)
class ScheduledJobSpec:
    id: str
    func: Callable[[], Awaitable[None]]
    minutes: int
    description: str = ""
    run_on_start: bool = False
    # fires at a fixed local-ish UTC time instead of an interval
    cron_hour: int | None = None
    cron_minute: int | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


STAGE_JOBS: list[ScheduledJobSpec] = []


def register_job(spec: ScheduledJobSpec) -> None:
    if any(existing.id == spec.id for existing in STAGE_JOBS):
        return
    STAGE_JOBS.append(spec)


def registered_job_ids() -> list[str]:
    return [spec.id for spec in STAGE_JOBS]


# --------------------------------------------------------------------------- #
# stage 3: AI logging
# --------------------------------------------------------------------------- #
async def _ai_housekeeping() -> None:
    from app.services.ai_meal import ai_housekeeping

    await ai_housekeeping()


register_job(
    ScheduledJobSpec(
        id="ai_housekeeping",
        func=_ai_housekeeping,
        minutes=24 * 60,
        description="Delete unsaved meal-photo thumbnails, prune old AI request/response payloads",
        cron_hour=20,  # 03:40 Asia/Bangkok — the quiet hours of the default timezone
        cron_minute=40,
        tags=("stage3", "ai"),
    )
)


# --------------------------------------------------------------------------- #
# stage 7: notifications
# --------------------------------------------------------------------------- #
async def _dispatch_notifications() -> None:
    from app.services.notifications import dispatch_job

    await dispatch_job()


async def _plan_daily_reminders() -> None:
    from app.services.notifications import planner_job

    await planner_job()


register_job(
    ScheduledJobSpec(
        id="dispatch_notifications",
        func=_dispatch_notifications,
        minutes=1,
        description="Deliver due reminders by web push (quiet hours, prefs, conditions at send time)",
        tags=("stage7", "push"),
    )
)
register_job(
    ScheduledJobSpec(
        id="plan_daily_reminders",
        func=_plan_daily_reminders,
        minutes=15,
        description="Create today's smart reminders (meal, water, workout, evening review) per user",
        tags=("stage7", "push"),
    )
)
