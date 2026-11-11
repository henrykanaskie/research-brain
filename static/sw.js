/* research brain service worker: makes the app installable and opens its shell quickly.
 *
 * It caches the app shell only: the page, scripts, styles, fonts and icons under /static/. It never caches
 * /api/, /pdf/, /attachments/, /pair or anything else: notes, PDFs and answers always come from your
 * computer, so nothing of your library is left on the phone, and revoking the phone (Library settings) or
 * stopping the app leaves no readable copy behind. When the computer can't be reached, the shell still
 * opens and says so.
 */
'use strict';
const VERSION = 'rb-shell-v1';
const SHELL = /^\/static\/(?!.*\.(pdf|map)$)[\w./-]+$/;   // static files only

self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function cacheable(url, req) {
  if (req.method !== 'GET' || url.origin !== self.location.origin) return false;
  return SHELL.test(url.pathname);
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (cacheable(url, req)) {
    // network first so updates arrive at once; the cached copy is only a fallback when offline
    e.respondWith(fetch(req).then((res) => {
      if (res.ok && res.type === 'basic') { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || Response.error())));
    return;
  }
  if (req.mode === 'navigate' && (url.pathname === '/' || url.pathname === '/share')) {
    // the page itself is never stored; offline, show a plain notice instead of the browser's error page
    e.respondWith(fetch(req).catch(() => new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
  }
  // everything else (/api/, /pdf/, /attachments/, ...) goes straight to the network, uncached
});

const OFFLINE = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>research brain</title><link rel="stylesheet" href="/static/vendor/fonts/fonts.css">
<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#EEEFEB;color:#15181B;font:16px/1.55 Archivo,sans-serif;padding:24px">
<main style="max-width:420px"><h1 style="font:italic 500 32px/1.1 'Bodoni Moda',Georgia,serif;margin:0 0 12px">Your computer isn't answering</h1>
<p style="color:#61676C;border-left:2px solid #BD3A2C;padding-left:14px">research brain runs on your computer. Check that it's awake, that the app is running, and that this phone is on Tailscale (or the same network). Then pull to refresh.</p></main></body>`;
