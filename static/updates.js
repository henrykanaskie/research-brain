/* research_brain — updates: the version, "Update" in the top bar, and installing a new version from the app.
 *
 * Asks GET /api/update a few seconds after the app opens (the server then asks GitHub), about every hour, when the
 * internet comes back, and a few minutes after a check that failed. When there's a newer version, a small "Update" button appears in the top bar; it opens
 * a popover with what's new, Update now and Later (Later hides the button until the version after that).
 * Update now saves open edits, then POST /api/update/install: the server downloads the new version and
 * stops, an installer starts it again (app/updates.py), and this page says what's happening, waits, and
 * reloads. The next start says "Updated to X" (or what went wrong) once.
 * Settings › About: the version (with Copy, for problem reports), Check now, and the same Update button.
 * window.__rbUpdates = { settingsSection(say), check(force) }.
 */
(() => {
  'use strict';
  const rb = () => window.__rb || null;

  const CSS = `
  .up-wrap[hidden] { display: none; }
  .up-pill { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px 0 8px; border-radius: 13px;
    border: 1px solid color-mix(in srgb, var(--moss) 45%, var(--rule)); background: color-mix(in srgb, var(--moss) 10%, transparent);
    color: var(--ink); font: 500 12px/1 var(--sans); cursor: pointer; white-space: nowrap; }
  .up-pill:hover { background: color-mix(in srgb, var(--moss) 18%, transparent); }
  .up-pill:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: 2px; }
  .up-pill svg { color: var(--moss); flex: none; }
  .up-pill.err { border-color: color-mix(in srgb, var(--red) 45%, var(--rule)); background: var(--red-wash); }
  .up-pill.err svg { color: var(--red); }
  .up-pop { width: 320px; padding: 14px; }
  .up-pop h3 { margin: 0 0 4px; font: 600 13px/1.4 var(--sans); color: var(--ink); }
  .up-pop p { margin: 0 0 8px; font: 12.5px/1.5 var(--sans); color: var(--ink-2); }
  .up-pop .up-err { color: var(--red); }
  .up-notes { margin: 6px 0 10px; padding-left: 16px; max-height: 180px; overflow: auto; font: 12.5px/1.5 var(--sans); color: var(--ink-2); }
  .up-notes li { margin: 2px 0; }
  .up-row { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin-top: 10px; }
  .up-log { white-space: pre-wrap; font: 11px/1.45 var(--mono); color: var(--ink-2); background: var(--ground); padding: 6px 8px;
    border-radius: 3px; max-height: 140px; overflow: auto; margin: 6px 0 0; }
  .up-cover { position: fixed; inset: 0; z-index: 1000; display: flex; align-items: center; justify-content: center; padding: 16px;
    background: color-mix(in srgb, var(--ground) 88%, transparent); backdrop-filter: blur(2px); }
  .up-cover .box { max-width: 380px; text-align: center; font: 13.5px/1.55 var(--sans); color: var(--ink-2); }
  .up-cover b { display: block; font: italic 500 19px/1.3 var(--display, var(--serif)); color: var(--ink); margin-bottom: 6px; }
  .up-cover .spin { width: 22px; height: 22px; margin: 0 auto 14px; border-radius: 50%; border: 2px solid var(--rule);
    border-top-color: var(--moss); animation: up-spin 0.9s linear infinite; }
  @keyframes up-spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .up-cover .spin { animation: none; border-top-color: var(--rule); } }
  .ls-ver { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font: 13px/1.4 var(--sans); color: var(--ink); }
  .ls-ver .v { font-weight: 600; font-variant-numeric: tabular-nums; }
  .ls-ver .sys { color: var(--ink-3); font-size: 12px; }
  @media (max-width: 760px) { .up-pill .t { display: none; } .up-pill { padding: 0 8px; } }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, '');
      else if (v !== false && v != null) e.setAttribute(k, v);
    }
    for (const c of kids) if (c != null) e.append(c);
    return e;
  }
  const toast = (msg, kind = 'info', opts) => {
    const t = rb() && rb().toast;
    if (t) t(msg, kind, opts);
  };
  async function api(path, opts = {}) {
    const r = await fetch(path, { cache: 'no-store', ...opts });
    let data = null; try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) {
      const R = rb() || {};
      throw new Error(R.errText ? R.errText(data, r.status) : ((data && data.detail) || `HTTP ${r.status}`));
    }
    return data;
  }
  const post = (path) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const store = {
    get(k) { try { return localStorage.getItem('rb.' + k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem('rb.' + k, v); } catch { /* storage blocked */ } },
  };

  let INFO = null;      // the last GET /api/update
  let failed = null;    // an install that didn't start, or the last update that didn't finish: {error, log?}
  const subs = new Set();  // Settings sections showing INFO
  const changed = () => { renderPill(); subs.forEach((f) => f()); };

  /** What's new: the release notes' "- " lines, without the version line itself. */
  function noteLines(notes, version) {
    return String(notes || '').split('\n').map((l) => l.replace(/^\s*[-*]\s*/, '').trim())
      .filter((l) => l && l !== version && l !== `v${version}`).slice(0, 8);
  }
  function when(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const days = Math.round((Date.now() - d.getTime()) / 86400000);
    if (days <= 0) return 'today';
    if (days === 1) return 'yesterday';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  // ---------------------------------------------------------------- the top bar button
  const ICON = '<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 12.5V3.5M4 7l4-4 4 4"/></svg>';
  let wrap = null, pill = null, pop = null;
  function mountPill() {
    const status = document.getElementById('status');
    if (!status || wrap) return;
    pill = el('button', { class: 'up-pill', type: 'button', 'aria-haspopup': 'dialog', 'aria-expanded': 'false' });
    pop = el('div', { class: 'menu menu-right up-pop', role: 'dialog', 'aria-label': 'Update research brain', hidden: true });
    wrap = el('div', { class: 'menu-wrap up-wrap', hidden: true }, pill, pop);
    status.before(wrap);
    pill.addEventListener('click', (e) => { e.stopPropagation(); togglePop(); });
    pop.addEventListener('click', (e) => e.stopPropagation());
    document.addEventListener('click', () => togglePop(false));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pop.hidden) { togglePop(false); pill.focus(); } });
  }
  function togglePop(on) {
    if (!pop) return;
    const show = on === undefined ? pop.hidden : on;
    if (show) renderPop();
    pop.hidden = !show;
    pill.setAttribute('aria-expanded', String(show));
  }
  function renderPill() {
    mountPill();
    if (!wrap) return;
    const i = INFO;
    const offer = i && i.newer && i.latest && store.get('update.later') !== i.latest;
    const show = !!(failed || offer);
    wrap.hidden = !show;
    if (!show) { togglePop(false); return; }
    pill.classList.toggle('err', !!failed);
    pill.innerHTML = ICON + `<span class="t">${failed ? 'Update didn’t finish' : 'Update'}</span>`;
    pill.title = failed ? 'The last update didn’t finish. Click for what happened.' : `research brain ${i.latest} is ready (you have ${i.current})`;
    if (!pop.hidden) renderPop();
  }
  function renderPop() {
    const i = INFO || {};
    pop.replaceChildren();
    if (failed) {
      pop.append(el('h3', { text: 'The update didn’t finish' }),
        el('p', { class: 'up-err', text: failed.error || 'Something went wrong.' }));
      if (failed.log) pop.append(el('pre', { class: 'up-log', text: failed.log }));
      const again = el('button', { class: 'btn btn-primary', type: 'button', text: 'Try again', onclick: () => install() });
      const dismiss = el('button', { class: 'btn', type: 'button', text: 'Dismiss', onclick: () => { failed = null; post('/api/update/ack').catch(() => {}); changed(); } });
      again.hidden = !(i.newer && i.can_install);
      pop.append(el('div', { class: 'up-row' }, dismiss, again));
      return;
    }
    pop.append(el('h3', { text: `research brain ${i.latest} is ready` }),
      el('p', { text: `You have ${i.current}. Updating takes about a minute, then research brain opens again. Your library isn’t touched.` }));
    const lines = noteLines(i.notes, i.latest);
    if (lines.length) pop.append(el('ul', { class: 'up-notes' }, ...lines.map((l) => el('li', { text: l }))));
    if (!i.can_install) pop.append(el('p', { class: 'up-err', text: i.reason }));
    const later = el('button', { class: 'btn', type: 'button', text: 'Later', onclick: () => { store.set('update.later', i.latest); changed(); } });
    const now = el('button', { class: 'btn btn-primary', type: 'button', text: 'Update now', onclick: () => install() });
    now.disabled = !i.can_install;
    pop.append(el('div', { class: 'up-row' }, later, now));
  }

  // ---------------------------------------------------------------- installing
  let cover = null;
  function showCover(title, text) {
    if (!cover) { cover = el('div', { class: 'up-cover', role: 'status', 'aria-live': 'polite' }); document.body.append(cover); }
    cover.replaceChildren(el('div', { class: 'box' }, el('div', { class: 'spin', 'aria-hidden': 'true' }), el('b', { text: title }), el('span', { text })));
  }
  function hideCover() { if (cover) { cover.remove(); cover = null; } }

  let installing = false;
  async function install() {
    if (installing) return;
    const R = rb() || {};
    if (R.saveNow && !(await R.saveNow())) {
      toast('Your open note isn’t saved yet. Save it (or discard the changes), then update.', 'error');
      return;
    }
    installing = true;
    togglePop(false);
    const to = (INFO && INFO.latest) || 'the new version';
    showCover(`Updating to ${to}`, 'Downloading…');
    window.__rbUpdating = true;  // static/live.js: research brain stopping now is expected, keep this window
    let started;
    try {
      started = await post('/api/update/install');
    } catch (e) {
      window.__rbUpdating = false;
      installing = false;
      hideCover();
      failed = { error: e.message };
      changed();
      togglePop(true);
      return;
    }
    showCover(`Updating to ${to}`, 'Installing. research brain opens again by itself in about a minute.');
    await waitForNew(started.from);
  }
  async function waitForNew(from) {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    await sleep(2500);
    const t0 = Date.now();
    let down = false;
    while (Date.now() - t0 < 10 * 60000) {
      try {
        // a request caught by the old server as it shuts down can hang forever: give each one up after 4s
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), 4000);
        const r = await fetch('/api/update', { cache: 'no-store', signal: ctl.signal }).finally(() => clearTimeout(t));
        if (r.ok) {
          const d = await r.json();
          // back: a different version answers (an update can finish before the first look), or it went away
          // and the installer is done (a failed update starts the old version again), or the installer has
          // finished and left a result (a fast failure restarts the old version before the first look)
          if (!d.installing && (d.current !== from || down || d.result)) { location.reload(); return; }
        } else down = true;
      } catch { down = true; }
      await sleep(1500);
    }
    showCover('This is taking longer than it should', 'Close research brain and open it again. If it doesn’t open, run the newer installer.');
  }

  // ---------------------------------------------------------------- checking
  let retry = 0;
  async function check(force, opened) {
    clearTimeout(retry);
    try { INFO = await api('/api/update' + (force ? '?force=true' : opened ? '?opened=true' : '')); } catch { return INFO; }
    if (INFO.enabled && INFO.failed) retry = setTimeout(() => check(false), 5 * 60000);
    const r = INFO.result;
    if (r && !installing) {
      if (r.ok) { failed = null; toast(`Updated to ${r.version}.`, 'ok'); store.set('update.later', ''); }
      else failed = { error: r.error, log: r.log };
      post('/api/update/ack').catch(() => {});
      INFO.result = null;
    }
    changed();
    return INFO;
  }

  // ---------------------------------------------------------------- Settings › About
  function settingsSection(say) {
    const sec = el('section', { class: 'ls-sec', id: 'ls-about' }, el('div', { class: 'ls-h', text: 'Version and updates' }));
    const ver = el('div', { class: 'ls-ver' });
    const line = el('p', { class: 'ls-p' });
    const notes = el('ul', { class: 'up-notes', hidden: true });
    const err = el('p', { class: 'ls-p ls-warn', hidden: true });
    const checkBtn = el('button', { class: 'btn', type: 'button', text: 'Check now' });
    const upBtn = el('button', { class: 'btn btn-primary', type: 'button', text: 'Update now', hidden: true, onclick: () => install() });
    sec.append(ver, line, notes, err, el('div', { class: 'ls-row' }, checkBtn, upBtn));
    const copyBtn = el('button', { class: 'btn btn-text', type: 'button', text: 'Copy' });
    copyBtn.addEventListener('click', async () => {
      const i = INFO || {};
      const text = `research brain ${i.current || '?'}${i.system ? `, ${i.system}` : ''}`;
      try { await navigator.clipboard.writeText(text); say('Copied: ' + text); } catch { say(text); }
    });
    let attached = false;
    function render() {
      if (attached && !sec.isConnected) { subs.delete(render); return; }  // Settings closed
      if (sec.isConnected) attached = true;
      const i = INFO;
      if (!i) { ver.replaceChildren(el('span', { class: 'v', text: 'research brain' })); line.textContent = 'Checking…'; return; }
      ver.replaceChildren(el('span', { class: 'v', text: `research brain ${i.current}` }), el('span', { class: 'sys', text: i.system || '' }), copyBtn);
      notes.hidden = true; err.hidden = true; upBtn.hidden = true;
      if (failed) { err.hidden = false; err.textContent = 'The last update didn’t finish: ' + failed.error; }
      if (!i.enabled) { line.textContent = 'Checking for updates is turned off on this computer (RB_UPDATE_CHECK=0).'; return; }
      const checked = when(i.checked);
      if (i.newer) {
        line.textContent = `${i.latest} is ready${i.published ? `, published ${when(i.published)}` : ''}. Updating takes about a minute; your library isn’t touched.`;
        const l = noteLines(i.notes, i.latest);
        notes.replaceChildren(...l.map((x) => el('li', { text: x })));
        notes.hidden = !l.length;
        upBtn.hidden = false; upBtn.disabled = !i.can_install;
        if (!i.can_install) { err.hidden = false; err.textContent = i.reason; }
      } else if (i.latest && !i.failed) {
        line.textContent = `You’re up to date. Checked ${checked}.`;
      } else {
        line.textContent = `Couldn’t check for updates${checked ? ` (last checked ${checked})` : ''}. It tries again by itself; you need an internet connection.`;
      }
    }
    checkBtn.addEventListener('click', async () => {
      checkBtn.disabled = true; line.textContent = 'Checking…';
      await check(true);
      checkBtn.disabled = false;
      render();
    });
    subs.add(render);
    render();
    if (!INFO) check(false);
    return sec;
  }

  setTimeout(() => check(false, true), 4000);
  setInterval(() => check(false), 3600 * 1000);
  addEventListener('online', () => setTimeout(() => { if (!installing) check(false); }, 3000));
  window.__rbUpdates = { settingsSection, check, install };
})();
