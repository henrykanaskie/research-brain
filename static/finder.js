/* research_brain — Find papers and On deck.
 * "Find papers" (top bar, command papers.find, key Shift+P): type a topic and your AI agent searches OpenAlex
 * and arXiv for it in the background (app/finder.py). What it proposes lands On deck, a review list across
 * all your searches: each paper with authors, year, venue, whether it's free to read, the agent's reason and
 * the abstract, plus Open, PDF (arXiv), Add to library and Dismiss. Nothing reaches the library until you add it.
 * When a search finishes you're told: a toast with Review, and if you're away, the Mac app's Dock icon bounces
 * and is badged (app/macwindow.py, via static/live.js) or, in a browser, a system notification.
 * Server: /api/finder* (app/finder_api.py) and POST /api/import/lookup to add. Needs window.__rb.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { h, api, toast, toastErr, truncate, fmtDate } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const enc = encodeURIComponent;
  const CMD = window.RB && window.RB.commands;

  const SINCE = [['any', 'Any time'], ['10y', 'Last 10 years'], ['5y', 'Last 5 years'], ['3y', 'Last 3 years'], ['1y', 'Last year']];
  const VERSION = { published: 'the published version', accepted: 'the authors’ accepted manuscript', preprint: 'a preprint', unknown: 'a copy (version unknown)' };
  const ACCESS = { open: 'Free to read', free: 'Free on the publisher’s site', closed: 'Paywalled' };

  const STYLE = `
  #btn-finder { position: relative; }
  #btn-finder .fp-badge { margin-left: 6px; min-width: 16px; height: 16px; padding: 0 4px; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center;
    font: 500 11px/1 var(--sans); color: var(--sheet); background: var(--ink); border-radius: 8px; font-variant-numeric: tabular-nums; }
  #btn-finder .fp-badge[hidden] { display: none; }
  #btn-finder .fp-run { width: 6px; height: 6px; margin-left: 6px; border-radius: 50%; background: var(--ink-2); display: inline-block; animation: fpPulse 1.4s ease-in-out infinite; }
  #btn-finder .fp-run[hidden] { display: none; }
  @keyframes fpPulse { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { #btn-finder .fp-run { animation: none; } }
  .fp-form { display: grid; grid-template-columns: 1fr auto auto; gap: 8px; align-items: center; }
  .fp-form input[type=text], .fp-form textarea, .fp-form select { box-sizing: border-box; border: 1px solid var(--rule); border-radius: var(--r, 3px); background: var(--sheet); color: var(--ink); font: 14px var(--sans); }
  .fp-form input[type=text] { height: 32px; padding: 0 10px; min-width: 0; }
  .fp-form select { height: 32px; padding: 0 6px; }
  .fp-form .fp-q { grid-column: 1 / -1; height: 30px; font-size: 13px; }
  .fp-forq { font-size: 12px; color: var(--violet, var(--ink-2)); }
  .fp-form textarea { grid-column: 1 / -1; min-height: 52px; padding: 6px 10px; resize: vertical; font: 13.5px/1.5 var(--sans); }
  .fp-form .btn { height: 32px; }
  .fp-note { font-size: 12px; color: var(--ink-3); margin: 6px 0 0; line-height: 1.5; }
  .fp-deck-h { display: flex; align-items: baseline; gap: 10px; margin: 26px 0 4px; font: 600 14px var(--sans); color: var(--ink); }
  .fp-deck-h .n { font-weight: 400; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .fp-search { margin-top: 18px; }
  .fp-search-h { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; border-bottom: 1px solid var(--rule); padding-bottom: 4px; }
  .fp-search-h .fp-topic { font: italic 16px/1.3 var(--display, var(--serif)); color: var(--ink); }
  .fp-search-h .fp-meta { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .fp-search-h .fp-err { font-size: 12px; color: var(--red); }
  .fp-search-h .sp { flex: 1; }
  .fp-search-h .btn { height: 22px; font-size: 12px; }
  .fp-busy::after { content: ''; display: inline-block; width: 1.2em; text-align: left; animation: fpDots 1.2s steps(4) infinite; }
  @keyframes fpDots { 0% { content: ''; } 25% { content: '.'; } 50% { content: '..'; } 75% { content: '...'; } }
  .fp-paper { padding: 12px 0 12px; border-top: 1px solid var(--rule); }
  .fp-paper:first-of-type { border-top: 0; }
  .fp-title { all: unset; cursor: pointer; font: 15.5px/1.35 var(--serif); color: var(--ink); }
  .fp-title:hover { text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--ink-2); }
  .fp-by { font-size: 12px; color: var(--ink-2); margin-top: 3px; display: flex; flex-wrap: wrap; column-gap: 10px; }
  .fp-by .open { color: var(--green, #3d7a52); }
  .fp-why { font: 13px/1.5 var(--sans); color: var(--machine, var(--ink-2)); margin-top: 6px; }
  .fp-why b { font-weight: 600; }
  .fp-free { font: 12.5px/1.5 var(--sans); color: var(--ink-2); margin-top: 6px; padding: 6px 10px; border-left: 2px solid var(--green, #3d7a52); background: var(--ground); }
  .fp-free b { font-weight: 600; color: var(--green, #3d7a52); }
  .fp-free a { color: inherit; }
  .fp-nofree { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
  .fp-abs { font: 14px/1.6 var(--serif); color: var(--ink-2); margin-top: 6px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .fp-abs.full { display: block; -webkit-line-clamp: unset; color: var(--ink); }
  .fp-acts { display: flex; gap: 4px; margin-top: 8px; flex-wrap: wrap; align-items: center; }
  .fp-acts .btn { height: 24px; font-size: 12px; padding: 0 8px; }
  .fp-paper.dim .fp-title, .fp-paper.dim .fp-abs { color: var(--ink-3); }
  .fp-empty { font-size: 13px; color: var(--ink-2); line-height: 1.55; padding: 6px 0; }
  details.fp-dismissed { margin-top: 24px; }
  details.fp-dismissed > summary { cursor: pointer; font: 500 13px var(--sans); color: var(--ink-2); }
  @media (max-width: 560px) { .fp-form { grid-template-columns: 1fr 1fr; } .fp-form input[type=text] { grid-column: 1 / -1; } }
  `;
  const style = document.createElement('style');
  style.id = 'rb-finder-style';
  style.textContent = STYLE;
  document.head.appendChild(style);

  const F = { data: null, open: new Set(), body: null, pollT: null, draft: { topic: '', detail: '', since: 'any', question: 'auto' },
    watching: new Set(), told: new Set() };

  // ====================================================================== top-bar button + badge
  const badge = h('span', { class: 'fp-badge', hidden: true, 'aria-label': 'papers on deck' });
  const runDot = h('span', { class: 'fp-run', hidden: true, 'aria-label': 'searching' });
  const topBtn = h('button', { id: 'btn-finder', class: 'btn btn-text', type: 'button', title: 'Find papers on a topic with your AI agent; review them On deck' }, 'Find papers', runDot, badge);
  const anchor = $('#btn-questions') || $('#btn-topics');
  if (anchor) anchor.after(topBtn); else { const a = $('#btn-review') || $('#btn-chat'); if (a) a.before(topBtn); }
  topBtn.addEventListener('click', () => openPanel());

  function setBadge() {
    const d = F.data;
    const n = d ? d.deck : 0;
    runDot.hidden = !(d && d.running);
    badge.hidden = !n || !runDot.hidden;
    badge.textContent = n > 99 ? '99+' : String(n);
    topBtn.title = d && d.running ? 'Searching for papers…' : n ? `${n} paper${n === 1 ? '' : 's'} on deck to review` : 'Find papers on a topic with your AI agent; review them On deck';
  }

  async function refresh() {
    try {
      F.data = await api('GET', '/api/finder');
      // a search we saw running has finished (the live event usually says so first; this is the fallback)
      for (const s of F.data.searches) {
        if (s.running) F.watching.add(s.id);
        else if (F.watching.has(s.id)) { F.watching.delete(s.id); finished(s, F.data.deck); }
      }
      setBadge();
      if (F.body && F.body.isConnected) drawDeck();
      window.dispatchEvent(new Event('rb:finder-changed'));  // the chat's paper cards follow (static/app.js)
      schedulePoll();
    } catch { /* offline: keep what we have */ }
  }
  function schedulePoll() {
    clearTimeout(F.pollT);
    if (F.data && F.data.running) F.pollT = setTimeout(refresh, 3000);
  }
  setTimeout(refresh, 500);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });

  // ====================================================================== the panel
  function openPanel(prefill) {
    if (!CMD || !CMD.panel) return;
    if (prefill) F.draft.topic = prefill;
    CMD.panel({ title: 'Find papers', wide: true, className: 'fp-sheet', render: (body) => {
      const deck = h('div', { class: 'fp-deck' });
      body.replaceChildren(form(), deck);
      F.body = deck;
      drawDeck();
      refresh();
      setTimeout(() => { const t = $('#fp-topic', body); if (t) t.focus(); }, 30);
    } });
  }

  function form() {
    const topic = h('input', { id: 'fp-topic', type: 'text', maxlength: 300, placeholder: 'A topic, e.g. “breathing mode oscillations in Hall thrusters”', 'aria-label': 'Topic', value: F.draft.topic });
    topic.value = F.draft.topic;
    const since = h('select', { 'aria-label': 'Published' }, SINCE.map(([v, l]) => h('option', { value: v, text: l })));
    since.value = F.draft.since;
    const detail = h('textarea', { maxlength: 1500, placeholder: 'Optional: what you’re after (reviews, a method, a specific result, what to leave out)', 'aria-label': 'What you’re after' });
    detail.value = F.draft.detail;
    const go = h('button', { class: 'btn btn-primary', type: 'button', text: 'Find papers' });
    // which open question the search is for: papers you add from it are linked to that question
    const qsel = h('select', { class: 'fp-q', 'aria-label': 'For which open question' },
      h('option', { value: 'auto', text: 'For an open question: matched automatically' }),
      h('option', { value: '', text: 'Not for any question' }));
    api('GET', '/api/questions').then((r) => {
      for (const q of (r.questions || []).filter((x) => x.question_status === 'open')) qsel.append(h('option', { value: q.id, text: 'For: ' + truncate(q.title, 90) }));
      qsel.value = F.draft.question || 'auto';
    }).catch(() => {});
    const keep = () => { F.draft = { topic: topic.value, detail: detail.value, since: since.value, question: qsel.value }; };
    topic.addEventListener('input', keep); detail.addEventListener('input', keep); since.addEventListener('change', keep); qsel.addEventListener('change', keep);
    const submit = async () => {
      const t = topic.value.trim();
      if (t.length < 3) { topic.focus(); toast('Type a topic to search for', 'info'); return; }
      go.disabled = true;
      try {
        askToNotify();
        const r = await api('POST', '/api/finder/search', { topic: t, detail: detail.value.trim(), since: since.value, question: qsel.value });
        if (r && r.search) F.watching.add(r.search.id);
        F.draft = { topic: '', detail: '', since: since.value, question: 'auto' };
        topic.value = ''; detail.value = '';
        F.startToast = toast('Searching. It takes a minute or two: close this and keep working, and you’ll be told when the papers are on deck.', 'ok', { ms: 6000 });
        await refresh();
      } catch (e) { toastErr(e, 'Could not start the search'); }
      finally { go.disabled = false; }
    };
    go.onclick = submit;
    topic.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); submit(); } });
    return h('div', null,
      h('div', { class: 'fp-form' }, topic, since, go, qsel, detail),
      h('p', { class: 'fp-note', text: 'Your AI agent rewords the topic, searches OpenAlex and arXiv, reads abstracts, follows reference lists and proposes up to 10 papers. The topic goes to your AI agent and its search words to OpenAlex and arXiv; nothing from your library is sent. Uses about one Research question of your AI plan.' }));
  }

  function drawDeck() {
    const box = F.body;
    if (!box) return;
    const d = F.data;
    if (!d) { box.replaceChildren(h('div', { class: 'fp-empty', text: 'Loading' })); return; }
    const kids = [h('div', { class: 'fp-deck-h' }, 'On deck', h('span', { class: 'n', text: d.deck ? `${d.deck} to review` : '' }))];
    if (!d.searches.length) kids.push(h('div', { class: 'fp-empty', text: 'Nothing yet. Search for a topic above; the papers your agent finds wait here until you add or dismiss them.' }));
    const bySearch = new Map();
    for (const it of d.items) { if (!bySearch.has(it.search)) bySearch.set(it.search, []); bySearch.get(it.search).push(it); }
    const dismissed = d.items.filter((i) => i.status === 'dismissed' && !i.library_id);
    for (const s of d.searches) {
      const items = (bySearch.get(s.id) || []).filter((i) => i.status === 'deck' || i.library_id);
      if (!items.length && s.status === 'done' && (bySearch.get(s.id) || []).length) continue; // all reviewed: tidy away
      kids.push(searchBlock(s, items));
    }
    if (dismissed.length) {
      kids.push(h('details', { class: 'fp-dismissed' }, h('summary', { text: `Dismissed (${dismissed.length})` }), dismissed.map((it) => paperRow(it))));
    }
    box.replaceChildren(...kids);
  }

  function searchBlock(s, items) {
    const when = fmtDate(s.started_at);
    const meta = s.running || s.status === 'running'
      ? h('span', { class: 'fp-meta fp-busy', text: 'Searching' })
      : s.status === 'error'
        ? h('span', { class: 'fp-err', text: s.error || 'The search failed.' })
        : h('span', { class: 'fp-meta', text: [when, `${s.added} new paper${s.added === 1 ? '' : 's'}`, s.lookups ? `${s.lookups} lookups` : '', s.tokens ? `~${s.tokens.toLocaleString()} tokens` : ''].filter(Boolean).join(' · ') });
    const forget = h('button', { class: 'btn btn-text', type: 'button', text: 'Forget', title: 'Remove this search and the papers still on deck from it',
      onclick: async () => { try { await api('DELETE', `/api/finder/searches/${enc(s.id)}`); refresh(); } catch (e) { toastErr(e, 'Could not remove it'); } } });
    const again = s.status === 'error' ? h('button', { class: 'btn btn-text', type: 'button', text: 'Try again',
      onclick: async () => { try { const r = await api('POST', '/api/finder/search', { topic: s.topic, detail: s.detail || '', since: s.since || 'any', question: s.question ? s.question.id : '' }); if (r && r.search) F.watching.add(r.search.id); await api('DELETE', `/api/finder/searches/${enc(s.id)}`); refresh(); } catch (e) { toastErr(e, 'Could not start the search'); } } }) : null;
    const forq = s.question ? h('span', { class: 'fp-forq', title: 'Papers you add from this search are linked to this open question', text: 'for ' + truncate(s.question.title, 60) }) : null;
    const kids = [h('div', { class: 'fp-search-h' }, h('span', { class: 'fp-topic', text: s.topic }), forq, meta, h('span', { class: 'sp' }), again, s.running ? null : forget)];
    if (s.status === 'done' && !items.length) kids.push(h('div', { class: 'fp-empty', text: s.note ? `Nothing new to propose. ${s.note}` : 'Nothing new to propose for this topic. Try other words or a wider date range.' }));
    kids.push(...items.map((it) => paperRow(it)));
    return h('section', { class: 'fp-search', 'data-id': s.id }, ...kids);
  }

  function paperUrl(p) { return p.url || (p.doi ? `https://doi.org/${p.doi}` : p.arxiv ? `https://arxiv.org/abs/${p.arxiv}` : ''); }

  function paperRow(it) {
    const p = it.paper || {};
    const url = paperUrl(p);
    const openIt = () => { if (it.library_id) rb.openNote(it.library_id, { center: true }); else if (url) window.open(url, '_blank', 'noopener,noreferrer'); };
    const authors = (p.authors || []).length ? `${p.authors.slice(0, 3).join(', ')}${p.authors.length > 3 ? ' et al.' : ''}` : '';
    const acc = ACCESS[p.access];
    const abs = h('div', { class: 'fp-abs' + (F.open.has(it.id) ? ' full' : ''), text: p.abstract || 'No abstract available.' });
    const absBtn = p.abstract && p.abstract.length > 260 ? h('button', { class: 'btn btn-text', type: 'button', text: F.open.has(it.id) ? 'Less' : 'Full abstract' }) : null;
    if (absBtn) absBtn.onclick = () => {
      const on = !F.open.has(it.id);
      if (on) F.open.add(it.id); else F.open.delete(it.id);
      abs.classList.toggle('full', on); absBtn.textContent = on ? 'Less' : 'Full abstract';
    };
    const acts = [];
    if (it.library_id) acts.push(h('button', { class: 'btn', type: 'button', text: 'In your library', onclick: () => { CMD && CMD.close(false); rb.openNote(it.library_id, { center: true }); } }));
    else if (it.status === 'dismissed') acts.push(h('button', { class: 'btn', type: 'button', text: 'Put back on deck', onclick: () => setStatus(it, 'deck') }));
    else acts.push(addBtn(it));
    if (url) acts.push(h('a', { class: 'btn btn-text', href: url, target: '_blank', rel: 'noopener noreferrer', text: p.arxiv ? 'arXiv page' : 'Open' }));
    if (p.arxiv) acts.push(h('a', { class: 'btn btn-text', href: `https://arxiv.org/pdf/${p.arxiv}`, target: '_blank', rel: 'noopener noreferrer', text: 'PDF' }));
    if (absBtn) acts.push(absBtn);
    if (it.status === 'deck' && !it.library_id) acts.push(h('button', { class: 'btn btn-text', type: 'button', text: 'Dismiss', title: 'Take it off deck; it won’t be proposed again', onclick: () => setStatus(it, 'dismissed') }));
    return h('div', { class: 'fp-paper' + (it.status === 'dismissed' ? ' dim' : ''), 'data-id': it.id },
      h('button', { class: 'fp-title', type: 'button', text: it.title || '(untitled)', onclick: openIt, title: it.library_id ? 'Open it in your library' : 'Open the paper’s page' }),
      h('div', { class: 'fp-by' },
        authors ? h('span', { text: authors }) : null,
        p.year ? h('span', { text: String(p.year) }) : null,
        p.venue ? h('span', { text: truncate(p.venue, 60) }) : null,
        acc ? h('span', { class: p.access === 'open' ? 'open' : '', text: acc }) : null),
      it.why ? h('div', { class: 'fp-why', title: 'Why your AI agent proposed it' }, h('b', { text: 'Why: ' }), it.why) : null,
      freeLine(it),
      abs,
      h('div', { class: 'fp-acts' }, ...acts));
  }

  /** A paywalled paper's free legal copy (app/freecopy.py) and the note on which version it is. */
  function freeLine(it) {
    const c = it.free_copy;
    if (!c || it.library_id) return null;
    if (c.none) return h('div', { class: 'fp-nofree', text: 'No free copy found in the open-access indexes.' });
    return h('div', { class: 'fp-free', title: 'Found in the open-access indexes (OpenAlex, Unpaywall, Semantic Scholar, arXiv). Adding the paper puts this note in it and downloads this copy.' },
      h('b', { text: 'Free copy: ' }), `${VERSION[c.version] || VERSION.unknown}${c.host ? ' on ' + c.host : ''}. `,
      h('a', { href: c.url, target: '_blank', rel: 'noopener noreferrer', text: 'Read it' }),
      h('div', { text: c.note || '' }));
  }

  function addBtn(it) {
    const p = it.paper || {};
    const b = h('button', { class: 'btn btn-primary', type: 'button', text: 'Add to library', title: 'Add it with its metadata (and a free PDF when one exists)' });
    b.onclick = async (ev) => {
      // which section of the map it goes in (or sorted automatically, by similarity)
      const section = rb.chooseSection ? await rb.chooseSection(ev) : null;
      if (section === undefined) return;  // closed the menu
      b.disabled = true; b.textContent = 'Adding';
      try {
        const q = p.doi || (p.arxiv ? 'arXiv:' + p.arxiv : it.title);
        const search = ((F.data && F.data.searches) || []).find((x) => x.id === it.search);
        const qid = search && search.question ? search.question.id : null;
        const note = await api('POST', '/api/import/lookup', qid ? { query: q, link_to: qid } : { query: q });
        let free = false;
        if (it.free_copy && it.free_copy.url) {
          try { free = (await api('POST', `/api/finder/items/${enc(it.id)}/added`, { note_id: note.id })).free_copy; } catch { /* the paper is added either way */ }
        }
        if (section && rb.intoSection) { try { await rb.intoSection(note, section); } catch { /* added either way */ } }
        toast(`Added “${truncate(note.title || it.title, 60)}”${section ? ` to ${section.label}` : ''}${qid ? `, linked to your question` : ''}.${free ? ' The free copy’s note is in it, and its PDF is downloading.' : ''}`, 'ok', { action: { label: 'Open', fn: () => { CMD && CMD.close(false); rb.openNote(note.id, { center: true }); } } });
        if (rb.refreshGraph) rb.refreshGraph();
        await refresh();
      } catch (e) { b.disabled = false; b.textContent = 'Add to library'; toastErr(e, 'Could not add it'); }
    };
    return b;
  }

  async function setStatus(it, status) {
    try {
      await api('PUT', `/api/finder/items/${enc(it.id)}`, { status });
      await refresh();
    } catch (e) { toastErr(e, 'Could not update it'); }
  }

  // ====================================================================== telling you a search finished
  // In the app: a toast that stays until you act on it, with Review. Away from it: the Mac app's Dock icon
  // bounces once and shows a badge (app/macwindow.py); in a browser, a system notification (if you allowed
  // it when you searched) and a dot in the tab's title until you come back.
  const away = () => document.visibilityState !== 'visible' || !document.hasFocus();
  const baseTitle = document.title;
  function back() { if (document.title !== baseTitle) document.title = baseTitle; }
  window.addEventListener('focus', back);
  document.addEventListener('visibilitychange', () => { if (!away()) back(); });

  function finished(s, deck) {
    if (!s || !s.id || F.told.has(s.id)) return;
    F.told.add(s.id);
    F.watching.delete(s.id);
    const topic = truncate(s.topic || 'your topic', 60);
    const failed = s.status === 'error';
    const msg = failed ? `The search for “${topic}” didn’t finish: ${s.error || 'something went wrong'}`
      : s.added ? `Found ${s.added} paper${s.added === 1 ? '' : 's'} for “${topic}”. They’re on deck.`
        : `The search for “${topic}” finished with nothing new.`;
    if (F.startToast) { F.startToast.remove(); F.startToast = null; }
    const panelOpen = F.body && F.body.isConnected;
    toast(msg, failed ? 'error' : 'ok', panelOpen ? {} : { ms: 24 * 3600 * 1000, action: { label: 'Review', fn: () => openPanel() } });
    if (!away()) return;
    if (!document.title.startsWith('● ')) document.title = '● ' + baseTitle;
    const live = window.__rbLive;
    if (live && live.native && live.native({ attention: true, badge: deck ? String(Math.min(deck, 99)) : '' })) return;
    try {
      if ('Notification' in window && Notification.permission === 'granted') {
        const n = new Notification('research brain', { body: msg, tag: 'rb-finder-' + s.id });
        n.onclick = () => { window.focus(); openPanel(); n.close(); };
      }
    } catch { /* notifications unavailable here */ }
  }
  window.addEventListener('rb:finder-done', (e) => {
    const d = e.detail || {};
    finished(d, d.deck);
    refresh();
  });
  /** Ask once, when you start a search (a click, so the browser allows the prompt); not in the Mac app. */
  function askToNotify() {
    try {
      const live = window.__rbLive;
      if (live && live.app === 'mac') return;
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
    } catch { /* not supported */ }
  }

  // ====================================================================== commands
  if (CMD) {
    CMD.register({ id: 'papers.find', title: 'Find papers', group: 'Library', keys: 'Shift+P',
      description: 'Type a topic; your AI agent searches OpenAlex and arXiv and puts what it finds On deck for you to review.',
      aliases: ['find papers', 'search papers', 'discover', 'literature search', 'on deck', 'triage papers', 'lit review'], run: () => openPanel() });
    CMD.tooltip(topBtn, 'papers.find', 'Find papers on a topic; review them On deck');
  }

  window.__rbFinder = { open: openPanel, refresh, paperRow, get data() { return F.data; } };
})();
