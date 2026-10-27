/**
 * Web Push handlers (stage 7), imported into the generated Workbox service
 * worker: `workbox.importScripts` in vite.config.ts makes the built
 * `dist/sw.js` start with `importScripts("sw-push.js")`.
 *
 * Payload sent by the API (app/services/push.py):
 *   { title, body, url, tag, renotify, kind, id }   — ≤ 3 KB, aes128gcm-encrypted
 *
 * Keep this file small and dependency-free: it runs on every push, often while
 * the app itself is closed.
 */
/* eslint-disable no-undef */
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
