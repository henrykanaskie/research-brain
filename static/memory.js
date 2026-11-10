/* research_brain — the Memory view (vault/memory/).
 * Two things Research mode reads before it searches:
 *   Library map (core.md): written by the app from your library (regions, key notes, recent notes,
 *     reading queue, where the PDFs are). Read-only here, because the app regenerates it.
 *   Findings (findings.md): short notes Claude saved during Research, each labelled "Added via AI"
 *     with its sources. You can edit or delete any of them; Claude can only add.
 * Opens from "Memory" in the chat header or the command palette ("Open memory").
 * Endpoints: GET /api/memory, PUT /api/memory/findings/{id}, DELETE /api/memory/findings/{id}.
 * Depends on window.__rb from app.js.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { h, api, toast, toastErr, renderMD, ago } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);

  const CSS = `
  .mm { position: fixed; inset: var(--topbar-h) 0 0 0; z-index: 45; background: var(--sheet); overflow-y: auto; animation: mmIn .15s ease-out; }
  @keyframes mmIn { from { opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .mm { animation: none; } }
  .mm-col { width: min(1080px, 100%); margin: 0 auto; padding: 32px 32px 96px; }
  .mm-head { display: flex; align-items: center; gap: 12px; min-height: 32px; }
  .mm-head h1 { font: 400 28px/1.15 var(--serif); letter-spacing: -.015em; margin: 0; }
  .mm-head .spacer { flex: 1; }
  .mm-lede { color: var(--ink-2); max-width: 70ch; margin: 10px 0 0; line-height: 1.55; }
  .mm-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 48px; margin-top: 40px; }
  @media (max-width: 900px) { .mm-grid { grid-template-columns: 1fr; } }
  .mm-sec h2 { font: 600 13px var(--sans); margin: 0 0 4px; }
  .mm-sec .sub { color: var(--ink-2); font-size: 12.5px; margin: 0 0 16px; }
  .mm-map { font-size: 14.5px; }
  .mm-map h1 { display: none; }
  .mm-map h2 { font: 600 13px var(--sans); margin: 22px 0 6px; color: var(--ink); }
  .mm-map h3 { font: 500 13px var(--sans); color: var(--ink-2); margin: 14px 0 4px; }
  .mm-map ul { margin: 0; padding-left: 16px; }
  .mm-map li { margin: 2px 0; }
  .mm-empty { color: var(--ink-2); padding: 24px 0; line-height: 1.55; }
  .mm-f { padding: 14px 0 16px; border-top: 1px solid var(--rule); }
  .mm-f:first-of-type { border-top: 0; padding-top: 0; }
  .mm-f .meta { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 12px; font-size: 12px; color: var(--ink-2); }
  .mm-f .ai { color: var(--machine); font-weight: 500; }
  .mm-f .txt { font: 15.5px/1.55 var(--serif); margin: 6px 0 8px; border-left: 2px solid var(--machine); padding-left: 12px; }
  .mm-f .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .mm-chip { all: unset; cursor: pointer; font-size: 12px; color: var(--ink); border: 1px solid var(--rule); border-radius: 3px; padding: 2px 8px; max-width: 32ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mm-chip:hover { background: var(--ground); }
  .mm-chip.gone { color: var(--ink-2); text-decoration: line-through; cursor: default; }
  .mm-chip .pg { color: var(--ink-2); margin-left: 4px; font-variant-numeric: tabular-nums; }
  .mm-f .acts { display: flex; gap: 4px; margin-top: 8px; }
  .mm-f textarea { width: 100%; min-height: 84px; font: 15px/1.5 var(--serif); padding: 8px 10px; margin: 6px 0 4px; }
  .mm-f .count { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .mm-f .count.over { color: var(--red); }
  .mm-summary { margin-top: 24px; color: var(--ink-2); font-size: 13px; }
  .mm-summary li { margin: 3px 0; }
  .btn-danger-text { color: var(--red); }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  let view = null, data = null, editing = null;

  async function load() {
    try { data = await api('GET', '/api/memory'); }
    catch (e) { toastErr(e, "Couldn't load memory"); data = data || { core: '', findings: [], summary: [] }; }
    paint();
  }

  function open() {
    if (view) { load(); return; }
    view = h('section', { class: 'mm', 'aria-label': 'Memory', tabindex: '-1' });
    document.body.appendChild(view);
    view.focus();
    paint();
    load();
  }
  function close() { if (view) { view.remove(); view = null; editing = null; } }
  const isOpen = () => !!view;

  function chip(s) {
    if (!s.exists) return h('span', { class: 'mm-chip gone', title: `${s.id} is no longer in the library`, text: s.id });
    const pdfPage = s.page ? s.page : null;
    return h('button', { type: 'button', class: 'mm-chip', title: `${s.title}${pdfPage ? `, page ${pdfPage}` : ''}`,
      onclick: () => { close(); rb.openNote(s.id, { center: true }); } },
      s.title || s.id, pdfPage ? h('span', { class: 'pg', text: `p. ${pdfPage}` }) : null);
  }

  function finding(f) {
    const box = h('article', { class: 'mm-f', 'data-id': f.id });
    const meta = h('div', { class: 'meta' }, h('span', { class: 'ai', text: data.label || 'Added via AI' }),
      h('span', { class: 'tnum', text: f.date }), f.edited ? h('span', { text: `edited ${f.edited}` }) : null);
    if (editing === f.id) {
      const ta = h('textarea', { 'aria-label': 'Finding text', maxlength: 400 });
      ta.value = f.text;
      const count = h('span', { class: 'count' });
      const upd = () => { count.textContent = `${ta.value.length} / 400`; count.classList.toggle('over', ta.value.length > 400); };
      ta.addEventListener('input', upd); upd();
      const save = async () => {
        try {
          await api('PUT', `/api/memory/findings/${encodeURIComponent(f.id)}`, { text: ta.value });
          editing = null; toast('Finding saved', 'info'); load();
        } catch (e) { toastErr(e, "Couldn't save"); }
      };
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); editing = null; paint(); }
      });
      box.append(meta, ta, h('div', { class: 'acts' },
        h('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Save', onclick: save }),
        h('button', { type: 'button', class: 'btn btn-text btn-sm', text: 'Cancel', onclick: () => { editing = null; paint(); } }), count),
        h('div', { class: 'chips' }, f.sources.map(chip)));
      setTimeout(() => ta.focus(), 0);
      return box;
    }
    box.append(meta, h('div', { class: 'txt md', html: renderMD(f.text) }), h('div', { class: 'chips' }, f.sources.map(chip)),
      h('div', { class: 'acts' },
        h('button', { type: 'button', class: 'btn btn-text btn-sm', text: 'Edit', onclick: () => { editing = f.id; paint(); } }),
        h('button', { type: 'button', class: 'btn btn-text btn-sm btn-danger-text', text: 'Delete', onclick: () => del(f) })));
    return box;
  }

  async function del(f) {
    const ok = rb.confirmDialog ? await rb.confirmDialog('Delete this finding?', 'Claude saved it during Research. Deleting removes it from memory for good.', 'Delete')
      : window.confirm('Delete this finding?');
    if (!ok) return;
    try { await api('DELETE', `/api/memory/findings/${encodeURIComponent(f.id)}`); toast('Finding deleted', 'info'); load(); }
    catch (e) { toastErr(e, "Couldn't delete"); }
  }

  /** core.md lists `[[id]] Title (year, PDF)` for Claude; the chip already shows the title, so drop the repeat. */
  function mapForView(md) {
    return String(md).replace(/^(- \[\[[a-z0-9][a-z0-9-]*\]\]) [^\n]*?( \([^()\n]*\))?$/gm, '$1$2');
  }
  function paint() {
    if (!view) return;
    const d = data;
    const head = h('div', { class: 'mm-head' }, h('h1', { text: 'Memory' }), h('div', { class: 'spacer' }),
      h('button', { type: 'button', class: 'btn btn-sm', text: 'Close', title: 'Close (Esc)', onclick: close }));
    const lede = h('p', { class: 'mm-lede', text: 'What Research mode reads first, to know where to look. The map is written by the app from your library; findings are short notes Claude saved while researching, and you can edit or delete any of them. Both live in vault/memory/.' });
    if (!d) { view.replaceChildren(h('div', { class: 'mm-col' }, head, lede, h('div', { class: 'mm-empty', text: 'Loading…' }))); return; }
    const when = d.updated ? (ago ? ago(new Date(d.updated * 1000).toISOString()) : '') : '';
    const mapSec = h('section', { class: 'mm-sec' }, h('h2', { text: 'Library map' }),
      h('p', { class: 'sub', text: `Generated from your library${when ? `, updated ${when}` : ''}. Read-only.` }),
      d.core ? h('div', { class: 'mm-map md', html: renderMD(mapForView(d.core)) }) : h('div', { class: 'mm-empty', text: 'The map appears once your library has notes.' }));
    const fs = d.findings || [];
    const findSec = h('section', { class: 'mm-sec' }, h('h2', { text: `Findings${fs.length ? ` (${fs.length})` : ''}` }),
      h('p', { class: 'sub', text: 'Added via AI during Research. Each lists the notes it came from.' }),
      fs.length ? fs.map(finding) : h('div', { class: 'mm-empty', text: 'No findings yet. When Claude finds something worth keeping during Research, it saves one short, sourced sentence here.' }),
      (d.summary || []).length ? h('div', { class: 'mm-summary' }, h('h2', { text: 'Earlier findings, summarised' }),
        h('ul', {}, d.summary.map((s) => h('li', { html: renderMD(s, { inline: true }) })))) : null);
    const top = view.scrollTop;
    view.replaceChildren(h('div', { class: 'mm-col' }, head, lede, h('div', { class: 'mm-grid' }, mapSec, findSec)));
    view.scrollTop = top;
  }

  // entry points: a "Memory" button in the chat header, Esc to close, and commands
  const chatHead = $('#chat .chat-head');
  if (chatHead) {
    const b = h('button', { id: 'btn-memory', type: 'button', class: 'btn btn-text btn-sm', text: 'Memory', title: 'The library map and findings Research mode reads', onclick: open });
    const clear = $('#btn-chat-clear');
    chatHead.insertBefore(b, clear || null);
  }
  const C = window.RB && window.RB.commands;
  if (C && C.layer) { try { C.layer({ id: 'memory', order: 80, isOpen: () => isOpen() && !editing, close }); } catch (e) { console.error(e); } }
  else document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isOpen() && !editing) { e.preventDefault(); close(); } });
  if (C && C.register) {
    try {
      C.register({ id: 'memory.open', title: 'Open memory', group: 'Research', keys: 'G then M',
        description: 'The library map and the findings Claude saved during Research. Edit or delete findings here.',
        aliases: ['findings', 'library map', 'core', 'remember'], run: () => (isOpen() ? close() : open()) });
    } catch (e) { console.error(e); }
  }

  window.__rbMemory = { open, close, isOpen, reload: load };
})();
