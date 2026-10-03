# Stage 2b — Nutrition backfill · delivery report

> This stage closes the gaps between the original spec and what stage 2 shipped.
> Each item was checked against the code first, and only the missing parts were
> built. Nothing from stage 2 was rewritten.
>
> - No migration was added: the schema already had every column, including
>   `recipes.cooked_weight_g`, `portion_size_g` and `is_pinned`.
> - The 84 original seed rows are byte-identical.

## 1. Plan that was executed

| # | Spec item | Found in the repo | Delivered |
| --- | --- | --- | --- |
| 1 | Curated seed ≈300 RU foods, new rows unverified, one-tap confirm | 84 rows, all `verified=true`. The importer forced `verified` per source. No confirm UI. `PATCH /foods/{id}` let any field through on library rows whenever `verified` was in the body. | **320 rows**: +236 home-cooked foods and dishes with Russian aliases, `"verified": false`. The flag is set per row, only on insert, so a re-import never resets a confirmation. Search rows and the log sheet have a one-tap **Подтвердить** (owner only). Library rows accept only `verified`. |
| 2 | USDA Foundation + SR Legacy, RU name mapping, tests | Flat CSV only (`id,name,kcal,…`). No Russian names. | Reads the FDC JSON download and the FDC CSV bundle, with a `--dataset` filter; the flat CSV still works. Energy falls back to Atwater or kJ, and broken rows are dropped. `data/usda_ru.json` has 212 rules plus per-`fdcId` overrides. 8 tests. |
| 3 | OFF: complete nutriments + ru/en names; barcode local → live OFF → cache | The importer took any row with a name in any language and defaulted missing macros to 0. The barcode endpoint was local only. | One quality bar (`product_to_raw`) shared by the importer and the live lookup. `GET /foods/barcode/{code}` tries the local library first (UPC-A/EAN-13 variants, the user's own food wins), then calls OFF live. A usable product is cached as an unverified library row; a miss is remembered for 24 h; an outage is never cached. Live calls are rate-limited per user, and the response reports `lookup`. |
| 4 | Recipes: cooked-weight yield, per-100 g / per-portion end to end, pinned on Today | The API had the columns. The UI had no yield, no portion and no pin. Nothing was pinned on Today. A yield that only mirrored the raw weight went stale when ingredients changed. Create was not idempotent. | Recipe sheet: cooked weight, and a portion given as a count or as grams, with a live preview using the same math as the server. Pin toggle in the sheet and on recipe cards. A **Закреплённые рецепты** row on Today: tap → log sheet at 1 portion (½ / 1 / 1½ / 2 chips) → save. The stale-yield bug is fixed. Create is idempotent by client id. |
| 5 | Search < 150 ms with the enlarged DB + test/benchmark | **Not met**: 600–780 ms at 100k rows. The candidate filter mixed index-less predicates, so every search scored the whole table. | Candidates are a UNION of capped single-index branches. Own foods and favorites come from uncapped branches. Only that bounded set is scored. Result: **p50 42 / p95 66 / max 77 ms at 100k rows**. A pytest gate checks the 150 ms budget, and `python -m app.cli bench-search` measures it on any database. |

**Baseline repairs** found while establishing the baseline (separate commits):

- `CORS_ORIGINS=a,b`, the format `.env.example` documents, crashed the API at start.
- Tailwind purged every `<Button>` variant class (`btn-${variant}` is built at runtime), so all buttons rendered unstyled. The committed stage-2 screenshots show this too.
- One browser check depended on letter case.

## 2. New & changed files

```
CLAUDE.md                                ★ binding rules for every future session
.env.example                             + OFF_* (live barcode fallback)
api/app/core/config.py                   + off_* settings · CsvList NoDecode fix
api/app/importers/
├── data/foods_seed_ru.json              + 236 rows (84 original rows untouched)
├── data/usda_ru.json                    ★ 212 rules: FDC description → name_ru + aliases
├── base.py                              + RawFood.verified (applied on insert only)
├── seed.py                              + per-row verified flag
├── usda.py                              ↻ FDC JSON / CSV bundle / flat CSV, RuNameMap
└── off.py                               ↻ product_to_raw quality bar, barcode helpers
api/app/services/
├── off_lookup.py                        ★ local → miss cache → live OFF → cache row
├── search_bench.py                      ★ synthetic library + latency benchmark, VACUUM helper
├── food_search.py                       ↻ capped-UNION candidates, name/generic ranking signals
└── recipes.py                           + portion_size_g, idempotent create, yield fix
api/app/api/v1/foods.py                  + barcode outcome · library rows: verified only, owner only
api/app/api/v1/presenters.py             + per-portion macros from portion_size_g
api/app/schemas/food.py                  + FoodBarcodeOut.lookup · RecipeIn.portion_size_g / is_pinned
api/app/cli.py                           + import-foods --dataset/--ru-map · VACUUM after load · bench-search
api/tests/test_stage2b.py                ★ 22 tests · conftest: OFF_LIVE_LOOKUP=false
web/src/
├── api/{types,client}.ts                + FoodBarcode, RecipePatch, verifyFood, updateRecipe
├── api/generated/schema.d.ts            ↻ regenerated from the live OpenAPI schema
├── components/ui/icons.tsx              + IconPin
├── features/food/VerifyFood.tsx         ★ VerifyChip (search row) + VerifyNote (log sheet)
├── features/food/FoodPage.tsx           + confirm chip, pin toggle on recipe cards
├── features/food/LogSheet.tsx           + recipes open at 1 portion, portion chips, verify note
├── features/food/RecipeSheet.tsx        ↻ yield, portion count/grams, preview, pin
├── features/food/useNutrition.ts        + useVerifyFood, useUpdateRecipe
├── features/today/TodayPage.tsx         + pinned recipes row · water/steps totals wrap
└── i18n/locales/{ru,en}.json            +30 keys each (339 per locale)
web/tailwind.config.js                   + safelist for Button variants
scripts/ui-check.mjs                     + 12 stage-2b browser checks (67 total)
scripts/smoke.sh                         + 3 API checks (17 total)
docs/screenshots/13-verify-chip.png, 14-recipe-sheet.png, 15-today-pinned.png ★
```

## 3. Commands

```bash
# library
make seed-foods                                                   # 320 rows; re-run is a no-op
cd api
.venv/bin/python -m app.cli import-foods --source usda --file FoodData_Central_foundation_food_json_2024-10-31.json
.venv/bin/python -m app.cli import-foods --source usda --file FoodData_Central_csv_2024-10-31/ --dataset sr_legacy
.venv/bin/python -m app.cli import-foods --source off --file off-ru.jsonl
.venv/bin/python -m app.cli bench-search                          # p50/p95/max on this DB, exit 1 if ≥150 ms
.venv/bin/python -m app.cli bench-search --synthetic 100000 --cleanup   # stress test, then remove the rows

# quality gates
cd api && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>
```

For **SR Legacy, use the CSV bundle**. The bundle is streamed, while a JSON
download is loaded whole, and the SR Legacy JSON is large for a 1 GB VPS.
Foundation JSON is small and fine either way.

## 4. What to test (walkthrough)

1. `make seed-foods` → `created=236 skipped=84` on a stage-2 database. A re-run gives `created=0`.
2. **Питание** → type `борщ`.
   - «Борщ с мясом» is on top, with an amber **Подтвердить** chip.
   - Tap the chip → toast «Данные подтверждены», and the chip disappears.
   - `took_ms` is under the list.
3. Type `сырники` → open the row. The sheet explains that the numbers are unchecked and offers the same confirm.
4. **Новый рецепт**:
   - Add «Гречка (ядрица, сухая)» 100 g, set cooked weight 300, switch to **Вес порции**, enter 150.
   - The preview shows «172 ккал в порции 150 г · 114 ккал на 100 г готового».
   - Turn on **Закрепить на «Сегодня»** and save.
5. **Сегодня** → the **Закреплённые рецепты** row shows the recipe with «172 ккал · 150 г».
   - Tap it → the sheet opens at «1 порц. · 150 г».
   - **Записать в дневник** → the day totals move.
6. Recipe card pin icon → unpin. The row disappears from Today.
7. Barcode:
   - `curl -H "Authorization: Bearer …" …/api/v1/foods/barcode/4607001234567` returns `lookup: local | off | miss | unavailable`.
   - With network access, an unknown code returns `off` once and `local` afterwards.
   - `?live=false` keeps the lookup local.
8. Owner-only confirm: a non-owner's `PATCH /foods/{id} {"verified":true}` returns 403 `verify_owner_only`. `{"verified":true,"kcal_per_100g":1}` returns 403 `global_food_readonly` for everyone.

## 5. Verification evidence (this build)

```
api      122 passed (100 baseline + 22 stage-2b) · ruff check clean · ruff format clean
         search bench (pytest, 50 320 rows): p50 27 · p95 45 · max 51 ms
         bench-search CLI (100 320 rows):    p50 42 · p95 66 · max 77 ms  (before: 617 / 755 / 782 ms)
web      tsc -b --noEmit clean · vite build + PWA 23 precache entries
i18n     339 keys per locale, complete & in sync
browser  67/67 checks green (Chromium, 390×844, ru-RU), incl. stage 2b:
         'борщ' 17 ms → confirm → toast → chip gone · unverified note in the sheet ·
         recipe preview 172 kcal / 150 g, 114 kcal / 100 g cooked · pinned on Today ·
         1 portion logged · barcode outcome 'invalid'; zero console errors, zero 5xx
smoke    17/17 green (14 stage-1 + search budget, barcode outcome, recipe portion)
seed     stage-2 DB: seen=320 created=236 skipped=84 → re-run created=0
```

Reviews run on this branch:

- **Backend Design `audit`.** Fixed: the live OFF call no longer holds a DB transaction open; barcode lookup is deterministic (the user's own food wins); a concurrent recipe replay no longer returns 500.
- **`code-review`.** Run against the local branch diff, since no GitHub PR exists. Three reviewers: bugs, CLAUDE.md compliance, comment/behaviour. Fixed:
  - own foods and favorites were capped in search;
  - an unscoped replay query;
  - a portion so small it overflowed NUMERIC;
  - the "portion weight" mode could save without a weight;
  - two stale doc comments.
- **`frontend-design`.** Used as guidance within the existing design system: no new tokens, plain sentence-case copy, the Confirm action named the same in the chip and the toast.

## 6. Decisions worth knowing

- **Search pipeline.** Each matching strategy is its own branch. The library
  branches are capped at `CANDIDATE_CAP = 120`, each ordered by its own measure
  with three tie-breakers: name hit, verified, popularity. The user's own foods
  and favorites are never capped. The full score runs on at most about 900 rows.
- **Ranking signals added.** These were needed once hundreds of rows share an
  alias or a brand word:
  - a name match outranks an alias-only match (`alias_sim × 0.6` vs `name_sim × 0.9`);
  - a name substring hit adds +0.3;
  - a row without a brand (curated seed, USDA, "my food") adds +0.2, so «огурцы»
    shows generic cucumbers before 40 branded packs. Typing the brand still
    ranks the branded pack first through full-text search.
- **`import-foods` runs `VACUUM (ANALYZE) foods` after a load.** Without it the
  first searches after a big import read every heap page.
- **Confirming is owner-only.** Library rows are shared by every user of the
  instance, so one confirmation affects everyone.
- **OFF live fallback.**
  - The miss cache is in-process; one API container makes that sufficient.
  - Only a usable product is cached, as `source=off`, `verified=false`.
  - Outages are never cached as misses.
  - The read transaction is committed before the network call.
- **Fiber is optional for OFF.** Most OFF products omit it, so "complete
  nutriments" means energy, protein, fat and carbs, and fiber defaults to 0.
  Energy must agree with the Atwater estimate within ±35 % (or 40 kcal), which
  drops kJ typed into the kcal field.
- **USDA Russian names are rule-based, not translated.** Rules match FDC facets
  ("Chicken, …, breast"), and the preparation state is appended in the rule's
  grammatical gender («Куриная грудка, запечённая»). Unmapped rows stay
  English-only and still searchable in English. Editing the map and re-importing
  updates the rows in place.
- **Recipe yield and portions.**
  - A yield equal to the raw weight is treated as "not set", so it keeps
    following the ingredients. A user who deliberately types the raw weight gets
    the same behaviour, which is harmless.
  - A portion given in grams is stored exactly. `servings` is derived from it,
    rounded to 0.01.
  - A later edit of the ingredients, yield or count re-derives the portion from
    that rounded `servings`, so a 130 g portion can become 129.9 g. A pin or a
    rename never touches it.

## 7. Not in stage 2b (by design)

- **Camera barcode scanning (zxing), photo and text AI logging:** stage 3. The
  barcode endpoint is ready for the scanner.
- **A full USDA/OFF dump in the repository:** too large. The dumps are imported
  by the commands above. The sandbox used for this build cannot reach FDC or OFF,
  so the live OFF fallback is verified with a mocked transport only.
- **Re-checking the 84 original rows:** they were left exactly as they were, as
  the stage plan asked.
