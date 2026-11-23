/* research_brain: one paper, one PDF: when a paper you added without its PDF (from a DOI, a search...) and the PDF
 * you imported later became two papers, join them (app/pdfmerge.py).
 *
 * From a paper's panel ("Use a PDF already in your library…" without a PDF, "This is the PDF of…" with one) or a
 * dot's right-click menu on the map: a picker lists the papers it could be joined with (GET
 * /api/notes/{id}/pdf-matches: with a PDF for a paper without one, and the other way round), closest title first,
 * with authors and year to tell them apart. After you confirm, POST /api/notes/{a}/take-pdf {from: b}: the paper
 * without a PDF gets it, the separate copy is removed, its highlights, notes and links move over.
 * window.__rbPdfMerge = { pick(note) }.
 */
(() => {
  'use strict';
  const rb = () => window.__rb || null;
  const CMD = window.RB && window.RB.commands;

  const CSS = `
  .pm-card { width: min(600px, 100%); max-height: calc(100vh - 48px); display: flex; flex-direction: column; padding-bottom: 14px; }
  .pm-lead { color: var(--ink-2); font: 12.5px/1.5 var(--sans); margin: 0 0 10px; }
  .pm-search { height: 32px; width: 100%; padding: 0 10px; border: 1px solid var(--rule); border-radius: var(--r); background: var(--sheet); color: var(--ink); font: 13px var(--sans); }
  .pm-list { list-style: none; margin: 8px 0 0; padding: 0; overflow: auto; min-height: 120px; max-height: 50vh; border-top: 1px solid var(--rule); }
  .pm-list li { padding: 7px 6px; border-bottom: 1px solid var(--rule); cursor: pointer; }
  .pm-list li.on, .pm-list li:hover { background: var(--ground); }
  .pm-t { font: 13px/1.35 var(--serif); color: var(--ink); }
  .pm-s { font: 11.5px/1.4 var(--sans); color: var(--ink-2); margin-top: 2px; }
  .pm-empty { color: var(--ink-3); font: 12.5px var(--sans); padding: 14px 4px; cursor: default !important; }
  .pm-bar { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  const enc = encodeURIComponent;
  const clip = (s, n) => { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  /** "Kajita 2025": the first author's surname and the year, else the start of the title. */
  function shortName(p) {
    const a = String((p.authors || [])[0] || '').trim();
    const sur = a.includes(',') ? a.split(',')[0].trim() : a.split(/\s+/).pop();
    if (sur) return `${sur}${(p.authors || []).length > 1 ? ' et al.' : ''}${p.year ? ' ' + p.year : ''}`;
    return `“${clip(p.title || p.id, 50)}”`;
  }
  function sub(p) {
    const a = (p.authors || []).slice(0, 3).join(', ') + ((p.authors || []).length > 3 ? ' et al.' : '');
    return [a, p.year, p.venue].filter(Boolean).join(' · ') || 'No authors or year saved';
  }

  let pickerEl = null;
  function closePicker() { if (pickerEl) { pickerEl.remove(); pickerEl = null; } }
  if (CMD && CMD.layer) CMD.layer({ id: 'pdfmerge.picker', order: 95, isOpen: () => !!pickerEl, close: closePicker });

  /** Open the picker for paper `n` (a panel note or a map dot). */
  function pick(n) {
    const R = rb();
    if (!R || !n || n.type !== 'paper') return;
    closePicker();
    const h = R.h;
    const hasPdf = !!n.has_pdf;
    const input = h('input', { class: 'pm-search', type: 'search', placeholder: 'Search your papers by title, author or year', 'aria-label': 'Search your papers' });
    const list = h('ul', { class: 'pm-list', role: 'listbox' });
    let rows = [], idx = 0, seq = 0;
    const draw = () => {
      list.replaceChildren();
      if (!rows.length) {
        list.append(h('li', { class: 'pm-empty', text: input.value.trim() ? 'No papers match.'
          : hasPdf ? 'Every other paper in your library already has a PDF.' : 'No other paper in your library has a PDF.' }));
      }
      rows.forEach((p, i) => {
        const li = h('li', { role: 'option', class: i === idx ? 'on' : '', 'data-id': p.id, 'aria-selected': i === idx ? 'true' : 'false' },
          h('div', { class: 'pm-t', text: p.title }), h('div', { class: 'pm-s', text: sub(p) }));
        li.addEventListener('click', () => choose(p));
        list.append(li);
      });
    };
    async function load() {
      const my = ++seq;
      try {
        const r = await R.api('GET', `/api/notes/${enc(n.id)}/pdf-matches?q=${enc(input.value.trim())}`);
        if (my !== seq) return;
        rows = r.items || []; idx = 0; draw();
      } catch (e) { if (my === seq) list.replaceChildren(h('li', { class: 'pm-empty', text: `Could not list your papers. ${e.message}` })); }
    }
    let t = null;
    input.addEventListener('input', () => { clearTimeout(t); t = setTimeout(load, 150); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (rows.length) { idx = (idx + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length; draw(); const on = list.querySelector('li.on'); if (on) on.scrollIntoView({ block: 'nearest' }); }
      } else if (e.key === 'Enter' && rows[idx]) { e.preventDefault(); choose(rows[idx]); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePicker(); }
    });

    async function choose(p) {
      // a: the paper without a PDF (it stays), b: the one with the PDF (it is removed)
      const [a, b] = hasPdf ? [p, n] : [n, p];
      closePicker();
      const text = `${shortName(a)} gets this PDF. The separate copy, “${clip(b.title || b.id, 70)}”, is removed: its highlights, notes, links and regions move over to ${shortName(a)}.\n\n`
        + `${shortName(a)} keeps its own title and details; blank ones are filled in from the copy.`;
      const ok = await R.confirmDialog(`Join them into one paper?`, text, 'Join them');
      if (ok !== true) return;
      try {
        const r = await R.api('POST', `/api/notes/${enc(a.id)}/take-pdf`, { from: b.id });
        R.toast(`${shortName(r)} has its PDF now`, 'ok');
        if (R.refreshGraph) await R.refreshGraph();
        await R.openNote(a.id, { center: true });
      } catch (e) { R.toastErr(e, 'Could not join them'); }
    }

    const card = h('div', { class: 'modal-card pm-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'pm-title' },
      h('div', { class: 'modal-title', id: 'pm-title', text: hasPdf ? 'This is the PDF of…' : 'Use a PDF already in your library' }),
      h('p', { class: 'pm-lead', text: hasPdf
        ? `Pick the paper this PDF belongs to: one you added without its PDF. It gets this PDF, and “${clip(n.title, 60)}” is removed, its notes and links moving over.`
        : `Pick the paper in your library that is this paper's PDF. “${clip(n.title, 60)}” gets that PDF, and the separate copy is removed, its notes and links moving over.` }),
      input, list,
      h('div', { class: 'pm-bar' }, h('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: closePicker })));
    const wrap = h('div', { class: 'modal', id: 'pdf-merge-picker' }, card);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) closePicker(); });
    document.body.appendChild(wrap);
    pickerEl = wrap;
    draw(); load();
    input.focus();
  }

  window.__rbPdfMerge = { pick };
})();
