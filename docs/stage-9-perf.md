# Stage 9 — Hardening · performance pass

> The app is used one-handed on an iPhone, often on mobile data. Measured first,
> fixed what the numbers showed, measured again — with the same tools, which stay in
> the repo. The main chunk is less than half its old size, the first screen starts its
> API reads while the code is still downloading, a tab switch no longer refetches
> everything and the Food and Plan tabs' code is loaded before their first tap;
> device-authenticated requests need fewer round trips and the gamification reads
> stopped writing rows that did not change.
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
the tab bar, counts the API requests of every switch and times it in the page: from the
tap event to the first frame painted with the new screen ready.

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
| **main chunk** (`index-*.js`) | **567.5 kB** / 173.2 kB (vite prints 540.5 kB) | **257.5 kB** / 79.1 kB (−55 %) |
| start-up JS + CSS (index.html, its preloads, the active locale) | 897.4 kB / 274.8 kB | 651.1 kB / 198.1 kB (−27 % / −28 %) |
| start-up chunks | index, vendor, motion (114.6 kB), charts (clsx), CSS | index, vendor, dexie, CSS, `ru` locale (preloaded) |
| loaded right after start, not needed to paint | — | motion features 96.1 kB / 31.0 kB |
| loaded when the app is idle (≥ 0.5 s after the shell) | — | Food 13.6 / 4.0 kB, Plan 53.2 / 13.4 kB |

Main chunk before, by source: both locales 189 kB (33 %), Dexie 75 kB, i18next 39 kB,
settings 36 kB, game 29 kB, plan 28 kB, query-core 27 kB, food 21 kB, ui 16 kB, auth
14 kB (pairing + onboarding), qrcode.react 13 kB, ai 12 kB, health 10 kB; framer-motion
(111 kB) sat in a chunk the entry imported. After: the Today screen's own code (shell,
Today, its cards, the health sheets and their queries — app code 149 kB), i18next
(44 kB), query-core (30 kB) and the slim `m` runtime of framer-motion (20 kB); React and
Dexie in long-cached vendor chunks.

### 2.2 Screens, throttled (CPU ×4, Fast 4G) — medians of RUNS_TBD runs, before/after in turn

SCREENS_TBD

### 2.3 Tab switches (Food → Plan → Today → Food → Plan → Stats → Today)

TABS_TBD

### 2.4 API hot paths (seeded year, in-process, alternating rounds)

`bench_api.py --compare <baseline api/> --rounds 4 --runs 10`: the baseline code (`0549984`)
and this branch in alternating rounds (child processes), 40 requests per endpoint and
version, on the seeded year (last run; box load 2.5 → 1.3). Statement counts are exact;
the p95 of 40 requests on the shared box moves by 10–20 % from run to run (the run
before gave the same picture), CPU per request is the steadier signal.

| endpoint | p50 ms | p95 ms | CPU ms (p50) | SQL / request |
| --- | --- | --- | --- | --- |
| `/v1/auth/session` | 8.2 → **4** | 11.4 → **5.8** | 7.2 → 3.6 | 5 → 1 |
| `/v1/meta/app-config` | 1 → **1** | 1.6 → **1.2** | 1 → 1 | 0 → 0 |
| `/v1/nutrition/day?date=2026-10-28` | 7.7 → **5.3** | 10.8 → **9.2** | 6.9 → 5 | 6 → 3 |
| `/v1/recipes` | 10.1 → **7.6** | 14.7 → **13.8** | 9 → 6.6 | 6 → 3 |
| `/v1/training/today` | 18.2 → **14.9** | 38.5 → **23.5** | 14.9 → 12.8 | 13 → 10 |
| `/v1/health/today` | 14.3 → **10.7** | 19.6 → **16.2** | 11.8 → 9.3 | 11 → 8 |
| `/v1/health/steps?days=7` | 9.1 → **7.4** | 14.5 → **11.4** | 8.4 → 6.8 | 7 → 4 |
| `/v1/health/weight?days=90` | 11.1 → **7.2** | 14.4 → **9** | 9.7 → 6.7 | 6 → 3 |
| `/v1/health/sleep?days=14` | 7.3 → **5.3** | 9.7 → **6.5** | 6.7 → 4.9 | 5 → 2 |
| `/v1/planner/views/today` | 28.1 → **28.9** | 41.5 → **37.8** | 21.4 → 19.8 | 17 → 14 |
| `/v1/planner/timer` | 8 → **6.3** | 12.2 → **11.7** | 7.2 → 5.5 | 5 → 2 |
| `/v1/gamification/today` | 123.2 → **96.3** | 182.2 → **156.1** | 87.4 → 66.3 | 48 → 38 |
| `/v1/foods/recents` | 10.3 → **8.8** | 17.1 → **13.5** | 8.9 → 7.6 | 7 → 4 |
| `/v1/foods/favorites` | 11.3 → **10.8** | 13.9 → **12.5** | 9.8 → 8.3 | 6 → 3 |
| `/v1/planner/counts` | 11.9 → **15** | 15 → **18.6** | 8.3 → 9.7 | 6 → 3 |
| `/v1/planner/views/inbox` | 18 → **17.2** | 153.9 → **20.9** | 14.4 → 13.8 | 8 → 5 |
| `/v1/planner/views/upcoming` | 21.6 → **20.8** | 32.4 → **27.6** | 17.8 → 16.5 | 11 → 8 |
| `/v1/planner/areas` | 13.3 → **11.8** | 18.3 → **18.6** | 11 → 9.8 | 8 → 5 |
| `/v1/stats/nutrition?period=week` | 11.1 → **7.5** | 14.5 → **9.4** | 9.8 → 7.1 | 6 → 3 |
| `/v1/stats/activity?period=week` | 15.4 → **16.8** | 21.5 → **23.8** | 11.8 → 14.8 | 9 → 6 |
| `/v1/stats/body?period=week` | 9.6 → **10.1** | 12.8 → **12.1** | 8.6 → 9.7 | 6 → 3 |
| `/v1/stats/planner?period=week` | 20.5 → **23.1** | 23.6 → **30.4** | 15.6 → 15.9 | 13 → 10 |
| `/v1/stats/progress?period=week` | 100.8 → **99.5** | 140.7 → **155.4** | 73.1 → 66.6 | 42 → 32 |
| `/v1/stats/nutrition?period=year` | 18.7 → **17.2** | 28.9 → **25.8** | 16.3 → 15.1 | 6 → 3 |
| `/v1/stats/activity?period=year` | 31.3 → **26** | 46.3 → **43.4** | 23 → 20.4 | 10 → 7 |
| `/v1/stats/body?period=year` | 16 → **14.7** | 26.2 → **19.1** | 14.8 → 13.6 | 6 → 3 |
| `/v1/stats/planner?period=year` | 39.9 → **40.4** | 55.9 → **49.8** | 27.4 → 24.2 | 13 → 10 |
| `/v1/stats/progress?period=year` | 109.1 → **110.5** | 156.9 → **140.9** | 80.6 → 77.6 | 42 → 32 |
| `/v1/gamification/profile` | 117.7 → **108.9** | 173.1 → **153.4** | 88.5 → 73.6 | 50 → 40 |

Every device-authenticated request runs three statements fewer (four for
`/auth/session`); the gamification reads ten fewer. Above 100 ms p95 before and after:
`/gamification/today`, `/gamification/profile` and `/stats/progress` (week and year) —
the engine recomputes the whole history on every read (§8).

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
3. **Lazy screens and sheets.** Pairing, the onboarding wizard and the Food tab are chunks
   of their own (`App.tsx`, inside the existing spinner fallbacks); the settings hub and
   all its sections (with `qrcode.react`) are one chunk (`features/settings/sections.ts`) —
   `lib/lazy.ts` `chunk()` renders a section at once when its chunk is already there, so
   moving between sections never shows a spinner (plain `React.lazy` suspends on the first
   render of every lazy component). The route table in `App.tsx` is unchanged. The sheets
   most visits never open load on first use and then stay mounted so their close
   animation plays (`useOnceTrue`): Today's recipe log sheet, the AI review / barcode log /
   custom-food sheets of `AiCapture`, the score breakdown.
   Not the health sheets: lazy, they read their series only once open (three reads fewer
   on Today), and the whole ui-check caught the price — the day's entry then arrived
   after the sheet had opened and could land in the middle of a weigh-in being typed
   (`80,080,4`), and a save before it arrived would have replaced the day's stored body
   fat. They are mounted with Today again, as in stage 5, and open prefilled; their fields
   now never take a late entry once the user has started editing (focus or input).
4. **Food's and Plan's code before the first tap** (`App.tsx` `prefetchTabs`, `lib/lazy.ts`
   `whenIdle`). With Food a chunk of its own, its first tap waited for the chunk (+222 ms
   cold, +59 ms from the service worker's cache — measured); Plan's always did. Both
   chunks now load half a second after the shell mounted (the first screen's data is
   usually in by then, §3.6), in an idle callback where the browser has one (Safari has
   none: at 0.5 s), never with Data Saver on and not during onboarding
   (`chunk().preload()`; a failed preload is tried again on the first render). A loaded
   chunk renders on the tap without a Suspense round. (A first try waited 2 s: the
   measured first tap, 1.5 s after Today was ready, still came before it.)
5. **Manual chunks as a function** (`vite.config.ts`): `vendor` (React, the router and
   the small helpers shared with other libraries), `dexie`, `charts` (Recharts and what
   only it uses). The old list form put clsx into the `charts` chunk, so the entry
   imported that chunk at start — with the stats dashboards merged it would have loaded
   all of Recharts on every screen (the stats branch fixed the same trap with a function;
   this one covers its rules). framer-motion gets no manual chunk on purpose.
6. **The first screen's reads start in index.html.** On a paired device an inline script
   requests the session, the app config and the reads of Today, Food or Plan while the
   code downloads; `api/client.ts` hands each response to the app's first GET of the same
   URL (once, within the first 10 s; everything else — errors, 401, offline — goes
   through the normal path). `main.tsx` renders once the session is in (at most 3 s), so
   React's first frame is the app shell rather than its own loading splash. Today's nine first-screen
   reads now finish before its JS has run (the three series the health sheets prefill
   from follow the first render, as before).
7. **No refetch storm.** The planner and health queue listeners invalidated their whole
   family (and the session) every time a screen mounted — 7 reads on every return to
   Today, 4 on Plan. One app-wide listener (`src/lib/queueRefresh.ts`) now refreshes once,
   300 ms after an offline flush settles (inactive queries are marked stale), so screens
   no longer refetch on mount "just in case"; a flush of many writes is one refresh.
   Reference lists only the app's own writes change (recipes, favourites, recents,
   templates, exercises, records, areas, projects, resources, tags, settings, devices,
   tokens, notification prefs) stay fresh for 5 minutes; live reads keep the 30 s default.

### API (`api/`)

8. **One round trip to authenticate.** `get_principal` loads device, user and settings in
   one statement (`User.settings` filled from the join with `contains_eager` instead of
   its own selectin query), and `get_or_create_settings` takes the row the request already
   loaded from the session. Every device request runs 2–4 statements fewer
   (`/auth/session` 5 → 1, `/nutrition/day` 6 → 3).
9. **Gamification reads stop rewriting what did not change.** Each read of
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
web/src/main.tsx                        render after i18nReady and the session, MotionProvider, installQueueRefresh
web/src/lib/{motion.tsx,motionFeatures.ts,lazy.ts,queueRefresh.ts}   ★ new
web/src/features/settings/sections.ts   ★ the settings chunk (hub + sections)
web/src/lib/queryClient.ts              staleTime per family
web/src/api/client.ts                   takeEarly
web/src/App.tsx                         lazy pairing, onboarding, settings chunk, Food; Food + Plan preloaded when idle
web/src/features/today/TodayPage.tsx    m.div, lazy recipe log sheet
web/src/features/ai/AiCapture.tsx       lazy review/log/custom-food sheets
web/src/features/game/{DayScoreCard,Celebration,queries}.tsx|ts   lazy score sheet, m, no queue listener
web/src/features/health/{useHealth,sheets}.ts(x)   no mount refetch; fields keep what the user typed
web/src/features/plan/queries.ts        no mount refetch (app-wide refresh)
web/src/components/ui/{index,Toast,ProgressRing}.tsx, components/layout/{BottomTabBar,SyncStatus}.tsx,
web/src/features/auth/{PairingScreen,OnboardingWizard}.tsx, features/plan/tasks/toast.tsx   motion → m
docs/stage-9-perf.md                    this report
```

Shared files touched (other stage-9 agents): `components/ui/index.tsx` and `Toast.tsx`
(accessibility), `SyncStatus.tsx`, `TodayPage.tsx`, `api/client.ts`, `plan/queries.ts`,
`health/useHealth.ts` (offline sync) — each a small, local change; see §9.

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

- The slowest SELECTs of the slowest endpoints are the engine's whole-history aggregates:
  tasks created per day (`Seq Scan on tasks`, 4 069 rows, 2–4 ms), tasks completed per day
  (`Seq Scan`, 3 711 of 4 069 rows), the XP total (`Seq Scan on xp_events`, 6 996 rows,
  1.3 ms). Over a 365-day range nearly every row qualifies: a sequential scan is the right
  plan, an index would not change it.
- Planner, nutrition, health and training reads use their indexes (index scans of
  0.02–0.1 ms); there is no N+1 — lookups of projects, areas, tags, subtasks and parents
  are batched per view.
- The one read that scans far more than it returns: tasks created per day for a week or a
  month (`/stats/planner`, `/stats/progress`) — 4 069 rows for ~100. An index
  `tasks (user_id, created_at)` would make it a range scan, but it costs 1–2 ms at a
  year of data, so it is not worth a migration now (and the next revision number is
  taken by the Notion branch's `0004`). Worth adding once tasks reach tens of thousands.
- Measured inside the requests, the same statements took several times their EXPLAIN
  execution time (round trip, driver, event loop): the cost of these endpoints is the
  number of statements and the Python around them, not the plans — hence §3.8–3.9.

## 7. Gates

```
api      pytest -q (TEST_DATABASE_URL=…/dsos_test_s9perf): 1153 passed, 1 skipped — the same as
         the base (0549984: 1153 passed, 1 skipped) · ruff check clean · ruff format --check clean
web      npm run typecheck clean · npm run build (tsc + vite + PWA, 55 precache entries)
         node scripts/perf/bundle-report.mjs --check: main chunk 249.5 kB ≤ 300 kB; no
         Recharts, stats code or framer-motion features at start
i18n     node scripts/check-i18n.mjs: complete and in sync
browser  scripts/ui-check.mjs, the whole run on a freshly created database (API with the
         scheduler and the VAPID keys, Vite dev server, fake camera): 295/295 green.
         The first whole run caught two regressions of the lazy loading (4 checks: a
         settings section rendered a spinner before its text; a weigh-in typed before the
         day's entry arrived was overwritten by it) — fixed in dc9fc51, then 295/295.
```

## 8. Not done, and why

- **`/gamification/today`, `/gamification/profile` and `/stats/progress` stay above
  100 ms p95.** Each read recomputes the whole history (stage 8, "recomputed on read"):
  eleven range aggregates over every day since the first entry, the streak simulation,
  the achievement metrics and the quests — still ~38 statements and the Python around
  them after §3.9. Getting under 100 ms on a year of data needs persisted per-day facts
  and incremental streaks, i.e. a rewrite of the engine's read path and its tests, not
  a tuning fix. What was pure waste (repeated inserts, identical upserts, duplicate
  settings and auth reads) is gone.
- **Dexie still loads at start** (94 kB / 31 kB gzip, now its own long-cached chunk):
  `lib/offlineQueue.ts` imports it statically and opens IndexedDB before the first
  screen. Loading it on demand (the localStorage mirror already holds the pending count)
  is a change inside `offlineQueue.ts`, which the offline-sync branch rewrites — a
  follow-up after that merge, worth 31 kB gzip off the start-up path.
- **Server-side caching.** No hot path serves per-day immutable data on its own: past
  days are read inside the same engine runs that compute today, and any cache would
  need invalidation on every write across two uvicorn workers. Not worth the risk.
- **Merging `day_metrics.collect`'s eleven range queries into one statement**: ~10 ms per
  engine run at this size, but a rewrite of the series code stage 8 shares with stats
  and export.
- **Preloading Stats on idle** (Food and Plan are, §3.4): with the dashboards merged its
  chunk pulls Recharts (402 kB) — too much to download on every cold open on mobile data
  for a tab opened far less often; the service worker precaches it after the first visit.
- **Lazy health sheets** — tried and reverted (§3.3): three reads fewer on Today were not
  worth sheets that open before their data.
- **Compression**: already on (Caddy `encode zstd gzip`, `GZipMiddleware` ≥ 1 kB).

## 9. Merge notes (for the coordinator)

Checked in a scratch clone (no branch touched) at `2011f3f`: this branch + `s8-web-stats`
+ `s9-offline` → `tsc` clean, main chunk 269.6 kB, `node scripts/perf/bundle-report.mjs
--check` ok (Recharts' 402.5 kB chunk and the stats page load with /stats only). This
branch's main chunk has grown by 6.7 kB since (the health sheets are back in it, §3.3):
≈ 278 kB merged, under the 300 kB budget — `--check` after the merge tells.

- `web/vite.config.ts` × `s8-web-stats` (`manualChunks`): keep this branch's function — it
  carries the stats branch's `vendor`/`charts` rules; their `motion` chunk must not come
  back (the entry would import all of framer-motion's features at start again).
- `web/src/components/layout/SyncStatus.tsx` × `s9-offline`: take theirs, then
  `motion` → `m` (the import and `<motion.span>`), or framer-motion's full bundle returns
  to the main chunk (`bundle-report.mjs --check` reports it).
- `web/src/features/today/TodayPage.tsx` × `s9-offline` (imports): theirs, without the
  static `LogSheet` import (it is lazy here); the health sheets' import is the base's.
- `web/src/features/plan/queries.ts` × `s9-offline` (import line):
  `import { mutate, type QueuedMethod, type QueueMeta } from '@/lib/offlineQueue'`.
- The stats branch's `useStatsAutoRefresh` also invalidates on a queue flush; with the
  app-wide refresh the dashboards refresh twice after a flush — harmless, its queue part
  can go.
- New code: `m.*` instead of `motion.*`; new settings sections lazy like the others in
  `App.tsx` (the Notion branch's Интеграции section); keep `index.html`'s early-read lists
  in step with what Today, Food and Plan request on open (`perf-run.mjs` prints them).
- A sheet that mounts lazily (`useOnceTrue` + `lazy`) must not depend on data that
  arrives after it opened for what the user types — the health sheets taught that (§3.3).
