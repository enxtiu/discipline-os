#!/usr/bin/env bash
# Local (non-Docker) API for development: expects a reachable PostgreSQL and the
# venv at $VENV (default ~/.venv). Usage: scripts/dev-api.sh [port]
set -euo pipefail
cd "$(dirname "$0")/../api"
PORT="${1:-8000}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres@127.0.0.1:55432/dsos}"
export SECRET_KEY="${SECRET_KEY:-dev-only-secret-key-please-change-0123456789}"
export MEDIA_DIR="${MEDIA_DIR:-$PWD/var/media}"   # meal thumbnails (stage 3)
exec "${VENV:-$HOME/.venv}/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT"
