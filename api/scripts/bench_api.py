#!/usr/bin/env python
"""p50/p95 of the API reads the app fires when a screen opens — on the seeded year
(``scripts/seed_year.py``), for the stage-9 performance pass.

    cd api && DATABASE_URL=postgresql+psycopg://…/dsos_s9perf SECRET_KEY=… \\
        .venv/bin/python scripts/bench_api.py [--runs 40] [--explain 4] [--only today] [--json out.json]

In-process (httpx ASGI transport): routing, auth, validation, the database and
serialisation — everything but the socket. Requests are sequential; every endpoint gets
three warm-up requests, then ``--runs`` timed ones. Per endpoint: p50, p95 and max
(ms), the SQL statements per request and the time spent waiting for the database. Then
``EXPLAIN (ANALYZE, BUFFERS)`` of the slowest SELECTs of the slowest endpoints (each in a
rolled-back transaction). The screens and their endpoints are what the browser perf run
(``scripts/perf/perf-run.mjs``) records when each screen opens, plus the /stats series.

On a busy machine the tail is noise; to compare two versions fairly, run them alternately:
``--compare /path/to/other/api --rounds 4`` runs this tree and the other one in turns
(child processes, ``--runs`` requests per endpoint and round) and prints both side by side.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# `app` from api/ — or from another checkout's api/ (`--app-dir`, to compare two versions
# under the same machine load: run them alternately)
_APP_DIR = (
    sys.argv[sys.argv.index("--app-dir") + 1] if "--app-dir" in sys.argv else str(Path(__file__).resolve().parents[1])
)
sys.path.insert(0, _APP_DIR)
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import httpx  # noqa: E402
from sqlalchemy import event, select, text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User, UserSettings  # noqa: E402
from app.services.pairing import create_pairing_code  # noqa: E402

SCREENS: dict[str, list[str]] = {
    "today": [
        "/v1/auth/session",
        "/v1/meta/app-config",
        "/v1/nutrition/day?date={today}",
        "/v1/recipes",
        "/v1/training/today",
        "/v1/health/today",
        "/v1/health/steps?days=7",
        "/v1/health/weight?days=90",
        "/v1/health/sleep?days=14",
        "/v1/planner/views/today",
        "/v1/planner/timer",
        "/v1/gamification/today",
    ],
    "food": ["/v1/foods/recents", "/v1/foods/favorites"],
    "plan": ["/v1/planner/counts", "/v1/planner/views/inbox", "/v1/planner/views/upcoming", "/v1/planner/areas"],
    "stats": [
        "/v1/stats/nutrition?period=week",
        "/v1/stats/activity?period=week",
        "/v1/stats/body?period=week",
        "/v1/stats/planner?period=week",
        "/v1/stats/progress?period=week",
        "/v1/stats/nutrition?period=year",
        "/v1/stats/activity?period=year",
        "/v1/stats/body?period=year",
        "/v1/stats/planner?period=year",
        "/v1/stats/progress?period=year",
        "/v1/gamification/profile",
    ],
}


@dataclass
class Statement:
    sql: str
    params: Any
    ms: float


@dataclass
class Result:
    path: str
    screen: str
    ms: list[float] = field(default_factory=list)
    cpu_ms: list[float] = field(default_factory=list)  # this process's CPU (app + client), load-insensitive
    statements: list[int] = field(default_factory=list)
    db_ms: list[float] = field(default_factory=list)
    slowest: dict[str, Statement] = field(default_factory=dict)  # normalised SQL → slowest occurrence
    status: int = 0
    size: int = 0

    def pct(self, q: float) -> float:
        values = sorted(self.ms)
        return values[min(len(values) - 1, max(0, round(q * (len(values) - 1))))]


CAPTURE: list[Statement] | None = None


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("bench_t0", []).append(time.perf_counter())


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after(conn, cursor, statement, parameters, context, executemany):
    started = conn.info["bench_t0"].pop()
    if CAPTURE is not None:
        CAPTURE.append(
            Statement(statement, None if executemany else parameters, (time.perf_counter() - started) * 1000)
        )


def normalise(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


async def device_token(client: httpx.AsyncClient) -> tuple[str, str]:
    """Pairs a bench device (reused by fingerprint) and returns (token, today in the user's zone)."""
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalars().first()
        if user is None:
            raise SystemExit("✗ no owner — run scripts/seed_year.py first")
        issued = await create_pairing_code(db, user, ttl_minutes=5, created_by="bench")
        tz = (await db.execute(select(UserSettings.timezone).where(UserSettings.user_id == user.id))).scalar_one()
        await db.commit()
    response = await client.post(
        "/api/v1/auth/pair",
        json={
            "code": issued.code,
            "device": {"name": "API bench", "platform": "web", "fingerprint": "fp_api_bench"},
            "timezone": tz,
            "language": "ru",
        },
    )
    response.raise_for_status()
    return response.json()["token"], datetime.now(ZoneInfo(tz)).date().isoformat()


async def bench(client: httpx.AsyncClient, screen: str, path: str, runs: int) -> Result:
    global CAPTURE
    result = Result(path=path, screen=screen)
    for _ in range(3):
        warm = await client.get(f"/api{path}")
        if warm.status_code >= 400:
            raise SystemExit(f"✗ {path}: HTTP {warm.status_code} {warm.text[:300]}")
    for _ in range(runs):
        CAPTURE = []
        started, cpu_started = time.perf_counter(), time.process_time()
        response = await client.get(f"/api{path}")
        elapsed = (time.perf_counter() - started) * 1000
        result.cpu_ms.append((time.process_time() - cpu_started) * 1000)
        captured, CAPTURE = CAPTURE, None
        result.ms.append(elapsed)
        result.statements.append(len(captured))
        result.db_ms.append(sum(item.ms for item in captured))
        result.status = response.status_code
        result.size = len(response.content)
        for item in captured:
            key = normalise(item.sql)
            if key not in result.slowest or item.ms > result.slowest[key].ms:
                result.slowest[key] = item
    return result


async def explain(item: Statement) -> str:
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            rows = await conn.exec_driver_sql(f"EXPLAIN (ANALYZE, BUFFERS) {item.sql}", item.params)
            return "\n".join(row[0] for row in rows)
        finally:
            await transaction.rollback()


def compare(args: argparse.Namespace) -> int:
    """This tree vs ``--compare`` in alternating child processes; p50/p95 over all rounds."""
    import subprocess
    import tempfile

    here = str(Path(__file__).resolve().parents[1])
    versions = {"this": here, "other": str(Path(args.compare).resolve())}
    samples: dict[str, dict[str, list[float]]] = {name: {} for name in versions}
    cpu: dict[str, dict[str, list[float]]] = {name: {} for name in versions}
    statements: dict[str, dict[str, float]] = {name: {} for name in versions}
    for round_no in range(args.rounds):
        for name, app_dir in versions.items():
            with tempfile.NamedTemporaryFile(suffix=".json") as out:
                command = [sys.executable, __file__, "--app-dir", app_dir, "--runs", str(args.runs), "--explain", "0"]
                command += ["--json", out.name] + (["--only", args.only] if args.only else [])
                subprocess.run(command, check=True, capture_output=True)  # noqa: S603 - our own script
                for row in json.loads(Path(out.name).read_text()):
                    samples[name].setdefault(row["path"], []).extend(row["ms"])
                    cpu[name].setdefault(row["path"], []).extend(row["cpu_ms"])
                    statements[name][row["path"]] = row["sql_per_request"]
        print(f"round {round_no + 1}/{args.rounds} done", file=sys.stderr)

    def pct(values: list[float], q: float) -> float:
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]

    print(f"{args.rounds} alternating rounds x {args.runs} requests; ms (this = {here}, other = {args.compare})")
    print(
        f"{'endpoint':42} {'other p50':>9} {'p95':>7} {'cpu':>6} {'sql':>4}   {'this p50':>9} {'p95':>7} {'cpu':>6}"
        f" {'sql':>4}"
    )
    for path in samples["this"]:
        this, other = samples["this"][path], samples["other"].get(path, [0.0])
        this_cpu, other_cpu = cpu["this"][path], cpu["other"].get(path, [0.0])
        print(
            f"{path:42.42} {pct(other, 0.5):9.1f} {pct(other, 0.95):7.1f} {pct(other_cpu, 0.5):6.1f}"
            f" {statements['other'].get(path, 0):4.0f}   {pct(this, 0.5):9.1f} {pct(this, 0.95):7.1f}"
            f" {pct(this_cpu, 0.5):6.1f} {statements['this'][path]:4.0f}"
        )
    if args.json:
        summary = {
            name: {
                path: {
                    "p50": round(pct(values, 0.5), 1),
                    "p95": round(pct(values, 0.95), 1),
                    "cpu_p50": round(pct(cpu[name][path], 0.5), 1),
                    "sql": statements[name][path],
                    "n": len(values),
                }
                for path, values in rows.items()
            }
            for name, rows in samples.items()
        }
        Path(args.json).write_text(json.dumps({"versions": versions, "summary": summary}, indent=2))
    return 0


def write_json(path: str, results: list[Result]) -> None:
    rows = [
        {
            "screen": item.screen,
            "path": item.path,
            "p50": round(item.pct(0.5), 1),
            "p95": round(item.pct(0.95), 1),
            "max": round(max(item.ms), 1),
            "sql_per_request": statistics.median(item.statements),
            "db_ms_p50": round(statistics.median(item.db_ms), 1),
            "cpu_ms_p50": round(statistics.median(item.cpu_ms), 1),
            "bytes": item.size,
            "ms": [round(value, 2) for value in item.ms],
            "cpu_ms": [round(value, 2) for value in item.cpu_ms],
        }
        for item in results
    ]
    Path(path).write_text(json.dumps(rows, indent=2))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--explain", type=int, default=4, help="EXPLAIN the slowest SELECTs of the N slowest endpoints")
    parser.add_argument("--only", default="", help="comma-separated screens (today,food,plan,stats)")
    parser.add_argument("--json", default="", help="write the numbers to this file")
    parser.add_argument("--app-dir", default="", help="import `app` from this api/ directory instead")
    parser.add_argument("--compare", default="", help="another api/ directory to run alternately with this one")
    parser.add_argument("--rounds", type=int, default=4, help="rounds per version with --compare")
    args = parser.parse_args()
    if args.compare:
        return compare(args)
    screens = [name for name in SCREENS if not args.only or name in args.only.split(",")]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
        token, today = await device_token(client)
        client.headers["Authorization"] = f"Bearer {token}"
        async with engine.connect() as conn:
            sizes = (
                await conn.execute(
                    text(
                        "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE n_live_tup > 300 "
                        "ORDER BY n_live_tup DESC"
                    )
                )
            ).all()
        print("rows: " + ", ".join(f"{name}={count}" for name, count in sizes))
        results: list[Result] = []
        for screen in screens:
            for path in SCREENS[screen]:
                results.append(await bench(client, screen, path.format(today=today), args.runs))

    print(f"\n{args.runs} timed requests each (after 3 warm-up), in-process; ms")
    print(
        f"{'screen':6} {'endpoint':42} {'p50':>7} {'p95':>7} {'max':>7} {'cpu p50':>8} {'sql/req':>8} {'db p50':>7}"
        f" {'kB':>6}"
    )
    for item in results:
        print(
            f"{item.screen:6} {item.path:42.42} {item.pct(0.5):7.1f} {item.pct(0.95):7.1f} {max(item.ms):7.1f} "
            f"{statistics.median(item.cpu_ms):8.1f} {statistics.median(item.statements):8.0f} "
            f"{statistics.median(item.db_ms):7.1f} {item.size / 1000:6.1f}"
        )
    over = [item for item in results if item.pct(0.95) > 100]
    print(f"\nabove 100 ms p95: {', '.join(item.path for item in over) or 'none'}")

    slow = sorted(results, key=lambda item: item.pct(0.95), reverse=True)[: args.explain]
    for item in slow:
        selects = [s for s in item.slowest.values() if s.sql.lstrip().upper().startswith(("SELECT", "WITH"))]
        for statement in sorted(selects, key=lambda s: s.ms, reverse=True)[:2]:
            print(f"\n=== {item.path} — slowest SELECT ({statement.ms:.1f} ms in the run)")
            print(normalise(statement.sql)[:700])
            print(await explain(statement))

    if args.json:
        write_json(args.json, results)
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
