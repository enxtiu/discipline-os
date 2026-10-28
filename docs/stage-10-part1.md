# Stage 10, part 1 — themes: the engine, «Оформление», the level for every theme, F «Наклейки»

> The owner decided that every redesign direction becomes a **theme** chosen in the app
> (Настройки → Оформление). All themes share the same screens and features and differ in look
> and a few per-theme parameters. This part ships the theme engine, the «Оформление» screen,
> the level and XP for every theme (Сегодня and Статистика), and the first real theme:
> **F «Наклейки»** (the final boards `Ffin-*.dc.html`). «Эволюция» (direction G, the stage 1–9
> look) stays the default and is pixel-identical wherever this part did not mean to change it.
>
> Two owner decisions arrived during the work and are in this part: **no streak freezes**
> anywhere in the app (frontend only — the server change is separate and merges later), and
> the stickers' quick add as a floating **«＋ Записать»** sticker above the tab bar.
> No backend change. One new dependency: `@fontsource/rubik` 5.3.0 (below).

## 1. Plan that was executed

| # | Item | Delivered |
| --- | --- | --- |
| 1 | Settings model | `lib/appearance.ts`: `skin` evolution (default) · stickers; reserved ids forge, clarity, pulse, notebook, ios, gentle, glass (shown «Скоро»); mode light · dark · system (system is new; the mode keeps the stage-1 key `dsos.theme`, so older code and tests keep working); stickers' `ground` lilac (default) · mint · peach · lemonade, `tilt` on/off (on), `shadow` hard (default) · soft; `largeText`, `reduceMotion` for every theme. **Per device** in localStorage (`dsos.appearance`), next to the device token — **no server sync in this part**. |
| 2 | Apply | Data attributes on `<html>`: `data-theme` (resolved), `data-skin`, `data-ground`, `data-tilt`, `data-shadow`, `data-text`, `data-motion`; `<meta name="theme-color">` per theme and mode; `color-scheme` follows `data-theme`. **No flash**: `index.html` applies the same rules inline before any CSS or app code (and the boot screen takes the theme's page colour); `installAppearance()` re-applies them and keeps them live (the OS dark/light switch in system mode, the OS motion preference, another tab). «Меньше анимации» and `prefers-reduced-motion` drive the CSS (`:root[data-motion='reduce']`), framer-motion (`AppMotionConfig`) and every live reduced-motion hook (sync pill and sheet, celebration, charts, planner toasts). «Крупный текст»: rem type and spacing ×1.125, the pixel sizes follow. |
| 3 | Category tokens | `--cat-kcal, -protein, -fat, -carbs, -fibre, -water, -steps, -streak, -score, -xp` + `--on-cat-*`; «Эволюция» values = the tokens each was drawn with (dark and light), so nothing changes. `ProgressRing` / `ProgressBar` take a category tone (the stage-8 glow stays); Today and the score card draw with them. Charts: role tokens `--chart-food, -move, -score, -xp, -rest, -grid` and an optional mark outline (`--chart-outline`), re-read when `data-skin`/`data-ground` change. |
| 4 | «Оформление» | Its own page `/settings/theme`, an entry in the hub with the theme and mode as hint, laid out as `Ffin-Theme`: «Так будет выглядеть» (today's real numbers in the current look, live), «Тема» (a `radiogroup` of nine tiles drawn with each theme's colours from design-finals; Эволюция and Наклейки selectable, seven «Скоро» with `aria-disabled`; arrows move and choose, as any radio group), «Режим» (Светлая · Тёмная · Как в системе), the stickers' parameters (Набор цветов, Наклон наклеек, Тени) when Наклейки is chosen, «Для всех тем» (Крупный текст, Меньше анимации). The stage-1 section keeps its route and controls as **«Язык и регион»** (language, timezone, week start, units — and the mode, now with «Как в системе», plus a way to Оформление). RU + EN. |
| 5 | Level and XP for every theme | **Сегодня**, under the calories: the day score, the overall streak («Пропуск обнулит серию»), «ур. 12 · Профи» / «Ур. 12 · Профи» with total XP, a bar to the next level, XP to go and XP today (`/gamification/today`, the read the score card already makes). **Статистика**, at the top above the period switch: the level and title, a big XP bar named from the ladder («340 / 690 XP до «13 · Эксперт»», «осталось 350 XP»), total XP, XP this week (the sum of the server's current week, as «Прогресс» counts it), XP today, the streak (`/gamification/profile` + the current week's progress series — the request the dashboards already make). Both look right in «Эволюция» (plain cards and its ring). No backend change. |
| 6 | F «Наклейки» | `styles/skins/stickers.css` (imported by index.css): tokens for the four grounds and the night set, contrast-checked; Rubik self-hosted; the shared classes as stickers (2 px outline, hard 4/3/2 px offset shadow or the soft one); category stickers; die-cut badges; the peel corner; the level medal; tilt on badges only; the tab bar and «＋ Записать»; sheets, skeleton, focus ring; outlined chart bars; «Наклейки недели». Details in §6. |
| — | Owner: no streak freezes | Every freeze UI removed: Сегодня's status and the evening at-risk hint, the plaque, the streak calendar (marking, tile, legend, tooltip, summary), the achievements screen (rule and tokens); strings removed. The server still sends `freeze_tokens` / `freeze` until its change lands; nothing shows them. |

## 2. Files

```
web/src/lib/appearance.ts                     ★ the store: read · apply · live · hooks; SKIN_TRAITS
web/index.html                                inline pre-paint apply; the boot screen per theme
web/src/main.tsx · lib/motion.tsx             installAppearance(); AppMotionConfig (less motion)
web/src/lib/device.ts                         the theme functions moved to appearance.ts (THEME_KEY stays)
web/src/styles/index.css                      category + chart-role tokens; data-motion / data-text rules; @import of the skin
web/src/styles/skins/stickers.css             ★ theme F: Rubik @font-face, tokens, component overrides, sk-* hooks
web/src/components/ui/ProgressRing.tsx · index.tsx   category tones, sk-* hooks (ring, bar, sheet)
web/src/components/layout/{AppShell,BottomTabBar,QuickAddSheet,SyncStatus}.tsx · ui/Toast.tsx   hooks; the «Записать» sticker form
web/src/features/settings/ThemeSection.tsx    ★ Настройки → Оформление
web/src/features/settings/{AppearanceSection,SettingsPage,sections}.tsx · App.tsx   «Язык и регион», the hub entry, the route
web/src/features/game/TodayStatus.tsx         ★ Сегодня: score · streak · level / XP
web/src/features/stats/LevelPlaque.tsx        ★ Статистика: the level plaque
web/src/features/stats/WeekStickers.tsx       ★ «Наклейки недели» (a stickers trait)
web/src/features/today/parts.tsx              ★ the calorie sticker, the macro bars (stickers traits)
web/src/features/today/TodayPage.tsx          tones, the status, the trait forms
web/src/features/stats/{StatsPage,charts/*,sections/ProgressSection,derive}.ts(x)   plaque, strip, chart roles, outline, no freezes
web/src/features/game/{DayScoreCard,AchievementsPage,Celebration,rules,icons}.tsx   tones, no freezes, the shared motion hook
web/src/features/{food/FoodPage,train/TrainPage,plan/tasks/toast,sync/useQueue}.ts(x)   hooks, the shared motion hook
web/src/i18n/locales/{ru,en}.json             look.*, game.status.*, stats.plaque.*, stats.week_stickers.*, … (1 945 keys each)
web/package.json · package-lock.json · vite.config.ts   @fontsource/rubik 5.3.0; Rubik out of the precache
scripts/ui-checks/stage10-themes.mjs          ★ the stage-10 browser checks; scripts/ui-check.mjs runs it before stage9-a11y
scripts/ui-checks/stage8-game.mjs             the streak rows: no freezes
scripts/ui-checks/stage9-a11y.mjs             Настройки → Оформление among the audited settings states
docs/stage-10-part1.md · docs/roadmap.md
```

## 3. Commands

```bash
# gates
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
node scripts/perf/bundle-report.mjs --check
PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs        # now with stage10-themes

# try a theme by hand: Настройки → Оформление → Наклейки; or from the console
localStorage.setItem('dsos.theme', 'light')            # light | dark | system
localStorage.setItem('dsos.appearance', JSON.stringify({ skin: 'stickers', ground: 'mint', tilt: false, shadow: 'soft', largeText: false, reduceMotion: false }))
```

How the browser run was done here (this container, as the owner agreed): a fresh export of the
branch (`git archive`) with its own `npm ci`, PostgreSQL 16 (UTF-8, en_US), the API with the fake AI
provider and Vite on their own ports and database, `scripts/ui-check.mjs` under
`flock -o /home/claude/locks/ui-check.lock` with `CHROMIUM_NO_DEV_SHM=1` (§6, «Local runs»;
runner: `/home/claude/runint/s10f/uicheck.sh`).

## 4. What to test (walkthrough)

1. **Оформление.** Настройки → «Оформление» (the hint says «Эволюция · Тёмная») → the preview shows
   today's calories and protein/fat → tap «Наклейки»: the whole app turns into stickers at once.
   Режим «Светлая» → the lilac sheet; «Набор цветов» Мята / Персик / Лимонад; «Наклон наклеек» off
   (badges straight); «Тени» Мягкие; «Крупный текст»; «Меньше анимации» (no slides, no tilt).
   «Как в системе», then switch the iPhone between light and dark — the app follows.
2. **No flash.** Close the app and open it again: the boot screen already has the theme's colour,
   the first screen is drawn in it (also the iOS status bar area, `theme-color`).
3. **Сегодня** (both themes): under the calories — «Оценка дня», «Серия · N дн.» with «Пропуск
   обнулит серию», «ур. N · title» with the XP bar, «ещё N XP до ур. N+1», «+N XP сегодня». In
   «Наклейки»: the lemon calorie sticker with the peel corner and «Осталось N ккал», the die-cut
   score and streak, the black level medal, macro bars, the water and steps tiles, «＋ Записать».
4. **Статистика**: the level plaque first (in «Наклейки» the medal overhangs its edge), then the
   period switch; the calorie bars are outlined in «Наклейки»; «Наклейки недели» appears after the
   calories once something was unlocked this week.
5. **Night**: Режим «Тёмная» with «Наклейки» — the stickers stay bright, outlines turn light.
6. **«Эволюция»** looks exactly as in stage 9 everywhere except the new status on Сегодня, the
   plaque on Статистика and the screens without freezes.

## 5. Evidence (this build)

Commit `c222faf`, as a fresh `git archive` export with its own `npm ci` (the commit after it
changes only this file). Run in this container on 28 Oct 2026.

| Gate | Result |
| --- | --- |
| `npm run typecheck` | exit 0 |
| `npm run build` | exit 0 — main chunk **292.83 kB** (gzip 90.41 kB; `main` was 282.49 kB), CSS 84.41 kB (gzip 14.94 kB), PWA precache 61 entries / 2 291.76 KiB. The eight Rubik woff2 files (7.85–19.11 kB) are not in the precache. |
| `node scripts/check-i18n.mjs` | complete and in sync — 1 945 keys in each locale, 1 502 referenced in code, 35 reserved for later |
| `node scripts/perf/bundle-report.mjs --check` | ok — main 292.9 kB ≤ 300 kB; no charts, stats or motion features at start |
| `pytest` · `ruff` | not run: no API change (`git diff main -- api` is empty) |
| `scripts/smoke.sh` (API of the local stack) | 26 checks passed (stages 1–9) |
| whole `ui-check.mjs`: fresh DB `dsos_s10f_ui`, fake AI, VAPID keys, every module including `stage10-themes` | **646 ok, 0 failed** (exit 0; stage 9 ended at 585) |
| `stage10-themes` (part of that run) | 60 checks; axe on 16 states (Оформление in dark and light; Наклейки's Сегодня, quick-add sheet, Питание, Статистика, Тренировки, План and Оформление in Сирень and Ночь): **0 serious/critical, 0 moderate, 0 minor** nodes; every target ≥ 48 px; with large text, 9 screens × 2 themes never wider than 390 px; «Наклейки недели» with 6 stickers in the week |
| `stage9-a11y` (part of that run) | 100 audits (screens × themes), Настройки → Оформление included: 0 serious/critical, 0 moderate, 0 minor nodes |
| «＋ Записать» against the owner's spec (Сирень · Ночь) | right 16 px, 14 px above the bar's top edge, 52 px tall, padding 0 18 0 12, border 2 px #111111, radius 16 px, fill #FFD84D, text #111111 16 px / 800, plus 22 × 22, shadow 4 px 4 px 0 #111111 (night #4C4766), `aria-haspopup="dialog"`, fixed with the bar; the bar's top border 2 px #111111 (night #F5F3FF) |
| «Эволюция» pixel diff against `main` (same API and data, dark and light, 390 × 844 at 3×) | **identical on 24 of 34 screens** (Питание, Тренировки, Здоровье, План, Входящие, Предстоящие, the settings sections, Достижения, the quick-add sheet). Different only where intended: Сегодня below the calories (the new status), Статистика (the plaque on top), the settings hub («Оформление», «Язык и регион») and «Язык и регион» itself (the mode with «Как в системе», the way to Оформление). The week calendar differs by its «now» line (18:26 → 18:27 between the shots). |

Screenshots for the owner, 390 × 844, in `/home/claude/runint/s10f/shots/`:
`f-lilac-*`, `f-mint-*` (tilt off) and `f-night-*`, each `01-today`, `02-today-more`, `03-add`,
`04-food`, `05-train`, `06-plan`, `07-stats`, `08-stats-more`, `09-theme`, `10-theme-more`; and
`evo-dark-01-today`, `-02-today-more`, `-07-stats`, `-09-theme` (the level in «Эволюция»).

## 6. Decisions

- **Themes are CSS; a few widgets ask for their form.** Everything a theme changes is tokens and
  the shared classes under `:root[data-skin='…']`. Where CSS cannot reshape a widget (a ring cannot
  become a bar) the component asks `useSkinTraits()` for its form — `calorieHero`, `macros`,
  `waterSteps`, `levelMedal`, `addButton`, `weekStickers` — never for the theme's name. «Эволюция»
  keeps its stage 1–9 forms, so its JSX path is unchanged.
- **Hooks, not forks.** Components carry inert `sk-*` classes and `data-cat` (no style of their own)
  for the themes. Never select `.btn` in a skin: index.css builds the variants with `@apply btn`,
  and Tailwind copies any rule naming `.btn` into each variant *after* the skin's rules.
- **Token specificity.** Light/dark tokens of a theme use `:root[data-skin][data-theme]` (0,3,0), so
  they beat `:root[data-theme='light']` whatever the order; the skin file is imported first.
- **Stickers' colours and contrast** (`Ffin-Style`): grounds Сирень #E8EAFB, Мята #DBF1E5, Персик
  #FBE6DC, Лимонад #F0EFD2 (text #111111 ≥ 15.7:1, labels #4A4A57 ≥ 7.3:1); night ground #17161F,
  tiles #24222F, text #F5F3FF (16.4 / 14.2), labels #B4B0C8 (8.5 / 7.4), outlines #F5F3FF, shadow
  #4C4766, tracks #3A3749; stickers lemon #FFD84D (calories), tomato #FF6B4A (streak), sky #6EC1FF
  (water), mint #5EE0A0 (steps), lavender #B9A2FF (score, XP) carry #111111 text only (#3B3A33 as a
  second shade on lemon, 8.2:1). Inside a bright sticker the muted and accent tokens are re-scoped
  to ink, so no #4A4A57 ever lands on tomato, sky or lavender. axe found 0 contrast nodes (§5).
- **The safety rules hold in the stickers' charts.** Only in-zone finished days get the calorie
  colour; other logged days are outlined white bars; the day in progress is a dashed outline.
- **Rubik** (`@fontsource/rubik` 5.3.0, OFL-1.1; the stack is pinned, so this is the stage's one new
  dependency, `--save-exact`): only the weights used (500–800), Latin + Cyrillic, woff2 only, with
  `unicode-range`. The `@font-face` rules live in the skin file, so a browser fetches the files only
  when text is set in Rubik — with the theme on. The service worker does not precache them
  (`globIgnores`); the existing woff2 runtime rule caches them on first use (offline afterwards).
- **The level everywhere.** The status and the plaque only display server numbers; the week's XP is
  the sum of the server's current week (its own `from`/`to`), as the «Прогресс» card already sums
  it. «Наклейки недели» places an unlock time on a day with the session's timezone (formatting)
  and compares it with the server's week — no day boundary of the client's own.
- **Naming.** The app's «Оценка дня» stays (the boards say «Счёт дня»); the streak tile is «Серия»
  (the overall streak, «Оценка 60+»). No plural forms (the app avoids them): «Заморозки…» texts are
  gone anyway; numbers keep the existing «дн.» forms.
- **«Язык и регион».** The stage-1 «Внешний вид» section keeps its route, its language / timezone /
  week-start controls and the mode (so the stage-1 browser checks still pass unchanged); «Оформление»
  is the theme screen.
- **The quick add in «Наклейки».** The owner changed it four times during the part (round sticker →
  square → full-width → docked tab → final): a lemon «＋ Записать» sticker floating at the right
  above the bar (right 16 px, 14 px above the bar's top edge, 52 px, `aria-haspopup="dialog"`), five
  tabs in a row; the page, toasts and the rest timer keep clear of it.
- **«Наклейки недели» with many stickers.** The board shows three stickers and two empty places in
  one row («3 из 5»). A name is set on two balanced lines as on the board («Первая / тренировка»),
  so a sticker is as narrow as its longer line; the stickers come in the order they were earned
  (the newest five); with four or five, or long names, the strip wraps to a second row instead of
  running past the screen (the full run caught that with six stickers in the week).
- **«Крупный текст»** is rem ×1.125 plus the fixed pixel sizes one step up. Two rows that share
  the 390 px width grow less: the tab bar labels and, in «Эволюция», the plan's four view tabs
  (13.5 px, a little less padding). The stage-10 checks sweep nine screens in both themes with
  large text: none is wider than the screen.
- **No freezes, frontend only.** The read types keep the server's fields (the OpenAPI drift guard);
  nothing displays them. The backend change is separate and merges into this branch later.
- **Local runs.** This container's PostgreSQL accepts TCP logins with a password only, so the runs
  use a role of their own (`dsos_s10f`, its password outside the repo) instead of the passwordless
  `postgres` the stage-9 run scripts assumed. The bash tools share one 6 GB memory cgroup that
  counts `/dev/shm`, and this sandbox's Chromium launcher drops Playwright's
  `--disable-dev-shm-usage` unless `CHROMIUM_NO_DEV_SHM=1`; headless Chromium on the Vite dev
  server holds ≈ 230 MB of `/dev/shm` per navigation (the same on `main`), so two full runs were
  OOM-killed in their last module (`dmesg`). The final run sets `CHROMIUM_NO_DEV_SHM=1` (the
  buffers become files in `/tmp`); nothing in the repo changed for it.

## 7. Not in this part

- **Server sync of the appearance** (it is per device; a second phone starts with «Эволюция»).
- The other themes (A–E, H, I are «Скоро»; D «Блокнот» is being built in a parallel branch).
- New content from the unified «Сегодня» boards that is not a look: «План дня» timeline,
  «Повторить» (one-tap re-log) and the insight line (in a parallel branch); the header's date
  line; Питание's day switcher and day-totals sticker; the «ИИ» labels on the AI buttons; the
  Plan's priority stickers and «Далее».
- The freeze removal on the server (separate change).
