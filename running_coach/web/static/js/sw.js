// Service Worker — handles push notifications
// This file must be served from the root of the app

self.addEventListener('push', function(event) {
  if (!event.data) return;

  const data = event.data.json();
  const title   = data.title   || 'Running Coach';
  const options = {
    body:    data.body    || 'Time to check your training.',
    icon:    data.icon    || '/static/icon-192.png',
    badge:   data.badge   || '/static/icon-192.png',
    tag:     data.tag     || 'daily-summary',
    data:    { url: data.url || '/' },
    actions: [
      { action: 'open',    title: 'Open app' },
      { action: 'dismiss', title: 'Dismiss'  },
    ],
    requireInteraction: false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  if (event.action === 'dismiss') return;

  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(list) {
      // If app is already open, focus it
      for (const client of list) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      // Otherwise open a new window
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});

self.addEventListener('install',  () => self.skipWaiting());
self.addEventListener('activate', () => clients.claim());
