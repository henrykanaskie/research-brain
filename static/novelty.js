/* research_brain — Novelty check, a mode of "Ask your library" next to Quick and Research.
 * You describe an idea and send it (or right-click selected text: "Check novelty", or the command novelty.check).
 * 1. The answer card shows the preview: the idea split into parts (by your AI agent) and the searches that will
 *    go to OpenAlex and arXiv. You can edit both; nothing goes to a paper index until "Check novelty".
 * 2. It runs in the background (app/novelty.py): searches, follows the citations of the closest hits, scores
 *    every paper against the idea and each part on this computer, and the AI reads the closest abstracts. The
 *    card follows it, and you're told when it's done (a toast with Open report when the chat isn't showing it).
 * 3. The card ends as a short report; Full report opens a sheet: very close / overlaps in part / background,
 *    which parts each paper covers (● covers, ◐ touches, ○ not), what none of them does, similar work by year,
 *    Add to library. A started check is saved with the conversation (its run id), so it comes back with it.
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
  .nv-card .nv-h:first-of-type { margin-top: 4px; }
  .nv-card .nv-row input.nv-name { width: 92px; }
  .nv-card .nv-go { margin-top: 10px; }
  .nv-card .nv-meta { margin-bottom: 2px; }
  .nv-bands.sm { gap: 6px; margin: 8px 0; }
  .nv-bands.sm .nv-band { padding: 6px 8px; }
  .nv-bands.sm .n { font-size: 18px; }
  .nv-bands.sm .l { font-size: 11.5px; }
  .nv-verdict-t { margin: 6px 0 4px; font-weight: 500; }
  .nv-unc { margin: 4px 0 8px; }
  .nv-unc .md, .nv-unc p { display: inline; margin: 0; }
  .nv-mini { padding: 6px 0; border-top: 1px solid var(--rule); font: 14px/1.4 var(--serif); }
  .nv-mini a { color: var(--ink); }
  .nv-mini .yr { color: var(--ink-2); font: 12px var(--sans); }
  .nv-mini .nv-why { font-size: 12.5px; margin-top: 3px; }
  .nv-tag { font: 500 10.5px/1 var(--sans); text-transform: uppercase; letter-spacing: .04em; padding: 2px 5px; border-radius: 3px; margin-right: 6px; vertical-align: 2px; border: 1px solid var(--rule); color: var(--ink-2); }
  .nv-tag.close { color: var(--red); border-color: var(--red); }
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

  // ====================================================================== in Ask: the Novelty mode
  // You type the idea and send it. The answer is a card in the chat: first the preview (the parts and the
  // searches, both editable, and what will be sent where), then "Check novelty" starts the run in the
  // background; the card follows it and ends as a short report with Full report.
  const RUNS = new Map();   // run id -> the run, as last fetched
  const joined = (t, x) => { t = (t || '').trim(); x = (x || '').trim(); return !t || !x ? t || x : `${t}${/[.?!:]$/.test(t) ? '' : '.'} ${x}`; };
  const aiName = () => (window.RB && RB.aiName ? RB.aiName() : 'your AI');
  const AIName = () => (window.RB && RB.AIName ? RB.AIName() : 'Your AI');  // to start a sentence with
  const running = (r) => r && (r.running || r.status === 'running');

  function wants(opts) {
    if (opts && opts.mode) return opts.mode === 'novelty';
    return !!(window.__rbResearch && window.__rbResearch.mode === 'novelty');
  }

  /** Put text in Ask's box, in Novelty mode, ready to send (right-click "Check novelty", the command). */
  function compose(text = '') {
    if (window.__rbResearch) window.__rbResearch.setMode('novelty');
    if (rb.openChat) rb.openChat();
    setTimeout(() => {
      const ta = $('#chat-input');
      if (!ta) return;
      if (text.trim()) { ta.value = text.trim(); ta.dispatchEvent(new Event('input')); }
      ta.focus();
    }, 30);
  }

  async function send(text) {
    text = (text || '').trim();
    if (!text) return;
    const msg = { role: 'assistant', mode: 'novelty', stage: 'plan', text, start: Date.now(), content: '' };
    S.chat.push({ role: 'user', content: text }, msg);
    rb.renderChat();
    try {
      msg.plan = await api('POST', '/api/novelty/plan', { text });
      msg.stage = 'preview';
    } catch (e) {
      msg.stage = 'error';
      msg.problem = rb.errText ? rb.errText(e) : e.message;
    }
    rb.renderChat();
  }

  async function start(msg, btn) {
    const pl = msg.plan;
    const parts = pl.parts.filter((p) => (p.text || '').trim().length >= 3);
    const queries = pl.online ? pl.queries.map((q) => q.trim()).filter((q) => q.length >= 3) : [];
    if (!parts.length) { toast('Give at least one part of the idea', 'info'); return; }
    if (pl.online && !queries.length) { toast('Give at least one search, a few words', 'info'); return; }
    btn.disabled = true;
    try {
      const r = await api('POST', '/api/novelty', { text: msg.text, parts, queries });
      RUNS.set(r.run.id, r.run);
      msg.novelty = r.run.id;
      msg.stage = 'run';
      msg.content = `Novelty check started: ${truncate(msg.text, 200)}`;
      if (rb.persistChat) rb.persistChat();
      rb.renderChat();
      poll();
    } catch (e) { btn.disabled = false; toastErr(e, 'Could not start the check'); }
  }

  /** The chat message (static/app.js renderChat calls this for mode "novelty" or a saved message with a run). */
  function renderMsg(m) {
    const card = h('div', { class: 'machine nv-card' });
    const wrap = h('div', { class: 'msg assistant nv-msg', dataset: m.novelty ? { nv: m.novelty } : {} }, card);
    fillCard(card, m);
    return wrap;
  }

  function fillCard(card, m) {
    const head = (t) => h('div', { class: 'machine-head' }, h('span', { text: t }));
    if (m.stage === 'plan') { card.replaceChildren(head('Novelty check'), rb.working('Splitting your idea into parts', m.start)); return; }
    if (m.stage === 'error') { card.replaceChildren(head('Novelty check'), h('div', { class: 'nv-err', text: m.problem || 'Something went wrong.' })); return; }
    if (m.stage === 'preview' && m.plan) { card.replaceChildren(head('Novelty check: what will be checked'), ...previewKids(m)); return; }
    const r = m.novelty && RUNS.get(m.novelty);
    if (!r) {
      card.replaceChildren(head('Novelty check'), h('div', { class: 'nv-note', text: m.content || 'Loading the report' }));
      if (m.novelty) fetchRun(m.novelty);
      return;
    }
    card.replaceChildren(head(running(r) ? 'Novelty check, running' : 'Novelty check'), ...reportKids(r));
  }

  function previewKids(m) {
    const pl = m.plan;
    const partsBox = h('div'), qBox = h('div');
    const drawParts = () => partsBox.replaceChildren(...[...pl.parts.map((p, i) => {
      const name = h('input', { class: 'nv-name', type: 'text', maxlength: 40, 'aria-label': `Part ${i + 1} name` });
      const text = h('input', { class: 'nv-text', type: 'text', maxlength: 400, 'aria-label': `Part ${i + 1}` });
      name.value = p.name; text.value = p.text;
      name.oninput = () => { p.name = name.value; };
      text.oninput = () => { p.text = text.value; };
      const rm = h('button', { class: 'btn btn-text', type: 'button', text: '×', title: 'Remove this part', 'aria-label': 'Remove this part',
        disabled: pl.parts.length < 2, onclick: () => { pl.parts.splice(i, 1); drawParts(); } });
      return h('div', { class: 'nv-row' }, name, text, rm);
    }), pl.parts.length < 5 ? h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Add a part', onclick: () => { pl.parts.push({ name: 'Part ' + (pl.parts.length + 1), text: '' }); drawParts(); } }) : null].filter(Boolean));
    const drawQ = () => qBox.replaceChildren(...[...pl.queries.map((q, i) => {
      const inp = h('input', { class: 'nv-text', type: 'text', maxlength: 200, 'aria-label': `Search ${i + 1}` });
      inp.value = q;
      inp.oninput = () => { pl.queries[i] = inp.value; };
      const rm = h('button', { class: 'btn btn-text', type: 'button', text: '×', title: 'Don’t send this search', 'aria-label': 'Remove this search',
        disabled: pl.queries.length < 2, onclick: () => { pl.queries.splice(i, 1); drawQ(); } });
      return h('div', { class: 'nv-row' }, inp, rm);
    }), pl.queries.length < 5 ? h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Add a search', onclick: () => { pl.queries.push(''); drawQ(); } }) : null].filter(Boolean));
    drawParts(); drawQ();
    const go = h('button', { class: 'btn btn-primary btn-sm', type: 'button', text: 'Check novelty' });
    go.onclick = () => start(m, go);
    const aiLine = pl.ai
      ? (pl.planned_by_ai ? `${AIName()} split the idea into parts. After the search it reads the closest abstracts and says what each did compared with your idea.`
        : `${AIName()} will read the closest abstracts.${pl.plan_error ? ` (It couldn’t split the idea: ${pl.plan_error})` : ''}`)
      : `No AI reading: ${pl.ai_why} Papers are compared with your idea on this computer.`;
    return [
      h('div', { class: 'nv-h' }, 'Parts of the idea', h('span', { class: 'sub', text: 'each paper is compared with each part' })), partsBox,
      pl.online ? h('div', { class: 'nv-h' }, 'Searches', h('span', { class: 'sub', text: 'these words go to OpenAlex and arXiv' })) : null,
      pl.online ? qBox : null,
      h('p', { class: 'nv-note' + (pl.online ? '' : ' warn'), text: pl.online ? 'Only these searches go to the paper indexes. Your library is searched on this computer, and the closest papers’ citations are followed.' : pl.online_why }),
      h('p', { class: 'nv-note', text: aiLine }),
      h('div', { class: 'nv-go' }, go, h('span', { class: 'nv-note', text: 'Runs in the background, about a minute or two.' })),
    ].filter(Boolean);
  }

  function reportKids(r) {
    if (running(r)) {
      return [rb.working(`${STAGE[r.stage] || 'Working'}${r.examined ? ` · ${r.examined} papers so far` : ''}`, Date.parse(r.started_at) || Date.now()),
        h('p', { class: 'nv-note', text: 'Keep working; you’ll be told when the report is ready.' })];
    }
    if (r.status === 'error') return [h('div', { class: 'nv-err', text: r.error || 'The check failed.' })];
    const c = r.counts || {};
    const kids = [h('div', { class: 'nv-meta' }, h('span', { text: `${c.examined || r.examined || 0} papers examined, from abstracts${r.online ? '' : ', your library only'}` })),
      h('div', { class: 'nv-bands sm' }, ...['close', 'partial', 'background'].map((b) =>
        h('div', { class: 'nv-band ' + b, title: BAND_HINT[b] }, h('div', { class: 'n', text: String(c[b] || 0) }), h('div', { class: 'l', text: BAND[b] }))))];
    if (r.verdict) kids.push(h('p', { class: 'nv-verdict-t', text: r.verdict }));
    if (r.uncovered) kids.push(h('div', { class: 'nv-unc' }, h('b', { text: 'What none of them does: ' }), rb.mdEl(r.uncovered)));
    const top = (r.papers || []).filter((p) => p.band === 'close' || p.band === 'partial').slice(0, 4);
    for (const p of top) {
      const url = paperUrl(p);
      kids.push(h('div', { class: 'nv-mini' },
        h('span', { class: 'nv-tag ' + p.band, text: p.band === 'close' ? 'very close' : 'in part' }),
        url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', text: p.title }) : h('span', { text: p.title }),
        p.year ? h('span', { class: 'yr', text: ` (${p.year})` }) : null,
        p.why ? h('div', { class: 'nv-why', text: p.why }) : null));
    }
    if (!top.length) kids.push(h('p', { class: 'nv-note warn', text: 'Nothing close among the papers examined. From abstracts only, so a good sign, not proof: try other searches too.' }));
    kids.push(h('div', { class: 'nv-go' }, h('button', { class: 'btn btn-sm', type: 'button', text: 'Full report', onclick: () => showRun(r) })));
    return kids;
  }

  async function fetchRun(id) {
    if (fetchRun.busy.has(id)) return;
    fetchRun.busy.add(id);
    try { const r = await api('GET', `/api/novelty/runs/${enc(id)}`); RUNS.set(id, r); paintRun(r); if (running(r)) poll(); }
    catch { RUNS.set(id, { id, status: 'error', error: 'This report is no longer kept.', counts: {} }); paintRun(RUNS.get(id)); }
    finally { fetchRun.busy.delete(id); }
  }
  fetchRun.busy = new Set();

  /** Redraw just the chat cards of this run (not the whole chat: a preview being edited elsewhere keeps its focus). */
  function paintRun(r) {
    for (const el of document.querySelectorAll(`#chat-log [data-nv="${CSS.escape(r.id)}"]`)) {
      const m = S.chat.find((x) => x.novelty === r.id);
      if (!m) continue;
      if (!running(r) && r.status === 'done' && !/^Novelty check:/.test(m.content || '')) {
        const c = r.counts || {};
        m.content = `Novelty check: ${c.close || 0} very close, ${c.partial || 0} overlapping in part, of ${c.examined || 0} papers examined.${r.verdict ? ' ' + r.verdict : ''}`;
        if (rb.persistChat) rb.persistChat();
      }
      fillCard(el.querySelector('.nv-card'), m);
    }
    if (live() && N.run && N.run.id === r.id) { N.run = r; drawRun(); }
  }

  let pollT = null;
  function poll() {
    clearTimeout(pollT);
    const ids = [...RUNS.values()].filter(running).map((r) => r.id);
    if (!ids.length) return;
    pollT = setTimeout(async () => {
      for (const id of ids) {
        try { const r = await api('GET', `/api/novelty/runs/${enc(id)}`); RUNS.set(id, r); paintRun(r); } catch { /* next time */ }
      }
      poll();
    }, 2000);
  }

  // ====================================================================== the full report (a sheet)
  async function openRun(rid) {
    try { const r = await api('GET', `/api/novelty/runs/${enc(rid)}`); RUNS.set(rid, r); showRun(r); }
    catch (e) { toastErr(e, 'Could not open the report'); }
  }

  function showRun(run) {
    N.run = run;
    if (!live()) sheet('Novelty check', () => {});
    drawRun();
  }

  function drawRun() {
    if (!live()) return;
    const r = N.run;
    const kids = [h('p', { class: 'nv-idea', text: truncate(joined(r.title, r.idea), 300) })];
    const forget = h('button', { class: 'btn btn-text', type: 'button', text: 'Forget', title: 'Delete this report',
      onclick: async () => {
        try {
          await api('DELETE', `/api/novelty/runs/${enc(r.id)}`);
          RUNS.set(r.id, { id: r.id, status: 'error', error: 'You deleted this report.', counts: {} });
          paintRun(RUNS.get(r.id));
          if (CMD) CMD.close(false);
        }
        catch (e) { toastErr(e, 'Could not delete it'); }
      } });
    if (r.running || r.status === 'running') {
      kids.push(rb.working ? rb.working(`${STAGE[r.stage] || 'Working'}${r.examined ? ` · ${r.examined} papers so far` : ''}`, Date.parse(r.started_at) || Date.now())
        : h('div', { text: STAGE[r.stage] || 'Working' }),
      h('p', { class: 'nv-note', text: 'You can close this and keep working; you’ll be told when the report is ready.' }));
      put(...kids);
      return;
    }
    const c = r.counts || {};
    const meta = [fmtDate ? fmtDate(r.finished_at || r.started_at) : '', `${c.examined || r.examined || 0} papers examined (abstracts)`,
      c.via_citations ? `${c.via_citations} found only through citations` : '', c.in_library ? `${c.in_library} of the close or overlapping ones already in your library` : '',
      r.online ? '' : 'your library only', r.tokens ? `~${r.tokens.toLocaleString()} tokens` : ''].filter(Boolean);
    kids.push(h('div', { class: 'nv-meta' }, ...meta.map((m) => h('span', { text: m })), h('span', { class: 'sp' }), forget));
    if (r.status === 'error') {
      kids.push(h('div', { class: 'nv-err', text: r.error || 'The check failed.' }));
      put(...kids);
      return;
    }
    kids.push(h('div', { class: 'nv-bands' }, ...['close', 'partial', 'background'].map((b) =>
      h('div', { class: 'nv-band ' + b, title: BAND_HINT[b] }, h('div', { class: 'n', text: String(c[b] || 0) }), h('div', { class: 'l', text: BAND[b] })))));
    if (c.compared) kids.push(h('div', { class: 'nv-new', text: c.new ? `${c.new} close or overlapping paper${c.new === 1 ? '' : 's'} new since your last check` : 'Nothing close is new since your last check.' }));
    if (r.verdict || r.uncovered) {
      kids.push(h('div', { class: 'machine nv-verdict' },
        h('div', { class: 'machine-head' }, h('span', { text: `${AIName()}, from the abstracts` })),
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
    put(...kids);
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
        toast(`Added “${truncate(window.RBTitle.text(note.title) || p.title, 60)}”${section ? ` to ${section.label}` : ''}.`, 'ok');
        if (rb.refreshGraph) rb.refreshGraph();
      } catch (e) { b.disabled = false; b.textContent = 'Add to library'; toastErr(e, 'Could not add it'); }
    };
    return b;
  }

  // ====================================================================== telling you it's done
  window.addEventListener('rb:novelty-done', (e) => {
    const d = e.detail || {};
    if (!d.id || N.told.has(d.id)) return;
    N.told.add(d.id);
    if (N.startToast && N.startToast.remove) { N.startToast.remove(); N.startToast = null; }
    const what = truncate(window.RBTitle.text(d.title) || 'your idea', 50);
    const c = d.counts || {};
    const failed = d.status === 'error';
    const msg = failed ? `The novelty check of “${what}” didn’t finish: ${d.error || 'something went wrong'}`
      : `Novelty check of “${what}”: ${c.close || 0} very close, ${c.partial || 0} overlapping in part, of ${c.examined || 0} papers.`;
    api('GET', `/api/novelty/runs/${enc(d.id)}`).then((r) => { RUNS.set(r.id, r); paintRun(r); }).catch(() => {});
    // the report is in the chat: say so quietly when you're looking at it, else keep the toast until you act
    const inView = !!document.querySelector(`#chat:not([hidden]) #chat-log [data-nv="${CSS.escape(d.id)}"]`);
    toast(msg, failed ? 'error' : 'ok', inView ? {} : { ms: 24 * 3600 * 1000, action: { label: 'Open report', fn: () => openRun(d.id) } });
  });

  // ====================================================================== entry points
  function selection() {
    const f = document.activeElement && /^(TEXTAREA|INPUT)$/.test(document.activeElement.tagName) ? document.activeElement : null;
    if (f && f.id !== 'chat-input' && typeof f.selectionStart === 'number') return f.value.slice(f.selectionStart, f.selectionEnd);
    return String((window.getSelection && window.getSelection()) || '');
  }
  if (CMD) {
    CMD.register({ id: 'novelty.check', title: 'Check novelty', group: 'Research',
      description: 'Ask in Novelty mode: describe an idea and see how many papers already did something like it, and how close they came. Selected text goes in as the idea.',
      aliases: ['novelty', 'prior work', 'has this been done', 'related work', 'similar papers', 'originality'],
      run: () => compose(selection()) });
  }
  window.__rbNovelty = { wants, send, renderMsg, compose, openRun };
})();
