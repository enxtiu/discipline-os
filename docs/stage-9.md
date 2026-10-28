# Stage 9 — Hardening · delivery report

> The app holds up in daily use: a write made without signal is never lost and a change made
> on another device is shown, not silently overwritten; the planner and the days can be
> mirrored into Notion (off until the owner connects it); the first screen arrives sooner and
> the tabs switch without refetching; every screen and sheet passes an accessibility audit
> in both themes. Four parts, each with its own detailed report:
>
> | Part | Report |
> | --- | --- |
> | Offline sync polish and conflict surfacing | [`stage-9-offline.md`](stage-9-offline.md) |
> | Optional Notion sync | [`stage-9-notion.md`](stage-9-notion.md) |
> | Performance pass | [`stage-9-perf.md`](stage-9-perf.md) |
> | Accessibility pass | [`stage-9-a11y.md`](stage-9-a11y.md) |
>
> - Migration **0004** adds the two Notion tables (`integrations`, `integration_links`);
>   nothing earlier is reshaped, its downgrade drops exactly those two tables.
> - Dependencies: `cryptography==50.0.2` is now pinned explicitly (it was already installed
>   through `pywebpush`; Fernet + HKDF seal the Notion secret) and `axe-core 4.14.0` is a
>   pinned devDependency used only by the browser check (not in `dist/`).
> - The Obsidian markdown export from the original plan was dropped at the owner's request.

## 1. Plan that was executed

| # | Spec item | Delivered |
| --- | --- | --- |
| 1 | Offline sync polish on top of the stage-1 queue | Nothing is dropped any more: a write the server refuses stays `failed` with the server's reason, a write that met a newer change stays a `conflict` with the server's current row; transient failures back off (2 s · 2ⁿ ± 20 %, ≤ 5 min, `Retry-After` wins); a write never overtakes the one it depends on; autosaves coalesce. **The sync sheet** behind the header pill: what waits (by kind, with its age), what failed and why («Повторить» / «Удалить»), what changed elsewhere («Открыть»), «Отправить всё сейчас». Meals and water logged without signal show at once on Today and Food («ждёт отправки», the server's totals ± the row's own numbers — nothing computed on the client); queued task edits show at once in every list. |
| 2 | Conflict surfacing | Task, journal, project, area, resource and time-entry edits carry `base` (the values they started from) and answer **409 `edit_conflict`** with the current row instead of overwriting; a field already set to the requested value or not listed never conflicts (two devices editing different fields merge; a replay is not a conflict). The editor and the journal show «Изменено на другом устройстве» with both values and «Оставить их версию» / «Сохранить мою» (field-level); a queued conflict lands in the sync sheet. Without `base` the stage-6 behaviour (last write wins) is unchanged for API clients. |
| 3 | Optional Notion sync | A one-way mirror of areas, projects, tasks, the journal and one row per day (food against the target, protein, water, steps, sleep, workouts and their burned calories **in their own columns**, tracked time, tasks done, the day score) into five databases DisciplineOS **creates itself** on a page the owner shares with an internal Notion connection. Off until connected; every 10 minutes or «Синхронизировать сейчас»; idempotent, resumable, never a duplicate (lost answers are looked up before anything is created again); the secret sealed at rest and never returned, logged or exported. Settings → Интеграции. |
| 4 | Performance pass | Measured on a seeded year with throttled Chromium (CPU ×4, Fast 4G) and in-process API benchmarks, then fixed: main chunk **640 → 283 kB** (start-up download −28 %): only the active locale loads, framer-motion through LazyMotion, lazy pairing/onboarding/Food/settings and rarely opened sheets, Food and Plan preloaded when idle; the first screen's API reads start from `index.html`; seven tab switches cost **8 API requests instead of 28**; every device request runs three SQL statements fewer and the gamification reads stopped rewriting unchanged rows. The tools stay in the repo (`scripts/perf/`, `api/scripts/{seed_year,bench_api}.py`). |
| 5 | Accessibility pass | axe-core (WCAG 2.1 A/AA) on 50 states × 2 themes, every control against the 48 px rule, a keyboard pass, live regions, reduced motion, `<html lang>`, page width and zoom — **serious + critical nodes 141 / 218 (dark / light) → 0**, controls under 48 px 127 → 0 (five documented exceptions). Fixed in the tokens and `components/ui` first: modal sheets (focus in, trapped, Escape, focus back), named rings and bars, contrast in the light theme, names that match the visible text, labels, «Статистика» as a real tab clear of the floating «+», live regions that do not chatter, `MotionConfig reducedMotion="user"`, pinch-zoom allowed. |
| — | Obsidian markdown export | Dropped at the owner's request (Oct 2026). |

## 2. New & changed files

Full lists with every file are in the four part reports; the shape of it:

```
api/alembic/versions/0004_integrations.py        ★ the two Notion tables (self-contained up/down)
api/app/models/integrations.py                   ★ Integration, IntegrationLink
api/app/core/secret_box.py                       ★ Fernet + HKDF sealing of provider secrets
api/app/services/notion/{client,schema,mapping,sync}.py   ★ the paced client, databases, rows, the run
api/app/api/v1/integrations.py · schemas/integrations.py  ★ /integrations/* (device only, rate-limited)
api/app/services/planner/conflicts.py            ★ `base` normalisation and the 409 check
api/app/api/v1/planner*.py · schemas/planner.py  `base` on the six edit endpoints
api/app/services/nutrition.py                    meal / water delete by the client id too
api/app/api/deps.py · services/settings_service.py   the principal in one statement, the settings row reused
api/app/services/gamification/{engine,streaks,xp}.py batched milestones, no identical upsert
api/app/services/hooks.py                        + notion_sync (every 10 min)
api/scripts/seed_year.py · bench_api.py          ★ a seeded year; API p50/p95, statements, EXPLAIN
api/tests/test_stage9_{conflicts,notion}.py · stage9_notion_helpers.py   ★ 24 + 59 tests, the fake Notion
web/src/lib/offlineQueue.ts                      statuses, Dexie v2, backoff, dependencies, coalescing
web/src/features/sync/                           ★ the sync sheet and the conflict view
web/src/features/food/pendingDay.ts · plan/pending.ts   ★ waiting meals/water and task edits shown at once
web/src/features/integrations/                   ★ Settings → Интеграции (lazy)
web/src/lib/{motion.tsx,motionFeatures.ts,lazy.ts,queueRefresh.ts}   ★ LazyMotion, lazy helpers, one refresh
web/src/i18n/index.ts · documentLanguage.ts      the active locale only; <html lang> follows it
web/index.html · vite.config.ts · main.tsx · App.tsx   early reads, locale preload, chunks, lazy routes
web/src/styles/index.css · tailwind.config.js    contrast tokens, 48 px controls, scroll padding
web/src/components/ui/ · components/layout/      modal sheets, labelled fields, named rings, the Stats tab
web/src/i18n/locales/{ru,en}.json                + sync, integrations, a11y (+217 keys, 1 895 per locale)
scripts/ui-checks/stage9-{offline,notion,a11y}.mjs   ★ browser checks; scripts/ui-check.mjs loads them
scripts/perf/{perf-run,bundle-report}.mjs        ★ throttled loads, tab switches, the bundle budget
scripts/smoke.sh                                 + 18 the conflict contract, 19 Notion, 20 a fresh install
docs/stage-9*.md · docs/screenshots/9o-*.png · 60–64-notion-*.png
```

## 3. Commands

```bash
# quality gates
cd api && TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/dsos_test .venv/bin/pytest -q
cd api && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build && node ../scripts/check-i18n.mjs
node scripts/perf/bundle-report.mjs --check          # main chunk ≤ 300 kB, no charts/stats/motion features at start
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs   # incl. stage9-offline, -notion, -a11y
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh XXXX-XXXX

# Notion: a run by hand (the job does the same every 10 minutes when connected)
docker compose exec api python -m app.cli run-job notion_sync

# performance: a seeded year, the API benchmark, throttled browser runs
cd api && .venv/bin/python scripts/seed_year.py && .venv/bin/python scripts/bench_api.py --runs 30 --explain 4
PAIRING_CODE=XXXX-XXXX WEB_URL=http://127.0.0.1:4173 node scripts/perf/perf-run.mjs --runs 5 --tabs
```

## 4. What to test (walkthrough)

1. **Offline.** Airplane mode → Питание → log a recent food and «+250 мл»: both appear at once
   on Food and Today marked «ждёт отправки», the ring moves by the row's own kcal; the header
   pill shows `2` → tap it: the sync sheet lists them with their age. Delete the meal before
   the network is back: it never leaves the phone. Network back: the pill empties, the rows
   lose the mark, the totals are the server's.
2. **Conflicts.** Rename a task on the laptop, then (offline, or with the editor already open)
   differently on the phone → «Изменено на другом устройстве» with «Там: …» / «У вас: …» →
   «Сохранить мою» or «Оставить их версию». The journal does the same; an edit of a task the
   laptop deleted says «Удалено на другом устройстве — правка не применена».
3. **Notion.** Настройки → Интеграции → follow the steps (an internal connection, a page
   shared with it) → «Подключить» → «Создать базы» → five databases fill within a minute.
   Edit a task → «Синхронизировать сейчас» → only that row changes; delete a database in
   Notion → «Создать заново»; «Отключить Notion» leaves everything in Notion.
4. **Speed.** Open the installed app: Today's ring arrives sooner; switching Food → Plan →
   Today shows each screen at once without a spinner (no refetch on return).
5. **Accessibility.** VoiceOver on the iPhone (not available in this environment — see §7):
   rotor through Today (headings, «Калории, … из …»), open «+» and close it with a two-finger
   scrub, edit a task, log a set and listen to the rest timer, switch to English and back.
   Light theme: every text is readable on its card; the «+» never covers «Статистика».

## 5. Verification evidence (this build)

The integrated branch (all four parts merged), a fresh database for every run:

```
api      1319 passed (1236 after stage 8 + 24 conflict-contract tests + 59 Notion tests against
         a fake Notion — no request leaves the machine) · ruff check clean · ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 61 precache entries (2 230.8 KiB); main chunk
         282.5 kB / 87.7 kB gzip (stage 8: 566.5 kB) · bundle-report --check: within 300 kB, no
         Recharts, stats code or framer-motion animation features at start
i18n     1 895 keys per locale (+217), complete & in sync
browser  585/585 checks green (Chromium, 390×844, ru-RU), incl. 260 for stage 9: 46 offline
         sync & conflicts, 39 Settings → Интеграции, 175 accessibility — axe-core 4.14.0 on 50
         states × 2 themes = 100 audits: 0 serious/critical, 0 moderate, 0 minor, nothing skipped
smoke    26/26 green (stages 1–9), incl. 18: a stale base → 409 edit_conflict with the other
         device's row and nothing written, keep mine → 200 and replayed once; 19: the Notion card
         without Notion, a malformed secret refused before any request
OpenAPI  web/src/api/generated/schema.d.ts regenerated from the running API: no difference
```

Measured improvements (details and method in the part reports):

| | before | after |
| --- | --- | --- |
| Main chunk (raw / gzip) | 640.2 / 195.1 kB | 282.6 / 87.7 kB |
| Cold Today, first content (CPU ×4, Fast 4G) | 1716 ms | 1403 ms (−18 %; against the stage-8 base −28 % and −37 % in two earlier measurements) |
| Installed app, Today's ring | 1435 ms | 916 ms (−36 %) |
| API requests for seven tab switches | 28 | 8 |
| SQL statements per device request | — | 3 fewer (4 for `/auth/session`); the engine reads 10 fewer |
| axe serious + critical nodes (dark / light) | 141 / 218 | 0 / 0 |
| Controls under 48 px | 127 | 0 (5 documented exceptions) |

Independent review: an outside review of the stage-9 backend (authentication after the perf
change, the `base` contract, the Notion sync) and of the offline queue was started on this
branch and paused when test runs moved to the owner's machine; it runs there before stage 10,
and anything it finds ships as follow-up commits.

Screenshots (Playwright, 390×844, from the final run): offline — `docs/screenshots/9o-sync-sheet-offline.png`,
`9o-today-offline-pending.png`, `9o-plan-offline-edit.png`, `9o-sync-failed.png`,
`9o-sync-conflict.png`, `9o-editor-conflict.png`, `9o-journal-conflict.png`; Notion —
`60-notion-connect.png`, `61-notion-connect-light.png`, `62-notion-error.png`,
`63-notion-connected.png`, `64-notion-connected-light.png`; accessibility —
`90-a11y-today-{dark,light}.png`, `91-a11y-stats-{dark,light}.png`.

## 6. Decisions worth knowing

- **Nothing is dropped silently** (offline §4): a refusal stays `failed`, a 409
  `edit_conflict` stays a `conflict`, 401 keeps every entry until the device is paired again,
  a DELETE answered 404 counts as done. Transient failures stop the whole replay; refusals and
  conflicts do not. «Отправить всё сейчас» is the user's call and ignores the backoff.
- **`base` is optional and field-level** (offline §3): absent = last write wins (API clients
  and writes queued by older builds keep working); the editor's `base` is the value a field
  had when it was first touched, so a refetch that arrives while you type cannot turn your
  save into a silent overwrite. Scoping comes first: another user's row is a 404, never a
  409 that would show it.
- **Optimistic nutrition adds, never recomputes**: the day's totals stay the server's ± the
  waiting rows' own numbers; targets, scores and day boundaries are never computed on the
  client. `POST /nutrition/meals/copy` stays online-only (it creates rows without client ids,
  so a replay would duplicate them).
- **Notion is a mirror, not a second source of truth** (notion §6): edits made in Notion are
  overwritten by the next sync, and the app says so before anything is connected. Creates are
  announced and looked up after a lost answer, never blindly retried (Notion's docs: retry 5xx
  only for idempotent requests); written by property id, so renaming a column breaks nothing;
  one run per user (an advisory lock that dies with the process); a disconnect stops a run in
  flight at its next write. The API facts were verified against Notion's documentation
  (version `2026-03-11`, Oct 2026) — notion §7 lists where they differ from the brief.
- **Performance targets** (perf §2, §8): the main-chunk budget (≤ 300 kB) is met and enforced
  by `bundle-report.mjs --check`. Cold Today first content improved 18–37 % across three
  measurements on a shared, throttled box — the 30 % goal is not reliably met: the code has
  arrived by ~0.6 s and the rest is main-thread work (evaluating the start-up JS and React's
  first render of Today). The three engine reads (`/gamification/today`, `/gamification/profile`,
  `/stats/progress`) stay at 115–144 ms p95 because the engine re-aggregates the whole history
  on every read; the waste around them is gone. No migration: EXPLAIN found no hot query
  missing an index (perf §6).
- **Accessibility is fixed in the design system** (a11y §3): the tokens in
  `styles/index.css` and the `components/ui` primitives first, a screen's markup only where it
  was itself wrong — so the stage-10 redesign inherits it. Five target exceptions stay under
  48 px for a stated reason (bars of a 7-to-14-day chart, the week calendar's columns, time
  blocks whose size is the data); all meet WCAG 2.5.8 AA.

## 7. Not in stage 9 (by design)

- **Offline**: copy-meals offline; optimistic display of tasks *created* offline (they appear
  once sent, as in stage 6); `base` on the calendar drag, inbox clarify and bulk moves (last
  write wins there); conflict checks on tags, recipes, workouts and health days (single-device
  data, PUT by day); cross-tab coordination of the queue (idempotent ids make a double send
  harmless).
- **Notion**: Notion → DisciplineOS (one-way only); page content/blocks (notes are a property);
  resources, time entries, meals, sets and weigh-ins; webhooks. Backups and exports do not carry
  the connection — after a restore on another server the owner connects again.
- **Performance**: Dexie on demand (31 kB gzip, inside the offline queue), a first frame of
  Today without the cards below the fold, persisted per-day facts and incremental streaks for
  the engine reads, a server-side cache (invalidation on every write across workers).
- **Accessibility**: VoiceOver on a real iPhone (no device here; the check covers what
  VoiceOver depends on — names, roles, states, modality, focus order, live regions, language);
  text sizes in px follow zoom but not a browser's text-size setting (rem is a redesign-wide
  change); the calendar's drag has no keyboard equivalent on the grid (the task editor sets
  the same date and time).

## 8. Handed to stage 10 (redesign)

- Visible changes this stage made inside the design system (a11y §6): darker accent, violet,
  warn and pastel text shades in the light theme, white text on accent buttons; small buttons,
  segmented controls and chips are 48 px tall; the «+» floats 3 rem above the tab bar with
  «Статистика» as an icon tab under it; done tasks and future repeats are muted and struck
  through instead of faded.
- To revisit with the new layout: the five 48 px exceptions (wider bars, fewer columns), sizes
  in rem, the first frame of Today (what is above the fold), and every colour pair against
  the contrast check (`scripts/ui-checks/stage9-a11y.mjs` fails any serious or critical axe
  node in either theme).
