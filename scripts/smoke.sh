#!/usr/bin/env bash
# End-to-end smoke test against a running API (stages 1–7).
#
#   API_BASE=http://127.0.0.1:8000/api scripts/smoke.sh VKT6-S6D5
#
# Needs a pairing code from `python -m app.cli create-owner` / `... pairing-code`.
# The device it pairs is revoked again at the end, so it can be re-run as long
# as a free device slot exists (default limit: 2).
# Exits non-zero on the first failed expectation. Read-only apart from one
# settings PATCH, one target preview and one API token it creates and revokes
# (stage 7: one push subscription it registers and removes, and the history row
# of the test notification it sends to a local receiver).
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

step "13a. food library search (stage 2b: seed of ~320 foods, 150 ms budget)"
split "$(api GET "/v1/foods/search?q=%D0%B1%D0%BE%D1%80%D1%89" '' "$TOKEN")"   # q=борщ
[[ "$STATUS" == "200" ]] || fail "search returned $STATUS: $BODY"
FIRST="$(parse "$BODY" "d['items'][0]['name_ru'] if d['items'] else ''")"
[[ "$FIRST" == Борщ* ]] || fail "'борщ' did not find the seed dish (got '$FIRST') — run: make seed-foods"
TOOK="$(parse "$BODY" "d['took_ms']")"
(( TOOK < 150 )) || fail "search took ${TOOK} ms (budget 150 ms)"
pass "'борщ' → $FIRST in ${TOOK} ms"

step "13b. barcode lookup reports its outcome"
split "$(api GET "/v1/foods/barcode/not-a-code" '' "$TOKEN")"
[[ "$STATUS" == "200" && "$(parse "$BODY" "d['lookup']")" == "invalid" ]] || fail "barcode: $STATUS $BODY"
split "$(api GET "/v1/foods/barcode/4600000000000?live=false" '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "barcode (local only) returned $STATUS"
pass "invalid code rejected, local lookup → $(parse "$BODY" "d['lookup']")"

step "13c. recipe yield and portion (stage 2b)"
split "$(api GET "/v1/foods/search?q=buckwheat" '' "$TOKEN")"
FOOD_ID="$(parse "$BODY" "d['items'][0]['id']")"
FOOD_KCAL="$(parse "$BODY" "d['items'][0]['kcal_per_100g']")"
split "$(api POST /v1/recipes "{\"name\":\"smoke recipe\",\"cooked_weight_g\":300,\"portion_size_g\":150,\"items\":[{\"food_id\":\"$FOOD_ID\",\"grams\":100}]}" "$TOKEN")"
[[ "$STATUS" == "201" ]] || fail "recipe create returned $STATUS: $BODY"
RECIPE_ID="$(parse "$BODY" "d['id']")"
EXPECTED="$(python3 -c "print(round($FOOD_KCAL * 150 / 300, 2))")"
GOT="$(parse "$BODY" "d['per_serving']['kcal']")"
python3 -c "import sys; sys.exit(0 if abs($GOT - $EXPECTED) < 0.05 else 1)" || fail "per-portion kcal $GOT, expected $EXPECTED"
[[ "$(parse "$BODY" "d['servings']")" == "2.0" ]] || fail "servings not derived from the portion: $BODY"
split "$(api DELETE "/v1/recipes/$RECIPE_ID" '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "recipe delete returned $STATUS"
pass "300 g cooked / 150 g portion → 2 portions × $GOT kcal (cleaned up)"

step "13d. AI logging (stage 3): availability, and a parse with the fake provider"
split "$(api GET /v1/ai/usage '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "ai usage returned $STATUS: $BODY"
PROVIDER="$(parse "$BODY" "d['provider'] or 'off'")"
if [[ "$PROVIDER" == "fake" || -n "${SMOKE_AI:-}" ]]; then
  MEALS_BEFORE="$(parse "$(sed '$d' <<<"$(api GET /v1/nutrition/day '' "$TOKEN")")" "d['logged_meals']")"
  split "$(api POST /v1/ai/parse-text '{"text":"200 g buckwheat, 2 eggs"}' "$TOKEN")"
  [[ "$STATUS" == "200" ]] || fail "parse-text returned $STATUS: $BODY"
  ITEMS="$(parse "$BODY" "len(d['items'])")"
  (( ITEMS >= 1 )) || fail "parse-text found no items: $BODY"
  MEALS_AFTER="$(parse "$(sed '$d' <<<"$(api GET /v1/nutrition/day '' "$TOKEN")")" "d['logged_meals']")"
  [[ "$MEALS_BEFORE" == "$MEALS_AFTER" ]] || fail "AI saved a meal on its own ($MEALS_BEFORE → $MEALS_AFTER)"
  pass "provider $PROVIDER: $ITEMS suggestion(s), nothing auto-saved"
else
  pass "provider $PROVIDER (set SMOKE_AI=1 to spend one real AI call)"
fi
# an oversized upload is refused before the body is read (no auth needed)
BIG_STATUS="$(head -c 4200000 /dev/zero | curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: multipart/form-data; boundary=b' --data-binary @- "$API_BASE/v1/ai/analyze-meal")"
[[ "$BIG_STATUS" == "413" ]] || fail "a 4.2 MB upload returned $BIG_STATUS, expected 413"
pass "oversized upload refused with 413 before parsing"

step "13e. training (stage 4): a workout end to end, burned kcal kept out of the food budget"
split "$(api GET '/v1/training/exercises?q=bench' '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "exercise search returned $STATUS: $BODY"
BENCH="$(parse "$BODY" "next((e['id'] for e in d['items'] if e['name_en']=='Barbell bench press'), '')")"
if [[ -z "$BENCH" ]]; then
  pass "exercise library not seeded here (python -m app.cli seed exercises) — skipped"
else
  split "$(api GET /v1/training/today '' "$TOKEN")"
  ACTIVE="$(parse "$BODY" "(d.get('active') or {}).get('id') or ''")"
  [[ -n "$ACTIVE" ]] && api DELETE "/v1/training/workouts/$ACTIVE" '' "$TOKEN" >/dev/null
  FOOD_BEFORE="$(parse "$(sed '$d' <<<"$(api GET /v1/nutrition/day '' "$TOKEN")")" "d['totals']")"
  WID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
  SID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
  split "$(api POST /v1/training/workouts "{\"id\":\"$WID\"}" "$TOKEN")"
  [[ "$STATUS" == "201" ]] || fail "start workout returned $STATUS: $BODY"
  for _ in 1 2; do  # the same set twice: idempotent by its id
    split "$(api PUT "/v1/training/workouts/$WID/sets/$SID" "{\"exercise_id\":\"$BENCH\",\"reps\":5,\"weight_kg\":40}" "$TOKEN")"
    [[ "$STATUS" == "200" ]] || fail "log set returned $STATUS: $BODY"
  done
  [[ "$(parse "$BODY" "d['total_sets']")" == "1" ]] || fail "a replayed set was counted twice"
  split "$(api POST "/v1/training/workouts/$WID/finish" '{"duration_s":1200}' "$TOKEN")"
  [[ "$STATUS" == "200" && "$(parse "$BODY" "d['status']")" == "completed" ]] || fail "finish returned $STATUS: $BODY"
  KCAL="$(parse "$BODY" "d['kcal_burned']")"
  FOOD_AFTER="$(parse "$(sed '$d' <<<"$(api GET /v1/nutrition/day '' "$TOKEN")")" "d['totals']")"
  [[ "$FOOD_BEFORE" == "$FOOD_AFTER" ]] || fail "a workout changed the food totals"
  api DELETE "/v1/training/workouts/$WID" '' "$TOKEN" >/dev/null
  pass "start → set (replayed) → finish: ${KCAL} kcal burned, food totals untouched (cleaned up)"
fi

step "13f. health ingest (stage 5): the Shortcut request, idempotent per day, never food budget"
split "$(api POST /v1/tokens '{"name":"smoke shortcut","scopes":["ingest:health"]}' "$TOKEN")"
[[ "$STATUS" == "200" || "$STATUS" == "201" ]] || fail "token create returned $STATUS: $BODY"
SHORTCUT="$(parse "$BODY" "d['token']")"
SHORTCUT_ID="$(parse "$BODY" "d['api_token']['id']")"
# a day 400 days back (the oldest the API accepts) so a real day is never touched; removed below
OLD_DAY="${OLD_DAY:-$(python3 -c 'import datetime;print(datetime.date.today()-datetime.timedelta(days=399))')}"
split "$(api GET "/v1/health/steps?days=1&end=$OLD_DAY" '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "steps read returned $STATUS: $BODY"
[[ "$(parse "$BODY" "d['days'][0]['source']")" == "None" ]] \
  || fail "$OLD_DAY already holds steps — refusing to touch real data (choose another day: OLD_DAY)"
FOOD_BEFORE="$(parse "$(sed '$d' <<<"$(api GET /v1/nutrition/day '' "$TOKEN")")" "d['totals']")"
for RUN in 1 2; do
  split "$(api POST /v1/ingest/health "{\"date\":\"$OLD_DAY\",\"steps\":\"8 342\",\"active_kcal\":\"512,5\",\"distance_km\":6.2}" "$SHORTCUT")"
  [[ "$STATUS" == "200" ]] || fail "ingest run $RUN returned $STATUS: $BODY"
done
[[ "$(parse "$BODY" "d['created']")" == "False" ]] || fail "the second run created a new day"
[[ "$(parse "$BODY" "(d['day']['steps'], d['day']['distance_m'], d['day']['active_kcal'])")" == "(8342, 6200, 512.5)" ]] \
  || fail "lenient numbers parsed wrong: $BODY"
split "$(api GET /v1/health/today '' "$SHORTCUT")"
[[ "$STATUS" == "403" ]] || fail "the ingest token could read /v1/health/today ($STATUS)"
FOOD_AFTER="$(parse "$(sed '$d' <<<"$(api GET /v1/nutrition/day '' "$TOKEN")")" "d['totals']")"
[[ "$FOOD_BEFORE" == "$FOOD_AFTER" ]] || fail "health ingest changed the food totals"
api DELETE "/v1/health/steps/$OLD_DAY" '' "$TOKEN" >/dev/null
api DELETE "/v1/tokens/$SHORTCUT_ID" '' "$TOKEN" >/dev/null
pass "ingest «8 342» / «512,5» / 6.2 km twice → one day, read access refused, food untouched (cleaned up)"

step "16. web push (stage 7): subscription → POST /push/test → local receiver → decrypt"
split "$(api GET /v1/push/config '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "push config returned $STATUS: $BODY"
if [[ "$(parse "$BODY" "d['enabled']")" != "True" ]]; then
  pass "push is not configured on this API (no VAPID keys) — skipped; generate them with: python -m app.cli vapid-keys"
else
  VAPID_PUBLIC="$(parse "$BODY" "d['vapid_public_key']")"
  PUSH_DIR="$(mktemp -d)"
  b64url() { base64 -w0 | tr '+/' '-_' | tr -d '='; }
  # the "browser": a fresh P-256 key pair and auth secret
  openssl ecparam -name prime256v1 -genkey -noout -out "$PUSH_DIR/browser.pem" 2>/dev/null
  P256DH="$(openssl ec -in "$PUSH_DIR/browser.pem" -pubout -outform DER 2>/dev/null | tail -c 65 | b64url)"
  head -c 16 /dev/urandom > "$PUSH_DIR/auth.bin"
  AUTH="$(b64url < "$PUSH_DIR/auth.bin")"
  # the "push service": a tiny local receiver that stores every POST (http://127.0.0.1 is
  # accepted as an endpoint only when the API runs with ENV=development)
  PUSH_PORT="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
  python3 - "$PUSH_PORT" "$PUSH_DIR" <<'RECEIVER' &
import base64, http.server, json, pathlib, sys

port, out = int(sys.argv[1]), pathlib.Path(sys.argv[2])

class Receiver(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        record = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                  "body": base64.b64encode(body).decode()}
        (out / f"post-{len(list(out.glob('post-*.json')))}.json").write_text(json.dumps(record))
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass

http.server.HTTPServer(("127.0.0.1", port), Receiver).serve_forever()
RECEIVER
  RECEIVER_PID=$!
  stop_receiver() { kill "$RECEIVER_PID" 2>/dev/null || true; rm -rf "$PUSH_DIR"; }
  trap stop_receiver EXIT
  for _ in $(seq 1 50); do
    python3 -c "import socket,sys;s=socket.socket();sys.exit(s.connect_ex(('127.0.0.1',$PUSH_PORT)))" && break
    sleep 0.1
  done
  ENDPOINT="http://127.0.0.1:$PUSH_PORT/push/smoke-$RANDOM$RANDOM"
  split "$(api POST /v1/push/subscriptions "{\"endpoint\":\"$ENDPOINT\",\"keys\":{\"p256dh\":\"$P256DH\",\"auth\":\"$AUTH\"}}" "$TOKEN")"
  if [[ "$STATUS" == "422" ]]; then
    stop_receiver
    trap - EXIT
    pass "this API refuses local push endpoints (ENV is not development) — skipped"
  else
  [[ "$STATUS" == "201" ]] || fail "subscribe returned $STATUS: $BODY"
  [[ "$(parse "$BODY" "d['endpoint_host']")" == "127.0.0.1" ]] || fail "subscription not bound to the receiver: $BODY"
  PUSH_SUB_ID="$(parse "$BODY" "d['id']")"
  split "$(api POST /v1/push/test '' "$TOKEN")"
  [[ "$STATUS" == "200" ]] || fail "push test returned $STATUS: $BODY"
  [[ "$(parse "$BODY" "d['sent'] >= 1")" == "True" ]] || fail "push test did not deliver: $BODY"
  for _ in $(seq 1 30); do [[ -f "$PUSH_DIR/post-0.json" ]] && break; sleep 0.1; done
  POSTS="$(find "$PUSH_DIR" -name 'post-*.json' | wc -l)"
  [[ "$POSTS" == "1" ]] || fail "the receiver got $POSTS POSTs, expected exactly one"
  python3 - "$PUSH_DIR/post-0.json" "$VAPID_PUBLIC" <<'CHECK' || fail "the push request is not an RFC 8291/8292 web push"
import json, sys
record, vapid_public = json.load(open(sys.argv[1])), sys.argv[2]
headers = record["headers"]
assert headers.get("content-encoding") == "aes128gcm", headers
auth = headers.get("authorization", "")
assert auth.startswith("vapid t=") and auth.endswith(f",k={vapid_public}"), auth[:40]
assert headers.get("ttl") == "60" and headers.get("urgency") == "high", headers
CHECK
  LANGUAGE="$(parse "$(sed '$d' <<<"$(api GET /v1/settings '' "$TOKEN")")" "d['settings']['language']")"
  EXPECTED_TITLE="$([[ "$LANGUAGE" == "en" ]] && echo "Test notification" || echo "Проверка уведомлений")"
  VENV_PY="$(dirname "$0")/../api/.venv/bin/python"
  if [[ -x "$VENV_PY" ]] && "$VENV_PY" -c 'import http_ece, cryptography' 2>/dev/null; then
    TITLE="$("$VENV_PY" - "$PUSH_DIR/post-0.json" "$PUSH_DIR/browser.pem" "$PUSH_DIR/auth.bin" <<'DECRYPT'
import base64, json, sys
import http_ece
from cryptography.hazmat.primitives.serialization import load_pem_private_key
record = json.load(open(sys.argv[1]))
key = load_pem_private_key(open(sys.argv[2], "rb").read(), password=None)
payload = json.loads(http_ece.decrypt(base64.b64decode(record["body"]), private_key=key,
                                      auth_secret=open(sys.argv[3], "rb").read(), version="aes128gcm"))
print(payload["title"])
DECRYPT
)"
    [[ "$TITLE" == "$EXPECTED_TITLE" ]] || fail "decrypted title '$TITLE', expected '$EXPECTED_TITLE'"
    DECRYPTED="decrypted with the browser key: «$TITLE»"
  else
    DECRYPTED="decryption skipped (no api/.venv with http_ece here)"
  fi
  split "$(api POST /v1/push/unsubscribe "{\"endpoint\":\"$ENDPOINT\"}" "$TOKEN")"
  [[ "$STATUS" == "204" ]] || fail "unsubscribe returned $STATUS: $BODY"
  split "$(api GET /v1/push/subscriptions '' "$TOKEN")"
  [[ "$(parse "$BODY" "any(i['id'] == '$PUSH_SUB_ID' for i in d['items'])")" == "False" ]] \
    || fail "the smoke subscription is still registered: $BODY"
  stop_receiver
  trap - EXIT
  pass "one aes128gcm POST with a VAPID header reached the receiver, $DECRYPTED; unsubscribed, receiver stopped"
  fi
fi

step "14. device self-revoke (cleanup)"
split "$(api DELETE "/v1/devices/$DEVICE_ID" '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "device revoke returned $STATUS: $BODY"
split "$(api GET /v1/auth/session '' "$TOKEN")"
[[ "$STATUS" == "401" ]] || fail "a revoked device token still works ($STATUS)"
pass "smoke device revoked, its token is dead"

printf '\n\033[32mAll smoke checks passed (stages 1–7).\033[0m\n'
