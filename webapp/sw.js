// sw.js — minimal service worker. Its only real job is satisfying browser installability
// criteria (Chrome requires a registered SW with a fetch handler before it'll offer "Install
// app") so the GUI can be added to a phone/desktop home screen. Deliberately NOT doing
// offline caching of API/websocket traffic — this app is meaningless without a live
// connection to the server, there's no "offline mode" worth pretending to support. Static
// assets just pass straight through to the network.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
