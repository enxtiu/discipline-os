# Stage 7 — Notifications · delivery report

> Web push to the iPhone home-screen app (and any desktop browser): task
> reminders from the planner, daily nudges for meals, water, the planned workout
> and the evening review — each only when it still makes sense at send time —
> per-category switches with their own times, quiet hours, a history of what was
> sent and why something was not, and an onboarding step that explains «На экран
> Домой» first, because iOS only allows push for installed web apps.
>
> - No migration: `push_subscriptions`, `notifications_log` and `reminders` were in 0001.
> - No dependency changes: `pywebpush`, `py-vapid`, `http-ece` and `cryptography` were
>   already pinned; the send itself goes out through the already pinned `httpx`.

## 1. Plan that was executed

| # | Spec item | Delivered |
| --- | --- | --- |
| 1 | Web push (VAPID, `pywebpush`) | `GET /push/config`, `POST/GET /push/subscriptions`, `DELETE /push/subscriptions/{id}`, `POST /push/unsubscribe`, `POST /push/test`. Subscriptions upsert by endpoint (re-subscribing refreshes keys and re-binds the device; another account's endpoint is refused), keys validated as a P-256 point + 16-byte secret. Messages are encrypted with `pywebpush` (aes128gcm, VAPID JWT per endpoint), TTL per message, Urgency, a Topic so a newer reminder replaces an older one. 2xx → success, 404/410 → the subscription is switched off, 429/5xx/timeouts → retried (3 attempts), other 4xx → permanent. `python -m app.cli vapid-keys` prints a key pair. |
| 2 | The in-process notification scheduler | `dispatch_notifications` (every minute) claims due reminders with `FOR UPDATE SKIP LOCKED`, leases them, sends **without** holding a database lock, then books the result; users are served side by side (4 at a time), each user's reminders in order. `plan_daily_reminders` (every 15 min) creates today's meal / water / workout / evening-review rows at your local times, idempotent by key, never in the past or in quiet hours. Both register through `services/hooks.py`. |
| 3 | Quiet hours | Overnight ranges, DST-safe. A task reminder that falls inside them is **deferred** to their end (and logged as such); a daily nudge is dropped. A push never outlives the next quiet-hours start (its TTL ends there), and the service worker drops a push that arrives after it expired. |
| 4 | Per-category prefs | `GET/PUT /notifications/prefs`: task reminders, meals (default 13:00, 19:30), water (12:00, 16:00, 20:00), workout (18:00), evening review (21:30), streak at risk (rule in stage 8); unknown legacy keys kept; a change re-plans today. Conditions are checked at send time: a meal reminder only when nothing was logged since the previous one, water only when today's water is below the pro-rata share of the target (the body says how much is left), the workout only when one is planned and not started, the review only when today's journal is empty. No message ever suggests eating less. |
| 5 | Settings screen | **Настройки → Уведомления**: a status card with the one action that fits (iPhone Safari tab → the install steps; blocked → how to re-allow in iOS Settings; off → «Включить уведомления»; on → «Отправить тестовое» and «Выключить на этом устройстве»; server without VAPID keys → an admin note), category switches (48 px) with their times, quiet hours, devices with push (remove a lost phone), the recent history with statuses and reasons. |
| 6 | iOS "add to home screen first" in onboarding | The wizard's last step shows the push card: on an iPhone Safari tab the install steps; once installed, an enable button; skippable. |
| — | Deep links and the service worker | `sw-push.js` is imported into the generated Workbox service worker; tapping a notification focuses the open app and navigates in place (`/plan?task=<id>` opens that task anywhere), a rotated subscription re-registers itself. Caddy serves `sw-push.js` uncached like `sw.js`. |

## 2. New & changed files

```
api/app/schemas/notifications.py        ★ subscription, prefs, history models; endpoint policy
api/app/services/push.py                ★ VAPID, encryption, send with a total deadline, booking
api/app/services/notifications.py       ★ dispatcher, daily planner, conditions, localised messages
api/app/api/v1/push.py · notifications.py   ★ /push/* and /notifications/* (device principal only)
api/app/api/v1/presenters.py · router.py    + stage-7 presenters and mounts
api/app/services/hooks.py               + dispatch_notifications, plan_daily_reminders
api/app/core/config.py                  + PUSH_EXTRA_HOSTS; db.py: hide_parameters=True
api/app/cli.py                          vapid-keys prints the browser's applicationServerKey
api/tests/test_stage7.py · test_stage7_hardening.py · stage7_helpers.py   ★ 109 tests
web/public/sw-push.js                   push, expiry, click → navigate, subscription change
web/vite.config.ts                      workbox.importScripts(['sw-push.js'])
web/src/lib/push.ts                     ★ support/permission/subscribe/unsubscribe/status
web/src/features/notifications/         ★ types, api, queries, PushStatusCard, useNotificationBridge
web/src/features/settings/NotificationsSection.tsx   rewritten: status, categories, times, devices, history
web/src/features/auth/OnboardingWizard.tsx           + the push step
web/src/components/layout/AppShell.tsx  + ?task= deep links and service-worker messages
web/src/i18n/locales/{ru,en}.json       +76 keys each (1 266 per locale)
docker/Caddyfile                        sw-push.js never cached
scripts/ui-checks/stage7-notify.mjs     ★ 48 browser checks with a local push receiver
scripts/smoke.sh                        + 16 web push end to end (encrypted, decrypted, VAPID checked)
docs/screenshots/40, 42, 43-*.png       ★ iPhone Safari tab, notifications on, onboarding
README.md                               VAPID setup, web push on iPhone, troubleshooting
```

## 3. Commands

```bash
# quality gates
cd api && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .
cd web && npm run typecheck && npm run build && grep -c sw-push.js dist/sw.js
node scripts/check-i18n.mjs
FAKE_CAMERA=/tmp/barcode.y4m PAIRING_CODE=XXXX-XXXX node scripts/ui-check.mjs
API_BASE=http://127.0.0.1:8000/api ./scripts/smoke.sh <PAIRING-CODE>

# once per server: VAPID keys into .env (keep them — a new pair invalidates every subscription)
docker compose exec api python -m app.cli vapid-keys
```

## 4. What to test (walkthrough)

1. On the iPhone in a Safari tab: **Настройки → Уведомления** says push needs the app on
   the home screen and shows the steps. Share → «На экран Домой», open the icon.
2. **Включить уведомления** → iOS asks → allow → «Отправить тестовое» → a notification
   arrives within seconds; tapping it opens the app.
3. **План** → a task due in 20 min with «напомнить за 15 мин» → the reminder arrives at
   the right minute with the task title and «Через 15 мин · HH:MM»; tapping it opens the task.
4. Set quiet hours to cover the next few minutes and add a reminder inside them → nothing
   arrives; the history says it was deferred, and it comes when the quiet hours end.
5. Meals at 13:00: log a meal before → no reminder; skip logging → «Запишите, что вы ели
   сегодня…» (about logging, never about eating less). Water at 16:00 below the pro-rata
   target → «Вода: осталось … мл до цели».
6. Turn a category off → its reminders stop (history: cancelled). «Выключить на этом
   устройстве» → the device disappears from the list.

## 5. Verification evidence (this build)

```
api      1035 passed (926 after stage 6 + 109 for stage 7, incl. a real aes128gcm round-trip
         decrypted with http_ece and a verified VAPID JWT) · ruff check clean · ruff format clean
web      tsc -b --noEmit clean · vite build + PWA 40 precache entries (1 585.6 KiB);
         dist/sw.js imports sw-push.js, dist/sw-push.js emitted; main chunk 498.1 kB
         (+22.1 kB: push client, notification bridge, onboarding step, strings)
i18n     1 266 keys per locale, complete & in sync
browser  266/266 checks green on a freshly created database (Chromium, 390×844, ru-RU),
         incl. 48 for stage 7 (a subscription to a local receiver, the test push decrypted
         with the generated keys and its VAPID header checked)
smoke    23/23 green (stages 1–7), incl. 16: a local receiver, browser keys from openssl,
         POST /push/test → one aes128gcm POST with a VAPID header, decrypted, unsubscribed
```

Reviews run on this branch:

- **Independent backend review** (agent, probes against the running API). Fixed:
  - **H1** the production endpoint check only refused private IP literals — a device
    could register `https://2130706433/`, compose service names or any DNS name, and a
    push service answering 307 could redirect the server anywhere (blind SSRF). Now,
    outside development, an endpoint must be https on port 443 at a known push service
    (FCM, Apple, Mozilla, Windows; more via `PUSH_EXTRA_HOSTS`), checked again before
    every send, and redirects are never followed;
  - **M1** one slow push service stalled every user's notifications (a per-read timeout,
    the reminder row locked during network I/O) → a 15 s total deadline per send, no
    lock held while sending, users served side by side, at most 10 subscriptions per user;
  - **M5** a 12-hour TTL let a reminder arrive at 03:00 with stale text → TTL until it
    goes stale and never past the next quiet hours; the service worker drops late pushes;
  - **L1** multi-byte endpoints broke the unique index (500) and failed inserts logged
    the endpoint and keys → ASCII, ≤ 2048 bytes; SQL parameters are never logged;
  - **L5** a subscription deleted mid-send rolled the reminder back and sent it again
    to the other device → results booked with tolerant per-row updates;
  - **L8** after a VAPID key change, rejected subscriptions stayed "active" forever →
    three 401/403 in a row switch it off (`vapid_rejected`) and Settings asks to re-enable.
- **iOS specifics** checked against WebKit's rules: permission only from a tap, push only
  for installed web apps, a push must show a notification (late pushes are the documented
  exception — see `sw-push.js`).
- **Playwright**: `docs/screenshots/40-notifications-iphone-safari.png`,
  `42-notifications-on.png`, `43-onboarding-push-iphone.png`.

## 6. Decisions worth knowing

- **Conditions at send time, not at planning time.** A reminder row exists for every
  configured time; whether it is sent is decided when it is due (logged meal, water
  share, planned workout, empty journal) — nobody gets a pointless nudge.
- **Task reminders are deferred, nudges are dropped** inside quiet hours: a task
  reminder is a commitment, a nudge is not worth waking someone for.
- **Lease, send, book.** The dispatcher never holds a row lock across the network; a
  run that dies mid-send leaves a lease that lapses, so a reminder is sent at most once
  by a healthy system and never blocks an edit of its task.
- **One log row per reminder** (`notifications_log` unique per user + key): the history
  shows what was sent, deferred, suppressed or cancelled and why.
- **Keys are server state.** Rotating VAPID keys invalidates every subscription; the
  server detects it (401/403 three times) and the app asks to re-enable.
- **Endpoint allowlist** instead of a blocklist: only real push services, https, 443.

## 7. Not in stage 7 (by design)

- **Streak-at-risk reminders**: the category and hook exist; stage 8 adds the rule
  (it depends on the streaks of stage 8).
- **Email/SMS/Telegram channels** — out of scope; web push only.
- **Rich notifications** (images, action buttons): iOS web push does not support action
  buttons; tapping opens the right screen instead.
