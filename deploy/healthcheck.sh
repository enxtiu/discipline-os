#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  DisciplineOS — health probe for cron/monitoring.
#
#    */5 * * * * /opt/disciplineos/deploy/healthcheck.sh >> /var/log/dsos-health.log 2>&1
#
#  Checks: public HTTPS health JSON, container state, database reachability,
#  the scheduler actually holding a next run, disk space and certificate
#  expiry. Exit 0 = everything is fine, 1 = something needs attention.
#
#  Optional: set ALERT_WEBHOOK_URL to POST a JSON payload on failure
#  (Slack/Discord/ntfy style { text: "…" } or a generic endpoint).
# ---------------------------------------------------------------------------
set -uo pipefail

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

DOMAIN="${DOMAIN:-$(grep -oP '^DOMAIN=\K.*' .env)}"
HEALTH_URL="${HEALTH_URL:-https://${DOMAIN}/api/v1/meta/health}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
MIN_FREE_MB="${MIN_FREE_MB:-2048}"
MIN_CERT_DAYS="${MIN_CERT_DAYS:-10}"
FAILURES=()

record() { FAILURES+=("$1"); printf '\033[31m✗ %s\033[0m\n' "$1"; }
ok() { printf '\033[32m✓ %s\033[0m\n' "$1"; }

# ---- 1. public health -----------------------------------------------------
BODY="$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null || true)"
if [[ -z "$BODY" ]]; then
  record "health endpoint is unreachable: $HEALTH_URL"
else
  ok "health endpoint reachable"
  python3 - "$BODY" <<'PY' || FAILURES+=("health payload is not ok")
import json, sys
d = json.loads(sys.argv[1])
assert d.get("status") == "ok", d
assert d.get("database") is True, "database flag is false"
jobs = d.get("scheduler", {}).get("jobs") or []
assert jobs, "the scheduler has no jobs registered"
assert all(j.get("next_run_time") for j in jobs), f"a job is paused: {jobs}"
print("  status=%s db=%s jobs=%s" % (d["status"], d["database"], len(jobs)))
PY
  [[ ${#FAILURES[@]} -eq 0 ]] && ok "payload ok (database + scheduler)"
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
BACKUP_DIR="${BACKUP_LOCAL_DIR:-/var/backups/dsos}"
if [[ -d "$BACKUP_DIR" ]]; then
  newest="$(ls -1t "$BACKUP_DIR"/*.age 2>/dev/null | head -1 || true)"
  if [[ -n "$newest" ]]; then
    age_h=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
    if (( age_h <= 36 )); then
      ok "last backup ${age_h}h old ($(basename "$newest"))"
    else
      record "the newest backup is ${age_h}h old — is the backup job running?"
    fi
  else
    record "no backups found in $BACKUP_DIR"
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
