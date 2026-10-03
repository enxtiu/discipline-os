"""APScheduler wiring (runs inside the API container).

Only one instance must run the scheduler. ``SCHEDULER_ENABLED`` guards it, and
the API also refuses to start a second scheduler if the lock job-store key is
taken — with a single container this is belt and braces, but it keeps the
"scale to 2 replicas later" path open.
"""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import session_scope
from app.services.hooks import STAGE_JOBS, ScheduledJobSpec
from app.services.pairing import prune_expired_codes

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None

SCHEDULER_LOCK_KEY = 918_273_645


async def _housekeeping() -> None:
    """Small periodic cleanup; safe to run concurrently."""
    async with session_scope() as db:
        revoked = await prune_expired_codes(db)
    if revoked:
        log.info("housekeeping_pruned_pairing_codes", count=revoked)


def _job_specs() -> list[ScheduledJobSpec]:
    settings = get_settings()
    specs = [
        ScheduledJobSpec(
            id="housekeeping",
            func=_housekeeping,
            minutes=max(5, settings.housekeeping_interval_minutes),
            description="Prune expired pairing codes, tidy ephemeral rows",
        )
    ]
    specs.extend(STAGE_JOBS)
    return specs


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}
    )
    for spec in _job_specs():
        if spec.cron_hour is not None:
            trigger: Any = CronTrigger(hour=spec.cron_hour, minute=spec.cron_minute or 0, timezone="UTC")
        else:
            trigger = IntervalTrigger(minutes=max(1, spec.minutes))
        # NB: do NOT pass next_run_time=None — in APScheduler that *pauses* the
        # job (next_run_time=None means "no next run"), so the trigger would
        # never fire. Interval/cron triggers compute their own first run.
        scheduler.add_job(
            spec.func,
            trigger=trigger,
            id=spec.id,
            name=spec.description or spec.id,
            replace_existing=True,
            tags=list(spec.tags),
        )
        log.debug("scheduler_job_registered", job_id=spec.id, trigger=str(trigger))
    return scheduler


async def start_scheduler() -> AsyncIOScheduler | None:
    """Start the scheduler unless disabled or another instance holds the lock."""
    global _scheduler

    settings = get_settings()
    if not settings.scheduler_enabled:
        log.info("scheduler_disabled")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    try:
        async with session_scope() as db:
            got_lock = await db.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": SCHEDULER_LOCK_KEY})
        if not got_lock:
            log.warning("scheduler_lock_not_acquired", msg="another instance owns the scheduler")
            return None
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("scheduler_lock_check_failed", error=str(exc))

    scheduler = build_scheduler()
    scheduler.start()
    _scheduler = scheduler
    log.info("scheduler_started", jobs=[job.id for job in scheduler.get_jobs()])
    return scheduler


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
    _scheduler = None


def scheduler_status() -> dict[str, Any]:
    if _scheduler is None:
        return {"running": False, "jobs": []}
    return {
        "running": _scheduler.running,
        "jobs": [
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in _scheduler.get_jobs()
        ],
    }


async def run_job_now(job_id: str) -> bool:
    """Used by the CLI (``python -m app.cli run-job housekeeping``) and tests."""
    for spec in _job_specs():
        if spec.id == job_id:
            await spec.func()
            return True
    return False
