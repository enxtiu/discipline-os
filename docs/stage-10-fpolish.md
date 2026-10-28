# Stage 10 — «Наклейки», polished against the final boards

Branch `stage-10-skin-fpolish` (from `stage-10-themes` 2ecb9cf). Every screen of the stickers
skin was compared with its final board (`Ffin-Food · -Stats · -Train · -Plan · -Add · -Theme`,
and `-Night` / `-Mint` for the dark and mint sets) in the running app — light and dark, Сирень and
Мята — and the gaps closed. Сегодня («План дня», «Повторить», the insight) belongs to the
`stage-10-today` branch and is untouched; the screen's header and its stickers are as before.

## 1. What changed

| Screen | Gap found | Done |
| --- | --- | --- |
| **Every tab** | a greeting and a name over a separate page title, 16 px margins | the tab's title (30 px / 800; 28 px on Статистика) shares the header row with the page's own buttons, sync and settings (`PageActions` renders in the header through a slot; in every other theme it renders in place, so «Эволюция» is unchanged); the page's `h1` stays for screen readers; 20 px margins as the boards; Оформление's back button and title on one row |
| **Питание** | no day switch, no day total, meals as plain rows | «Ср, 28 октября» · сегодня with the previous / next day (browsing: «＋» only on today); the lemon day-total sticker (calories, bar, «Осталось», four macros); a card per meal type — title and time, a kcal sticker, «＋» that opens the search for that meal (`defaultMealType`), a line per food with its grams and kcal, delete kept; «ИИ» tags on Фото and Текст; the three ways to log in one 48 px row; recents and favourites below the meals |
| **Тренировки** | a plain card with one line of exercise names | the mint «Сегодня по плану» card: name, a tilted «Упражнений: N / ~M мин» sticker, the white list of numbered exercises («4×8 · 80 кг»), «Начать по плану» and «Кардио»; the week's date range; 58 px day cells with the done badge, «отдых» dashed; the burned card as title / figure / note; outlined burned bars; star sticker on records |
| **Статистика** | the level plaque was three wells under the title; «Достижения» a wide button | the plaque as the board: the medal over the corner, «Профи» with «N XP всего», «+N XP за неделю» (mint) and «+N сегодня» stickers, the tomato streak sticker top right, a 22 px bar with «340 / 690 XP» (on the fill past the half, on a white label at night), «до 13 · Эксперт» and «осталось 350 XP»; «Достижения» an icon button (lavender) in the header row; the calorie card's figures flat |
| **План** | big buttons over the title, grey counts, ring checkboxes with priority colours | search and the lemon «Новая задача» beside sync and settings; the day heading 18 px; counts as stickers (tomato for «Просрочено»); P1 (tomato die-cut) / P2 (lemon) / P3 (sky) stickers at the right of a task, named for screen readers; white sticker checkboxes; «Выполнено сегодня» a sticker button; the running timer as the black bar with the tomato shadow, lemon time, tomato stop; the idle timer a dashed sticker |
| **Быстрое добавление** | order and content differed | a dialog in the board's order (Фото, Текст, Штрих-код, Поиск, вода, шаги, «Начать тренировку · по плану: ноги» on its own row, задача, итоги), «ИИ» tags, «Недавнее · запись в одно касание» — three recents logged again in one tap with their last grams |
| **Night / Мята** | bright tiles kept the light outline on their nested stickers | inside a bright tile at night the nested outlines are #111 (the tile keeps #F5F3FF), as `Ffin-Night`; tilt off in Мята still straightens every new label |

Floating «＋ Записать» and the tab bar's 2 px top border were already as `Ffin-Plan` (right 16 px,
14 px above the bar, 52 px, 2 px ink border, hard 4 px shadow, `aria-haspopup="dialog"`); checked
again, unchanged.

## 2. Files

```
web/src/styles/skins/stickers-polish.css        ★ all of the polish (its own file: stickers.css is not touched, so the merge
                                                  next to the Сегодня work is trivial); @import in styles/index.css
web/src/lib/appearance.ts                       SkinTraits.headerTitle, boardContent (stickers: true, Эволюция: false)
web/src/components/layout/headerSlot.tsx        ★ PageActions (portal into the header slot; in place for other themes), tabTitleKey
web/src/components/layout/{AppShell,QuickAddSheet}.tsx   the header row; the quick add's tags, plan tag, recents
web/src/features/food/{FoodBoard,useRelog,mealType}.ts(x) ★ day switch, day total, meal card; one-tap re-log; guessMealType (its own
                                                  module so the main chunk does not pull LogSheet)
web/src/features/food/{FoodPage,LogSheet}.tsx   the board forms behind boardContent
web/src/features/train/{TrainBoard,TrainPage,BurnedChart}.tsx   exercise list, week range, hooks
web/src/features/stats/{LevelPlaque,StatsPage,parts}.tsx        the sticker plaque (its own branch for levelMedal), the header action, flat figures
web/src/features/plan/{PlanLayout,tasks/TaskList,tasks/TaskRow,timer/TimerBar,views/PlanTodayPage}.tsx   header actions, counts, priority stickers, hooks
web/src/features/settings/{SettingsHeader,ThemeSection}.tsx     hooks for the header row
web/src/i18n/locales/{ru,en}.json               board.* (ru + en)
scripts/ui-checks/stage10-themes.mjs            the polish checks (§4)
```

Nothing of «Эволюция» changes: every new element is behind a skin trait or is an inert class.

## 3. Decisions

- **One extra stylesheet.** `stickers-polish.css` is imported right after `stickers.css`; it uses the
  same tokens and scope. Never select `.btn` (the same rule as before).
- **The header slot, not a fork.** `PageActions` takes the page's own buttons to the header row when the
  theme asks (`headerTitle`); the shell shows the tab's name as an `aria-hidden` title and the page's
  `h1` becomes visually hidden (it stays the page's heading). Pages without a title route are untouched.
- **Питание's day switch browses.** The day data is the server's (`GET /nutrition/day?date=`), the
  previous day is two taps away; adding food always logs now, so «＋» is hidden on a past day.
- **Counts, not plurals.** «Упражнений: 5», «дн.», as the rest of the app; the board's «12 дней» /
  «5 упражнений» need plural forms the app avoids.
- **48 px wins over the board's 4 px gutter** in the week strip: seven cells of 48.3 px in the 350 px
  column (3 px gutter, 3 px margin) instead of 46.6 px.
- **Large text.** Статистика's title is a size smaller; the Plan's four view tabs take 13 px / 3 px padding
  in the narrower column.

## 4. Remaining differences from the boards (and why)

- **Статистика: «Раздел статистики» chips** are links that scroll to a section, so none is «pressed»
  (the board draws «Питание» black); there is no scroll-spy to make one honest.
- **«Наклейки недели»** says «N за неделю», the board «3 из 5» (the strip has no fixed 5).
- **Тренировки: templates** are rows with «Начать» (the board: three chips), **records** a list (the board:
  one «Рекорд» sticker) — the app shows all of them and a template's count and use.
- **Питание: the board has no recents / favourites / recipes** — they stay (they are how food is logged in two
  taps), below the meals; a past day cannot be added to.
- **План: the four views** (Сегодня · Входящие · Предстоящие · Календарь) stay as a segmented control
  under the timer; the board shows only the PARA chips.
- **The plaque** adds «+N сегодня» (the app's earlier requirement) and says «12 дн.» not «12 дней»;
  the XP numbers sit on a white label in the night set because the night track is dark.
- **Язык и регион** (a settings sub-page the boards do not draw): its «Начало недели» label is cut to
  «Начало н…» in the narrower 350 px column.
- **Сегодня** is not in this branch's scope (see the top).

## 5. Commands

```bash
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
node scripts/perf/bundle-report.mjs --check
PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs     # stage10-themes carries the polish checks
```

## 6. Evidence (this build)

Run in this container on 28–29 Oct 2026, on the branch head (stack `dsos_s10_fpolish`, API :8038, Vite :5238;
the full run on a fresh `dsos_s10_fpolish_ui` with the fake AI, `CHROMIUM_NO_DEV_SHM=1`, under the shared locks).

| Gate | Result |
| --- | --- |
| `npm run typecheck` | exit 0 |
| `npm run build` | exit 0 — main chunk **296.63 kB** (gzip 91.47 kB; part 1 had 292.83 kB), CSS 99.56 kB (gzip 17.24 kB; part 1 84.41 / 14.94 — the polish stylesheet), PWA precache 61 entries / 2 323.10 KiB |
| `node scripts/check-i18n.mjs` | complete and in sync — 1 969 keys in each locale (`board.*` added, ru + en) |
| `node scripts/perf/bundle-report.mjs --check` | ok — main ≤ 300.0 kB; no charts, stats or motion features at start (the first build was 300.74 kB: `LogSheet` came in with a shared helper; `guessMealType` moved to its own module) |
| whole `ui-check.mjs`, fresh DB | **663 ok, 0 failed** (part 1: 646) |
| `stage10-themes` (in that run) | 77 ok: the polish checks (header title on four tabs, day switch and day total = API, ИИ tags, quick-add order / dialog / recents, P1–P3 stickers, plaque geometry, seven 48 px day cells, Оформление's header row) + axe on 17 states (Сирень and Ночь; the quick add with recents added): 0 serious/critical, 0 moderate, 0 minor; every target ≥ 48 px; large text: 9 screens × 2 themes never wider than 390 px |
| `stage9-a11y` (in that run) | 100 audits (screens × themes): 0 serious/critical, 0 moderate, 0 minor |
| `pytest` · `ruff` | not run: no API change |

Two defects the first full run caught are fixed above (recents' accessible name; the Тренировки card's
label at night with nothing planned); its other two failures (the offline sync-sheet state and the console
error behind it) were a dev-server load flake — the sheet's module is prefetched when the page is idle
and the machine was shared — and passed on the next full run without a change to that code.

Screenshots, 390 × 844: `/home/claude/runint/s10-fpolish/shots/before/` and `/shots/after/` — `f-lilac-*`,
`f-night-*`, `f-mint-*` (tilt off), each `today · food · food-more · stats · stats-more · train · train-more ·
plan · plan-more · plan-timer · add · theme · theme-more · region` (before: the first nine of them).
