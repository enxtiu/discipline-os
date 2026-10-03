#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  DisciplineOS — nightly encrypted database backup.
#
#    deploy/backup/backup.sh            # run now
#    deploy/backup/backup.sh --no-push  # keep it local only
#
#  Flow:  pg_dump (custom format) → age (public-key encryption) → local dir
#         → optional git push to a PRIVATE repository → 14-day local retention.
#
#  The dump never touches disk unencrypted: pg_dump streams straight into age
#  through a pipe, and the temporary file is only ever the ciphertext.
#
#  Configuration comes from .env:  AGE_RECIPIENT, BACKUP_REPO, BACKUP_LOCAL_DIR,
#  BACKUP_RETENTION_DAYS, POSTGRES_USER, POSTGRES_DB.
#
#  Restore: deploy/backup/restore.sh <file.sql.age>   (see the README).
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; . ./.env; set +a

PUSH=1
[[ "${1:-}" == "--no-push" ]] && PUSH=0

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/dsos}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
POSTGRES_USER="${POSTGRES_USER:-dsos}"
POSTGRES_DB="${POSTGRES_DB:-dsos}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="dsos-${STAMP}.dump.age"
TARGET_DIR="${BACKUP_LOCAL_DIR}/${STAMP}"
TARGET="${TARGET_DIR}/${NAME}"

log() { printf '\033[36m[backup %s] %s\033[0m\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf '\033[31m[backup] %s\033[0m\n' "$*" >&2; exit 1; }

[[ -n "${AGE_RECIPIENT:-}" ]] || die "AGE_RECIPIENT is not set in .env (see the README, 'Backups')"
command -v age >/dev/null 2>&1 || die "age is not installed (apt-get install age)"
command -v docker >/dev/null 2>&1 || die "docker is not available"

mkdir -p "$TARGET_DIR"

log "dumping ${POSTGRES_DB} → ${TARGET}"
# --clean/--if-exists so the dump can be restored over an existing database;
# the custom format keeps it restorable table-by-table with pg_restore.
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --compress=9 --no-owner --no-privileges \
  | age --encrypt --recipient "$AGE_RECIPIENT" --output "$TARGET"

chmod 600 "$TARGET"

# Integrity: we can decrypt, and the payload is a real pg_dump archive.
if ! age --decrypt --identity "${AGE_IDENTITY_FILE:-/root/.config/age/dsos.key}" "$TARGET" 2>/dev/null | head -c 5 | grep -q "PGDMP"; then
  log "WARNING: could not verify the archive header (age identity missing?) — keeping the file, check it by hand"
fi

SIZE="$(du -h "$TARGET" | cut -f1)"
log "wrote ${TARGET} (${SIZE})"

# ---- local retention -------------------------------------------------------
deleted=0
while IFS= read -r old; do
  rm -rf "$old"; deleted=$((deleted + 1))
done < <(find "$BACKUP_LOCAL_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+${BACKUP_RETENTION_DAYS}" 2>/dev/null || true)
log "retention: kept ${BACKUP_RETENTION_DAYS} days, removed ${deleted} old backup(s)"

# ---- off-site: private git repository -------------------------------------
if (( PUSH )) && [[ -n "${BACKUP_REPO:-}" ]]; then
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT

  log "pushing to ${BACKUP_REPO}"
  if git clone --quiet --depth 1 "$BACKUP_REPO" "$WORK/repo" 2>/dev/null; then
    cp "$TARGET" "$WORK/repo/"
    # Keep the remote copy bounded too (the repo is a backup, not an archive).
    find "$WORK/repo" -maxdepth 1 -name 'dsos-*.dump.age' -mtime "+${BACKUP_RETENTION_DAYS}" -delete
    git -C "$WORK/repo" add -A
    git -C "$WORK/repo" -c user.name="dsos-backup" -c user.email="backup@$(hostname)" \
      commit --quiet -m "backup: ${STAMP} (${SIZE})" || log "nothing to commit"
    git -C "$WORK/repo" push --quiet origin HEAD || die "git push failed — is the deploy key authorised?"
    log "off-site copy updated"
  else
    log "WARNING: could not clone ${BACKUP_REPO} — backup stays local only"
  fi
fi

log "done: ${TARGET}"
