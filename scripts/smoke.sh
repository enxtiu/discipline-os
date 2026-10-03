#!/usr/bin/env bash
# End-to-end smoke test against a running API (stage 1).
#
#   API_BASE=http://127.0.0.1:8000/api scripts/smoke.sh VKT6-S6D5
#
# Needs a pairing code from `python -m app.cli create-owner` / `... pairing-code`.
# The device it pairs is revoked again at the end, so it can be re-run as long
# as a free device slot exists (default limit: 2).
# Exits non-zero on the first failed expectation. Read-only apart from one
# settings PATCH, one target preview and one API token it creates and revokes.
set -euo pipefail

API_BASE="${API_BASE:-http://127.0.0.1:8000/api}"
CODE="${1:-${PAIRING_CODE:-}}"

if [[ -z "$CODE" ]]; then
  echo "usage: API_BASE=... $0 <PAIRING-CODE>" >&2
  exit 2
fi

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }
step() { printf '\n\033[36m%s\033[0m\n' "$1"; }

# api METHOD PATH [json-body] [bearer]
api() {
  local method="$1" path="$2" body="${3:-}" token="${4:-}"
  local args=(-sS -X "$method" -H 'Content-Type: application/json' -w '\n%{http_code}')
  [[ -n "$token" ]] && args+=(-H "Authorization: Bearer $token")
  [[ -n "$body" ]] && args+=(-d "$body")
  curl "${args[@]}" "$API_BASE$path"
}

# Splits the trailing status code from the response body.
split() { BODY="$(sed '$d' <<<"$1")"; STATUS="$(tail -n1 <<<"$1")"; }

# parse <json> <python-expression over `d`>
parse() { python3 -c "import json,sys;d=json.loads(sys.stdin.read());print($2)" <<<"$1"; }

step "1. health"
split "$(api GET /v1/meta/health)"
[[ "$STATUS" == "200" ]] || fail "health returned $STATUS"
[[ "$(parse "$BODY" "d['status']")" == "ok" ]] || fail "status != ok"
[[ "$(parse "$BODY" "d['database']")" == "True" ]] || fail "database not reachable"
[[ "$(parse "$BODY" "d['scheduler']['jobs'][0]['next_run_time'] is not None")" == "True" ]] \
  || fail "scheduler job is not scheduled (next_run_time is null)"
pass "api ok · database ok · scheduler running"

step "2. authorization guard (no token)"
split "$(api GET /v1/auth/session)"
[[ "$STATUS" == "401" ]] || fail "unauthenticated session returned $STATUS"
[[ "$(parse "$BODY" "d['error']['code']")" == "unauthorized" ]] || fail "wrong error envelope"
pass "401 with the standard error envelope"

step "3. pairing"
split "$(api POST /v1/auth/pair "{\"code\":\"$CODE\",\"device\":{\"name\":\"smoke-test\",\"platform\":\"linux\",\"app_version\":\"smoke\"},\"timezone\":\"Asia/Bangkok\",\"language\":\"ru\"}")"
[[ "$STATUS" == "200" ]] || fail "pairing returned $STATUS: $BODY"
TOKEN="$(parse "$BODY" "d['token']")"
DEVICE_ID="$(parse "$BODY" "d['device']['id']")"
[[ -n "$TOKEN" ]] || fail "no device token in the response"
pass "device paired (${DEVICE_ID:0:8}…)"

step "4. reusing the same code must fail"
split "$(api POST /v1/auth/pair "{\"code\":\"$CODE\",\"device\":{\"name\":\"replay\",\"platform\":\"linux\"}}")"
# a spent code is reported as 401 invalid_pairing_code (deliberately not
# distinguishable from "never existed"), so anything 2xx would be the bug
[[ "$STATUS" == "401" || "$STATUS" == "400" || "$STATUS" == "409" || "$STATUS" == "410" ]] \
  || fail "a spent pairing code was accepted again ($STATUS)"
pass "single-use pairing code enforced ($STATUS)"

step "5. session"
split "$(api GET /v1/auth/session '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "session returned $STATUS: $BODY"
[[ "$(parse "$BODY" "d['auth_kind']")" == "device" ]] || fail "auth_kind != device"
[[ -n "$(parse "$BODY" "d['settings']['timezone']")" ]] || fail "no settings row on the session"
[[ "$(parse "$BODY" "isinstance(d['targets'], dict)")" == "True" ]] || fail "no target block on the session"
pass "session ok (auth_kind=device, settings + targets attached)"

step "6. target preview (Mifflin-St Jeor, male 30y 180cm 80kg, light)"
split "$(api POST /v1/settings/targets/preview '{"sex":"male","age_years":30,"height_cm":180,"weight_kg":80,"activity_level":"light","goal_mode":"lose","goal_rate_pct":15}' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "preview returned $STATUS: $BODY"
BMR="$(parse "$BODY" "d['bmr_kcal']")"
TDEE="$(parse "$BODY" "d['tdee_kcal']")"
TARGET="$(parse "$BODY" "d['target_kcal']")"
FLOOR="$(parse "$BODY" "d['safe_floor_kcal']")"
[[ "$BMR" -ge 1700 && "$BMR" -le 1850 ]] || fail "unexpected BMR $BMR (expected ~1780)"
[[ "$TARGET" -lt "$TDEE" ]] || fail "a deficit must land below TDEE ($TARGET >= $TDEE)"
[[ "$TARGET" -ge "$FLOOR" ]] || fail "preview target $TARGET is below the safe floor $FLOOR"
pass "bmr=$BMR tdee=$TDEE target=$TARGET floor=$FLOOR"

step "7. aggressive deficit must warn"
split "$(api POST /v1/settings/targets/preview '{"sex":"female","age_years":30,"height_cm":160,"weight_kg":50,"activity_level":"sedentary","goal_mode":"lose","goal_rate_pct":30}' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "preview returned $STATUS"
[[ "$(parse "$BODY" "len(d['warnings']) > 0")" == "True" ]] \
  || fail "a 30% deficit at 50 kg produced no warning"
pass "warnings: $(parse "$BODY" "' | '.join(d['warnings'])[:80]")"

step "8. onboarding completes"
split "$(api POST /v1/settings/onboarding '{"language":"ru","timezone":"Asia/Bangkok","sex":"male","birth_date":"1995-06-01","height_cm":180,"weight_kg":80,"activity_level":"light","goal_mode":"lose","goal_rate_pct":15,"target_steps":9000,"quiet_hours_start":"23:00","quiet_hours_end":"07:00"}' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "onboarding returned $STATUS: $BODY"
[[ "$(parse "$BODY" "d['settings']['onboarding_completed']")" == "True" ]] || fail "onboarding not marked complete"
pass "targets saved: $(parse "$BODY" "d['targets']['target_kcal']") kcal, $(parse "$BODY" "d['targets']['target_water_ml']") ml water, $(parse "$BODY" "d['targets']['target_steps']") steps"

step "9. settings patch"
split "$(api PATCH /v1/settings '{"language":"en","target_steps":12000,"notification_prefs":{"meals":false}}' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "settings patch returned $STATUS: $BODY"
[[ "$(parse "$BODY" "d['settings']['language']")" == "en" ]] || fail "language not updated"
[[ "$(parse "$BODY" "d['settings']['target_steps']")" == "12000" ]] || fail "target_steps not updated"
pass "language=en · target_steps=12000"

step "10. scoped API token (iOS Shortcut)"
split "$(api POST /v1/tokens '{"name":"Shortcut steps","scopes":["ingest:health"]}' "$TOKEN")"
[[ "$STATUS" == "200" || "$STATUS" == "201" ]] || fail "token create returned $STATUS: $BODY"
INGEST_TOKEN="$(parse "$BODY" "d['token']")"
TOKEN_ID="$(parse "$BODY" "d['api_token']['id']")"
pass "issued ingest token ${TOKEN_ID:0:8}…"

step "11. scoped token cannot read the planner"
split "$(api GET /v1/settings '' "$INGEST_TOKEN")"
[[ "$STATUS" == "403" ]] || fail "scoped token reached /v1/settings ($STATUS)"
pass "scope enforced (403)"

step "12. revoking the token"
split "$(api DELETE "/v1/tokens/$TOKEN_ID" '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "revoke returned $STATUS"
split "$(api GET /v1/settings '' "$INGEST_TOKEN")"
[[ "$STATUS" == "401" || "$STATUS" == "403" ]] || fail "revoked token still works ($STATUS)"
pass "revoked token rejected"

step "13. devices"
split "$(api GET /v1/devices '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "device list returned $STATUS"
pass "$(parse "$BODY" "len(d['devices'])") device(s) registered"

step "14. device self-revoke (cleanup)"
split "$(api DELETE "/v1/devices/$DEVICE_ID" '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "device revoke returned $STATUS: $BODY"
split "$(api GET /v1/auth/session '' "$TOKEN")"
[[ "$STATUS" == "401" ]] || fail "a revoked device token still works ($STATUS)"
pass "smoke device revoked, its token is dead"

printf '\n\033[32mAll stage-1 smoke checks passed.\033[0m\n'
