/* research_brain — writing help on a note (POST /api/notes/<id>/write).
 * Without AI: cite papers (Alt+C) and the bibliography of the papers the draft links to (static/cite.js).
 * With AI, under the same rules as everywhere: you tick the sources it may read (the papers and notes your
 * draft links to, plus any you add); private ones can't be ticked; your draft goes only if you tick it and
 * it isn't private; the line under the button says how much will be sent before you send. The answer cites
 * the passages it used; "Add below my draft" appends it, nothing is replaced.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb || !rb.h || !rb.onPanelRender) return;
  const { h, api, toast, toastErr, truncate } = rb;
  const enc = encodeURIComponent;

  document.head.appendChild(h('style', { text: `
  .wr-sec .wr-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
  .wr-sec .wr-chips button { background: none; border: 1px solid var(--rule); border-radius: 12px; padding: 2px 9px; font: 12.5px var(--sans); color: var(--ink-2); cursor: pointer; }
  .wr-sec .wr-chips button:hover { color: var(--ink); border-color: var(--ink-2); }
  .wr-sec textarea { width: 100%; font: 14px/1.5 var(--sans); padding: 6px 8px; border: 1px solid var(--rule-strong); background: var(--sheet); color: var(--ink); resize: vertical; }
  .wr-src { font-size: 13px; margin: 6px 0; }
  .wr-src label { display: flex; gap: 8px; align-items: baseline; padding: 1px 0; }
  .wr-src .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wr-src .k { color: var(--ink-3); font-size: 12px; }
  .wr-add { width: 100%; font: 13px var(--sans); padding: 4px 6px; border: 1px solid var(--rule); background: transparent; color: var(--ink); }
  .wr-sugg { border: 1px solid var(--rule); background: var(--sheet); max-height: 180px; overflow: auto; }
  .wr-sugg button { display: block; width: 100%; text-align: left; background: none; border: 0; padding: 4px 8px; font: 13px var(--sans); color: var(--ink); cursor: pointer; }
  .wr-sugg button:hover { background: var(--tint); }
  .wr-row { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
  .wr-size { color: var(--ink-2); font-size: 12px; margin-left: auto; }
  .wr-out { margin-top: 10px; border-left: 1px solid var(--machine); padding-left: 12px; font: 15px/1.6 var(--serif); }
  .wr-off { color: var(--ink-3); font-size: 12.5px; }
  ` }));

  const CHIPS = ['Turn my bullet points into a paragraph', 'Draft a related-work paragraph from these sources',
    'Check my claims against the sources', 'Tighten this without changing the meaning', 'Suggest where I need citations'];

  function section(n, body) {
    const box = h('div', { class: 'section wr-sec', id: 'wr-sec' });
    const head = h('div', { class: 'sec-head' }, h('span', { class: 'sec-title', text: 'Writing help' }));
    const cite = h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Cite papers… (Alt+C)', title: 'Search your library and copy \\cite{}, an in-text citation or a bibliography. No AI.',
      onclick: () => window.__rbCite && window.__rbCite.open() });
    const req = h('textarea', { rows: 2, placeholder: 'What would you like help with?', 'aria-label': 'What you want help with' });
    const chips = h('div', { class: 'wr-chips' }, CHIPS.map((c) => h('button', { type: 'button', text: c, onclick: () => { req.value = c; req.focus(); refresh(); } })));
    const draft = h('input', { type: 'checkbox', id: `wr-draft-${n.id}` });
    const draftLab = h('label', { class: 'wr-src', for: draft.id }, draft, h('span', { text: ' Let it read my draft (or the part I selected)' }));
    const srcBox = h('div', { class: 'wr-src' });
    const add = h('input', { class: 'wr-add', type: 'text', placeholder: '+ Add a source from your library', 'aria-label': 'Add a source', spellcheck: 'false' });
    const sugg = h('div', { class: 'wr-sugg', hidden: true });
    const size = h('span', { class: 'wr-size' });
    const go = h('button', { class: 'btn', type: 'button', text: 'Ask AI' });
    const out = h('div');
    let chosen = null;      // ids; null until the first preview fills it from the draft's links
    const unticked = new Set();
    let selection = '';
    let pv = null, timer = 0;

    const bodyEl = () => body.querySelector('textarea.body-field');
    const grabSel = () => {
      const t = bodyEl();
      selection = t && t.selectionEnd > t.selectionStart ? t.value.slice(t.selectionStart, t.selectionEnd) : '';
    };
    const refresh = () => { clearTimeout(timer); timer = setTimeout(preview, 200); };
    async function preview() {
      grabSel();
      const sources = chosen ? chosen.filter((i) => !unticked.has(i)) : null;
      try {
        pv = await api('POST', `/api/notes/${enc(n.id)}/write`, { dry: true, request: req.value, sources, include_draft: draft.checked, selection });
      } catch (e) { size.textContent = e.message; return; }
      if (chosen === null) chosen = pv.sources.map((s) => s.id);
      draw();
    }
    function draw() {
      const all = chosen.map((id) => pv.sources.find((s) => s.id === id) || { id, title: id, allowed: true, unticked: true });
      srcBox.replaceChildren(h('div', { class: 'k', text: all.length ? 'Sources it may read:' : 'No sources yet. Link papers in your draft with [[ ]], or add some below.' }),
        ...all.map((s) => {
          const b = h('input', { type: 'checkbox', disabled: !s.allowed, 'aria-label': s.title });
          b.checked = s.allowed && !unticked.has(s.id);
          b.addEventListener('change', () => { if (b.checked) unticked.delete(s.id); else unticked.add(s.id); refresh(); });
          return h('label', { title: s.allowed ? '' : 'Private: it never goes to an AI. Mark it shareable (the lock in its header) to use it.' },
            b, h('span', { class: 't', text: s.title }), h('span', { class: 'k', text: s.allowed ? (s.type || '') : 'private' }));
        }));
      draft.disabled = !pv.draft.allowed;
      if (!pv.draft.allowed) draft.checked = false;
      draftLab.title = pv.draft.why || '';
      draftLab.querySelector('span').textContent = pv.draft.allowed ? (selection ? ' Let it read the part I selected' : ' Let it read my draft')
        : ' Your draft is private, so it isn’t sent (the lock in its header)';
      size.textContent = `About ${pv.tokens.toLocaleString()} tokens${pv.passages ? `, ${pv.passages} passages` : ''}`;
    }
    add.addEventListener('input', async () => {
      const q = add.value.trim();
      if (q.length < 2) { sugg.hidden = true; return; }
      let hits = [];
      try { hits = await api('GET', `/api/search?q=${enc(q)}&limit=8`); } catch { }
      const list = (Array.isArray(hits) ? hits : hits.results || []).filter((x) => x.id !== n.id && !(chosen || []).includes(x.id));
      sugg.replaceChildren(...list.map((x) => h('button', { type: 'button', text: `${truncate(x.title || x.id, 80)}${x.private ? ' (private)' : ''}`,
        onclick: () => { (chosen || (chosen = [])).push(x.id); add.value = ''; sugg.hidden = true; refresh(); } })));
      sugg.hidden = !list.length;
    });
    req.addEventListener('input', refresh);
    draft.addEventListener('change', refresh);
    const t = bodyEl();
    if (t) t.addEventListener('mouseup', () => setTimeout(refresh, 0));
    go.addEventListener('click', async () => {
      if (!req.value.trim()) { req.focus(); return; }
      grabSel();
      go.disabled = true; go.textContent = 'Writing…';
      try {
        const r = await api('POST', `/api/notes/${enc(n.id)}/write`, { request: req.value, sources: (chosen || []).filter((i) => !unticked.has(i)), include_draft: draft.checked, selection });
        const md = h('div', { class: 'wr-out md' });
        md.innerHTML = rb.renderMD(r.text || '');
        const ins = h('button', { class: 'btn btn-sm', type: 'button', text: 'Add below my draft', onclick: async () => {
          try {
            const cur = await api('GET', `/api/notes/${enc(n.id)}`);
            await api('PUT', `/api/notes/${enc(n.id)}`, { body: `${(cur.body || '').replace(/\s+$/, '')}\n\n${r.text}` });
            toast('Added below your draft.', 'ok'); rb.reloadCur && rb.reloadCur();
          } catch (e) { toastErr(e, 'Could not add it'); }
        } });
        const cp = h('button', { class: 'btn btn-sm btn-text', type: 'button', text: 'Copy', onclick: () => navigator.clipboard.writeText(r.text).then(() => toast('Copied.', 'ok')) });
        out.replaceChildren(md, h('div', { class: 'wr-row' }, ins, cp));
      } catch (e) { toastErr(e, 'The AI couldn’t help with that'); }
      finally { go.disabled = false; go.textContent = 'Ask AI'; }
    });
    box.append(head, h('div', { class: 'wr-row' }, cite, h('span', { class: 'wr-off', text: 'citations and bibliographies, no AI' })),
      h('div', { class: 'k wr-off', text: 'Ask your AI agent, with only what you choose:' }), chips, req, srcBox, add, sugg, draftLab,
      h('div', { class: 'wr-row' }, go, size), out);
    preview();
    return box;
  }

  rb.onPanelRender((n, body) => {
    if (!n || n.type !== 'note') return;
    const old = body.querySelector('#wr-sec');
    if (old) old.remove();
    const danger = body.querySelector('.danger-zone');
    const sec = section(n, body);
    if (danger) danger.before(sec); else body.append(sec);
  });
})();
