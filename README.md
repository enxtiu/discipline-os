# DisciplineOS

A single-user-now, multi-user-ready **personal discipline tracker**: nutrition,
training, steps, PARA tasks and planner, time tracking, gamification. Built as a
mobile-first PWA for iPhone (iOS 17+, Safari → Add to Home Screen), dark by
default, one-handed, every frequent action within two taps.

> **Status: stages 1, 2, 2b, 3 and 4 of 9 are complete** — foundation, nutrition
> core, nutrition backfill, AI logging (photo/text → review → save, camera
> barcode scanner) and training (templates, weekly plan, live logger, records,
> burned calories kept out of the food budget). The foundation is live: repository,
> infrastructure, the full database schema for every entity of the whole
> product, device-token auth with pairing, settings with target calculation, the
> design system and app shell with bottom navigation, i18n (ru/en), PWA basics
> and the operational layer (deploy, backups, health checks). Feature stages
> (nutrition, AI logging, training, health ingest, planner, push, gamification,
> offline hardening) plug into it without rewrites — see
> [`docs/stage-1.md`](docs/stage-1.md) for the stage report and
> [`docs/roadmap.md`](docs/roadmap.md) for what each later stage adds.

---

## Table of contents

- [What works today](#what-works-today)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Local development](#local-development)
- [Environment variables](#environment-variables)
- [Auth model](#auth-model)
- [Targets and safety rules](#targets-and-safety-rules)
- [Commands](#commands)
- [Testing](#testing)
- [Deployment](#deployment)
- [Backups and restore](#backups-and-restore)
- [iPhone setup: install the PWA](#iphone-setup-install-the-pwa)
- [iOS Shortcut recipe (steps and active energy)](#ios-shortcut-recipe-steps-and-active-energy)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [Stage 1 acceptance checklist](#stage-1-acceptance-checklist)

---

## What works today

| Area | Status |
| --- | --- |
| Pairing, device tokens, scoped API tokens, device list + revoke | ✅ |
| Settings: language, timezone, week start, quiet hours, units | ✅ |
| Body params + goal → calorie/macro/water/step targets (server-computed) | ✅ |
| Onboarding wizard with a live target preview | ✅ |
| Design system (dark/light, tokens, rings, sheets, skeletons, toasts) | ✅ |
| App shell: bottom tabs Today / Food / Train / Plan / Stats + quick add | ✅ |
| i18n ru (default) + en, per-device and per-user switch | ✅ |
| PWA: manifest, icons, service worker, offline shell, install flow | ✅ |
| Offline write queue with client UUIDs (Dexie) | ✅ infrastructure |
| Database schema for **all** 49 tables of every stage | ✅ |
| Docker Compose (postgres + api + caddy, automatic HTTPS) | ✅ |
| GitHub Actions: CI, build → GHCR → SSH deploy, nightly backup | ✅ |
| Encrypted backups (`pg_dump \| age`) + restore drill | ✅ |
| Food library importers (seed RU/EN, OFF, USDA) + ranked search + barcode lookup | ✅ stage 2 |
| Manual logging, favorites, recents, copy-previous-meal, recipes & portions | ✅ stage 2 |
| Daily nutrition dashboard (ring, macros, water, meals) in the user's timezone | ✅ stage 2 |
| 320-food curated RU library (one-tap confirm), USDA Foundation/SR Legacy with Russian names, live Open Food Facts barcode fallback, recipe yield/portions, pinned recipes on Today, search < 150 ms | ✅ stage 2b |
| AI meal logging: photo or text (ru/en) → local-library matching → review screen (editable grams, swap/remove/add) → explicit save, never auto-saved; provider abstraction (OpenAI / Anthropic / Google / OpenRouter / fake), versioned prompts, soft daily budget; optional 400 px thumbnail, original photo never stored | ✅ stage 3 |
| Camera barcode scanner (`@zxing/browser`, works in iOS Safari) with typed-digits fallback | ✅ stage 3 |
| Training: exercise library (ru/en search, your own exercises), templates, weekly plan, live workout logger with rest timer (sets work offline), personal records, quick cardio, burned calories on their own card/chart — never food budget | ✅ stage 4 |
| Health: iOS Shortcut ingest (scoped token, idempotent per day, lenient numbers, 62-day backfill), steps on Today with a manual fallback, weight (profile + targets follow) and sleep entries, a /health page with 14-day steps, 90-day weight and a sleep timeline | ✅ stage 5 |
| Planner, push, gamification | stages 6–8 |

---

## Architecture

```
 iPhone (Safari PWA)
        │  HTTPS
        ▼
   Caddy  ──────────────► /srv/www  static SPA build (immutable hashed assets)
   (TLS, HTTP/3)             │
        │ /api/*              └── service worker: app shell cache + offline page
        ▼
   FastAPI (uvicorn ×2)
        ├── SQLAlchemy 2 + Alembic ──► PostgreSQL 16
        ├── APScheduler (in-process jobs; notifications from stage 7)
        └── AI provider abstraction (stage 3: AI_PROVIDER / AI_MODEL_VISION / AI_MODEL_TEXT)
```

Three containers, one compose file, one `.env`. The frontend is a static bundle
served by Caddy from the same origin as the API, so there are no CORS problems in
production and the API URL in the client is always relative (`/api`).

**Multi-user ready from day one:** every table carries `user_id`, every query is
scoped through the authenticated principal, and the owner/device model already
supports several devices and several users — the UI simply has no user switcher
yet.

---

## Repository layout

```
discipline-os/
├── api/                     FastAPI service
│   ├── app/
│   │   ├── ai/              provider adapters, strict schema, runner, prompts/*.vN.md (stage 3)
│   │   ├── api/             routers (v1: auth, devices, tokens, settings, meta, foods, nutrition, ai, training)
│   │   ├── core/            config, logging, errors, security, rate limit, tz, ids
│   │   ├── models/          SQLAlchemy 2 models — every entity, every stage
│   │   ├── schemas/         pydantic v2 request/response models
│   │   ├── services/        pairing, tokens, targets, settings, scheduler, recurrence
│   │   ├── seeds/           seed framework + exercise library
│   │   └── cli.py           server-side admin commands
│   ├── alembic/versions/    migrations (0001 = the whole schema)
│   ├── importers/           seed (320 foods RU/EN), Open Food Facts, USDA FDC (+ RU names)
│   └── tests/               pytest suite (174 tests)
├── web/                     React 18 + TS + Vite PWA
│   ├── src/api/             fetch client, types, generated OpenAPI types
│   ├── src/components/      design system (ui/) + shell (layout/)
│   ├── src/features/        auth, today, food (stage 2), ai (stage 3), train (stage 4), settings, placeholders
│   ├── src/i18n/            ru.json / en.json (all strings live here)
│   ├── src/lib/             device, offline queue, formatting, errors, targets
│   └── public/              icons, offline.html, sw-push.js (stage 7)
├── docker/                  api.Dockerfile, caddy.Dockerfile, Caddyfile
├── deploy/                  install.sh, update.sh, healthcheck.sh, backup/
├── scripts/                 dev-api.sh, smoke.sh, check-i18n.mjs, ui-check.mjs
├── .github/workflows/       ci.yml, deploy.yml, backup.yml
├── docs/                    roadmap.md, stage-1/2/2b/3/4.md, screenshots/
├── docker-compose.yml       postgres + api + caddy (production)
├── docker-compose.dev.yml   overlay: published postgres, api with reload
└── Makefile                 every developer shortcut
```

---

## Local development

Requires Docker (for PostgreSQL), Python 3.12 and Node 20.

```bash
git clone <repo> discipline-os && cd discipline-os
cp .env.example .env
make install          # python venv + backend deps + npm install

# 1. database (published on 127.0.0.1:5432 by the dev overlay)
make dev-db

# 2. in .env, point the API at the local database:
#    DATABASE_URL=postgresql+psycopg://dsos:<password>@127.0.0.1:5432/dsos
make migrate          # alembic upgrade head
make seed-exercises   # optional: the exercise library (52 rows)

# 3. two terminals
make api              # http://localhost:8000/docs
make web              # http://localhost:5173  (proxies /api → :8000)

# 4. create the owner and pair your browser
make owner            # prints a one-time pairing code, valid 15 minutes
```

Prefer no Docker for PostgreSQL? Any reachable PostgreSQL 16 works as long as the
cluster was initialised with a real UTF-8 locale (`--locale=en_US.utf8`), which
`pg_trgm` needs for Cyrillic similarity. `scripts/dev-api.sh` starts the API
against a local cluster (§[Troubleshooting](#troubleshooting)).

**Regenerate the TypeScript API types** after changing a schema or a router:

```bash
make gen-api          # web/src/api/generated/schema.d.ts from the running API
```

The generated file is committed: CI and the Docker build typecheck without a live
API.

---

## Environment variables

All of them live in `.env` (copy from `.env.example`; never commit the real one).

### Deployment

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOMAIN` | — | Public hostname; Caddy obtains the Let's Encrypt certificate for it |
| `ACME_EMAIL` | — | Contact address for certificate expiry notices |
| `COMPOSE_PROJECT_NAME` | `disciplineos` | Compose project name |
| `API_IMAGE` / `WEB_IMAGE` | `ghcr.io/OWNER/…:latest` | Image coordinates; CI rewrites the tag per release |
| `VITE_APP_VERSION` | `prod` | Baked into the bundle and shown in Settings → Data |

### Database

| Variable | Purpose |
| --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credentials of the database container |
| `DATABASE_URL` | SQLAlchemy DSN used by the API (`postgresql+psycopg://…`) |

### API

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENV` | `development` | `development` \| `staging` \| `production` |
| `DEBUG` | `false` | Verbose errors (never enable in production) |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | Structured logging |
| `SECRET_KEY` | — | 32+ random chars: `openssl rand -hex 32`; hashes tokens and pairing codes |
| `MAX_DEVICES_DEFAULT` | `2` | Simultaneously active devices per user |
| `PAIRING_CODE_TTL_MINUTES` | `15` | One-time pairing code lifetime |
| `CROSS_DEVICE_PAIRING_CODE_TTL_MINUTES` | `0` | Optional small window in which one code may pair a second device |
| `API_TOKEN_PREFIX` | `dsos_pat_` | Prefix of scoped API tokens (so they are recognisable in logs) |
| `CORS_ORIGINS` | empty | Extra allowed origins; empty in production (same origin via Caddy) |
| `DEFAULT_TIMEZONE` | `Asia/Bangkok` | Used before the device reports its own |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `10` | Per-IP limit for pairing/refresh |
| `MAX_REQUEST_BYTES` | `4000000` | Any larger request body is refused with 413 before it is parsed (Caddy backstop: 6 MB) |
| `MEDIA_DIR` | `/data/media` | Opted-in meal thumbnails (the `api_media` volume in compose) |

### Scheduler

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCHEDULER_ENABLED` | `true` | APScheduler inside the API container |
| `HOUSEKEEPING_INTERVAL_MINUTES` | `15` | Expired pairing codes, ephemeral rows (stage 7 adds notifications) |

### AI (stage 3) and push (stage 7)

`AI_PROVIDER` (`openai` \| `anthropic` \| `google` \| `openrouter` \| `fake` —
deterministic, no key, for dev/CI), `AI_API_KEY`, `AI_BASE_URL`,
`AI_MODEL_VISION`, `AI_MODEL_TEXT` (cheaper model for text),
`AI_DAILY_CALL_LIMIT` (soft cap per day for the whole server, 0 = unlimited),
`AI_TIMEOUT_SECONDS`, `AI_RATE_LIMIT_PER_MINUTE` (per user),
`AI_MAX_IMAGE_BYTES`, `AI_MAX_IMAGE_PX`, `AI_MAX_TEXT_CHARS`,
`AI_PENDING_THUMB_TTL_HOURS`, `AI_JOB_PAYLOAD_RETENTION_DAYS` (stage 3; endpoints
`POST /api/v1/ai/analyze-meal`, `POST /api/v1/ai/parse-text`, `GET /api/v1/ai/usage`);
`OFF_LIVE_LOOKUP`, `OFF_BASE_URL`, `OFF_TIMEOUT_SECONDS`, `OFF_USER_AGENT`,
`OFF_LOOKUPS_PER_MINUTE`, `OFF_MISS_TTL_HOURS` (stage 2b: a barcode missing locally
is looked up once on Open Food Facts and cached);
`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` (generate with
`python -m app.cli vapid-keys`), `VAPID_SUBJECT`.

### Backups

| Variable | Purpose |
| --- | --- |
| `BACKUP_REPO` | Private git repository that receives the encrypted dumps |
| `BACKUP_LOCAL_DIR` | Local directory, default `/var/backups/dsos` |
| `BACKUP_RETENTION_DAYS` | Local retention, default `14` |
| `AGE_RECIPIENT` | `age` public key; the matching private key never leaves the VPS |

---

## Auth model

No passwords anywhere.

1. **Bootstrap.** On the server:
   `docker compose exec api python -m app.cli create-owner` (idempotent). It
   creates the owner account, its settings row, and prints a **one-time pairing
   code** (8 characters, `XXXX-XXXX`, valid 15 minutes, 1 use).
2. **Pairing.** The device posts the code to `POST /api/v1/auth/pair` together
   with its name, platform, model, timezone and language. The server returns a
   long-lived **device token** (stored hashed; the plaintext is shown once and
   kept only in the browser's storage). The pairing screen is the only UI a
   device can see without a token.
3. **Session.** Every request carries `Authorization: Bearer <token>`. The token
   resolves to a principal: `auth_kind = device` (full scopes) or `api_token`
   (restricted scopes such as `ingest:health`). A 401 from any request
   immediately tears the session down and returns the device to the pairing
   screen.
4. **Devices.** Settings → Devices lists every device with its name, platform
   and last-seen time; each can be renamed or revoked (immediate effect — the
   token stops working on the next request). A user cannot revoke their last
   active device; the limit is `MAX_DEVICES_DEFAULT` per user.
5. **Scoped API tokens** exist for automations (the iOS Shortcut): created in
   Settings → Access tokens or via `python -m app.cli api-token`. The plaintext
   is shown **once** next to a QR code; the list keeps prefix, scopes, last use
   and request count, and revocation is instant.

Scopes: `read:all`, `write:all`, `ingest:health`, `ingest:weight`, `ingest:sleep`
(stage 5 endpoints consume the latter three).

---

## Targets and safety rules

Targets are **always computed on the server** (`app/services/targets.py`) and
returned in the session payload, so the client never invents numbers.

- **BMR:** Mifflin-St Jeor by default; Katch-McArdle when body-fat percentage is
  known (`bmr_formula = auto` picks it, `mifflin`/`katch` forces one).
- **TDEE:** BMR × activity factor (sedentary 1.2 → very active 1.725+).
- **Goal:** `maintain`, `lose`, `gain` with preset deficits/surpluses of
  10 / 15 / 20 % (`goal_rate_pct`), all editable.
- **Protein:** g per kg body weight (default 2.0). **Fat:** a minimum g per kg
  (default 0.8). **Remaining kcal → carbs.** **Fiber:** 14 g per 1000 kcal.
  **Water:** 30–35 ml per kg (default 33).
- **Safe floor:** 1500 kcal for men, 1200 for women. The engine never recommends
  or rewards intake below it. Raising a manual target under the floor requires an
  explicit confirmation in the UI (`confirm_floor`) and is recorded.
- **Warnings** are returned as machine-readable codes
  (`aggressive_deficit`, `high_deficit`, `below_safe_floor`, `low_protein`,
  `missing_body_params`, …) and rendered through i18n.
- **Burned calories are never added to the food budget.** Training calories are
  shown in a separate card/chart (stage 8), never as extra allowance.
- All of it is overridable by hand (`targets_manual`), after which auto
  recomputation stops until you press *Recompute*.

---

## Commands

```bash
make help              # every target with its description
```

| Command | What it does |
| --- | --- |
| `make install` | venv + backend and frontend dependencies |
| `make dev-db` | PostgreSQL only, published on 127.0.0.1:5432 |
| `make migrate` | `alembic upgrade head` |
| `make migration m="…"` | autogenerate + normalise a migration |
| `make api` / `make web` | API with reload / Vite dev server |
| `make test` / `make lint` / `make fmt` / `make typecheck` | quality gates |
| `make check-i18n` | fail if any UI string is untranslated or ru/en drift |
| `make ui-check PAIRING_CODE=XXXX-XXXX` | browser acceptance run (Playwright/Chromium) |
| `make gen-api` | regenerate the TypeScript types from the live OpenAPI schema |
| `make build` / `make up` / `make down` / `make logs` | images and stack |
| `make owner` / `make pair` | create the owner / print a fresh pairing code |
| `make backup` / `make restore f=…` | encrypted backup / restore (see below) |
| `make seed-exercises` | load the exercise library |
| `make seed-foods` | load the curated RU/EN food library (320 rows, idempotent) |

Server-side admin CLI (`docker compose exec api python -m app.cli …`):
`create-owner`, `pairing-code`, `api-token`, `list-devices`, `revoke-device`,
`recompute-targets`, `seed`, `run-job`, `import-foods`, `bench-search`, `vapid-keys`, `stats`.

### Food library imports (stage 2b)

```bash
python -m app.cli import-foods --source seed                       # curated RU library, 320 rows
python -m app.cli import-foods --source usda --file FoodData_Central_foundation_food_json_<date>.json
python -m app.cli import-foods --source usda --file FoodData_Central_csv_<date>/ --dataset sr_legacy
python -m app.cli import-foods --source off  --file off-ru.jsonl  # complete nutriments + ru/en names only
python -m app.cli bench-search                                     # search latency on this DB (budget 150 ms)
```

Download the FDC files from fdc.nal.usda.gov → Download Datasets (prefer the CSV
bundle for SR Legacy: it is streamed). Russian names come from
`api/app/importers/data/usda_ru.json`; edit it and re-import to update rows in
place. New library rows are **unverified** until the owner taps *Подтвердить*.

---

## Testing

```bash
# backend: 174 tests — calorie math, food search & ranking (incl. the 150 ms
# budget on a 50k-row library), importers (seed/USDA/OFF), live barcode fallback
# (mocked), meal logging, recipes, day boundaries, recurrence, tokens/devices,
# AI logging (fake provider + every adapter behind httpx.MockTransport), training
# (search, templates, plan, idempotent sets, records, burned kcal, bounds)
cd api
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/dsos_test \
  SECRET_KEY=test-secret-key-please-change-0123456789 .venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .

# frontend: types + production build (also verifies the PWA generation)
cd web && npm run typecheck && npm run build

# end-to-end against a running API (20 checks: pairing, scopes, targets, search
# budget, barcode, recipes, AI parse without auto-save, 413 upload guard, a workout
# end to end with food totals untouched, revoke)
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>

# i18n gate (also runs in the CI web job): every key the UI uses exists in both
# locales, the locales do not drift, dynamic prefixes resolve
node scripts/check-i18n.mjs

# browser acceptance: 112 checks in a real Chromium — pairing gate, code entry,
# the 4-step onboarding with the live target preview, Today, quick add,
# settings (language / theme / timezone), every route, the built PWA manifest,
# food logging, recipes, AI text/photo review → save, offline AI message, the
# camera barcode scanner, and training (template, plan, live sets incl. offline,
# finish, records, cardio, burned kcal apart from food), and health (Shortcut token + ingest,
# manual steps incl. offline, weight → profile, sleep, /health; cleans up after itself)
# (FAKE_CAMERA plays an EAN-13 video:
# python3 scripts/make-barcode-y4m.py 460700123456 /tmp/barcode.y4m).
# Needs a running api (AI_PROVIDER=fake) + web and a fresh single-use code:
npm i -D playwright && npx playwright install chromium   # once per machine
cd api && .venv/bin/python -m app.cli pairing-code       # copy the printed code
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs   # screenshots → $SHOTS
```

What the suite covers: pairing-code lifecycle and single use, device limits,
token revocation, scope enforcement (`ingest:health` cannot read `/v1/settings`),
Mifflin/Katch BMR, activity factors, deficit presets, the safe floor, macro
split, water/fiber/steps, warning codes, the manual-target override and
recompute, Russian/English full-text search with trigram typo tolerance,
barcode lookup, RRULE expansion across DST, and seed idempotency.

---

## Deployment

### 1. Prepare the VPS

Any small Debian/Ubuntu box with 1 vCPU / 1 GB RAM / 20 GB disk is plenty
(`caddy` + `api` + `postgres`). Point an `A` record for `DOMAIN` at the server
before the first run so Let's Encrypt can validate it.

### 2. Bootstrap

```bash
ssh root@your-server
bash <(curl -fsSL https://raw.githubusercontent.com/OWNER/discipline-os/main/deploy/install.sh)
```

`deploy/install.sh` installs Docker + the compose plugin + `age`, opens
22/80/443, clones the repository into `/opt/disciplineos`, generates the secrets
into a `chmod 600 .env`, pulls the images, starts the stack, runs migrations and
creates the owner. It is idempotent — re-run it any time.

If the GHCR packages are private, give the server a token:

```bash
echo <PAT-with-read:packages> | docker login ghcr.io -u OWNER --password-stdin
```

### 3. Continuous deployment

Push to `main` → `.github/workflows/deploy.yml` builds both images, pushes
`ghcr.io/<owner>/disciplineos-{api,web}:sha-<12>` plus `:latest`, then SSHes in and
runs `deploy/update.sh <tag>` (which is exactly
`docker compose pull && docker compose up -d` plus health gating and an automatic
rollback). Repository secrets:

| Secret | Meaning |
| --- | --- |
| `SSH_HOST` | Server IP/hostname |
| `SSH_USER` | Deploy user, member of the `docker` group |
| `SSH_KEY` | Private key; add its public half to `~/.ssh/authorized_keys` |
| `SSH_PORT` | Optional, default 22 |
| `DEPLOY_PATH` | Checkout path, e.g. `/opt/disciplineos` |

Add `DOMAIN` as a repository **variable** for the post-deploy smoke URL, and
`DEPLOY_ENABLED=true` once the secrets are in place — until then pushes to `main`
only run CI (tests, lint, build); image builds, the rollout and the nightly backup
workflow stay off.

### 4. Manual deploy / rollback

```bash
cd /opt/disciplineos
./deploy/update.sh sha-1a2b3c4d5e6f     # pull, recreate, health-gate, auto-rollback
API_IMAGE=ghcr.io/owner/disciplineos-api:sha-<previous> ...
./deploy/update.sh latest               # back to the newest published image
```

### 5. Monitoring

```bash
*/5 * * * * /opt/disciplineos/deploy/healthcheck.sh >> /var/log/dsos-health.log 2>&1
```

Checks the public health endpoint, that the scheduler actually holds a scheduled
run (a *paused* APScheduler job is silent otherwise), container state, database
reachability, disk space, TLS expiry and backup age. Exit code 1 on any failure;
set `ALERT_WEBHOOK_URL` in the environment to POST a JSON alert.

---

## Backups and restore

Nightly at 03:15 UTC, `.github/workflows/backup.yml` runs
`deploy/backup/backup.sh` on the server:

```
pg_dump (custom format, zstd)  ──►  age --encrypt -r $AGE_RECIPIENT  ──►  /var/backups/dsos/<stamp>/dsos-<stamp>.dump.age
                                                                          │
                                            14 days local retention ◄─────┤
                                                                          ▼
                                        git push → private repo (BACKUP_REPO)
```

The plaintext dump never touches the disk: `pg_dump` streams through the pipe
into `age`. Only the ciphertext is stored, locally and in the private repository.

**Key management.** `deploy/install.sh` generates `/root/.config/age/dsos.key`
and puts its public half in `.env` as `AGE_RECIPIENT`. **Copy that private key
into your password manager.** Without it every backup is unreadable — that is the
whole point.

**Restore drill** (do this once now, not on the day you need it):

```bash
cd /opt/disciplineos
./deploy/backup/restore.sh /var/backups/dsos/<stamp>/dsos-<stamp>.dump.age --into dsos_drill
# then inspect dsos_drill; drop it when satisfied

# a real restore (takes a safety dump of the live database first)
./deploy/backup/restore.sh /var/backups/dsos/<stamp>/dsos-<stamp>.dump.age
python -m app.cli stats          # users/devices/settings sanity check
```

Backups include everything you cannot regenerate: devices and their tokens,
settings, targets, and every logged row. `postgres` is the stateful volume that
matters; `caddy_data` only holds certificates, which Caddy re-issues
automatically. `api_media` holds the optional 400 px meal thumbnails (stage 3);
it is **not** in the `pg_dump` backup — copy the volume too if you want the
pictures to survive a server loss (the meals themselves are in the database).

---

## iPhone setup: install the PWA

1. Open `https://<DOMAIN>` in **Safari** (iOS 17+).
2. Share → **Add to Home Screen** → Add.
3. Launch it from the home screen; the app opens standalone (no Safari chrome)
   and respects the safe areas.
4. Pair the device with the code from `make owner` / `make pair`.
5. Finish the onboarding wizard (body params → goal → targets preview).

Web push (stage 7) **only** works for a PWA launched from the home screen — iOS
does not deliver notifications to a Safari tab. The app detects standalone mode
and tells you when that is the missing piece. Voice input for meal notes uses the
iOS keyboard microphone inside the ordinary text field; there is no in-app
recorder.

---

## iOS Shortcut recipe (steps and active energy)

Goal: get iPhone step count, active energy and distance into DisciplineOS twice
a day, without installing anything on the phone.

The app walks you through it: **Settings → Команда iOS** creates a token that
can only send health data, shows the exact URL and header with copy buttons,
the six steps below and when the Shortcut last sent data. A token can also be
made on the server: `python -m app.cli api-token --name "Shortcut" --scopes ingest:health`.

**The request** (idempotent: the same day is updated, never duplicated):

```http
POST https://<DOMAIN>/api/v1/ingest/health
Authorization: Bearer dsos_pat_…
Content-Type: application/json

{ "steps": 8342, "active_kcal": 512.5, "distance_km": 6.2 }
```

- `date` is optional — without it the server uses *your* today (timezone from
  Settings). A full timestamp is converted to your timezone.
- Numbers may arrive as Health formats them: `"8 342"`, `"512,5"`, `"8,342"`
  (en grouping) and `""` (an empty sum) all work. `distance_m` works too.
- Fields you do not send keep their value for that day; a second run at 22:00
  just raises the numbers. Typing steps by hand in the app replaces the day
  until the next run (and does not count as a sync).
- Backfill: `POST /api/v1/ingest/health/batch` with `{"days": [{…}, …]}` (up to 62).
- The token gets 403 everywhere else and is limited to 60 requests a minute.

**Building the Shortcut (iOS 17/18):**

1. Settings → Команда iOS → **Создать токен**, copy it.
2. Shortcuts → **+** → **Find Health Samples**: type *Steps*, start date *today*;
   then **Calculate Statistics** → *Sum*.
3. Repeat for **Active Energy** and **Walking + Running Distance** (km).
4. **Get Contents of URL**: the URL above, method **POST**, header
   `Authorization: Bearer <token>`, request body **JSON** with `steps`,
   `active_kcal`, `distance_km` set to the three statistics.
5. Run it once: the Settings screen says "только что", Today shows the steps.
6. **Automation** → Time of Day → 13:00, and another at 22:00 → *Run Immediately*.

**Manual fallback:** Today → steps → «Ввести шаги» (also from the + button).
Nothing depends on the Shortcut working.

**Weight and sleep** are entered in the app (Today → «Вес и сон»). If you want a
smart scale or a sleep app to push them through Health, give the token
`ingest:weight` / `ingest:sleep` («Также разрешить вес и сон») and call
`POST /api/v1/ingest/weight` with `{"weight_kg": 80.4}` or
`POST /api/v1/ingest/sleep` with `{"bed_at": "…", "wake_at": "…"}` (ISO
timestamps; the night is stored on the wake-up day). A pushed weigh-in keeps the
waist and note typed in the app.

**Calories:** Health's active energy (or, without it, an estimate from steps,
height and weight) is shown on its own tile next to workouts — never added to
the food budget. Active energy from Health already contains workouts recorded
by a watch, so the two numbers are not meant to be added up.

---

## Troubleshooting

**`pg_trgm` returns 0 for every Russian query.** The database cluster was
initialised with the `C`/`POSIX` locale. Trigrams for Cyrillic need a real UTF-8
locale: keep `POSTGRES_INITDB_ARGS: "--encoding=UTF8 --locale=en_US.utf8"` in
compose (it only applies at first `initdb`), or re-create the cluster. The
health endpoint reports `fuzzy_search: true/false`.

**A scheduled job never fires.** APScheduler jobs created with
`next_run_time=None` are *paused*. `/api/v1/meta/health` lists each job with its
`next_run_time` — it must never be `null`. `deploy/healthcheck.sh` fails loudly
if it is.

**The pairing code is rejected.** Codes are single-use and live 15 minutes
(`PAIRING_CODE_TTL_MINUTES`). Print a new one with `make pair`. An expired or
already spent code answers `401 invalid_pairing_code` — deliberately
indistinguishable from a code that never existed.

**"Device limit reached (2/2)".** Revoke an old device in Settings → Devices, or
raise `MAX_DEVICES_DEFAULT` in `.env` and restart the API.

**Caddy cannot obtain a certificate.** The `A` record must already point at the
server and port 80 must be reachable (Let's Encrypt HTTP-01). Check
`docker compose logs caddy`; the certificate lives in the `caddy_data` volume, so
delete it only if you intend to re-issue.

**The SPA loads but every request 401s.** The device token was revoked (or the
database was restored from a different instance). Open the site: it shows the
pairing screen and you can pair again with a fresh code.

**No Docker locally.** Use any PostgreSQL 16 with a UTF-8 locale:
`scripts/dev-api.sh` starts uvicorn against
`DATABASE_URL=postgresql+psycopg://…@127.0.0.1:55432/dsos` (see the Makefile for
the migrate/test equivalents).

**Type errors after pulling a schema change.** Regenerate:
`make gen-api` (needs the API running) and commit the updated file.

---

## Security notes

- Secrets live only in the server `.env` (mode 600), GitHub Secrets and the age
  private key — never in the repository, never in an image layer.
- Device tokens and API tokens are stored **hashed**; the plaintext exists exactly
  once, in the response that created it.
- Pairing codes are single-use, short-lived and rate-limited per IP.
- Every query is scoped by `user_id`; a scoped API token can only reach the
  endpoints its scopes allow (verified by tests and by `scripts/smoke.sh`).
- Rate limits apply to pairing/refresh (`AUTH_RATE_LIMIT_PER_MINUTE`) and will
  cover the AI endpoints from stage 3 (`AI_DAILY_CALL_LIMIT`).
- Caddy terminates TLS with HTTP/3, sets `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff` and a referrer policy; the service worker is
  served `no-store`.
- The postgres port is never published in production — only reachable inside the
  compose network.

---

## Stage 1 acceptance checklist

- [x] Repository, compose stack (postgres + api + caddy), Makefile, `.env.example`
- [x] Database schema for all 49 tables of every stage + an idempotent migration
- [x] Seeding framework + exercise library (52 exercises, ru/en, MET, idempotent)
- [x] Device-token auth: one-time pairing code → device token, revocation, limit
- [x] Scoped API tokens with QR code for Shortcuts
- [x] Settings: language, timezone, quiet hours, units, body params, targets
- [x] Target engine with safe floors and warning codes + unit tests
- [x] Design system, app shell, bottom navigation, quick-add sheet, rings
- [x] i18n ru/en for every string, Russian by default
- [x] PWA: manifest, icons, service worker, offline shell, install detection
- [x] Offline write queue (client UUIDs, idempotent replay) — infrastructure
- [x] Docker images for api and web, CI (lint/tests/typecheck/build)
- [x] CD: build → GHCR → SSH deploy with health gating and rollback
- [x] Encrypted nightly backups + restore script + health check cron
- [x] README: setup, env vars, deploy, backup/restore, iOS Shortcut recipe
- [x] `pytest` 122 passed · `ruff` clean · `tsc` clean · production build with PWA
- [x] `scripts/smoke.sh` — 17 end-to-end checks green against the live API (incl. stage 2b)
- [x] `scripts/ui-check.mjs` — 67 browser acceptance checks green in Chromium (incl. nutrition + stage 2b)
- [x] `scripts/check-i18n.mjs` — 339 keys per locale, every UI key resolved (CI gate)

Stage 2 (food importers, ranked search, logging, recipes, the daily nutrition
dashboard) is delivered — see `docs/stage-2.md` — and stage 2b closed its gaps
(320-food seed, USDA + RU names, OFF fallback, recipe yield/portions, the search
budget) — see `docs/stage-2b.md`. Stage 3 (AI photo/text logging, barcode
scanning, `docs/stage-3.md`), stage 4 (training, `docs/stage-4.md`) and stage 5
(health ingest: the iOS Shortcut, steps, weight, sleep — `docs/stage-5.md`) are
delivered; stage 6 (the planner) is next.
