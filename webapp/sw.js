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

// ---------------------------------------------------------------------------
// Phase 6 item 6: real Web Push. This is the actual point of a service worker beyond just
// satisfying installability — `push` fires here even if every tab is closed and the browser
// itself isn't the foreground app, which is what makes a phone notification possible at all.
// The payload shape (title/body/category/tag/conversation_id) is whatever
// push_notifications.py's send_to_device() sent as JSON — see that module for the category
// list and quiet-hours gating; by the time a push reaches this handler it's already been
// decided worth showing.
// ---------------------------------------------------------------------------

self.addEventListener("push", (event) => {
  let payload = { title: "Jarvis", body: "" };
  try {
    if (event.data) payload = { ...payload, ...event.data.json() };
  } catch (e) {
    payload.body = event.data ? event.data.text() : "";
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "Jarvis", {
      body: payload.body || "",
      icon: "/icon.svg",
      badge: "/icon.svg",
      tag: payload.tag || "jarvis",
      data: { conversation_id: payload.conversation_id || null },
    })
  );
});

// Tap-to-deep-link: focus an already-open Jarvis tab and hand it the conversation id via
// postMessage if one exists, otherwise open a fresh tab straight into that conversation via
// a URL query param (app.js reads it on load — see boot()).
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const conversationId = event.notification.data && event.notification.data.conversation_id;
  event.waitUntil(
    (async () => {
      const clientsList = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of clientsList) {
        if ("focus" in client) {
          await client.focus();
          if (conversationId) client.postMessage({ type: "open_conversation", conversation_id: conversationId });
          return;
        }
      }
      const url = conversationId ? `/?conv=${encodeURIComponent(conversationId)}` : "/";
      if (self.clients.openWindow) await self.clients.openWindow(url);
    })()
  );
});
