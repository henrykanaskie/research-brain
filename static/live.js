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
    // a Find papers search finished (app/finder_api.py): static/finder.js tells you to come back
    es.addEventListener('finder-done', (e) => {
      let data = null;
      try { data = JSON.parse(e.data); } catch { return; }
      window.dispatchEvent(new CustomEvent('rb:finder-done', { detail: data }));
    });
    // a novelty check finished (app/novelty_api.py): static/novelty.js says so
    es.addEventListener('novelty-done', (e) => {
      let data = null;
      try { data = JSON.parse(e.data); } catch { return; }
      window.dispatchEvent(new CustomEvent('rb:novelty-done', { detail: data }));
    });
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
  // ?app=mac: research brain's own Mac window (app/macwindow.py), no title bar
  // ?app=win: research brain's own Windows window (app/winwindow.py); with &titlebar=custom it has no title
  // bar (the header is one, with the window buttons at its right end), else Windows' own title bar
  let appKind = '', winTitlebar = '';
  try {
    const params = new URLSearchParams(location.search);
    const q = params.get('app');
    if (q === '1' || q === 'mac' || q === 'win') {
      sessionStorage.setItem('rb.appWindow', q);
      if (q === 'win') sessionStorage.setItem('rb.winTitlebar', params.get('titlebar') === 'custom' ? 'custom' : '');
      history.replaceState(null, '', location.pathname + location.hash);
    }
    appKind = sessionStorage.getItem('rb.appWindow') || '';
    if (appKind === 'win') winTitlebar = sessionStorage.getItem('rb.winTitlebar') || '';
  } catch { /* storage unavailable */ }
  // research brain's own channel to its window: the Mac window (app/macwindow.py, WebKit) or the Windows
  // window (?app=win, app/winwindow.py, WebView2, which reads the message's "rb" key); false anywhere else
  const post = (msg) => {
    if (appKind === 'mac') {
      const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.rb;
      if (!h) return false;
      h.postMessage(JSON.stringify(msg));
      return true;
    }
    if (appKind === 'win') {
      const w = window.chrome && window.chrome.webview;
      if (!w) return false;
      w.postMessage({ rb: msg });
      return true;
    }
    return false;
  };
  if (appKind === 'mac' || appKind === 'win') {
    const nativeOpen = window.open.bind(window);
    // the window has no tabs: new windows (the PDF viewer, articles) open in your browser
    window.open = (u, ...rest) => {
      if (u && post({ open: new URL(String(u), location.href).href })) return null;
      return nativeOpen(u, ...rest);
    };
  }
  if (appKind === 'mac') {
    // the window buttons sit at the left of the header, and pressing an empty part of the header drags the
    // window (app/macwindow.py): tell it where the header's buttons and fields are, so those still click
    // (the Windows window does this with app-region in style.css, below)
    document.documentElement.classList.add('mac-window');
    const CLICKABLE = 'button, input, select, textarea, a[href], label, summary, [role="button"], [role="menuitem"], [tabindex]:not([tabindex="-1"]), [contenteditable=""], [contenteditable="true"]';
    let sendT = 0;
    const send = () => {
      clearTimeout(sendT);
      sendT = setTimeout(() => {
        const bar = document.getElementById('topbar');
        if (!bar) return;
        const rects = [...bar.querySelectorAll(CLICKABLE)].map((el) => el.getBoundingClientRect())
          .filter((r) => r.width && r.height).map((r) => [r.left - 2, r.top - 2, r.width + 4, r.height + 4]);
        post({ keep_clear: rects });
      }, 60);
    };
    window.addEventListener('resize', send);
    window.addEventListener('load', send);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(send);
    const watch = () => {
      const bar = document.getElementById('topbar');
      if (!bar) return;
      new MutationObserver(send).observe(bar, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'hidden', 'style'] });
      send();
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();
  }
  if (appKind === 'win' && winTitlebar === 'custom') {
    // no title bar (app/winwindow.py): the header is the title bar (style.css marks it a drag region, so an
    // empty part of it moves the window and a double-click maximizes it, both done by Windows), and the
    // minimize, maximize and close buttons are drawn here, at its right end, like a Windows 11 app's
    const root = document.documentElement;
    root.classList.add('win-window');
    const GLYPH = { minimize: '\uE921', maximize: '\uE922', restore: '\uE923', close: '\uE8BB' };
    const bar = document.createElement('div');
    bar.className = 'win-caption';
    const button = (cls, caption, glyph, label) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = cls; b.textContent = glyph;
      b.title = label; b.setAttribute('aria-label', label); b.tabIndex = -1;
      b.addEventListener('click', () => post({ caption }));
      bar.appendChild(b);
      return b;
    };
    button('wc-min', 'minimize', GLYPH.minimize, 'Minimize');
    const max = button('wc-max', 'maximize', GLYPH.maximize, 'Maximize');
    button('wc-close', 'close', GLYPH.close, 'Close');
    const setMaximized = (on) => {
      const label = on ? 'Restore' : 'Maximize';
      max.textContent = on ? GLYPH.restore : GLYPH.maximize;
      max.title = label; max.setAttribute('aria-label', label);
    };
    // the header's color, so the window paints the thin edge above the page (where it resizes) to match. The
    // header is see-through (color(srgb ... / .88)): painted over the page's own background on a 1 px canvas
    const px = document.createElement('canvas');
    px.width = px.height = 1;
    const hex = (under, over) => {
      const g = px.getContext('2d', { willReadFrequently: true });
      if (!g) return '';
      g.clearRect(0, 0, 1, 1);
      g.fillStyle = '#ffffff'; g.fillRect(0, 0, 1, 1);
      for (const c of [under, over]) { try { g.fillStyle = c; g.fillRect(0, 0, 1, 1); } catch { /* unknown color */ } }
      const d = g.getImageData(0, 0, 1, 1).data;
      return '#' + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, '0')).join('');
    };
    let lastChrome = '';
    const sendChrome = () => {
      const tb = document.getElementById('topbar');
      if (!tb) return;
      const c = hex(getComputedStyle(document.body).backgroundColor, getComputedStyle(tb).backgroundColor);
      if (c && c !== lastChrome) { lastChrome = c; post({ chrome: c }); }
    };
    const mount = () => {
      document.body.appendChild(bar);
      sendChrome();
      new MutationObserver(() => requestAnimationFrame(sendChrome))
        .observe(root, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
    window.addEventListener('load', sendChrome);
    try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => requestAnimationFrame(sendChrome)); } catch { /* old engine */ }
    // from the window: maximized or not (the middle button's glyph), active or not (dimmed buttons)
    try {
      window.chrome.webview.addEventListener('message', (e) => {
        const m = e && e.data && e.data.rb;
        if (!m || typeof m !== 'object') return;
        if ('window' in m) setMaximized(m.window === 'maximized');
        if ('active' in m) root.classList.toggle('win-inactive', !m.active);
      });
    } catch { /* not the Windows window */ }
  }
  function stopped() {
    if (es) { es.close(); es = null; }
    // stopping to update (static/updates.js): stay open, it says what's happening and reloads when it's back
    if (window.__rbUpdating) return;
    if (appKind === '1') window.close();  // the Mac and Windows windows are closed by app/macwindow.py and app/winwindow.py
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
  // Where dropped files and folders are on disk: only research brain's own window knows (a page never sees
  // paths). The Windows window reads them from the dropped File objects (WebView2), the Mac window from the drag
  // itself (app/macwindow.py). Resolves to [{name, path}], or null in a browser or if the window doesn't answer.
  const dropWaits = new Map();
  let dropSeq = 0;
  function droppedPaths(files, names) {
    if (appKind !== 'mac' && appKind !== 'win') return Promise.resolve(null);
    const id = `d${Date.now()}-${++dropSeq}`;
    const p = new Promise((resolve) => {
      dropWaits.set(id, resolve);
      setTimeout(() => { if (dropWaits.delete(id)) resolve(null); }, 2000);
    });
    try {
      if (appKind === 'win') {
        const w = window.chrome && window.chrome.webview;
        if (!w || !w.postMessageWithAdditionalObjects) { dropWaits.get(id)(null); dropWaits.delete(id); return p; }
        w.postMessageWithAdditionalObjects({ rb: { dropped: id } }, files);
      } else if (!post({ dropped: id, names })) { dropWaits.get(id)(null); dropWaits.delete(id); }
    } catch { const r = dropWaits.get(id); dropWaits.delete(id); if (r) r(null); }
    return p;
  }
  function droppedReply(m) {
    const r = m && dropWaits.get(m.dropped);
    if (!r) return;
    dropWaits.delete(m.dropped);
    r(Array.isArray(m.paths) ? m.paths.filter((x) => x && typeof x.path === 'string') : null);
  }
  if (appKind === 'win') {
    try { window.chrome.webview.addEventListener('message', (e) => { const m = e && e.data && e.data.rb; if (m && m.dropped) droppedReply(m); }); }
    catch { /* not the Windows window */ }
  }
  window.__rbDropped = droppedReply;  // the Mac window answers through this (app/macwindow.py)
  window.__rbLive = { get source() { return es; }, flush, native: post, get app() { return appKind; }, droppedPaths };
})();
