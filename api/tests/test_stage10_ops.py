"""Stage 10 ops fixes found by the first real deployment (Ubuntu 24.04, images built on the server).

* ``vapid-keys`` prints a ``.env`` fragment every reader of that file can take: bash (the backup and
  health scripts ``source`` it), python-dotenv (the API) and docker compose (``env_file``);
* ``GET /api/v1/meta/health`` tells the truth about the scheduler in *every* uvicorn worker.

The deploy scripts themselves are covered in ``test_stage10_deploy.py``.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from sqlalchemy import text
from typer.testing import CliRunner

from app.cli import cli
from tests.stage10_helpers import REPO, run


# --------------------------------------------------------------------------- #
# 1. vapid-keys → a .env fragment that bash, python-dotenv and docker compose all read the same way
# --------------------------------------------------------------------------- #
def _vapid_env_lines() -> list[str]:
    result = CliRunner().invoke(cli, ["vapid-keys"])
    assert result.exit_code == 0, result.output
    return [line for line in result.output.splitlines() if line.startswith("VAPID_")]


def _write_env(tmp_path: Path, lines: list[str]) -> Path:
    """A real .env: the template with its two empty VAPID lines replaced by the CLI's lines."""
    template = (REPO / ".env.example").read_text().splitlines()
    out = [line for line in template if not line.startswith(("VAPID_PUBLIC_KEY=", "VAPID_PRIVATE_KEY="))]
    out.extend(lines)
    path = tmp_path / ".env"
    path.write_text("\n".join(out) + "\n")
    return path


def test_vapid_private_key_is_single_quoted_on_one_line() -> None:
    lines = _vapid_env_lines()
    assert [line.split("=", 1)[0] for line in lines] == ["VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"]
    private = lines[1].split("=", 1)[1]
    # the PEM has spaces: only the quotes keep «PRIVATE» from being run as a command by `. ./.env`
    assert private.startswith("'-----BEGIN PRIVATE KEY-----\\n") and private.endswith("-----END PRIVATE KEY-----\\n'")
    assert "\n" not in private and "'" not in private[1:-1]


def test_vapid_env_lines_are_read_the_same_by_bash_and_python_dotenv(tmp_path: Path) -> None:
    env = _write_env(tmp_path, _vapid_env_lines())
    expected = dotenv_values(env)["VAPID_PRIVATE_KEY"]
    assert expected and expected.startswith("-----BEGIN PRIVATE KEY-----\\n") and expected.endswith("\\n")
    assert "'" not in expected

    # deploy/backup/backup.sh, restore.sh and deploy/healthcheck.sh all start with `set -a; . ./.env`
    script = tmp_path / "source_env.sh"
    script.write_text(
        'set -euo pipefail\nset -a\n. "$1"\nset +a\nprintf %s "$VAPID_PRIVATE_KEY"\n'
        'printf "\\0%s" "$VAPID_PUBLIC_KEY"\n'
    )
    proc = run(["bash", script, env], timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", proc.stderr  # no «PRIVATE: command not found»
    private, public = proc.stdout.split("\0")
    assert private == expected
    assert public == dotenv_values(env)["VAPID_PUBLIC_KEY"]


def _compose_available() -> bool:
    return run(["docker", "compose", "version"], timeout=60).returncode == 0


@pytest.mark.skipif(not _compose_available(), reason="the docker compose plugin is not installed")
def test_vapid_env_lines_are_literal_for_docker_compose(tmp_path: Path) -> None:
    """compose keeps a single-quoted value as it is (a double-quoted one would get real line breaks)."""
    env = _write_env(tmp_path, _vapid_env_lines())
    (tmp_path / "compose.yml").write_text("services:\n  api:\n    image: busybox\n    env_file: [.env]\n")
    proc = run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "COMPOSE_PROJECT_NAME": "dsos-test"},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    environment = json.loads(proc.stdout)["services"]["api"]["environment"]
    values = dotenv_values(stream=io.StringIO(env.read_text()))
    assert environment["VAPID_PRIVATE_KEY"] == values["VAPID_PRIVATE_KEY"]
    assert environment["VAPID_PUBLIC_KEY"] == values["VAPID_PUBLIC_KEY"]


# --------------------------------------------------------------------------- #
# 4. /meta/health reports the scheduler of the deployment, whichever worker answers
# --------------------------------------------------------------------------- #
API_DIR = REPO / "api"
HEALTH = "/api/v1/meta/health"

# What the worker that wins the lock does at start-up (uvicorn --workers 2): it runs until stdin closes.
OWNER_SCRIPT = """
import asyncio, sys
from app.services.scheduler import start_scheduler, stop_scheduler

async def main():
    scheduler = await start_scheduler()
    print("READY owner" if scheduler else "READY not-owner", flush=True)
    await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
    await stop_scheduler()

asyncio.run(main())
"""


def _owner_env() -> dict[str, str]:
    from tests.conftest import TEST_DSN

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(
        DATABASE_URL=TEST_DSN, SCHEDULER_ENABLED="true", ENV="development", LOG_LEVEL="WARNING", LOG_JSON="false"
    )
    return env


@pytest.fixture
def other_worker() -> Iterator[subprocess.Popen[str]]:
    """Another process that owns the scheduler — this test process is then «the other worker».

    (A plain thread-and-pipe fixture: the tests run in their own event loops and an asyncio
    subprocess would be tied to the loop that started it.)
    """
    proc = subprocess.Popen(  # noqa: S603 - our own interpreter, a fixed script
        [sys.executable, "-c", OWNER_SCRIPT],
        cwd=API_DIR,
        env=_owner_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    output: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:  # a reader thread: mixing select() with buffered readline() loses lines
        assert proc.stdout is not None
        for line in proc.stdout:
            output.put(line.rstrip("\n"))
        output.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        seen: list[str] = []
        while True:  # the app's log lines come first; the marker says the scheduler is up
            try:
                line = output.get(timeout=60)
            except queue.Empty:
                line = None
            if line is None:
                break
            seen.append(line)
            if line.startswith("READY"):
                break
        assert seen and seen[-1] == "READY owner", (seen[-5:], proc.poll())
        yield proc
    finally:
        if proc.poll() is None:
            proc.stdin.close()  # the script stops the scheduler (and lets go of the lock) and exits
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        reader.join(timeout=5)
        proc.stdout.close()


@pytest.fixture
def scheduler_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """SCHEDULER_ENABLED=true for this test (the tests start and stop the scheduler themselves)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)


@asynccontextmanager
async def owning_the_scheduler() -> AsyncIterator[None]:
    """Start the scheduler in this process (it takes the lock) and always stop it again in this loop."""
    from app.services.scheduler import start_scheduler, stop_scheduler

    try:
        assert await start_scheduler() is not None
        yield
    finally:
        await stop_scheduler()


async def scheduler_health(client: httpx.AsyncClient) -> dict:
    response = await client.get(HEALTH)
    assert response.status_code == 200, response.text
    return response.json()["scheduler"]


async def test_a_worker_that_does_not_own_the_scheduler_reports_the_owners(
    client: httpx.AsyncClient, other_worker: subprocess.Popen[str]
) -> None:
    """The bug: with `uvicorn --workers 2` half the answers said «running: false, jobs: []»."""
    from app.services.scheduler import scheduler_status

    assert scheduler_status() == {"running": False, "jobs": []}  # this worker really has no scheduler

    report = await scheduler_health(client)
    assert report["running"] is True
    assert report["owner"] is False  # ... and it says so
    assert report["lock_held"] is True
    assert report["stale"] is False
    assert report["heartbeat_age_seconds"] is not None and report["heartbeat_age_seconds"] < 30
    assert report["started_at"]
    ids = {job["id"] for job in report["jobs"]}
    assert {"housekeeping", "scheduler_heartbeat", "dispatch_notifications", "plan_daily_reminders"} <= ids
    assert all(job["next_run_time"] for job in report["jobs"])  # none of them paused
    assert set(report["jobs"][0]) == {"id", "name", "next_run_time"}  # the shape health.sh/smoke.sh already read


async def test_a_dead_owner_is_reported_as_not_running(
    client: httpx.AsyncClient, other_worker: subprocess.Popen[str]
) -> None:
    other_worker.kill()  # SIGKILL: no chance to release anything — the database drops its connection
    await asyncio.to_thread(other_worker.wait, 20)
    for _ in range(50):  # the server notices the closed socket at once; allow for a slow machine
        report = await scheduler_health(client)
        if not report["lock_held"]:
            break
        await asyncio.sleep(0.2)
    assert report["lock_held"] is False
    assert report["running"] is False and report["jobs"] == []  # the old snapshot is not a running scheduler
    assert report["stale"] is False  # nothing is expected to heartbeat


async def test_a_stopped_heartbeat_is_reported_stale(
    client: httpx.AsyncClient, other_worker: subprocess.Popen[str], db
) -> None:
    """The owner holds the lock but no longer publishes (wedged event loop, database writes failing)."""
    await db.execute(text("UPDATE app_meta SET updated_at = now() - interval '10 minutes' WHERE key = 'scheduler'"))
    await db.commit()
    report = await scheduler_health(client)
    assert report["running"] is True and report["lock_held"] is True
    assert report["stale"] is True and report["heartbeat_age_seconds"] >= 600


async def test_a_running_scheduler_without_any_published_state_is_stale(
    client: httpx.AsyncClient, other_worker: subprocess.Popen[str], db
) -> None:
    await db.execute(text("DELETE FROM app_meta WHERE key = 'scheduler'"))
    await db.commit()
    report = await scheduler_health(client)
    assert report["running"] is True and report["jobs"] == []
    assert report["stale"] is True and report["heartbeat_age_seconds"] is None


async def test_a_second_instance_leaves_the_scheduler_to_the_lock_holder(
    other_worker: subprocess.Popen[str], scheduler_enabled: None
) -> None:
    from app.services.scheduler import scheduler_status, start_scheduler

    assert await start_scheduler() is None  # worker B: «another instance owns the scheduler»
    assert scheduler_status() == {"running": False, "jobs": []}


async def test_the_owner_reports_its_own_live_state(client: httpx.AsyncClient, scheduler_enabled: None, db) -> None:
    from app.services.scheduler import scheduler_lock_held, stop_scheduler

    assert await scheduler_lock_held(db) is False
    async with owning_the_scheduler():
        report = await scheduler_health(client)
        assert report["enabled"] is True and report["running"] is True and report["owner"] is True
        assert report["lock_held"] is True and report["stale"] is False
        assert report["heartbeat_age_seconds"] is not None and report["heartbeat_age_seconds"] < 30
        assert {"housekeeping", "scheduler_heartbeat"} <= {job["id"] for job in report["jobs"]}
        assert all(job["next_run_time"] for job in report["jobs"])
        assert await scheduler_lock_held(db) is True

        await stop_scheduler()  # a clean shutdown lets go of the lock at once
        await db.rollback()
        assert await scheduler_lock_held(db) is False
        report = await scheduler_health(client)
        assert report["running"] is False and report["lock_held"] is False and report["owner"] is False


async def test_the_lock_is_not_a_pooled_connection(scheduler_enabled: None, db) -> None:
    """A pooled connection is recycled (pool_recycle) and takes a session-level lock with it."""
    from app.db import engine
    from app.services.scheduler import scheduler_lock_held

    async with owning_the_scheduler():
        await engine.dispose()  # what recycling does to every pooled connection
        await db.rollback()
        assert await scheduler_lock_held(db) is True


async def test_the_heartbeat_takes_the_lock_again_after_a_database_restart(scheduler_enabled: None, db) -> None:
    from app.services.scheduler import run_job_now, scheduler_lock_held

    async with owning_the_scheduler():
        holder = await db.scalar(
            text(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted AND objid = 918273645 "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        )
        assert holder
        await db.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": holder})  # connection and lock die
        await db.commit()
        for _ in range(50):
            if not await scheduler_lock_held(db):
                break
            await db.rollback()
            await asyncio.sleep(0.1)
        assert await scheduler_lock_held(db) is False

        assert await run_job_now("scheduler_heartbeat") is True
        await db.rollback()
        assert await scheduler_lock_held(db) is True  # back, on a new connection


async def test_the_heartbeat_does_nothing_outside_the_owner(db) -> None:
    """`python -m app.cli run-job scheduler_heartbeat` must neither take the lock nor write a state."""
    from app.services.scheduler import run_job_now, scheduler_lock_held

    assert await run_job_now("scheduler_heartbeat") is True
    assert await scheduler_lock_held(db) is False
    assert await db.scalar(text("SELECT count(*) FROM app_meta WHERE key = 'scheduler'")) == 0


async def test_health_without_a_database_answers_from_this_process() -> None:
    from app.services.scheduler import scheduler_health as health

    report = await health(None)
    assert report["lock_held"] is None and report["running"] is False and report["jobs"] == []
    assert report["enabled"] is False and report["owner"] is False and report["stale"] is False


def test_the_health_schema_did_not_change() -> None:
    """The new keys live inside the open `scheduler` object: OpenAPI (and the generated client) stay as they were."""
    from app.main import app

    schema = app.openapi()["components"]["schemas"]["HealthOut"]
    assert sorted(schema["properties"]) == [
        "database",
        "env",
        "fuzzy_search",
        "scheduler",
        "status",
        "time_utc",
        "version",
    ]
    assert schema["properties"]["scheduler"] == {
        "anyOf": [{"additionalProperties": True, "type": "object"}, {"type": "null"}],
        "title": "Scheduler",
    }
    assert schema["required"] == ["status", "version", "env", "time_utc", "database"]
