# Stage 4 — Training · delivery report

> Workouts now live in the app: the exercise library you can search in
> Russian or English, templates, a weekly plan, a live logger with one tap per
> set and a rest timer, personal records, and burned calories — on their own
> card and chart, never added to the food budget.
>
> - No migration: every table (exercises, templates, weekly plan, workout logs,
>   sets, records, body metrics) was already in migration 0001.
> - No dependency changes. The burned-kcal chart is plain SVG/CSS; the training
>   screens load as their own chunks, so Today stays as light as before.

## 1. Plan that was executed

| # | Spec item | Delivered |
| --- | --- | --- |
| 1 | Exercise library UI (seeded in stage 1) | Search across ru/en names and aliases, typo-tolerant (FTS + trigram, ё = е), exact alias first («жим лёжа» → «Жим штанги лёжа»), your own exercises first, muscle filter, recent first when the field is empty. Create your own exercise (name, muscle, equipment, type, timed); archive it later. Each exercise opens its history: all-time bests, records, last sessions. |
| 2 | Workout templates | Create / edit / delete; exercises in order with sets, reps, kg (or seconds; or minutes and km for cardio) and rest; reorder; start in one tap; save any finished workout as a template. |
| 3 | Weekly plan | A template or a rest day per weekday, an override for a single date, week navigation. Today and the Train tab show the plan of the day and whether it is done. |
| 4 | Live workout logging | Rows pre-filled from last time (else the template target); one tap on ✓ logs a set (idempotent by its id, queued when offline), starts the rest timer (±15 s, skip; survives a reload). Warm-up toggle, extra sets, exercises added on the fly. Finish asks for duration and an optional feeling score. Finished workouts stay editable. Quick cardio: activity, minutes, km. |
| 5 | Personal records | Weight, estimated 1RM (Epley, 1–12 reps), volume per session, reps (bodyweight), time and distance (cardio). A record is a set that beats every *other* completed workout; the first time is the baseline. Sets that beat a best are starred while you train (computed by the server), records are listed on the summary, the Train tab and the exercise history. |
| 6 | Burned calories — separate card/chart, never food budget | Net MET estimate `(MET − 1) × kg × hours` from the latest weigh-in (else the profile weight); a 7-day chart on the Train tab, today's number on its own Today card, the workout summary. The nutrition day is provably unchanged (pytest, smoke and browser checks). |

## 2. New & changed files

```
api/app/schemas/training.py              ★ request/response models (no NaN/∞, bounded)
api/app/services/training.py             ★ search, templates, plan, workouts, sets, records, kcal
api/app/api/v1/training.py               ★ /training/* (17 write routes rate-limited per user)
api/app/api/v1/presenters.py             + training presenters (workout, plan, records, burned)
api/app/api/v1/router.py                 + mount training
api/tests/test_stage4.py                 ★ 17 tests
web/src/features/train/
├── TrainPage.tsx                        ★ today's workout, week, burned chart, templates, records, history
├── WorkoutPage.tsx                      ★ live logger / finished-workout editor, rest timer, finish + summary
├── ExercisePicker.tsx                   ★ search, muscle filter, create your own
├── ExerciseHistorySheet.tsx             ★ bests, records, sessions, archive your own
├── TemplateSheet.tsx                    ★ template editor
├── sheets.tsx                           ★ start a workout, quick cardio, plan a day
├── BurnedChart.tsx                      ★ 7-day burned-kcal bars (validated amber, one series)
├── TrainingTodayCard.tsx                ★ the Today card
├── useTraining.ts · useStart.ts · format.ts   ★ queries, set writes via the offline queue, helpers
web/src/App.tsx                          + lazy /train and /train/workout/:id
web/src/features/today/TodayPage.tsx     + training card, burned ring from real workouts
web/src/components/layout/QuickAddSheet.tsx   workout tile is live (no stage badge)
web/src/components/ui/icons.tsx          + IconStar `filled`
web/src/lib/offlineQueue.ts              + pendingFor() (read the queue for one screen)
web/src/api/{client,types}.ts · generated/schema.d.ts · lib/queryClient.ts   + training
web/src/styles/index.css                 + --chart-burn, .above-tabbar
web/src/i18n/locales/{ru,en}.json        +203 keys each (600 per locale)
scripts/ui-check.mjs                     + 21 stage-4 browser checks (112 total)
scripts/smoke.sh                         + workout end to end (20 total)
.env.example · docker-compose.yml        image names lowercase for local builds (user-reported bug)
docs/screenshots/20–25-*.png             ★ Train tab, live workout, summary, template editor, Today, chart
```

## 3. Commands

```bash
docker compose exec api python -m app.cli seed exercises   # 52 exercises; re-run is an update
# quality gates
cd api && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>
```

## 4. What to test (walkthrough)

1. **Тренировки → Новый шаблон** → name it, **Добавить упражнение** → type
   «жим лёжа» → «Жим штанги лёжа» → 3 × 10, 60 kg → **Сохранить**.
2. Tap today in **Неделя** → choose the template («Каждый вт» or «Только в эту
   дату»). The top card says the template and **Начать по плану**.
3. **Начать по плану** → three rows pre-filled. Tap ✓ on the first: the row turns
   aqua, the rest timer starts above the tab bar (−15 / +15 / Дальше). Type 62,5
   in the second row and ✓.
4. **Добавить упражнение** → «подтягивания» → reps only → 9 → ✓. In airplane mode
   the set shows a clock and «Нет сети — подход сохранится…»; back online it is sent.
5. **Завершить** → 35 min, feeling 4 → **Завершить тренировку** → summary: time,
   sets, volume, burned kcal with «Сожжённое не прибавляется к бюджету еды», new
   records if any, «Сохранить как шаблон» for ad-hoc workouts.
6. **Сегодня**: the training card says the plan is done; the burned card shows the
   kcal; the food numbers did not move.
7. **+ → Начать тренировку**: continues a running workout, starts today's plan, or
   offers templates / empty / **Кардио** (activity, minutes, km).
8. Repeat the bench a bit heavier → the set gets a star while you train, and the
   summary lists the record; **Рекорды** on the Train tab and the exercise history
   show it.

## 5. Verification evidence (this build)

```
api      174 passed (157 before + 17 stage 4) · ruff check clean · ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 27 precache entries (1271.9 KiB);
         TrainPage 22.4 kB and WorkoutPage 18.6 kB lazy chunks, main chunk 383.8 kB
i18n     600 keys per locale, complete & in sync
browser  112/112 checks green on a freshly created database (Chromium, 390×844, ru-RU), incl.
         21 for stage 4: search «жим лёжа» first hit · template via UI · plan today ·
         start by plan · pre-filled rows · rest timer · «62,5» saved · ad-hoc exercise ·
         offline set queued then sent on reconnect · finish 35 min with kcal · summary
         keeps kcal apart · history + today updated · weight record · records list ·
         cardio from quick add · chart bar · Today burned ring = API · food totals unchanged
smoke    20/20 green (stages 1–4), incl. start → replayed set → finish, food totals untouched
```

Reviews run on this branch:

- **Backend Design `security-reviewer`** (agent). Nothing blocking: every write
  and read by client id is owner-scoped, no mass assignment. Fixed from its
  should-fix list:
  - per-user write limit and quotas (sets per workout, own exercises, templates);
  - bounded datetimes and plan dates (no overflow 500s);
  - race-safe idempotent creates (savepoint + re-read instead of check-then-insert);
  - user-scoped record deletes and name lookups;
  - alias length, NaN/Infinity rejected, burned kcal capped.
- **`code-review`** — bug scan and CLAUDE.md/spec compliance (agents, local diff;
  no GitHub remote). Fixed:
  - overlapping set saves (a row stuck on "saving", a saved set dropped by an
    out-of-order response) → `mutateAsync` and one refetch per burst;
  - offline-queued sets lost on remount → read back from the queue, block Finish
    until sent, refresh on flush;
  - record dates on the UTC day → `local_day` from the server;
  - naive datetimes read two ways → always the user's wall clock;
  - two parallel starts → advisory lock; replay of a discarded workout → 404;
  - client-side record rule → the server flags sets live;
  - template edits dropped rep ranges and notes; duplicate template exercises;
  - 36 px buttons on frequent actions → 48 px; the burned ring had an invented
    target → none; quick add needed three taps to start the plan → two;
  - missing `errors.*` strings; week navigation; archiving your own exercises.
- **`frontend-design`** + **`dataviz`**: inside the existing design system; the
  burned colour (#C97A06 dark / #D97706 light) passed the palette validator for
  both surfaces; one series, today and the best day labelled, a tap per bar.
- **Playwright**: `docs/screenshots/20-train-page.png` … `25-train-burned-chart.png`.

## 6. Decisions worth knowing

- **Burned calories are net MET.** `(MET − 1) × kg × hours`: the daily target
  already contains resting energy, so counting the resting MET again would
  overstate it. Timed sets count by their own duration; strength sets share the
  rest of the session, at least 1 and at most 6 minutes per set — a workout left
  running overnight cannot burn thousands. No weight anywhere → no number.
- **Records are events, bests are computed.** A record row is written when a
  workout beats the bests of every other completed workout; bests themselves are
  always computed from the sets. Deleting a workout removes its records and the
  bests fall back automatically. Editing a finished set recomputes its records
  and calories (a manual kcal value stays manual).
- **Client ids everywhere.** Workout, set, template and exercise ids come from the
  app. Sets are upserts by id, so the offline queue may replay them in any order.
  Starting, finishing, templates and plans need the network (stage 9 extends the
  offline queue beyond sets).
- **One workout at a time.** A second start answers 409 with the running
  workout's id, and the app simply opens it.
- **Plan resolution:** a date override beats the weekday plan; weeks start on the
  day from settings (Monday by default).
- **Live record stars** are computed by the server on every set write (same rules
  as at finish), so the client never re-implements the rule.

## 7. Not in stage 4 (by design)

- **Steps and health ingest** (Shortcut, step-based calories): stage 5.
- **Workout reminders** from the weekly plan (`reminder_enabled` is stored): stage 7.
- **XP, streaks and long-range charts** (e1RM trends, monthly volume): stage 8.
- **Offline start/finish and plan edits:** stage 9; sets already work offline.
- **Found, not changed (earlier-stage code):** the shared `Sheet` close button's
  aria-label "Close" is not translated — left for the stage 9 accessibility pass.
