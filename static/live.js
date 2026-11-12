/* research_brain — live updates.
 * Subscribes to GET /api/events (Server-Sent Events). When note files change outside the app
 * (Obsidian, an editor, a sync), the server sends `notes-changed` {ids}; we refresh the graph and
 * reload the open note if it changed and has no unsaved edits. The app's own saves are filtered
 * out on the server, so typing in the panel never triggers a reload of what you just wrote.
 * Loaded after app.js; uses window.__rb (S, refreshGraph, openNote).
 */
(() => {
  'use strict';
  if (!window.EventSource) return;
  const rb = () => window.__rb || null;
  let es = null;
  let retryMs = 2000;
  let refreshT = null;
  const pending = new Set();

  function hasUnsaved(S) {
    if (!S) return true;
    if (S.saveTimer || S.saving || (S.patch && Object.keys(S.patch).length)) return true;
    return false;
  }

  function flush() {
    refreshT = null;
    const app = rb();
    if (!app) return;
    const ids = new Set(pending);
    pending.clear();
    app.refreshGraph();
    const S = app.S;
    const cur = S && S.cur;
    if (!cur || !ids.has(cur.id)) return;
    if (hasUnsaved(S)) {
      // keep the user's typing; try again shortly in case the save is about to land
      ids.forEach((i) => pending.add(i));
      refreshT = setTimeout(flush, 1500);
      return;
    }
    const panel = document.getElementById('panel');
    const pbody = document.getElementById('panel-body');
    const top = pbody ? pbody.scrollTop : 0;
    const focused = document.activeElement && panel && panel.contains(document.activeElement);
    if (focused && /^(TEXTAREA|INPUT)$/.test(document.activeElement.tagName)) {
      // the user is in a field (maybe about to type): don't yank it away, retry when they leave it
      ids.forEach((i) => pending.add(i));
      document.activeElement.addEventListener('blur', () => { if (!refreshT) refreshT = setTimeout(flush, 200); }, { once: true });
      return;
    }
    Promise.resolve(app.openNote(cur.id)).then(() => { if (pbody) pbody.scrollTop = top; }).catch(() => {});
  }

  function connect() {
    try { es = new EventSource('/api/events'); } catch { return; }
    es.addEventListener('hello', () => { retryMs = 2000; });
    es.addEventListener('server-stopped', () => stopped());
    es.addEventListener('notes-changed', (e) => {
      let data = null;
      try { data = JSON.parse(e.data); } catch { return; }
      (data.ids || []).forEach((i) => pending.add(i));
      clearTimeout(refreshT);
      refreshT = setTimeout(flush, 150);
    });
    es.onerror = () => {
      // the browser retries on its own while CONNECTING; if it gave up (CLOSED), reconnect with backoff
      if (es && es.readyState === EventSource.CLOSED) {
        es = null;
        setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, 30000);
      }
    };
  }

  // research brain stopped (Quit on the Dock icon, the menu, or no window for a few minutes): the window the
  // app opened (?app=1) closes itself, so no dead window is left behind; any other tab says what happened
  try {
    if (new URLSearchParams(location.search).get('app') === '1') {
      sessionStorage.setItem('rb.appWindow', '1');
      history.replaceState(null, '', location.pathname + location.hash);
    }
  } catch { /* storage unavailable */ }
  function stopped() {
    if (es) { es.close(); es = null; }
    let app = false;
    try { app = sessionStorage.getItem('rb.appWindow') === '1'; } catch { /* storage unavailable */ }
    if (app) window.close();
    setTimeout(() => {  // still here: not the app's window, or the browser kept it open
      if (document.getElementById('rb-stopped')) return;
      const d = document.createElement('div');
      d.id = 'rb-stopped'; d.className = 'rb-stopped'; d.setAttribute('role', 'status');
      d.innerHTML = '<div><b>research brain has stopped.</b> Open it again from its icon, or run <code>research-brain open</code>.</div>';
      document.body.appendChild(d);
    }, 400);
  }
  window.__rbStopped = stopped;

  window.addEventListener('beforeunload', () => { if (es) es.close(); });
  connect();
  window.__rbLive = { get source() { return es; }, flush };
})();
