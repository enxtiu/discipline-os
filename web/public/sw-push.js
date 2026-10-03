/**
 * Web Push handlers, imported into the generated Workbox service worker
 * (see `importScripts` handling in vite.config.ts / the built sw.js).
 *
 * Stage 1 ships the handlers so the plumbing is testable end to end
 * (`curl` a push via the backend once stage 7 lands); the notification
 * scheduler itself is stage 7.
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

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      tag: payload.tag,
      renotify: payload.renotify,
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      data: { url: payload.url },
      vibrate: [80, 40, 80],
      // iOS ignores most of these, but they are correct everywhere else:
      silent: false,
      requireInteraction: false,
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/today'

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          client.navigate(target)
          return client.focus()
        }
      }
      return self.clients.openWindow(target)
    }),
  )
})

self.addEventListener('pushsubscriptionchange', (event) => {
  // The browser rotated the subscription; stage 7 re-registers it with the API.
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      clients.forEach((client) => client.postMessage({ type: 'push-subscription-changed' }))
    }),
  )
})
