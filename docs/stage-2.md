# Stage 2 — Nutrition core · delivery report

> Everything below exists, runs and is verified in this repository. Stage 1
> remains untouched in spirit: no file was rewritten, only extended — the
> schema already held every table, stage 2 filled them with life.

## 1. Plan that was executed

| # | Promise | Where it landed |
| --- | --- | --- |
| 1 | Food DB importers (RU + EN), idempotent | `api/app/importers/` — `base.run_import` pipeline + `seed` (bundled 84-food RU/EN library), `off` (Open Food Facts JSONL), `usda` (CSV); CLI `import-foods --source … --file … --limit …`, `make seed-foods` |
| 2 | Ranked search < 150 ms, personal world first | `GET /v1/foods/search` — one SQL: ru/en FTS + `pg_trgm` similarity/word-similarity + boosts (own ×2.0, favorite ×0.5, verified ×0.15, popularity ×0.25); `took_ms` returned with every response |
| 3 | Barcode lookup | `GET /v1/foods/barcode/{code}` (camera scanning stays in stage 3) |
| 4 | Manual logging, idempotent | `POST/PATCH/DELETE /v1/nutrition/meals`, client-UUID replay, macro snapshots at write time, soft delete |
| 5 | Favorites / recents / copy-previous-meal | `/v1/foods/favorites`, `/v1/foods/recents` (auto-touched on logging), `POST /v1/nutrition/meals/copy` |
| 6 | Recipes & portions | `/v1/recipes` CRUD; denormalised whole-dish + per-100 g + per-serving macros, ingredient snapshots |
| 7 | Custom foods ("my food") | `POST /v1/foods`, `PATCH /v1/foods/{id}` (library rows read-only except `verified`), archive on delete |
| 8 | Daily nutrition dashboard | `GET /v1/nutrition/day?date=` aggregated in the **user's timezone**; Today page ring/macros/water/meals wired to it |
| 9 | Water logging | `POST/DELETE /v1/nutrition/water`, +250 ml one-tap on Today |
| 10 | UI: search → log in two taps | `web/src/features/food/` — FoodPage hub, LogSheet, RecipeSheet, CustomFoodSheet; deep links `/food?action=search|water|recipe|custom` |
| 11 | i18n ru/en for every new string | +34 keys per locale incl. a new `meal.*` section; `scripts/check-i18n.mjs` in CI |
| 12 | Tests | `api/tests/test_nutrition.py` — 17 tests: importer idempotency, ranking, typo/morphology, ownership, day boundaries across UTC+7, snapshot math, recipes, copy, water bounds |

## 2. New & changed files

```
api/
├── app/importers/            ★ base.py (upsert pipeline), seed.py, off.py, usda.py
│   └── data/foods_seed_ru.json ★ 84 everyday foods, RU+EN, per-100 g, servings
├── app/services/food_search.py ★ ranked search, favorites, recents, barcodes
├── app/services/nutrition.py   ★ meal logs, day aggregation, water, copy
├── app/services/recipes.py     ★ recipe CRUD + macro denormalisation
├── app/schemas/food.py         ★ all nutrition request/response models
├── app/api/v1/foods.py         ★ /foods router (search, barcode, favs, recents, CRUD)
├── app/api/v1/nutrition.py     ★ /nutrition router (day, meals, copy, water)
├── app/api/v1/recipes.py       ★ /recipes router
├── app/api/v1/presenters.py    + nutrition presenters (single place for shapes)
├── app/api/v1/router.py        + three routers mounted
├── app/cli.py                  + import-foods command
└── tests/test_nutrition.py     ★ 17 tests
web/src/
├── api/types.ts  api/client.ts  lib/queryClient.ts   + nutrition types/endpoints/keys
├── features/food/useNutrition.ts ★ queries & mutations, invalidation rules
├── features/food/FoodPage.tsx    ★ hub: search, recents, favorites, meals, recipes
├── features/food/LogSheet.tsx    ★ grams + meal type + live macro preview
├── features/food/RecipeSheet.tsx ★ ingredient picker over the same search
├── features/food/CustomFoodSheet.tsx ★ "my food"
├── features/today/TodayPage.tsx  ★ real dashboard (was stage-1 zeros)
├── features/shared/Placeholders.tsx − FoodPage (now real)
└── i18n/locales/{ru,en}.json     +34 keys each, new meal.* section
docs/screenshots/11-log-sheet.png, 12-today-with-meal.png ★
Makefile + seed-foods target
```

## 3. Commands

```bash
# library
cd api && .venv/bin/python -m app.cli import-foods --source seed   # or: make seed-foods
.venv/bin/python -m app.cli import-foods --source off  --file products.jsonl
.venv/bin/python -m app.cli import-foods --source usda --file foundation.csv --limit 5000

# quality gates
cd api && .venv/bin/pytest -q                 # 100 tests
cd web && npm run typecheck && npm run build  # tsc + vite + PWA
node scripts/check-i18n.mjs                   # 309 keys/locale in sync
PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs   # 55 browser checks
```

## 4. What to test (walkthrough)

1. `make seed-foods`, re-run → `created=0 updated=0 skipped=84` (idempotent).
2. Pair a device, open **Питание**: type `гречка` → instant ranked results with
   `took_ms` under the list; `chicken brest` (typo) and `огурцы` (morphology)
   both find their row.
3. Tap a result → sheet with the default serving pre-filled, live macro
   preview → **Записать в дневник** → toast; **Today** ring and macro rings move.
4. Star a result → it appears in the favorites row and outranks library rows.
5. Log the same food again from the recents chip (one tap).
6. **Новый рецепт**: add two ingredients through the inline search, set
   servings → per-serving kcal shown before save; log the recipe by grams.
7. **Мой продукт**: create a food → it is searchable immediately and ranks
   above the library.
8. Today: **+250 мл** grows the water bar; deleting a meal row shrinks the ring.
9. `curl -H "Authorization: Bearer …" …/v1/nutrition/day?date=2026-10-02` —
   a meal logged at 2026-10-01T18:00Z belongs to **Oct 2** for an Asia/Bangkok
   user (tested).

## 5. Verification evidence (this build)

```
api      100 passed in 7.64s (83 stage-1 + 17 stage-2)
         ruff check clean · ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 23 precache entries
i18n     309 keys per locale, complete & in sync (CI gate)
browser  55/55 checks green in Chromium, incl. the nutrition flow:
         search → log sheet → toast → Today ring 165 kcal → +250 ml water
         → recipe deep link; zero console errors, zero 5xx
import   seed: seen=84 created=84 → re-run seen=84 skipped=84
search   'гречка' fts · 'chicken brest' trgm · 'огурцы' morphology · took_ms ≈ 5-15
```

Screenshots: `docs/screenshots/11-log-sheet.png` (grams + preview),
`12-today-with-meal.png` (live dashboard).

## 6. Decisions worth knowing

- **Ranking weights** (`food_search.py`): exact ×1.0, FTS ×1.2, trgm ×0.9, then
  own ×2.0, favorite ×0.5, verified ×0.15, popularity ×0.25. The user's own
  world always outranks anonymous library rows; a favourite outranks other
  library rows but not the user's customs.
- **Snapshots everywhere**: meal items and recipe items store their macros at
  write time, so editing a food never rewrites history.
- **Boolean → float in SQL** goes through `CASE WHEN … THEN 1.0 ELSE 0.0 END`;
  Postgres has no direct cast (caught by the first live run, now tested).
- **Async ORM discipline**: collections are loaded with an explicit
  `refresh(…, ["items"])` before mutation — lazy loads raise `MissingGreenlet`
  in async sessions (caught by tests).
- **The seed library is `verified=True`**; OFF/USDA imports are not, so the
  "verified" boost meaningfully separates curated from scraped rows.
- Water, steps and burned calories stay **outside** the calorie budget math;
  the dashboard merely displays them (steps arrive in stage 5).

## 7. Not in stage 2 (by design)

Photo/text AI logging and the review flow, zxing barcode **scanning** (stage 3);
workouts (4); steps/Shortcuts ingest (5); push (7); XP and charts (8).
The quick-add sheet still badges those actions with their stage number.
