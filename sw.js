// Service worker بسيط علشان التطبيق يتثبت (PWA). مفيش كاش للـ API.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {});
