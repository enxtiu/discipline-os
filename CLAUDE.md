# DisciplineOS — working rules for every session

Personal discipline tracker (nutrition, training, steps, PARA planner, time
tracking, gamification). Mobile-first PWA for iPhone Safari, one-hand use,
single user now, multi-user-ready. Read `docs/roadmap.md` for stage status and
the latest `docs/stage-*.md` for what exists before touching code.

## Architectural rules (binding, from docs/roadmap.md)

- **Multi-user ready.** Every table has `user_id`; every query is scoped by the
  authenticated principal (`app/api/deps.py`). Library rows (`user_id IS NULL`)
  are read-only to users except the `verified` flag.
- **The client never computes targets, scores or day boundaries.** Timestamps
  are UTC in the DB; days are computed in the user's timezone on the server
  (`app/core/tz.py`).
- **Writes are idempotent.** The client generates the UUID (`id` in the body /
  `X-Client-UUID`); replays return the same row. The Dexie offline queue
  replays writes in order.
- **Every UI string goes through i18n** (`web/src/i18n/locales/{ru,en}.json`,
  Russian default). `node scripts/check-i18n.mjs` must stay green.
- **Metric units** everywhere.
- **No reward, recommendation or streak ever depends on eating below the safe
  floor** (1500 kcal men / 1200 kcal women; editable only behind an explicit
  warning).
- **Burned calories are never food budget.** Show them in a separate card/chart.
- **AI is a fallback, not the source of truth**: local food matching first, AI
  only when nothing matches, always flagged `source=ai`, always reviewed by a
  human, never auto-saved.
- **Later stages plug in.** The stage-1 schema (49 tables, migration 0001)
  holds every entity. Add a migration only for genuinely new columns/indexes;
  never reshape existing data.

## Working conventions

- Extend, never rewrite earlier-stage files beyond the minimal change.
- Keep the pinned stack (`api/requirements*.txt`, `web/package.json`); any
  upgrade or swap must be justified in the stage report.
- Scheduler jobs register through `app/services/hooks.py` only.
- Routers mount in `app/api/v1/router.py`; response shapes live in
  `app/api/v1/presenters.py`; endpoint helpers in `web/src/api/client.ts`.
- UI reuses `web/src/components/ui` and the CSS-variable design system in
  `web/src/styles/index.css`: dark default, tabular numerals, safe-area insets,
  48 px targets, every frequent action within two taps. Class names must be
  literal strings (or safelisted in `tailwind.config.js`) or Tailwind purges them.
- One branch per stage (`stage-N-short-name`), small conventional commits,
  a PR (or a ready-to-apply commit sequence) with the stage summary.
- Every stage ends with `docs/stage-N.md` (plan · files · commands · walkthrough
  · evidence · decisions · not-in-this-stage) and a roadmap status update.

## Quality gates (all green before "done" — never claim a gate you did not run)

```bash
cd api && .venv/bin/pytest -q                       # real PostgreSQL (TEST_DATABASE_URL)
cd api && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build        # tsc + vite + PWA
node scripts/check-i18n.mjs
PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs    # Playwright, 390x844
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>
```

PostgreSQL must be initialised with a UTF-8 locale (`en_US.utf8`) or `pg_trgm`
returns nothing for Cyrillic.
