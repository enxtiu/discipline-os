# Stage 3 — AI logging & barcode scanner · delivery report

> A meal can now be logged from a photo, from a sentence (Russian or English,
> typed or dictated with the iOS keyboard) or by scanning a barcode. The AI only
> **proposes**: every suggestion is matched against the local food library first,
> shown on a review screen, and written to the diary only when the user presses
> **Записать**.
>
> - No migration: `ai_jobs`, `ai_usage_daily`, the meal provenance columns and
>   `user_settings.keep_photo_thumbnail` were already in migration 0001.
> - No dependency changes: `@zxing/browser` 0.1.5 was already pinned; the
>   provider adapters use the existing `httpx`.

## 1. Plan that was executed

| # | Spec item | Delivered |
| --- | --- | --- |
| 1 | Provider abstraction by env (`AI_PROVIDER`, `AI_MODEL_VISION`, `AI_MODEL_TEXT`, `AI_DAILY_CALL_LIMIT`), fake provider | `app/ai/providers.py`: OpenAI and OpenRouter (chat completions, `response_format: json_schema` strict), Anthropic (messages with a forced tool), Google Gemini (`responseSchema`). Errors are classified transient (timeout, 429, 5xx) or permanent. `app/ai/fake.py` is a deterministic provider (rule-based ru/en text parser, a fixed plate for photos) so the whole flow runs without a key in dev, tests and CI. |
| 2 | Versioned prompts in the repo | `app/ai/prompts/analyze_meal_photo.v1.md`, `parse_food_text.v1.md`; `prompt_store.CURRENT` pins the version. Every `ai_jobs` row records prompt id and version. |
| 3 | Strict JSON schema, validation, one retry | One schema for both flows (per-100 g macros + estimated grams + confidence). Replies are validated by `AiMealResult`: bounds, macros ≤ 105 g/100 g, and energy recomputed from macros when the model contradicts itself (`kcal_adjusted`). An invalid reply is retried **once** with the rejection reason; a transient failure is retried once; a permanent one is not. |
| 4 | Rate limits | Per user `AI_RATE_LIMIT_PER_MINUTE` (burst) and the soft daily budget `AI_DAILY_CALL_LIMIT` for the whole server → 429 `ai_daily_limit`. `GET /ai/usage` shows the caller's own calls and what is left of the shared budget. |
| 5 | Photo flow | Camera capture through `<input capture>` opened by a `<label>` tap (iOS needs a direct gesture), client downscale to 1280 px JPEG (q 0.82), `POST /api/v1/ai/analyze-meal` → `items[]` with `name_ru, name_en, estimated_grams, confidence, kcal, protein, fat, carbs, fiber`. |
| 6 | Text flow | `POST /api/v1/ai/parse-text` with the cheaper `AI_MODEL_TEXT`; voice = iOS keyboard dictation (hint in the sheet), no audio upload. |
| 7 | Matching against the local DB | Each suggestion goes through the stage-2 ranked search (ru name, then en name) plus the user's own/favourite foods sharing a word stem. A candidate must share ≥ half of the AI name's words and have plausible energy. Ranking: **own food → all words present → verified → overlap → closest kcal → search score**. No match → the AI macros are kept, flagged `source=ai`, with **Сохранить как мой продукт**. |
| 8 | Review screen, never auto-save | Grams slider + number field (shown as «≈ 200 г — оценка»), per-item confidence badge, swap product (alternatives + search + «Оставить оценку ИИ»), remove, add from search, meal type, live totals. Nothing is written until **Записать · N ккал**. The server accepts `ai_job_id` only for the caller's own successful analysis, once. |
| 9 | Photo privacy | The original photo is never written to disk. With **Сохранять миниатюру фото (400 px)** on, a 400 px thumbnail waits in `MEDIA_DIR/pending/` and moves to the meal on save (deleted otherwise, and pruned nightly). Deleting the meal deletes the picture. |
| 10 | Barcode via `@zxing/browser` (no BarcodeDetector) | Lazy-loaded scanner sheet (EAN-13/8, UPC-A/E) → the stage-2b lookup (local → live OFF) → log sheet; unknown code → «Создать продукт» with the code prefilled; typed digits as fallback when the camera is unavailable. |
| 11 | AI needs network, clear offline message | Offline, the text flow answers «ИИ-функции доступны только онлайн» at once (no hung spinner), and the photo tile says so **before** the camera opens. |

## 2. New & changed files

```
.env.example                             + AI_RATE_LIMIT_PER_MINUTE, AI_MAX_*, AI_PENDING_THUMB_TTL_HOURS,
                                           AI_JOB_PAYLOAD_RETENTION_DAYS, MAX_REQUEST_BYTES, MEDIA_DIR
docker-compose.yml · docker/api.Dockerfile   + api_media volume, /data/media owned by appuser
docker/Caddyfile                         + request_body max_size 6MB on /api/*
Makefile · scripts/dev-api.sh · .gitignore   MEDIA_DIR defaults to api/var/media for local dev
api/app/ai/
├── providers.py                         ★ OpenAI/OpenRouter, Anthropic, Google adapters, error classes
├── fake.py                              ★ deterministic provider (text parser, photo plate, test script queue)
├── schema.py                            ★ strict JSON schema + AiItem/AiMealResult validation
├── prompt_store.py                      ★ versioned prompt loader
├── prompts/*.v1.md                      ★ analyze_meal_photo, parse_food_text
└── runner.py                            ★ budget → audit row → provider → validate → one retry
api/app/core/body_limit.py               ★ 413 for oversized bodies before parsing/auth
api/app/core/{config,errors}.py · main.py   + AI/media/body-limit settings, 413 code, middleware
api/app/services/
├── ai_meal.py                           ★ photo/text analysis, local matching, attach-on-save, housekeeping
├── media.py                             ★ JPEG checks (no Pillow), thumbnail storage with path containment
├── nutrition.py                         + ai_job_id on meal create, thumbnail delete with the meal
└── hooks.py                             + nightly ai_housekeeping job
api/app/api/v1/ai.py                     ★ /ai/analyze-meal, /ai/parse-text, /ai/usage
api/app/api/v1/{nutrition,presenters,router}.py   + meal photo endpoint, AI presenters, router mount
api/app/schemas/{ai,food}.py             ★ AI response shapes · + AI provenance on meal items
api/tests/test_stage3.py                 ★ 35 tests
web/src/
├── features/ai/AiCapture.tsx            ★ one owner of photo/text/barcode flows (app shell)
├── features/ai/AiReviewSheet.tsx        ★ review screen
├── features/ai/{useAi.ts,MealPhoto.tsx} ★ mutations (fail fast offline), meal thumbnail
├── features/food/BarcodeScannerSheet.tsx ★ zxing scanner (lazy chunk)
├── features/food/{CustomFoodSheet,LogSheet,FoodPage}.tsx   + barcode prefill, barcode source, capture row
├── features/today/TodayPage.tsx         + photo/text empty state, thumbnails in meal rows
├── components/layout/{AppShell,QuickAddSheet}.tsx   + capture provider, live photo/text/barcode tiles
├── lib/{image,errors}.ts                ★ client downscale · client error codes via i18n
├── api/{client,types}.ts · api/generated/schema.d.ts   + FormData, AI endpoints, regenerated types
├── styles/index.css                     + 5-option segmented control fits 390 px
└── i18n/locales/{ru,en}.json            +58 keys each (397 per locale)
scripts/make-barcode-y4m.py              ★ EAN-13 → Y4M video for Chromium's fake camera
scripts/ui-check.mjs                     + 24 stage-3 browser checks (91 total)
scripts/smoke.sh                         + AI parse without auto-save, 413 upload guard (19 total)
docs/screenshots/16–19-*.png             ★ review (text), review (photo), Today with thumbnail, barcode → log
```

## 3. Commands

```bash
# development without a key: deterministic fake provider (or AI_PROVIDER=fake in .env)
AI_PROVIDER=fake make api

# a real provider (any of openai | anthropic | google | openrouter), in .env:
#   AI_PROVIDER=openai  AI_API_KEY=sk-…  AI_MODEL_VISION=gpt-4o  AI_MODEL_TEXT=gpt-4o-mini
docker compose up -d --build api     # production: thumbnails go to the api_media volume

# quality gates
cd api && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
python3 scripts/make-barcode-y4m.py 460700123456 /tmp/barcode.y4m     # prints 4607001234562
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>
SMOKE_AI=1 API_BASE=… ./scripts/smoke.sh <CODE>    # spends one real AI call when a key is set
```

## 4. What to test (walkthrough)

1. **+** → **Текст** → «200 г гречки, 2 яйца и куриная грудка 150 g» → **Распознать**.
   - «Проверьте запись» lists three items with «из базы» and a confidence badge;
     grams read «≈ 200 г — оценка».
   - Type 150 for buckwheat → 165 ккал. **Заменить продукт** on the egg →
     «Яйцо варёное». Remove the third item. **Добавить продукт** → search → pick.
   - Close the sheet: nothing was saved. Repeat and press **Записать · N ккал** →
     toast «Записано: …», one meal on Today.
2. «соус тартар 30 г» → the item says «оценка ИИ» (no local match) →
   **Сохранить как мой продукт** turns it into your food.
3. **Настройки → Сохранять миниатюру фото (400 px)** on → **+** → **Фото** → take a
   picture → «Проверьте фото-запись» with a note about oil/sauce → **Записать**.
   Today shows the thumbnail; deleting the meal deletes it. With the setting off,
   no picture is kept anywhere.
4. **+** → **Штрих-код** → point at a pack: known code → log sheet; unknown →
   «Штрихкод … не найден» → **Создать продукт** with the code filled in.
   Without a camera, type the digits under «Или введите цифры».
5. Airplane mode → **Текст** → «ИИ-функции доступны только онлайн»; the **Фото**
   tile shows the same message instead of opening the camera.
6. `AI_DAILY_CALL_LIMIT=1` → the second analysis answers with the daily-limit
   message; `GET /api/v1/ai/usage` shows your calls and the remaining budget.

## 5. Verification evidence (this build)

```
api      157 passed (122 before + 35 stage 3) · ruff check clean · ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 24 precache entries (1192.7 KiB);
         zxing in its own lazy chunk (415 kB, 109 kB gzip), loaded only when the scanner opens
i18n     397 keys per locale, complete & in sync
browser  91/91 checks green (Chromium, 390×844, ru-RU, fake camera playing an EAN-13 video), incl. stage 3:
         text → 3 suggestions, nothing saved before confirm · grams edit recomputes kcal ·
         swap · remove (48×48 target) · hand-added item: slider range stays put ·
         explicit save → one meal with confidence + edited flags · photo → downscaled JPEG →
         3 suggestions, note in the UI language · kept thumbnail on Today · offline message
         (text and photo tile, no file chooser) · real zxing decode of the camera feed → log sheet
smoke    19/19 green (stages 1–3), incl. AI parse with no auto-save and 413 for a 4.2 MB upload
```

**Real AI providers were not called from this sandbox** (no API key, no network
to the providers). Each adapter is tested against its documented request and
response format through `httpx.MockTransport`, including one end-to-end request
through the OpenAI adapter. First run with a real key: `SMOKE_AI=1 ./scripts/smoke.sh`.

Reviews run on this branch:

- **Backend Design `audit`** (transactions around network calls, failure
  paths): the AI runner commits its audit row before the provider call, so no
  pooled connection waits inside a transaction during a model call, and commits
  the job as soon as it succeeds; provider errors are split into transient
  (retried once) and permanent.
- **`code-review`** against the local branch diff (no GitHub remote, so no PR):
  a bug scan and a CLAUDE.md/spec compliance pass. Fixed:
  - an unauthenticated client could make the API spool an unbounded upload
    (FastAPI parses multipart before auth) → `MAX_REQUEST_BYTES` guard + Caddy limit;
  - usage counters opened a transaction that stayed open across the retry call;
  - a hand-added item's slider range grew while dragging;
  - a lost response + second tap could save the same analysis twice → one client
    id per analysis, and the server refuses a job already saved to another meal
    (409 `ai_job_already_saved`, row lock);
  - the scanner could leave the camera on when closed during the permission prompt;
  - two client error codes had no translation;
  - response shapes were built in the service layer → moved to `presenters.py`;
  - `/ai/usage` reported every user's calls as yours;
  - own foods only won ties → now ranked first;
  - an oversized or unwritable thumbnail failed the whole analysis → dropped, logged;
  - 40 px / 36 px buttons on the review screen → 48 px;
  - offline, the camera opened before the warning.
- **`frontend-design`** used as guidance inside the existing design system: no
  new tokens, the review screen reuses cards, badges, segmented control and the
  sheet; numbers are tabular; every action stays within two taps of **+**.
- **Playwright** screenshots: `docs/screenshots/16-ai-review.png`,
  `17-ai-photo-review.png`, `18-today-photo-meal.png`, `19-barcode-log-sheet.png`.

## 6. Decisions worth knowing

- **The model returns per-100 g values plus an estimated weight**, not totals.
  Per-100 g numbers are comparable with the library (plausibility check, swap)
  and stay right when the user changes the grams.
- **Local first, AI second.** The search's own ranking is not trusted blindly:
  "Куриная грудка запечённая" must share words and energy with a candidate, so it
  resolves to «Куриная грудка приготовленная» (165 kcal), not to «Куриная голень
  запечённая» that the search ranks higher.
- **One analysis → one meal.** The client sends the same meal id for every save
  attempt of an analysis; the server also refuses to attach a job twice.
- **The daily cap is a soft, server-wide budget** (one server, one API key):
  `AI_DAILY_CALL_LIMIT` counts every user's calls, retries included; `/ai/usage`
  shows your own count and the shared remainder. Per-user fairness is the
  per-minute limit.
- **Rate limits are in-process**, like the stage-1 auth limiter: correct for the
  one-container deployment; a second API worker would need a shared store.
- **Photos:** the original lives only in request memory. The thumbnail is
  re-checked server-side (JPEG markers, ≤ 480 px, ≤ 250 KB); anything else is
  dropped, never an error. Thumbnails are in the `api_media` volume, **not** in
  the `pg_dump` backup; meals and their numbers are.
- **The thumbnail file is moved before the meal transaction commits.** If that
  commit fails, the picture is lost but the meal is not created either; the
  user simply saves again without it. Accepted instead of a two-phase file move.
- **Audit trail:** `ai_jobs` keeps provider, model, prompt version, tokens,
  latency, raw replies and the validated result; payloads are pruned after
  `AI_JOB_PAYLOAD_RETENTION_DAYS`, counters stay. `cost_usd` stays 0 (no price
  table yet).
- **Request bodies over 4 MB are refused with 413** before parsing; the photo
  cap is `AI_MAX_IMAGE_BYTES` (3 MB, the app sends ~150–400 KB).

## 7. Not in stage 3 (by design)

- **Saving a reviewed meal offline.** The analysis itself needs the network; the
  save uses the stage-2 meal mutation, which waits for the connection. Queueing
  it in the offline queue is part of stage 9 (offline sync polish).
- **Voice recording:** dictation through the iOS keyboard only, as specified.
- **Cost accounting in money:** tokens are counted; prices per model are not.
- **Found, not fixed (earlier-stage code):** the shared `Sheet` close button has
  the hard-coded aria-label "Close", and `LogSheet` creates a new client id per
  save tap. Both are left for the stage 9 accessibility/offline pass rather than
  rewriting stage-1/2 files here.
