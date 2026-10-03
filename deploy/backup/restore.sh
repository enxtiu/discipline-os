#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  DisciplineOS — restore a backup produced by deploy/backup/backup.sh.
#
#    deploy/backup/restore.sh /var/backups/dsos/20261001T030000Z/dsos-….dump.age
#    deploy/backup/restore.sh backup.dump.age --into dsos_drill   # rehearsal
#
#  Safety: the script always dumps the *current* database to
#  <BACKUP_LOCAL_DIR>/pre-restore-<stamp>.dump.age first, so a mistaken restore
#  is itself recoverable. With --into it restores into a throwaway database
#  instead of the live one — do this at least once before you need it.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

FILE="${1:-}"
INTO="${POSTGRES_DB:-dsos}"
SKIP_SAFETY=0
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --into) INTO="$2"; SKIP_SAFETY=1; shift 2 ;;
    --force) SKIP_SAFETY=1; shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ -n "$FILE" ]] || { printf 'usage: %s <file.dump.age> [--into DB] [--force]\n' "$0" >&2; exit 2; }
[[ -f "$FILE" ]] || { printf '✗ no such file: %s\n' "$FILE" >&2; exit 1; }

POSTGRES_USER="${POSTGRES_USER:-dsos}"
POSTGRES_DB="${POSTGRES_DB:-dsos}"
BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/dsos}"
IDENTITY="${AGE_IDENTITY_FILE:-/root/.config/age/dsos.key}"

log() { printf '\033[36m[restore] %s\033[0m\n' "$*"; }
die() { printf '\033[31m[restore] %s\033[0m\n' "$*" >&2; exit 1; }

command -v age >/dev/null 2>&1 || die "age is not installed"
[[ -f "$IDENTITY" ]] || die "age identity not found at $IDENTITY (set AGE_IDENTITY_FILE)"

# ---- 1. safety dump of the live database ----------------------------------
if (( SKIP_SAFETY == 0 )); then
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$BACKUP_LOCAL_DIR"
  SAFETY="${BACKUP_LOCAL_DIR}/pre-restore-${STAMP}.dump.age"
  log "saving the current database to ${SAFETY}"
  docker compose exec -T postgres \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges \
    | age --encrypt --recipient "${AGE_RECIPIENT:?AGE_RECIPIENT missing in .env}" --output "$SAFETY"
  chmod 600 "$SAFETY"
else
  log "safety dump skipped (--into/--force)"
fi

# ---- 2. decrypt to stdout, pipe into pg_restore ---------------------------
log "restoring $(basename "$FILE") → ${INTO}"

docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '${INTO}'" | grep -q 1 \
  || { log "creating database ${INTO}"; docker compose exec -T postgres createdb -U "$POSTGRES_USER" "$INTO"; }

# --clean --if-exists: replace whatever is there. --no-owner: the dump is
# restored as POSTGRES_USER regardless of who created it.
age --decrypt --identity "$IDENTITY" "$FILE" \
  | docker compose exec -T postgres \
      pg_restore -U "$POSTGRES_USER" -d "$INTO" --clean --if-exists --no-owner --no-privileges \
        --single-transaction

# ---- 3. post-checks -------------------------------------------------------
log "row counts"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$INTO" -c \
  "SELECT (SELECT count(*) FROM users) AS users,
          (SELECT count(*) FROM devices) AS devices,
          (SELECT count(*) FROM exercises) AS exercises,
          (SELECT count(*) FROM foods) AS foods;"

if [[ "$INTO" == "$POSTGRES_DB" ]]; then
  log "restarting the api so it reconnects to the restored data"
  docker compose restart api >/dev/null
  sleep 5
  curl -fsS "http://127.0.0.1/api/v1/meta/health" || log "WARNING: the API did not answer after the restore"
  echo
fi

log "restore finished. Verify: python -m app.cli stats"
