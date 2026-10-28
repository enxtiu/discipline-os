"""APScheduler wiring (runs inside the API container).

Only one instance must run the scheduler. ``SCHEDULER_ENABLED`` guards it, and the instance that
runs it holds a PostgreSQL advisory lock: with ``uvicorn --workers 2`` (or a second replica) the
process that takes the lock owns the scheduler and the others leave it alone.

Whatever a worker that does *not* own the scheduler needs to say about it — is it running, which jobs,
when is the next run — cannot be read from its own memory, and ``GET /api/v1/meta/health`` is answered
by any worker. So the lock is read from ``pg_locks`` (:func:`scheduler_lock_held`) and the owner
publishes its jobs and next runs in ``app_meta`` (:func:`publish_status`, at start and then every
minute from the ``scheduler_heartbeat`` job); :func:`scheduler_health` puts the two together, the same
answer from every worker.

The lock is a session-level advisory lock: it lives exactly as long as the connection that took it.
That connection is therefore the scheduler's own (``_lock_conn``) and never goes back to the pool — a
pooled connection is recycled after ``pool_recycle`` seconds and the lock would silently go with it.
"""

from __future__ import annotations

import os
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.core.ids import new_id
from app.core.logging import get_logger
from app.db import engine, session_scope
from app.models import AppMeta
from app.services.hooks import STAGE_JOBS, ScheduledJobSpec
from app.services.pairing import prune_expired_codes

log = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None
_started_at: datetime | None = None
# The connection that holds the scheduler lock for as long as this process owns the scheduler.
_lock_conn: AsyncConnection | None = None

SCHEDULER_LOCK_KEY = 918_273_645
# app_meta row in which the owner publishes {pid, started_at, jobs}; ``updated_at`` is the heartbeat.
SCHEDULER_META_KEY = "scheduler"
HEARTBEAT_JOB_ID = "scheduler_heartbeat"
HEARTBEAT_MINUTES = 1
# A published state older than three missed heartbeats is stale: the owner is wedged or cannot write.
HEARTBEAT_STALE_SECONDS = 3 * 60 * HEARTBEAT_MINUTES

# A bigint key shows its high half in ``classid`` and its low half in ``objid`` (``objsubid`` 1).
_LOCK_HELD_SQL = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
    "AND classid = CAST(:hi AS oid) AND objid = CAST(:lo AS oid) AND objsubid = 1 "
    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()){extra})"
)
_HELD_HERE = " AND pid = pg_backend_pid()"  # ... and by this very connection


def _lock_params() -> dict[str, int]:
    unsigned = SCHEDULER_LOCK_KEY & 0xFFFFFFFFFFFFFFFF
    return {"hi": unsigned >> 32, "lo": unsigned & 0xFFFFFFFF}


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
        ),
        ScheduledJobSpec(
            id=HEARTBEAT_JOB_ID,
            func=_heartbeat,
            minutes=HEARTBEAT_MINUTES,
            description="Keep the scheduler lock alive and publish the jobs and next runs for /meta/health",
        ),
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


# --------------------------------------------------------------------------- #
# The lock
# --------------------------------------------------------------------------- #
async def _acquire_lock() -> bool:
    """Try to take the scheduler lock on a connection of its own. ``False``: another instance holds it."""
    global _lock_conn
    if _lock_conn is not None:
        return True
    conn = await engine.connect()
    try:
        got = await conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": SCHEDULER_LOCK_KEY})
        await conn.commit()  # no open transaction (it would pin a snapshot for the process's lifetime)
    except BaseException:
        await conn.close()
        raise
    if not got:
        await conn.close()
        return False
    _lock_conn = conn
    return True


async def _drop_lock_connection() -> None:
    """Let go of the lock connection whatever state it is in (closing it releases the lock)."""
    global _lock_conn
    conn, _lock_conn = _lock_conn, None
    if conn is None:
        return
    with suppress(Exception):  # the connection may be gone already
        await conn.invalidate()
    await conn.close()


async def _release_lock() -> None:
    global _lock_conn
    conn = _lock_conn
    if conn is None:
        return
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SCHEDULER_LOCK_KEY})
        await conn.commit()
        _lock_conn = None
        await conn.close()
    except Exception:  # pragma: no cover - the lock dies with the connection anyway
        log.warning("scheduler_lock_release_failed")
        await _drop_lock_connection()


async def _keep_lock() -> None:
    """Make sure *this* connection still holds the lock; take it again after a database restart."""
    conn = _lock_conn
    if conn is not None:
        try:
            held = bool(await conn.scalar(text(_LOCK_HELD_SQL.format(extra=_HELD_HERE)), _lock_params()))
            await conn.commit()
        except Exception as exc:
            held = False
            log.warning("scheduler_lock_connection_lost", error=type(exc).__name__)
        if held:
            return
        await _drop_lock_connection()
    try:
        if await _acquire_lock():
            log.warning("scheduler_lock_retaken")
        else:
            log.error("scheduler_lock_lost", msg="another instance holds the lock while this one still runs jobs")
    except Exception as exc:
        log.warning("scheduler_lock_retake_failed", error=type(exc).__name__)


async def scheduler_lock_held(db: AsyncSession) -> bool:
    """Is the scheduler lock held right now — by any process of this database? (``pg_locks``)"""
    return bool(await db.scalar(text(_LOCK_HELD_SQL.format(extra="")), _lock_params()))


# --------------------------------------------------------------------------- #
# Start / stop
# --------------------------------------------------------------------------- #
async def start_scheduler() -> AsyncIOScheduler | None:
    """Start the scheduler unless disabled or another instance holds the lock."""
    global _scheduler, _started_at

    settings = get_settings()
    if not settings.scheduler_enabled:
        log.info("scheduler_disabled")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    try:
        if not await _acquire_lock():
            log.warning("scheduler_lock_not_acquired", msg="another instance owns the scheduler")
            return None
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("scheduler_lock_check_failed", error=str(exc))

    scheduler = build_scheduler()
    scheduler.start()
    _scheduler = scheduler
    _started_at = datetime.now(UTC)
    log.info("scheduler_started", jobs=[job.id for job in scheduler.get_jobs()])
    await publish_status()
    return scheduler


async def stop_scheduler() -> None:
    global _scheduler, _started_at
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
    _scheduler = None
    _started_at = None
    await _release_lock()


# --------------------------------------------------------------------------- #
# What /meta/health reports
# --------------------------------------------------------------------------- #
def scheduler_status() -> dict[str, Any]:
    """This process's own scheduler (``running`` is False in a worker that does not own it)."""
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


async def publish_status() -> None:
    """Owner only: write the jobs and their next runs where every worker can read them."""
    if _scheduler is None or not _scheduler.running:
        return
    payload = {
        "pid": os.getpid(),
        "started_at": _started_at.isoformat() if _started_at else None,
        "jobs": scheduler_status()["jobs"],
    }
    stmt = pg_insert(AppMeta).values(id=new_id(), key=SCHEDULER_META_KEY, value=payload, updated_at=func.now())
    stmt = stmt.on_conflict_do_update(
        index_elements=[AppMeta.key], set_={"value": stmt.excluded.value, "updated_at": func.now()}
    )
    try:
        async with session_scope() as db:
            await db.execute(stmt)
    except Exception as exc:
        log.warning("scheduler_status_publish_failed", error=type(exc).__name__)


async def _published(db: AsyncSession) -> tuple[dict[str, Any] | None, float | None]:
    """The owner's last published state and its age in seconds (by the database's clock)."""
    row = (
        await db.execute(
            select(AppMeta.value, sa.extract("epoch", func.now() - AppMeta.updated_at)).where(
                AppMeta.key == SCHEDULER_META_KEY
            )
        )
    ).first()
    if row is None:
        return None, None
    value, age = row
    return (value if isinstance(value, dict) else None), float(age)


async def scheduler_health(db: AsyncSession | None) -> dict[str, Any]:
    """The scheduler as ``/meta/health`` reports it — the same from every worker and replica.

    ``running`` / ``jobs`` are the deployment's scheduler, not just this process's: in a worker that
    does not own it they come from the lock and the owner's published state. New, additive keys:
    ``enabled`` (SCHEDULER_ENABLED here), ``owner`` (this worker runs it), ``lock_held`` (``None`` =
    the database could not be asked), ``started_at``, ``heartbeat_age_seconds`` and ``stale`` (it
    runs but has not published for more than ``HEARTBEAT_STALE_SECONDS``). A job's ``next_run_time``
    is exact in the owner and as of the last heartbeat elsewhere.
    """
    local = scheduler_status()
    owner = bool(local["running"])
    report: dict[str, Any] = {
        "enabled": get_settings().scheduler_enabled,
        "running": owner,
        "owner": owner,
        "lock_held": None,
        "started_at": _started_at.isoformat() if owner and _started_at else None,
        "heartbeat_age_seconds": None,
        "stale": False,
        "jobs": local["jobs"],
    }
    if db is None:
        return report
    try:
        report["lock_held"] = await scheduler_lock_held(db)
        published, age = await _published(db)
    except Exception as exc:
        log.warning("scheduler_health_unavailable", error=type(exc).__name__)
        with suppress(Exception):
            await db.rollback()  # a failed statement leaves the request's transaction aborted
        return report
    if age is not None:
        report["heartbeat_age_seconds"] = round(age)
    if not owner and report["lock_held"]:
        # another worker (or replica) owns the scheduler: what it published is the answer
        report["running"] = True
        if published:
            report["jobs"] = published.get("jobs") or []
            report["started_at"] = published.get("started_at")
    report["stale"] = bool(report["running"] and (age is None or age > HEARTBEAT_STALE_SECONDS))
    return report


async def _heartbeat() -> None:
    """Job: keep the lock and publish the state (a no-op anywhere but in the owner, e.g. ``run-job``)."""
    if _scheduler is None or not _scheduler.running:
        return
    await _keep_lock()
    await publish_status()


async def run_job_now(job_id: str) -> bool:
    """Used by the CLI (``python -m app.cli run-job housekeeping``) and tests."""
    for spec in _job_specs():
        if spec.id == job_id:
            await spec.func()
            return True
    return False
