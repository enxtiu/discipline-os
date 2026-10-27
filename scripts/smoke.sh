#!/usr/bin/env bash
# End-to-end smoke test against a running API (stages 1–6).
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

step "15. planner (stage 6): quick add, a recurring task's next instance, timer, journal, calendar"
new_uuid() { python3 -c 'import uuid;print(uuid.uuid4())'; }
shift_day() { python3 -c "import datetime,sys;print(datetime.date.fromisoformat(sys.argv[1])+datetime.timedelta(days=int(sys.argv[2])))" "$1" "$2"; }
split "$(api GET /v1/planner/counts '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "planner counts returned $STATUS: $BODY"
P_TODAY="$(parse "$BODY" "d['date']")"   # the server's today in the user's timezone
SMOKE_TAG="smoke$(python3 -c 'import secrets;print(secrets.token_hex(3))')"
# quick add, replayed with the same id: one task. Dates far in the future — no real day is touched.
QUICK_ID="$(new_uuid)"
for RUN in 1 2; do
  split "$(api POST /v1/planner/tasks/quick "{\"id\":\"$QUICK_ID\",\"text\":\"smoke-test 2099-05-12 18:00 #$SMOKE_TAG p2\"}" "$TOKEN")"
  [[ "$STATUS" == "201" ]] || fail "quick add run $RUN returned $STATUS: $BODY"
done
[[ "$(parse "$BODY" "(d['task']['id'], d['task']['source'])")" == "('$QUICK_ID', 'quick_add')" ]] || fail "quick add: $BODY"
P_TOKENS="$(parse "$BODY" "len(d['parsed']['tokens'])")"
if (( P_TOKENS > 0 )); then
  [[ "$(parse "$BODY" "(d['task']['due_date'], d['task']['due_time'], d['task']['priority'], [t['name'] for t in d['task']['tags']])")" \
    == "('2099-05-12', '18:00', 2, ['$SMOKE_TAG'])" ]] || fail "quick add parsed wrong: $BODY"
fi
# a recurring task: completing it twice creates exactly one next instance
FAR_DAY="$(shift_day "$P_TODAY" 700)"
SERIES_ID="$(new_uuid)"
split "$(api POST /v1/planner/tasks "{\"id\":\"$SERIES_ID\",\"title\":\"smoke-test daily\",\"due_date\":\"$FAR_DAY\",\"due_time\":\"07:00\",\"recurrence_rule\":\"daily\"}" "$TOKEN")"
[[ "$STATUS" == "201" && "$(parse "$BODY" "d['recurrence_preset']")" == "daily" ]] || fail "recurring task: $STATUS $BODY"
for RUN in 1 2; do
  split "$(api POST "/v1/planner/tasks/$SERIES_ID/complete" '{}' "$TOKEN")"
  [[ "$STATUS" == "200" ]] || fail "complete run $RUN returned $STATUS: $BODY"
done
NEXT_ID="$(parse "$BODY" "d['next_instance']['id']")"
[[ "$(parse "$BODY" "(d['task']['status'], d['next_instance']['due_date'], d['next_instance']['due_time'], d['next_instance']['series_index'])")" \
  == "('done', '$(shift_day "$FAR_DAY" 1)', '07:00', 1)" ]] || fail "next instance wrong: $BODY"
# the timer: only when none is running (a real timer is never switched off)
split "$(api GET /v1/planner/timer '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "timer read returned $STATUS: $BODY"
if [[ "$(parse "$BODY" "d['running'] is None")" == "True" ]]; then
  TIMER_ID="$(new_uuid)"
  STARTED_AT="$(python3 -c 'import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=2)).isoformat())')"
  split "$(api POST /v1/planner/timer/start "{\"id\":\"$TIMER_ID\",\"description\":\"smoke-test\",\"started_at\":\"$STARTED_AT\"}" "$TOKEN")"
  [[ "$STATUS" == "200" && "$(parse "$BODY" "d['running']['entry']['id']")" == "$TIMER_ID" ]] || fail "timer start: $STATUS $BODY"
  split "$(api POST /v1/planner/timer/stop "{\"entry_id\":\"$TIMER_ID\"}" "$TOKEN")"
  [[ "$STATUS" == "200" ]] || fail "timer stop returned $STATUS: $BODY"
  TIMER_S="$(parse "$BODY" "d['stopped']['duration_s']")"
  (( TIMER_S >= 115 )) || fail "the timer ran ${TIMER_S}s, expected about 120"
  split "$(api POST /v1/planner/timer/stop '{}' "$TOKEN")"
  [[ "$STATUS" == "200" && "$(parse "$BODY" "d['stopped'] is None")" == "True" ]] || fail "a second stop is not a no-op: $BODY"
  split "$(api DELETE "/v1/planner/time-entries/$TIMER_ID" '' "$TOKEN")"
  [[ "$STATUS" == "204" ]] || fail "time entry delete returned $STATUS"
  TIMER_NOTE="timer start → stop ${TIMER_S}s"
else
  TIMER_NOTE="a timer is running — left alone"
fi
# today's journal: written only when the day has no entry yet, then removed again
split "$(api GET "/v1/planner/journal/$P_TODAY" '' "$TOKEN")"
[[ "$STATUS" == "200" && "$(parse "$BODY" "d['day']['date']")" == "$P_TODAY" ]] || fail "journal read: $STATUS $BODY"
if [[ "$(parse "$BODY" "d['entry'] is None")" == "True" ]]; then
  JOURNAL_ID="$(new_uuid)"
  split "$(api PUT "/v1/planner/journal/$P_TODAY" "{\"id\":\"$JOURNAL_ID\",\"note_md\":\"smoke-test\",\"rating\":4}" "$TOKEN")"
  [[ "$STATUS" == "200" && "$(parse "$BODY" "d['id']")" == "$JOURNAL_ID" ]] || fail "journal write: $STATUS $BODY"
  split "$(api PUT "/v1/planner/journal/$P_TODAY" '{"mood":3}' "$TOKEN")"
  [[ "$(parse "$BODY" "(d['note_md'], d['rating'], d['mood'])")" == "('smoke-test', 4, 3)" ]] || fail "journal upsert lost fields: $BODY"
  split "$(api DELETE "/v1/planner/journal/$P_TODAY" '' "$TOKEN")"
  [[ "$STATUS" == "204" ]] || fail "journal delete returned $STATUS"
  JOURNAL_NOTE="journal written, merged, removed"
else
  JOURNAL_NOTE="today already has a journal entry — read only"
fi
# a calendar range read, and the range limit
split "$(api GET "/v1/planner/views/calendar?from=$P_TODAY&to=$(shift_day "$P_TODAY" 6)" '' "$TOKEN")"
[[ "$STATUS" == "200" && "$(parse "$BODY" "len(d['days'])")" == "7" ]] || fail "calendar returned $STATUS: $BODY"
split "$(api GET "/v1/planner/views/calendar?from=$P_TODAY&to=$(shift_day "$P_TODAY" 62)" '' "$TOKEN")"
[[ "$STATUS" == "422" && "$(parse "$BODY" "d['error']['code']")" == "range_too_large" ]] || fail "63 days were accepted ($STATUS)"
# clean up everything this step created
for TASK_ID in "$QUICK_ID" "$SERIES_ID" "$NEXT_ID"; do
  split "$(api DELETE "/v1/planner/tasks/$TASK_ID" '' "$TOKEN")"
  [[ "$STATUS" == "204" ]] || fail "cleanup of task $TASK_ID returned $STATUS"
done
split "$(api GET /v1/planner/tags '' "$TOKEN")"
SMOKE_TAG_ID="$(parse "$BODY" "next((t['id'] for t in d['items'] if t['name'] == '$SMOKE_TAG'), '')")"
if [[ -n "$SMOKE_TAG_ID" ]]; then
  split "$(api DELETE "/v1/planner/tags/$SMOKE_TAG_ID" '' "$TOKEN")"
  [[ "$STATUS" == "204" ]] || fail "cleanup of tag $SMOKE_TAG returned $STATUS"
fi
split "$(api GET "/v1/planner/tasks/$QUICK_ID" '' "$TOKEN")"
[[ "$STATUS" == "404" ]] || fail "the smoke task survived the cleanup ($STATUS)"
pass "quick add (${P_TOKENS} tokens, replayed once) · next instance on $(shift_day "$FAR_DAY" 1) · ${TIMER_NOTE} · ${JOURNAL_NOTE} · calendar 7 days, 63 refused (cleaned up)"

step "16. device self-revoke (cleanup)"
split "$(api DELETE "/v1/devices/$DEVICE_ID" '' "$TOKEN")"
[[ "$STATUS" == "200" ]] || fail "device revoke returned $STATUS: $BODY"
split "$(api GET /v1/auth/session '' "$TOKEN")"
[[ "$STATUS" == "401" ]] || fail "a revoked device token still works ($STATUS)"
pass "smoke device revoked, its token is dead"

printf '\n\033[32mAll smoke checks passed (stages 1–6).\033[0m\n'
