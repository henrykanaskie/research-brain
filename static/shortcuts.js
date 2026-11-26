/* research_brain — the app's keyboard commands, note history, the tours (first run: basics or in depth) and in-app guide.
 * Registers through RB.commands (static/commands.js, loaded first) and drives the app through
 * window.__rb (static/app.js). Loaded after every other app script so it can describe their
 * commands too (views.js focus, review.js keys, topics.js reading queue).
 */
(() => {
  // ⌘ on a Mac, Ctrl+ elsewhere, for shortcuts written into text
  const MOD = (window.RB && window.RB.commands && window.RB.commands.isMac) ? '⌘' : 'Ctrl+';
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
      const sub = n.type === 'fragment' ? `Fragment${parent ? ' in ' + rb.truncate(window.RBTitle.text(parent.title), 50) : ''}` : [n.type === 'paper' ? 'Paper' : 'Note', n.year, n.source === 'linked' && !n.has_pdf ? 'linked article' : ''].filter(Boolean).join(', ');
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
      title: `Link “${rb.truncate(window.RBTitle.text(cur.title) || cur.id, 48)}” to…`, placeholder: 'Search notes and papers by title, author or tag',
      hint: 'to link', empty: 'No other notes match.',
      items: (q) => noteItems(q, { exclude: ex }),
      onPick: (it) => { rb.linkNotes(cur.id, it.id); rb.toast(`Linked to “${rb.truncate(window.RBTitle.text(it.title), 50)}”`, 'ok'); },
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
    description: `While searching, opens the next match. ↓ and ↑ work in the search box; ${MOD}J works anywhere.`, aliases: ['match', 'find next', 'cycle'],
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
    description: 'Pick a PDF from disk. You can also drop one anywhere on the window, or a link to one: a paper dragged from OneDrive on the web too.', aliases: ['upload', 'open file', 'add pdf', 'drop'],
    run: () => { rb.closeMenus(); rb.pickPdf(); } });
  reg({ id: 'library.importNotes', title: 'Import notes…', group: 'Library', description: 'Notes from Obsidian, Notion, Word, Google Docs or web pages, or handwritten pages kept as they are.',
    aliases: ['import notes', 'obsidian', 'notion', 'docx', 'word', 'markdown', 'handwritten', 'scan', 'handwriting'], when: () => !!window.__rbNoteImport, run: () => window.__rbNoteImport.open() });
  reg({ id: 'library.zotero', title: 'Import from Zotero…', group: 'Library', description: 'Papers, notes and highlights from your local Zotero library.',
    aliases: ['zotero', 'migrate', 'bulk import'], when: () => !!$('#btn-zotero'), run: () => { rb.openMenu('#btn-add-menu', '#add-menu'); $('#btn-zotero').click(); } });
  reg({ id: 'library.checkDetails', title: 'Check paper titles and authors', group: 'Library',
    description: 'Papers whose title looks wrong (an affiliation, a name, cut off) or that have no authors: {AI} reads each one\u2019s first page and fixes them from what is printed there. Private papers are skipped.',
    aliases: ['metadata', 'fix titles', 'authors', 'details'], run: async () => {
      try {
        const r = await rb.api('POST', '/api/papers/check-details');
        rb.toast(r.queued ? `Checking ${r.queued} paper${r.queued === 1 ? '' : 's'} in the background; they update on the map as they're fixed.` : 'Every paper has a title and authors that look right.', 'info', { ms: 6000 });
      } catch (e) { rb.toastErr(e, 'Could not check them'); }
    } });
  reg({ id: 'library.ask', title: 'Ask your library', group: 'Library', keys: 'A', description: 'Ask {AI} a question across all your papers and notes.',
    aliases: ['chat', 'question', 'claude', 'query', 'rag'], run: () => { if ($('#chat').hidden) rb.openChat(); else $('#chat-input').focus(); } });
  reg({ id: 'library.review', title: 'Review', group: 'Library', keys: 'G then R', description: 'Recall your own summaries and fragment notes, then grade yourself.',
    aliases: ['recall', 'flashcards', 'spaced repetition', 'quiz', 'memory practice'], when: () => !!rb.openReview, run: () => rb.openReview() });
  reg({ id: 'library.topics', title: 'Open regions', group: 'Library', keys: 'G then T', description: 'Your regions and what to read next in each.',
    aliases: ['region', 'reading list', 'projects', 'collections'], when: () => !!rb.topics, run: () => rb.topics.open() });
  reg({ id: 'library.export', title: 'Export a read-only site…', group: 'Library', description: 'Your search matches, or every note, as a static site to share.',
    aliases: ['share', 'publish', 'html', 'static site'], when: () => !!$('#btn-export'), run: () => $('#btn-export').click() });

  // Note
  const notFrag = () => curIs('paper', 'note');
  reg({ id: 'note.summary', title: 'Write your summary', group: 'Note', keys: 'S', description: 'Puts the cursor in “Your summary”.',
    aliases: ['summary', 'focus summary', 'tldr'], when: () => panelOpen() && !!$('#summary-sec textarea'), run: () => focusField($('#summary-sec textarea')) });
  reg({ id: 'note.body', title: 'Write in your notes', group: 'Note', keys: 'B', description: 'Opens the notes body for editing and puts the cursor at the end.',
    aliases: ['body', 'notes', 'focus notes', 'edit notes'], when: () => panelOpen() && !!bodySection(), run: focusBody });
  reg({ id: 'note.critique', title: 'Critique my summary', group: 'Note', keys: ['C', 'Mod+Shift+C'], allowInInput: true,
    description: '{AI} compares your summary with the paper and points out what is wrong, missing or vague.',
    aliases: ['feedback', 'check summary', 'review summary', 'claude'], when: () => panelOpen() && !!$('#summary-sec'),
    run: () => { if (!($('#summary-sec textarea').value || '').trim()) { toast(`Write your summary first, then ${RB.aiName()} can critique it.`); return; } rb.runCritique(S.cur.id); } });
  reg({ id: 'note.fragment', title: 'Add fragment', group: 'Note', keys: 'Shift+F', description: 'Save a quote, equation or passage with your note on it.',
    aliases: ['quote', 'highlight', 'excerpt', 'passage', 'equation', 'clip'], when: () => notFrag() && !!sec('Fragments'), run: addFragment });
  reg({ id: 'note.viewer', title: 'Open the PDF in the viewer', group: 'Note', keys: 'O', description: 'Read the paper and highlight passages as fragments.',
    aliases: ['pdf', 'read', 'highlight', 'viewer'], when: () => panelOpen() && !!viewerUrl(), run: () => { window.open(viewerUrl(), '_blank', 'noopener'); } });
  reg({ id: 'note.link', title: 'Link to…', group: 'Note', keys: 'Shift+L', description: 'Pick another note to link this one to (a red line on the graph). In text, type [[ instead.',
    aliases: ['connect', 'wikilink', '[[', 'relate', 'join'], when: panelOpen, run: linkPicker });
  reg({ id: 'note.tags', title: 'Add a tag', group: 'Note', keys: '#', description: 'Puts the cursor in the tags field.', aliases: ['tag', 'label', 'category'],
    when: () => panelOpen() && !!$('#panel-body .tags-box input'), run: () => { const i = $('#panel-body .tags-box input'); i.focus(); i.scrollIntoView({ block: 'center', behavior: 'smooth' }); } });
  reg({ id: 'note.title', title: 'Rename', group: 'Note', keys: 'R', description: 'Puts the cursor in the title.', aliases: ['title', 'rename'],
    when: () => panelOpen() && !!$('#panel-body .title-input'), run: () => { const t = $('#panel-body .title-input'); t.hidden = false; t.focus(); t.select(); } });
  reg({ id: 'note.toggleEdit', title: 'Switch between edit and preview', group: 'Note', keys: 'Mod+E', allowInInput: true,
    description: `For the notes body. ${MOD}↵ in the editor also shows the preview.`, aliases: ['preview', 'render', 'markdown', 'edit mode'],
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
  reg({ id: 'graph.lines', title: 'Show all connections on the map, or few', group: 'Graph', keys: [], description: 'Few: your links and the strongest connections inside each region. Point at a paper to see the rest.',
    aliases: ['clutter', 'declutter', 'connections', 'all lines', 'fewer lines'], run: () => { rb.setLines(S.lines === 'all' ? 'few' : 'all'); toast(S.lines === 'all' ? 'All connections shown' : 'Few connections shown: point at a paper to see the rest of its connections'); } });
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
  const RV = [['review.reveal', 'Reveal your original', 'Space', `After writing what you remember. ${MOD}↵ works while typing.`, ['show answer', 'flip']],
    ['review.again', 'Grade: again', '1', 'You did not remember it. It comes back tomorrow.', ['forgot', 'fail']],
    ['review.hard', 'Grade: hard', '2', 'You remembered with effort.', ['difficult']],
    ['review.good', 'Grade: good', '3', 'You remembered it.', ['correct']],
    ['review.easy', 'Grade: easy', '4', 'Effortless. It comes back much later.', ['simple']]];
  for (const [id, title, keys, description, aliases] of RV) reg({ id, title, keys, description, aliases, group: 'Review', scope: 'review', passive: true });
  reg({ id: 'review.close', title: 'Close review', group: 'Review', scope: 'review', passive: true, description: 'Esc. The first Esc leaves the text box and keeps what you wrote.', aliases: ['exit', 'leave'] });

  // topics.js copy for the sheet (it registers the commands itself)
  const ann = {
    'topics.toggle': { title: 'Regions and next to read', group: 'Library', description: 'Opens or closes the regions drawer.', aliases: ['reading list', 'queue', 'projects', 'drawer'] },
    'topics.next': { title: 'Next to read', group: 'Library', description: 'Opens your reading queue at the top.', aliases: ['queue', 'reading list', 'what to read', 'up next'] },
    'note.park': { title: 'Park for later', group: 'Note', description: 'Set the open paper aside and say why, so it comes back in your queue.', aliases: ['save for later', 'read later', 'park', 'snooze', 'defer'] },
    'note.addTopic': { title: 'Add to a region', group: 'Note', description: 'Files the open paper under one of your regions.', aliases: ['topic', 'collection', 'file under', 'categorize'] },
  };
  const STATUS = { 'to-read': 'Not started yet.', reading: 'You are reading it now.', read: 'Finished.', later: 'Set aside for later.', 'not-relevant': 'Not useful for your work.' };
  for (const [v, d] of Object.entries(STATUS)) ann[`note.status.${v}`] = { group: 'Note', description: `Reading status. ${d}`, aliases: ['status', 'reading status', 'mark', v.replace('-', ' ')] };
  for (const [id, meta] of Object.entries(ann)) CMD.annotate(id, meta);

  // Help
  reg({ id: 'help.guide', title: 'Help', group: 'General', description: 'A short guide to every feature, with its shortcut.',
    aliases: ['guide', 'manual', 'how to', 'docs', 'documentation', 'what is', 'explain'], run: () => openGuide() });
  reg({ id: 'help.tour', title: 'Help: quick tour', group: 'General', description: 'Five short tips that point at the main parts of the app.',
    aliases: ['tour', 'onboarding', 'intro', 'walkthrough', 'getting started', 'coach', 'basics'], run: () => startTour() });
  reg({ id: 'help.deepTour', title: 'Help: get me up to speed', group: 'General', description: 'The full tour: regions, subsections, Reorganize, Find papers, questions, citing and the rest, one step at a time.',
    aliases: ['tutorial', 'in depth', 'full tour', 'everything', 'advanced', 'up to speed', 'learn'], run: () => startTour(0, 'deep') });
  reg({ id: 'help.tutorials', title: 'Help: tutorials', group: 'General', description: 'Pick a tour: just the basics, or everything you can do.',
    aliases: ['tutorial', 'tour', 'welcome', 'onboarding', 'learn'], run: () => chooseTour() });

  // ====================================================================== tooltips with shortcuts
  const tips = [['#search-input', 'nav.search'], ['#btn-add-menu', 'library.addById', 'Add a paper, PDF or note'], ['#btn-chat', 'library.ask'],
    ['#btn-review', 'library.review'], ['#btn-zoom-fit', 'graph.fit'], ['#btn-topics', 'topics.toggle', 'Regions and what to read next'],
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
      ['Add a paper', 'Drop a PDF on the window, add one by DOI, arXiv id or title, or right-click an empty spot on the map (what you add appears right there). Its details come from Crossref, arXiv and Semantic Scholar; when none has an abstract, it’s read from the PDF itself.', ['library.addById', 'library.importPdf']],
      ['Find papers', 'Find papers in the top bar: describe a topic and {AI} searches OpenAlex and arXiv and proposes papers. Nothing is added until you say so, and you’re told when a search finishes.', ['papers.find']],
      ['Write a note', 'Your own idea or synthesis, not tied to one paper.', ['library.newNote']],
      ['Zotero', 'Add, then Import from Zotero brings in papers, notes and highlights.', []],
      ['Folders you keep PDFs in', 'Add › Add folders of papers: pick a folder (one in OneDrive works too). The PDFs stay where they are, never moved, renamed or deleted, and no copy is made. Each folder becomes a region named after it: before adding you can rename any, clear one, let {AI} suggest names, or let {AI} sort the papers into regions by what they’re about instead of by folder. PDFs you add to those folders later come in by themselves, and a PDF you move to another folder changes region. Online-only PDFs are read one at a time and sent straight back to OneDrive, so every paper has its full text and only the text stays on your computer. A large import shows a gauge in the bottom-right corner. Settings › Library settings › Folders of papers lists them, with Check now and Stop following.', ['import.linkFolder']],
      ['Copy in a folder of PDFs', 'Add › Import a folder of PDFs copies each one into your papers folder instead. Let {AI} sort them into regions, or make a region per subfolder. For papers without a region: the command Sort papers that have no region into regions.', ['import.folder', 'topics.autoSort']],
      ['Reorganize regions', 'Reorganize (bottom right of the map, or in Regions): {AI} regroups the papers you tick by what they’re about, ignoring how they’re filed now, or into regions you name, and you can set how many regions to make. You see the new regions and can rename them before anything changes, and Undo puts every region back. A region’s own Split up… does this for just that region. When {AI} sorts an import you can set the number of regions, or have it use only your existing regions. Both imports and Reorganize can also have {AI} find connections: it reads papers that look alike and links the ones that really belong together, so they sit together on the map.', ['topics.reorganize']],
      ['Short on disk space', 'Keep your PDFs in OneDrive: Library settings › Papers folder › move it into OneDrive, then right-click it › Free up space. A PDF downloads only when you open it. On Windows, tick Keep new PDFs online only. research brain always leaves 300 MB free, refuses a PDF that won’t fit, and warns you below 1 GB.', ['library.settings']],
    ]],
    ['Reading and thinking', [
      ['Saving', 'Everything saves by itself a moment after you stop typing. Save (or the Save key) saves at once. If a save fails, the panel says so and keeps your text, and research brain asks before you leave that note or close the window.', ['note.save']],
      ['Private by default', 'Your notes, questions and highlights stay on this computer: no AI reads them until you click the lock in a note’s header to share it. Papers are shareable.', []],
      ['Your summary', 'Two or three sentences in your own words. Then ask {AI} to critique it against the paper.', ['note.summary', 'note.critique']],
      ['Fragments', 'A quote, equation or passage with your note on it. Add one in the panel, or highlight in the viewer.', ['note.fragment', 'note.viewer']],
      ['Links', 'Use the Links box near the top of the panel, or right-click a dot on the map. Type [[ in any text to mention a note. Your links are red on the graph.', ['note.link']],
      ['Math', 'Type $…$ for inline math and $$…$$ for display. A backslash opens LaTeX suggestions; Tab accepts.', []],
      ['Outlines', `Start a line with "- " for a bullet, "1. " for a numbered list or "- [ ] " for a checkbox. Tab nests the line under the one above and Shift+Tab brings it back; Tab never leaves your notes (press Esc first to move on). Enter starts the next item; Enter on an empty one steps back a level, then ends the list. The bar above your notes does the same, plus bold (${MOD}B), italic (${MOD}I), highlight (${MOD}Shift+H) and headings. Tick checkboxes right in the reading view.`, []],
      ['Figures and other notes', `Paste a screenshot or figure into a note, drop an image on it, or use Image in the bar. Pasting from Google Docs, Word, Apple Notes or Notion keeps bullets, headings and bold. Add › Import notes brings in Markdown, Word, web pages and whole Obsidian or Notion exports, or handwritten pages: kept as they are, and {AI} can write them out as text.`, ['library.importNotes']],
    ]],
    ['The graph', [
      ['What you see', 'Filled dots are papers, rings are notes, small red dots are fragments. A slightly bigger dot is more written about and connected. A paper you haven’t read yet has a small yellow glow at its centre.', []],
      ['Lines', 'Red lines are links you made. Dashed blue-grey lines are text similarity. Click a line to ask {AI} why two notes connect.', ['graph.similar', 'graph.citations']],
      ['Focus', 'Double-click a node to show only its neighbourhood. Filter by type, year, tag or region. Zoom in as far as you like while there’s something on screen (it stops before you’d zoom into empty space); resizing the window refits the map. With a note open, drag the map to look around; click empty map to close the note (the tab on the right edge reopens it).', ['graph.focus', 'graph.filter', 'graph.fit']],
      ['Labels', 'Papers are labelled with first author and year; Settings › Labels shows the title too, or the title alone. Hover a dot for its full title.', []],
      ['Move dots', 'Drag a dot where you want it: it keeps that spot within its region (and next time) and no other dot moves with it: its lines just stretch to follow. Clean up (above the map) puts dragged dots and moved regions back into the tidy layout. Regions arrange themselves around the middle. Right-click › Let it move freely gives a dot back to the layout.', ['graph.unpinAll']],
      ['Regions', 'Papers are grouped into regions, named on the map and listed in Regions at the top. Click a name on the map to zoom in on it (click another to go straight there, empty space to come back), then Rename to change it. To move a paper into a region, right-click its dot › Put in region.', ['graph.focusRegion', 'graph.renameRegion']],
    ]],
    ['Finding and recalling', [
      ['Search', 'Highlights matches on the graph. Enter opens the first; ↓ and ↑ step through the rest.', ['nav.search', 'nav.nextResult']],
      ['Ask: Library', 'The agent answers from your papers and notes and cites the pages it used. Switch Quick to Research for harder questions.', ['library.ask']],
      ['Ask: Regions', 'Pick one or more regions: the agent reads every paper in them and the passages that best match. Also Ask about this region in the Regions drawer.', ['chat.aboutTopics']],
      ['Ask: General', 'Ask anything, without your library. Attach… adds papers, notes, questions or regions for it to read. Nothing of yours goes along unless you attach it.', ['chat.general']],
      ['Web search', 'Off until you allow it in Settings › Library settings › AI and privacy. Then tick Search the web in a General chat. Attached papers go with their published parts only; your own notes go along only after you confirm once.', []],
      ['Conversations', 'Every conversation (library, each paper, each set of regions, General) is kept and comes back next time. Older messages are condensed into a short summary to save space. New chat starts over and keeps the last one in Past chats, where you can go back to any earlier conversation.', []],
      ['Review', 'Your own summaries come back for recall. Write what you remember, reveal, grade yourself 1 to 4.', ['library.review']],
      ['Regions', 'Group papers under regions and keep a queue of what to read next.', ['topics.toggle', 'note.park']],
    ]],
    ['Getting around', [
      ['Commands', 'Find any action by name. Every action has a title; frequent ones have a key.', ['general.palette', 'general.shortcuts']],
      ['Panel width', 'Drag the grip on the left edge of the note panel or the chat to make it wider or narrower (or focus it and use ← →). Double-click it to reset. Widths are kept.', []],
      ['Updates', 'When there’s a newer version, download its installer zip, unzip it (Windows: right-click › Extract All) and run the installer again, the same way as the first time. Your library stays exactly as it is.', []],
      ['Settings', 'The gear at the top right: Library settings (in tabs, with a search box), what the graph shows, and appearance.', []],
      ['History', 'Go back and forward through the notes you opened.', ['nav.back', 'nav.forward']],
      ['Paper lookup keys', 'If adding or searching for papers says rate limited, add free keys: get one at openalex.org/settings/api and one from semanticscholar.org/product/api, then paste them in Settings › Library settings › Paper lookup.', []],
      ['Chrome extension', 'Save papers and clip passages from any web page. To add it: unzip research-brain-extension-….zip from the installer folder (or use the extension folder in the research brain folder) into a folder you keep that isn’t synced, open chrome://extensions in Chrome, turn on Developer mode, click Load unpacked and choose that folder. research brain must be open for it to work.', ['extension.clip']],
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
            body.append(el('div', { class: 'rbk-row' }, el('div', { class: 'rbk-txt' }, el('div', { class: 'rbk-rt', text: name }), el('div', { class: 'rbk-rd', text: RB.aiText(text) })),
              seqs.length ? capsEl(seqs) : null));
          }
        }
        body.append(el('p', { style: 'margin-top: 20px; font: 13px var(--sans); color: var(--ink-2)' },
          'Press ? for every shortcut. ', el('a', { href: '#', text: 'Take the quick tour', onclick: (e) => { e.preventDefault(); CMD.close(false); startTour(); } }),
          ', or ', el('a', { href: '#', text: 'get up to speed on everything', onclick: (e) => { e.preventDefault(); CMD.close(false); startTour(0, 'deep'); } }), '.'));
      },
    });
  }

  // ====================================================================== tours (coach marks)
  // Both tours start with the user's own papers: they import the ones they have or find new ones online, and the
  // steps after that work on that library. A step with a `task` waits for the user to do it (its Next reads Skip
  // until then; `needs` says when it can't be done yet), `actions` are buttons that start it for them.
  // Nothing in a tour changes the library by itself: actions only open things the user then confirms.
  const papers = () => [...S.nodes.values()].filter((n) => n.type === 'paper');
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const cmd = (id) => () => CMD.run(id);
  const regions = () => S.clusters || [];
  const biggest = () => regions().slice().sort((x, y) => (y.size || 0) - (x.size || 0))[0];
  const regionLabel = () => { const r = regions().find((c) => c.id === S.focusCluster); return r ? `“${r.label}”` : 'that region'; };
  const short = (t) => { t = String(t || 'it'); return t.length > 60 ? t.slice(0, 59) + '…' : t; };
  const userMsgs = () => (S.chat || []).filter((m) => m.role === 'user').length;
  const needPapers = () => (papers().length ? null : 'This needs a paper: add one in the first step, or skip this one.');
  const needRegions = () => needPapers() || (regions().length ? null : 'Regions appear once you have a few papers. Add some more, or skip this one.');
  function openAPaper() {
    const ps = papers(); if (!ps.length) return;
    const p = ps.find((n) => n.status !== 'read') || ps[0];
    rb.openNote(p.id, { center: true });
  }
  function openSubs() {
    if (S.focusCluster == null) { const r = biggest(); if (!r) return; rb.focusRegion(r.id); }
    setTimeout(() => { const b = $('#rf-subs'); if (b) b.click(); }, 120);
  }
  function askExample() {
    rb.openChat();
    const ta = $('#chat-input'); if (!ta) return;
    if (!ta.value.trim()) { ta.value = 'What do my papers disagree about?'; ta.dispatchEvent(new Event('input')); }
    ta.focus();
  }
  const ADD_ACTIONS = [
    { label: 'Import PDFs', run: () => rb.pickPdf(), primary: true },
    { label: 'Add folders of papers', run: cmd('import.linkFolder') },
    { label: 'Find papers online', run: cmd('papers.find') },
    { label: 'Add by DOI or title', run: cmd('library.addById') },
    { label: 'Import from Zotero', run: cmd('library.zotero') },
  ];
  const addStep = (ch) => ({ ch, sel: '#btn-add-menu', title: 'Bring in your papers',
    text: 'This tour uses your own library, so start with your papers: import PDFs you already have, add the folders you keep them in (they stay where they are, and each folder becomes a region), or find new ones online. Ten or more makes regions worth exploring.',
    actions: ADD_ACTIONS, keys: ['library.addById', 'papers.find'],
    task: { check: () => papers().length > 0,
      wait: 'Waiting for your first paper. Find papers searches in the background: when it’s done, click Add on the ones you want.',
      done: () => `You have ${plural(papers().length, 'paper')}. Add more any time from Add.` } });

  const TOUR = [
    addStep(),
    { sel: '.search', title: 'Search', text: 'Matching nodes light up on the graph. Enter opens the first match.', keys: ['nav.search'] },
    { sel: '#legend', fallback: '#graph', title: 'Reading the graph', text: 'Dots are papers, rings are notes, red points are fragments. Size grows slightly with what you write and link. Red lines are your links; dashed lines are similar text.', keys: ['graph.fit'] },
    { sel: '#panel:not([hidden])', fallback: '#graph', title: 'The side panel', text: 'Click a dot to open it. Write your summary, then ask for a critique. Save fragments, and type [[ to link notes.', keys: ['note.summary', 'note.critique'],
      actions: [{ label: 'Open one for me', run: openAPaper }],
      task: { needs: needPapers, check: () => curIs('paper'), wait: 'Click a dot on the map to open it.', done: () => `Opened ${short(S.cur.title)}.` } },
    { sel: '#btn-chat', title: 'Ask', text: 'Ask about your whole library, about the papers in some regions, or anything at all (General, where you can attach things and, if you allow it, search the web). Conversations are kept.', keys: ['library.ask', 'chat.general'] },
    { sel: '#btn-view-menu', title: 'Settings and tutorials', text: 'The gear holds Library settings, graph options and appearance, and Tutorials, where the full tour waits whenever you want it. Press ? for every shortcut; find any action by name in the command palette.', keys: ['general.shortcuts', 'general.palette'] },
  ];

  const DEEP = [
    addStep('Your papers'),
    { ch: 'Your papers', sel: '#btn-finder', fallback: '#btn-add-menu', title: 'Find papers online', text: 'Describe a topic and {AI} searches OpenAlex and arXiv in the background. What it proposes lands On deck, with a reason for each paper and whether it’s free to read. Nothing reaches your library until you click Add, and you’re told when a search finishes.', keys: ['papers.find'],
      actions: [{ label: 'Open Find papers', run: cmd('papers.find') }] },
    { ch: 'Your papers', sel: '#graph', title: 'Where a new paper lands', text: 'A paper that clearly belongs in a region is put there, with Undo or Move in the message. One that could go in a few is asked about: pick a region, any other, or leave it. In Add you can also choose the region up front.', keys: [] },
    { ch: 'The map', sel: '#legend', fallback: '#graph', title: 'Reading the map', text: 'Filled dots are papers, rings are notes, small red dots are fragments. A paper you haven’t read has a yellow glow at its centre. Red lines are links you made, dashed lines are similar text, and you can click a line to ask {AI} why two notes connect.', keys: ['graph.similar', 'graph.citations'] },
    { ch: 'The map', sel: '#graph', title: 'Open a paper', text: 'Click any dot on the map to open it in the side panel.', keys: [],
      actions: [{ label: 'Open one for me', run: openAPaper }],
      task: { needs: needPapers, check: () => curIs('paper'), wait: 'Click a dot on the map to open it.', done: () => `Opened ${short(S.cur.title)}.` } },
    { ch: 'Reading', sel: '#panel:not([hidden])', fallback: '#graph', title: 'Your summary', text: 'In the panel, write two sentences in your own words about what the paper shows, then ask {AI} to critique it against the paper. Type [[ to link another note, $…$ for math, “- ” for an outline. It saves by itself.', keys: ['note.summary', 'note.critique', 'note.link'] },
    { ch: 'Reading', sel: '#panel:not([hidden])', fallback: '#graph', title: 'Fragments, highlights and ideas', text: 'Open the PDF from the panel and highlight a passage: it becomes a fragment with your note on it, a red dot on the map. Under it, Ideas lists the paper’s claims, equations and measured values, each with its quote; confirm the ones you trust and they’re linked to the same idea in other papers.', keys: ['note.viewer', 'note.fragment', 'ideas.view'] },
    { ch: 'The map', sel: '#btn-cleanup', fallback: '#graph', title: 'Move dots, then Clean up', text: 'Drag a dot where you want it and it keeps that spot. Drop it on another region and it moves there. Clean up puts dragged dots and moved regions back into the tidy layout; double-click a dot to see only its neighbourhood.', keys: ['graph.cleanUp', 'graph.focus'] },
    { ch: 'Organizing', sel: '#btn-regions', fallback: '#graph', title: 'Focus a region', text: 'Your papers are grouped into regions, named on the map. Click a region’s name to focus it: the map zooms in and the rest steps back. Rename is in the bar at the top. To file a paper elsewhere, drag its dot into another region or right-click it › Put in region.', keys: ['graph.focusRegion', 'graph.renameRegion'],
      actions: [{ label: 'Focus the biggest for me', run: () => { const r = biggest(); if (r) rb.focusRegion(r.id); } }],
      task: { needs: needRegions, check: () => S.focusCluster != null, wait: 'Click a region’s name on the map.', done: () => `Focused on ${regionLabel()}. Esc comes back out.` } },
    { ch: 'Organizing', sel: '#rf-subs', fallback: '#btn-regions', title: 'Subsections', text: 'Inside a region, subsections are named groups of its papers. With a region focused, click Subsections at the top: add one and tick its papers, or let {AI} propose them (nothing changes until you Apply). Right-click a paper › New subsection… works too.', keys: [],
      actions: [{ label: 'Open Subsections', run: openSubs }],
      task: { needs: needRegions, begin: (t) => { t.sawSubs = false; },
        check: (t) => { const head = $('.rf-chooser .rf-head'); if (head && /^Subsections of/.test(head.textContent)) t.sawSubs = true; return t.sawSubs || (S.subsections || []).some((x) => x.region === S.focusCluster); },
        wait: 'Focus a region, then click Subsections at the top of the map.', done: () => 'That’s where they live. Make one now, or come back to it later.' } },
    { ch: 'Organizing', sel: '#btn-reorganize', fallback: '#graph', title: 'Reorganize', text: 'Tick papers and {AI} regroups them by what they’re about, ignoring how they’re filed now, or into regions you name, as many as you ask for. You see and can rename the new regions before anything changes, and Undo puts everything back. A region’s Split up… (in Regions) does this for one region.', keys: ['topics.reorganize'],
      actions: [{ label: 'Open Reorganize', run: cmd('topics.reorganize') }] },
    { ch: 'Organizing', sel: '#btn-chat', title: 'Ask the chat to move things', text: 'You can also just ask: “put the transformer papers in their own region.” The answer ends with a card listing each change, with Apply and Dismiss. Nothing moves until you Apply, and Undo puts it back.', keys: [] },
    { ch: 'Asking', sel: '#btn-chat', title: 'Ask your library', text: 'Ask a question about your papers. The answer cites the pages it used; click a citation to open that passage. At the top of the chat, Regions reads every paper in the regions you pick, and General asks anything with only what you attach.', keys: ['library.ask', 'chat.aboutTopics', 'chat.general'],
      actions: [{ label: 'Open Ask with an example', run: askExample }],
      task: { needs: () => needPapers() || (S.claudeOK ? null : 'Ask needs an AI agent on this computer: pick one in Library settings › AI and privacy, or skip this one.'),
        begin: (t) => { t.msgs = userMsgs(); }, check: (t) => !!S.chatBusy || userMsgs() > t.msgs,
        wait: 'Type a question (or use the example) and click Ask.', done: () => 'Asked. The answer keeps writing while you go on.' } },
    { ch: 'Asking', sel: '#btn-chat', title: 'Research mode and Novelty check', text: 'Next to Quick, Research lets {AI} search your library itself: several searches, opening pages, following citations, shown as a trail. Novelty check takes an idea, shows you the searches first, then reports which published work overlaps and what none of it does.', keys: ['research.toggle', 'novelty.check'] },
    { ch: 'Thinking it through', sel: '#btn-questions', fallback: '#btn-add-menu', title: 'Open questions', text: 'A question keeps collecting evidence as you add papers: passages and notes that support it, argue against it, or relate. The matching runs on this computer and never goes to an AI. Triage with S, A, R and D.', keys: ['question.new', 'questions.open'],
      actions: [{ label: 'Write a question', run: cmd('question.new') }] },
    { ch: 'Thinking it through', sel: '#btn-review:not([hidden])', fallback: '#graph', title: 'Review', text: 'Your own summaries and fragment notes come back for recall, spaced out over time. Write what you remember, reveal, and grade yourself 1 to 4. The Review button appears once there’s something due.', keys: ['library.review'] },
    { ch: 'Writing', sel: '#graph', title: 'Citing and writing', text: 'Cite any paper into Word, Google Docs or LaTeX from the Cite picker, or copy a region’s bibliography. A .bib file can update itself for your LaTeX project (Library settings › Citations). In a note, writing help drafts from only the sources you tick.', keys: ['cite.pick'],
      actions: [{ label: 'Cite a paper', run: cmd('cite.pick') }] },
    { ch: 'Your data', sel: '#btn-view-menu', title: 'Private by default', text: 'Your notes, questions and highlights stay on this computer: no AI reads them until you click the lock in a note’s header. Library settings › AI and privacy shows what was sent and lets you pick the AI tool on this computer.', keys: ['agent.choose'] },
    { ch: 'Your data', sel: '#btn-view-menu', title: 'Phone, browser and voice', text: 'Pair a phone from Library settings › Devices to add papers and read on the go. The Chrome extension saves papers and clips passages from any page. In Ask, the waveform button starts a spoken conversation.', keys: ['extension.clip'] },
    { ch: 'Getting around', sel: '#btn-view-menu', title: 'Commands, shortcuts and this tour', text: 'Find any action by name in the command palette; press ? for every shortcut. Both tours and the full guide stay here under Settings › Tutorials, whenever you want them again.', keys: ['general.palette', 'general.shortcuts', 'help.guide'] },
  ];
  const TOURS = { quick: TOUR, deep: DEEP };
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
  .rb-coach .c-ch { font-size: 12px; color: var(--red); font-weight: 600; }
  .rb-coach .c-step .c-bar { display: block; height: 2px; margin-top: 4px; background: var(--rule); border-radius: 1px; overflow: hidden; }
  .rb-coach .c-step .c-bar > i { display: block; height: 100%; background: var(--ink-2); }
  .rb-coach .c-acts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .rb-coach .c-status { margin-top: 10px; font-size: 12.5px; color: var(--ink-2); display: flex; gap: 6px; align-items: baseline; }
  .rb-coach .c-status.ok { color: var(--ink); }
  .rb-coach .c-status .mk { flex: none; width: 12px; text-align: center; }
  .rb-coach .c-status.ok .mk { color: var(--red); }
  .rb-coach.rb-choose { width: 420px; top: 50%; left: 50%; transform: translate(-50%, -50%); padding: 20px 22px 16px; }
  .rb-choose .c-title { font-size: 16px; margin-bottom: 6px; }
  .rb-choose .c-opts { display: grid; gap: 8px; margin-top: 14px; }
  .rb-choose .c-opt { display: block; width: 100%; text-align: left; padding: 10px 12px; border: 1px solid var(--rule); border-radius: 6px;
    background: transparent; color: var(--ink); font: inherit; cursor: pointer; }
  .rb-choose .c-opt:hover, .rb-choose .c-opt:focus-visible { border-color: var(--ink-2); outline: none; }
  .rb-choose .c-opt b { display: block; font-weight: 600; }
  .rb-choose .c-opt span { color: var(--ink-2); font-size: 12.5px; }
  .rb-choose .c-foot { margin-top: 12px; font-size: 12px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; }
  .rb-choose .c-foot .sp { flex: 1; }
  .rb-coach-ring { position: fixed; z-index: 66; pointer-events: none; border: 1px solid var(--ink-2); border-radius: 6px; }
  .empty-hint { margin-top: 28px; font-size: 12.5px; color: var(--ink-2); }
  .empty-hint kbd { margin: 0 2px; }
  .menu-item .menu-kbd { margin-left: auto; }
  .menu-item.has-kbd { flex-direction: row; align-items: center; }
  `;
  const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);

  let tour = null; // {i, kind, card, ring, shown, sig, + whatever a step's task keeps}
  let chooser = null; // the welcome card: basics or in depth
  let poll = 0;
  function startTour(i = 0, kind = 'quick') {
    endTour(false);
    closeChooser(false);
    rb.closeMenus();
    tour = { i, kind: TOURS[kind] ? kind : 'quick', shown: -1 };
    showStep();
    // a step with a task follows what the user does (a paper arriving, a region focused, a question asked)
    poll = setInterval(() => { if (tour && TOURS[tour.kind][tour.i].task && stepState().sig !== tour.sig) showStep({ refresh: true }); }, 700);
  }
  function endTour(done = true) {
    if (!tour) return;
    clearInterval(poll); poll = 0;
    if (tour.card) tour.card.remove();
    if (tour.ring) tour.ring.remove();
    if (done) { LS.set('tour.done', true); if (tour.kind === 'deep' && tour.i === DEEP.length - 1) LS.set('tour.deepDone', true); }
    tour = null;
  }
  function closeChooser(done = true) {
    if (!chooser) return;
    chooser.remove(); chooser = null;
    if (done) LS.set('tour.done', true);
  }
  /** The welcome card (first run, Settings › Tutorials, the command Help: tutorials): just the basics, or the full tour. */
  function chooseTour({ first = false } = {}) {
    endTour(false);
    closeChooser(false);
    rb.closeMenus();
    const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };
    const card = mk('div', 'rb-coach rb-choose'); card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', 'Tutorials');
    const opt = (kind, name, hint) => {
      const b = mk('button', 'c-opt'); b.type = 'button'; b.dataset.tour = kind;
      b.append(mk('b', null, name), mk('span', null, hint));
      b.onclick = () => startTour(0, kind);
      return b;
    };
    const opts = mk('div', 'c-opts');
    opts.append(
      opt('quick', 'Show me the basics', `Add your first papers, then ${TOUR.length - 1} short tips on the main parts of the app. A few minutes.`),
      opt('deep', 'Get me up to speed', `Add your papers, then ${DEEP.length - 1} steps through everything, tried on your own library: regions, subsections, Reorganize, Find papers, Ask, questions, citing and more.`));
    const later = mk('button', 'btn btn-text btn-sm', first ? 'Not now' : 'Close'); later.type = 'button'; later.onclick = () => closeChooser();
    const foot = mk('div', 'c-foot');
    foot.append(mk('span', null, 'Both stay in Settings › Tutorials.'), mk('span', 'sp'), later);
    card.append(mk('div', 'c-title', first ? 'Welcome to research brain' : 'Tutorials'),
      mk('div', 'c-text', first
        ? 'Both tours start with your own papers: import the ones you have, or find new ones online. Then a quick look around, or the full tour of what it can do?'
        : 'Both start with your own papers. You can leave either one at any step with Esc.'), opts, foot);
    document.body.appendChild(card);
    chooser = card;
    opts.firstChild.focus({ preventScroll: true });
  }
  function target(s) {
    for (const sel of [s.sel, s.fallback]) { const n = sel && $(sel); if (n && n.offsetParent !== null && n.getBoundingClientRect().width) return n; }
    return null;
  }
  /** Where the current step's task stands: blocked (and why), waiting, or done (and what was done). */
  function stepState() {
    const t = TOURS[tour.kind][tour.i].task;
    if (!t) return { sig: '' };
    let needs = null, ok = false, msg = '';
    try {
      needs = t.needs ? t.needs(tour) : null;
      ok = !needs && !!t.check(tour);
      msg = needs || (ok ? t.done(tour) : t.wait);
    } catch (e) { console.error(e); msg = t.wait; }
    return { needs, ok, msg, sig: `${needs ? 'n' : ok ? 'y' : 'w'}|${msg}` };
  }
  function showStep({ refresh = false } = {}) {
    if (!tour) return;
    const steps = TOURS[tour.kind], s = steps[tour.i];
    const fresh = tour.shown !== tour.i;
    if (fresh) { tour.shown = tour.i; if (s.task && s.task.begin) { try { s.task.begin(tour); } catch (e) { console.error(e); } } }
    const t = target(s), st = stepState();
    tour.sig = st.sig;
    if (tour.card) tour.card.remove();
    if (tour.ring) tour.ring.remove();
    tour.ring = null;
    const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };
    const card = mk('div', 'rb-coach'); card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', s.title);
    if (s.task) card.dataset.task = st.needs ? 'blocked' : st.ok ? 'done' : 'waiting';
    const keys = mk('div', 'c-keys');
    for (const id of s.keys || []) {
      const c = CMD.get(id); if (!c || !c.seqs.length) continue;
      const w = mk('span', null); w.style.cssText = 'display:inline-flex;align-items:center;gap:6px;color:var(--ink-2);font-size:12px';
      const caps = CMD.capsEl(c.seqs.slice(0, 1)); caps.style.maxWidth = 'none';
      w.append(caps, c.title);
      keys.append(w);
    }
    // buttons that start the step for the user (hidden while it can't be done yet, and once a task is done)
    const acts = mk('div', 'c-acts');
    if (!st.needs && !st.ok) for (const a of s.actions || []) {
      const b = mk('button', a.primary ? 'btn btn-primary btn-sm' : 'btn btn-sm', a.label); b.type = 'button';
      // after this click has finished (a menu it opens would otherwise see the click as outside it and close)
      b.onclick = () => { setTimeout(() => { try { a.run(); } catch (e) { console.error(e); } }, 0); };
      acts.append(b);
    }
    let status = null;
    if (s.task) {
      status = mk('div', 'c-status' + (st.ok ? ' ok' : ''));
      status.setAttribute('role', 'status');
      status.append(mk('span', 'mk', st.ok ? '✓' : st.needs ? '–' : '…'), mk('span', null, RB.aiText(st.msg)));
    }
    const back = mk('button', 'btn btn-text btn-sm', 'Back'); back.type = 'button'; back.disabled = tour.i === 0; back.onclick = () => { tour.i--; showStep(); };
    const skip = mk('button', 'btn btn-text btn-sm', 'End tour'); skip.type = 'button'; skip.onclick = () => endTour();
    const last = tour.i === steps.length - 1;
    const pending = !!s.task && !st.ok; // not done yet: Next skips it
    const next = mk('button', pending ? 'btn btn-sm' : 'btn btn-primary btn-sm', last ? 'Done' : pending ? 'Skip' : 'Next'); next.type = 'button';
    next.onclick = () => { if (last) endTour(); else { tour.i++; showStep(); } };
    const act = mk('div', 'c-act'); act.append(skip, mk('span', 'sp'), back, next);
    const stepEl = mk('div', 'c-step', `${tour.i + 1} of ${steps.length}`);
    if (s.ch) { // the in-depth tour: its chapter, and a bar for how far along you are
      card.append(mk('div', 'c-ch', s.ch));
      const bar = mk('span', 'c-bar'), fill = mk('i'); fill.style.width = `${((tour.i + 1) / steps.length) * 100}%`; bar.append(fill);
      stepEl.append(bar);
    }
    card.append(stepEl, mk('div', 'c-title', s.title), mk('div', 'c-text', RB.aiText(s.text)), acts.children.length ? acts : '', status || '', keys.children.length ? keys : '', act);
    document.body.appendChild(card);
    tour.card = card;
    const W = window.innerWidth, Hh = window.innerHeight, cw = card.offsetWidth, ch = card.offsetHeight;
    const ring = (r) => {
      const n = mk('div', 'rb-coach-ring');
      Object.assign(n.style, { left: r.left - 4 + 'px', top: r.top - 4 + 'px', width: r.width + 8 + 'px', height: r.height + 8 + 'px' });
      document.body.appendChild(n); tour.ring = n;
    };
    let x = (W - cw) / 2, y = (Hh - ch) / 2;
    if (s.task) {
      // the user works on the map, the panel or the chat now: keep out of their way, bottom left of the map
      // (right of the Regions drawer when it's open), and ring what the step is about
      const drawer = $('.td'); const dr = drawer && drawer.offsetParent !== null ? drawer.getBoundingClientRect() : null;
      x = dr ? dr.right + 16 : 16;
      const legend = $('#legend'); const lr = legend && legend.offsetParent !== null ? legend.getBoundingClientRect() : null;
      y = (lr ? lr.top : Hh) - ch - 12;
      if (t) { const r = t.getBoundingClientRect(); if (!(r.width > W * 0.5 && r.height > Hh * 0.5)) ring(r); }
    } else if (t) {
      const r = t.getBoundingClientRect();
      const big = r.width > W * 0.5 && r.height > Hh * 0.5;
      if (big) { x = r.left + (r.width - cw) / 2; y = r.top + Math.min(120, r.height / 4); }
      else {
        ring(r);
        if (r.width > W * 0.25 && r.height > Hh * 0.4) { x = r.left - cw - 16 > 16 ? r.left - cw - 16 : r.left + 24; y = r.top + 40; } // tall panel: beside it
        else if (r.bottom + 12 + ch < Hh) { x = r.left + r.width / 2 - cw / 2; y = r.bottom + 12; }
        else { x = r.left + r.width / 2 - cw / 2; y = r.top - ch - 12; }
      }
    }
    card.style.left = Math.max(16, Math.min(W - cw - 16, x)) + 'px';
    card.style.top = Math.max(16, Math.min(Hh - ch - 16, y)) + 'px';
    // a new step takes focus; a step redrawn because something changed never does (the user may be typing)
    if (fresh && !refresh) next.focus({ preventScroll: true });
  }
  // Esc: the welcome card first; a running tour only once nothing else is open (it's under the chat, the panel and
  // a focused region, so Esc in them doesn't end the tour while the user tries things)
  CMD.layer({ id: 'tour.choose', order: 95, isOpen: () => !!chooser, close: () => closeChooser() });
  CMD.layer({ id: 'tour', order: 30, isOpen: () => !!tour, close: () => endTour() });
  window.addEventListener('resize', () => { if (tour) showStep({ refresh: true }); });
  rb.tour = { start: startTour, end: endTour, choose: chooseTour, get step() { return tour ? tour.i : -1; }, get kind() { return tour ? tour.kind : null; } };
  rb.openGuide = openGuide;
  // Settings menu › Tutorials
  const tutBtn = $('#btn-tutorials');
  if (tutBtn) tutBtn.addEventListener('click', () => { rb.closeMenus(); chooseTour(); });
  // once, on first run (not under automation, so tests and scripted runs aren't covered by it)
  if (!LS.get('tour.done', false) && !navigator.webdriver) setTimeout(() => { if (!document.querySelector('.rbk-ov:not([hidden]), .modal:not([hidden])')) chooseTour({ first: true }); }, 1200);
})();
