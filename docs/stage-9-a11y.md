# Stage 9 — Hardening · 9D accessibility pass

> Every screen and sheet measured — axe-core in the dark and the light theme, every
> control against the 48 px rule, a keyboard pass, live regions, reduced motion, the
> document language, zoom — and fixed inside the design system: the tokens in
> `web/src/styles/index.css` and the `components/ui` primitives first, a screen's markup
> only where it was itself wrong. No redesign (stage 10); the visible changes are listed
> in §6.
>
> - One new package: **axe-core 4.14.0**, a pinned devDependency, injected into the page
>   by the browser check only — nothing in `src` imports it, it is not in `dist/`.
> - No API change, no migration.

## 1. How it is measured

`scripts/ui-checks/stage9-a11y.mjs`, the last module of `scripts/ui-check.mjs`. It seeds a
day (a meal, water, steps, sleep, a finished and a live workout, an area, a project, a
resource, tasks for today / tomorrow / the inbox, a done task, a daily repeat, a time
entry, a running timer) and removes all of it at the end.

| Check | What fails it |
| --- | --- |
| **axe-core** (`wcag2a wcag2aa wcag21a wcag21aa`) on 44 states × 2 themes = 88 audits: Today (with data), quick-add, score, steps, recipe, log, AI review sheets, Food, Train, template editor, live workout, Health, Plan today / inbox / upcoming, calendar day / week / month, task editor, task quick add, projects, project page, project sheet, areas, resources, journal, time log, Stats dashboards, achievements + sheet, Settings and profile / appearance / notifications / data / devices / tokens / shortcut, onboarding steps 1–4, pairing (empty and refused code); a sheet is audited inside its dialog | any `serious` / `critical` node (printed with its selector); `moderate` / `minor` are printed as notes. Everything lands in `$SHOTS/a11y-axe.json` |
| **48 px targets**: the bounding box of every button, link, field, `summary`, ARIA widget and `tabindex ≥ 0` element of each state | under 48 × 48 px, except inline links in a sentence and the five documented exceptions (§4); a tab-bar or header control whose centre is covered by another element |
| Text fields | font under 16 px (pinch-zoom is allowed now, so iOS would zoom into them on focus) |
| Roles | a control whose role is overridden by a non-interactive one (`<button role="listitem">`) |
| Graphics | an `svg` drawing (3+ shapes) neither hidden nor inside a named `role="img"` |
| **Live regions** sampled 5 × over 5 s on Today with a running timer and on a live workout during a rest | a region whose text changes on every sample (it would be read aloud every second) |
| A toast | not in a polite status region, or focus leaves the button that caused it |
| **Keyboard**: Tab through Today; the quick-add sheet and the task editor opened with Enter | focus not visible on a stop, a stop hidden under the header / tab bar, focus not moved into the sheet, Tab / Shift+Tab escaping it, Escape not closing it, focus not back on the opener |
| Reduced motion (`prefers-reduced-motion: reduce`) | a sheet that still slides in, CSS transitions that still run |
| `<html lang>` | not following ru → en → ru |
| Page width | any audited state wider than the screen (a phone would zoom the whole screen out) |
| Zoom | sideways scrolling at 320 px (WCAG 1.4.10: 400 % of a 1280 px window) or at 640 × 400 (200 % of a 1280 × 800 window) on Today, Food, Train, Plan, the week calendar, Stats, profile settings; pinch-zoom itself is axe's `meta-viewport` |

Visited when present, skipped with a note otherwise (the coordinator runs the module on the
integrated branch): **Settings → Интеграции** (`/settings/integrations`), the **sync sheet**
behind the header pill, and the **celebration sheet** (only when an achievement is unseen).

## 2. Before / after

Same harness, same 39 states (78 audits) on the base of this branch (`7fb4530`) and on its
head; the head adds 5 more sheets (88 audits), all clean.

| axe rule (impact) | before: dark | before: light | after |
| --- | ---: | ---: | ---: |
| `aria-progressbar-name` (serious) | 71 | 71 | 0 |
| `color-contrast` (serious) | 13 | 90 | 0 |
| `label-content-name-mismatch` (serious) | 45 | 45 | 0 |
| `label` (critical) | 8 | 8 | 0 |
| `select-name` (critical) | 2 | 2 | 0 |
| `button-name` (critical) | 2 | 2 | 0 |
| **serious + critical nodes** | **141** | **218** | **0** |
| `meta-viewport` (moderate) | 33 | 33 | 0 |

| Other checks | before | after |
| --- | --- | --- |
| Controls under 48 px | 127 distinct, in 36 of 39 states | 0 (5 documented exceptions) |
| Stats tab under the floating «+» | covered (centre hit-tests to «+») | clear |
| Sheet: focus in / trapped / Escape / focus back | none of the four | all four |
| Focused control hidden under the tab bar | yes (Today: «Шаги» card) | no |
| Toast: focus kept on «+250 мл» | dropped to `<body>` | kept |
| Reduced motion: sheet transform after open | sliding (`translateY(545px)`) | `none` |
| `<html lang>` after switching to English | `ru` | `en` |
| Rest timer live region | one `aria-live` region updated every 250 ms (found in the code; the sampling check came with the fix) | silent ticks; announced on start / change / end |

## 3. Found and fixed — per class

- **Sheets were not modal for a keyboard or VoiceOver user** (`components/ui` `Sheet`):
  focus now moves into the sheet (an `autoFocus` field keeps it), Tab / Shift+Tab cycle
  inside, Escape closes, focus returns to the control that opened it; only the topmost of
  stacked sheets answers. The dialog is named by its title (`aria-labelledby`), the
  backdrop and grab handle are `aria-hidden`, and a body that scrolls without anything
  focusable in it takes the focus itself (axe `scrollable-region-focusable`).
- **Progress bars and rings without names** (142 nodes): `ProgressBar` is decorative
  (`aria-hidden`) unless it gets a `label` — its numbers are always written next to it —
  and a labelled one carries `aria-valuetext`; `ProgressRing` takes `name` / `valueText`
  (Today: «Калории, 440 из 2 080 ккал», «Белки, 15 из 160 г») and hides its drawn text,
  or stays decoration. The export download bar is the one labelled bar.
- **Contrast** (103 nodes, 90 of them light): the fix is in the tokens. Light theme
  `--accent` #0EA393 → #0B6B63, `--accent-2` → #6D28D9, `--warn` #D97706 → #92400E; new
  `--on-accent` / `--on-accent-2` for text on accent fills (dark ink / white); the pastel
  palette shades used as text (`aqua-300/400`, `violet-300/400`, `amber-200/300/400`,
  `rose-200/400`) are theme tokens in `tailwind.config.js` — the dark theme keeps the exact
  hex values, the light theme reads them as darker shades of the same hue. Dark theme
  `--accent-2` #8B5CF6 → #A182F9 and `--text-muted` #8492AB → #8A97B0. Text faded with
  `opacity` (out-of-month days, future streak days, done tasks and future repeats in lists
  and the calendar, completed-task meta) is muted and struck through instead. Each value is
  the lightest that keeps 4.5:1 on the page, the cards, the sunken wells and its own
  10–15 % tint. Progress-ring strokes follow the tokens.
- **Accessible name ≠ visible text** (90 nodes, WCAG 2.5.3): controls with an `aria-label`
  that left out what they show («пн 26» named «Открыть день: Понедельник, 26 октября», the
  timer bar without its clock, month cells without their task chips) now take the name from
  their content: the full date / value is screen-reader text, the short on-screen forms are
  `aria-hidden` (Train week strip, calendar day headers and month cells, journal strip,
  time-log / steps / burned bars, sleep nights, the day-score button, the timer bar).
- **Charts**: the stage-4 burned chart was `<button role="listitem">` (announced as a list
  item, never as a button) — a `<ul role="list">` of buttons now, with `aria-pressed` like
  the other bar charts; each bar's value is its name. Stats and Health charts were already
  named `role="img"` summaries; the pairing logo is now `aria-hidden`, the pairing-code and
  token QR codes are named images. The check guards both patterns.
- **Labels and names** (label, select-name, button-name — critical): `Field` ties its label
  to the `Input` / `Select` inside without an explicit `htmlFor`, names a segmented control
  with it, and makes its hint / error the field's description. Icon-only revoke buttons
  (devices, tokens) and meal delete buttons say what they act on; the onboarding switches
  were named by raw keys («task_reminders»); the quiet-hours end field was named «—»; the
  profile's hard-coded Russian «Б г/кг» and «g» are built from translated words.
- **Targets under 48 px** (127): `.btn-sm`, segmented options and pressable chips are 48 px
  (were 36–40); a `Toggle` draws its 28 px track inside a 48 px button with negative margins
  (rows keep their height); `IconButton` never shrinks in a row; task rows, the workout
  exercise header, the task title field, the meal delete icon (was 16 px), onboarding
  «Назад» / «Пропустить», the pairing toggles, the header sync pill and settings button.
- **BottomTabBar**: the nav was labelled «Сегодня» → «Разделы приложения». «Статистика»
  was a text button half under the floating «+»: it is a tab like the others (icon,
  `NavLink`, `aria-current`, 48 px), and the «+» floats 3 rem above the bar so it never
  covers it; the page padding, the rest timer, toasts and the profile's sticky save button
  moved up with it.
- **Focus never lost or hidden**: a `Button` that is loading stays focusable
  (`aria-busy`, `aria-disabled`, presses ignored) — a disabled button dropped focus to
  `<body>`; `scroll-padding` keeps a control scrolled into view by Tab or VoiceOver clear of
  the sticky header and the tab bar; date / time fields keep their focus border while Tab
  sits on their calendar button (`:focus-within`).
- **Live updates**: toasts were already a polite status region (now verified, focus never
  moves); the journal autosave status is `role="status"`; the running timer ticks inside a
  button, not a live region; the rest timer was one `aria-live` region updated every 250 ms
  — the countdown is a silent `role="timer"`, a polite status says «Отдых 1:30» on start /
  change and «Отдых окончен» at the end.
- **Reduced motion**: CSS already honoured it; framer-motion did not — `MotionConfig
  reducedMotion="user"` at the root (sheets, toasts, layout animations).
- **Language**: `<html lang>` follows the UI language (`i18n/documentLanguage.ts`), so
  VoiceOver reads English UI with an English voice.
- **Lists in Safari**: Tailwind's preflight `list-style: none` makes WebKit drop list
  semantics (VoiceOver never says «список, 7 объектов»); `ul` / `ol` get an invisible
  zero-width marker in the base layer instead, utilities (`list-disc`, …) still win.
- **Screen-reader text in a scrolling row** (found by the whole run, caused by this pass):
  `.sr-only` is `position: absolute`; inside the journal's 30-day strip it escaped the
  horizontal scroller and made the page 1 661 px wide. `.scroll-x` is `position: relative`
  now, and the check fails any state wider than the screen.
- **Zoom**: the viewport set `maximum-scale=1` (axe `meta-viewport`); pinch-zoom works now.
  Every text field is 16 px or more (checked), so iOS does not zoom into it on focus.

## 4. Targets that stay under 48 px (each with its reason; all meet WCAG 2.5.8 AA)

| Target | Size | Why |
| --- | --- | --- |
| Burned-kcal and time-log day bars | 45 × 100+ px | 7 days (or a month) side by side on a 390 px card; the value is the bar's spoken name and shows on tap |
| Health steps bars | 24 × 130 px | 14 days side by side |
| Week-calendar day headers | 45 × 48 px | 7 columns beside the hour gutter; the day view's arrows reach the same days |
| Compact chips in a week column (all-day tasks, repeats) | ~41 × 20 px | the day view shows the same task as a 48 px chip |
| Time blocks, continuations, resize handles | height = duration | the size is the data (essential); the same task opens at 48 px from the day list and Plan › Сегодня |

The list lives in `TARGET_EXCEPTIONS` in the check; anything else under 48 px fails.

## 5. Files

```
scripts/ui-checks/stage9-a11y.mjs            ★ the check (axe, targets, keyboard, live regions, zoom)
scripts/ui-check.mjs                         + stage9-a11y in the module list (last)
web/package.json · package-lock.json         + axe-core 4.14.0 (devDependency, pinned)
web/index.html                               viewport without maximum-scale
web/tailwind.config.js                       palette text shades and on-accent as theme tokens
web/src/styles/index.css                     tokens (contrast), 48 px btn-sm / segmented / chips,
                                             scroll-padding, focus-within on fields, tab-bar offsets
web/src/main.tsx                             MotionConfig reducedMotion="user", <html lang> sync
web/src/i18n/documentLanguage.ts             ★ <html lang> follows i18next
web/src/components/ui/index.tsx              Sheet (modal focus), Field (labels), Button (busy keeps
                                             focus), Toggle (48 px), ProgressBar, SegmentedControl
web/src/components/ui/ProgressRing.tsx       name / valueText, token strokes
web/src/components/layout/                   BottomTabBar (Stats tab, nav label), AppShell, SyncStatus
web/src/components/ui/Toast.tsx · features/plan/tasks/toast.tsx   above the floating «+»
web/src/features/**                          per-screen names, targets, list semantics, dimming
web/src/i18n/locales/{ru,en}.json            + a11y.* (15 keys each, the section opens the file)
docs/stage-9-a11y.md                         this report
```

## 6. Visible changes (inside the design system, for the stage-10 redesign to know)

- Light theme: accent, violet, warn and the pastel text shades are darker (contrast); white
  text on accent buttons. Dark theme: violet `--accent-2` and muted text slightly lighter.
- Small buttons, segmented controls and pressable chips are 48 px tall.
- The «+» floats 3 rem above the tab bar; «Статистика» is an icon tab under it; toasts, the
  rest timer and the page padding sit higher to stay clear of it.
- Done tasks and future repeats are muted and struck through, not faded.

## 7. Commands

```bash
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
# the whole browser run on a fresh DB, under the shared lock (Chromium is heavy on 2 cores)
flock -o /home/claude/locks/ui-check.lock <fresh DB + API + Vite> \
  PAIRING_CODE=XXXX-XXXX FAKE_CAMERA=/tmp/barcode.y4m node scripts/ui-check.mjs
# report: $SHOTS/a11y-axe.json (every axe node, target list, keyboard stops)
```

## 8. Evidence (this branch)

FILLED IN BELOW AFTER THE RUN

## 9. What remains and why

- **VoiceOver on a real iPhone was not run** — this environment has no device. The check
  covers what VoiceOver depends on (names, roles, states, modality, focus order, live
  regions, language); the manual walk to do once on the phone: rotor through Today
  (headings, the ring «Калории, … из …», the macro rings), open «+» and close it with a
  two-finger scrub, edit a task, log a set and listen to the rest timer, switch to
  English and back.
- **The five target exceptions** (§4) need a layout change (wider bars, fewer columns) —
  stage 10.
- **Placeholders** use the muted colour at 70 % — axe does not measure placeholders and they
  never carry the only label (every field has a real one now).
- **Text-only scaling**: sizes written in px (`text-[11px]`, `text-[15px]`) do not follow a
  browser's text-size setting, only zoom; pinch-zoom and page zoom work and reflow is
  checked. Moving them to rem is a redesign-wide change (stage 10).
- **Calendar drag** has no keyboard equivalent on the grid itself; the task editor sets the
  same date and time by keyboard.
- **Settings → Интеграции and the sync sheet** are not on this branch; the check audits them
  (and the header pill) on the integrated branch and fails if they break a rule.
