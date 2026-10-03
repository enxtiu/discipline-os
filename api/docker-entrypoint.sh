#!/usr/bin/env sh
# ---------------------------------------------------------------------------
# API container entrypoint.
#  1. wait for PostgreSQL (compose healthcheck usually wins the race already)
#  2. run migrations (idempotent; safe on every boot)
#  3. hand over to CMD (uvicorn)
# ---------------------------------------------------------------------------
set -eu

echo "[entrypoint] waiting for the database…"
python - <<'PY'
import os
import sys
import time

import psycopg

dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://")
for attempt in range(1, 61):
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            print(f"[entrypoint] database is up (attempt {attempt})")
            sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        if attempt == 60:
            print(f"[entrypoint] database unreachable: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PY

echo "[entrypoint] applying migrations…"
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
