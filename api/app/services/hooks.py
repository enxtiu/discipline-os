"""Stage extension points.

Later stages must not touch the scheduler wiring: they simply register their
jobs here (or call :func:`register_job` at import time from their own module).

Stage 3  -> nightly AI usage rollup pruning
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
