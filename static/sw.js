// Billdable's service worker: it only makes the app installable on a phone (so photos and
// PDFs can be shared to it). Every request goes to the network; nothing is cached, so the
// app never shows stale money figures or works on data it can't save.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => event.respondWith(fetch(event.request)));
