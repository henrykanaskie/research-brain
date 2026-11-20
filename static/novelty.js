/* research_brain — Novelty check.
 * "Check novelty…" on an open question (its AI section), the command novelty.check (the open note, or the text
 * selected in it) and "Check novelty" on the right-click menu of selected text in the panel.
 * 1. The preview: the idea split into parts (by your AI agent when it may read the note) and the search words
 *    that will go to OpenAlex and arXiv. You can edit both. A private note is checked against your library only.
 * 2. It runs in the background (app/novelty.py): searches, follows the citations of the closest hits, scores
 *    every paper against the idea and each part on this computer, and the AI reads the closest abstracts.
 *    You're told when it's done (a toast with Open report).
 * 3. The report: how many papers were examined, very close / overlaps in part / background, which parts each
 *    paper covers (● covers, ◐ touches, ○ not), what none of them does, similar work by year, Add to library.
 * Server: /api/novelty* (app/novelty_api.py), POST /api/import/lookup to add. Needs window.__rb.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { S, h, api, toast, toastErr, truncate, fmtDate } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const enc = encodeURIComponent;
  const CMD = window.RB && window.RB.commands;

  const BAND = { close: 'Very close', partial: 'Overlaps in part', background: 'Background' };
  const BAND_HINT = {
    close: 'Does essentially what the idea says',
    partial: 'Shares some of the idea’s parts',
    background: 'Same area, a different aim; or prior work the close papers keep citing',
  };
  const STAGE = { searching: 'Searching OpenAlex, arXiv and your library', citations: 'Following citations of the closest papers',
    scoring: 'Comparing every paper with your idea', reading: 'Your AI is reading the closest abstracts', done: 'Done' };
  const DOT = ['○', '◐', '●'];
  const DOT_WORD = ['doesn’t cover', 'touches', 'covers'];

  const STYLE = `
  .nv-idea { font: italic 15px/1.5 var(--serif); color: var(--ink-2); margin: 0 0 14px; border-left: 2px solid var(--rule); padding-left: 10px; }
  .nv-h { font: 600 13px var(--sans); color: var(--ink); margin: 18px 0 6px; display: flex; gap: 8px; align-items: baseline; }
  .nv-h .sub { font-weight: 400; color: var(--ink-2); }
  .nv-row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
  .nv-row input { box-sizing: border-box; height: 30px; border: 1px solid var(--rule); border-radius: var(--r, 3px); background: var(--sheet); color: var(--ink); font: 13.5px var(--sans); padding: 0 8px; min-width: 0; }
  .nv-row input.nv-name { width: 110px; flex: none; font-weight: 500; }
  .nv-row input.nv-text { flex: 1; }
  .nv-row .btn { height: 26px; padding: 0 8px; }
  .nv-note { font-size: 12px; color: var(--ink-3); line-height: 1.5; margin: 6px 0 0; }
  .nv-note.warn { color: var(--ink-2); }
  .nv-go { display: flex; gap: 8px; align-items: center; margin-top: 18px; flex-wrap: wrap; }
  .nv-go .sp { flex: 1; }
  .nv-meta { font-size: 12.5px; color: var(--ink-2); font-variant-numeric: tabular-nums; display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
  .nv-meta .sp { flex: 1; }
  .nv-meta .btn { height: 24px; font-size: 12px; }
  .nv-err { color: var(--red); font-size: 13px; margin-top: 8px; }
  .nv-bands { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 14px 0 4px; }
  .nv-band { border: 1px solid var(--rule); border-radius: var(--r, 3px); padding: 8px 10px; background: var(--sheet); }
  .nv-band .n { font: 500 22px/1.1 var(--display, var(--serif)); color: var(--ink); font-variant-numeric: tabular-nums; }
  .nv-band .l { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
  .nv-band.close .n { color: var(--red); }
  .nv-new { font-size: 12.5px; color: var(--red); margin-top: 6px; }
  .nv-verdict { margin-top: 12px; }
  .nv-years { display: flex; align-items: flex-end; gap: 2px; height: 34px; margin-top: 4px; }
  .nv-years span { flex: 1; min-width: 3px; max-width: 14px; background: var(--ink-3); border-radius: 1px 1px 0 0; }
  .nv-years-l { display: flex; justify-content: space-between; font-size: 11px; color: var(--ink-3); font-variant-numeric: tabular-nums; max-width: calc(var(--n, 10) * 16px); }
  .nv-legend { font-size: 12px; color: var(--ink-3); margin: 4px 0 0; }
  .nv-legend b { font-weight: 500; color: var(--ink-2); }
  .nv-paper { padding: 11px 0; border-top: 1px solid var(--rule); }
  .nv-paper:first-of-type { border-top: 0; }
  .nv-title { all: unset; cursor: pointer; font: 15px/1.35 var(--serif); color: var(--ink); }
  .nv-title:hover { text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--ink-2); }
  .nv-by { font-size: 12px; color: var(--ink-2); margin-top: 3px; display: flex; flex-wrap: wrap; column-gap: 10px; }
  .nv-by .new { color: var(--red); font-weight: 500; }
  .nv-parts { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 5px; font-size: 12px; color: var(--ink-2); }
  .nv-parts .d { font-size: 13px; color: var(--ink); margin-right: 3px; }
  .nv-parts .p0 { color: var(--ink-3); }
  .nv-why { font: 13px/1.5 var(--sans); color: var(--machine, var(--ink-2)); margin-top: 5px; }
  .nv-via { font-size: 12px; color: var(--ink-3); margin-top: 3px; }
  .nv-acts { display: flex; gap: 4px; margin-top: 7px; flex-wrap: wrap; }
  .nv-acts .btn { height: 24px; font-size: 12px; padding: 0 8px; }
  details.nv-bg { margin-top: 16px; }
  details.nv-bg > summary { cursor: pointer; font: 600 13px var(--sans); color: var(--ink-2); }
  .nv-past { margin-top: 26px; font-size: 12.5px; color: var(--ink-2); }
  .nv-past button { all: unset; cursor: pointer; display: block; padding: 4px 0; color: var(--ink-2); }
  .nv-past button:hover { color: var(--ink); text-decoration: underline; }
  .nv-past button.on { color: var(--ink); font-weight: 500; }
  .nv-fine { font-size: 12px; color: var(--ink-3); line-height: 1.5; margin-top: 22px; }
  @media (max-width: 560px) { .nv-bands { grid-template-columns: 1fr; } .nv-row input.nv-name { width: 84px; } }
  `;
  const style = document.createElement('style');
  style.id = 'rb-novelty-style';
  style.textContent = STYLE;
  document.head.appendChild(style);

  const N = { body: null, note: null, run: null, runs: [], pollT: null, told: new Set(), watching: new Set() };

  function sheet(title, fill) {
    if (!CMD || !CMD.panel) return;
    clearTimeout(N.pollT);
    CMD.panel({ title, wide: true, className: 'nv-sheet', render: (body) => { N.body = body; fill(body); } });
  }
  const live = () => N.body && N.body.isConnected;
  /** Fill the sheet (skipping the parts that don't apply: null). */
  const put = (...kids) => N.body.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== ''));

  // ====================================================================== opening
  /** Check a note (or the text selected in it). With an earlier check of this note and no selection: its report. */
  async function open(note, selection = '') {
    if (!note || !note.id) { toast('Open a note or question first, then check its novelty', 'info'); return; }
    N.note = { id: note.id, title: note.title || '' };
    let runs = [];
    try { runs = (await api('GET', `/api/novelty?note_id=${enc(note.id)}`)).runs || []; } catch { /* none */ }
    N.runs = runs;
    if (runs.length && !selection.trim()) { showRun(runs[0]); return; }
    preview(selection);
  }

  async function openRun(noteId, rid) {
    try {
      N.runs = (await api('GET', `/api/novelty?note_id=${enc(noteId)}`)).runs || [];
      const r = N.runs.find((x) => x.id === rid) || await api('GET', `/api/novelty/runs/${enc(rid)}`);
      N.note = { id: noteId, title: r.title || '' };
      showRun(r);
    } catch (e) { toastErr(e, 'Could not open the report'); }
  }

  // ====================================================================== 1. the preview
  async function preview(selection = '') {
    sheet('Novelty check', (body) => body.replaceChildren(rb.working ? rb.working('Splitting your idea into parts') : h('div', { text: 'Splitting your idea into parts' })));
    let pl;
    try { pl = await api('POST', '/api/novelty/plan', { note_id: N.note.id, text: selection }); }
    catch (e) {
      if (live()) N.body.replaceChildren(h('div', { class: 'nv-err', text: rb.errText ? rb.errText(e) : e.message }));
      return;
    }
    if (!live()) return;
    const parts = pl.parts.map((p) => ({ ...p }));
    const queries = pl.online ? pl.queries.slice() : [];
    const partsBox = h('div'), qBox = h('div');
    const drawParts = () => partsBox.replaceChildren(...[...parts.map((p, i) => {
      const name = h('input', { class: 'nv-name', type: 'text', maxlength: 40, value: p.name, 'aria-label': `Part ${i + 1} name` });
      const text = h('input', { class: 'nv-text', type: 'text', maxlength: 400, value: p.text, 'aria-label': `Part ${i + 1}` });
      name.value = p.name; text.value = p.text;
      name.oninput = () => { p.name = name.value; };
      text.oninput = () => { p.text = text.value; };
      const rm = h('button', { class: 'btn btn-text', type: 'button', text: '×', title: 'Remove this part', 'aria-label': 'Remove this part',
        disabled: parts.length < 2, onclick: () => { parts.splice(i, 1); drawParts(); } });
      return h('div', { class: 'nv-row' }, name, text, rm);
    }), parts.length < 5 ? h('button', { class: 'btn btn-text', type: 'button', text: 'Add a part', onclick: () => { parts.push({ name: 'Part ' + (parts.length + 1), text: '' }); drawParts(); } }) : null].filter(Boolean));
    const drawQ = () => qBox.replaceChildren(...[...queries.map((q, i) => {
      const inp = h('input', { class: 'nv-text', type: 'text', maxlength: 200, value: q, 'aria-label': `Search ${i + 1}` });
      inp.value = q;
      inp.oninput = () => { queries[i] = inp.value; };
      const rm = h('button', { class: 'btn btn-text', type: 'button', text: '×', title: 'Don’t send this search', 'aria-label': 'Remove this search',
        disabled: queries.length < 2, onclick: () => { queries.splice(i, 1); drawQ(); } });
      return h('div', { class: 'nv-row' }, inp, rm);
    }), queries.length < 5 ? h('button', { class: 'btn btn-text', type: 'button', text: 'Add a search', onclick: () => { queries.push(''); drawQ(); } }) : null].filter(Boolean));
    drawParts(); drawQ();
    const go = h('button', { class: 'btn btn-primary', type: 'button', text: 'Check novelty' });
    go.onclick = async () => {
      const ps = parts.filter((p) => (p.text || '').trim().length >= 3);
      const qs = queries.map((q) => q.trim()).filter((q) => q.length >= 3);
      if (!ps.length) { toast('Give at least one part of the idea', 'info'); return; }
      if (pl.online && !qs.length) { toast('Give at least one search, a few words', 'info'); return; }
      go.disabled = true;
      try {
        const r = await api('POST', '/api/novelty', { note_id: N.note.id, text: selection, parts: ps, queries: qs });
        N.watching.add(r.run.id);
        N.runs = [r.run, ...N.runs];
        N.startToast = toast('Checking novelty. It takes a minute or two: close this and keep working, and you’ll be told when the report is ready.', 'ok', { ms: 6000 });
        showRun(r.run);
      } catch (e) { go.disabled = false; toastErr(e, 'Could not start the check'); }
    };
    const aiLine = pl.ai
      ? (pl.planned_by_ai ? `${aiName()} split the idea into parts. When the search is done it reads the closest abstracts and says what each did compared with your idea.` : `${aiName()} will read the closest abstracts.${pl.plan_error ? ' (It couldn’t split the idea: ' + pl.plan_error + ')' : ''}`)
      : `No AI reading: ${pl.ai_why} Papers are compared with your idea on this computer.`;
    put(
      h('p', { class: 'nv-idea', text: truncate(pl.from_selection ? pl.idea : joined(pl.title, pl.idea), 420) }),
      h('div', { class: 'nv-h' }, 'Parts of the idea', h('span', { class: 'sub', text: 'each paper is compared with each part' })), partsBox,
      pl.online ? h('div', { class: 'nv-h' }, 'Searches', h('span', { class: 'sub', text: 'these words go to OpenAlex and arXiv' })) : null,
      pl.online ? qBox : null,
      pl.online ? h('p', { class: 'nv-note', text: 'Only the searches above leave this computer for the paper indexes. Your library is searched locally too, and the closest papers’ citations are followed.' })
        : h('p', { class: 'nv-note warn', text: pl.online_why }),
      h('p', { class: 'nv-note', text: aiLine }),
      h('div', { class: 'nv-go' }, go, h('span', { class: 'sp' }), N.runs.length ? h('button', { class: 'btn btn-text', type: 'button', text: 'Back to the last report', onclick: () => showRun(N.runs[0]) }) : null));
  }
  const joined = (t, x) => { t = (t || '').trim(); x = (x || '').trim(); return !t || !x ? t || x : `${t}${/[.?!:]$/.test(t) ? '' : '.'} ${x}`; };
  const aiName = () => (window.RB && RB.aiName ? RB.aiName() : 'Your AI');

  // ====================================================================== 3. the report
  function showRun(run) {
    N.run = run;
    if (!live()) sheet('Novelty check', () => {});
    drawRun();
    poll();
  }
  function poll() {
    clearTimeout(N.pollT);
    const r = N.run;
    if (!r || !(r.running || r.status === 'running')) return;
    N.pollT = setTimeout(async () => {
      if (!live()) return;
      try {
        const fresh = await api('GET', `/api/novelty/runs/${enc(r.id)}`);
        if (N.run && N.run.id === fresh.id) { N.run = fresh; N.runs = N.runs.map((x) => (x.id === fresh.id ? fresh : x)); drawRun(); }
      } catch { /* try again */ }
      poll();
    }, 1500);
  }

  function drawRun() {
    if (!live()) return;
    const r = N.run;
    const kids = [h('p', { class: 'nv-idea', text: truncate(joined(r.title, r.idea), 300) })];
    const again = h('button', { class: 'btn btn-text', type: 'button', text: 'New check', title: 'Check again (edit the parts and searches first)', onclick: () => preview('') });
    const forget = h('button', { class: 'btn btn-text', type: 'button', text: 'Forget', title: 'Delete this report',
      onclick: async () => {
        try { await api('DELETE', `/api/novelty/runs/${enc(r.id)}`); N.runs = N.runs.filter((x) => x.id !== r.id); if (N.runs.length) showRun(N.runs[0]); else preview(''); }
        catch (e) { toastErr(e, 'Could not delete it'); }
      } });
    if (r.running || r.status === 'running') {
      kids.push(rb.working ? rb.working(`${STAGE[r.stage] || 'Working'}${r.examined ? ` · ${r.examined} papers so far` : ''}`, Date.parse(r.started_at) || Date.now())
        : h('div', { text: STAGE[r.stage] || 'Working' }),
      h('p', { class: 'nv-note', text: 'You can close this and keep working; you’ll be told when the report is ready.' }));
      put(...kids, pastList());
      return;
    }
    const c = r.counts || {};
    const meta = [fmtDate ? fmtDate(r.finished_at || r.started_at) : '', `${c.examined || r.examined || 0} papers examined (abstracts)`,
      c.via_citations ? `${c.via_citations} found only through citations` : '', c.in_library ? `${c.in_library} of the close or overlapping ones already in your library` : '',
      r.online ? '' : 'your library only', r.tokens ? `~${r.tokens.toLocaleString()} tokens` : ''].filter(Boolean);
    kids.push(h('div', { class: 'nv-meta' }, ...meta.map((m) => h('span', { text: m })), h('span', { class: 'sp' }), again, forget));
    if (r.status === 'error') {
      kids.push(h('div', { class: 'nv-err', text: r.error || 'The check failed.' }));
      put(...kids, pastList());
      return;
    }
    kids.push(h('div', { class: 'nv-bands' }, ...['close', 'partial', 'background'].map((b) =>
      h('div', { class: 'nv-band ' + b, title: BAND_HINT[b] }, h('div', { class: 'n', text: String(c[b] || 0) }), h('div', { class: 'l', text: BAND[b] })))));
    if (c.compared) kids.push(h('div', { class: 'nv-new', text: c.new ? `${c.new} close or overlapping paper${c.new === 1 ? '' : 's'} new since your last check` : 'Nothing close is new since your last check.' }));
    if (r.verdict || r.uncovered) {
      kids.push(h('div', { class: 'machine nv-verdict' },
        h('div', { class: 'machine-head' }, h('span', { text: `${aiName()}, from the abstracts` })),
        r.verdict ? h('p', { text: r.verdict }) : null,
        r.uncovered ? h('div', null, h('b', { text: 'What none of them does: ' }), rb.mdEl ? rb.mdEl(r.uncovered) : r.uncovered) : null));
    }
    const yrs = years(r.years || {});
    if (yrs) kids.push(h('div', { class: 'nv-h' }, 'Similar work by year', h('span', { class: 'sub', text: 'very close and overlapping papers' })), yrs);
    const parts = r.parts || [];
    const papers = r.papers || [];
    if (parts.length > 1 && papers.length) {
      kids.push(h('p', { class: 'nv-legend' }, 'Parts: ', ...parts.flatMap((p, i) => [i ? ' · ' : '', h('b', { text: `${i + 1} ${p.name}` })]), '. ● covers, ◐ touches, ○ doesn’t.'));
    }
    for (const b of ['close', 'partial']) {
      const list = papers.filter((p) => p.band === b);
      if (!list.length) continue;
      kids.push(h('div', { class: 'nv-h' }, BAND[b], h('span', { class: 'sub', text: `${list.length}${(c[b] || 0) > list.length ? ' of ' + c[b] : ''} · ${BAND_HINT[b].toLowerCase()}` })),
        ...list.map((p) => paperRow(p, parts)));
    }
    if (!papers.some((p) => p.band === 'close' || p.band === 'partial')) {
      kids.push(h('p', { class: 'nv-note warn', text: `Nothing close among the ${c.examined || 0} papers examined. That’s from abstracts only, so it is a good sign, not proof: try other words for the searches too.` }));
    }
    const bg = papers.filter((p) => p.band === 'background');
    if (bg.length) kids.push(h('details', { class: 'nv-bg' }, h('summary', { text: `Background (${bg.length})` }), ...bg.map((p) => paperRow(p, parts))));
    kids.push(h('p', { class: 'nv-fine', text: 'From titles and abstracts only. Paywalled, very recent and non-English work can be missing from OpenAlex and arXiv, and a close paper can use words nobody searched for. Treat this as a map of what exists, not a verdict on novelty.' }));
    put(...kids, pastList());
  }

  function years(map) {
    const ks = Object.keys(map).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
    if (ks.length < 2) return null;
    const lo = ks[0], hi = ks[ks.length - 1], max = Math.max(...Object.values(map));
    const bars = [];
    for (let y = lo; y <= hi; y++) {
      const n = map[String(y)] || 0;
      bars.push(h('span', { title: `${y}: ${n}`, style: `height:${n ? Math.max(8, Math.round((n / max) * 100)) : 2}%` }));
    }
    return h('div', null, h('div', { class: 'nv-years', style: `max-width:${bars.length * 16}px` }, ...bars),
      h('div', { class: 'nv-years-l', style: `--n:${bars.length}` }, h('span', { text: String(lo) }), h('span', { text: String(hi) })));
  }

  function paperUrl(p) { return p.url || (p.doi ? `https://doi.org/${p.doi}` : p.arxiv ? `https://arxiv.org/abs/${p.arxiv}` : ''); }

  function paperRow(p, parts) {
    const url = paperUrl(p);
    const openIt = () => {
      if (p.library_id) { CMD && CMD.close(false); rb.openNote(p.library_id, { center: true }); }
      else if (url) window.open(url, '_blank', 'noopener,noreferrer');
    };
    const authors = (p.authors || []).length ? `${p.authors.slice(0, 3).join(', ')}${p.authors.length > 3 ? ' et al.' : ''}` : '';
    const via = [];
    if (p.via && p.via.length === 1 && p.via[0] === 'citations') via.push('Found through citations, not by the searches');
    if (p.cited_by_seeds >= 2) via.push(`cited by ${p.cited_by_seeds} of the closest papers`);
    if (p.cites_seeds >= 2) via.push(`cites ${p.cites_seeds} of the closest papers`);
    const acts = [];
    if (p.library_id) acts.push(h('button', { class: 'btn', type: 'button', text: 'In your library', onclick: openIt }));
    else acts.push(addBtn(p));
    if (url) acts.push(h('a', { class: 'btn btn-text', href: url, target: '_blank', rel: 'noopener noreferrer', text: p.arxiv ? 'arXiv page' : 'Open' }));
    return h('div', { class: 'nv-paper' },
      h('button', { class: 'nv-title', type: 'button', text: p.title || '(untitled)', onclick: openIt, title: p.library_id ? 'Open it in your library' : 'Open the paper’s page' }),
      h('div', { class: 'nv-by' }, authors ? h('span', { text: authors }) : null, p.year ? h('span', { text: String(p.year) }) : null,
        p.venue ? h('span', { text: truncate(p.venue, 60) }) : null, p.new ? h('span', { class: 'new', text: 'New since last check' }) : null),
      parts.length > 1 ? h('div', { class: 'nv-parts' }, ...parts.map((pt, i) => {
        const lvl = (p.parts || [])[i] || 0;
        return h('span', { class: 'p' + lvl, title: `${pt.name}: ${DOT_WORD[lvl]} (${pt.text})` }, h('span', { class: 'd', text: DOT[lvl] }), pt.name);
      })) : null,
      p.why ? h('div', { class: 'nv-why', text: p.why }) : null,
      via.length ? h('div', { class: 'nv-via', text: via.join(' · ') }) : null,
      h('div', { class: 'nv-acts' }, ...acts));
  }

  function addBtn(p) {
    const b = h('button', { class: 'btn btn-primary', type: 'button', text: 'Add to library' });
    b.onclick = async (ev) => {
      const section = rb.chooseSection ? await rb.chooseSection(ev) : null;
      if (section === undefined) return;
      b.disabled = true; b.textContent = 'Adding';
      try {
        const note = await api('POST', '/api/import/lookup', { query: p.doi || (p.arxiv ? 'arXiv:' + p.arxiv : p.title) });
        if (section && rb.intoSection) { try { await rb.intoSection(note, section); } catch { /* added either way */ } }
        p.library_id = note.id;
        b.replaceWith(h('button', { class: 'btn', type: 'button', text: 'In your library', onclick: () => { CMD && CMD.close(false); rb.openNote(note.id, { center: true }); } }));
        toast(`Added “${truncate(note.title || p.title, 60)}”${section ? ` to ${section.label}` : ''}.`, 'ok');
        if (rb.refreshGraph) rb.refreshGraph();
      } catch (e) { b.disabled = false; b.textContent = 'Add to library'; toastErr(e, 'Could not add it'); }
    };
    return b;
  }

  function pastList() {
    if (N.runs.length < 2) return '';
    return h('div', { class: 'nv-past' }, h('div', { class: 'nv-h' }, 'Earlier checks of this note'),
      ...N.runs.map((x) => h('button', { type: 'button', class: N.run && x.id === N.run.id ? 'on' : '', onclick: () => showRun(x),
        text: `${fmtDate ? fmtDate(x.started_at) : x.started_at} · ${x.status === 'done' ? `${(x.counts || {}).close || 0} very close, ${(x.counts || {}).partial || 0} in part` : x.status}` })));
  }

  // ====================================================================== telling you it's done
  window.addEventListener('rb:novelty-done', (e) => {
    const d = e.detail || {};
    if (!d.id || N.told.has(d.id)) return;
    N.told.add(d.id);
    if (N.startToast && N.startToast.remove) { N.startToast.remove(); N.startToast = null; }
    const what = truncate(d.title || 'your idea', 50);
    const c = d.counts || {};
    const failed = d.status === 'error';
    const msg = failed ? `The novelty check of “${what}” didn’t finish: ${d.error || 'something went wrong'}`
      : `Novelty check of “${what}”: ${c.close || 0} very close, ${c.partial || 0} overlapping in part, of ${c.examined || 0} papers.`;
    if (live() && N.run && N.run.id === d.id) {
      api('GET', `/api/novelty/runs/${enc(d.id)}`).then((r) => { N.run = r; N.runs = N.runs.map((x) => (x.id === r.id ? r : x)); drawRun(); }).catch(() => {});
      toast(msg, failed ? 'error' : 'ok');
      return;
    }
    toast(msg, failed ? 'error' : 'ok', { ms: 24 * 3600 * 1000, action: { label: 'Open report', fn: () => openRun(d.note_id, d.id) } });
  });

  // ====================================================================== entry points
  function selectionIn(root) {
    const sel = window.getSelection && window.getSelection();
    const f = document.activeElement && /^(TEXTAREA|INPUT)$/.test(document.activeElement.tagName) ? document.activeElement : null;
    if (f && root && root.contains(f) && typeof f.selectionStart === 'number') return f.value.slice(f.selectionStart, f.selectionEnd);
    if (sel && sel.rangeCount && root && root.contains(sel.anchorNode)) return String(sel);
    return '';
  }
  if (CMD) {
    CMD.register({ id: 'novelty.check', title: 'Check novelty', group: 'Library',
      description: 'How many papers already did something like the open note (or the text selected in it), and how close they came. Runs in the background.',
      aliases: ['novelty', 'prior work', 'has this been done', 'related work', 'similar papers', 'originality'],
      run: () => open(S.cur, selectionIn($('#panel-body'))) });
  }
  window.__rbNovelty = { open, openRun };
})();
