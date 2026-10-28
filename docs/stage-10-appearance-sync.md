# Stage 10 — optional sync of the appearance across a user's devices

> Until now the appearance (Настройки → Оформление: theme, mode, the stickers' parameters, «Крупный текст»,
> «Меньше анимации») lived on one device, in `localStorage` (`docs/stage-10-part1.md`). Now it can also live on the
> **account**: a per-device switch **«Одинаково на всех устройствах»** (on by default) keeps the look the same on every
> device of the user. Everything is per user — keyed by `user_id`, nothing global — because the app is meant to become a
> multi-user service with clients and subscriptions (§6).

## 1. Plan that was executed

| # | Item | Delivered |
| --- | --- | --- |
| 1 | Storage | `user_settings.appearance` — one nullable JSONB column (migration `0005`, `NULL` = never synced). The object is validated on every write: known skins, modes and parameters only; unknown keys and values answer 422. |
| 2 | API | `GET /settings` (and `/auth/session`, the pairing answer) show `settings.appearance`; `PATCH /settings` accepts `appearance` (any subset of the keys, merged) and `base` (the stage-9 compare-and-set, per key → 409 `edit_conflict`). Writes ask `services/entitlements.py` (the seam for plan-gated features). A restore re-checks the object. |
| 3 | Web | In Оформление, group **«Устройства»**: the switch, stored on the device. ON: the account's appearance is applied when settings load (start, back online, visible again); changes are saved to the account after a short pause; the first enable with nothing on the account uploads this device's. OFF: the device neither reads nor writes the account's value. The preview stays instant (local first). RU + EN, 48 px target, calm copy. |
| 4 | Tests | `api/tests/test_stage10_appearance_sync.py` (validation, round trip, two users isolated, conflicts on a stale base, the plan gate, backup); `scripts/ui-checks/stage10-appearance-sync.mjs` (two browser contexts = two devices: ON syncs, OFF does not, offline, a conflict). |

## 2. Behaviour

**Local first, always.** `setAppearance()` (lib/appearance.ts) repaints and writes `localStorage` exactly as before; the
preview never waits for the network. The sync lives beside it in `web/src/lib/appearanceSync.ts` — a lazy chunk (the
start-up bundle only gains the `import()` that loads it after the first render; «Оформление» imports it from its own
chunk).

**The switch.** `dsos.appearance.sync` in `localStorage`: `off` turns it off, anything else (absent too) is on. It is the
device's own choice — turning it off here changes nothing on the other devices. The copy under it follows the state
(«Тема и вид сохраняются в аккаунте…» / «Это устройство хранит своё оформление…»), and a quiet status line shows
«Сохраняю в аккаунте…», «Нет связи — сохраню, когда она появится.» or, when the plan does not include the feature,
«Недоступно в вашем тарифе…» (§6).

**What a device remembers.** `dsos.appearance.base` = the account's appearance this device last saw (`null` = the
account had none; absent = never seen). "Changed on this device" always means *this device's value differs from `base`*.

**When the server's value is seen** (the session at start — it already carries `settings.appearance`, so there is no
extra request —, a refetched session when the device is back online or visible again after more than a minute, the
answer of this device's own write, a 409's `current`) it is merged **key by key**:

| Situation | Result |
| --- | --- |
| Account has none, this device has an appearance of its own (anything stored) | the whole of this device's is uploaded (`base: {appearance: null}`) |
| Account has none, this device is still the factory look (nothing stored) | nothing is uploaded — a fresh install never overwrites what another device is about to send |
| Account has one, this device has not seen it before (first start, or the switch just turned on) | **the account's wins**: it is applied here |
| Account has one, this device has seen one | per key: changed here since `base` → stays (and is sent); otherwise → follows the account |
| A key the account holds that this build does not know (a newer theme) | ignored here, never overwritten by an older build |

**Saving.** Changes are collected for 800 ms, then one `PATCH /settings` carries only the keys that changed and `base`
for exactly those keys, so two devices changing *different* keys merge. On a 409 `edit_conflict` the `current` value is
merged as above and the write is sent once more (at most three rounds): **this device's own choice wins on a key both
devices changed, every other key follows the account** — no dialog; a look is not worth interrupting someone for.

**Offline and failures.** Settings are not queued (like every other setting; the stage-9 queue and sync sheet are not
involved). A change that cannot be sent simply waits — the device's value differs from `base` — and goes out on `online`,
when the page is visible again, or on a timer with a back-off (15 s · 2ⁿ, ≤ 5 min). Nothing is lost, and nothing
appears in the sync sheet. A late answer never undoes a newer one (`updated_at` of the settings row is compared).

**Turning it on again.** `base` is forgotten, so the device is a first-time reader: the account's look (if any) is
applied here, otherwise this device's is uploaded. The hint under the switch says so when it is off.

**Turning it off.** The look stays as it is; nothing is read from or written to the account until it is turned on.

**What is synced.** All seven keys: `skin`, `mode` (`system` follows each device's OS), `ground`, `tilt`, `shadow`,
`large_text`, `reduce_motion`. (`reduce_motion` and `large_text` are accessibility settings a user may want per device
— that is what the switch is for.)

## 3. API contract

`GET /api/v1/settings` → `settings.appearance`: `null` (never synced) or the complete object (the session and pairing
answers carry the same `settings`):

```json
{ "skin": "stickers", "mode": "light", "ground": "mint", "tilt": false,
  "shadow": "soft", "large_text": false, "reduce_motion": false }
```

| Key | Values |
| --- | --- |
| `skin` | `evolution` · `stickers` (the themes shown as «Скоро» are not accepted until they ship) |
| `mode` | `light` · `dark` · `system` |
| `ground` · `shadow` | `lilac` `mint` `peach` `lemonade` · `hard` `soft` (the stickers theme's parameters) |
| `tilt` · `large_text` · `reduce_motion` | booleans (a string or a number is refused) |

`PATCH /api/v1/settings` takes, besides the existing fields:

- `appearance` — **any subset** of those keys (merged onto the stored object, or onto the defaults the first time, so
  the stored object is always complete); `null` puts the account back to "never synced". Unknown keys, unknown values
  and `null` as a key's value → 422 `validation_error`, nothing written (not even the other fields of the request).
- `base` — optional, **the stage-9 rules** (`docs/stage-9-offline.md` §3) per key: `{"appearance": {"ground": "lilac"}}`
  = the values the edit started from, for the keys it depends on; `{"appearance": null}` = "the account had none". Absent
  (or `base` without `appearance`) → last write wins. A listed key whose stored value differs *and is not already the
  value this request sets* → **409**, nothing is written:

```json
{ "error": { "code": "edit_conflict",
             "message": "Changed on another device since this edit started",
             "details": { "fields": ["appearance"],
                          "current": { "…": "the settings row exactly as GET /settings shows it", "appearance": { "…": "…" } } } } }
```

  A replay of a write that went through (its answer was lost) and the same choice made on two devices are not conflicts;
  keys not listed never conflict. The row is locked (`SELECT … FOR UPDATE`) while `appearance` or `base` is processed, so two
  devices saving at once are compared one after the other. `base` may also stand alone, as a precondition for the other
  fields of the request.
- Refused with **403 `feature_not_in_plan`** (`details.feature = "appearance_sync"`) when the user's plan lacks the feature
  (§6). Reads are never gated.

Storage details: the column is `JSONB NULL` with no default, so existing rows stay `NULL`; the export/backup carries it
with the rest of `user_settings` (a restore re-validates it: an object this app would not store is refused); a value
written by another build that does not validate is shown key by key with the defaults instead of failing `GET /settings`.

## 4. Files

```
api/alembic/versions/0005_appearance_sync.py      ★ user_settings.appearance (JSONB, nullable); the downgrade drops it
api/app/models/user.py                            UserSettings.appearance
api/app/schemas/appearance.py                     ★ the object: AppearanceOut · AppearanceIn (subset) · AppearanceBaseIn · defaults · merge
api/app/schemas/settings.py                       appearance + base on SettingsUpdateIn; appearance on UserSettingsOut
api/app/services/appearance.py                    ★ check_appearance_base (409 per key) · apply_appearance
api/app/services/entitlements.py                  ★ the plan gate seam: plan_allows · require_feature · FEATURE_APPEARANCE_SYNC
api/app/api/v1/settings.py                        PATCH: gate, lock, base check before any write, apply
api/app/services/restore.py                       a restored appearance is re-validated
api/tests/test_stage10_appearance_sync.py         ★ 41 tests
web/src/lib/appearanceSync.ts                     ★ the switch, base, merge, debounced push, retries (lazy chunk)
web/src/features/settings/ThemeSection.tsx        the «Устройства» group
web/src/main.tsx                                  loads appearanceSync after the first render
web/src/api/{types.ts,generated/schema.d.ts}      AccountAppearance; regenerated from the OpenAPI schema
web/src/i18n/locales/{ru,en}.json                 look.sync_* (device_note replaced)
scripts/ui-checks/stage10-appearance-sync.mjs     ★ two/three browsers = devices
scripts/ui-check.mjs                              the main browser starts with the switch off; the module is listed
docs/stage-10-appearance-sync.md · docs/roadmap.md
```

## 5. Commands and walkthrough

```bash
cd api && alembic upgrade head                                   # 0005
cd api && .venv/bin/pytest -q tests/test_stage10_appearance_sync.py
curl -s -X PATCH $API/api/v1/settings -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"appearance": {"skin": "stickers", "ground": "mint"}, "base": {"appearance": null}}'
```

By hand, with two phones (or two browsers):

1. Phone 1: Настройки → Оформление → Наклейки, ground «Мята». Scroll down: «Устройства» — «Одинаково на всех
   устройствах» is on.
2. Phone 2 (just paired, never customised): open the app — after a moment it looks like phone 1.
3. Phone 2: turn «Наклон наклеек» off. Phone 1: leave the app and come back (or reload) — the badges are straight.
4. Phone 2: turn the switch off, choose «Эволюция». Phone 1 does not change; phone 2 stays «Эволюция» after a restart.
5. Phone 2: turn the switch on — the look of the account (stickers) comes back.
6. Airplane mode, change the ground: the page changes at once; the status says «Нет связи — сохраню, когда она
   появится.»; back online, phone 1 gets it.

## 6. Toward multi-user (subscriptions and clients)

**Already per user.**

- The value is a column of that user's `user_settings` row; `PATCH`/`GET /settings` resolve the row from the
  authenticated principal (`get_or_create_settings(db, principal.user)`) — a client can name no other `user_id`
  (unknown fields are 422), and a `base` is only ever compared with the caller's own row. `test_two_users_are_isolated`
  pins it.
- Concurrency is per user: the row lock is on that user's settings row only.
- No global or singleton state on the server. In the browser, the switch is per device and `base` is stored with the
  `user_id` it was seen for: pairing another account on the same browser finds a `base` that is not its own and starts from
  "never seen" (the first-sight rule above) — one account's look never leaks into another's.
- The object is validated against a closed vocabulary (`schemas/appearance.py`), so a hosted service never stores
  free-form client JSON; `export`/`restore` treat it like any other settings column.

**Where a subscription gate hooks in.**

- `services/entitlements.py` is the one place that answers «does this user's plan include X?»:
  `plan_allows(user, feature)` is `True` for every user today. A real plan would be stored per user (a column on
  `users`/`user_settings`, or a `subscriptions` table keyed by `user_id`) and looked up there; **no endpoint changes**.
- `PATCH /settings` calls `require_feature(user, "appearance_sync")` only when the request writes `appearance`; the answer
  is `403 feature_not_in_plan` with `details.feature`. Reads are never gated, so a downgraded account still shows what it
  holds. The web client treats that 403 as «Недоступно в вашем тарифе. Оформление остаётся на этом устройстве.» and stops
  sending for the page load; the look keeps working locally (the per-device switch and the whole local store are
  unaffected).
- Other plan-gated looks follow the same way: e.g. themes shown as «Скоро» can become `feature = "theme:forge"` checked
  in `AppearanceIn`'s endpoint (the closed vocabulary is where a paid theme list would be filtered per plan), without
  changing the client protocol.
- Not built (on purpose): plans, billing, a features list in `GET /settings`, per-plan limits on devices
  (`users.max_devices` already exists).

## 7. Evidence (this build)

Commit before this file's last edit, as the worktree `stage-10-appearance-sync` (from internal `main` at `c1c7acd`). Run in this
container on 29 Oct 2026; PostgreSQL 16 (UTF-8, en_US), the API with the fake AI provider.

| Gate | Result |
| --- | --- |
| `alembic downgrade 0004` · `upgrade head` | the column goes and comes back; `alembic current` = `0005 (head)` |
| `ruff check` · `ruff format --check` | clean (183 files) |
| `pytest -q` (whole suite, DB `dsos_test_s10sync`) | **1 491 passed, 7 skipped, 0 failed** (7.5 min); 41 of them are the new `test_stage10_appearance_sync.py` |
| `npm run typecheck` · `npm run build` | exit 0 — main chunk **297.30 kB** (297.1 before; the only addition is the `import()` that loads the sync); `appearanceSync` is a 4.91 kB (gzip 2.14) chunk of its own |
| `node scripts/check-i18n.mjs` | complete and in sync — 1 997 keys in each locale |
| `node scripts/perf/bundle-report.mjs --check` | ok — main 297.4 kB ≤ 300 kB |
| whole `scripts/ui-check.mjs` (fresh DB `dsos_s10_sync_ui`, every module) | **730 ok, 0 failed** (exit 0; 646 before the stage-10 part-2 and this module) |
| `stage10-appearance-sync` (part of that run) | 35 checks: the switch (default on, 48 px, stored on the device, copy), a fresh device uploads nothing, preview instant, three taps = one write with `base`, the second device adopts at start and writes nothing, follows when back online without a reload, OFF neither writes nor reads (0 writes, own look kept across a restart), ON again takes the account's look, first enable with nothing on the account uploads this device's, offline → «Нет связи…» → sent when back, a stale base → 409 → merged (own key wins, the rest follows, two writes), a `base` of another account ignored, no console errors, the account reset to null |
| axe on Оформление (stage10-themes, stage9-a11y — both now include the «Устройства» group) | 0 serious/critical nodes, 48 px targets |
| `scripts/smoke.sh` | not run (no API change outside `/settings`; the whole pytest suite and the browser run cover it) |

Screenshots (390 × 844): `/home/claude/runint/s10sync/shots/s10-sync-on-evolution.png`, `s10-sync-off-evolution.png`,
`s10-sync-on-stickers.png`.

## 8. Decisions

- **The account's value wins on first sight.** A device that has not seen the account's appearance (first start, or the
  switch just turned on) applies it; it never uploads over it. That is what «Одинаково на всех устройствах» promises, and
  the hint under the switch says it plainly. A device with the switch off keeps its own.
- **A fresh install uploads nothing.** "This device has an appearance" means something is stored (`dsos.appearance` or
  `dsos.theme`). Without that rule the default-on switch would stamp the factory look on the account from the first
  device that happened to start, and a customised phone would then lose its look.
- **Three-way merge, not "last write wins" and not a dialog.** The server compares per key (`base`), the client merges per
  key; on a real conflict the device's own choice wins, because the user just made it. Settings are calm; the sync sheet
  and its conflict view (stage 9) stay for data.
- **Not through the offline queue.** No other setting goes through it (`PATCH /settings` is a direct call), the value is
  derived (device vs `base`), and a replayed queue entry could apply an old look after a newer one. The device's own
  retry is idempotent by construction.
- **snake_case on the wire** (`large_text`, `reduce_motion`) like the rest of the API; the client maps to its camelCase store.
- **Subset writes, complete reads.** Sending only changed keys is what makes different-key edits merge; storing a complete
  object keeps every reader simple.
- **`ui-check.mjs` starts its main browser with the switch off.** Earlier modules write the look to `localStorage` and
  expect it to stay on the device; the new module opens browsers of its own with the default (on).

## 9. Not in this stage

Plans, billing and the features list; syncing anything but the appearance; a per-device override of a single key; a
"copy this device to the account" button (turning the switch off and on again on an account with a look applies the
account's — to replace it, change a key); other themes (the vocabulary grows with them: a new skin id is one line in
`schemas/appearance.py` and `lib/appearance.ts`).
