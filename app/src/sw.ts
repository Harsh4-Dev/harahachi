/// <reference lib="webworker" />
// Custom service worker (injectManifest strategy). Keeps the workbox app-shell
// precache AND adds push + notificationclick handlers (API.md v2 reminders).
// The hanko icon is reused for the notification (DESIGN.md 6.6).

import { precacheAndRoute } from 'workbox-precaching';

declare let self: ServiceWorkerGlobalScope;

// Injected at build time by vite-plugin-pwa (injectManifest).
precacheAndRoute(self.__WB_MANIFEST);

// Activate immediately so push works without a reload.
self.addEventListener('install', () => {
  void self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

interface PushPayload {
  title?: string;
  body?: string;
  url?: string;
  tag?: string;
}

self.addEventListener('push', (event: PushEvent) => {
  let data: PushPayload = {};
  try {
    data = event.data ? (event.data.json() as PushPayload) : {};
  } catch {
    data = { body: event.data?.text() };
  }
  const title = data.title || 'Harahachi 腹八分';
  const options: NotificationOptions = {
    body: data.body || "A nudge from the road.",
    icon: '/pwa-192.png',
    badge: '/pwa-192.png',
    tag: data.tag || 'harahachi-reminder',
    data: { url: data.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event: NotificationEvent) => {
  event.notification.close();
  const target = (event.notification.data as { url?: string })?.url || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Focus an existing tab if the app is already open, else open a new one.
      for (const client of clients) {
        if ('focus' in client) return (client as WindowClient).focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});
