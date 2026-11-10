/* research_brain — "Import from Zotero" dialog.
 * Self-contained: injects its own styles and markup, talks to /api/zotero/*, and uses
 * window.__rb (refreshGraph, openNote, S) from app.js. See DESIGN.md for the visual rules.
 */
(() => {
  'use strict';

  const CSS = `
  .zt-modal { position: fixed; inset: 0; z-index: 72; display: flex; align-items: center; justify-content: center; padding: 16px;
    background: color-mix(in srgb, var(--ground) 72%, transparent); }
  .zt-card { width: min(520px, 100%); max-height: calc(100vh - 32px); display: flex; flex-direction: column;
    background: var(--sheet); border: 1px solid var(--rule); color: var(--ink); }
  .zt-head { display: flex; align-items: flex-start; gap: 12px; padding: 28px 32px 0; }
  .zt-title { font: 600 24px/1.15 var(--serif); letter-spacing: -.01em; margin: 0; }
  .zt-sub { margin: 8px 0 0; color: var(--ink-2); font-size: 13px; line-height: 1.5; }
  .zt-sub .ident { color: var(--ink-2); overflow-wrap: anywhere; }
  .zt-body { padding: 20px 32px 0; overflow: auto; min-height: 0; }
  .zt-label { display: flex; justify-content: space-between; align-items: baseline; font-size: 12px; color: var(--ink-2); margin: 0 0 6px; }
  .zt-tree { list-style: none; margin: 0; padding: 4px 0; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
    max-height: 264px; overflow: auto; }
  .zt-row { display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 8px; border-radius: 3px; cursor: pointer;
    font-size: 13px; color: var(--ink); user-select: none; }
  .zt-row:hover { background: var(--ground); }
  .zt-row:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: -2px; }
  .zt-row[aria-selected="true"] { background: var(--ground); font-weight: 500; }
  .zt-row .zt-mark { width: 12px; height: 12px; flex: none; border: 1px solid var(--ink-2); border-radius: 50%; position: relative; }
  .zt-row[aria-selected="true"] .zt-mark { border-color: var(--ink); }
  .zt-row[aria-selected="true"] .zt-mark::after { content: ""; position: absolute; inset: 2px; border-radius: 50%; background: var(--ink); }
  .zt-row .zt-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .zt-row .zt-count { color: var(--ink-2); font-variant-numeric: tabular-nums; font-weight: 400; }
  .zt-row.zt-all .zt-name { font-weight: 500; }
  .zt-libs { display: flex; flex-wrap: wrap; gap: 4px; margin: 0 0 16px; }
  .zt-lib { height: 32px; padding: 0 12px; border: 1px solid var(--rule); border-radius: 3px; background: none;
    color: var(--ink-2); font: inherit; font-size: 13px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
  .zt-lib:hover { background: var(--ground); color: var(--ink); }
  .zt-lib[aria-checked="true"] { color: var(--ink); border-color: var(--ink); font-weight: 500; }
  .zt-lib .zt-count { color: var(--ink-2); font-weight: 400; font-variant-numeric: tabular-nums; }
  .zt-kept { margin: 12px 0 0; font-size: 12.5px; color: var(--ink-2); }
  .zt-kept summary { cursor: pointer; color: var(--ink); }
  .zt-kept ul { margin: 6px 0 0; padding-left: 16px; max-height: 120px; overflow: auto; }
  .zt-kept button { background: none; border: 0; padding: 0; color: var(--ink); font: inherit; text-decoration: underline;
    text-decoration-color: var(--rule); cursor: pointer; text-align: left; }
  .zt-opts { display: flex; flex-direction: column; gap: 2px; margin: 16px 0 0; }
  .zt-check { display: flex; align-items: flex-start; gap: 10px; padding: 6px 0; cursor: pointer; font-size: 13px; }
  .zt-check input { accent-color: var(--ink); margin: 2px 0 0; flex: none; }
  .zt-check .menu-hint { display: block; margin-top: 1px; }
  .zt-progress { margin: 12px 0 0; }
  .zt-line { font-size: 13px; color: var(--ink); display: flex; gap: 6px; min-width: 0; }
  .zt-line .zt-n { font-variant-numeric: tabular-nums; flex: none; }
  .zt-line .zt-cur { color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; font-family: var(--serif); font-size: 14px; line-height: 1.3; }
  .zt-bar { height: 2px; background: var(--rule); margin-top: 10px; position: relative; overflow: hidden; }
  .zt-bar i { position: absolute; inset: 0 auto 0 0; width: 0; background: var(--ink); transition: width .2s ease-out; }
  .zt-summary { font: 400 17px/1.45 var(--serif); margin: 0; }
  .zt-summary-sub { color: var(--ink-2); font-size: 12.5px; margin: 6px 0 0; }
  .zt-errors { margin: 12px 0 0; padding: 0 0 0 12px; border-left: 2px solid var(--red); font-size: 12.5px; color: var(--ink-2); }
  .zt-errors summary { cursor: pointer; color: var(--ink); }
  .zt-errors ul { margin: 6px 0 0; padding-left: 16px; max-height: 120px; overflow: auto; }
  .zt-error { margin: 16px 0 0; padding: 2px 0 2px 12px; border-left: 2px solid var(--red); font-size: 13px; line-height: 1.5; }
  .zt-error strong { color: var(--red); font-weight: 600; }
  .zt-error code { font-family: var(--mono); font-size: 12px; }
  .zt-empty { padding: 16px 0 0; margin: 0; color: var(--ink-2); font-size: 13px; line-height: 1.55; }
  .zt-actions { display: flex; justify-content: flex-end; align-items: center; gap: 8px; padding: 24px 32px 24px; }
  .zt-actions .zt-note { margin-right: auto; color: var(--ink-2); font-size: 12px; }
  @media (max-width: 560px) { .zt-head, .zt-body, .zt-actions { padding-left: 20px; padding-right: 20px; } }
  `;

  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : (many || one + 's')}`;
  const rb = () => window.__rb || {};

  async function api(method, path, body) {
    let r;
    try {
      r = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw new Error("Couldn't reach the research_brain server. Check that it's still running in Terminal.");
    }
    let data = null;
    try { data = await r.json(); } catch (e) { /* non-JSON error */ }
    if (!r.ok) throw new Error((data && data.detail) || `The server answered ${r.status}.`);
    return data;
  }

  // ---------------------------------------------------------------- state + markup
  const st = { open: false, status: null, selected: null, library: null, job: null, poll: null, lastFocus: null, busy: false };
  // Zotero group libraries: when there's more than one library, the dialog shows a picker and the tree
  // lists only the chosen library's collections. One library (the usual case): no picker, nothing changes.
  const libs = () => ((st.status && st.status.libraries) || []).filter((l) => l.items > 0 || l.kind === 'user');
  const multiLib = () => libs().length > 1;
  const libCols = () => {
    const cols = (st.status && st.status.collections) || [];
    return multiLib() && st.library != null ? cols.filter((c) => c.library_id == null || c.library_id === st.library) : cols;
  };
  const libItems = () => {
    const l = libs().find((x) => x.id === st.library);
    return multiLib() && l ? l.items : (st.status ? st.status.items : 0);
  };
  let root;

  function build() {
    const style = document.createElement('style');
    style.id = 'zotero-style';
    style.textContent = CSS;
    document.head.appendChild(style);
    root = document.createElement('div');
    root.className = 'zt-modal';
    root.hidden = true;
    root.innerHTML = `
      <div class="zt-card" role="dialog" aria-modal="true" aria-labelledby="zt-title">
        <div class="zt-head">
          <div style="flex:1;min-width:0">
            <h2 id="zt-title" class="zt-title">Import from Zotero</h2>
            <p id="zt-sub" class="zt-sub">Looking for your Zotero library…</p>
          </div>
          <button id="zt-close" class="btn btn-icon" type="button" title="Close (Esc)" aria-label="Close">
            <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" stroke-width="1.3"/></svg>
          </button>
        </div>
        <div id="zt-body" class="zt-body"></div>
        <div class="zt-actions">
          <span id="zt-note" class="zt-note"></span>
          <button id="zt-cancel" class="btn" type="button">Cancel</button>
          <button id="zt-go" class="btn btn-primary" type="button" disabled>Import</button>
        </div>
      </div>`;
    document.body.appendChild(root);
    $('#zt-close', root).addEventListener('click', close);
    $('#zt-cancel', root).addEventListener('click', onCancel);
    $('#zt-go', root).addEventListener('click', onPrimary);
    root.addEventListener('pointerdown', (e) => { if (e.target === root) close(); });
    root.addEventListener('keydown', onKey);
  }

  // ---------------------------------------------------------------- open / close
  function closeAddMenu() {
    const m = document.getElementById('add-menu');
    if (m && !m.hidden) {
      m.hidden = true;
      const b = document.getElementById('btn-add-menu');
      if (b) b.setAttribute('aria-expanded', 'false');
    }
  }

  async function open() {
    closeAddMenu();
    if (!root) build();
    st.lastFocus = document.activeElement;
    st.open = true;
    root.hidden = false;
    const S = rb().S;
    if (S) S.modalClose = close;  // app.js's Escape handler calls this
    if (st.job && st.job.state === 'running') { renderProgress(); return; }
    st.job = null;
    renderLoading();
    try {
      st.status = await api('GET', '/api/zotero/status');
    } catch (e) {
      st.status = null;
      renderError(e.message);
      return;
    }
    if (!st.open) return;
    if (st.status.job) { startPolling(st.status.job); return; }
    renderPicker();
  }

  function close() {
    if (!root || root.hidden) return;
    if (st.finished) { st.finished = false; st.job = null; }
    root.hidden = true;
    st.open = false;
    const S = rb().S;
    if (S && S.modalClose === close) S.modalClose = null;
    if (st.lastFocus && st.lastFocus.focus && document.contains(st.lastFocus)) st.lastFocus.focus({ preventScroll: true });
    // a running import keeps going; reopening the dialog shows its progress
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (e.key === 'Tab') {  // keep focus inside the dialog
      const f = Array.from(root.querySelectorAll('button:not([disabled]), input:not([disabled]), [tabindex="0"], summary'))
        .filter((el) => el.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    const row = e.target.closest && e.target.closest('.zt-row');
    if (row && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault();
      const rows = Array.from(root.querySelectorAll('.zt-row'));
      const next = rows[rows.indexOf(row) + (e.key === 'ArrowDown' ? 1 : -1)];
      if (next) { select(next.dataset.key); next.focus(); }
    } else if (row && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      if (e.key === 'Enter') onPrimary(); else select(row.dataset.key);
    }
  }

  // ---------------------------------------------------------------- views
  const body = () => $('#zt-body', root);
  function setSub(html) { $('#zt-sub', root).innerHTML = html; }
  function setActions({ primary = 'Import', disabled = false, cancel = 'Cancel', note = '', hidePrimary = false } = {}) {
    const go = $('#zt-go', root);
    go.textContent = primary; go.disabled = disabled; go.hidden = hidePrimary;
    $('#zt-cancel', root).textContent = cancel;
    $('#zt-note', root).textContent = note;
  }

  function renderLoading() {
    setSub('Looking for your Zotero library…');
    body().innerHTML = '<div class="working"><span class="spin"></span>Reading zotero.sqlite</div>';
    setActions({ disabled: true });
  }

  function renderError(msg, title = 'Something went wrong.') {
    body().innerHTML = `<div class="zt-error" role="alert"><strong>${esc(title)}</strong> ${esc(msg)}</div>`;
    setActions({ primary: 'Try again', cancel: 'Close' });
    st.retry = true;
  }

  function renderNotFound(s) {
    const unreadable = /couldn't read/i.test(s.message || '');
    setSub(unreadable ? 'Zotero was found but couldn\'t be read.' : "Zotero wasn't found on this Mac.");
    body().innerHTML = unreadable
      ? `<div class="zt-error" role="alert"><strong>Couldn't read the library.</strong> ${esc(s.message)}</div>
         <p class="zt-empty">Make sure Zotero has finished starting or syncing, then check again.</p>`
      : `<div class="zt-error" role="alert"><strong>No library at <span class="ident">${esc(s.dir)}</span>.</strong></div>
         <p class="zt-empty">In Zotero, open Settings, then Advanced, then Files and Folders, and copy the data directory
           location. Quit research_brain, run <span class="ident">export RB_ZOTERO_DIR="/that/folder"</span> in Terminal,
           and start it again.</p>`;
    setActions({ primary: 'Check again', cancel: 'Close' });
    st.retry = true;
  }

  function renderPicker() {
    const s = st.status;
    st.retry = false;
    if (!s.found) { renderNotFound(s); return; }
    setSub(`${plural(s.items, 'item')} in <span class="ident">${esc(s.dir)}</span>. Nothing in Zotero is changed.`);
    if (!s.items) {
      body().innerHTML = '<p class="zt-empty">Your Zotero library has no papers yet. Add some in Zotero, then come back.</p>';
      setActions({ disabled: true, cancel: 'Close' });
      return;
    }
    if (multiLib() && !libs().some((l) => l.id === st.library)) st.library = libs()[0].id;
    if (!multiLib()) st.library = null;
    const cols = libCols();
    if (st.selected !== null && !cols.some((c) => c.key === st.selected)) st.selected = null;
    const curLib = libs().find((l) => l.id === st.library);
    const libPicker = multiLib() ? `
      <div class="zt-label" id="zt-lib-label"><span>Library</span></div>
      <div class="zt-libs" role="radiogroup" aria-labelledby="zt-lib-label">${libs().map((l) => `
        <button type="button" class="zt-lib" role="radio" data-lib="${l.id}" aria-checked="${l.id === st.library}"
          title="${esc(l.kind === 'group' ? 'Group library' : 'Your own library')}">${esc(l.name)}
          <span class="zt-count">${l.items.toLocaleString()}</span></button>`).join('')}</div>` : '';
    const rows = [`<li class="zt-row zt-all" role="option" tabindex="-1" data-key="" aria-selected="false">
        <span class="zt-mark" aria-hidden="true"></span><span class="zt-name">${multiLib() && curLib ? `All of ${esc(curLib.name)}` : 'Entire library'}</span>
        <span class="zt-count">${libItems().toLocaleString()}</span></li>`];
    for (const c of cols) {
      rows.push(`<li class="zt-row" role="option" tabindex="-1" data-key="${esc(c.key)}" aria-selected="false"
          style="padding-left:${8 + 18 * ((c.depth || 0) + 1)}px">
        <span class="zt-mark" aria-hidden="true"></span><span class="zt-name" title="${esc(c.name)}">${esc(c.name)}</span>
        <span class="zt-count">${c.count.toLocaleString()}</span></li>`);
    }
    body().innerHTML = `${libPicker}
      <div class="zt-label" id="zt-tree-label"><span>What to import</span><span>Papers</span></div>
      <ul class="zt-tree" role="listbox" aria-labelledby="zt-tree-label">${rows.join('')}</ul>
      <div class="zt-opts">
        <label class="zt-check"><input id="zt-pdfs" type="checkbox" checked>
          <span>Copy PDFs<span class="menu-hint">Claude can read them, and they open in the viewer.</span></span></label>
        <label class="zt-check"><input id="zt-anns" type="checkbox" checked>
          <span>Import highlights as fragments<span class="menu-hint">Each highlight becomes a fragment, with its page and your comment.</span></span></label>
      </div>`;
    body().querySelector('.zt-tree').addEventListener('click', (e) => {
      const r = e.target.closest('.zt-row');
      if (r) { select(r.dataset.key); r.focus(); }
    });
    body().querySelector('.zt-tree').addEventListener('dblclick', (e) => { if (e.target.closest('.zt-row')) onPrimary(); });
    const picker = body().querySelector('.zt-libs');
    if (picker) picker.addEventListener('click', (e) => {
      const b = e.target.closest('.zt-lib');
      if (!b || Number(b.dataset.lib) === st.library) return;
      const keep = { pdfs: $('#zt-pdfs', root).checked, anns: $('#zt-anns', root).checked };
      st.library = Number(b.dataset.lib);
      st.selected = null;
      renderPicker();
      $('#zt-pdfs', root).checked = keep.pdfs; $('#zt-anns', root).checked = keep.anns;
      const nb = body().querySelector(`.zt-lib[data-lib="${st.library}"]`);
      if (nb) nb.focus();
    });
    setActions({ note: 'Papers you already have are matched, not duplicated.' });
    select(st.selected || '');  // after setActions, so the button keeps "Import N papers"
    const sel = body().querySelector('.zt-row[aria-selected="true"]');
    if (sel) sel.focus({ preventScroll: true });
  }

  function select(key) {
    st.selected = key || null;
    for (const r of root.querySelectorAll('.zt-row')) {
      const on = (r.dataset.key || '') === (key || '');
      r.setAttribute('aria-selected', on ? 'true' : 'false');
      r.tabIndex = on ? 0 : -1;
    }
    const c = libCols().find((x) => x.key === key);
    const n = c ? c.count : libItems();
    const go = $('#zt-go', root);
    go.textContent = n ? `Import ${plural(n, 'paper')}` : 'Import';
    go.disabled = !n;
  }

  function renderProgress() {
    const j = st.job || { done: 0, total: 0, current: '' };
    if (!body().querySelector('.zt-progress')) {
      body().innerHTML = `
        <div class="zt-progress" aria-live="polite">
          <div class="zt-line"><span class="zt-n" id="zt-n"></span><span class="zt-cur" id="zt-cur"></span></div>
          <div class="zt-bar" role="progressbar" aria-valuemin="0" id="zt-bar"><i></i></div>
        </div>`;
      setSub('Importing. You can close this window; the import keeps going.');
      setActions({ disabled: true, primary: 'Importing…', cancel: 'Close' });
    }
    const n = Math.min(j.done + 1, j.total);
    $('#zt-n', root).textContent = j.total ? `Importing ${n.toLocaleString()} of ${j.total.toLocaleString()}${j.current ? ':' : ''}` : 'Starting…';
    $('#zt-cur', root).textContent = j.current || '';
    $('#zt-cur', root).title = j.current || '';
    const bar = $('#zt-bar', root);
    bar.setAttribute('aria-valuemax', String(j.total || 1));
    bar.setAttribute('aria-valuenow', String(j.done));
    bar.firstElementChild.style.width = `${j.total ? (100 * j.done) / j.total : 0}%`;
  }

  function summaryText(j) {
    const parts = [`Added ${plural(j.created, 'paper')}`];
    if (j.updated) parts.push(`updated ${j.updated.toLocaleString()}`);
    if (j.fragments) parts.push(plural(j.fragments, 'highlight'));
    if (!j.created && !j.updated && !j.fragments) return 'Everything was already in your library.';
    return parts.join(', ') + '.';
  }
  /** Re-import sync of highlights: "2 highlights updated, 1 removed (deleted in Zotero), 1 kept (edited)." */
  function syncText(j) {
    const parts = [];
    if (j.highlights_updated) parts.push(`${plural(j.highlights_updated, 'highlight')} updated from Zotero`);
    if (j.highlights_removed) parts.push(`${j.highlights_removed.toLocaleString()} removed (deleted in Zotero, moved to the trash)`);
    if (j.highlights_kept) parts.push(`${j.highlights_kept.toLocaleString()} kept (edited)`);
    if (!parts.length) return '';
    const s = parts.join(', ') + '.';
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function renderDone() {
    const j = st.job;
    const extra = [];
    if (j.skipped) extra.push(`${plural(j.skipped, 'paper')} already up to date`);
    if (j.pdfs) extra.push(`${plural(j.pdfs, 'PDF')} copied`);
    const errs = j.errors || [];
    setSub(errs.length ? 'Finished, with some problems.' : 'Finished.');
    const sync = syncText(j);
    const kept = j.kept || [];
    body().innerHTML = `
      <div class="zt-progress">
        <p class="zt-summary">${esc(summaryText(j))}</p>
        ${extra.length ? `<p class="zt-summary-sub">${esc(extra.join(', '))}.</p>` : ''}
        ${sync ? `<p class="zt-summary-sub">${esc(sync)}</p>` : ''}
        ${kept.length ? `<details class="zt-kept"><summary>Highlights kept because you edited them</summary>
          <ul>${kept.map((k) => `<li><button type="button" data-open="${esc(k.id)}">${esc(k.title || k.id)}</button></li>`).join('')}</ul>
          <p style="margin:6px 0 0">They changed or were deleted in Zotero, but you've written in them here, so they were left as they are.</p></details>` : ''}
        ${errs.length ? `<details class="zt-errors"><summary>${plural(errs.length, "paper")} couldn't be imported</summary>
          <ul>${errs.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>
          <p style="margin:6px 0 0">The rest imported fine. Running the import again retries only these.</p></details>` : ''}
      </div>`;
    setActions({ primary: 'Done', cancel: 'Import more' });
    for (const b of body().querySelectorAll('.zt-kept [data-open]')) {
      b.addEventListener('click', () => { const id = b.dataset.open; close(); if (rb().openNote) rb().openNote(id, { center: true }); });
    }
    $('#zt-go', root).focus({ preventScroll: true });
    st.finished = true;
  }

  // ---------------------------------------------------------------- actions
  async function onPrimary() {
    const go = $('#zt-go', root);
    if (go.disabled || st.busy) return;
    if (st.finished) { st.finished = false; st.job = null; close(); return; }
    if (st.retry) { st.retry = false; open(); return; }
    if (st.job && st.job.state === 'running') return;
    st.busy = true;
    go.disabled = true;
    try {
      const res = await api('POST', '/api/zotero/import', {
        collection_key: st.selected || null,
        library_id: multiLib() ? st.library : null,
        include_pdfs: $('#zt-pdfs', root).checked,
        include_annotations: $('#zt-anns', root).checked,
      });
      startPolling(res.job_id);
    } catch (e) {
      renderError(e.message, "Couldn't start the import.");
    } finally {
      st.busy = false;
    }
  }

  function onCancel() {
    if (st.finished) { st.finished = false; st.job = null; open(); return; }  // "Import more"
    close();
  }

  function startPolling(jobId) {
    clearTimeout(st.poll);
    st.job = { id: jobId, state: 'running', done: 0, total: 0, current: '' };
    if (st.open) renderProgress();
    let failures = 0;
    const tick = async () => {
      try {
        st.job = await api('GET', `/api/zotero/jobs/${encodeURIComponent(jobId)}`);
        failures = 0;
      } catch (e) {
        if (++failures < 5) { st.poll = setTimeout(tick, 1000); return; }
        st.job = null;
        if (st.open) renderError(`${e.message} Your library may be partly imported; running the import again picks up where it stopped.`, 'Lost track of the import.');
        return;
      }
      if (st.job.state === 'running') {
        if (st.open) renderProgress();
        st.poll = setTimeout(tick, 400);
        return;
      }
      try { if (rb().refreshGraph) await rb().refreshGraph(); } catch (e) { /* graph refresh is best-effort */ }
      if (st.job.state === 'error') {
        const j = st.job;
        st.job = null;
        if (st.open) renderError(`${j.message || 'The import stopped.'} ${j.done ? `${plural(j.done, 'paper')} were imported before it stopped; running the import again continues from there.` : ''}`, 'The import stopped.');
        else notify(`Zotero import stopped: ${j.message || 'unknown error'}`);
        return;
      }
      if (st.open) renderDone();
      else { notify(`Zotero import finished. ${summaryText(st.job)} ${syncText(st.job)}`.trim()); st.job = null; }
    };
    st.poll = setTimeout(tick, 250);
  }

  // A small toast when an import finishes with the dialog closed (matches app.js's .toast styling).
  function notify(msg) {
    const box = document.getElementById('toasts');
    if (!box) return;
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = `<span class="t-msg">${esc(msg)}</span>`;
    t.addEventListener('click', () => t.remove());
    box.appendChild(t);
    setTimeout(() => t.remove(), 8000);
  }

  // ---------------------------------------------------------------- wiring
  function wire() {
    const btn = document.getElementById('btn-zotero');
    if (btn) btn.addEventListener('click', (e) => { e.stopPropagation(); open(); });
    if (!root) build();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();
  window.__rbZotero = { open, close };
})();
