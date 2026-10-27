# Stage 9 — Hardening · performance pass

> The app is used one-handed on an iPhone, often on mobile data. Measured first,
> fixed what the numbers showed, measured again — with the same tools, which stay in
> the repo. The first screen now downloads less than half the JavaScript before it can
> render, starts its API reads while the code is still downloading, and a tab switch
> no longer refetches everything; device-authenticated requests need fewer round trips
> and the gamification reads stopped writing rows that did not change.
>
> - No migration (EXPLAIN found no hot query missing an index — see §6), no dependency
>   change: the analyzers use the app's own Vite/esbuild and Playwright as before.
> - This is the performance part of stage 9; the coordinator merges it into `docs/stage-9.md`.

## 1. How it was measured

**Data.** `api/scripts/seed_year.py` fills the owner with a year of realistic history
(Europe/Moscow, onboarded): 1 329 meals with 3 990 items, 2 565 water logs, 142
workouts with 2 272 sets and 49 records, 365 days of steps and sleep, 221 weigh-ins,
4 069 tasks (subtasks, tags, reminders, three recurring series, an inbox and an
upcoming week), 970 time entries, 306 journal days — then runs the gamification engine
once over the history (365 daily scores, 6 996 XP events, 44 achievements, all seen).

**Browser.** `scripts/perf/perf-run.mjs` opens `/today`, `/food`, `/plan` and `/stats` of
the production build (`npm run build` + `vite preview`, API behind its proxy) in Chromium
with CDP CPU throttling ×4 and DevTools' *Fast 4G* profile (165 ms latency, 9 Mbit/s ×0.9
down, 1.5 Mbit/s ×0.9 up), iPhone 13 viewport, ru-RU, Europe/Moscow, a paired device:

| mark | meaning |
| --- | --- |
| **shell** | first app content painted: header, tab bar and the screen (JS ran, session known) |
| **ring** | Today only: the calorie ring painted (the day's numbers are in) |
| **ready** | the screen painted with its data: its marker present and no skeleton left |
| fcp · lcp | Chromium's first / largest contentful paint (fcp is index.html's boot spinner) |
| idle | the last request finished (then 1 s without network activity) |
| js-kB | JavaScript transferred (gzip, as served) until idle |
| block-ms | Σ (long task − 50 ms) until idle — main-thread blocking |

*cold* = a fresh browser context (empty cache, service worker blocked): the first open
on mobile data. *warm* = the installed app opened again: the service worker precached
the build on a first visit, the measured load is the second one (JS from the SW cache,
API over the network). `--tabs` then taps Food, Plan, Today, Food, Plan, Stats, Today in
the tab bar and counts the API requests of every switch.

The box is shared (2 cores, other agents' builds and browsers): all browser and API
measurements ran under `flock /home/claude/locks/ui-check.lock`, and **before and after
were measured in turn** — the baseline build (`0549984`, served by a second `vite
preview`) and this branch's build, run after run, against the same API and database, so
a load spike slows both alike (`--targets before=…,after=…`). Medians of N runs.

**API.** `api/scripts/bench_api.py` calls, in-process (httpx ASGI transport: routing,
auth, validation, the database, serialisation — everything but the socket), every read
the four screens fire when they open plus the `/stats/*` series for a week and a year:
p50 / p95 / max, this process's CPU per request, SQL statements per request and DB time,
then `EXPLAIN (ANALYZE, BUFFERS)` of the slowest SELECTs. `--compare <other api/>` runs
the baseline code and this tree in alternating rounds (child processes).

**Bundle.** `scripts/perf/bundle-report.mjs` rebuilds into a temp dir and attributes every
chunk's bytes to packages and source folders (each module's rendered code minified on
its own by the build's esbuild, the chunk split in proportion — source maps cannot,
Vite's JSON modules come with empty maps). `--check` fails when the main chunk is over
300 kB or the start-up JS contains Recharts, `src/features/stats` or framer-motion's
animation features.

## 2. Results — before → after

### 2.1 What the first screen downloads

Files as shipped (`web/dist`), raw / gzip:

| | before (`0549984`) | after |
| --- | --- | --- |
| **main chunk** (`index-*.js`) | **567.5 kB** / 173.2 kB (vite prints 540.5 kB) | **251.5 kB** / 77.4 kB |
| start-up JS + CSS (index.html, its preloads, the active locale) | 897.4 kB / 274.8 kB | 645.1 kB / 196.3 kB (−28 % / −29 %) |
| start-up chunks | index, vendor, motion (114.6 kB), charts (clsx), CSS | index, vendor, dexie, CSS, `ru` locale (preloaded) |
| loaded right after start, not needed to paint | — | motion features 96.1 kB / 31.0 kB |

Main chunk before, by source: both locales 189 kB (33 %), Dexie 75 kB, i18next 39 kB,
settings 36 kB, game 29 kB, plan 28 kB, query-core 27 kB, food 21 kB, ui 16 kB, auth
14 kB (pairing + onboarding), qrcode.react 13 kB, ai 12 kB, health 10 kB; framer-motion
(111 kB) sat in a chunk the entry imported. After: the Today screen's own code (shell,
Today, its cards and their queries), i18next, query-core and the slim `m` runtime of
framer-motion (20 kB); React and Dexie in long-cached vendor chunks.

### 2.2 Screens, throttled (CPU ×4, Fast 4G) — medians of RUNS_TBD runs, before/after in turn

SCREENS_TBD

### 2.3 Tab switches (Food → Plan → Today → Food → Plan → Stats → Today)

TABS_TBD

### 2.4 API hot paths (seeded year, in-process, alternating rounds)

API_TBD

## 3. What changed

### Frontend (`web/`)

1. **Only the active locale** (`src/i18n/index.ts`). Both locales (1 300+ keys each) were
   in the main chunk. Each is now a chunk of its own, read by a ten-line i18next backend;
   `main.tsx` renders once `i18nReady` settled — no flash of keys — and `changeLanguage`
   loads the other locale before switching (i18next waits for the backend). Russian
   stays the fallback for every language except English, which is complete
   (`check-i18n.mjs`) and so never downloads the Russian chunk as well. A small Vite
   plugin (`localePreload`) puts an inline script into the built `index.html` that
   preloads the locale the device will ask for (stored language → the browser's first
   ru/en → ru), so it downloads in parallel with the app code. JSON is emitted as
   `JSON.parse("…")` (`json.stringify`), which V8 reads faster than an object literal.
2. **framer-motion through LazyMotion** (`src/lib/motion.tsx`). Every `motion.*` is now
   `m.*` (ten files); `MotionProvider` loads `domMax` (animations, exit, tap/hover, drag
   for the sheets, layout for the planner toast) as its own chunk, requested as soon as
   the app code runs. Every animation, `MotionConfig` and the reduced-motion handling
   are unchanged; until the chunk is there an `m.*` element shows its `initial` state.
3. **Lazy screens and sheets.** Pairing, the onboarding wizard, every settings section
   (with `qrcode.react`) and the Food tab are chunks of their own (`App.tsx`, inside the
   existing spinner fallbacks); the sheets most visits never open load on first use and
   then stay mounted so their close animation plays (`src/lib/lazy.ts` → `useOnceTrue`):
   Today's log and health sheets, the AI review / barcode log / custom-food sheets of
   `AiCapture`, the score breakdown.
4. **Manual chunks as a function** (`vite.config.ts`): `vendor` (React, the router and
   the small helpers shared with other libraries), `dexie`, `charts` (Recharts and what
   only it uses). The old list form put clsx into the `charts` chunk, so the entry
   imported that chunk at start — with the stats dashboards merged it would have loaded
   all of Recharts on every screen (the stats branch fixed the same trap with a function;
   this one covers its rules). framer-motion gets no manual chunk on purpose.
5. **The first screen's reads start in index.html.** On a paired device an inline script
   requests the session, the app config and the reads of Today, Food or Plan while the
   code downloads; `api/client.ts` hands each response to the app's first GET of the same
   URL (once, within the first 10 s; everything else — errors, 401, offline — goes
   through the normal path). Today's nine reads now finish before its JS has run.
6. **No refetch storm.** The planner and health queue listeners invalidated their whole
   family (and the session) every time a screen mounted — 7 reads on every return to
   Today, 4 on Plan. One app-wide listener (`src/lib/queueRefresh.ts`) now refreshes once,
   300 ms after an offline flush settles (inactive queries are marked stale), so screens
   no longer refetch on mount "just in case"; a flush of many writes is one refresh.
   Today stopped loading the three health series its closed sheets do not show (they
   read while open), and reference lists only the app's own writes change (recipes,
   favourites, recents, templates, exercises, records, areas, projects, resources, tags,
   settings, devices, tokens, notification prefs) stay fresh for 5 minutes; live reads
   keep the 30 s default.

### API (`api/`)

7. **One round trip to authenticate.** `get_principal` loads device, user and settings in
   one statement (`User.settings` filled from the join with `contains_eager` instead of
   its own selectin query), and `get_or_create_settings` takes the row the request already
   loaded from the session. Every device request runs 2–4 statements fewer
   (`/auth/session` 5 → 1, `/nutrition/day` 6 → 3).
8. **Gamification reads stop rewriting what did not change.** Each read of
   `/gamification/today`, `/gamification/profile` and `/stats/progress` runs the engine:
   the overall-streak milestones were inserted one statement each (7 on a long streak,
   all `ON CONFLICT DO NOTHING`) and the seven streak rows were upserted even when
   identical. Milestones go in one INSERT (`xp.award_permanent_many`), the streak upsert is
   skipped when the stored rows (read under the same advisory lock) hold the values.
   `/gamification/today` 48 → 38 statements, `/gamification/profile` 50 → 40,
   `/stats/progress` 42 → 32.

Compression was already in place (Caddy `encode zstd gzip`, FastAPI `GZipMiddleware` ≥ 1 kB).

## 4. Files

```
api/scripts/seed_year.py                ★ a year of realistic history for one account (bulk, ~15 s)
api/scripts/bench_api.py                ★ p50/p95, CPU, statements, EXPLAIN; --compare two versions
scripts/perf/perf-run.mjs               ★ throttled Chromium loads, tab switches, waterfall; --targets
scripts/perf/bundle-report.mjs          ★ bundle composition by package/folder; --check budget
api/app/api/deps.py                     principal in one statement
api/app/services/settings_service.py    reuse the request's settings row
api/app/services/gamification/{engine,streaks,xp}.py   batched milestones, no identical upsert
web/index.html                          early reads of the first screen
web/vite.config.ts                      localePreload, json.stringify, manualChunks function
web/src/i18n/index.ts                   locale backend, i18nReady
web/src/main.tsx                        render after i18nReady, MotionProvider, installQueueRefresh
web/src/lib/{motion.tsx,motionFeatures.ts,lazy.ts,queueRefresh.ts}   ★ new
web/src/lib/queryClient.ts              staleTime per family
web/src/api/client.ts                   takeEarly
web/src/App.tsx                         lazy pairing, onboarding, settings, Food
web/src/features/today/TodayPage.tsx    m.div, lazy log/health sheets
web/src/features/ai/AiCapture.tsx       lazy review/log/custom-food sheets
web/src/features/game/{DayScoreCard,Celebration,queries}.tsx|ts   lazy score sheet, m, no queue listener
web/src/features/health/{useHealth,sheets}.ts(x)   series only while open, no mount refetch
web/src/features/plan/queries.ts        no mount refetch (app-wide refresh)
web/src/components/ui/{index,Toast,ProgressRing}.tsx, components/layout/{BottomTabBar,SyncStatus}.tsx,
web/src/features/auth/{PairingScreen,OnboardingWizard}.tsx, features/plan/tasks/toast.tsx   motion → m
docs/stage-9-perf.md                    this report
```

Shared files touched (other stage-9 agents): `components/ui/index.tsx` and `Toast.tsx`
(accessibility), `SyncStatus.tsx`, `TodayPage.tsx`, `api/client.ts`, `plan/queries.ts`,
`health/useHealth.ts` (offline sync) — each a small, local change; see §8.

## 5. Commands

```bash
# data: a migrated DB with the reference rows, then a year for the owner
cd api && .venv/bin/python -m app.cli import-foods --source seed && .venv/bin/python -m app.seeds.exercises \
  && .venv/bin/python -m app.cli seed && .venv/bin/python scripts/seed_year.py
# bundle
node scripts/perf/bundle-report.mjs [--check] [--json out.json]
# browser (production build behind vite preview; a second preview of an older dist to compare)
cd web && npm run build && VITE_DEV_API_TARGET=http://127.0.0.1:8000 npx vite preview --port 4173
PAIRING_CODE=XXXX-XXXX WEB_URL=http://127.0.0.1:4173 node scripts/perf/perf-run.mjs --runs 5 --tabs [--waterfall]
#   … --targets before=http://127.0.0.1:4174,after=http://127.0.0.1:4173
# API
cd api && .venv/bin/python scripts/bench_api.py --runs 30 --explain 4 [--compare ../../old/api --rounds 4]
```

## 6. EXPLAIN — and why there is no migration

GATES_TBD

## 7. Not done, and why

NOT_TBD

## 8. Merge notes (for the coordinator)

MERGE_TBD
