# Stage 9 — Hardening · performance pass

> The app is used one-handed on an iPhone, often on mobile data. Measured first,
> fixed what the numbers showed, measured again — with the same tools, which stay in
> the repo. Merged with the integration branch and measured against it: the main chunk
> 640 → 278 kB (start-up download −28 %), the first screen starts its API reads while the
> code is still downloading (cold Today: first content −18 %, the calorie ring −22 %;
> installed app: ring −36 %), seven tab switches cost 8 API requests instead of 28 and
> the Food and Plan tabs' code is there before their first tap; every device-authenticated
> request runs three SQL statements fewer and the gamification reads stopped rewriting
> rows that did not change.
>
> - No migration (EXPLAIN found no hot query missing an index — see §6), no dependency
>   change: the analyzers use the app's own Vite/esbuild and Playwright as before.
> - This is the performance part of stage 9; the coordinator merges it into `docs/stage-9.md`.

## 1. How it was measured

**Data.** `api/scripts/seed_year.py` fills the owner with a year of realistic history
(Europe/Moscow, onboarded): 1 329 meals with 3 990 items, 2 565 water logs, 142
workouts with 2 272 sets and 49 records, 365 days of steps and sleep, 221 weigh-ins,
4 069 tasks (subtasks, tags, reminders, three recurring series, an inbox and an
upcoming week), 970 time entries, 306 journal days — then has the gamification engine
finish the whole history the way the nightly jobs would have (merged code: 363 days
finalised plus the open window, 62 191 XP, level 30; every achievement already seen).

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
were measured in turn** — the baseline build (served by a second `vite preview`) and this
branch's build, run after run, against the same API and database, so a load spike slows
both alike (`--targets before=…,after=…`). Medians of N runs.

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

Two comparisons, each measured before/after in turn:

- **merged** — this branch merged with `stage-9-hardening` (§9) against
  `stage-9-hardening` itself (`f36e964`: the integration branch without this pass, its
  own build and API code): what this pass changes in the code that ships. The final
  measurement, 3 runs.
- **stand-alone** — this branch before the merge (`1563c58`) against the stage-8 base
  (`0549984`), 5 runs (§2.5).

### 2.1 What the first screen downloads

Files as shipped (`web/dist`), raw / gzip:

| | stage-8 base (`0549984`) | `stage-9-hardening` | this branch, merged |
| --- | --- | --- | --- |
| **main chunk** (`index-*.js`) | 567.5 / 173.2 kB | **640.2 / 195.1 kB** (vite prints 605.2) | **277.7 / 86.1 kB** (−57 % / −56 %) |
| start-up JS + CSS (index.html, its preloads, the active locale) | 897.4 / 274.8 kB | 975.6 / 297.8 kB | 704.8 / 213.7 kB (−28 % / −28 %) |
| start-up chunks | index, vendor, motion, charts (clsx), CSS | index (both locales), vendor, motion (113.7 kB), CSS | index, vendor, dexie, CSS, `ru` locale (preloaded) |
| loaded right after start, not needed to paint | — | — | motion features 96.1 / 31.0 kB |
| loaded when the app is idle | — | — | Food 14.1 / 4.2 kB, Plan 53.2 / 13.4 kB (≥ 0.5 s after the shell); the sync sheet 6.6 / 2.3 kB (the offline branch's own idle preload) |

`bundle-report.mjs --check` on the merged tree: main chunk 277.7 kB ≤ 300 kB, no Recharts
(its 402.5 kB `charts` chunk loads with /stats), no stats code and no framer-motion
animation features at start.

Main chunk before this pass (stage-8 base), by source: both locales 189 kB (33 %), Dexie
75 kB, i18next 39 kB, settings 36 kB, game 29 kB, plan 28 kB, query-core 27 kB, food
21 kB, ui 16 kB, auth 14 kB (pairing + onboarding), qrcode.react 13 kB, ai 12 kB, health
10 kB; framer-motion (111 kB) sat in a chunk the entry imported. Merged, after: i18next
45 kB, the planner's Today card and queries 36 kB, query-core 30 kB, game 29 kB, the slim
`m` runtime of framer-motion 20 kB, `lib` 19 kB (the offline queue), ui 19 kB, health
(with its sheets) 12 kB, Today 12 kB; React and Dexie in long-cached vendor chunks.

### 2.2 Screens, throttled (CPU ×4, Fast 4G) — merged, medians of 3 runs

`stage-9-hardening` → this branch merged, measured in turn (box load 6.6 at the start,
2.5 at the end). *shell* is the first app content; JS is what was transferred until idle
(the after column includes the idle preloads of Food, Plan and the sync sheet).

| screen | mode | shell (first content) | ring | ready | LCP | JS kB (gzip) | requests (API) | blocking ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| today | cold | 1716 → **1403** (−18 %) | 2323 → **1810** (−22 %) | 2323 → **1809** (−22 %) | 1700 → **1388** (−18 %) | 299.0 → 274.8 | 22 (13) → 29 (12) | 580 → 615 |
| food | cold | 1614 → **1122** (−30 %) | — | 1789 → **1550** (−13 %) | 1624 → **1476** (−9 %) | 299.0 → 274.8 | 15 (6) → 23 (6) | 399 → 268 |
| plan | cold | 1477 → **1055** (−29 %) | — | 2047 → **1523** (−26 %) | 2064 → **1540** (−25 %) | 319.2 → 274.8 | 17 (5) → 22 (5) | 431 → 267 |
| stats | cold | 1522 → **994** (−35 %) | — | 3926 → **3426** (−13 %) | 2076 → **1764** (−15 %) | 426.6 → 402.4 | 21 (7) → 29 (7) | 1661 → 1238 |
| today | warm | 731 → **676** (−8 %) | 1435 → **916** (−36 %) | 1483 → **1020** (−31 %) | 724 → **564** (−22 %) | 0.0 → 0.0 | 22 (13) → 29 (12) | 254 → 115 |
| food | warm | 597 → **444** (−26 %) | — | 786 → **636** (−19 %) | 616 → **672** (+9 %) | 0.0 → 0.0 | 15 (6) → 23 (6) | 49 → 35 |
| plan | warm | 641 → **529** (−17 %) | — | 1012 → **761** (−25 %) | 1036 → **792** (−24 %) | 0.0 → 0.0 | 17 (5) → 22 (5) | 56 → 72 |
| stats | warm | 616 → **405** (−34 %) | — | 2306 → **1741** (−25 %) | 936 → **688** (−26 %) | 0.0 → 0.0 | 21 (7) → 23 (7) | 924 → 718 |

Every pair agreed: cold Today first content 2116 → 1403, 1716 → 1275, 1702 → 1441 ms;
the ring 3176 → 1854, 2323 → 1680, 2282 → 1810 ms. Today asks for 12 API reads instead of
13 (the session once); nine of them now start in index.html. A cold waterfall of the
merged build (`--waterfall`): the start-up JS has arrived at ~0.6 s; the rest of the way
to the shell (1.1–1.2 s in the DOM) is main-thread work — evaluating it and React's first
render of Today, two long tasks of 150–290 ms at CPU ×4 (§8).

### 2.3 Tab switches — merged, medians of 3 runs

Food → Plan → Today → Food → Plan → Stats → Today after a cold or warm open of Today: API
requests and time from the tap to the screen painted ready, per tap.

| tap | before, cold | after, cold | before, warm | after, warm |
| --- | --- | --- | --- | --- |
| Food | 2 req · 135 ms | 2 req · 111 ms | 2 req · 164 ms | 2 req · 121 ms |
| Plan | 3 req · 418 ms | 1 req · **161 ms** | 3 req · 283 ms | 1 req · **172 ms** |
| Today | 7 req · 215 ms | **0 req** · 184 ms | 7 req · 226 ms | **0 req** · 178 ms |
| Food | 0 req · 77 ms | 0 req · 84 ms | 0 req · 80 ms | 0 req · 88 ms |
| Plan | 4 req · 120 ms | **0 req** · 138 ms | 4 req · 160 ms | **0 req** · 140 ms |
| Stats | 5 req · 44 ms | 5 req · 45 ms | 5 req · 49 ms | 5 req · 50 ms |
| Today | 7 req · 247 ms | **0 req** · 177 ms | 7 req · 204 ms | **0 req** · 181 ms |
| **API requests** | **28** | **8** | **28** | **8** |

The first taps on Food and Plan find their code preloaded (§3.4); returning to Today or
Plan no longer refetches (§3.7). Stats' five reads are its dashboards' first load.

### 2.4 API hot paths — merged (seeded year, in-process, alternating rounds)

`bench_api.py --compare <stage-9-hardening api/> --rounds 3 --runs 10`: the integration
branch's API code and the merged one in alternating rounds (child processes), 30 requests
per endpoint and version, same database (load 2.5 → 1.2). Statement counts are exact; the
p95 of 30 requests on the shared box moves by 10–20 % from run to run and catches the odd
spike (`/recipes`, `/stats/activity?period=year` here) — CPU per request is the steadier
signal.

| endpoint | p50 ms | p95 ms | CPU ms (p50) | SQL / request |
| --- | --- | --- | --- | --- |
| `/v1/auth/session` | 6.8 → **4.4** | 8.2 → **6.5** | 6.2 → 3.9 | 5 → 1 |
| `/v1/meta/app-config` | 1 → **1.1** | 1.1 → **2.1** | 1 → 1.1 | 0 → 0 |
| `/v1/nutrition/day?date=2026-10-28` | 7.1 → **5.4** | 7.9 → **7** | 6.7 → 5 | 6 → 3 |
| `/v1/recipes` | 9.2 → **7.5** | 116.3 → **124.4** | 8.4 → 6.9 | 6 → 3 |
| `/v1/training/today` | 15.8 → **14.8** | 24.8 → **20** | 13.9 → 13.1 | 13 → 10 |
| `/v1/health/today` | 12.6 → **10.5** | 16.3 → **12.9** | 11.1 → 9.2 | 11 → 8 |
| `/v1/health/steps?days=7` | 8.3 → **6.8** | 9.9 → **8.8** | 7.8 → 6.3 | 7 → 4 |
| `/v1/health/weight?days=90` | 8.6 → **7.2** | 9.9 → **8.1** | 7.9 → 6.7 | 6 → 3 |
| `/v1/health/sleep?days=14` | 6.9 → **5.2** | 10.1 → **5.8** | 6.5 → 5 | 5 → 2 |
| `/v1/planner/views/today` | 27.2 → **25.5** | 36.3 → **38** | 20.5 → 19.1 | 17 → 14 |
| `/v1/planner/timer` | 6.8 → **5.5** | 8.5 → **6.6** | 6.3 → 4.7 | 5 → 2 |
| `/v1/gamification/today` | 119 → **98.9** | 259.5 → **250.3** | 88.5 → 75.8 | 48 → 38 |
| `/v1/foods/recents` | 9.8 → **8.1** | 11.4 → **9.9** | 8.8 → 7 | 7 → 4 |
| `/v1/foods/favorites` | 9 → **8.3** | 12.3 → **13.7** | 8.1 → 7.1 | 6 → 3 |
| `/v1/planner/counts` | 11.4 → **11.3** | 14.2 → **13.4** | 7.7 → 7.2 | 6 → 3 |
| `/v1/planner/views/inbox` | 13.2 → **13** | 15.1 → **16.1** | 10.8 → 10.5 | 8 → 5 |
| `/v1/planner/views/upcoming` | 20 → **19.4** | 30.4 → **27.2** | 16.5 → 15.4 | 11 → 8 |
| `/v1/planner/areas` | 12.3 → **9.8** | 17.4 → **15.9** | 10.3 → 8.5 | 8 → 5 |
| `/v1/stats/nutrition?period=week` | 12.6 → **8.5** | 16.4 → **14** | 11.6 → 8 | 7 → 4 |
| `/v1/stats/activity?period=week` | 16.7 → **15** | 24.4 → **24.6** | 13.2 → 12.2 | 10 → 7 |
| `/v1/stats/body?period=week` | 9.3 → **6.5** | 14.4 → **10.2** | 8.5 → 6.2 | 6 → 3 |
| `/v1/stats/planner?period=week` | 20.4 → **20.7** | 27.3 → **27.7** | 15.7 → 14.9 | 13 → 10 |
| `/v1/stats/progress?period=week` | 102.4 → **91.1** | 151.7 → **128.7** | 79.1 → 69.5 | 42 → 32 |
| `/v1/stats/nutrition?period=year` | 29.4 → **25.1** | 43.2 → **27.8** | 23.9 → 22.2 | 7 → 4 |
| `/v1/stats/activity?period=year` | 38.1 → **38.1** | 59.1 → **218.7** | 28.8 → 29 | 11 → 8 |
| `/v1/stats/body?period=year` | 15.5 → **13.4** | 22.3 → **21.7** | 14.2 → 12.4 | 6 → 3 |
| `/v1/stats/planner?period=year` | 35.6 → **34.2** | 49.6 → **40** | 21.9 → 19.1 | 13 → 10 |
| `/v1/stats/progress?period=year` | 105.6 → **100.1** | 147.9 → **152.6** | 81.7 → 74.5 | 42 → 32 |
| `/v1/gamification/profile` | 116.9 → **111** | 151.9 → **145.2** | 88.8 → 83.4 | 50 → 40 |

Every device-authenticated request runs three statements fewer (four for
`/auth/session`); the engine reads ten fewer. Above 100 ms p95, before and after:
`/gamification/today`, `/gamification/profile` and `/stats/progress` (week and year) —
the engine re-aggregates the whole history on every read (§8). The merged branch alone, 20
requests each: those four at 115–144 ms p95 (p50 94–110 ms, CPU 66–78 ms), everything else
≤ 40 ms p95.

### 2.5 Stand-alone — this branch before the merge against the stage-8 base

`0549984` → `1563c58`, 5 runs in turn, CPU ×4, Fast 4G (load 5.0 at the start, 2.5 at the
end; another agent's browser ran outside the lock for part of it). The tab preload then
still waited 2 s (§3.4).

| screen | mode | shell (first content) | ring | ready | LCP | JS kB (gzip) | requests (API) | blocking ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| today | cold | 1982 → **1431** (−28 %) | 2471 → **1925** (−22 %) | 2585 → **1925** (−26 %) | 1980 → **1380** (−30 %) | 269.7 → 224.2 | 20 (13) → 20 (12) | 825 → 747 |
| food | cold | 1641 → **1207** (−26 %) | — | 1860 → **1732** (−7 %) | 1664 → **1600** (−4 %) | 269.7 → 232.0 | 13 (6) → 17 (6) | 421 → 366 |
| plan | cold | 1506 → **1184** (−21 %) | — | 2022 → **1735** (−14 %) | 2024 → **1748** (−14 %) | 291.7 → 246.1 | 16 (5) → 17 (5) | 431 → 463 |
| stats | cold | 1591 → **1141** (−28 %) | — | 1653 → **1302** (−21 %) | 1676 → **1332** (−21 %) | 270.9 → 225.4 | 10 (2) → 11 (2) | 405 → 243 |
| today | warm | 903 → **752** (−17 %) | 1688 → **955** (−43 %) | 1687 → **1074** (−36 %) | 900 → **728** (−19 %) | 0.0 → 0.0 | 20 (13) → 20 (12) | 363 → 150 |
| food | warm | 627 → **487** (−22 %) | — | 835 → **647** (−23 %) | 652 → **640** (−2 %) | 0.0 → 0.0 | 13 (6) → 17 (6) | 65 → 39 |
| plan | warm | 686 → **426** (−38 %) | — | 1097 → **699** (−36 %) | 1104 → **712** (−36 %) | 0.0 → 0.0 | 16 (5) → 17 (5) | 101 → 62 |
| stats | warm | 568 → **441** (−22 %) | — | 594 → **466** (−22 %) | 616 → **476** (−23 %) | 0.0 → 0.0 | 10 (2) → 11 (2) | 22 → 9 |

An earlier 5-run measurement (`243a7e4`, the health sheets still lazy, load 2.0 → 5.0):
cold Today first content 2035 → 1286 ms (−37 %), ring 2775 → 1741 ms (−37 %); warm ring
1338 → 837 ms (−37 %). Tab switches in both: 23 → 3 API requests (the stats dashboards
were not merged yet). The API, `0549984` → this branch, 40 requests each: the same
statement counts as §2.4; CPU per request lower on most endpoints (`/auth/session` −50 %,
the health and nutrition reads −19…−31 %, the engine reads −4…−24 %), within run-to-run
noise (±15 %) on the rest.

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
   the merged function keeps its rules). framer-motion gets no manual chunk on purpose.
6. **The first screen's reads start in index.html.** On a paired device an inline script
   requests the session, the app config and the reads of Today, Food or Plan while the
   code downloads; `api/client.ts` hands each response to the app's first GET of the same
   URL (once, within the first 10 s; everything else — errors, 401, offline — goes
   through the normal path). `main.tsx` renders once the session is in (at most 3 s), so
   React's first frame is the app shell rather than its own loading splash. Today's nine
   first-screen reads now finish before its JS has run (the three series the health
   sheets prefill from follow the first render, as before).
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
   loaded from the session. Every device request runs three statements fewer
   (`/nutrition/day` 6 → 3; `/auth/session` four, 5 → 1).
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
api/scripts/seed_year.py                ★ a year of realistic history for one account (bulk; the engine's backlog via sync_fully)
api/scripts/bench_api.py                ★ p50/p95, CPU, statements, EXPLAIN; --compare two versions (rate limit reset per request)
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

Shared files touched (other stage-9 agents, review 8): `components/ui/index.tsx` and
`Toast.tsx` (accessibility), `SyncStatus.tsx`, `TodayPage.tsx`, `api/client.ts`,
`plan/queries.ts`, `health/useHealth.ts` (offline sync), `App.tsx` (Notion),
`vite.config.ts` (stats), `gamification/engine.py` (review 8) — each a small, local
change; the conflicts are resolved in the merge (§9).

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
  plan, an index would not change it. The merged code, same picture: tasks completed per
  day (3 711 rows, 2.7 ms), the stored score rows review 8 reads with their frozen targets
  (`Seq Scan on daily_scores`, 365 rows, 1.1 ms), the achievement counts (2.5 ms in all).
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

On the merged tree (`fc74c84` + `662913e`, the seed and bench scripts only):

```
api      TEST_DATABASE_URL=…/dsos_test_s9perf .venv/bin/pytest (whole suite): 1318 passed,
         1 skipped in 420.8 s — the integration branch's own count
         ruff check . clean · ruff format --check . clean (170 files)
web      npm run typecheck clean · npm run build (tsc + vite + PWA): 61 precache entries,
         main chunk index-C_cpqd8r.js 277.63 kB as vite prints it (277.7 kB / 86.1 kB gzip)
         node scripts/perf/bundle-report.mjs --check: main chunk 277.7 kB ≤ 300 kB; no
         Recharts, stats code or framer-motion animation features at start
i18n     node scripts/check-i18n.mjs: complete and in sync
browser  scripts/ui-check.mjs, the whole run on a freshly created database (migrations to
         0004, API with the scheduler and the VAPID keys, Vite dev server, fake camera):
         410/410 green, every section through stage 8's dashboards, stage 9's offline sync
         and conflicts and the Notion screen
```

Before the merge (this branch alone): pytest 1153 passed, 1 skipped (= the stage-8 base),
the same lint, type, build, bundle and i18n gates, and the whole ui-check 295/295 on
`dc9fc51`. Two whole runs on the way failed, both found and fixed: the first caught two
regressions of the lazy loading (4 checks: a settings section rendered a spinner before
its text; a weigh-in typed before the day's entry arrived was overwritten — fixed in
`dc9fc51`); the run on `243a7e4` failed one check (the weigh-in field read `80,080,4`:
the entry arrived mid-typing — root cause in §3.3, fixed in `39f53bc`; a focused repro
failed 1 in 8 before, 0 in 12 after). One more run stopped at pairing because my run
script read digits of a UUID as the pairing code (§9) — not the app.

## 8. Not done, and why

- **`/gamification/today`, `/gamification/profile` and `/stats/progress` stay above
  100 ms p95.** Each read re-aggregates the whole history (stage 8, "recomputed on read";
  review 8 freezes finished days' scores but still collects every day's metrics for the
  streaks): eleven range aggregates over every day since the first entry, the streak simulation,
  the achievement metrics and the quests — still ~38 statements and the Python around
  them after §3.9. Getting under 100 ms on a year of data needs persisted per-day facts
  and incremental streaks, i.e. a rewrite of the engine's read path and its tests, not
  a tuning fix. What was pure waste (repeated inserts, identical upserts, duplicate
  settings and auth reads) is gone.
- **First content on cold Today: the ≥ 30 % target is met in one measurement of three,
  not reliably** — −18 % merged (3 runs), −28 % and −37 % stand-alone (5 runs each);
  the ring and the warm open gained more (−22 % cold, −36 % warm, merged). The bytes are
  done: the start-up JS has arrived at ~0.6 s of the ~1.4 s. What is left is main-thread
  work at CPU ×4 — evaluating ~540 kB of start-up JS (Dexie 94 kB, i18next 45 kB,
  query-core 30 kB, the offline queue) and React's first render of the whole Today screen
  (two long tasks of 150–290 ms in the waterfall). The next steps are structural: Dexie on
  demand (below) and a first frame of Today with the cards below the fold rendered a frame
  later — a change to how Today renders that the ui-check has to cover, not a tuning knob.
- **Dexie still loads at start** (94 kB / 31 kB gzip, its own long-cached chunk):
  `lib/offlineQueue.ts` imports it statically and reads IndexedDB before the first
  screen. Loading it on demand (the localStorage mirror already holds the pending count)
  is a change inside the offline queue the offline branch has just rewritten — the next
  step, worth 31 kB gzip off the start-up path, but one for that queue's owner and its
  conflict tests, not a side change of this pass.
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

## 9. Merge with `stage-9-hardening` (for the coordinator)

`stage-9-hardening` (`f36e964`: stage 8 final with the review-8 fixes and the stats
dashboards, `s9-offline`, `s9-notion`) is merged into this branch (`fc74c84`), so it
merges back without conflicts. Resolutions, both sides' intent kept:

- `api/app/services/gamification/engine.py`: the review-8 engine (at most 60 pending days
  per request, chunked finalisation, explicit finality, frozen per-day targets and week
  requirements) with this branch's streak read: the stored rows are read once, give
  `longest`, and their upsert is skipped when nothing changed; milestones are still
  awarded from the complete history only (`not snapshot.pending_left`). `streaks.py` and
  `xp.py` merged cleanly (review 8 changed the rules, this branch the writes).
- `web/vite.config.ts`: one `manualChunks` function — `vendor`, `dexie`, `charts`
  (Recharts loads with /stats only: `bundle-report.mjs --check` passes); no `motion`
  chunk — with LazyMotion it would bring all of framer-motion's features back to start.
- `web/src/App.tsx`: the Notion branch's lazy Интеграции screen next to the Food chunk,
  the settings chunk and the tab preload.
- `web/src/components/layout/SyncStatus.tsx`: the offline branch's pill and lazy sync
  sheet (it preloads when idle and online, like the tabs), `motion.span` → `m.span`.
- `web/src/features/plan/queries.ts`: the offline branch's `QueueMeta` import; the queue
  refresh stays app-wide (`lib/queueRefresh.ts` — the offline queue's `onQueueChange`
  still reports the pending count).
- `web/src/features/today/TodayPage.tsx`: the offline branch's `useDayView`,
  `useDeleteWater`, `planToast`; the recipe log sheet stays lazy.
- `api/scripts/seed_year.py` finishes the engine's backlog with `engine.sync_fully` (a
  request now finalises at most 60 days); `bench_api.py` resets the in-memory rate limiter
  before each request (the engine GETs share 120/min per user since review 8) and stops on
  any non-2xx answer.

Still worth knowing:

- The stats branch's `useStatsAutoRefresh` also invalidates on a queue flush; with the
  app-wide refresh the dashboards refresh twice after a flush — harmless, its queue part
  can go.
- New code: `m.*` instead of `motion.*` (`bundle-report.mjs --check` catches a slip); new
  settings sections lazy like the others in `App.tsx`; keep `index.html`'s early-read
  lists in step with what Today, Food and Plan request on open (`perf-run.mjs` prints
  them).
- A sheet that mounts lazily (`useOnceTrue` + `lazy`) must not depend on data that
  arrives after it opened for what the user types — the health sheets taught that (§3.3).
- Run scripts that read the pairing code with `grep -oE '[A-Z0-9]{4}-[A-Z0-9]{4}'` can pick
  up digits of a UUID from the CLI's log line (`…-7000-8459-…`): one of my whole runs
  failed at pairing that way. The code is the indented line on its own:
  `grep -E '^ +[A-Z0-9]{4}-[A-Z0-9]{4} *$' | tr -d ' '`.
