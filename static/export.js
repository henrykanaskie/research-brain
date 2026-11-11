/* research_brain — "Export" (View menu): writes a read-only HTML reading list to vault/exports/.
 * Exports the current search matches when a search is active, otherwise asks before exporting
 * everything. Uses window.__rb (S) from app.js; the dialog reuses the app's .modal styles.
 */
(() => {
  'use strict';
  const btn = document.getElementById('btn-export');
  if (!btn) return;
  const rb = () => window.__rb || null;

  const CSS = `
  .ex-card { width: min(440px, 100%); }
  .ex-field { display: flex; flex-direction: column; gap: 6px; margin-top: 16px; font-size: 12px; color: var(--ink-2); }
  .ex-field input[type=text] { height: 32px; padding: 0 10px; border: 1px solid var(--rule); border-radius: 3px;
    background: var(--sheet); color: var(--ink); font: 13px var(--sans); }
  .ex-field input[type=text]:focus { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: 1px; }
  .ex-check { display: flex; gap: 10px; align-items: flex-start; margin-top: 12px; font-size: 13px; color: var(--ink); cursor: pointer; }
  .ex-check input { accent-color: var(--ink); margin: 2px 0 0; }
  .ex-path { font: 11.5px/1.5 var(--mono); color: var(--ink); overflow-wrap: anywhere; background: var(--ground);
    padding: 8px 10px; margin-top: 12px; border-radius: 3px; user-select: all; }
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

  function toast(msg, kind = 'info', ms) {
    const box = document.getElementById('toasts');
    if (!box) return;
    const t = el('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, el('span', { class: 't-msg', text: msg }));
    const kill = () => t.remove();
    t.addEventListener('click', kill);
    box.appendChild(t);
    setTimeout(kill, ms || (kind === 'error' ? 9000 : 4200));
  }

  function closeViewMenu() {
    const m = document.getElementById('view-menu');
    if (m && !m.hidden) { m.hidden = true; document.getElementById('btn-view-menu').setAttribute('aria-expanded', 'false'); }
  }

  /** What to export: the search matches when a search is active, else everything. */
  function selection() {
    const S = rb() && rb().S;
    const q = S && (S.search || '').trim();
    if (q) {
      const ids = (S.ranked || []).map((n) => n.id);
      return { ids, label: ids.length === 1 ? '1 note' : `${ids.length} notes`, title: `Reading list: ${q}`, search: q };
    }
    let n = 0;
    if (S && S.nodes) for (const x of S.nodes.values()) if (x.type !== 'fragment') n++;
    return { ids: null, label: n === 1 ? '1 note' : `${n} notes`, title: 'Reading list', count: n };
  }

  let open = null;
  function dialog(sel) {
    if (open) open.close(false);
    return new Promise((resolve) => {
      const heading = sel.search ? `Export ${sel.label} matching “${sel.search}”?` : `Export all ${sel.label}?`;
      const name = el('input', { type: 'text', value: sel.title, spellcheck: 'false', 'aria-label': 'Title' });
      const pdfs = el('input', { type: 'checkbox' });
      const priv = el('input', { type: 'checkbox' }); // private notes (app/privacy.py) are left out unless ticked
      const ok = el('button', { class: 'btn btn-primary', type: 'submit', text: 'Export' });
      const cancel = el('button', { class: 'btn', type: 'button', text: 'Cancel' });
      const form = el('form', { class: 'modal-card ex-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'ex-title' },
        el('div', { class: 'modal-title', id: 'ex-title', text: heading }),
        el('div', { class: 'modal-text', text: 'Writes a read-only website (summaries, notes, fragments and math) to the exports folder in your vault. Nothing is uploaded. Claude’s critiques are left out.' }),
        el('label', { class: 'ex-field' }, 'Title', name),
        el('label', { class: 'ex-check' }, pdfs, el('span', null, 'Include PDFs', el('span', { class: 'menu-hint', text: ' Only share PDFs you’re allowed to redistribute.' }))),
        el('label', { class: 'ex-check' }, priv, el('span', null, 'Include private notes', el('span', { class: 'menu-hint', text: ' Left out unless you tick this.' }))),
        el('div', { class: 'modal-actions' }, cancel, ok));
      const wrap = el('div', { class: 'modal', id: 'export-modal' }, form);
      const prev = document.activeElement;
      const close = (v) => {
        wrap.remove(); document.removeEventListener('keydown', onKey, true); open = null;
        if (prev && prev.focus) prev.focus();
        resolve(v);
      };
      const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(false); } };
      cancel.addEventListener('click', () => close(false));
      wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(false); });
      form.addEventListener('submit', (e) => { e.preventDefault(); close({ title: name.value.trim() || sel.title, include_pdfs: pdfs.checked, include_private: priv.checked }); });
      document.addEventListener('keydown', onKey, true);
      document.body.appendChild(wrap);
      open = { close };
      setTimeout(() => ok.focus(), 0);
    });
  }

  function done(r) {
    const path = el('div', { class: 'ex-path', text: r.index });
    const copy = el('button', { class: 'btn', type: 'button', text: 'Copy path' });
    const close = el('button', { class: 'btn btn-primary', type: 'button', text: 'Done' });
    const parts = [`${r.notes} ${r.notes === 1 ? 'page' : 'pages'}`];
    if (r.fragments) parts.push(`${r.fragments} ${r.fragments === 1 ? 'fragment' : 'fragments'}`);
    if (r.pdfs) parts.push(`${r.pdfs} ${r.pdfs === 1 ? 'PDF' : 'PDFs'}`);
    if (r.private_skipped) parts.push(`${r.private_skipped} private left out`);
    const card = el('div', { class: 'modal-card ex-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'ex-done' },
      el('div', { class: 'modal-title', id: 'ex-done', text: 'Export ready' }),
      el('div', { class: 'modal-text', text: `${parts.join(', ')}. Open index.html in a browser, or zip the folder to share it.` }),
      path,
      el('div', { class: 'modal-actions' }, copy, close));
    const wrap = el('div', { class: 'modal', id: 'export-modal' }, card);
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); kill(); } };
    const kill = () => { wrap.remove(); document.removeEventListener('keydown', onKey, true); };
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(r.path); copy.textContent = 'Copied'; }
      catch { const s = window.getSelection(); const rg = document.createRange(); rg.selectNodeContents(path); s.removeAllRanges(); s.addRange(rg); copy.textContent = 'Selected'; }
    });
    close.addEventListener('click', kill);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) kill(); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(wrap);
    setTimeout(() => close.focus(), 0);
  }

  async function run() {
    closeViewMenu();
    const sel = selection();
    if (sel.search && !sel.ids.length) { toast('Nothing matches that search, so there is nothing to export.'); return; }
    if (!sel.search && !sel.count) { toast('Your library is empty. Add a paper or note first.'); return; }
    const opts = await dialog(sel);
    if (!opts) return;
    btn.disabled = true;
    try {
      const r = await fetch('/api/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: sel.ids, include_pdfs: opts.include_pdfs, include_private: !!opts.include_private, title: opts.title }),
      });
      let data = null; try { data = await r.json(); } catch { /* not JSON */ }
      if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
      done(data);
    } catch (e) {
      toast(`Export failed. ${e.message}`, 'error');
    } finally { btn.disabled = false; }
  }

  btn.addEventListener('click', (e) => { e.stopPropagation(); run(); });
  window.__rbExport = { run, selection };
})();
