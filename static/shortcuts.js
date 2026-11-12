/* research_brain — the app's keyboard commands, note history, first-run tour and in-app guide.
 * Registers through RB.commands (static/commands.js, loaded first) and drives the app through
 * window.__rb (static/app.js). Loaded after every other app script so it can describe their
 * commands too (views.js focus, review.js keys, topics.js reading queue).
 */
(() => {
  'use strict';
  const rb = window.__rb;
  const CMD = window.RB && window.RB.commands;
  if (!rb || !CMD) return;
  const { S } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('rb.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('rb.' + k, JSON.stringify(v)); } catch { /* private window */ } },
  };
  const reg = (c) => { try { CMD.register(c); } catch (e) { console.error(e); } };
  const panelOpen = () => !!(S.cur && !$('#panel').hidden);
  const curIs = (...types) => panelOpen() && types.includes(S.cur.type || 'note');
  const sec = (name) => $(`#panel-body .section[data-sec="${name}"]`);
  const toast = (m) => rb.toast(m, 'info', { ms: 2400 });

  // ====================================================================== history (back / forward)
  const H = { list: [], pos: -1, moving: false };
  S.onOpenNote = (id) => {
    if (H.moving) return;
    if (H.list[H.pos] === id) return;
    H.list = H.list.slice(0, H.pos + 1);
    H.list.push(id);
    if (H.list.length > 100) H.list.shift();
    H.pos = H.list.length - 1;
  };
  function go(d) {
    let p = H.pos + d;
    while (p >= 0 && p < H.list.length && !S.nodes.has(H.list[p])) p += d; // skip deleted notes
    if (p < 0 || p >= H.list.length) { toast(d < 0 ? 'No earlier note to go back to.' : 'Nothing to go forward to.'); return; }
    H.pos = p;
    H.moving = true;
    try { rb.openNote(H.list[p], { center: true }); } finally { H.moving = false; }
  }
  rb.history = H;

  // ====================================================================== search results
  let resIdx = -1;
  $('#search-input').addEventListener('input', () => { resIdx = -1; });
  function step(d) {
    const r = S.ranked || [];
    if (!r.length) { toast(S.search ? 'Nothing matches that search.' : 'Type in the search box first.'); return; }
    resIdx = (resIdx + d + r.length) % r.length;
    rb.openNote(r[resIdx].id, { center: true });
    const cnt = $('#search-count'); if (cnt) cnt.textContent = `${resIdx + 1} of ${r.length}`;
  }
  const inSearch = () => document.activeElement === $('#search-input');
  const typingEl = (t) => !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  const notTypingElsewhere = () => !!S.search.trim() && (inSearch() || !typingEl(document.activeElement));

  // ====================================================================== pickers
  function noteItems(q, { exclude } = {}) {
    q = String(q || '').trim().toLowerCase();
    const out = [];
    for (const n of S.nodes.values()) {
      if (exclude && exclude.has(n.id)) continue;
      const t = (n.title || n.id).toLowerCase();
      let s = -1;
      if (!q) s = 1;
      else if (t.startsWith(q)) s = 100;
      else if (t.includes(' ' + q)) s = 80;
      else if (t.includes(q)) s = 50;
      else if ((n.authors || []).some((a) => String(a).toLowerCase().includes(q))) s = 30;
      else if ((n.tags || []).some((a) => String(a).toLowerCase().includes(q))) s = 30;
      if (s < 0) continue;
      const parent = n.type === 'fragment' && S.nodes.get(n.parent);
      const sub = n.type === 'fragment' ? `Fragment${parent ? ' in ' + rb.truncate(parent.title, 50) : ''}` : [n.type === 'paper' ? 'Paper' : 'Note', n.year, n.source === 'linked' && !n.has_pdf ? 'linked article' : ''].filter(Boolean).join(', ');
      out.push({ id: n.id, title: n.title || n.id, sub, s, frag: n.type === 'fragment' ? 1 : 0 });
    }
    out.sort((a, b) => b.s - a.s || a.frag - b.frag || a.title.localeCompare(b.title));
    return out;
  }
  function linkPicker() {
    const cur = S.cur;
    if (!cur) return;
    const ex = new Set([cur.id, ...(cur.links || [])]);
    CMD.pick({
      title: `Link “${rb.truncate(cur.title || cur.id, 48)}” to…`, placeholder: 'Search notes and papers by title, author or tag',
      hint: 'to link', empty: 'No other notes match.',
      items: (q) => noteItems(q, { exclude: ex }),
      onPick: (it) => { rb.linkNotes(cur.id, it.id); rb.toast(`Linked to “${rb.truncate(it.title, 50)}”`, 'ok'); },
    });
  }
  function openPicker() {
    CMD.pick({
      title: 'Open a note', placeholder: 'Search notes and papers by title, author or tag', hint: 'to open', empty: 'No notes match.',
      items: (q) => noteItems(q), onPick: (it) => rb.openNote(it.id, { center: true }),
    });
  }

  // ====================================================================== note actions
  function focusField(ta) { if (!ta) return false; ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); ta.scrollIntoView({ block: 'center', behavior: 'smooth' }); return true; }
  function bodySection() { return $$('#panel-body .section').find((s) => s.querySelector('textarea.body-field')); }
  function focusBody() {
    const s = bodySection(); if (!s) return;
    const ta = $('textarea.body-field', s);
    if (ta.offsetParent === null) { const b = $$('.seg button', s).find((x) => x.textContent === 'Edit'); if (b) b.click(); }
    focusField(ta);
  }
  function toggleEdit() {
    const s = bodySection(); if (!s) return;
    const ta = $('textarea.body-field', s);
    const editing = ta.offsetParent !== null;
    const b = $$('.seg button', s).find((x) => x.textContent === (editing ? 'Preview' : 'Edit'));
    if (b) b.click();
    if (!editing) focusField(ta); else if (document.activeElement === ta) ta.blur();
  }
  function viewerUrl() {
    const n = S.cur; if (!n) return null;
    if (n.type === 'fragment') {
      const pm = S.noteMeta.get(n.parent);
      if (!pm || !pm.has_pdf) return null;
      return `/viewer/${encodeURIComponent(n.parent)}${n.page ? '#page=' + n.page : ''}`;
    }
    return n.has_pdf ? `/viewer/${encodeURIComponent(n.id)}` : null;
  }
  function addFragment() {
    const s = sec('Fragments'); if (!s) return;
    const form = $('form.frag-form', s);
    if (form && form.hidden) { const t = $$('.sec-head button', s).find((b) => /Add fragment/.test(b.textContent)); if (t) t.click(); }
    const q = form && $('textarea', form);
    if (q) { q.focus(); s.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
  }

  // ====================================================================== commands
  // Navigate
  reg({ id: 'nav.nextResult', title: 'Next search result', group: 'Navigate', keys: ['Down', 'Mod+J'], allowInInput: true,
    description: 'While searching, opens the next match. ↓ and ↑ work in the search box; ⌘J works anywhere.', aliases: ['match', 'find next', 'cycle'],
    when: notTypingElsewhere, run: (e) => (e && e.key === 'ArrowDown' && !inSearch() ? false : step(1)) });
  reg({ id: 'nav.prevResult', title: 'Previous search result', group: 'Navigate', keys: ['Up', 'Mod+Shift+J'], allowInInput: true,
    description: 'While searching, opens the previous match.', aliases: ['match', 'find previous'],
    when: notTypingElsewhere, run: (e) => (e && e.key === 'ArrowUp' && !inSearch() ? false : step(-1)) });
  reg({ id: 'nav.open', title: 'Open a note…', group: 'Navigate', keys: ['G then O'], description: 'Jump to any paper, note or fragment by title.',
    aliases: ['go to', 'jump', 'switch', 'find note', 'quick open'], run: openPicker });
  reg({ id: 'nav.back', title: 'Back to the previous note', group: 'Navigate', keys: 'Mod+[', description: 'Steps back through the notes you opened.',
    aliases: ['history', 'previous', 'return', 'undo navigation'], run: () => go(-1) });
  reg({ id: 'nav.forward', title: 'Forward to the next note', group: 'Navigate', keys: 'Mod+]', description: 'Steps forward again after going back.',
    aliases: ['history', 'next', 'redo navigation'], run: () => go(1) });
  reg({ id: 'nav.parent', title: 'Go to the fragment’s paper', group: 'Navigate', keys: 'G then P', description: 'Opens the paper a fragment comes from.',
    aliases: ['parent', 'source', 'up'], when: () => curIs('fragment') && !!S.cur.parent, run: () => rb.openNote(S.cur.parent, { center: true }) });

  // Library
  reg({ id: 'library.newNote', title: 'New note', group: 'Library', keys: ['N', 'G then N'], description: 'Write your own idea or synthesis.',
    aliases: ['write', 'create', 'idea', 'synthesis', 'blank note'], run: () => rb.newNote() });
  reg({ id: 'library.importPdf', title: 'Import a PDF…', group: 'Library', keys: 'Mod+O', allowInInput: true,
    description: 'Pick a PDF from disk. You can also drop one anywhere on the window.', aliases: ['upload', 'open file', 'add pdf', 'drop'],
    run: () => { rb.closeMenus(); rb.pickPdf(); } });
  reg({ id: 'library.zotero', title: 'Import from Zotero…', group: 'Library', description: 'Papers, notes and highlights from your local Zotero library.',
    aliases: ['zotero', 'migrate', 'bulk import'], when: () => !!$('#btn-zotero'), run: () => { rb.openMenu('#btn-add-menu', '#add-menu'); $('#btn-zotero').click(); } });
  reg({ id: 'library.ask', title: 'Ask your library', group: 'Library', keys: 'A', description: 'Ask Claude a question across all your papers and notes.',
    aliases: ['chat', 'question', 'claude', 'query', 'rag'], run: () => { if ($('#chat').hidden) rb.openChat(); else $('#chat-input').focus(); } });
  reg({ id: 'library.review', title: 'Review', group: 'Library', keys: 'G then R', description: 'Recall your own summaries and fragment notes, then grade yourself.',
    aliases: ['recall', 'flashcards', 'spaced repetition', 'quiz', 'memory practice'], when: () => !!rb.openReview, run: () => rb.openReview() });
  reg({ id: 'library.topics', title: 'Open topics', group: 'Library', keys: 'G then T', description: 'Your topics and what to read next in each.',
    aliases: ['reading list', 'projects', 'collections'], when: () => !!rb.topics, run: () => rb.topics.open() });
  reg({ id: 'library.export', title: 'Export a read-only site…', group: 'Library', description: 'Your search matches, or every note, as a static site to share.',
    aliases: ['share', 'publish', 'html', 'static site'], when: () => !!$('#btn-export'), run: () => $('#btn-export').click() });

  // Note
  const notFrag = () => curIs('paper', 'note');
  reg({ id: 'note.summary', title: 'Write your summary', group: 'Note', keys: 'S', description: 'Puts the cursor in “Your summary”.',
    aliases: ['summary', 'focus summary', 'tldr'], when: () => panelOpen() && !!$('#summary-sec textarea'), run: () => focusField($('#summary-sec textarea')) });
  reg({ id: 'note.body', title: 'Write in your notes', group: 'Note', keys: 'B', description: 'Opens the notes body for editing and puts the cursor at the end.',
    aliases: ['body', 'notes', 'focus notes', 'edit notes'], when: () => panelOpen() && !!bodySection(), run: focusBody });
  reg({ id: 'note.critique', title: 'Critique my summary', group: 'Note', keys: ['C', 'Mod+Shift+C'], allowInInput: true,
    description: 'Claude compares your summary with the paper and points out what is wrong, missing or vague.',
    aliases: ['feedback', 'check summary', 'review summary', 'claude'], when: () => panelOpen() && !!$('#summary-sec'),
    run: () => { if (!($('#summary-sec textarea').value || '').trim()) { toast('Write your summary first, then Claude can critique it.'); return; } rb.runCritique(S.cur.id); } });
  reg({ id: 'note.fragment', title: 'Add fragment', group: 'Note', keys: 'Shift+F', description: 'Save a quote, equation or passage with your note on it.',
    aliases: ['quote', 'highlight', 'excerpt', 'passage', 'equation', 'clip'], when: () => notFrag() && !!sec('Fragments'), run: addFragment });
  reg({ id: 'note.viewer', title: 'Open the PDF in the viewer', group: 'Note', keys: 'O', description: 'Read the paper and highlight passages as fragments.',
    aliases: ['pdf', 'read', 'highlight', 'viewer'], when: () => panelOpen() && !!viewerUrl(), run: () => { window.open(viewerUrl(), '_blank', 'noopener'); } });
  reg({ id: 'note.link', title: 'Link to…', group: 'Note', keys: 'Shift+L', description: 'Pick another note to link this one to (a red line on the graph). In text, type [[ instead.',
    aliases: ['connect', 'wikilink', '[[', 'relate', 'join'], when: panelOpen, run: linkPicker });
  reg({ id: 'note.tags', title: 'Add a tag', group: 'Note', keys: '#', description: 'Puts the cursor in the tags field.', aliases: ['tag', 'label', 'category'],
    when: () => panelOpen() && !!$('#panel-body .tags-box input'), run: () => { const i = $('#panel-body .tags-box input'); i.focus(); i.scrollIntoView({ block: 'center', behavior: 'smooth' }); } });
  reg({ id: 'note.title', title: 'Rename', group: 'Note', keys: 'R', description: 'Puts the cursor in the title.', aliases: ['title', 'rename'],
    when: () => panelOpen() && !!$('#panel-body .title-input'), run: () => { const t = $('#panel-body .title-input'); t.focus(); t.select(); } });
  reg({ id: 'note.toggleEdit', title: 'Switch between edit and preview', group: 'Note', keys: 'Mod+E', allowInInput: true,
    description: 'For the notes body. ⌘↵ in the editor also shows the preview.', aliases: ['preview', 'render', 'markdown', 'edit mode'],
    when: () => panelOpen() && !!bodySection(), run: toggleEdit });
  reg({ id: 'note.discover', title: 'Find related papers', group: 'Note', description: 'References, citing papers and recommendations from Semantic Scholar.',
    aliases: ['discover', 'references', 'citations', 'semantic scholar', 'recommend'],
    when: () => panelOpen() && !!$$('#panel-body .actions button').find((b) => b.textContent === 'Find related papers'),
    run: () => $$('#panel-body .actions button').find((b) => b.textContent === 'Find related papers').click() });
  reg({ id: 'note.refreshMetadata', title: 'Refresh metadata', group: 'Note', description: 'Look the paper up again by its DOI, arXiv id, PMID or ISBN and update its details (fields you edited are kept).',
    aliases: ['metadata', 'crossref', 'fix metadata', 'update details', 'doi lookup', 'citation details'],
    when: () => panelOpen() && !!$('#panel-body .actions button[data-act="refresh-metadata"]'),
    run: () => $('#panel-body .actions button[data-act="refresh-metadata"]').click() });
  reg({ id: 'note.delete', title: 'Delete…', group: 'Note', keys: 'Mod+Backspace', description: 'Moves the open note to the vault’s .trash folder, after you confirm.',
    aliases: ['remove', 'trash', 'discard'], when: () => panelOpen() && !!$('#panel-body .danger-zone .btn-danger'), run: () => $('#panel-body .danger-zone .btn-danger').click() });

  // Graph
  reg({ id: 'graph.fit', title: 'Fit the graph', group: 'Graph', keys: ['Shift+1', 'G then F'], description: 'Zooms out to show every node.',
    aliases: ['zoom out', 'overview', 'whole graph', 'reset view'], run: () => rb.zoomFit() });
  reg({ id: 'graph.zoomSelected', title: 'Zoom to the selected node', group: 'Graph', keys: 'Shift+2', description: 'Centres and zooms in on the open note.',
    aliases: ['center', 'locate', 'where is', 'zoom in'], when: () => !!S.selected && S.nodes.has(S.selected), run: () => rb.centerOn(S.selected, true) });
  reg({ id: 'graph.similar', title: 'Show or hide similarity links', group: 'Graph', keys: 'Shift+S', description: 'The dashed blue-grey lines found by text similarity.',
    aliases: ['semantic', 'dashed', 'similar', 'toggle links'], run: () => { rb.setShowSim(!S.showSim); toast(S.showSim ? 'Similarity links shown' : 'Similarity links hidden'); } });
  reg({ id: 'graph.citations', title: 'Show or hide citation links', group: 'Graph', keys: 'Shift+C', description: 'Thin ink lines with an arrowhead at the cited paper.',
    aliases: ['cites', 'references', 'arrows', 'toggle links'], when: () => !!$('#cites-toggle'),
    run: () => { const c = $('#cites-toggle'); c.checked = !c.checked; c.dispatchEvent(new Event('change')); toast(c.checked ? 'Citation links shown' : 'Citation links hidden'); } });
  reg({ id: 'graph.ambient', title: 'Ambient activity on or off', group: 'Graph', description: 'Quiet signals travelling along random connections.',
    aliases: ['animation', 'motion', 'signals'], run: () => { const c = $('#ambient-toggle'); c.checked = !c.checked; c.dispatchEvent(new Event('change')); } });
  reg({ id: 'graph.filter', title: 'Filter and saved views', group: 'Graph', keys: 'G then V', description: 'Fade everything except a type, year range, tag or region.',
    aliases: ['filter', 'views', 'type', 'year', 'tag', 'region'], when: () => !!$('.gv-btn'), run: () => $('.gv-btn').click() });
  reg({ id: 'graph.focus', title: 'Focus on the neighbourhood', group: 'Graph', keys: 'F', description: 'Shows only the nodes within two links of the selected one. Press F again, or Esc, to show all.',
    aliases: ['2-hop', 'neighbours', 'isolate', 'local graph'], when: () => !!S.selected && !!rb.views,
    run: () => { const pill = $('.gv-pill'); if (pill && !pill.hidden) rb.views.unfocus(); else rb.views.focusOn(S.selected); } });
  reg({ id: 'graph.showAll', title: 'Show all', group: 'Graph', description: 'Leaves the neighbourhood focus. Esc does this too.',
    aliases: ['unfocus', 'everything', 'reset'], when: () => !!rb.views && !!$('.gv-pill') && !$('.gv-pill').hidden, run: () => rb.views.unfocus() });
  for (const [t, label] of [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']]) {
    reg({ id: `general.theme.${t}`, title: `Appearance: ${label.toLowerCase()}`, group: 'General', description: t === 'system' ? 'Follow the system setting.' : `Use the ${t} theme.`,
      aliases: ['theme', 'dark mode', 'light mode', 'colour', 'color'], run: () => { const b = $(`.theme button[data-theme="${t}"]`); if (b) b.click(); } });
  }

  // Review (review.js handles these keys itself while Review is open)
  const RV = [['review.reveal', 'Reveal your original', 'Space', 'After writing what you remember. ⌘↵ works while typing.', ['show answer', 'flip']],
    ['review.again', 'Grade: again', '1', 'You did not remember it. It comes back tomorrow.', ['forgot', 'fail']],
    ['review.hard', 'Grade: hard', '2', 'You remembered with effort.', ['difficult']],
    ['review.good', 'Grade: good', '3', 'You remembered it.', ['correct']],
    ['review.easy', 'Grade: easy', '4', 'Effortless. It comes back much later.', ['simple']]];
  for (const [id, title, keys, description, aliases] of RV) reg({ id, title, keys, description, aliases, group: 'Review', scope: 'review', passive: true });
  reg({ id: 'review.close', title: 'Close review', group: 'Review', scope: 'review', passive: true, description: 'Esc. The first Esc leaves the text box and keeps what you wrote.', aliases: ['exit', 'leave'] });

  // topics.js copy for the sheet (it registers the commands itself)
  const ann = {
    'topics.toggle': { title: 'Topics and next to read', group: 'Library', description: 'Opens or closes the topics drawer.', aliases: ['reading list', 'queue', 'projects', 'drawer'] },
    'topics.next': { title: 'Next to read', group: 'Library', description: 'Opens your reading queue at the top.', aliases: ['queue', 'reading list', 'what to read', 'up next'] },
    'note.park': { title: 'Park for later', group: 'Note', description: 'Set the open paper aside and say why, so it comes back in your queue.', aliases: ['save for later', 'read later', 'park', 'snooze', 'defer'] },
    'note.addTopic': { title: 'Add to a topic', group: 'Note', description: 'Files the open paper under one of your topics.', aliases: ['topic', 'collection', 'file under', 'categorize'] },
  };
  const STATUS = { 'to-read': 'Not started yet.', reading: 'You are reading it now.', read: 'Finished.', later: 'Set aside for later.', 'not-relevant': 'Not useful for your work.' };
  for (const [v, d] of Object.entries(STATUS)) ann[`note.status.${v}`] = { group: 'Note', description: `Reading status. ${d}`, aliases: ['status', 'reading status', 'mark', v.replace('-', ' ')] };
  for (const [id, meta] of Object.entries(ann)) CMD.annotate(id, meta);

  // Help
  reg({ id: 'help.guide', title: 'Help', group: 'General', description: 'A short guide to every feature, with its shortcut.',
    aliases: ['guide', 'manual', 'how to', 'docs', 'documentation', 'what is', 'explain'], run: () => openGuide() });
  reg({ id: 'help.tour', title: 'Help: quick tour', group: 'General', description: 'Five short tips that point at the main parts of the app.',
    aliases: ['tour', 'onboarding', 'intro', 'walkthrough', 'getting started', 'coach'], run: () => startTour() });

  // ====================================================================== tooltips with shortcuts
  const tips = [['#search-input', 'nav.search'], ['#btn-add-menu', 'library.addById', 'Add a paper, PDF or note'], ['#btn-chat', 'library.ask'],
    ['#btn-review', 'library.review'], ['#btn-zoom-fit', 'graph.fit'], ['#btn-topics', 'topics.toggle', 'Topics and what to read next'],
    ['#btn-panel-close', 'general.close', 'Close'], ['#btn-chat-close', 'general.close', 'Close'], ['#btn-shortcuts', 'general.shortcuts', 'All keyboard shortcuts']];
  function applyTips() { for (const [sel, id, base] of tips) { const n = $(sel); if (n && !n.dataset.tipBase) CMD.tooltip(n, id, base); } }
  applyTips();
  setTimeout(applyTips, 1500); // topics.js may add its button later
  // review.js rewrites its title as the count changes: keep the shortcut on it
  const rvb = $('#btn-review');
  if (rvb && window.MutationObserver) new MutationObserver(() => {
    const k = CMD.keysText('library.review');
    if (k && !rvb.title.includes(k)) { rvb.dataset.tipBase = rvb.title; rvb.title = `${rvb.title} (${k})`; }
  }).observe(rvb, { attributes: true, attributeFilter: ['title'] });

  // View menu entry
  const sc = $('#btn-shortcuts');
  if (sc) sc.addEventListener('click', () => { rb.closeMenus(); CMD.openSheet(); });

  // ====================================================================== in-app guide
  const GUIDE = [
    ['Adding things', [
      ['Add a paper', 'Drop a PDF on the window, or add one by DOI, arXiv id or title. Metadata comes from Semantic Scholar.', ['library.addById', 'library.importPdf']],
      ['Write a note', 'Your own idea or synthesis, not tied to one paper.', ['library.newNote']],
      ['Zotero', 'Add, then Import from Zotero brings in papers, notes and highlights.', []],
    ]],
    ['Reading and thinking', [
      ['Your summary', 'Two or three sentences in your own words. Then ask Claude to critique it against the paper.', ['note.summary', 'note.critique']],
      ['Fragments', 'A quote, equation or passage with your note on it. Add one in the panel, or highlight in the viewer.', ['note.fragment', 'note.viewer']],
      ['Links', 'Use the Links box near the top of the panel, or right-click a dot on the map. Type [[ in any text to mention a note. Your links are red on the graph.', ['note.link']],
      ['Math', 'Type $…$ for inline math and $$…$$ for display. A backslash opens LaTeX suggestions; Tab accepts.', []],
    ]],
    ['The graph', [
      ['What you see', 'Filled dots are papers, rings are notes, small red dots are fragments. Bigger means more connected and more written about.', []],
      ['Lines', 'Red lines are links you made. Dashed blue-grey lines are text similarity. Click a line to ask Claude why two notes connect.', ['graph.similar', 'graph.citations']],
      ['Focus', 'Double-click a node to show only its neighbourhood. Filter by type, year, tag or region.', ['graph.focus', 'graph.filter', 'graph.fit']],
    ]],
    ['Finding and recalling', [
      ['Search', 'Highlights matches on the graph. Enter opens the first; ↓ and ↑ step through the rest.', ['nav.search', 'nav.nextResult']],
      ['Ask your library', 'Claude answers from your papers and notes and cites the pages it used.', ['library.ask']],
      ['Review', 'Your own summaries come back for recall. Write what you remember, reveal, grade yourself 1 to 4.', ['library.review']],
      ['Topics', 'Group papers under topics and keep a queue of what to read next.', ['topics.toggle', 'note.park']],
    ]],
    ['Getting around', [
      ['Commands', 'Find any action by name. Every action has a title; frequent ones have a key.', ['general.palette', 'general.shortcuts']],
      ['History', 'Go back and forward through the notes you opened.', ['nav.back', 'nav.forward']],
      ['Chrome extension', 'Clip a selection from any web page as a fragment. See extension/README.md to install it.', ['extension.clip']],
    ]],
  ];
  function openGuide() {
    CMD.panel({
      title: 'How research_brain works', wide: true, className: 'rbk-guide',
      render(body, { el, capsEl, keysFor }) {
        for (const [head, rows] of GUIDE) {
          body.append(el('h3', { text: head }));
          for (const [name, text, ids] of rows) {
            const seqs = ids.flatMap((id) => keysFor(id).slice(0, 1));
            body.append(el('div', { class: 'rbk-row' }, el('div', { class: 'rbk-txt' }, el('div', { class: 'rbk-rt', text: name }), el('div', { class: 'rbk-rd', text })),
              seqs.length ? capsEl(seqs) : null));
          }
        }
        body.append(el('p', { style: 'margin-top: 20px; font: 13px var(--sans); color: var(--ink-2)' },
          'Press ? for every shortcut. ', el('a', { href: '#', text: 'Take the quick tour', onclick: (e) => { e.preventDefault(); CMD.close(false); startTour(); } }), '.'));
      },
    });
  }

  // ====================================================================== first-run tour (coach marks)
  const TOUR = [
    { sel: '#btn-add-menu', title: 'Add papers and notes', text: 'Add a paper by DOI, arXiv id or title, import a PDF, or write a note. You can also drop a PDF anywhere.', keys: ['library.addById', 'library.newNote'] },
    { sel: '.search', title: 'Search', text: 'Matching nodes light up on the graph. Enter opens the first match.', keys: ['nav.search'] },
    { sel: '#legend', fallback: '#graph', title: 'Reading the graph', text: 'Dots are papers, rings are notes, red points are fragments. Size grows with what you write and link. Red lines are your links; dashed lines are similar text.', keys: ['graph.fit'] },
    { sel: '#panel:not([hidden])', fallback: '#graph', title: 'The side panel', text: 'Click a node to open it. Write your summary, then ask for a critique. Save fragments, and type [[ to link notes.', keys: ['note.summary', 'note.critique'] },
    { sel: '#btn-view-menu', title: 'Every shortcut', text: 'Press ? to see them all, with a search field. Find any action by name in the command palette.', keys: ['general.shortcuts', 'general.palette'] },
  ];
  const CSS = `
  .rb-coach { position: fixed; z-index: 67; width: 300px; max-width: calc(100vw - 32px); background: var(--sheet); color: var(--ink); border: 1px solid var(--rule);
    border-radius: 6px; padding: 14px 16px 12px; font: 13px/1.5 var(--sans); box-shadow: 0 12px 32px -12px color-mix(in srgb, var(--ink) 22%, transparent); }
  .rb-coach .c-step { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .rb-coach .c-title { font: 600 14px/1.3 var(--sans); margin: 2px 0 4px; }
  .rb-coach .c-text { color: var(--ink); }
  .rb-coach .c-keys { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
  .rb-coach .c-keys .rbk-keys { max-width: none; }
  .rb-coach .c-act { display: flex; align-items: center; gap: 6px; margin-top: 12px; }
  .rb-coach .c-act .sp { flex: 1; }
  .rb-coach-ring { position: fixed; z-index: 66; pointer-events: none; border: 1px solid var(--ink-2); border-radius: 6px; }
  .empty-hint { margin-top: 28px; font-size: 12.5px; color: var(--ink-2); }
  .empty-hint kbd { margin: 0 2px; }
  .menu-item .menu-kbd { margin-left: auto; }
  .menu-item.has-kbd { flex-direction: row; align-items: center; }
  `;
  const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);

  let tour = null; // {i, card, ring}
  function startTour(i = 0) {
    endTour(false);
    rb.closeMenus();
    tour = { i };
    showStep();
  }
  function endTour(done = true) {
    if (!tour) return;
    if (tour.card) tour.card.remove();
    if (tour.ring) tour.ring.remove();
    tour = null;
    if (done) LS.set('tour.done', true);
  }
  function target(s) {
    for (const sel of [s.sel, s.fallback]) { const n = sel && $(sel); if (n && n.offsetParent !== null && n.getBoundingClientRect().width) return n; }
    return null;
  }
  function showStep() {
    if (!tour) return;
    const s = TOUR[tour.i], t = target(s);
    if (tour.card) tour.card.remove();
    if (tour.ring) tour.ring.remove();
    const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };
    const card = mk('div', 'rb-coach'); card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', s.title);
    const keys = mk('div', 'c-keys');
    for (const id of s.keys) {
      const c = CMD.get(id); if (!c || !c.seqs.length) continue;
      const w = mk('span', null); w.style.cssText = 'display:inline-flex;align-items:center;gap:6px;color:var(--ink-2);font-size:12px';
      const caps = CMD.capsEl(c.seqs.slice(0, 1)); caps.style.maxWidth = 'none';
      w.append(caps, c.title);
      keys.append(w);
    }
    const back = mk('button', 'btn btn-text btn-sm', 'Back'); back.type = 'button'; back.disabled = tour.i === 0; back.onclick = () => { tour.i--; showStep(); };
    const skip = mk('button', 'btn btn-text btn-sm', 'Skip tour'); skip.type = 'button'; skip.onclick = () => endTour();
    const last = tour.i === TOUR.length - 1;
    const next = mk('button', 'btn btn-primary btn-sm', last ? 'Done' : 'Next'); next.type = 'button';
    next.onclick = () => { if (last) endTour(); else { tour.i++; showStep(); } };
    const act = mk('div', 'c-act'); act.append(skip, mk('span', 'sp'), back, next);
    card.append(mk('div', 'c-step', `${tour.i + 1} of ${TOUR.length}`), mk('div', 'c-title', s.title), mk('div', 'c-text', s.text), keys.children.length ? keys : '', act);
    document.body.appendChild(card);
    tour.card = card;
    // place it next to the target, inside the viewport
    const W = window.innerWidth, Hh = window.innerHeight, cw = card.offsetWidth, ch = card.offsetHeight;
    let x = (W - cw) / 2, y = (Hh - ch) / 2;
    if (t) {
      const r = t.getBoundingClientRect();
      const big = r.width > W * 0.5 && r.height > Hh * 0.5;
      if (big) { x = r.left + (r.width - cw) / 2; y = r.top + Math.min(120, r.height / 4); }
      else {
        const ring = mk('div', 'rb-coach-ring');
        Object.assign(ring.style, { left: r.left - 4 + 'px', top: r.top - 4 + 'px', width: r.width + 8 + 'px', height: r.height + 8 + 'px' });
        document.body.appendChild(ring); tour.ring = ring;
        if (r.width > W * 0.25 && r.height > Hh * 0.4) { x = r.left - cw - 16 > 16 ? r.left - cw - 16 : r.left + 24; y = r.top + 40; } // tall panel: beside it
        else if (r.bottom + 12 + ch < Hh) { x = r.left + r.width / 2 - cw / 2; y = r.bottom + 12; }
        else { x = r.left + r.width / 2 - cw / 2; y = r.top - ch - 12; }
      }
    }
    card.style.left = Math.max(16, Math.min(W - cw - 16, x)) + 'px';
    card.style.top = Math.max(16, Math.min(Hh - ch - 16, y)) + 'px';
    next.focus({ preventScroll: true });
  }
  CMD.layer({ id: 'tour', order: 95, isOpen: () => !!tour, close: () => endTour() });
  window.addEventListener('resize', () => { if (tour) showStep(); });
  rb.tour = { start: startTour, end: endTour, get step() { return tour ? tour.i : -1; } };
  rb.openGuide = openGuide;
  // once, on first run (not under automation, so tests and scripted runs aren't covered by it)
  if (!LS.get('tour.done', false) && !navigator.webdriver) setTimeout(() => { if (!document.querySelector('.rbk-ov:not([hidden]), .modal:not([hidden])')) startTour(); }, 1200);
})();
