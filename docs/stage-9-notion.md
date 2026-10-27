# Stage 9 · part B — optional Notion sync · delivery report

> A **one-way mirror** of the planner and of the days into Notion: areas, projects,
> tasks, the journal and one row per day (food against the target, protein, water,
> steps, sleep, workouts with their burned calories in their own columns, tracked
> time, tasks done, the day score). **Off until the owner connects it**; it then
> **creates its own databases** on a page the owner shares with an internal Notion
> connection. DisciplineOS stays the source of truth — edits made in Notion are
> overwritten by the next sync, and the app says so before anything is connected.
>
> - Migration **0004** adds two tables of its own (`integrations`, `integration_links`);
>   nothing earlier is reshaped; its downgrade drops exactly those two tables.
> - No new package: `cryptography==50.0.2` (already installed through `pywebpush` /
>   `py-vapid`) is now pinned explicitly in `api/requirements.txt` for Fernet + HKDF.
> - No request to Notion in any test: a fake Notion behind `httpx.MockTransport`.

## 1. Plan that was executed

| # | Brief item | Delivered |
| --- | --- | --- |
| 1 | Storage (0004, scoped by user) | `integrations` (id, user_id, provider `notion`, status `connected\|error\|disconnected`, `secret_encrypted`, `config` JSONB — page id/title/url, bot and workspace names, enabled kinds, per-kind `{database_id, data_source_id, url, lang, state, props, relations}` — `generation`, `run_id/run_started_at/run_finished_at`, `last_sync_at`, `last_error`, `counts` JSONB, timestamps; unique (user_id, provider)) and `integration_links` (integration_id, user_id, entity_type, entity_id, external_id, content_hash, in_trash, synced_at, timestamps; unique (integration_id, entity_type, entity_id)). |
| 2 | Secret at rest | Fernet with a key derived by HKDF-SHA256 (info `dsos-integrations-v1`) from `INTEGRATIONS_KEY`, else `SECRET_KEY` (`app/core/secret_box.py`). Never returned (responses say `secret_set`), never logged (asserted against every structlog event and every stdlib record of a whole connect → setup → sync → failure session), not in exports or backups. A key change makes it unreadable → status `error` / `notion_secret_unreadable`, the card asks for the secret again. |
| 3 | Client | `app/services/notion/client.py`: fixed `https://api.notion.com/v1`, `Notion-Version: 2026-03-11` in one constant, ids normalised to UUIDs before they reach a path, no redirects, read timeout 30 s / write 75 s (longer than Notion's own 60 s, so a slow create gets its 503 + `committed_resource_id`), pace ≤ 3 req/s, a per-run request budget, `Retry-After` honoured up to 60 s (longer → the run stops and the next one resumes), 429/529 and `conflict_error` retried, 5xx and timeouts retried **only for idempotent requests**, typed errors. |
| 4 | Sync engine | `app/services/notion/sync.py` + `schema.py` + `mapping.py` — see §6. Kinds `areas`, `projects`, `tasks`, `journal`, `days`; `days` reuses `day_metrics.collect` and the stored daily scores (nothing recomputed); burned kcal are their own columns. Connect validates locally (secret format, page link or id), then `GET /users/me` and `GET /pages/{id}`. Setup creates one database per enabled kind in dependency order with a Russian-first schema (English when the owner's language is English), reads the property ids back and writes by id; relations task→project/area/parent and project→area are two-way. Each run hashes every row, creates / updates / trashes only what changed, records each result at once; idempotent, resumable, one run per user (advisory lock); a 404/403 database is marked missing and «Создать заново» is offered; a deleted column is repaired. Notes go into a rich_text property, split into ≤ 2 000-character text objects (UTF-16), capped at 10. |
| 5 | Job + endpoints | `notion_sync` every 10 min via `app/services/hooks.py` (no integration → nothing at all). `GET /integrations`, `GET /integrations/notion`, `POST /integrations/notion/connect` (201), `POST /integrations/notion/setup {kinds}`, `PATCH /integrations/notion {kinds}`, `POST /integrations/notion/sync` (202 + run id; 409 `notion_sync_running`), `DELETE /integrations/notion` (204; Notion content stays). Device principal only (API token → 403 `device_only`), writes 20/min, connects 6/min per user. Error codes: `notion_secret_invalid`, `notion_page_invalid`, `notion_unauthorized`, `notion_forbidden`, `notion_page_not_shared`, `notion_page_in_trash`, `notion_rate_limited`, `notion_unavailable`, `notion_not_connected`, `notion_sync_running`, `notion_secret_unreadable`, `notion_database_missing` (run result), `notion_rejected`, `notion_error`. |
| 6 | Tests | `api/tests/test_stage9_notion.py` (57) with `tests/stage9_notion_helpers.py` (`FakeNotion`, `FakeClock`) — every item of the brief's list, see §5. |
| F | Settings → Интеграции | `web/src/features/integrations/` (lazy chunk): the not-connected card (what it does, one-way, the verified steps, a link to the Developer portal, a password field for the secret, the page link, «Подключить»), the connected card (page with «Открыть в Notion», connection/workspace names, per-kind switches with «Открыть в Notion», «Создать базы» / «Создать заново», the last run in words, «Синхронизировать сейчас» polling until done, «Отключить» with a confirmation saying the Notion data stays, the error state with «Ввести новый секрет»). ru + en, 48 px targets, existing tokens/components only. |
| F | Browser checks | `scripts/ui-checks/stage9-notion.mjs` (in the module list): 40 checks, screenshots 390×844 dark + light. |

## 2. New & changed files

```
api/alembic/versions/0004_integrations.py   ★ the two tables (self-contained up/down)
api/app/models/integrations.py              ★ Integration, IntegrationLink (+ models/__init__.py exports)
api/app/core/secret_box.py                  ★ Fernet + HKDF sealing of provider secrets
api/app/core/config.py                      + INTEGRATIONS_KEY, NOTION_REQUEST_BUDGET, NOTION_MAX_RPS
api/app/services/notion/client.py           ★ the pinned, paced, budgeted, retrying client
api/app/services/notion/schema.py           ★ databases and properties (ru/en, stable keys, relations)
api/app/services/notion/mapping.py          ★ rows → property values (dates with time_zone, 2 000-char text)
api/app/services/notion/sync.py             ★ connect, setup, reconcile/repair, the run, lock, job
api/app/schemas/integrations.py             ★ request/response models (the secret is SecretStr)
api/app/api/v1/integrations.py              ★ /integrations/* (device only, rate-limited)
api/app/api/v1/presenters.py · router.py    + notion_integration_out / integration_summary_out, the mount
api/app/services/hooks.py                   + notion_sync (every 10 min)
api/requirements.txt                        + cryptography==50.0.2 (same version, explicit)
api/tests/test_stage9_notion.py · stage9_notion_helpers.py   ★ 57 tests, the fake Notion
api/tests/test_stage8_export.py             the schema version is the alembic head (was a fixed 0003)
web/src/features/integrations/              ★ types (OpenAPI drift guard), api, queries, IntegrationsScreen
web/src/App.tsx · features/settings/SettingsPage.tsx   + the lazy route and the hub row
web/src/api/generated/schema.d.ts           regenerated (+ /integrations/*)
web/src/i18n/locales/{ru,en}.json           + integrations.* and errors.notion_* (90 keys each)
scripts/ui-checks/stage9-notion.mjs         ★ 40 browser checks; scripts/ui-check.mjs loads it
README.md · .env.example                    «Notion (optional)», the three settings
docs/screenshots/60–64-notion-*.png         ★ not connected (dark, light), an error, connected (dark, light)
```

## 3. Commands

```bash
cd api && TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/dsos_test_s9notion \
  .venv/bin/pytest tests/test_stage9_notion.py        # the fake Notion, no network
cd api && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build && node ../scripts/check-i18n.mjs
PAIRING_CODE=XXXX-XXXX WEB_URL=http://127.0.0.1:5216 node scripts/ui-check.mjs
# a run by hand (the job does the same every 10 minutes)
docker compose exec api python -m app.cli run-job notion_sync
```

## 4. What to test (walkthrough)

1. **Настройки → Интеграции**: the card explains the one-way copy and the steps; tap
   «Открыть Notion Developer portal».
2. In Notion: Internal connections → Create a new connection → Configuration: Read,
   Update, Insert content → copy the API token. Create a page «DisciplineOS» → ⋯ →
   Connections → Add connection → the connection → ⋯ → Copy link.
3. Paste both → «Подключить» → the card shows the page and the five kinds →
   «Создать базы» → five databases appear on the page and fill within a minute.
4. Edit a task in the app → «Синхронизировать сейчас» → only that row changes in Notion.
   Delete a task → its page goes to the Notion trash. Edit a cell in Notion → the next
   sync puts the app's value back.
5. Delete «Проекты» in Notion → the next sync marks it «База удалена…» → «Создать
   заново» → a new database, tasks point at it again.
6. «Отключить Notion» → confirm → the card is back to the start; everything stays in Notion.

## 5. Verification evidence (this build)

See the final report of this branch for the exact gate output; the module itself:

```
api   tests/test_stage9_notion.py — 57 passed (the fake Notion, no network)
web   tsc clean (incl. the OpenAPI drift guard) · vite build: IntegrationsScreen is its own
      lazy chunk (13.4 kB) · i18n complete and in sync
browser  stage9-notion: 40/40 (not connected, validation, the server's 422s in words, no
      secret rendered back or kept in storage, 48 px targets, light theme; the connected
      card from a fixture), screenshots 60–64
```

What the tests prove (each with the fake Notion recording every request): connect with a
good secret (only `GET /users/me` and `GET /pages/{id}`, version header and bearer
checked, the column holds a Fernet token that decrypts to the secret), a wrong secret,
a page not shared, a page in the trash, eleven link forms, obvious mistakes refused with no
request; setup creates five databases under the page with the right parent, version,
Russian/English schema, descriptions and two-way relations (the self-relation added after
creation), idempotently; the first sync creates every row exactly once with its values
(dates with `time_zone`, relations to the right pages, subtasks after parents); burned
kcal never touch «Съедено»; an unchanged run sends **no request at all**; one edit →
one `PATCH`; delete/archive → trash, restore → back (restored, not created again); a page
trashed or deleted in Notion is brought back; `Retry-After: 7` waits 7 s, pacing ≥ 1/3 s
between requests; `Retry-After: 3600` ends the run (`partial`, `notion_rate_limited`) and
the next run finishes; 502 retried for updates; a create saved but answered 500, a timeout
after saving, an answer without an id → looked up by «ID DisciplineOS», never duplicated;
503 + `committed_resource_id` adopted; a budget of 4 requests → many partial runs, every
row once; two runs at once → one `busy`; the 202 endpoint, 409 while a run (or the job)
holds the lock, an interrupted run reported as such; disconnect wipes the secret column
and the links, the job sends nothing afterwards, a run in flight stops at its next write;
kinds switched off are left alone; a deleted/trashed database → `missing`, the others go
on, «Создать заново» re-points the relations; a deleted column (and a deleted relation) is
put back; a refused row is counted and not retried until it changes; notes split into
≤ 2 000-unit objects with emoji; `notion_unauthorized` → status `error`, the job skips it,
a new secret for the same page keeps the databases and links; missing capabilities
reported; a secret sealed with another key reported; two users mirror only their own rows
with their own secrets; an API token gets 403 everywhere; write and connect rate limits;
the secret in no response, OpenAPI document, structlog event or stdlib log record.

## 6. Decisions worth knowing

- **Creates are announced, never blindly retried.** Notion's docs say to retry 5xx only
  for idempotent requests (a write answered 503 may have been saved). A create commits a
  link without page id *before* the request; if the answer is lost (timeout, 5xx, an
  answer without an id) the row stays "pending" and the next run looks it up by its
  «ID DisciplineOS» property before creating anything. `committed_resource_id` is adopted
  at once. Updates and trashes set absolute values, so they are retried like reads.
- **Hash of the exact payload.** The hash covers the property ids and values that are
  sent, so a repaired column or a relation that can now resolve updates the row; an
  unchanged row costs nothing. Relations are written from the links held, in dependency
  order (areas → projects → tasks, parents before subtasks).
- **Written by property id, options by name in the database's language.** Renaming a
  column in Notion breaks nothing; a column deleted there is added back (with
  « (DisciplineOS)» if its name was taken) the next time a row of that kind changes.
- **One run per user**: a session-level advisory lock on a connection of its own (dies
  with the process — never stale); the card reads `pg_locks`, so a run killed by a restart
  shows as «прервалась», not as running forever. Each link write locks the integration
  row and checks `generation`: a disconnect (or a new connect) stops a run in flight at its
  next write and nothing is written afterwards.
- **Days**: the last 365 days with any data; older day rows are left alone (never
  trashed); a day whose data was all deleted is trashed. The day's target comes from its
  stored daily score when there is one (frozen history), else the current target — the
  same objects the stats screens use; burned calories are their own columns.
- **Errors that need the owner** (`notion_unauthorized`, `notion_secret_unreadable`,
  `notion_forbidden`, `notion_page_not_shared`) put the card in `error`; the job does not
  retry the first two on its own (a new secret is needed), «Синхронизировать сейчас»
  still can. Transient ones (rate limit, unavailable) leave it `connected`; the next run
  retries.
- **The connected card in the browser checks comes from a fixture.** The dev API has no
  Notion; everything that does not need Notion (the not-connected state, the server's
  422s) is real. The base URL is fixed — no test hook in the API.

## 7. Notion API facts verified (Oct 2026) and where the docs differ from the brief

- `Notion-Version` is required; latest `2026-03-11` (developers.notion.com/reference/versioning);
  it renamed `archived` → `in_trash`, `after` → `position`, `transcription` → `meeting_notes`
  (…/guides/get-started/upgrade-guide-2026-03-11); `2025-09-03` split databases into data
  sources (…/reference/changes-by-version).
- Internal connections are created in the Developer portal, <https://app.notion.com/developers/connections>
  → Internal connections → Create a new connection; the token is on the **Configuration**
  tab, with the capabilities (read / update / insert content) (…/guides/get-started/internal-connections,
  …/docs/authorization). Sharing: page → ⋯ → Connections → Add connection. *The docs do
  not specify a token format* — the API-keys guide only shows `ntn_…` as an example — so
  the server checks printable ASCII, 20–512 characters, and lets Notion decide.
  Personal access tokens also exist (expire ≤ 1 year) (…/guides/get-started/personal-access-tokens).
- Create database: `parent {type: page_id}`, `title`, `description`, `icon`,
  `initial_data_source.properties`; response `data_sources[{id, name}]`
  (…/reference/database-create). Property configs accept `description` (≤ 280) and
  relations `{data_source_id, type: single_property|dual_property, dual_property:
  {synced_property_name}}` (…/reference/property-object). Update data source: add by name,
  remove with `null`, keyed by name or id (…/reference/update-a-data-source).
- Create page under `{type: data_source_id}`; property names **or ids** (…/reference/post-page);
  update page with `properties` and/or `in_trash` (…/reference/patch-page,
  …/reference/archive-a-page). Values: title/rich_text arrays, number, select / multi_select
  by name (no commas; a new name adds an option), date `{start, end, time_zone}` — with
  `time_zone` the times carry no offset —, checkbox, relation `[{id}]`, url; clearing with
  `[]`/`null` (…/reference/page-property-values).
- Limits: 2 000 characters per text object and per URL, 100 elements per array, 500 KB per
  request; rate 180 requests/min per connection (600 on Business/Enterprise) over a fixed
  60-second window since 2026-09-09, plus a per-workspace limit since 2026-06-16; 429
  `rate_limited` with `Retry-After` in seconds (≤ 60 for the per-connection window, longer
  for the workspace one), repeated in `additional_data.retry_after`; 529
  `service_overload` like a 429 (…/reference/request-limits, …/page/changelog).
- Errors: 400 `validation_error`…, 401 `unauthorized`, 403 `restricted_resource`, 404
  `object_not_found` (also "not shared with the connection"), 409 `conflict_error`, 429,
  500/502/503/504 (…/reference/status-codes, …/reference/errors).
- **Differences from the brief (the docs win):** 5xx and timeouts are retried only for
  idempotent requests, not for creates (handled by announce + look-up and
  `committed_resource_id`); 529 and `conflict_error` are retried too, a 429
  `public_api_request_blocked` is not; the Developer portal URL is
  `app.notion.com/developers/connections` (the changelog also names
  `www.notion.so/developers`); generated page links moved to
  `https://app.notion.com/p/{id}` in June 2026 — the parser accepts both forms (ids are
  stored, links never requested).

## 7½. Not in this part (by design)

- Notion → DisciplineOS (one-way only); page content/blocks (notes are a property);
  resources, time entries, meals, sets and weigh-ins; webhooks.
- A database create whose answer is lost *without* `committed_resource_id` (a network
  failure, not Notion's 503) can leave an empty extra database on the page if setup is
  repeated — the docs do not guarantee that a `child_database` block id is the database
  id, so it is not looked up.
- Backups/exports do not carry the connection (the secret must not leave the server);
  after a restore on another server the owner connects again.
