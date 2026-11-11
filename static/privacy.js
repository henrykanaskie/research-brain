/* research_brain — privacy of your own ideas (app/privacy.py).
 *
 * 1. A small lock in the note panel's header: closed = private (never sent to an AI agent, left out of
 *    exports), open = shareable. Click to switch; POST /api/notes/{id}/privacy.
 * 2. window.__rbPrivacy.settingsSection(say) for Library settings (static/settings.js): which types are
 *    private by default, the local-AI exception, and "What was sent to AI" (GET /api/privacy/log).
 * Loaded after app.js and before settings.js. Graph drawing is app.js's; nodes carry `private: true`.
 */
(() => {
  'use strict';
  const rb = () => window.__rb || null;

  const CSS = `
  .pv-lock { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 7px; border: 1px solid transparent;
    border-radius: 3px; background: transparent; color: var(--ink-3); font: 500 11.5px/1 var(--sans); cursor: pointer; }
  .pv-lock:hover { color: var(--ink); border-color: var(--rule); }
  .pv-lock.on { color: var(--red); }
  .pv-lock:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: 1px; }
  .pv-lock svg { flex: none; }
  .pv-log { width: 100%; border-collapse: collapse; margin-top: 6px; font: 12px/1.45 var(--sans); color: var(--ink-2); }
  .pv-log th { text-align: left; font-weight: 600; color: var(--ink); border-bottom: 1px solid var(--rule); padding: 4px 6px 4px 0; }
  .pv-log td { border-bottom: 1px solid var(--rule); padding: 4px 6px 4px 0; vertical-align: top; }
  .pv-log a { color: var(--ink); text-decoration: underline; text-decoration-color: var(--rule); cursor: pointer; }
  .pv-log .p { color: var(--red); }
  .pv-log-wrap { max-height: 240px; overflow: auto; }
  .pv-kinds { display: flex; flex-wrap: wrap; gap: 4px 14px; }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'text') e.textContent = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, '');
      else if (v !== false && v != null) e.setAttribute(k, v);
    }
    for (const c of kids) if (c != null) e.append(c);
    return e;
  }
  async function call(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    let data = null; try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
    return data;
  }
  const toast = (m, k) => { const t = rb() && rb().toast; if (t) t(m, k); };

  const LOCKED = '<svg width="11" height="12" viewBox="0 0 11 12" aria-hidden="true"><rect x="1.5" y="5.5" width="8" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M3.3 5.5V3.8a2.2 2.2 0 0 1 4.4 0v1.7" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
  const OPEN = '<svg width="11" height="12" viewBox="0 0 11 12" aria-hidden="true"><rect x="1.5" y="5.5" width="8" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M3.3 5.5V3.8a2.2 2.2 0 0 1 4.3-.6" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';

  // ---------------------------------------------------------------- the lock in the panel header
  function renderLock(n) {
    const head = document.querySelector('#panel .panel-head');
    if (!head || !n) return;
    let b = document.getElementById('pv-lock');
    if (!b) {
      b = el('button', { id: 'pv-lock', class: 'pv-lock', type: 'button' });
      const type = document.getElementById('panel-type');
      (type || head.firstChild).after(b);
      b.addEventListener('click', toggle);
    }
    const on = !!n.private;
    const inherited = on && n.private_explicit !== true;
    b.classList.toggle('on', on);
    b.innerHTML = (on ? LOCKED : OPEN) + `<span>${on ? 'Private' : 'Shareable'}</span>`;
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.title = on
      ? `Private${inherited ? (n.type === 'fragment' || n.parent ? ' (its parent is private)' : ' by default for this type') : ''}: never sent to an AI agent and left out of exports. Click to make it shareable.`
      : 'Shareable: the AI features can read it and exports include it. Click to make it private.';
  }

  async function toggle() {
    const r = rb(); const n = r && r.S && r.S.cur;
    if (!n) return;
    try {
      if (r.flushSave) await r.flushSave();
      const out = await call('POST', `/api/notes/${encodeURIComponent(n.id)}/privacy`, { private: !n.private });
      if (out.private === n.private && !out.private) toast('Still private: its parent note is private.', 'info');
      else toast(out.private ? 'Private. It stays on this computer: no AI agent sees it and exports leave it out.' : 'Shareable. The AI features can read it now.');
      if (r.reloadCur) await r.reloadCur();
      if (r.refreshGraph) r.refreshGraph();
    } catch (e) { toast(`Couldn’t change privacy. ${e.message}`, 'error'); }
  }

  function hook() {
    const r = rb();
    if (r && r.onPanelRender) r.onPanelRender((n) => renderLock(n));
    else setTimeout(hook, 50);
  }
  hook();

  // ---------------------------------------------------------------- Library settings section
  const KIND_LABEL = { note: 'Standalone notes', question: 'Open questions', paper: 'Papers', fragment: 'Fragments' };

  function settingsSection(say) {
    const sec = el('section', { class: 'ls-sec', id: 'ls-privacy' },
      el('div', { class: 'ls-h', text: 'Privacy' }),
      el('p', { class: 'ls-p', text: 'Private items never go to an AI agent (chat, critique, Research, review checks) and are left out of exports. Search, similarity and the graph keep working: they run on this computer. Use the lock in a note’s header to change one item.' }));
    const kindsBox = el('div', { class: 'pv-kinds' });
    const local = el('input', { type: 'checkbox' });
    const counts = el('p', { class: 'ls-p' });
    const logBox = el('div', { class: 'pv-log-wrap' });
    sec.append(el('div', { class: 'ls-p', text: 'Private by default (an item can still be marked either way):' }), kindsBox,
      el('label', { class: 'ls-check' }, local, el('span', { text: 'Let a local model server (Ollama, LM Studio, … on this computer) read private items. Cloud agents never do.' })),
      counts, el('div', { class: 'ls-h', text: 'What was sent to AI' }),
      el('p', { class: 'ls-p', text: 'Every request to an AI agent, newest first: when, which agent and model, what for, which items, and how big. The log holds ids only, never content (state/ai_log.jsonl in your library).' }),
      logBox);
    let cur = null;
    async function save(patch, msg) {
      try { cur = await call('PUT', '/api/privacy/settings', patch); paint(); say(msg); const r = rb(); if (r && r.refreshGraph) r.refreshGraph(); if (r && r.reloadCur) r.reloadCur(); }
      catch (e) { say(e.message, true); }
    }
    function paint() {
      kindsBox.replaceChildren();
      const on = new Set(cur.settings.private_by_default);
      for (const k of cur.kinds) {
        const c = el('input', { type: 'checkbox' }); c.checked = on.has(k);
        c.addEventListener('change', () => {
          const next = new Set(cur.settings.private_by_default); if (c.checked) next.add(k); else next.delete(k);
          save({ private_by_default: [...next] }, `${KIND_LABEL[k] || k} ${c.checked ? 'are private by default now' : 'are shareable by default now'}.`);
        });
        kindsBox.append(el('label', { class: 'ls-check' }, c, el('span', { text: KIND_LABEL[k] || k })));
      }
      local.checked = !!cur.settings.private_ok_for_local_ai;
      counts.textContent = `${cur.counts.private} of ${cur.counts.total} items are private.`;
    }
    local.addEventListener('change', () => save({ private_ok_for_local_ai: local.checked },
      local.checked ? 'Local model servers may read private items.' : 'No AI agent reads private items.'));
    async function loadLog() {
      let data;
      try { data = await call('GET', '/api/privacy/log?limit=100'); } catch (e) { logBox.textContent = e.message; return; }
      if (!data.entries.length) { logBox.replaceChildren(el('p', { class: 'ls-p', text: 'Nothing has been sent yet.' })); return; }
      const rows = data.entries.map((e) => {
        const items = el('td');
        (e.items || []).slice(0, 8).forEach((id, i) => {
          if (i) items.append(', ');
          const a = el('a', { text: data.titles[id] || id, title: id, onclick: () => { const r = rb(); if (r && r.openNote) r.openNote(id, { center: true }); } });
          if ((e.private_items || []).includes(id)) a.classList.add('p');
          items.append(a);
        });
        if ((e.items || []).length > 8) items.append(` and ${e.items.length - 8} more`);
        const when = new Date(e.at);
        return el('tr', null, el('td', { text: isNaN(when) ? e.at : when.toLocaleString() }),
          el('td', { text: (e.agent_name || e.agent) + (e.model ? ` (${e.model})` : '') }), el('td', { text: e.purpose }), items,
          el('td', { text: `${Math.round((e.chars || 0) / 100) / 10}k chars` }));
      });
      logBox.replaceChildren(el('table', { class: 'pv-log' },
        el('thead', null, el('tr', null, ...['When', 'Agent', 'For', 'Items', 'Size'].map((t) => el('th', { text: t })))),
        el('tbody', null, ...rows)));
    }
    call('GET', '/api/privacy').then((d) => { cur = d; paint(); }).catch((e) => { counts.textContent = e.message; });
    loadLog();
    return sec;
  }

  window.__rbPrivacy = { settingsSection, renderLock };
})();
