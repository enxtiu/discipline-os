# Roadmap

Ten stages, built in order. Each one is complete and runnable on its own — no
placeholders that pretend to work, no half-finished feature behind a flag.

| # | Stage | What it adds | Status |
| --- | --- | --- | --- |
| 1 | **Foundation** | Repo, compose stack (postgres + api + caddy), full DB schema, device-token auth + pairing, scoped API tokens, settings + target engine, design system, app shell with bottom navigation, i18n ru/en, PWA basics, deploy/backup/health automation, README | ✅ done |
| 2 | **Nutrition core** | Food database importers (RU + EN), ranked search (FTS + pg_trgm + custom/verified/favorites/recents first, <150 ms), barcode lookup, manual logging, recipes/portions, favorites, copy-previous-meal, daily nutrition dashboard | ✅ done |
| 2b | **Nutrition backfill** | Curated seed 84 → 320 (new rows unverified + one-tap confirm), USDA Foundation/SR Legacy with Russian names, OFF quality bar + live barcode fallback with cache, recipe yield/portions + pinned recipes on Today, search < 150 ms proven at 100k rows | ✅ done |
| 3 | **AI logging** | `POST /api/v1/ai/analyze-meal` (photo → 1280 px JPEG downscale → strict JSON `items[]`), text logging (ru/en), review screen (editable grams, swap/remove/add), **never auto-save**, barcode scanning with `@zxing/browser`, provider abstraction, versioned prompts, soft daily call limit | ✅ done |
| 4 | **Training** | Exercise library UI (seeded in stage 1), workout templates, weekly plan, live workout logging, personal records, **burned calories in a separate card/chart — never added to the food budget** | ✅ done |
| 5 | **Health ingest** | `POST /api/v1/ingest/health` (scoped token, idempotent upsert by date), the iOS Shortcut recipe, steps card + manual fallback, manual weight and sleep entries | ✅ done |
| 6 | **Planner** | PARA structure (Projects/Areas/Resources/Archive/Inbox), tasks P1–P4 with tags and subtasks, due date/time, scheduled time blocks, estimates, RRULE recurrence, reminders, markdown notes; views Today/Inbox/Upcoming/Calendar (day/week/month with drag), Project/Area/Tag/Search; quick-add NL parsing; time tracking with a timer that survives reloads and device switches; daily journal | next |
| 7 | **Notifications** | Web push (VAPID, `pywebpush`), the in-process notification scheduler, quiet hours, per-category prefs, the iOS "add to home screen first" explanation in onboarding | planned |
| 8 | **Gamification & insight** | XP rules (never for intake below the safe floor), streaks, rings, calorie score peaking **in** the target zone, levels, badges, charts and dashboards (Recharts), data export, backup download | planned |
| 9 | **Hardening** | Offline sync polish on top of the stage-1 queue, conflict surfacing, Obsidian markdown export, Notion sync hooks, performance pass, accessibility pass | planned |
| 10 | **Redesign** | A new visual identity chosen together with the owner: brand book, colour palette, typography, iconography, layout and navigation reworked for comfort. Every screen of stages 1–9 is built on the CSS-variable tokens and the shared `components/ui` kit, so the redesign re-skins them in one place instead of rewriting them | planned |

## Architectural rules that hold across all stages

- **Multi-user ready.** Every table has `user_id`; every query is scoped by the
  authenticated principal. Single user today is a UI decision, not a schema one.
- **The client never computes targets, scores or day boundaries.** Days are
  computed in the user's timezone on the server; timestamps are UTC in the
  database.
- **Writes are idempotent.** The client generates the UUID and retries safely;
  the offline queue replays them in order.
- **Every string goes through i18n** (Russian default, English switchable).
- **Metric units** everywhere.
- **No reward, recommendation or streak ever depends on eating below the safe
  floor** (1500 kcal men / 1200 kcal women, editable only behind an explicit
  warning).
- **Burned calories are never food budget.**
- **AI is a fallback, not the source of truth**: local food matching first, AI
  only when nothing matches, always flagged `source=ai`, always reviewed by a
  human before saving.
- **Later stages plug in.** The stage-1 schema already contains every table the
  later stages need, so a new stage adds routers/services/UI — not migrations
  that reshape existing data.
