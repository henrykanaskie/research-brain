/* research_brain — active recall ("Review") and critique history.
 * Review: a calm full-height reading view over the user's own summaries and fragment notes,
 * scheduled with SM-2 on the server (/api/review/*). Write what you remember, reveal your original,
 * grade yourself. Claude can optionally check the recall (at most 3 lines on what was missed); it
 * never writes the answer for you.
 * Critique history: "Earlier critiques (n)" under the latest critique, with a word diff between the
 * summary each critique saw and the current one. Hooked in through __rb.S.critiqueHistory.
 * Depends on window.__rb from app.js.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { S, h, api, toast, toastErr, mdEl, fmtDate } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);

  const CSS = `
  #btn-review .review-n { color: var(--red); font-weight: 600; margin-left: 2px; }
  .rv { position: fixed; inset: var(--topbar-h) 0 0 0; z-index: 45; background: var(--sheet); overflow-y: auto; animation: rvIn .15s ease-out; }
  @keyframes rvIn { from { opacity: 0; } }
  .rv-col { width: min(960px, 100%); margin: 0 auto; padding: 32px 32px 96px; }
  .rv-head { display: flex; align-items: center; gap: 12px; color: var(--ink-2); font-size: 12.5px; min-height: 32px; }
  .rv-head .rv-name { color: var(--ink); font-weight: 600; font-size: 13px; }
  .rv-prompt { margin-top: 56px; max-width: 70ch; }
  .rv-kind { font-size: 12.5px; color: var(--ink-2); margin-bottom: 10px; }
  .rv-title { font: 400 30px/1.18 var(--serif); letter-spacing: -.015em; color: var(--ink); margin: 0; }
  .rv-meta { margin-top: 10px; color: var(--ink-2); font-size: 13px; display: flex; flex-wrap: wrap; column-gap: 14px; }
  .rv-quote { font: italic 20px/1.55 var(--serif); color: var(--ink); }
  .rv-quote .katex { font-style: normal; }
  .rv-src { margin-top: 12px; color: var(--ink-2); font-size: 13px; display: flex; flex-wrap: wrap; column-gap: 12px; }
  .rv-src button { all: unset; cursor: pointer; color: var(--ink); text-decoration: underline; text-decoration-color: var(--rule); text-underline-offset: 3px; }
  .rv-src button:hover { text-decoration-color: var(--ink-2); }
  .rv-write { margin-top: 40px; }
  .rv-write label { display: block; font-size: 12.5px; color: var(--ink-2); margin-bottom: 8px; }
  .rv-write textarea { width: 100%; min-height: 150px; font: 16px/1.6 var(--serif); padding: 12px 14px; }
  .rv-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 16px; }
  .rv-actions .hint kbd { margin: 0 2px; }
  .rv-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-top: 40px; }
  .rv-compare .rv-lbl { font-size: 12.5px; color: var(--ink-2); margin-bottom: 8px; }
  .rv-recalled .md { color: var(--ink-2); }
  .rv-recalled .empty { color: var(--ink-2); font-size: 13px; }
  .rv-grades { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 40px; max-width: 640px; }
  .rv-grade { all: unset; box-sizing: border-box; cursor: pointer; border: 1px solid var(--rule); border-radius: var(--r); padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; }
  .rv-grade:hover { background: var(--ground); }
  .rv-grade:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: 2px; }
  .rv-grade .g-name { font: 500 13px var(--sans); color: var(--ink); display: flex; justify-content: space-between; }
  .rv-grade .g-name kbd { font-size: 10.5px; }
  .rv-grade .g-int { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .rv-grade.again .g-name { color: var(--red); }
  .rv-check { max-width: 70ch; }
  .rv-done { margin-top: 96px; max-width: 52ch; }
  .rv-done h1 { font: 400 40px/1.1 var(--serif); letter-spacing: -.02em; margin: 0 0 16px; }
  .rv-done p { color: var(--ink-2); font-size: 15px; line-height: 1.6; margin: 0 0 8px; }
  .rv-done .n { color: var(--ink); font-variant-numeric: tabular-nums; }
  .rv-done .rv-actions { margin-top: 32px; }
  @media (max-width: 760px) { .rv-compare { grid-template-columns: 1fr; gap: 24px; } .rv-grades { grid-template-columns: repeat(2, 1fr); } .rv-col { padding: 20px 16px 64px; } }

  .crit-hist { margin-top: 12px; }
  .crit-hist > summary { font-size: 12.5px; color: var(--ink-2); }
  .crit-hist > summary:hover { color: var(--ink); }
  .crit-hist .ch-list { margin-top: 6px; display: flex; flex-direction: column; }
  .crit-hist, .ch-item { min-width: 0; max-width: 100%; }
  details.disclose.crit-hist > summary { display: inline-flex; }
  details.disclose.ch-item > summary { display: flex; width: 100%; box-sizing: border-box; gap: 12px; align-items: baseline; padding: 6px 8px; margin: 0 -8px; border-radius: var(--r); cursor: pointer; }
  details.disclose.ch-item > summary:hover { background: var(--ground); }
  .ch-item > summary::before { flex: none; }
  .ch-date { flex: none; width: 104px; font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .ch-verdict { flex: 1; min-width: 0; font-size: 13px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ch-item[open] .ch-verdict { white-space: normal; }
  .ch-body { padding: 4px 0 12px 18px; }
  .ch-body .machine { margin-top: 6px; }
  .ch-diff-lbl { font-size: 12px; color: var(--ink-2); margin: 14px 0 6px; }
  .ch-diff { font: 15px/1.6 var(--serif); color: var(--ink); white-space: pre-wrap; overflow-wrap: anywhere; max-width: 68ch; }
  .ch-diff ins { text-decoration: none; color: var(--red); background: var(--red-wash); border-radius: 2px; }
  .ch-diff del { color: var(--ink-2); text-decoration: line-through; text-decoration-color: var(--ink-2); }
  .ch-diff del + ins, .ch-diff ins + del { margin-left: .3em; }
  .ch-diff .same-all { color: var(--ink-2); font: 12.5px var(--sans); }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  // ====================================================================== top-bar count
  const btn = $('#btn-review');
  let stats = null;
  async function loadStats() {
    try {
      stats = await api('GET', '/api/review/stats');
      const n = stats.due_today || 0;
      btn.hidden = n === 0 && !open;
      $('.review-n', btn).textContent = n ? String(n) : '';
      btn.title = n ? `${n} to recall today: your own summaries and fragment notes` : 'Nothing due';
    } catch { /* server down: keep the last state */ }
  }
  loadStats();
  setInterval(loadStats, 60000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') loadStats(); });
  // summaries and fragment notes are written in the panel; refresh the count after editing there
  let statsT = null;
  document.addEventListener('focusout', (e) => { if (e.target.closest && e.target.closest('#panel')) { clearTimeout(statsT); statsT = setTimeout(loadStats, 1500); } });

  // ====================================================================== review view
  let open = false, view = null, queue = [], pos = 0, done = 0, cur = null;
  const GRADES = [['again', 'Again', 1], ['hard', 'Hard', 3], ['good', 'Good', 4], ['easy', 'Easy', 5]];

  function fmtInterval(d) {
    if (d <= 1) return 'tomorrow';
    if (d < 31) return `in ${d} days`;
    if (d < 365) { const m = Math.round(d / 30.4); return `in ${m} month${m === 1 ? '' : 's'}`; }
    const y = Math.round(d / 36.5) / 10; return `in ${y} year${y === 1 ? '' : 's'}`;
  }
  const authorsShort = (a) => !a || !a.length ? '' : a.length > 2 ? `${a[0]} et al.` : a.join(' and ');

  async function openReview() {
    if (open) return;
    await rb.flushSave();
    open = true;
    view = h('section', { class: 'rv', role: 'dialog', 'aria-label': 'Review', 'aria-modal': 'true' });
    view.addEventListener('keydown', onKey);
    document.body.appendChild(view);
    document.body.classList.add('review-open');
    rb.closeMenus();
    queue = []; pos = 0; done = 0;
    view.replaceChildren(h('div', { class: 'rv-col' }, header(), h('div', { class: 'rv-prompt' }, h('div', { class: 'working' }, h('span', { class: 'spin' }), 'Loading'))));
    await fetchMore();
    show();
  }
  function closeReview() {
    if (!open) return;
    open = false;
    view.remove(); view = null; cur = null;
    document.body.classList.remove('review-open');
    loadStats();
  }
  async function fetchMore() {
    try {
      const q = await api('GET', '/api/review/queue?limit=20');
      const seen = new Set(queue.slice(0, pos).map((x) => x.id)); // graded items are due tomorrow at the earliest
      queue = queue.slice(0, pos).concat(q.items.filter((x) => !seen.has(x.id)));
    } catch (e) { toastErr(e, 'Could not load the review queue'); }
  }

  function header() {
    const left = queue.length ? h('span', { class: 'tnum', text: `${Math.min(pos + 1, queue.length)} of ${queue.length}` }) : null;
    return h('div', { class: 'rv-head' },
      h('span', { class: 'rv-name', text: 'Review' }), left, h('div', { class: 'spacer' }),
      h('span', { class: 'hint' }, h('kbd', { text: 'Esc' }), ' to close'),
      h('button', { class: 'btn btn-icon', title: 'Close (Esc)', 'aria-label': 'Close', onclick: closeReview,
        html: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" stroke-width="1.3"/></svg>' }));
  }

  function promptEl(it) {
    const p = it.prompt;
    if (it.kind === 'summary') {
      const meta = [];
      if (p.authors && p.authors.length) meta.push(h('span', { text: authorsShort(p.authors) }));
      if (p.year) meta.push(h('span', { class: 'tnum', text: String(p.year) }));
      return h('div', { class: 'rv-prompt' },
        h('div', { class: 'rv-kind', text: p.type === 'paper' ? 'What did you write about this paper?' : 'What did you write in this note?' }),
        h('h1', { class: 'rv-title', text: p.title }),
        meta.length ? h('div', { class: 'rv-meta' }, meta) : null);
    }
    const src = [];
    if (p.paper_title) src.push(h('span', null, 'From ', h('button', { title: 'Open the paper', text: p.paper_title, onclick: () => { closeReview(); rb.openNote(p.paper_id, { center: true }); } })));
    if (p.locator) src.push(h('span', { class: 'tnum', text: p.locator }));
    return h('div', { class: 'rv-prompt' },
      h('div', { class: 'rv-kind', text: 'What did you note about this passage?' }),
      h('div', { class: 'rv-quote', html: rb.renderMD(p.quote, { inline: false }) }),
      src.length ? h('div', { class: 'rv-src' }, src) : null);
  }

  function show() {
    if (!view) return;
    if (pos >= queue.length) { endScreen(); return; }
    const it = queue[pos];
    cur = { it, revealed: false, check: null, checking: false };
    const ta = h('textarea', { id: 'rv-recall', rows: 6, spellcheck: true, placeholder: 'Write what you remember', 'aria-label': 'Write what you remember' });
    const live = h('div', { class: 'md live' });
    const reveal = h('button', { class: 'btn btn-primary', onclick: doReveal }, 'Reveal');
    const write = h('div', { class: 'rv-write' },
      h('label', { for: 'rv-recall', text: 'Write what you remember' }), ta, live,
      h('div', { class: 'rv-actions', id: 'rv-act' }, reveal,
        h('span', { class: 'hint' }, h('kbd', { text: 'Space' }), ' or ', h('kbd', { text: navigator.platform.includes('Mac') ? '⌘' : 'Ctrl' }), h('kbd', { text: 'Enter' }), ' reveals')));
    cur.ta = ta;
    view.replaceChildren(h('div', { class: 'rv-col' }, header(), promptEl(it), write, h('div', { id: 'rv-after' })));
    rb.latex(ta, live);
    ta.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); doReveal(); } });
    view.scrollTop = 0;
    setTimeout(() => ta.focus({ preventScroll: true }), 0);
  }

  function doReveal() {
    if (!cur || cur.revealed) return;
    cur.revealed = true;
    const act = $('#rv-act', view);
    if (act) act.hidden = true;
    renderAfter();
    const g = $('.rv-grade.good', view);
    if (g) g.focus({ preventScroll: true });
    const after = $('#rv-after', view);
    if (after) after.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function renderAfter() {
    const after = $('#rv-after', view);
    if (!after || !cur) return;
    const it = cur.it, recalled = cur.ta.value.trim();
    const last = it.last && it.last.at ? `last reviewed ${rb.ago(it.last.at)}` : it.is_new ? 'first review' : '';
    const compare = h('div', { class: 'rv-compare' },
      h('div', { class: 'rv-recalled' }, h('div', { class: 'rv-lbl', text: 'What you recalled' }),
        recalled ? mdEl(recalled) : h('div', { class: 'empty', text: 'Nothing written. That is fine; compare against the original and grade honestly.' })),
      h('div', null, h('div', { class: 'rv-lbl' }, it.kind === 'summary' ? 'Your summary' : 'Your note', last ? h('span', { text: `, ${last}` }) : null),
        h('div', { class: 'mine' }, mdEl(it.answer))));
    const kids = [compare];
    // optional: Claude checks the recall against the original (at most 3 lines, never a rewrite)
    const checkBtn = h('button', { class: 'btn btn-text needs-claude' + (cur.checking ? ' busy' : ''), disabled: cur.checking || !recalled,
      title: recalled ? 'Claude lists, in at most 3 lines, what you missed or got wrong. It does not rewrite your note.' : 'Write what you remember first',
      onclick: runCheck }, 'Check my recall');
    const checkRow = h('div', { class: 'rv-actions' }, checkBtn);
    kids.push(checkRow);
    if (cur.checking) kids.push(h('div', { class: 'machine rv-check' }, h('div', { class: 'working' }, h('span', { class: 'spin' }), 'Claude is comparing')));
    else if (cur.check) kids.push(h('div', { class: 'machine rv-check' }, h('div', { class: 'machine-head', text: "Claude's check" }), mdEl(cur.check)));
    const iv = it.intervals || {};
    kids.push(h('div', { class: 'rv-grades', role: 'group', 'aria-label': 'How well did you remember?' },
      GRADES.map(([key, label], i) => h('button', { class: 'rv-grade ' + key, onclick: () => grade(key) },
        h('span', { class: 'g-name' }, label, h('kbd', { text: String(i + 1) })),
        h('span', { class: 'g-int', text: fmtInterval(iv[key] || 1) })))));
    after.replaceChildren(...kids);
    rb.applyClaude(after);
  }

  async function runCheck() {
    if (!cur || cur.checking) return;
    const c = cur;
    c.checking = true; renderAfter();
    try {
      const r = await api('POST', `/api/review/${encodeURIComponent(c.it.id)}/check`, { recalled_text: c.ta.value });
      c.check = r.text;
    } catch (e) { toastErr(e, 'Check failed'); }
    c.checking = false;
    if (cur === c) renderAfter();
  }

  let grading = false;
  async function grade(key) {
    if (!cur || !cur.revealed || grading) return;
    grading = true;
    try {
      await api('POST', `/api/review/${encodeURIComponent(cur.it.id)}/grade`, { grade: key, recalled_text: cur.ta.value });
      done++; pos++;
      if (pos >= queue.length) await fetchMore();
      show();
      loadStats();
    } catch (e) {
      toastErr(e, 'Could not save the grade');
      if (/no longer exists/.test(e.message || '')) { pos++; show(); }
    } finally { grading = false; }
  }

  async function endScreen() {
    cur = null;
    let st = stats;
    try { st = await api('GET', '/api/review/stats'); stats = st; } catch { /* keep */ }
    if (!view) return;
    const streak = (st && st.streak) || 0, today = (st && st.reviewed_today) || done;
    const lines = [];
    if (done) lines.push(h('p', null, 'You recalled ', h('span', { class: 'n', text: String(done) }), done === 1 ? ' item this session.' : ' items this session.'));
    else if (st && !st.total) lines.push(h('p', { text: 'Nothing to review yet. Write a summary of a paper, or a note on a fragment, and it will come back here for recall.' }));
    else lines.push(h('p', { text: 'Nothing is due right now.' }));
    if (today && today !== done) lines.push(h('p', null, 'Today: ', h('span', { class: 'n', text: String(today) }), ' reviewed.'));
    if (streak) lines.push(h('p', null, 'Streak: ', h('span', { class: 'n', text: String(streak) }), streak === 1 ? ' day.' : ' days in a row.'));
    view.replaceChildren(h('div', { class: 'rv-col' }, header(),
      h('div', { class: 'rv-done' }, h('h1', { text: 'Done for today' }), ...lines,
        h('div', { class: 'rv-actions' }, h('button', { class: 'btn btn-primary', onclick: closeReview, text: 'Back to the graph' })))));
    const b = $('.rv-done .btn', view); if (b) b.focus();
  }

  function onKey(e) {
    if (!open) return;
    const t = e.target;
    const typing = t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable);
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      if (typing && t.value && !cur?.revealed) { t.blur(); return; } // first Esc leaves the textarea, keeps the text
      closeReview(); return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if ((e.key === ' ' || e.key === 'Spacebar') && cur && !cur.revealed) { e.preventDefault(); e.stopPropagation(); doReveal(); return; }
    const i = ['1', '2', '3', '4'].indexOf(e.key);
    if (i >= 0 && cur && cur.revealed) { e.preventDefault(); e.stopPropagation(); grade(GRADES[i][0]); }
  }
  // keys that arrive outside the view (focus on body) while it is open
  window.addEventListener('keydown', (e) => { if (open && view && !view.contains(e.target)) onKey(e); }, true);

  btn.addEventListener('click', openReview);
  rb.openReview = openReview;

  // ====================================================================== critique history
  /** word-level diff (LCS over words and whitespace); returns [[op, text]] with op in '=', '+', '-' */
  function wordDiff(a, b) {
    const ta = (a || '').split(/(\s+)/).filter((x) => x !== ''), tb = (b || '').split(/(\s+)/).filter((x) => x !== '');
    const n = ta.length, m = tb.length;
    if (n * m > 400000) return [['-', a], ['+', b]];
    const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = ta[i] === tb[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const out = [];
    const push = (op, s) => { const last = out[out.length - 1]; if (last && last[0] === op) last[1] += s; else out.push([op, s]); };
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (ta[i] === tb[j]) { push('=', ta[i]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) push('-', ta[i++]);
      else push('+', tb[j++]);
    }
    while (i < n) push('-', ta[i++]);
    while (j < m) push('+', tb[j++]);
    // whitespace-only changes between two kept words read as noise: fold them back into '='
    return out.map(([op, s]) => (op !== '=' && !s.trim() ? ['=', op === '+' ? s : ''] : [op, s])).filter(([, s]) => s !== '');
  }
  rb.wordDiff = wordDiff;

  function verdictLine(text) {
    const lines = String(text || '').split('\n').map((l) => l.trim()).filter(Boolean);
    const v = lines.find((l) => /^\W*verdict\W/i.test(l)) || lines[0] || '';
    return v.replace(/^\W*verdict\W*\s*/i, '').replace(/[*_`#>]/g, '').replace(/^[\s—–:-]+/, '').trim() || 'Critique';
  }
  const shortDate = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const recent = Date.now() - d.getTime() < 7 * 86400e3; // several critiques a day are common: add the time
    return d.toLocaleString(undefined, recent ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
      : { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
  };

  const histOpen = new Map(); // note id -> {outer: bool, items: Set(at)}
  S.critiqueHistory = (n, liveSummary) => {
    const all = (n.critiques || []).filter((c) => c && c.text);
    const latestAt = n.critique && n.critique.at;
    const earlier = all.filter((c) => c.at !== latestAt).reverse(); // newest first
    if (!earlier.length) return null;
    const st = histOpen.get(n.id) || { outer: false, items: new Set() };
    histOpen.set(n.id, st);
    const items = earlier.map((c) => {
      const det = h('details', { class: 'disclose ch-item', open: st.items.has(c.at) },
        h('summary', null, h('span', { class: 'ch-date', title: fmtDate(c.at), text: shortDate(c.at) }), h('span', { class: 'ch-verdict', text: verdictLine(c.text) })));
      const fill = () => {
        if (!det.open || det.__filled === liveSummary) return;
        det.__filled = liveSummary;
        const parts = wordDiff(c.summary_snapshot || '', liveSummary || '');
        const changed = parts.some(([op]) => op !== '=');
        const diff = h('div', { class: 'ch-diff' }, changed ? parts.map(([op, s]) => (op === '=' ? document.createTextNode(s) : h(op === '+' ? 'ins' : 'del', { text: s })))
          : h('span', { class: 'same-all', text: 'Your summary is the same now as when this critique was written.' }));
        det.querySelector('.ch-body')?.remove();
        det.append(h('div', { class: 'ch-body' },
          h('div', { class: 'machine' }, h('div', { class: 'machine-head', title: fmtDate(c.at), text: `Claude's critique, ${rb.ago(c.at)}` }), mdEl(c.text)),
          h('div', { class: 'ch-diff-lbl', text: changed ? 'How your summary changed since: new words in red, removed words struck through' : 'How your summary changed since' }),
          diff));
      };
      det.addEventListener('toggle', () => { if (det.open) st.items.add(c.at); else st.items.delete(c.at); fill(); });
      fill();
      return det;
    });
    const outer = h('details', { class: 'disclose crit-hist', open: st.outer },
      h('summary', null, `Earlier critiques (${earlier.length})`), h('div', { class: 'ch-list' }, items));
    outer.addEventListener('toggle', () => { st.outer = outer.open; });
    return outer;
  };
  // the panel may already be showing a note
  if (rb.rerenderCritique) rb.rerenderCritique();
})();
