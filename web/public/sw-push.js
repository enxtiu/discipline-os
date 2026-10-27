/**
 * Web Push handlers (stage 7), imported into the generated Workbox service
 * worker: `workbox.importScripts` in vite.config.ts makes the built
 * `dist/sw.js` start with `importScripts("sw-push.js")`.
 *
 * Payload sent by the API (app/services/push.py):
 *   { title, body, url, tag, renotify, kind, id, sent_at, expires_at }
 *   — ≤ 3 KB, aes128gcm-encrypted; `sent_at` / `expires_at` are ISO 8601 UTC.
 *
 * Expiry: the API gives every reminder a TTL that ends before the reminder goes
 * stale and before the user's next quiet hours, and the push service drops it
 * after that. A push that still arrives after `expires_at` (a late delivery) is
 * dropped here: no notification. CLOCK_GRACE_MS absorbs a phone clock that is a
 * few minutes off. The test notification has no `expires_at`: always shown.
 *
 * iOS / Safari: WebKit removes a push subscription after three pushes that show
 * no notification, and a dropped push counts as one. That is accepted: it only
 * happens to pushes the push service should not have delivered at all (or on a
 * phone whose clock is off by more than the grace). If WebKit does remove the
 * subscription, the next push gets 410, the API switches the subscription off
 * and Settings → Уведомления asks to turn notifications on again on that
 * device. Showing a neutral "missed reminder" instead was rejected: it would
 * still buzz inside the quiet hours, which is what the expiry is for.
 *
 * Keep this file small and dependency-free: it runs on every push, often while
 * the app itself is closed.
 */
/* eslint-disable no-undef */
const CLOCK_GRACE_MS = 15 * 60 * 1000

/** Has this push outlived its `expires_at` (beyond the clock grace)? No or bad value: no. */
function isExpired(payload, now) {
  if (typeof payload.expires_at !== 'string') return false
  const expiresAt = Date.parse(payload.expires_at)
  return Number.isFinite(expiresAt) && now > expiresAt + CLOCK_GRACE_MS
}

self.addEventListener('push', (event) => {
  let payload = {
    title: 'DisciplineOS',
    body: '',
    url: '/today',
    tag: 'dsos',
    renotify: false,
  }

  try {
    if (event.data) {
      const parsed = event.data.json()
      payload = { ...payload, ...parsed }
    }
  } catch (error) {
    payload.body = event.data ? event.data.text() : ''
  }

  if (isExpired(payload, Date.now())) {
    // too late to be useful (see the header): show nothing, but still settle the event
    event.waitUntil(Promise.resolve())
    return
  }

  const tag = typeof payload.tag === 'string' && payload.tag ? payload.tag : 'dsos'
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      // a newer reminder with the same tag replaces the older one on the device…
      tag,
      // …and still alerts (renotify needs a tag, or showNotification throws)
      renotify: Boolean(payload.renotify),
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      data: { url: payload.url, kind: payload.kind || null, id: payload.id || null },
      vibrate: [80, 40, 80],
      // iOS ignores most of these, but they are correct everywhere else:
      silent: false,
      requireInteraction: false,
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const origin = self.location.origin
  const raw = (event.notification.data && event.notification.data.url) || '/today'
  let target
  try {
    target = new URL(raw, origin)
  } catch (error) {
    target = new URL('/today', origin)
  }
  // only ever open our own app
  if (target.origin !== origin) target = new URL('/today', origin)
  const path = target.pathname + target.search + target.hash

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      const client = clientList.find((candidate) => candidate.url.startsWith(origin))
      if (client) {
        // an open window navigates in place (react-router, see useNotificationBridge):
        // no reload, nothing typed is lost
        client.postMessage({ type: 'dsos:navigate', url: path })
        return 'focus' in client ? client.focus() : undefined
      }
      return self.clients.openWindow(target.href)
    }),
  )
})

self.addEventListener('pushsubscriptionchange', (event) => {
  // The browser rotated the subscription. The service worker has no device token,
  // so it asks an open page to register the new one with the API.
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      clients.forEach((client) => client.postMessage({ type: 'push-subscription-changed' }))
    }),
  )
})
