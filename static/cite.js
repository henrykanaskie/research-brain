/* research_brain — citing: the Cite picker (Alt+C), the Cite menu in a paper's or note's panel, topic
 * bibliographies, and the "Citations" section of Library settings.
 *
 * Copies put text/html (formatted, for Word, Google Docs, Pages) and text/plain (for LaTeX, Markdown) on
 * the clipboard together. No live-updating Word/Docs plugin: paste the bibliography again after changes,
 * or point an auto-export .bib file at your LaTeX project (Library settings, Citations).
 * Endpoints: /api/cite/*, /api/export/bibtex|ris|csl-json (see API.md "Citing").
 */
(() => {
  'use strict';
  const rb = () => window.__rb || null;
  const CMD = window.RB && window.RB.commands;

  const CSS = `
  .ct-card { width: min(640px, 100%); max-height: calc(100vh - 48px); display: flex; flex-direction: column; padding-bottom: 14px; }
  .ct-card .modal-title { font-family: var(--display, var(--serif)); }
  .ct-search { height: 32px; width: 100%; padding: 0 10px; border: 1px solid var(--rule); border-radius: var(--r); background: var(--sheet); color: var(--ink); font: 13px var(--sans); }
  .ct-list { list-style: none; margin: 8px 0 0; padding: 0; overflow: auto; min-height: 120px; max-height: 44vh; border-top: 1px solid var(--rule); }
  .ct-list li { display: flex; gap: 10px; align-items: flex-start; padding: 7px 4px; border-bottom: 1px solid var(--rule); cursor: pointer; }
  .ct-list li.on { background: var(--ground); }
  .ct-list li.sel .ct-t { color: var(--ink); font-weight: 600; }
  .ct-list input { accent-color: var(--red); margin: 3px 0 0; }
  .ct-t { font: 13px/1.35 var(--serif); color: var(--ink); }
  .ct-s { font: 11.5px/1.4 var(--sans); color: var(--ink-2); margin-top: 2px; }
  .ct-k { font: 11px var(--mono); color: var(--machine); }
  .ct-empty { color: var(--ink-3); font: 12.5px var(--sans); padding: 14px 4px; }
  .ct-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
  .ct-bar select { height: 28px; border: 1px solid var(--rule); border-radius: var(--r); background: var(--sheet); color: var(--ink); font: 12.5px var(--sans); max-width: 220px; }
  .ct-n { color: var(--ink-2); font: 12px var(--sans); flex: 1; }
  .ct-prev { margin-top: 10px; font: 12.5px/1.5 var(--serif); color: var(--ink-2); max-height: 96px; overflow: auto; }
  .ct-menu { width: 300px; }
  .ct-menu .ct-key { display: flex; gap: 6px; align-items: center; padding: 6px 8px; font: 12px var(--sans); color: var(--ink-2); }
  .ct-menu .ct-key input { flex: 1; min-width: 0; height: 26px; padding: 0 6px; border: 1px solid var(--rule); border-radius: var(--r); background: var(--sheet); color: var(--ink); font: 12px var(--mono); }
  .ls-cite-list { list-style: none; margin: 6px 0 0; padding: 0; }
  .ls-cite-list li { display: flex; gap: 8px; align-items: baseline; padding: 6px 0; border-bottom: 1px solid var(--rule); font: 12.5px/1.45 var(--sans); }
  .ls-cite-list .p { font: 11.5px var(--mono); color: var(--ink); overflow-wrap: anywhere; flex: 1; }
  .ls-cite-list .w { color: var(--ink-3); font-size: 11.5px; white-space: nowrap; }
  .ls-cite-list .e { color: var(--red); font-size: 11.5px; }
  .ls-row select { height: 30px; border: 1px solid var(--rule); border-radius: var(--r); background: var(--sheet); color: var(--ink); font: 13px var(--sans); max-width: 100%; }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  // ---------------------------------------------------------------- helpers
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
  function toast(msg, kind = 'info') {
    const t = rb() && rb().toast;
    if (t) return t(msg, kind);
    const box = document.getElementById('toasts');
    if (box) { const n = el('div', { class: `toast ${kind}` }, el('span', { class: 't-msg', text: msg })); box.appendChild(n); setTimeout(() => n.remove(), 5000); }
  }
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    let data = null; try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) { const e = new Error((data && data.detail) || `HTTP ${r.status}`); e.status = r.status; throw e; }
    return data;
  }
  const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const put = (path, body) => api(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let STY = null; // {styles, default, copy}
  async function styles(fresh) {
    if (!STY || fresh) STY = await api('/api/cite/styles');
    return STY;
  }
  const styleTitle = (id) => { const s = STY && STY.styles.find((x) => x.id === id); return s ? (s.short || s.title) : id; };

  /** Put html + text on the clipboard. `make` returns (a promise of) {html, text}; the clipboard write
   *  starts synchronously so it stays inside the click (Safari, Chrome). */
  function copyRich(make) {
    const job = Promise.resolve().then(make);
    const w = navigator.clipboard && navigator.clipboard.write && window.ClipboardItem;
    if (w) {
      try {
        const item = new ClipboardItem({
          'text/plain': job.then((r) => new Blob([r.text || ''], { type: 'text/plain' })),
          'text/html': job.then((r) => new Blob([r.html || esc(r.text || '')], { type: 'text/html' })),
        });
        return navigator.clipboard.write([item]).then(() => job).catch(async (e) => { const r = await job; await fallbackCopy(r); return r; });
      } catch { /* old ClipboardItem without promise values */ }
    }
    return job.then(async (r) => { await fallbackCopy(r); return r; });
  }
  async function fallbackCopy(r) {
    if (!r.html && navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(r.text || ''); return; } catch { /* below */ }
    }
    const box = el('div', { contenteditable: 'true', style: 'position:fixed;left:-9999px;top:0;white-space:pre-wrap' });
    if (r.html) box.innerHTML = r.html; else box.textContent = r.text || '';
    document.body.appendChild(box);
    const range = document.createRange(); range.selectNodeContents(box);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    const onCopy = (e) => { e.preventDefault(); e.clipboardData.setData('text/plain', r.text || ''); if (r.html) e.clipboardData.setData('text/html', r.html); };
    document.addEventListener('copy', onCopy, true);
    let ok = false; try { ok = document.execCommand('copy'); } catch { ok = false; }
    document.removeEventListener('copy', onCopy, true);
    sel.removeAllRanges(); box.remove();
    if (!ok) throw new Error('The browser didn’t allow copying. Select the text and press Ctrl/⌘+C.');
  }

  // what: 'latex' | 'citation' | 'bibliography'.  scope: {ids} | {topic} | {note}
  async function render(what, scope, styleId) {
    if (what === 'latex') {
      const r = await post('/api/cite/keys', scope);
      if (!r.latex) throw new Error('Nothing to cite here.');
      return { text: r.latex, html: '', label: r.latex };
    }
    const s = styleId || (await styles()).default;
    const r = await post('/api/cite/format', Object.assign({ style: s, mode: what, format: 'html' }, scope));
    if (!r.text) throw new Error(what === 'bibliography' ? 'No papers to put in a bibliography.' : 'Nothing to cite here.');
    return { text: r.text, html: r.html, label: what === 'citation' ? r.text : `${r.entries.length} ${r.entries.length === 1 ? 'reference' : 'references'} (${styleTitle(s)})` };
  }
  function copy(what, scope, styleId) {
    return copyRich(() => render(what, scope, styleId)).then((r) => {
      toast(what === 'latex' ? `Copied ${r.label}` : what === 'citation' ? `Copied the citation ${r.label}` : `Copied the bibliography: ${r.label}. Paste it into Word, Google Docs or your editor.`);
      return r;
    }).catch((e) => { toast(`Couldn’t copy. ${e.message}`, 'error'); throw e; });
  }
  function download(scope, flavor = 'bibtex', fmt = 'bibtex') {
    const q = new URLSearchParams();
    if (scope.ids) q.set('ids', scope.ids.join(','));
    if (scope.topic) q.set('topic', scope.topic);
    if (scope.note) q.set('note', scope.note);
    if (scope.all) q.set('all', '1');
    if (fmt === 'bibtex') q.set('flavor', flavor);
    const a = el('a', { href: `/api/export/${fmt}?${q}`, download: '' });
    document.body.appendChild(a); a.click(); a.remove();
  }

  // ---------------------------------------------------------------- picker (Alt+C)
  let pickerEl = null;
  function closePicker() { if (pickerEl) { pickerEl.remove(); pickerEl = null; } }
  async function openPicker(preselect = []) {
    closePicker();
    let st;
    try { st = await styles(true); } catch (e) { toast(`Couldn’t load citation styles. ${e.message}`, 'error'); return; }
    const sel = new Map(); // id -> hit
    for (const p of preselect) sel.set(p.id, p);
    let hits = [], idx = 0, seq = 0;
    const input = el('input', { class: 'ct-search', type: 'search', placeholder: 'Search papers by title, author or year', spellcheck: 'false', 'aria-label': 'Search papers to cite', autocomplete: 'off' });
    const list = el('ul', { class: 'ct-list', role: 'listbox', 'aria-multiselectable': 'true', 'aria-label': 'Papers' });
    const count = el('span', { class: 'ct-n' });
    const styleSel = el('select', { 'aria-label': 'Citation style' }, ...st.styles.filter((s) => !s.error).map((s) => el('option', { value: s.id, text: s.short ? `${s.short} · ${s.title}` : s.title })));
    styleSel.value = st.default;
    const prev = el('div', { class: 'ct-prev', 'aria-live': 'polite' });
    const bLatex = el('button', { class: 'btn btn-sm', type: 'button', text: 'Copy \\cite{…}', title: 'LaTeX: \\cite{key1,key2}. The keys are saved with the papers, so they never change.' });
    const bCit = el('button', { class: 'btn btn-sm', type: 'button', text: 'Copy citation', title: 'The in-text citation in the chosen style, e.g. (Doe, 2024) or [1]' });
    const bBib = el('button', { class: 'btn btn-sm', type: 'button', text: 'Copy bibliography', title: 'The reference list in the chosen style, formatted for Word and Google Docs' });
    const primary = st.copy === 'citekey' ? bLatex : bBib;
    primary.classList.add('btn-primary');
    const scope = () => ({ ids: Array.from(sel.keys()) });
    const need = () => { if (!sel.size) { toast('Pick at least one paper first.'); input.focus(); return false; } return true; };
    bLatex.addEventListener('click', () => { if (need()) copy('latex', scope()).then(closePicker, () => {}); });
    bCit.addEventListener('click', () => { if (need()) copy('citation', scope(), styleSel.value).then(closePicker, () => {}); });
    bBib.addEventListener('click', () => { if (need()) copy('bibliography', scope(), styleSel.value).then(closePicker, () => {}); });
    styleSel.addEventListener('change', () => { preview(); put('/api/cite/settings', { style: styleSel.value }).then(() => { if (STY) STY.default = styleSel.value; }).catch(() => {}); });

    function sub(h) {
      const a = (h.authors || []).join(', ') + (h.more_authors ? ' et al.' : '');
      return [a, h.year, h.venue].filter(Boolean).join(' · ');
    }
    function draw() {
      const rows = hits.slice();
      for (const h of sel.values()) if (!rows.some((x) => x.id === h.id)) rows.unshift(h);
      list.replaceChildren();
      if (!rows.length) list.append(el('li', { class: 'ct-empty', text: input.value.trim() ? 'No papers match.' : 'No papers in your library yet.' }));
      rows.forEach((h, i) => {
        const cb = el('input', { type: 'checkbox', tabindex: '-1', 'aria-hidden': 'true' }); cb.checked = sel.has(h.id);
        const li = el('li', { role: 'option', 'aria-selected': sel.has(h.id) ? 'true' : 'false', class: (i === idx ? 'on ' : '') + (sel.has(h.id) ? 'sel' : ''), 'data-id': h.id },
          cb, el('div', null, el('div', { class: 'ct-t', text: h.title }), el('div', { class: 'ct-s' }, sub(h), h.citekey ? ' · ' : '', h.citekey ? el('span', { class: 'ct-k', text: h.citekey }) : '')));
        li.addEventListener('click', () => { idx = i; toggle(h); });
        list.append(li);
      });
      list.__rows = rows;
      count.textContent = sel.size ? `${sel.size} selected` : 'Click papers to select them';
    }
    function toggle(h) { if (sel.has(h.id)) sel.delete(h.id); else sel.set(h.id, h); draw(); preview(); }
    let pt = null;
    function preview() {
      clearTimeout(pt);
      if (!sel.size) { prev.textContent = ''; return; }
      pt = setTimeout(async () => {
        try { const r = await post('/api/cite/format', { ids: Array.from(sel.keys()), style: styleSel.value, mode: 'bibliography', format: 'html' }); prev.innerHTML = r.html; }
        catch (e) { prev.textContent = e.message; }
      }, 120);
    }
    let searching = Promise.resolve();
    function search() { searching = runSearch(); return searching; }
    async function runSearch() {
      const my = ++seq;
      try {
        const r = await api('/api/cite/search?' + new URLSearchParams({ q: input.value.trim(), limit: '30' }));
        if (my !== seq) return;
        hits = r; idx = 0; draw();
      } catch (e) { if (my === seq) list.replaceChildren(el('li', { class: 'ct-empty', text: `Search failed. ${e.message}` })); }
    }
    let t = null;
    input.addEventListener('input', () => { clearTimeout(t); searching = new Promise((res) => { t = setTimeout(() => search().then(res, res), 120); }); });
    // keys inside the picker (window capture: before the page's shortcuts)
    const onKey = (e) => {
      if (!pickerEl) return;
      const rows = list.__rows || [];
      const mod = e.metaKey || e.ctrlKey;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePicker(); return; }
      if (document.activeElement !== input) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); e.stopPropagation();
        if (rows.length) { idx = (idx + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length; draw(); const on = list.querySelector('li.on'); if (on) on.scrollIntoView({ block: 'nearest' }); }
      } else if (e.key === 'Enter' && mod) { e.preventDefault(); e.stopPropagation(); primary.click(); }
      else if (e.key === 'Enter') { // the results for what was just typed, not the previous ones
        e.preventDefault(); e.stopPropagation();
        searching.then(() => { const r = list.__rows || []; if (r[idx]) toggle(r[idx]); });
      }
    };
    window.addEventListener('keydown', onKey, true);
    const card = el('div', { class: 'modal-card ct-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'ct-title' },
      el('div', { class: 'modal-title', id: 'ct-title', text: 'Cite' }),
      input, list,
      el('div', { class: 'ct-bar' }, count, styleSel),
      prev,
      el('div', { class: 'ct-bar' }, el('span', { class: 'ct-n', text: 'Enter selects · ⌘/Ctrl+Enter copies' }), bLatex, bCit, bBib,
        el('button', { class: 'btn btn-text btn-sm', type: 'button', text: '.bib', title: 'Download the selected papers as a BibTeX file', onclick: () => { if (need()) download(scope()); } })));
    const wrap = el('div', { class: 'modal', id: 'cite-picker' }, card);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) closePicker(); });
    const obs = new MutationObserver(() => { if (!document.body.contains(wrap)) { window.removeEventListener('keydown', onKey, true); obs.disconnect(); } });
    document.body.appendChild(wrap);
    obs.observe(document.body, { childList: true });
    pickerEl = wrap;
    draw(); preview(); search();
    input.focus();
  }

  // ---------------------------------------------------------------- the Cite menu (paper / note panel, topics)
  let menuEl = null;
  function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
  document.addEventListener('mousedown', (e) => { if (menuEl && !menuEl.contains(e.target) && !(e.target.closest && e.target.closest('.ct-btn'))) closeMenu(); }, true);

  async function openMenu(anchor, n) {
    if (menuEl) { const same = menuEl.__for === n.id; closeMenu(); if (same) return; }
    const isPaper = n.type === 'paper';
    const scope = isPaper ? { ids: [n.id] } : { note: n.id };
    const st = await styles().catch(() => null);
    const sname = st ? styleTitle(st.default) : '';
    const item = (label, hint, fn) => el('button', { class: 'menu-item', type: 'button', role: 'menuitem', onclick: () => { closeMenu(); fn(); } }, label, hint ? el('span', { class: 'menu-hint', text: hint }) : null);
    const kids = [];
    if (isPaper) {
      kids.push(item('Copy \\cite{…}', 'LaTeX key, saved with the paper', () => copy('latex', scope).catch(() => {})),
        item('Copy citation', sname, () => copy('citation', scope).catch(() => {})),
        item('Copy reference', `${sname}, for Word and Google Docs`, () => copy('bibliography', scope).catch(() => {})),
        item('Download .bib', 'BibTeX entry', () => download(scope)),
        item('Cite with other papers…', 'Opens the picker with this one selected', () => openPicker([{ id: n.id, title: n.title, year: n.year, authors: (n.authors || []).slice(0, 3) }])));
      const key = el('input', { type: 'text', spellcheck: 'false', 'aria-label': 'Citation key' });
      const saveKey = async () => {
        const v = key.value.trim();
        if (!v || v === key.dataset.was) { key.value = key.dataset.was || ''; return; }
        try { const r = await put(`/api/cite/keys/${encodeURIComponent(n.id)}`, { citekey: v }); key.value = key.dataset.was = r.citekey; toast(`Citation key set to ${r.citekey}`); }
        catch (e) { key.value = key.dataset.was || ''; toast(e.message, 'error'); }
      };
      key.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); saveKey(); } });
      key.addEventListener('blur', saveKey);
      post('/api/cite/keys', scope).then((r) => { key.value = key.dataset.was = r.keys[n.id] || ''; }).catch(() => {});
      kids.push(el('div', { class: 'menu-sep' }), el('label', { class: 'ct-key', title: 'Edit to use your own key; it is saved in the note’s citekey property' }, 'Key', key));
    } else {
      kids.push(el('div', { class: 'menu-label', text: 'Papers this note links to with [[ ]]' }),
        item('Copy bibliography', `${sname}, for Word and Google Docs`, () => copy('bibliography', scope).catch(() => {})),
        item('Copy \\cite{…}', 'All their LaTeX keys', () => copy('latex', scope).catch(() => {})),
        item('Export .bib', 'BibTeX file of those papers', () => download(scope)));
    }
    const m = el('div', { class: 'menu ct-menu', role: 'menu' }, ...kids);
    m.__for = n.id;
    const wrap = anchor.parentElement;
    wrap.classList.add('menu-wrap');
    wrap.style.position = 'relative';
    m.style.top = `${anchor.offsetTop + anchor.offsetHeight + 6}px`;
    m.style.left = `${Math.max(0, Math.min(anchor.offsetLeft, wrap.clientWidth - 300))}px`;
    wrap.appendChild(m);
    menuEl = m;
    const first = m.querySelector('.menu-item'); if (first) first.focus();
  }

  function panelHook(n, pbody) {
    if (!n || n.type === 'fragment') return;
    if (n.type === 'note' && !((n.mentions || []).some((x) => x.type !== 'note') || (n.links || []).length)) return;
    if (pbody.querySelector('.ct-btn')) return;
    const btn = el('button', { class: 'btn ct-btn', type: 'button', 'aria-haspopup': 'menu', text: 'Cite', title: n.type === 'paper' ? 'Copy a citation, reference or \\cite{key}; download .bib' : 'Bibliography of the papers this note links to' });
    btn.addEventListener('click', (e) => { e.stopPropagation(); openMenu(btn, n); });
    if (CMD && CMD.tooltip) CMD.tooltip(btn, 'cite.paper');
    let acts = pbody.querySelector('.actions');
    if (!acts) {
      acts = el('div', { class: 'actions' });
      const anchor = pbody.querySelector('.ids') || pbody.querySelector('.meta') || pbody.querySelector('textarea.title-input');
      if (anchor) anchor.after(acts); else pbody.prepend(acts);
    }
    acts.append(btn);
  }

  function topicTools(slug) {
    return [
      el('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Copy bibliography', title: 'The papers in this topic as a reference list in your default style', onclick: () => copy('bibliography', { topic: slug }).catch(() => {}) }),
      el('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Export .bib', title: 'Download this topic’s papers as BibTeX. For a file that updates itself, add an auto-export in Library settings › Citations.', onclick: () => download({ topic: slug }) }),
    ];
  }

  // ---------------------------------------------------------------- Library settings › Citations
  function settingsSection(say) {
    const sec = el('section', { class: 'ls-sec', id: 'ls-citations' }, el('div', { class: 'ls-h', text: 'Citations' }));
    const body = el('div', null, el('p', { class: 'ls-p', text: 'Loading…' }));
    sec.append(body);
    fill(body, say).catch((e) => { body.replaceChildren(el('p', { class: 'ls-p', text: `Couldn’t load. ${e.message}` })); });
    return sec;
  }
  async function fill(body, say) {
    const [st, ax, topics] = await Promise.all([styles(true), api('/api/cite/auto-exports'), api('/api/topics').catch(() => [])]);
    const styleSel = el('select', { 'aria-label': 'Default citation style' }, ...st.styles.map((s) => el('option', { value: s.id, text: (s.kind === 'user' ? 'Yours: ' : '') + s.title + (s.error ? ' (can’t be used)' : ''), disabled: !!s.error })));
    styleSel.value = st.default;
    styleSel.addEventListener('change', async () => {
      try { await put('/api/cite/settings', { style: styleSel.value }); STY.default = styleSel.value; say(`Default style: ${styleTitle(styleSel.value)}.`); } catch (e) { say(e.message, true); }
    });
    const copyRow = el('div');
    for (const [val, label] of [['formatted', 'Formatted citation and bibliography (for Word, Google Docs)'], ['citekey', '\\cite{key} (for LaTeX and Overleaf)']]) {
      const r = el('input', { type: 'radio', name: 'ls-cite-copy', value: val }); r.checked = st.copy === val;
      r.addEventListener('change', async () => { if (!r.checked) return; try { await put('/api/cite/settings', { copy: val }); STY.copy = val; say(val === 'citekey' ? 'Cite copies \\cite{key} first.' : 'Cite copies formatted text first.'); } catch (e) { say(e.message, true); } });
      copyRow.append(el('label', { class: 'ls-check' }, r, el('span', { text: label })));
    }
    // your own styles
    const file = el('input', { type: 'file', accept: '.csl,application/xml,text/xml', hidden: true });
    const addStyle = el('button', { class: 'btn', type: 'button', text: 'Add a .csl style…' });
    addStyle.addEventListener('click', () => file.click());
    file.addEventListener('change', async () => {
      const f = file.files && file.files[0]; file.value = '';
      if (!f) return;
      if (f.size > 1024 * 1024) { say('That file is larger than 1 MB; it isn’t a CSL style.', true); return; }
      try { const s = await post('/api/cite/styles', { filename: f.name, content: await f.text() }); say(`Added “${s.title}”.`); fill(body, say); }
      catch (e) { say(e.message, true); }
    });
    const mine = st.styles.filter((s) => s.kind === 'user');
    const mineList = el('ul', { class: 'ls-cite-list' }, ...mine.map((s) => el('li', null, el('span', { class: 'p', text: s.title }), s.error ? el('span', { class: 'e', text: s.error }) : null,
      el('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Remove', onclick: async () => { try { await api(`/api/cite/styles/${encodeURIComponent(s.id)}`, { method: 'DELETE' }); fill(body, say); } catch (e) { say(e.message, true); } } }))));
    // auto-exports
    const list = el('ul', { class: 'ls-cite-list', id: 'ls-auto-exports' });
    const when = (iso) => { if (!iso) return 'not written yet'; const a = rb() && rb().ago; return 'written ' + (a ? a(iso) : new Date(iso).toLocaleString()); };
    for (const e of ax.exports) {
      list.append(el('li', null, el('span', { class: 'p', text: e.path }),
        el('span', { class: 'w', text: `${e.scope === 'all' ? 'whole library' : 'topic ' + e.scope.slice(6)} · ${e.flavor === 'bibtex' ? 'BibTeX' : 'BibLaTeX'} · ${when(e.written)}` }),
        e.error ? el('span', { class: 'e', text: e.error }) : null,
        el('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Remove', title: 'Stop updating this file (the file itself stays)', onclick: async () => { try { await api(`/api/cite/auto-exports/${encodeURIComponent(e.id)}`, { method: 'DELETE' }); say('Stopped. The .bib file was left where it is.'); fill(body, say); } catch (er) { say(er.message, true); } } })));
    }
    if (!ax.exports.length) list.append(el('li', null, el('span', { class: 'w', text: 'None yet.' })));
    const path = el('input', { type: 'text', placeholder: '/Users/you/thesis/references.bib', spellcheck: 'false', 'aria-label': 'Path of the .bib file' });
    const scopeSel = el('select', { 'aria-label': 'What to export' }, el('option', { value: 'all', text: 'Whole library' }), ...topics.map((t) => el('option', { value: `topic:${t.slug}`, text: `Topic: ${t.name}` })));
    const flavorSel = el('select', { 'aria-label': 'BibTeX format' }, el('option', { value: 'biblatex', text: 'BibLaTeX (biber, UTF-8)' }), el('option', { value: 'bibtex', text: 'BibTeX (ASCII, LaTeX accents)' }));
    const add = el('button', { class: 'btn', type: 'button', text: 'Add auto-export' });
    add.addEventListener('click', async () => {
      if (!path.value.trim()) { path.focus(); return; }
      try {
        const r = await post('/api/cite/auto-exports', { path: path.value.trim(), scope: scopeSel.value, flavor: flavorSel.value });
        say(r.error ? `Saved, but: ${r.error}` : `Wrote ${r.entries} ${r.entries === 1 ? 'entry' : 'entries'} to ${r.path}. It updates whenever the papers change.`, !!r.error);
        fill(body, say);
      } catch (e) { say(e.message, true); }
    });
    body.replaceChildren(...[
      el('p', { class: 'ls-p', text: 'Cite papers with Alt+C (or the Cite button on a paper). Copies are formatted for Word and Google Docs and plain for LaTeX. There is no live-updating Word or Docs plugin: after changes, paste the bibliography again, or use Zotero’s plugin for live citations.' }),
      el('div', { class: 'ls-row' }, el('span', { text: 'Default style' }), styleSel),
      el('div', { class: 'ls-h', text: 'Cite copies first', style: 'margin-top:12px' }), copyRow,
      el('div', { class: 'ls-h', text: 'Your styles', style: 'margin-top:12px' }),
      el('p', { class: 'ls-p', text: `Add any of the 10,000+ CSL styles from zotero.org/styles (download the .csl file). They are kept in ${st.user_dir}.` }),
      mine.length ? mineList : null, el('div', { class: 'ls-row' }, addStyle, file),
      el('div', { class: 'ls-h', text: 'Auto-updating .bib files', style: 'margin-top:12px' }),
      el('p', { class: 'ls-p', text: 'research brain rewrites the file whenever a paper in it changes, with stable citation keys, so LaTeX and Overleaf (via git or Dropbox) always see the current library. It writes only that one file and never overwrites a .bib it didn’t create.' }),
      list, el('div', { class: 'ls-row' }, path), el('div', { class: 'ls-row' }, scopeSel, flavorSel, add)].filter(Boolean));
  }

  // ---------------------------------------------------------------- wiring
  const tryHook = () => { const a = rb(); if (a && a.onPanelRender) { a.onPanelRender(panelHook); return true; } return false; };
  if (!tryHook()) window.addEventListener('load', tryHook, { once: true });

  if (CMD) {
    CMD.register({ id: 'cite.pick', title: 'Cite papers…', group: 'Library', keys: 'Alt+C', allowInInput: true,
      description: 'Search your library, pick papers, copy \\cite{…}, a formatted citation or a bibliography.',
      aliases: ['cite', 'citation', 'reference', 'bibliography', 'bibtex', 'latex', 'word', 'google docs', 'apa', 'aps', 'ieee'],
      when: () => !pickerEl, run: () => { openPicker(); } });
    CMD.register({ id: 'cite.paper', title: 'Cite this paper', group: 'Note',
      description: 'Copy a citation, reference or \\cite{key} for the open paper; download its .bib.',
      aliases: ['cite', 'citation', 'reference', 'bibtex', 'key'],
      when: () => { const S = rb() && rb().S; return !!(S && S.cur && S.cur.type === 'paper'); },
      run: () => { const b = document.querySelector('#panel .ct-btn'); if (b) b.click(); } });
    CMD.register({ id: 'cite.exportLibrary', title: 'Export the library as .bib', group: 'Library',
      description: 'Download every paper as a BibTeX file (stable citation keys).',
      aliases: ['bibtex', 'biblatex', 'export', 'bib', 'references', 'latex'], run: () => download({ all: true }) });
    CMD.register({ id: 'cite.settings', title: 'Citation settings', group: 'General',
      description: 'Default style, what Cite copies, your own .csl styles and auto-updating .bib files.',
      aliases: ['citations', 'style', 'csl', 'bibtex', 'auto export', 'bib'], run: () => { const s = window.__rbSettings; if (s) s.open('citations'); } });
    if (CMD.layer) {
      CMD.layer({ id: 'cite-picker', order: 100, isOpen: () => !!pickerEl, close: closePicker });
      CMD.layer({ id: 'cite-menu', order: 90, isOpen: () => !!menuEl, close: closeMenu });
    }
  }

  window.__rbCite = { open: openPicker, close: closePicker, copy, render, download, topicTools, settingsSection, styles, copyRich };
})();
