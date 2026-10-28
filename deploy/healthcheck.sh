#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  DisciplineOS — health probe for cron/monitoring.
#
#    */5 * * * * /opt/disciplineos/deploy/healthcheck.sh >> /var/log/dsos-health.log 2>&1
#
#  Checks: public HTTPS health JSON, container state, database reachability,
#  the scheduler (running, every job holding a next run, a fresh heartbeat, its lock
#  held), disk space, certificate expiry and the age of the newest backup.
#  Exit 0 = everything is fine, 1 = something needs attention.
#
#  The api runs several uvicorn workers and only one of them owns the scheduler;
#  /api/v1/meta/health reports the scheduler of the whole deployment from whichever
#  worker answers (its lock from pg_locks, the owner's jobs from what it publishes).
#
#  Optional: set ALERT_WEBHOOK_URL to POST a JSON payload on failure
#  (Slack/Discord/ntfy style { text: "…" } or a generic endpoint).
# ---------------------------------------------------------------------------
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
set -a
# shellcheck source=/dev/null
. ./.env
set +a

DOMAIN="${DOMAIN:-$(grep -oP '^DOMAIN=\K.*' .env)}"
HEALTH_URL="${HEALTH_URL:-https://${DOMAIN}/api/v1/meta/health}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
MIN_FREE_MB="${MIN_FREE_MB:-2048}"
MIN_CERT_DAYS="${MIN_CERT_DAYS:-10}"
FAILURES=()

record() { FAILURES+=("$1"); printf '\033[31m✗ %s\033[0m\n' "$1"; }
ok() { printf '\033[32m✓ %s\033[0m\n' "$1"; }

# ---- 1. public health -----------------------------------------------------
# Prints the one-line summary, then one "! <problem>" line per problem found.
analyze_health() {
  python3 - "$1" <<'PY'
import json
import sys

try:
    d = json.loads(sys.argv[1])
    if not isinstance(d, dict):
        raise ValueError("not an object")
except ValueError as exc:
    print(f"! the health endpoint did not answer a JSON object ({exc})")
    sys.exit(0)

bad = []
if d.get("status") != "ok":
    bad.append(f"health status is {d.get('status')!r}, not 'ok'")
if d.get("database") is not True:
    bad.append("the database flag is false")

# `scheduler` is the deployment's, whichever api worker answered. Older api versions only
# know running/jobs (and report them for the answering worker): the new keys are optional here.
sch = d.get("scheduler") or {}
jobs = sch.get("jobs") or []
running = bool(sch.get("running"))
if not running:
    if sch.get("enabled") is False:
        bad.append("the scheduler is disabled (SCHEDULER_ENABLED=false): no reminders or sync jobs run")
    elif sch.get("lock_held") is False:
        bad.append("no scheduler is running: nothing holds the scheduler lock (the api worker that owns it died?)")
    else:
        bad.append("the scheduler is not running")
else:
    if sch.get("stale"):
        age = sch.get("heartbeat_age_seconds")
        since = f"for {age} s" if age is not None else "at all"
        bad.append(f"the scheduler has not published its state {since}: it is stuck, or cannot write to the database")
    elif not jobs:
        bad.append("the scheduler has no jobs registered")
    paused = [str(j.get("id", "?")) for j in jobs if not j.get("next_run_time")]
    if paused:
        bad.append("a scheduler job is paused (no next run): " + ", ".join(paused))
    if sch.get("lock_held") is False:
        bad.append("the scheduler runs, but the lock that keeps a second one away is gone (database connection lost): restart the api")

heartbeat = sch.get("heartbeat_age_seconds")
print(
    "status=%s db=%s scheduler: running=%s jobs=%d%s%s"
    % (
        d.get("status"),
        d.get("database"),
        running,
        len(jobs),
        "" if "owner" not in sch else f" owner={sch['owner']}",
        "" if heartbeat is None else f" heartbeat={heartbeat}s",
    )
)
for line in bad:
    print("! " + line)
PY
}

BODY="$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null || true)"
if [[ -z "$BODY" ]]; then
  record "health endpoint is unreachable: $HEALTH_URL"
else
  ok "health endpoint reachable"
  if analysis="$(analyze_health "$BODY")"; then
    problems=0
    while IFS= read -r line; do
      if [[ "$line" == "! "* ]]; then
        record "${line#! }"
        problems=$((problems + 1))
      elif [[ -n "$line" ]]; then
        printf '  %s\n' "$line"
      fi
    done <<<"$analysis"
    if (( problems == 0 )); then ok "payload ok (database + scheduler)"; fi
  else
    record "could not read the health payload (is python3 installed?)"
  fi
fi

# ---- 2. containers --------------------------------------------------------
for service in postgres api caddy; do
  state="$(docker compose ps --format json "$service" 2>/dev/null | python3 -c 'import json,sys;print((json.load(sys.stdin) or {}).get("State",""))' 2>/dev/null || true)"
  if [[ "$state" == "running" ]]; then
    ok "$service container running"
  else
    record "$service container is not running (state='${state:-missing}')"
  fi
done

# ---- 3. database ----------------------------------------------------------
if docker compose exec -T postgres pg_isready -q -U "${POSTGRES_USER:-dsos}" -d "${POSTGRES_DB:-dsos}" 2>/dev/null; then
  ok "postgres accepts connections"
else
  record "postgres is not accepting connections"
fi

# ---- 4. disk --------------------------------------------------------------
free_mb="$(df -Pm / | awk 'NR==2 {print $4}')"
if (( free_mb >= MIN_FREE_MB )); then
  ok "disk free ${free_mb} MB"
else
  record "low disk space: ${free_mb} MB free (threshold ${MIN_FREE_MB} MB)"
fi

# ---- 5. certificate -------------------------------------------------------
if command -v openssl >/dev/null 2>&1; then
  expiry="$(echo | openssl s_client -servername "$DOMAIN" -connect "${DOMAIN}:443" 2>/dev/null \
    | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
  if [[ -n "$expiry" ]]; then
    days=$(( ( $(date -d "$expiry" +%s) - $(date +%s) ) / 86400 ))
    if (( days >= MIN_CERT_DAYS )); then
      ok "TLS certificate valid for ${days} more days"
    else
      record "TLS certificate expires in ${days} days — Caddy failed to renew?"
    fi
  else
    record "could not read the TLS certificate for $DOMAIN"
  fi
fi

# ---- 6. last backup age ---------------------------------------------------
# backup.sh writes <BACKUP_LOCAL_DIR>/<stamp>/dsos-<stamp>.dump.age, one directory per
# run. (restore.sh's pre-restore-*.dump.age safety dumps sit directly in the directory:
# they are not nightly backups and do not count.)
BACKUP_DIR="${BACKUP_LOCAL_DIR:-/var/backups/dsos}"
if [[ -d "$BACKUP_DIR" ]]; then
  newest=""
  newest_mtime=0
  for archive in "$BACKUP_DIR"/*/*.dump.age; do
    [[ -f "$archive" ]] || continue # the pattern matched nothing
    mtime="$(stat -c %Y "$archive")"
    if (( mtime > newest_mtime )); then
      newest="$archive"
      newest_mtime="$mtime"
    fi
  done
  if [[ -n "$newest" ]]; then
    age_h=$(( ( $(date +%s) - newest_mtime ) / 3600 ))
    if (( age_h <= 36 )); then
      ok "last backup ${age_h}h old ($(basename "$newest"))"
    else
      record "the newest backup is ${age_h}h old — is the backup job running?"
    fi
  else
    record "no backups found in $BACKUP_DIR (expected $BACKUP_DIR/<stamp>/dsos-<stamp>.dump.age)"
  fi
fi

# ---- verdict --------------------------------------------------------------
if (( ${#FAILURES[@]} > 0 )); then
  printf '\n\033[31m%d check(s) failed\033[0m\n' "${#FAILURES[@]}"
  if [[ -n "$ALERT_WEBHOOK_URL" ]]; then
    payload="$(printf '{"text":"DisciplineOS health check failed on %s: %s"}' "$(hostname)" "$(IFS='; '; echo "${FAILURES[*]}")")"
    curl -fsS -X POST -H 'Content-Type: application/json' -d "$payload" "$ALERT_WEBHOOK_URL" >/dev/null 2>&1 || true
  fi
  exit 1
fi

printf '\n\033[32mAll checks passed\033[0m\n'
