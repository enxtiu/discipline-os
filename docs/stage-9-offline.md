# Stage 9 · offline sync polish and conflict surfacing — workstream report

> The offline queue of stage 1 never loses a write any more: a write the server
> refuses is kept with the server's reason, a write that met a newer change from
> another device is kept as a conflict with the server's current row, a failing
> server is retried with backoff (and `Retry-After`), and a write never overtakes
> the one it depends on. A tap on the header pill opens the sync sheet: what
> waits, what failed and why, what changed elsewhere — «Повторить», «Удалить»,
> «Оставить их версию», «Сохранить мою». Meals and water can be logged without
> signal and show at once; queued task edits show at once everywhere. Task,
> journal, project, area, resource and time-entry edits carry `base` (the values
> they started from) and answer **409 `edit_conflict`** instead of silently
> overwriting a change made on another device.
>
> - No migration, no dependency change. One additive backend change outside the
>   planner: a meal / water log can be deleted by the client id it was logged with.
> - The coordinator merges this part into `docs/stage-9.md`.

## 1. What was built

| # | Brief item | Delivered |
| --- | --- | --- |
| 1 | Queue engine | `lib/offlineQueue.ts`: entries get a status — `pending` → sent (removed) · `failed` (HTTP status, code, message kept) · `conflict` (the server's current row kept). Dexie schema v2 with an upgrade that marks stage-1 rows `pending`. Replay oldest-first; the first transient failure (network, 5xx, 408, 429, retryable 409) stops it and schedules a retry: 2 s · 2ⁿ, ±20 % jitter, ≤ 5 min, a `Retry-After` wins. Replays also start on `online`, on the page becoming visible, after any answered request (only when the wait was about the network; never before a `Retry-After`) and from the sheet. 401 keeps every entry and stops until a new device token is stored. A DELETE answered 404 is done. Dependencies: see §4. Per-entry `discardEntry`, `retryEntry`, `resolveConflict(id, 'mine' \| 'theirs')`, `sendAllNow`, `withdraw` (take back an unsent write). `onQueueChange` keeps its signature; `onQueueState` adds counts by status, online, sending, next retry, auth; `onQueueSent` tells views a write left. Autosaves and repeated task edits coalesce (`merge: true`) while they wait. A write sent online whose entity still has an earlier write waiting is queued behind it (order kept) and returns the server's answer when the replay gets it through. |
| 2 | Sync sheet | `features/sync/SyncSheet.tsx` behind the header pill (`components/layout/SyncStatus.tsx`, now a 48 × 48 target). Status line (online / offline / sending / «следующая попытка через 30 с» / device must be paired again), conflicts with «Открыть», failed writes with the server's reason in plain words and «Повторить» / «Удалить», waiting writes grouped by kind («Приём пищи», «Вода», «Задача», «Подход», «Дневник», …) with when they were made and «ждёт: сначала решите…» for writes stuck behind a failed one; «Отправить всё сейчас». Reads IndexedDB only (works offline), rendered at the root (the header's backdrop blur would trap a fixed sheet), reduced motion respected. The pill counts waiting writes and shows a dot for failed/conflicting ones. |
| 3 | Nutrition offline | `useLogMeal`, `useDeleteMeal`, `useAddWater`, new `useDeleteWater` go through `mutate()` with the client ids and the tap instant as `logged_at` (a meal eaten at 23:50 and sent after midnight lands on its day). `useDayView` (Today, Food) = the server's day + waiting writes (`features/food/pendingDay.ts`): the meal appears in its slot marked «ждёт отправки», totals = the server's totals ± the row's own kcal/macros as the log sheet (or AI review) showed them, water moves likewise; the server's answer replaces the rows after the refetch, a sent write keeps showing until that refetch lands. A meal deleted before it was sent is withdrawn from the queue; +250 ml has «Отменить». Scores and streaks never see these numbers (server-side). |
| 4 | Queued planner edits shown at once | `features/plan/pending.ts`: every pending PATCH / complete / reopen of a task is patched into every cached copy (`patchTaskInCaches`, which now takes a function patch and leaves queries without the task alone), re-applied whenever a planner query brings server data while the edit still waits; rows say «ждёт отправки» (one shared read of the queue per change, not one per row). Mounted once in `TaskSheetHost`. |
| 5 | Conflict surfacing | Backend: `base` on `PATCH /planner/tasks/{id}`, `PUT /planner/journal/{date}`, `PATCH /planner/projects|areas|resources|time-entries/{id}` (contract in §3). Frontend: the task editor remembers the server value of each field when it was first touched and sends it as `base`; the journal autosave sends `base` for the fields it changes. An online 409 opens an inline «Изменено на другом устройстве» with their value and yours and «Оставить их версию» / «Сохранить мою»; a queued 409 lands in the sync sheet as a conflict and «Открыть» shows the same choice; the journal also shows it inline when its queued write met the conflict. A queued edit of a row deleted elsewhere (404) says «Удалено на другом устройстве — правка не применена». |
| 6 | Tests | `api/tests/test_stage9_conflicts.py` (24 tests) and `scripts/ui-checks/stage9-offline.mjs` (in the `ui-check.mjs` module list), see §6. |

## 2. Files

```
api/app/schemas/planner.py                   + BaseTime, TaskBaseIn, AreaBaseIn, ProjectBaseIn, ResourceBaseIn,
                                               TimeEntryBaseIn, JournalBaseIn; `base` on the six edit models
api/app/services/planner/conflicts.py        ★ normalisers, "what the row is" per entity, check_base → 409
api/app/api/v1/planner.py                    check_task_base (lock order kept), EDIT_CONFLICT response doc
api/app/api/v1/planner_org.py · planner_time.py   the check on areas, projects, resources, time entries, journal
api/app/services/planner/journal.py          get_entry(lock=…)
api/app/services/nutrition.py                a meal / water log is found by its id or its client id
api/tests/test_stage9_conflicts.py           ★ 24 tests
web/src/lib/offlineQueue.ts                  statuses, Dexie v2, backoff, dependencies, coalescing, decisions
web/src/api/client.ts                        ApiError.retryAfterMs (Retry-After), onApiSuccess
web/src/features/sync/                       ★ SyncSheet, ConflictView, labels (kinds, reasons, values), hooks
web/src/components/layout/SyncStatus.tsx     the pill opens the sheet (own chunk, prefetched when idle and
                                               online), counts, attention dot, 48 px
web/src/features/food/pendingDay.ts          ★ optimistic day (waiting meals/water)
web/src/features/food/useNutrition.ts        writes through the queue, useDayView, useDeleteWater
web/src/features/food/{FoodPage,LogSheet}.tsx · features/ai/AiReviewSheet.tsx · features/today/TodayPage.tsx
                                             previews, «ждёт отправки», withdraw on delete, water undo
web/src/features/plan/pending.ts             ★ queued task edits in every cache, «ждёт отправки» ids
web/src/features/plan/queries.ts             patchTaskInCaches(function patch), merge/meta on writes
web/src/features/plan/tasks/{draft.ts,TaskSheet.tsx}   origin per field → base; inline conflict; 404 message
web/src/features/plan/tasks/{TaskRow,TaskSheetHost,useTaskActions}.tsx   mark, overlay mount, titles for the sheet
web/src/features/plan/journal/{useJournalAutosave.ts,JournalScreen.tsx}  base, coalescing, conflicts held + inline
web/src/features/plan/types.ts               TaskBaseIn, JournalIn.base, EditConflictDetails
web/src/api/generated/schema.d.ts            regenerated
web/src/i18n/locales/{ru,en}.json            + `sync` section (117 keys each), errors.edit_conflict
scripts/ui-checks/stage9-offline.mjs         ★ browser checks
scripts/ui-check.mjs                         + the module; a 5xx provoked inside allowErrors is not an app error
scripts/ui-checks/stage6-plan.mjs            the editor's PATCH now also carries `base` (checked, not refused)
docs/screenshots/9o-*.png                    sync sheet offline, Today offline, plan offline edit, conflicts, failed
```

## 3. Contract: `base` and 409 `edit_conflict`

Applies to `PATCH /api/v1/planner/tasks/{id}`, `PUT /api/v1/planner/journal/{date}`,
`PATCH /api/v1/planner/projects/{id}`, `…/areas/{id}`, `…/resources/{id}`, `…/time-entries/{id}`.

```json
PATCH /api/v1/planner/tasks/{id}
{ "title": "Купить молоко 2 л", "priority": 2,
  "base": { "title": "Купить молоко", "priority": 4 } }
```

- `base` is optional. **Absent (or `null`) = the stage-6 behaviour, last write wins** — API
  clients and writes queued by older builds keep working.
- `base` holds the values the edit **started from**, under the request's field names (the
  same names and shapes as the body; for a task `project_id`, `block: {date, start, end}`,
  `tags: [...]`, `reminders: [...]`; for a time entry `date`, `start`, `end`). Any field of
  the request may be listed, also one this request does not change (a precondition). Unknown
  keys and malformed values answer 422 `validation_error`.
- Before anything is written the row is locked and shown as `GET` would show it; every field
  listed in `base` is compared with that value, both normalised the same way:
  text without surrounding whitespace, `""` = `null`, CRLF = LF, Unicode NFC (Markdown
  included); times as `HH:MM` on your wall clock (`9:00` = `09:00` = `09:00:00`; a task's
  `due_time` and a time entry's `start`/`end` are compared in your timezone, not UTC); dates
  as `YYYY-MM-DD`; tags as a set — order, case and a leading `#` do not count; reminders as
  a set of definitions (the pending ones); a recurrence preset key (`weekly`) as its RRULE;
  a task `status` of `scheduled` as `open` (it is derived from the block); `@` in a context
  and the case of a colour do not count; ids as UUIDs.
- A listed field whose stored value differs **and is not already the value this request
  sets** is a conflict. So a replay of an edit that went through (its response was lost) or
  the same change made on two devices is not a conflict, and fields not listed never
  conflict — two devices editing different fields of a row merge.
- Any conflict → **409**, nothing is written (not even the fields that matched, not a
  status change, no next repeat):

```json
{ "error": { "code": "edit_conflict",
             "message": "Changed on another device since this edit started",
             "details": { "fields": ["title"],
                          "current": { "id": "…", "title": "Купить молоко и хлеб", "…": "…" } } } }
```

  `current` is the row as the endpoint answers it: `TaskDetailOut` (exactly `GET
  /tasks/{id}`), `JournalOut` or `null` when the day has no entry (deleted on another
  device: every field then counts as empty), `ProjectOut`, `AreaOut`, `ResourceOut`,
  `TimeEntryOut` — the entity object, not the detail wrappers (`ProjectDetailOut`,
  `AreaDetailOut`) the GETs of projects and areas return.
- Scoping first: a row of another user (or none) answers 404, never a 409 that would show it.
- To overwrite after a conflict («Сохранить мою»), send the same edit without those fields
  in `base`. To keep the other version («Оставить их версию»), leave those fields out.
- Lock order is unchanged: a task edit that moves the task under a parent takes the
  per-user tree lock before the row lock; a time entry takes the timer lock first; journal
  writes with `base` are serialised per user and day.

Also: `DELETE /api/v1/nutrition/meals/{id}` and `DELETE /api/v1/nutrition/water/{id}` accept
the client id the row was logged with as well as its id (the id wins if both match) — a meal
logged offline is deleted by the id the phone knows, even if its queued log reached the
server first.

## 4. Decisions

- **Nothing is dropped silently.** The stage-1 queue dropped every 4xx; now a refusal stays
  as `failed` (401 excepted: auth, entries kept) and a 409 `edit_conflict` as `conflict`. A
  409 that is not `edit_conflict` (`name_taken`, `id_conflict`) has no current row to show:
  it is `failed` with its reason; a retryable 409 (a deadlock the server broke) is transient.
  A DELETE answered 404 counts as done — the row is gone either way.
- **Dependency rule** (documented in `offlineQueue.ts`): a write waits behind an earlier
  unsent one when their entity paths are prefixes of each other (a create counts as
  `<collection>/<its id>`, so `/tasks/X`, `/tasks/X/complete` and the create of X are one
  entity) or when its path or body names the earlier write's entity id (`parent_id`,
  `task_id`, `/meals/<id>`, …). Over-cautious on purpose: something that names a failed
  write waits until the user decides about that write.
- **Transient failures stop the whole replay** (the server or the network is in trouble —
  sending more only adds load); refusals and conflicts do not (the rest is independent
  unless the rule above says otherwise).
- **Automatic triggers respect `Retry-After`.** `online`, visibility and an answered request
  replay at once, except before a server-imposed `Retry-After` deadline; an answered request
  only cuts a backoff that was about the network (a 5xx elsewhere is not cured by a GET that
  worked). «Отправить всё сейчас» is the user's call and always goes now.
- **Coalescing** only into the newest queued write of the same method and path while it is
  pending and not on its way (claims and merges are Dexie transactions), so the order
  relative to other writes never changes. A merged field keeps the `base` it was first queued
  with; a field the older write set without `base` gets none.
- **Keep theirs / keep mine are field-level.** «Оставить их версию» drops only the
  conflicting fields from the write (and from later waiting writes of the same row, which
  were built on the value given up) and sends the rest; «Сохранить мою» sends the write again
  without `base` for those fields.
- **The editor's `base` is the value a field had when it was first touched**, not the
  latest refetch: a refetch that brings the other device's change while you type must not
  turn your save into a silent overwrite.
- **Optimistic nutrition adds, never recomputes.** The day's totals stay the server's ± the
  waiting rows' own numbers (as the log sheet showed them); targets and day boundaries are
  never computed on the client. The optimistic row's day is the day view it was logged on;
  `logged_at` is the tap instant and the server decides the real day.
- **copy-meals stays online-only**: `POST /nutrition/meals/copy` creates rows without client
  ids, so a replay would duplicate them; making it idempotent is an API change of its own.
- Queued *creates* of tasks still appear once sent (as in stage 6); the brief's optimistic
  scope is edits of existing tasks (save, complete, move, reschedule).

## 5. Commands

```bash
cd api && TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/dsos_test_s9off .venv/bin/pytest -q
cd api && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build
node scripts/check-i18n.mjs
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs   # includes stage9-offline
cd web && API_URL=http://127.0.0.1:8106 npm run gen:api
```

## 6. Walkthrough (what to try)

1. Airplane mode on the phone. **Питание** → a recent food → «Записать в дневник»: the toast
   says it will be saved when the network is back; the meal is in its slot on Food and Today
   marked «ждёт отправки», the ring and «Съедено» moved by its kcal. «+250 мл» moves water.
   The pill shows `2`; tap it → «Ждут отправки»: «Приём пищи · добавление — Гречка · 165 ккал,
   только что», «Вода». Delete the meal → it was never sent («Запись удалена, пока не ушла
   на сервер»).
2. Network back: the pill empties within seconds; the rows lose «ждёт отправки»; the server's
   totals are what was shown.
3. Offline, rename a task and tick another: both show at once with «ждёт отправки».
4. Rename a task offline on the phone and differently on the laptop. Network back on the
   phone: the pill gets a red dot; the sheet lists «Изменено на другом устройстве» →
   «Открыть» → «Там: …» / «У вас: …» → «Сохранить мою» or «Оставить их версию».
5. With both online, open the same task on both, save the laptop first, then the phone: the
   editor shows the choice inline. Same for the journal: type on the phone after the laptop
   wrote the day — the journal says «Изменено на другом устройстве».
6. Edit a task offline that the laptop deletes: the sheet says «Удалено на другом устройстве
   — правка не применена» with «Удалить».

## 7. Verification evidence (this build)

```
api      1178 passed (dsos_test_s9off; 24 of them stage-9 conflict tests) · ruff check clean ·
         ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 44 precache entries (1 697.1 KiB);
         SyncSheet is its own 6.6 kB chunk (fetched while idle and online)
i18n     complete & in sync (+117 `sync` keys per locale)
browser  whole scripts/ui-check.mjs on a fresh database: 338 ok — the stage-9 offline module
         green; three earlier-stage checks failed on a saturated machine (load ≈ 10) because
         of fixed pauses (the wizard's save, the live workout's rows, the offline steps
         toast) — made to wait for what they read on the integration branch and re-run there
```

## 8. Not in this part (by design)

- copy-meals offline (see §4), optimistic display of tasks *created* offline, `base` on the
  calendar drag / inbox clarify / bulk moves (last write wins there, as before), conflict
  checks on tags, recipes, workouts and health days (single-device data, PUT by day).
- Cross-tab coordination of the queue: one tab replays at a time in practice; two tabs of
  the PWA replaying the same IndexedDB could both send an entry (idempotent ids make that
  harmless).
