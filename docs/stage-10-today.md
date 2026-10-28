# Stage 10 — «Сегодня, ниже»: «План дня», «Повторить» and the daily insight

> The unified «Сегодня» of every theme (boards `Ffin-TodayMore` / `Dfin-TodayMore`, brief
> `design-finals.md`): under the cards that were already there, three new blocks. Same content
> in every theme; «Эволюция» draws them with the shared classes, «Наклейки» as stickers.
> Branch `stage-10-today`. One new endpoint (`GET /insights/today`), no migration.

## 1. What

| Block | What it shows | Where it comes from |
| --- | --- | --- |
| **План дня** | One timeline: a row per logged meal type (time of its first meal, kcal; done), today's planner tasks that have a time (done / open; the first open one still ahead is **«Далее»**, emphasised), then the main meals not logged yet («Ужин — пока не записан» + «Добавить ужин»). Line solid up to «Далее», dashed after it. | Reads Today already makes (`/nutrition/day`, `/planner/views/today`) — no request of its own. `features/today/dayPlan.ts` (pure) + `DayPlan.tsx`. «Добавить» → `/food?action=search&meal=<type>`: Питание opens its search and the log sheet starts on that meal (nothing is saved until confirmed). «Далее» moves on once a minute. |
| **Повторить** | The three most recent foods/recipes (`GET /foods/recents`, the list «Недавние»), one tap logs the same food with the grams it was last logged with. | `RepeatFoods.tsx`. The write is the log sheet's `useLogMeal`: client UUID, offline queue, shown in the day at once («ждёт отправки»), toast with «Отменить», `source=recent`, meal type from the shared default (`features/food/mealType.ts`, also used by the log sheet and the AI review). A tile ignores taps while its write is on its way (a double tap logs once). Names in the resolved UI language. |
| **Наблюдение** | At most one calm sentence, e.g. «Белка больше, чем в среднем за 4 недели». Nothing when there is none. | `GET /insights/today` (below); the client picks the sentence for `metric` + `day`, it computes nothing. |

The three live in one lazy chunk (`TodayMore`, 10.8 kB raw / 3.9 kB gzip), loaded as soon as
Today renders. They are extras: if that chunk cannot be fetched (offline, not cached yet) they are
left out, Today itself is unaffected, and the chunk is asked for again the next time Today opens.

«Наклейки» (`styles/skins/stickers.css`, new section «Сегодня, ниже»): the plan as an ink line through
sticker dots (mint + tick done, ink + white play «Далее», white open, dashed «пока не записан»), the
next task a lemon sticker lifted over the line with a white «Далее» label (2° tilt, badge only),
«Повторить» as three white stickers with a lemon «+» that sit down when pressed, the insight as a white
tile with a die-cut lavender badge (−3°). Night: light outlines and ink, the marks stay bright.
Rules are scoped to `:root[data-skin='stickers']`; the tilt switch and «Меньше анимации» switch it off as
everywhere else.

## 2. API: `GET /api/v1/insights/today`

Read-only, **device principal only** (an API token answers 403 `device_only`, no credentials 401).
Scoped to the authenticated user; the day is the user's today in their timezone (`GameContext.today`).
Three grouped range queries whatever the history (`app.services.day_metrics`). Router
`app/api/v1/insights.py`, shapes `app/schemas/insights.py`, presenter `today_insight_out`,
rules `app/services/insights.py`.

```jsonc
// 200
{
  "date": "2026-10-28",                 // the user's today
  "insight": {                          // null = nothing worth saying (the screen shows nothing)
    "metric": "protein",                // protein | fiber | water | steps
    "day": "today",                     // today (so far) | yesterday (a finished day)
    "date": "2026-10-28",               // the local day the observation is about
    "value": 112.4,                     // that day's amount: g (1 decimal) · ml · steps
    "average": 96.0,                    // mean of the 28 days before it, over the days with data
    "unit": "g",                        // g | ml | steps
    "window_days": 28,
    "days_with_data": 19                // >= 7
  }
}
```

Rules (binding — the insight is a kind word and follows the rules of every reward):

- **Only "more" of what the app encourages**: protein, fibre, water, steps. Never calories, fat or
  carbs; never "less than"; no observation is a shortfall.
- **Nutrition credit**: protein / fibre / water need that day's intake at or above the safe floor
  (`DayRules.credit`, the predicate of the protein/fibre/water XP). Steps need no credit.
- **Enough history**: average over the 28 days before the day, only days that have data (missing is not
  zero), at least 7 such days.
- **A real difference**: value ≥ 105 % of the average and still above it once both are rounded to the unit.
- **Fresh first**: today before yesterday; within a day protein → fibre → water → steps, the first that holds.
- Deleted meals never count; another user's data is never seen.

Client: `endpoints.todayInsight()`, key `queryKeys.insight` (under `nutrition`, so every food write
refreshes it), prefetched by `index.html` for `/today`; types in `api/types.ts` and the regenerated
`api/generated/schema.d.ts`. i18n: `today.insight_*` (RU + EN, 8 sentences + title).

## 3. Files

```
api/app/api/v1/insights.py · router.py · presenters.py   endpoint, mount, presenter
api/app/schemas/insights.py · app/services/insights.py   shapes, rules
api/tests/test_stage10_insights.py                       14 tests (rules, timezone, statements, isolation, auth)
web/src/features/today/{DayPlan,dayPlan,RepeatFoods,TodayInsight,TodayMore}.ts(x)   the blocks
web/src/features/today/TodayPage.tsx                      lazy `TodayMoreLoader` under the cards
web/src/features/food/{mealType.ts,FoodPage,LogSheet}.tsx · ai/AiReviewSheet.tsx     one meal-type default; ?action=search&meal=
web/src/styles/skins/stickers.css                         «Сегодня, ниже»
web/src/i18n/locales/{ru,en}.json · api/{client,types}.ts · lib/queryClient.ts · index.html
scripts/ui-checks/stage10-today.mjs · scripts/ui-check.mjs   browser checks (§4); CHROMIUM_NO_DEV_SHM honoured
```

## 4. Gates (final state of the branch)

| Gate | Result |
| --- | --- |
| `ruff check .` + `ruff format --check .` | clean, 174 files formatted |
| `pytest -q` (real PostgreSQL, under the build lock) | 1 332 passed, 0 failed when run without `test_stage9_conflicts.py::test_time_entry_base`; that one test (not touched by this stage) answers `invalid_time_entry` («cannot end in the future») whenever the suite runs between 00:00 and 01:15 local — it posts an entry «yesterday 22:30 → 01:15» — and passed in the earlier full run at 22:35; to be re-run after 01:15 |
| `npm run typecheck` | clean |
| `npm run build` | ok, PWA 63 precache entries |
| `node scripts/check-i18n.mjs` | in sync (RU = EN) |
| `bundle-report.mjs --check` | main chunk `index-*.js` **293.3 kB** raw / 90.6 kB gzip ≤ 300 kB; no charts, stats or motion at start; `TodayMore` 10.8 kB is its own chunk |
| `scripts/ui-check.mjs`, whole run (Chromium 390×844, fake AI) | **678 ok, 0 failed** |
| stage-10 «Сегодня, ниже» checks (`stage10-today.mjs`, in that run) | plan = the server's day, «Далее», «Добавить» → search on that meal, «Повторить» (one tap, UUID replay, «Отменить», double tap once, offline queue sent once), the insight sentence; look in both skins |
| axe-core WCAG 2.1 A/AA + 48 px targets on Сегодня with the blocks in view | 4 audits (Эволюция dark and light, Наклейки Сирень and Ночь): **0 serious/critical, 0 moderate, 0 minor**, every target ≥ 48 px; whole stage-10 theme audit 16 audits, 0 |

Screenshots (390×844 at 3×, Сегодня scrolled to the blocks): `/home/claude/runint/s10today/shots/`
`s10t-01-today-more-evolution-dark.png`, `s10t-02-today-more-evolution-light.png`,
`s10t-03-today-more-stickers-lilac.png`, `s10t-04-today-more-stickers-night.png`; axe report `s10t-axe.json`.

## 5. Deviations and decisions

- **No streak freezes.** The boards still draw «2 заморозки в запасе» in the status trio; the owner
  decided (stage 10 part 1) that freezes are gone from the app, so none is shown. The blocks of this
  document do not touch the trio.
- **«План дня» lists what exists.** The boards show five fixed rows; the app shows logged meals (one per
  meal type, kcal summed), planner tasks that have a time, and the main meals still missing. The
  board's «Дневник дня · 5 мин» is a timed task like any other (shown when the user has one). A day
  with nothing logged shows the three missing meals.
- **«Повторить» = the three most recent** (the server's recents), not most frequent; no new endpoint.
- **The insight is server-chosen** and may be absent; with no history, below the safe floor, or with no
  clear difference the block is simply not there (the board's example is the protein case).
- **Lazy chunk, quiet failure**: a first version let a failed chunk fetch bubble up and break Today when
  the dev server was offline (the stage-9 offline check caught it); now the loader swallows the failure.
- Stickers' «Далее» label and the insight badge lean ±2–3° as the boards do; lists, tiles and buttons never.
- Not in this part: the insight is not personalised beyond the four metrics; no new themes (only
  «Эволюция» and «Наклейки» exist at this base, the other skins will pick up the `sk-plan*`, `sk-repeat*`,
  `sk-insight*` hooks).
