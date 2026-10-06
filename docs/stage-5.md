# Stage 5 — Health ingest · delivery report

> Steps, active energy and distance arrive from an iOS Shortcut with a token
> that can do nothing else; the same numbers can be typed in on Today. Weight
> and sleep have their own entries, a weigh-in keeps the profile (and so the
> targets) current, and a /health page shows 14 days of steps, 90 days of
> weight and the last nights on a timeline. Calories from activity stay on
> their own tile — never food budget.
>
> - No migration: `health_daily`, `body_metrics` and `sleep_logs` (with their
>   one-row-per-day unique keys) were already in migration 0001.
> - No dependency changes. Charts are plain SVG/CSS; /health is its own lazy chunk.

## 1. Plan that was executed

| # | Spec item | Delivered |
| --- | --- | --- |
| 1 | `POST /api/v1/ingest/health` — scoped token, idempotent upsert by date | Upsert by the user's local day (`ON CONFLICT` on `user_id, local_day`); fields not sent keep their value, so the 13:00 and 22:00 runs just update the day. `date` optional (default: your today), timestamps converted to your timezone, −400…+1 days. Numbers as Health formats them: `"8 342"`, `"512,5"`, `"8,342"`, `""`; `distance_km` or `distance_m`. 60 requests/min per token; the token gets 403 everywhere else. Backfill: `/ingest/health/batch` (≤ 62 days, one range query). Optional `/ingest/weight` and `/ingest/sleep` behind their own scopes. |
| 2 | The iOS Shortcut recipe | In the app (**Settings → Команда iOS**): create an ingest-only token (shown once), URL and header with copy buttons, the JSON fields, six steps, last sync. README section rewritten for the live endpoint. |
| 3 | Steps card + manual fallback | Today: steps vs target (server day and target), «Ввести шаги» sheet (today / yesterday, works offline through the queue, clear a day). The burned card shows workouts and activity as two separate numbers — Health's active energy, or `≈` an estimate from steps, height and weight — never a total, never budget. Quick add → «Шаги» is live. |
| 4 | Manual weight and sleep entries | Today → «Вес и сон»: latest weigh-in with the weekly change, last night with bed/wake times; sheets for both (decimal comma, one weigh-in per day, body fat optional; bed/wake as wall-clock times, quality 1–5, the night belongs to the wake-up day). The newest weigh-in becomes `settings.weight_kg` and the targets are recomputed (stage-1 engine, manual targets respected). |
| — | /health page | 14-day steps bars with a dashed target line (days below target dimmed — the server says which), 90-day weigh-ins with the 7-day average and the 30-day change, sleep nights on a 20:00 → 12:00 timeline with the average, sync status linking to the Shortcut setup. |

## 2. New & changed files

```
api/app/schemas/health.py                ★ ingest + manual models (lenient numbers, bounds, no NaN/∞)
api/app/services/health.py               ★ day upserts, step kcal, weight ↔ profile, sleep, last sync
api/app/api/v1/health.py                 ★ /health/* for the app (writes rate-limited per user)
api/app/api/v1/ingest.py                 ★ /ingest/* for the Shortcut (scoped token, per-token limit)
api/app/api/v1/presenters.py             + health presenters (day, series, today)
api/app/api/v1/router.py                 + mount health and ingest
api/tests/test_stage5.py                 ★ 8 tests
web/src/features/health/
├── useHealth.ts · format.ts             ★ queries, offline-queued writes, parsing/formatting
├── sheets.tsx                           ★ steps, weight, sleep sheets
├── HealthTodayCard.tsx                  ★ «Вес и сон» on Today
└── HealthPage.tsx                       ★ /health: steps bars, weight line, sleep timeline
web/src/features/settings/ShortcutSection.tsx   ★ Settings → Команда iOS
web/src/features/today/TodayPage.tsx     + real steps, two-tile burned card, health card, ?action=steps|weight|sleep
web/src/features/settings/SettingsPage.tsx · web/src/App.tsx   + routes /health, /settings/shortcut
web/src/components/layout/QuickAddSheet.tsx   steps tile is live (no stage badge)
web/src/api/{client,types}.ts · generated/schema.d.ts · lib/queryClient.ts   + health
web/src/styles/index.css                 + --chart-sleep, .chart-steps, .chart-sleep
web/src/i18n/locales/{ru,en}.json        +109 keys each (709 per locale)
scripts/ui-check.mjs                     + 23 stage-5 browser checks (135 total), cleans up its data
scripts/smoke.sh                         + 13f Shortcut ingest end to end (21 total)
docs/screenshots/26–30-*.png             ★ Shortcut setup, /health, Today, sleep sheet, timeline
README.md · docs/roadmap.md              Shortcut recipe for the live endpoint; stage 5 done, 6 next
```

## 3. Commands

```bash
# quality gates
cd api && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>

# try the Shortcut request by hand (token from Settings → Команда iOS)
curl -X POST https://<DOMAIN>/api/v1/ingest/health \
  -H "Authorization: Bearer dsos_pat_…" -H "Content-Type: application/json" \
  -d '{"steps":"8 342","active_kcal":"512,5","distance_km":"6,2"}'
```

## 4. What to test (walkthrough)

1. **Настройки → Команда iOS** → **Создать токен** → the token appears once; copy it.
   The «Адрес» and «Заголовок» lines have copy buttons.
2. Build the Shortcut from the six steps on that screen (or run the `curl` above).
   Back in the app the top line says «прислала данные только что»; **Сегодня**
   shows the steps against the target, and the burned card shows
   «Активность (Здоровье)» with Health's kcal next to the workouts.
3. Run it again: the same day is updated, nothing is duplicated.
4. **+ → Шаги** → the sheet says the number came from the Shortcut; type
   «10 000» → saved, the activity tile turns into «Шаги ≈ … ккал» (estimate).
   In airplane mode the entry says «Нет сети — сохранится…» and is sent later.
5. **Вес и сон → Вес** → «80,4» → «Вес сохранён, цели обновлены»; Settings →
   Профиль shows 80,4 kg and the new targets.
6. **Вес и сон → Прошлая ночь** → 23:30 / 07:15 → «Сон: 7 ч 45 мин».
7. **Все данные** → steps bars, the weight point(s), the night on the timeline.
8. Food numbers on Today never move through any of this.

## 5. Verification evidence (this build)

```
api      182 passed (174 before + 8 stage 5) · ruff check clean · ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 28 precache entries (1313.4 KiB);
         HealthPage 9.4 kB lazy chunk, main chunk 412.6 kB (+28.8 kB: Today sheets,
         Shortcut settings, strings)
i18n     709 keys per locale, complete & in sync
browser  135/135 checks green on a freshly created database (Chromium, 390×844, ru-RU), incl.
         23 for stage 5: setup screen · token once + URL · «8 342»/«512,5»/km ingest ·
         replay idempotent · token 403 on settings · last sync shown · Today steps vs
         target · Health kcal tile = API · quick add steps (no badge) · synced note ·
         manual 10 000 with ≈ estimate · «80,4» → profile · weight tile · sleep preview
         7 ч 45 мин · sleep saved on the wake day · offline steps queued → sent ·
         /health steps bars · latest weight · sleep timeline · food totals unchanged ·
         test data removed and profile weight restored
smoke    21/21 green (stages 1–5), incl. 13f: an empty day 400 days back is checked
         first, ingested twice (one row), read access refused, food untouched, removed
```

Reviews run on this branch:

- **Backend Design `security-reviewer`** (agent). Nothing blocking; every query is
  user-scoped and ingest tokens cannot reach app routes. Fixed from its list:
  - a per-user write limit on the app's health routes (60/min);
  - batch ingest builds its response with one range query, not one per day;
  - a scale/sleep push no longer wipes waist, note or quality typed in the app;
  - `"8,342"` is read as 8342 (en grouping), not 8.342;
  - crafted input no longer reaches a 500: weights that round onto the database
    bound, year-9999 timestamps, times with a UTC offset → 422;
  - ingest cannot label data `manual`; the profile row is locked while weigh-ins
    sync; tests for a second user, NaN and a wrong-scope token.
- **Code review** — bug scan and CLAUDE.md compliance (two agents over the stage
  diff; no GitHub remote). Fixed:
  - empty Health sums (`""`) were rejected → accepted as "no value";
  - deleting the only weigh-in left the deleted weight in the profile → 409
    `weight_last_entry` (correct it instead);
  - a «30-day change» could compare with a weigh-in from months ago → 30–45 days only;
  - the sleep timeline drew evening/afternoon bedtimes in the wrong place;
  - `active_kcal: 0` (empty sum) hid the steps estimate;
  - the Shortcut screen's «вес и сон» switch could show URLs the token can't use;
  - the app wrote to "today" from the phone clock → the server's day;
  - hard-coded strings (`≈`, placeholders, a date range, an aria-label) → i18n;
    36 px buttons → 48 px; chart bars were `button role=listitem` → a list of
    buttons; the target rule was repeated in the client → `on_target` from the server;
  - ui-check left its data behind; smoke could overwrite a real day.
- **`frontend-design`**: inside the existing design system; one new colour
  (sleep, #60A5FA dark / #2563EB light, 6.9:1 and 5.2:1 on cards), steps reuse
  the violet of the steps progress bar, weight the accent.
- **Playwright**: `docs/screenshots/26-settings-shortcut.png` … `30-health-sleep-weight.png`.

## 6. Decisions worth knowing

- **Upsert by local day, not by client UUID.** A day has exactly one row; the
  Shortcut, a batch backfill and a manual entry all write the same key, so
  replays are naturally idempotent. Unsent fields stay.
- **Manual beats synced until the next sync.** Typing steps replaces the day and
  clears the synced distance/active energy (they belonged to the old count); the
  next Shortcut run overwrites it again — the phone counted, the phone wins.
  A manual edit does not reset «last sync».
- **Calories from activity.** Health's active energy when the Shortcut sends it
  (it already includes workouts recorded by a watch — the card says so);
  otherwise `0.5 kcal × kg × km`, stride = height × 0.415 (0.75 m without a
  height). Shown on its own tile next to workouts, never summed, never budget.
- **Weight keeps the profile current.** The newest weigh-in is the profile
  weight; targets are recomputed unless set by hand. The only weigh-in can be
  corrected but not deleted (the profile needs a weight). A scale push only
  touches weight and body fat.
- **Sleep belongs to the wake-up day.** Bed time after wake time means the
  evening before. Manual entries are wall-clock times in your timezone.
- **Ingest responses** return the day as the app sees it (incl. the step-kcal
  estimate) — it is your own data and helps when debugging a Shortcut.
- **DELETE is not silently idempotent:** a second delete answers 404; the
  offline queue drops it, the button is disabled while the first one runs.

## 7. Not in stage 5 (by design)

- **Reading Apple Health directly** (HealthKit needs a native app): the Shortcut
  is the bridge; sleep and weight from Health are optional pushes.
- **Step/weight reminders and streaks:** stages 7–8 (XP never depends on eating
  below the floor).
- **Long-range charts** (months of steps, weight trend lines over a year): stage 8.
- **Found, not changed (earlier-stage code):** the stage-4 burned chart uses the
  same `button role="listitem"` pattern fixed here, and Today's «+250 мл» button
  is 36 px — both left for the stage-9 accessibility pass (Axe).
