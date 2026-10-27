# Stage 8 — Gamification & insight · delivery report

> A daily score (0–100) whose calorie part peaks **inside** the target zone and is 0
> below the safe floor, three rings, XP with 30 levels, streaks with freeze tokens
> (the workout streak is weekly and follows the plan — rest is part of training),
> 46 achievements and daily/weekly quests; Recharts dashboards for nutrition,
> activity, body, the planner and progress over a week, month, quarter or year; and
> the user's data out of the app — JSON, CSV for Excel, and a signed backup that a
> CLI restores. Every number is the server's, in the user's timezone; nothing pays
> for eating less, burned calories are never food.
>
> - Migration **0003** swaps the `xp_events.kind` CHECK (two new kinds, no data reshaped).
> - No dependency changes: Recharts was pinned in stage 1 (now its own chunk, loaded
>   with /stats only); signing uses the standard library (HKDF + HMAC).

## 1. Plan that was executed

| # | Spec item | Delivered |
| --- | --- | --- |
| 1 | Daily score, rings | Eight parts with weights (kcal 25, protein 15, water 10, fiber 5, activity 15, tasks 15, sleep 10, review 5; editable in settings, validated, renormalised). Parts without a target or data drop out and the rest renormalise — but nutrition with **no meals is 0, never "excluded"**. Calorie curve: 0 below the floor; floor → zone 0.25 → 1; **1 inside [max(F, 0.92·T), 1.06·T]**; then down to 0.25 at 1.35·T and 0 at 1.6·T. Today is *provisional*: below the zone it is progress, not a verdict. Rings: nutrition, activity, discipline. Stored per day, frozen after a grace day. |
| 2 | XP rules, levels | A deterministic ledger: each day's awards are a pure function of that day (dedupe key per meal, task, workout, record, project, or per day), synced both ways for today and yesterday, final afterwards; caps per day, P1–P4 points, category multipliers 0–2, never negative. Intake-tied awards (protein, fiber, kcal in zone, water) **only on a day with a logged meal at or above the floor**; meal-logging XP is for logging and never depends on the amount. 30 levels: `round(100·(L−1)^1.6/10)·10`, titles ru/en. |
| 3 | Streaks | Meal logging, water, steps, tasks, review, overall (score ≥ 60 with a meal and intake at or above the floor), and a **weekly** workout streak that follows the plan (no plan → 3 a week, a rest week is neutral). Today counts as soon as it is met; «at risk» after 18:00 local; freeze tokens +1 a week (max 2) save a missed day or week; finished days and weeks are judged against the targets and plan of their time. An evening push (stage 7 hook) protects streaks ≥ 3 — about the streak only, never about food amounts. |
| 4 | Badges | 46 achievements in 8 categories and 4 rarities, some secret (masked until unlocked), unlocked once from server metrics with permanent XP; locked ones show progress; a celebration sheet for new ones (reduced motion respected). None rewards low intake, fasting, weight-loss speed or burned calories. |
| 5 | Quests | 3 daily and 2 weekly per user and period, chosen deterministically from a 17-quest pool among those that make sense (no workout quest on a planned rest day), progress on read, an idempotent **claim**, expiry with the period. |
| 6 | Charts and dashboards (Recharts) | **/stats**: a period switcher (week, month, quarter, year; prev/next on the server's period bounds) and five sections — Питание (kcal with the target line and the zone band, days without meals as «нет записей», never 0; adherence), Активность (steps vs target, workouts per week vs plan, burned calories **in their own card**, volume per muscle group, records), Тело (weigh-ins with the 7-day mean and the 30-day change, sleep), План (tasks, time by area vs target, projects), Прогресс (score, XP, the streak calendar). Tooltips by tap, a text summary and a table for every chart, tokens for colours in both themes. **/stats/achievements**: level ladder, XP, streaks, quests, achievements. Today gets the day-score card. |
| 7 | Data export, backup download | Settings → Данные: **JSON** (everything of yours), **CSV** (one file per table, UTF-8 with BOM for Excel, formula-safe) and a **backup** zip (`data.json` + a signed `manifest.json`), streamed (constant memory), named `disciplineos-YYYY-MM-DD.json` / `…-csv.zip` / `…-backup.zip`, 6 a minute, paired devices only; no secrets ever (token hashes, pairing codes, push keys, AI payloads or provider error bodies, server paths). `python -m app.cli restore-backup <zip> [--user-id] [--dry-run]` restores into the same or an empty account in one transaction (see §6). |

## 2. New & changed files

```
api/alembic/versions/0003_gamification_xp_kinds.py   ★ xp kinds steps_target, achievement_unlocked
api/app/services/day_metrics.py              ★ per-day series in one grouped query each (shared by score, XP, stats)
api/app/services/gamification/
├── score.py · levels.py · xp.py              ★ the curve, components, rings; levels; the XP ledger
├── streaks.py · achievements.py · quests.py  ★ streaks + freezes; achievements; quests
└── engine.py · context.py                    ★ per-user run (days → streaks → achievements → quests), lock
api/app/seeds/gamification.py                 ★ 30 levels, 46 achievements, 17 quests (idempotent)
api/app/services/stats.py · export.py · restore.py   ★ stats series; streamed exports; the restore
api/app/schemas/gamification.py · stats.py     ★ response models
api/app/api/v1/gamification.py · stats.py · export.py   ★ /gamification/*, /stats/*, /export/*
api/app/api/v1/presenters.py · router.py      + stage-8 presenters and mounts
api/app/services/hooks.py                     + recompute_daily_scores, sync_xp, evaluate_achievements, rotate_quests
api/app/services/notifications.py             + the evening streak reminder (stage-7 hook)
api/app/core/security.py · config.py          + HKDF; BACKUP_SIGNING_KEY, BACKUP_MAX_DATA_BYTES
api/app/ai/providers.py                       provider rejections stored without their body
api/app/cli.py                                + restore-backup, backfill-scores
api/tests/test_stage8_{score,xp,streaks,quests,game,stats,export}.py   ★
api/tests/test_stage8_{export,restore}_hardening.py · test_stage8_review_game.py · stage8_helpers.py   ★
web/src/features/game/                        ★ day-score card, score sheet, quests, celebration, achievements
web/src/features/stats/                       ★ /stats dashboards (sections, charts, period switcher), routes
web/src/features/settings/DataSection.tsx     the three downloads, restore help; strings moved to i18n
web/src/lib/download.ts                       ★ streamed download → file (share sheet on iPhone)
web/src/features/today/TodayPage.tsx · App.tsx   the score card; /stats/*
web/vite.config.ts                            manualChunks as a function: Recharts only with /stats
web/src/api/generated/schema.d.ts             regenerated
web/src/i18n/locales/{ru,en}.json             +412 keys each (1 678 per locale)
scripts/ui-checks/stage8-game.mjs · stage8-stats.mjs   ★ 29 + 30 browser checks
scripts/smoke.sh                              + 17 the day score, a stats week, the three exports
docs/screenshots/44–51-*.png                  ★ score card, score sheet, celebration, achievements, stats, data
README.md · .env.example                      export/restore, the two backup settings
```

## 3. Commands

```bash
# quality gates
cd api && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>

# levels, achievements and quests (also seeded on first use)
docker compose exec api python -m app.cli seed gamification
# once after deploying stage 8: score and XP for the existing history, in 60-day chunks
docker compose exec api python -m app.cli backfill-scores
# restore a backup (dry run first: the same checks, nothing written)
docker compose exec api python -m app.cli restore-backup /backups/disciplineos-…-backup.zip --dry-run
```

## 4. What to test (walkthrough)

1. **Сегодня**: the day-score card — the score «предварительно», three rings, XP today and
   the level, streak flames, today's quests. Log a meal and water → the score and XP move.
2. Tap the score → every part with its weight and the server's detail; the kcal line explains
   the zone («Цель 2 080 ккал — зона 1 914–2 205 ккал»), that below the safe floor scores 0
   and that burned calories never count as food.
3. Finish a quest (e.g. water) → «Получить» → «+20 XP»; a second tap changes nothing.
4. A first meal, workout or task → the celebration sheet once; it does not come back.
5. **Статистика**: switch week / month / quarter / year, tap a day on a chart for its numbers,
   open the table view; days without meals say «нет записей»; burned calories are only in
   Активность.
6. **Статистика → Достижения**: the level ladder, streaks (with freeze tokens), quests,
   achievements (locked ones with progress, secret ones as «???»).
7. **Настройки → Данные**: download the JSON, the CSV zip (opens in Excel; in a Russian-locale
   spreadsheet switch the import locale for dot decimals) and the backup.

## 5. Verification evidence (this build)

```
api      1236 passed (1035 after stage 7 + 201 for stage 8, incl. the review's regression
         tests) · ruff check clean · ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 45 precache entries (2 131.9 KiB); lazy chunks
         StatsPage 56.7 kB, charts (Recharts) 402.5 kB — imported by StatsPage only, never
         preloaded by the main bundle, AchievementsPage 15.0 kB; main chunk 566.5 kB (+68 kB:
         the Today score card, celebration, quests and 411 strings per locale — the stage-9
         performance pass splits locales and the motion library)
i18n     1 678 keys per locale, complete & in sync
browser  325/325 checks green on a freshly created database (Chromium, 390×844, ru-RU), incl.
         59 for stage 8 (29 score/XP/quests/achievements + 30 stats/export)
smoke    24/24 green (stages 1–8), incl. 17: the day score, a stats week, the three exports
OpenAPI  web/src/api/generated/schema.d.ts regenerated from the running API: no difference
```

Reviews run on this branch: **independent backend review** (agent, 16 probes against the
running code, 8 "sound" checks kept passing) — 1 high, 3 medium, 6 low, 2 policy decisions:

- **H1** water was credited on days without any meal (XP, the water streak, quests, badges
  and the evening nudge) → one shared nutrition-credit rule (a meal logged and intake at or
  above the floor) for every nutrition reward, water included; a water quest whose amount is
  reached waits for food and says so;
- **M1** streaks and day-count badges re-judged the whole history with today's targets and
  today's weekly plan (one weigh-in wiped an 11-day water streak; adding a plan day paid an
  8-week badge) → finished days keep the targets they were finalised with (a compact copy in
  the stored score), each finished week stores its requirement; current targets apply to
  yesterday and today only, the current plan to the current week only — streaks, badges,
  quests and the stats agree;
- **M2** a crafted backup could point rows into another account (foreign keys, the meal-photo
  path) → every foreign key must point at the backup's own rows or the target's; server paths
  are never restored blindly;
- **M3** an edited backup with recomputed checksums was accepted, and the manifest's user id
  picked the target account → manifests are signed (HMAC, key from `BACKUP_SIGNING_KEY` or
  `SECRET_KEY`); an unsigned or edited backup restores only with an explicit `--user-id` into
  an **empty** account, its derived gamification rows are rebuilt by the engine, and settings
  pass the API's own validators (an unconfirmed floor below the default goes back to it);
- **L1** zip bomb → sizes and the compression ratio are checked before anything is inflated;
- **L2** malformed JSON in a restored score → refused on restore, and every reader of stored
  JSON degrades (finite numbers only, clamped) instead of answering 500;
- **L3** a timezone change re-opened finished days → finality is explicit (`final` on the
  stored day); a final day is never recomputed;
- **L4** `week_start` 2–6 ignored by gamification → one week helper (the planner's) everywhere;
- **L5** the first engine run finalised ~3 years inside one request (6–7 s) → a request
  finalises at most 60 pending days; the jobs (and `python -m app.cli backfill-scores`) finish
  the rest in 60-day transactions with row-for-row identical results;
- **L6** raw AI-provider error bodies (with a masked key fragment) reached the export → never
  stored or exported;
- **D1** (decided: keep) meal-logging rewards are for logging and never depend on the amount
  — nothing pays more for eating less, and not logging a low day is never encouraged;
- **D2** (decided: keep) a very large day scores 0 on kcal; overall-streak credit stays «a meal
  and at or above the floor».
- Notes: the streak reminder comes at the first minute from 18:00 outside the quiet hours (none
  when the whole evening is quiet); the GETs that run the engine share a per-user limit of
  120 a minute; export files are named `…-csv.zip` / `…-backup.zip`.

Screenshots (Playwright, 390×844): `docs/screenshots/44-today-score-card.png`,
`45-score-sheet.png`, `46-celebration.png`, `47-achievements.png`, `48-stats-week.png`,
`49-stats-month.png`, `50-stats-light.png`, `51-settings-data.png`.

## 6. Decisions worth knowing

- **The safe floor has one source** (`settings_service.default_floor_for`: 1500 kcal men, 1200
  women and other/unknown, or the confirmed manual value) — the score, XP, quests,
  achievements and the stats adherence all use it; exactly at the floor counts.
- **Finished days are final.** Today and yesterday are recomputed on read and by the jobs;
  a day becomes final after its grace day and is never recomputed (a later deleted meal or a
  timezone change does not rewrite history). Streaks and badges judge each finished day
  against the targets and plan of that day.
- **Quests are claimed**, not auto-paid: completing shows «Получить»; unclaimed ones expire
  with the period. Secret achievements stay masked until unlocked.
- **Stats are the server's.** Every series is computed in the user's timezone with every day
  present (nulls where nothing was logged — a chart draws «нет записей», never 0); burned
  calories live in Активность only.
- **Backups are signed.** To move to a new server with the full history (XP, scores, streaks)
  keep the same `BACKUP_SIGNING_KEY`; otherwise the backup restores into an empty account and
  the engine rebuilds the gamification data from the records.
- **Downloads on iPhone:** the installed app hands the file to the share sheet («Сохранить в
  Файлы»); desktop browsers save it directly.

## 7. Not in stage 8 (by design)

- **Charts over several years** and custom ranges: the periods are week to year (≤ 366 days).
- **Leaderboards or social features:** single user by design.
- **A level-up celebration** (achievements have one; levels show on the card).
- **Restoring from the app:** restore is an admin CLI on the server (it can overwrite data).
