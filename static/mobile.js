/* research brain on phones and tablets (and any window 900px wide or less).
 *
 * The same app, rearranged for a thumb: a bottom tab bar (Library, Graph, Add, Review, Ask), a list-first
 * library with search, status and topic filters and quick to-read triage, notes in a full-height sheet that
 * swipes down to close, a capture screen for links, quick notes, photos and PDFs, and the phone's share
 * sheet (Web Share Target, /share) feeding it. The graph stays: pinch and drag as before; tap a dot to
 * select it, tap it again (or Open) to read it.
 *
 * Lite mode: when this page isn't running on the computer that hosts research brain (a paired phone,
 * see app/pairing.py), things that only make sense there say "Available on your computer": Zotero import,
 * export, auto-updating .bib files, the advanced Library settings sections. PDFs open in the device's own
 * PDF viewer instead of the desktop viewer.
 *
 * Also here: "Phones and tablets" in Library settings (pairing with a QR code, paired devices, revoke),
 * the service worker registration (static/sw.js: app shell only, never notes), and a gestures sheet in
 * place of keyboard shortcuts on touch screens. Desktop layout above 900px is untouched.
 * Depends on window.__rb (static/app.js); loaded last.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb) return;
  const { S, h, api, toast, toastErr } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const body = document.body;
  const CMD = window.RB && window.RB.commands;
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('rb.m.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('rb.m.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };

  const small = window.matchMedia('(max-width: 900px)');
  const coarse = window.matchMedia('(pointer: coarse)');
  const fine = window.matchMedia('(pointer: fine)');
  const M = { on: false, tab: LS.get('tab', 'library'), filter: LS.get('filter', 'all'), topic: null, topics: null,
    local: true, device: null, sel: null, built: false, prevTab: 'library' };

  // ====================================================================== icons (hairline, 20px)
  const ICON = {
    library: '<path d="M4 4.5h12M4 8.5h12M4 12.5h8M4 16.5h10"/>',
    graph: '<circle cx="5" cy="6" r="1.8"/><circle cx="15" cy="5" r="1.8"/><circle cx="10" cy="15" r="1.8"/><path d="M6.8 6 H11 L13.2 5.2 M6 7.6 L9.2 13.4 M14.4 6.7 L10.8 13.4"/>',
    add: '<path d="M10 4v12M4 10h12"/>',
    review: '<path d="M4 10a6 6 0 1 0 1.8-4.3"/><path d="M4 4.5v3.2h3.2"/>',
    ask: '<path d="M4 5.5h12v7.5H9l-3.2 2.6V13H4z"/>',
    lock: '<rect x="5" y="9" width="10" height="7.5" rx="1"/><path d="M7 9V7a3 3 0 0 1 6 0v2"/>',
  };
  const icon = (name, size = 20) => {
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('viewBox', '0 0 20 20'); s.setAttribute('width', size); s.setAttribute('height', size);
    s.setAttribute('aria-hidden', 'true'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
    s.setAttribute('stroke-width', '1.3'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    s.innerHTML = ICON[name];
    return s;
  };

  // ====================================================================== device: this computer or a paired phone
  async function loadDevice() {
    try {
      const d = await api('GET', '/api/device');
      M.local = !!d.local; M.device = d.device || null;
    } catch { M.local = true; }
    body.classList.toggle('rb-remote', !M.local);
  }

  function syncTouch() {
    const touch = coarse.matches || (!fine.matches && ('ontouchstart' in window || navigator.maxTouchPoints > 0));
    body.classList.toggle('rb-touch', touch);
  }

  // ====================================================================== shell
  const TABS = [['library', 'Library'], ['graph', 'Graph'], ['add', 'Add'], ['review', 'Review'], ['ask', 'Ask']];
  let tabbar, libView, addView, peek, viewTitle;

  function build() {
    if (M.built) return;
    M.built = true;
    tabbar = h('nav', { id: 'm-tabs', class: 'm-tabs', 'aria-label': 'Sections' });
    for (const [id, label] of TABS) {
      const b = h('button', { type: 'button', class: 'm-tab', 'data-tab': id, 'aria-label': label, onclick: () => setTab(id, { user: true }) },
        icon(id), h('span', { class: 'm-tab-l', text: label }));
      if (id === 'review') b.appendChild(h('span', { class: 'm-badge tnum', id: 'm-review-n' }));
      tabbar.appendChild(b);
    }
    viewTitle = h('span', { class: 'm-title', id: 'm-title', 'aria-live': 'polite' });
    $('#topbar .brand').after(viewTitle);
    libView = buildLibrary();
    addView = buildCapture();
    peek = h('div', { class: 'm-peek', id: 'm-peek', hidden: true });
    body.append(libView, addView, peek, tabbar);
    // the note sheet: a grab handle that drags the sheet down to close it
    const head = $('#panel .panel-head');
    if (head && !$('.m-grab', head)) head.prepend(h('div', { class: 'm-grab', 'aria-hidden': 'true' }));
    sheetDrag($('#panel'), head, () => rb.closePanel());
  }

  function syncShell() {
    const on = small.matches;
    if (on === M.on) return;
    M.on = on;
    body.classList.toggle('rb-m', on);
    if (on) {
      build();
      setTab(M.tab === 'review' || M.tab === 'ask' ? 'library' : M.tab);
    } else {
      for (const el of [libView, addView, peek]) if (el) el.hidden = true;
      M.sel = null;
    }
    setTimeout(() => { const fg = rb.fg; const g = $('#graph'); if (fg && g) fg.width(g.clientWidth).height(g.clientHeight); }, 50);
  }

  function setTab(t, { user = false } = {}) {
    if (!M.on) return;
    if (t !== 'review' && t !== 'ask') { M.tab = t; LS.set('tab', t); }
    if (t !== 'ask' && !$('#chat').hidden) rb.closeChat();
    if (t !== 'review' && body.classList.contains('review-open')) { const x = $('.rv .rv-head .btn-icon'); if (x) x.click(); }
    if (user && !$('#panel').hidden) rb.closePanel();
    libView.hidden = t !== 'library';
    addView.hidden = t !== 'add';
    peek.hidden = t !== 'graph' || !M.sel;
    body.dataset.mtab = t;
    for (const b of $$('.m-tab', tabbar)) {
      const on = b.dataset.tab === t;
      b.classList.toggle('on', on);
      if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    }
    viewTitle.textContent = ({ library: 'Library', graph: 'Graph', add: 'Add', review: 'Review', ask: 'Ask your library' })[t];
    if (t === 'library') renderLibrary();
    if (t === 'graph') setTimeout(() => { const fg = rb.fg; if (fg && user) fg.zoomToFit(0, 40); }, 60);
    if (t === 'review') { M.prevTab = M.tab; if (CMD) CMD.run('library.review'); }
    if (t === 'ask') {
      M.prevTab = M.tab;
      rb.openChat();
      if (body.classList.contains('rb-touch')) setTimeout(() => { const i = $('#chat-input'); if (i && document.activeElement === i) i.blur(); }, 30);
    }
  }

  // leaving Review or Ask by their own close buttons returns to the tab you came from
  new MutationObserver(() => {
    if (!M.on) return;
    const t = body.dataset.mtab;
    if (t === 'review' && !body.classList.contains('review-open')) setTab(M.prevTab || 'library');
    if (t === 'ask' && $('#chat').hidden) setTab(M.prevTab || 'library');
    if (t !== 'review' && body.classList.contains('review-open')) markTab('review');
    if (t !== 'ask' && !$('#chat').hidden && t) markTab('ask');
  }).observe(body, { attributes: true, attributeFilter: ['class'] });
  new MutationObserver(() => { if (M.on && body.dataset.mtab !== 'ask' && !$('#chat').hidden) markTab('ask'); })
    .observe($('#chat'), { attributes: true, attributeFilter: ['hidden'] });
  function markTab(t) { M.prevTab = M.tab; body.dataset.mtab = t; for (const b of $$('.m-tab', tabbar)) b.classList.toggle('on', b.dataset.tab === t); libView.hidden = true; addView.hidden = true; peek.hidden = true; }

  // ====================================================================== drag a sheet down to close it
  function sheetDrag(sheet, handle, close) {
    if (!sheet || !handle) return;
    let y0 = null, dy = 0, id = null;
    handle.addEventListener('pointerdown', (e) => {
      if (!M.on || e.button > 0 || e.target.closest('button, input, a, textarea, select')) return;
      y0 = e.clientY; dy = 0; id = e.pointerId;
      try { handle.setPointerCapture(id); } catch { /* old browsers */ }
      sheet.style.transition = 'none';
    });
    handle.addEventListener('pointermove', (e) => {
      if (y0 == null || e.pointerId !== id) return;
      dy = Math.max(0, e.clientY - y0);
      sheet.style.transform = `translateY(${dy}px)`;
    });
    const end = (e) => {
      if (y0 == null || (e && e.pointerId !== id)) return;
      y0 = null;
      sheet.style.transition = '';
      if (dy > Math.min(140, sheet.clientHeight * 0.22)) {
        sheet.style.transform = `translateY(${sheet.clientHeight}px)`;
        setTimeout(() => { sheet.style.transform = ''; close(); }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 160);
      } else sheet.style.transform = '';
    };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }

  // ====================================================================== library list
  const STATUS = [['to-read', 'To read'], ['reading', 'Reading'], ['read', 'Read'], ['later', 'Later'], ['not-relevant', 'Not relevant']];
  const ST_LABEL = Object.fromEntries(STATUS);
  let libList, libSearch, libChips, libCount;

  function buildLibrary() {
    libSearch = h('input', { type: 'search', id: 'm-search', placeholder: 'Search your library', spellcheck: false,
      'aria-label': 'Search your library', autocomplete: 'off', enterkeyhint: 'search' });
    libSearch.addEventListener('input', () => {
      S.search = libSearch.value;
      const top = $('#search-input'); if (top) top.value = libSearch.value;
      rb.updateSearch();
      renderLibrary();
    });
    libSearch.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); libSearch.blur(); } });
    libChips = h('div', { class: 'm-chips', role: 'tablist', 'aria-label': 'Show' });
    libCount = h('div', { class: 'm-count tnum', 'aria-live': 'polite' });
    libList = h('div', { class: 'm-list', id: 'm-list' });
    return h('section', { class: 'm-view m-lib', id: 'm-library', 'aria-label': 'Library', hidden: true },
      h('div', { class: 'm-lib-head' }, h('div', { class: 'm-search' }, libSearch), libChips),
      h('div', { class: 'm-scroll' }, libCount, libList));
  }

  async function loadTopics() {
    try { M.topics = await api('GET', '/api/topics'); } catch { M.topics = M.topics || []; }
  }
  async function topicMembers(slug) {
    if (rb.topicSets && rb.topicSets.get(slug)) return rb.topicSets.get(slug);
    try { const d = await api('GET', `/api/topics/${encodeURIComponent(slug)}`); const s = new Set(d.members || []); if (rb.topicSets) rb.topicSets.set(slug, s); return s; }
    catch { return new Set(); }
  }

  function chips() {
    const all = [...S.nodes.values()];
    const count = (fn) => all.filter(fn).length;
    const list = [['all', 'All'], ['to-read', 'To read'], ['reading', 'Reading'], ['later', 'Later'], ['read', 'Read'], ['notes', 'Your notes']];
    if (all.some((n) => n.type === 'question')) list.push(['questions', 'Questions']);
    for (const t of (M.topics || [])) list.push(['topic:' + t.slug, t.name]);
    libChips.replaceChildren(...list.map(([k, label]) => {
      const n = k === 'all' ? count((x) => x.type !== 'fragment') : k === 'notes' ? count((x) => x.type === 'note')
        : k === 'questions' ? count((x) => x.type === 'question') : k.startsWith('topic:') ? null : count((x) => x.status === k);
      const on = M.filter === k;
      return h('button', { type: 'button', role: 'tab', 'aria-selected': String(on), class: 'm-chip' + (on ? ' on' : '') + (k.startsWith('topic:') ? ' topic' : ''),
        'data-f': k, onclick: () => { M.filter = k; LS.set('filter', k); renderLibrary(); libList.parentElement.scrollTop = 0; } },
        h('span', { text: label }), n != null ? h('span', { class: 'n tnum', text: String(n) }) : null);
    }));
    const on = $('.m-chip.on', libChips);
    if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  const authorsShort = (a) => !a || !a.length ? '' : a.length > 2 ? `${a[0]} et al.` : a.join(' and ');

  function row(n, { triage = false } = {}) {
    const kind = n.type === 'paper' ? 'paper' : n.type === 'fragment' ? 'fragment' : n.type === 'question' ? 'question' : 'note';
    const sub = [];
    if (n.type === 'fragment') {
      const p = S.nodes.get(n.parent) || (S.folded && S.folded.get(n.parent));
      if (p) sub.push(h('span', { text: 'in ' + p.title }));
    } else {
      if (n.authors && n.authors.length) sub.push(h('span', { class: 'au', text: authorsShort(n.authors) }));
      if (n.year) sub.push(h('span', { class: 'tnum', text: String(n.year) }));
    }
    if (n.status && ST_LABEL[n.status]) sub.push(h('span', { class: 'st', 'data-st': n.status, text: ST_LABEL[n.status] }));
    if (n.private) sub.push(h('span', { class: 'm-lock', title: 'Private: never sent to an AI agent' }, icon('lock', 12), 'Private'));
    const title = n.type === 'fragment' && S.quoteIdx && S.quoteIdx.get(n.id) ? S.quoteIdx.get(n.id) : (n.title || n.id);
    const main = h('button', { type: 'button', class: 'm-row-main', onclick: () => rb.openNote(n.id) },
      h('span', { class: `m-kind ${kind}`, 'aria-hidden': 'true' }),
      h('span', { class: 'm-row-text' },
        h('span', { class: 'm-row-title' + (n.type === 'fragment' ? ' quote' : ''), text: title }),
        sub.length ? h('span', { class: 'm-row-sub' }, sub) : null));
    const el = h('div', { class: 'm-row', 'data-id': n.id }, main);
    if (triage) {
      const act = (st, label) => h('button', { type: 'button', class: 'btn btn-sm', text: label, onclick: () => setStatus(n, st, label) });
      el.appendChild(h('div', { class: 'm-triage' }, act('reading', 'Start reading'), act('later', 'Later'), act('not-relevant', 'Not relevant')));
    }
    return el;
  }

  async function setStatus(n, st, label) {
    const prev = n.status || '';
    n.status = st; renderLibrary();
    try {
      await api('PUT', `/api/notes/${encodeURIComponent(n.id)}`, { status: st });
      toast(`${label === 'Start reading' ? 'Moved to Reading' : label === 'Later' ? 'Parked for later' : 'Marked not relevant'}: “${rb.truncate(n.title, 50)}”`, 'ok', { ms: 2500 });
    } catch (e) { n.status = prev; renderLibrary(); toastErr(e, 'Could not change the status'); }
  }

  let renderSeq = 0;
  async function renderLibrary() {
    if (!M.on || !libList) return;
    const seq = ++renderSeq;
    if (!M.topics) await loadTopics();
    if (seq !== renderSeq) return;
    chips();
    const q = (S.search || '').trim();
    if (libSearch && document.activeElement !== libSearch && libSearch.value !== (S.search || '')) libSearch.value = S.search || '';
    let items = q ? (S.ranked || []).slice() : [...S.nodes.values()].filter((n) => n.type !== 'fragment');
    const f = M.filter;
    let topicSet = null;
    if (f.startsWith('topic:')) { topicSet = await topicMembers(f.slice(6)); if (seq !== renderSeq) return; }
    items = items.filter((n) => {
      if (f === 'all') return true;
      if (f === 'notes') return n.type === 'note';
      if (f === 'questions') return n.type === 'question';
      if (topicSet) return topicSet.has(n.id);
      return n.status === f;
    });
    const triage = f === 'to-read' && !q;
    const sections = [];
    if (!q && f === 'all') {
      const order = [['reading', 'Reading now'], ['to-read', 'To read'], ['', 'Everything else']];
      const pick = (st) => items.filter((n) => (st ? n.status === st : !['reading', 'to-read'].includes(n.status)));
      for (const [st, label] of order) {
        const xs = pick(st).sort(byTitle);
        if (xs.length) sections.push([label, xs]);
      }
    } else {
      sections.push([null, q ? items : items.sort(byTitle)]);
    }
    const total = items.length;
    libCount.textContent = q ? `${total} match${total === 1 ? '' : 'es'} for “${rb.truncate(q, 40)}”` : '';
    libCount.hidden = !q;
    if (!total) {
      libList.replaceChildren(h('div', { class: 'm-empty' },
        h('p', { text: q ? 'Nothing matches that search.' : S.nodes.size ? 'Nothing here yet.' : 'Your library is empty.' }),
        S.nodes.size && !q ? null : h('button', { type: 'button', class: 'btn btn-primary', text: 'Add a paper or note', onclick: () => setTab('add', { user: true }) })));
      return;
    }
    const frag = document.createDocumentFragment();
    for (const [label, xs] of sections) {
      if (label) frag.appendChild(h('div', { class: 'm-sec' }, h('span', { text: label }), h('span', { class: 'n tnum', text: String(xs.length) })));
      for (const n of xs.slice(0, 400)) frag.appendChild(row(n, { triage }));
    }
    libList.replaceChildren(frag);
  }
  const byTitle = (a, b) => (a.title || '').localeCompare(b.title || '');

  // ====================================================================== capture (Add tab and the share sheet)
  let shareBox, linkIn, linkBtn, noteIn, noteBtn, photoIn;
  function buildCapture() {
    linkIn = h('input', { type: 'text', id: 'm-link', inputmode: 'url', autocomplete: 'off', spellcheck: false, enterkeyhint: 'done',
      placeholder: 'https://…, 10.1063/…, 2106.09685 or a title', 'aria-label': 'Link, DOI, arXiv id or title' });
    linkBtn = h('button', { type: 'submit', class: 'btn btn-primary', text: 'Save to read' });
    const linkForm = h('form', { class: 'm-cap', autocomplete: 'off' },
      h('label', { for: 'm-link', class: 'm-cap-h', text: 'A paper or a page' }),
      h('p', { class: 'm-cap-p', text: 'Saved as to read. Papers with a DOI or arXiv id get their details looked up on your computer.' }),
      linkIn, linkBtn);
    linkForm.addEventListener('submit', (e) => { e.preventDefault(); saveLink(); });
    noteIn = h('textarea', { id: 'm-note', rows: 4, placeholder: 'An idea, a question, a thing to check', 'aria-label': 'Quick note' });
    noteBtn = h('button', { type: 'submit', class: 'btn', text: 'Save note' });
    const noteForm = h('form', { class: 'm-cap', autocomplete: 'off' },
      h('label', { for: 'm-note', class: 'm-cap-h mine-h', text: 'A quick note' }), noteIn, noteBtn);
    noteForm.addEventListener('submit', (e) => { e.preventDefault(); saveNote(); });
    photoIn = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, id: 'm-photo-in' });
    photoIn.addEventListener('change', () => { const f = photoIn.files && photoIn.files[0]; photoIn.value = ''; if (f) savePhoto(f); });
    const more = h('div', { class: 'm-cap m-cap-more' },
      h('button', { type: 'button', class: 'm-cap-item', onclick: () => photoIn.click() },
        h('span', { class: 'm-cap-t', text: 'Photo of a whiteboard or page' }), h('span', { class: 'm-cap-s', text: 'Saved as a note with the photo attached' })),
      h('button', { type: 'button', class: 'm-cap-item', onclick: () => rb.pickPdf() },
        h('span', { class: 'm-cap-t', text: 'Import a PDF' }), h('span', { class: 'm-cap-s', text: 'From Files or Downloads' })),
      $('#btn-new-question') ? h('button', { type: 'button', class: 'm-cap-item', onclick: () => $('#btn-new-question').click() },
        h('span', { class: 'm-cap-t', text: 'A new open question' }), h('span', { class: 'm-cap-s', text: 'It collects evidence as your library grows' })) : null,
      h('button', { type: 'button', class: 'm-cap-item computer-only', onclick: () => { const b = $('#btn-zotero'); if (b) b.click(); } },
        h('span', { class: 'm-cap-t', text: 'Import from Zotero' }), h('span', { class: 'm-cap-s', text: 'Papers, notes and highlights' })),
      photoIn);
    shareBox = h('div', { class: 'm-shared', hidden: true });
    return h('section', { class: 'm-view m-add', id: 'm-add', 'aria-label': 'Add', hidden: true },
      h('div', { class: 'm-scroll' }, h('div', { class: 'm-add-col' }, shareBox, linkForm, noteForm, more)));
  }

  const URL_RE = /\bhttps?:\/\/[^\s<>"']+/i;
  let shared = null;

  async function saveLink() {
    const q = linkIn.value.trim();
    if (!q) { linkIn.focus(); toast('Paste a link, DOI, arXiv id or title first.', 'info'); return; }
    busy(linkBtn, 'Saving');
    try {
      let note = null;
      try { note = await api('POST', '/api/import/lookup', { query: q }); }
      catch (e) {
        const url = (URL_RE.exec(q) || [])[0];
        if (!url) throw e;
        // no identifier the lookup could resolve (or the computer is offline): keep the link to read later
        note = await api('POST', '/api/notes', { type: 'paper', title: (shared && shared.title) || titleFromUrl(url), url, status: 'to-read',
          saved_because: shared && shared.text && !URL_RE.test(shared.text) ? shared.text.slice(0, 300) : '' });
      }
      if (note && !note.duplicate && !note.status) { try { await api('PUT', `/api/notes/${encodeURIComponent(note.id)}`, { status: 'to-read' }); } catch { /* keep going */ } }
      toast(note.duplicate ? `Already in your library: “${rb.truncate(note.title, 60)}”` : `Saved to read: “${rb.truncate(note.title, 60)}”`, 'ok');
      linkIn.value = ''; clearShared();
      await rb.refreshGraph();
      rb.openNote(note.id);
    } catch (e) { toastErr(e, 'Could not save it'); }
    finally { done(linkBtn, 'Save to read'); }
  }
  function titleFromUrl(u) {
    try { const x = new URL(u); const last = decodeURIComponent(x.pathname.split('/').filter(Boolean).pop() || ''); return (last ? last.replace(/[-_]+/g, ' ').replace(/\.\w+$/, '') + ', ' : '') + x.hostname.replace(/^www\./, ''); }
    catch { return u; }
  }
  async function saveNote() {
    const text = noteIn.value.trim();
    if (!text) { noteIn.focus(); return; }
    busy(noteBtn, 'Saving');
    const [first, ...rest] = text.split('\n');
    try {
      const n = await api('POST', '/api/notes', { type: 'note', title: rb.truncate(first, 120), body: rest.join('\n').trim() || (first.length > 120 ? text : '') });
      noteIn.value = ''; clearShared();
      toast('Note saved', 'ok', { ms: 2000 });
      await rb.refreshGraph();
      rb.openNote(n.id);
    } catch (e) { toastErr(e, 'Could not save the note'); }
    finally { done(noteBtn, 'Save note'); }
  }
  async function shrink(file) {
    // re-encode big photos (and HEIC) as JPEG, at most 2400 px on the long side
    try {
      if (!window.createImageBitmap) return file;
      const bmp = await createImageBitmap(file);
      const k = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
      if (k === 1 && /^image\/(jpeg|png|webp)$/.test(file.type) && file.size < 6e6) return file;
      const c = document.createElement('canvas'); c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
      c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
      return await new Promise((res) => c.toBlob((b) => res(b || file), 'image/jpeg', 0.86));
    } catch { return file; }
  }
  async function savePhoto(file) {
    const title = window.prompt ? (window.prompt('Title for this photo', 'Whiteboard') || 'Photo') : 'Photo';
    const t = toast('Saving the photo…', 'info', { ms: 60000 });
    try {
      const blob = await shrink(file);
      const r = await fetch(`/api/capture/photo?title=${encodeURIComponent(title)}`, { method: 'POST', headers: { 'Content-Type': blob.type || 'image/jpeg' }, body: blob });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      if (t && t.remove) t.remove();
      toast('Photo saved as a note', 'ok', { ms: 2000 });
      await rb.refreshGraph();
      rb.openNote(d.id);
    } catch (e) { if (t && t.remove) t.remove(); toastErr(e, 'Could not save the photo'); }
  }
  function busy(b, label) { b.disabled = true; b.dataset.l = b.textContent; b.replaceChildren(h('span', { class: 'spin' }), label); }
  function done(b, label) { b.disabled = false; b.textContent = label; }

  function showShared(p) {
    shared = p;
    const url = p.url || (URL_RE.exec(p.text || '') || [])[0] || '';
    shareBox.hidden = false;
    shareBox.replaceChildren(h('div', { class: 'm-shared-h', text: 'Shared with research brain' }),
      p.title ? h('div', { class: 'm-shared-t', text: p.title }) : null,
      url ? h('div', { class: 'm-shared-u ident', text: url }) : null);
    if (url) linkIn.value = url;
    else if (p.text) noteIn.value = (p.title ? p.title + '\n' : '') + p.text;
  }
  function clearShared() { shared = null; if (shareBox) { shareBox.hidden = true; shareBox.replaceChildren(); } }

  function readShareTarget() {
    const u = new URL(location.href);
    const tab = u.searchParams.get('tab');
    if (u.pathname === '/share') {
      const p = { title: (u.searchParams.get('title') || '').slice(0, 300), text: (u.searchParams.get('text') || '').slice(0, 4000), url: (u.searchParams.get('url') || '').slice(0, 2000) };
      history.replaceState(null, '', '/');
      if (M.on) { setTab('add'); showShared(p); }
      else {  // a wide window: the Add menu with the link filled in
        const link = p.url || (URL_RE.exec(p.text) || [])[0] || p.title;
        rb.openMenu('#btn-add-menu', '#add-menu');
        const i = $('#lookup-input'); if (i) { i.value = link; i.select(); }
      }
      return;
    }
    if (tab && M.on && TABS.some(([t]) => t === tab)) { history.replaceState(null, '', '/'); setTab(tab); }
  }

  // ====================================================================== graph: tap to select, tap again to open
  const prevHook = S.nodeClickHook;
  S.nodeClickHook = (n, e) => {
    if (M.on) {
      if (M.sel === n.id) { M.sel = null; peek.hidden = true; return false; }  // second tap: app.js opens it
      M.sel = n.id;
      S.selected = n.id; rb.kick(300);
      showPeek(n);
      return true;
    }
    return prevHook ? prevHook(n, e) : false;
  };
  function showPeek(n) {
    const sub = [n.type === 'paper' ? 'Paper' : n.type === 'fragment' ? 'Fragment' : n.type === 'question' ? 'Question' : 'Note'];
    if (n.year) sub.push(String(n.year));
    if (n.authors && n.authors.length) sub.push(authorsShort(n.authors));
    peek.replaceChildren(
      h('div', { class: 'm-peek-text' }, h('div', { class: 'm-peek-t', text: n.title || n.id }), h('div', { class: 'm-peek-s' }, sub.map((s) => h('span', { text: s })))),
      h('button', { type: 'button', class: 'btn btn-primary', text: 'Open', onclick: () => { M.sel = null; peek.hidden = true; rb.openNote(n.id, { center: true }); } }));
    peek.hidden = body.dataset.mtab !== 'graph';
  }
  function wireGraphBackground() {
    const fg = rb.fg;
    if (!fg || !fg.onBackgroundClick) return;
    // don't replace the app's own background handler (region tags use it); add to it
    S.backgroundClickHook = () => { if (M.on && M.sel) { M.sel = null; peek.hidden = true; S.selected = S.cur ? S.cur.id : null; rb.kick(300); } };
  }

  // ====================================================================== lite: desktop-only things
  // PDFs open in the device's own viewer (the desktop viewer needs a mouse and a wide window)
  document.addEventListener('click', (e) => {
    if (!M.on) return;
    const a = e.target.closest && e.target.closest('a[href^="/viewer/"]');
    if (!a) return;
    const m = /^\/viewer\/([^#?]+)(?:#page=(\d+))?/.exec(a.getAttribute('href'));
    if (!m) return;
    e.preventDefault();
    window.open(`/pdf/${m[1]}${m[2] ? '#page=' + m[2] : ''}`, '_blank', 'noopener');
  }, true);

  // Library settings: sections that change this computer say so on a paired device
  const COMPUTER_ONLY_SECTIONS = /^(Library|Profiles|Library profiles|Citations|Open-access PDFs|Backup|Export and import|Keep UI state|Where your library|Import|AI agent)/i;
  new MutationObserver(() => {
    const dlg = document.getElementById('library-settings');
    if (!dlg || dlg.__m) return;
    dlg.__m = true;
    if (!M.local) {
      for (const sec of $$('.ls-sec', dlg)) {
        const head = $('.ls-h', sec);
        if (!head || sec.id === 'ls-devices' || !COMPUTER_ONLY_SECTIONS.test(head.textContent.trim())) continue;
        sec.classList.add('m-computer-only');
        sec.append(h('p', { class: 'm-elsewhere', text: 'Available on your computer.' }));
      }
    }
    // the card opens at its top, with the title in view
    const card = $('.ls-card', dlg); if (card) requestAnimationFrame(() => { card.scrollTop = 0; });
  }).observe(body, { childList: true });

  // ====================================================================== touch: gestures instead of shortcuts
  function openTips() {
    const close = () => { wrap.remove(); };
    const tips = [
      ['Library', 'Search, filter by status or topic, and triage what to read next.'],
      ['Graph', 'Pinch to zoom and drag to move. Tap a dot to select it; tap it again to open it.'],
      ['Notes', 'A note opens as a sheet. Drag its top edge down to close it.'],
      ['Share', 'Share a link from any app to research brain to save it to read.'],
      ['Install', 'Add research brain to your home screen to open it like an app.'],
    ];
    const wrap = h('div', { class: 'modal m-tips', role: 'presentation' },
      h('div', { class: 'modal-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'm-tips-t' },
        h('div', { class: 'modal-title', id: 'm-tips-t', text: 'Getting around' }),
        h('dl', { class: 'm-tips-list' }, tips.map(([t, d]) => [h('dt', { text: t }), h('dd', { text: d })])),
        h('div', { class: 'modal-actions' }, h('button', { type: 'button', class: 'btn btn-primary', text: 'Done', onclick: close }))));
    wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });
    body.appendChild(wrap);
  }
  function wireTips() {
    const item = h('button', { id: 'btn-tips', class: 'menu-item', type: 'button', onclick: () => { rb.closeMenus(); openTips(); } },
      'Tips', h('span', { class: 'menu-hint', text: 'gestures for touch screens' }));
    const sc = $('#btn-shortcuts'); if (sc) sc.after(item);
  }

  // ====================================================================== Library settings: phones and tablets
  const ago = (iso) => { try { return rb.ago(iso); } catch { return iso || ''; } };
  function settingsSection(say) {
    const sec = h('section', { class: 'ls-sec', id: 'ls-devices' }, h('div', { class: 'ls-h', text: 'Phones and tablets' }));
    if (!M.local) {
      sec.append(h('p', { class: 'ls-p', text: `This device${M.device ? ` (${M.device.name})` : ''} is paired with your computer. Unpair it to stop it opening your library.` }),
        h('div', { class: 'ls-row' }, h('button', { type: 'button', class: 'btn', text: 'Unpair this device', onclick: async () => {
          const ok = await rb.confirmDialog('Unpair this device?', 'It stops opening your library until you pair it again from your computer.', 'Unpair');
          if (!ok) return;
          try { await api('POST', '/api/device/forget'); location.reload(); } catch (e) { say(e.message, true); }
        } })));
      return sec;
    }
    const list = h('ul', { class: 'ls-profiles m-devs' });
    const pairBox = h('div', { class: 'm-pair', hidden: true });
    const pairBtn = h('button', { type: 'button', class: 'btn', text: 'Pair a phone' });
    const tokBtn = h('button', { type: 'button', class: 'btn btn-text', text: 'Token for the iOS Shortcut' });
    sec.append(h('p', { class: 'ls-p', text: 'Open your library on your phone over Tailscale. A paired phone can read, search, review, ask and save new things; it can’t change this computer’s settings. Nothing is cached on the phone.' }),
      list, h('div', { class: 'ls-row' }, pairBtn, tokBtn), pairBox);
    tokBtn.addEventListener('click', async () => {
      let d;
      try { d = await api('POST', '/api/devices/token', { name: 'iOS Shortcut' }); } catch (e) { say(e.message, true); return; }
      pairBox.hidden = false;
      const code = h('div', { class: 'ls-path', text: `Bearer ${d.token}` });
      pairBox.replaceChildren(h('div', { class: 'm-pair-side' },
        h('p', { class: 'ls-p', text: 'In the Shortcut’s Get Contents of URL action, add a header named Authorization with this value. It is shown only now; revoke “iOS Shortcut” below to stop it.' }),
        code, h('div', { class: 'ls-row' },
          h('button', { type: 'button', class: 'btn btn-sm', text: 'Copy', onclick: async () => { try { await navigator.clipboard.writeText(code.textContent); say('Copied.'); } catch { say('Select the text and copy it.', true); } } }),
          h('button', { type: 'button', class: 'btn btn-sm btn-text', text: 'Done', onclick: () => { pairBox.hidden = true; pairBox.replaceChildren(); } }))));
      loadDevs();
    });
    const loadDevs = async () => {
      let d;
      try { d = await api('GET', '/api/devices'); } catch (e) { list.replaceChildren(h('li', { class: 'n', text: e.message })); return; }
      list.replaceChildren(...(d.devices.length ? d.devices.map((x) => h('li', {},
        h('span', { class: 'nm', text: x.name }), h('span', { class: 'n', text: `last used ${ago(x.last_seen)}` }), h('span', { class: 'grow' }),
        h('button', { type: 'button', class: 'btn btn-danger btn-sm', text: 'Revoke', onclick: async () => {
          const ok = await rb.confirmDialog(`Revoke ${x.name}?`, 'It stops opening your library at once. You can pair it again later.', 'Revoke');
          if (!ok) return;
          try { await api('DELETE', `/api/devices/${encodeURIComponent(x.id)}`); say(`${x.name} can no longer open your library.`); loadDevs(); }
          catch (e) { say(e.message, true); }
        } }))) : [h('li', { class: 'n', text: 'No paired devices.' })]));
    };
    loadDevs();
    let timer = null;
    pairBtn.addEventListener('click', async () => {
      clearInterval(timer);
      let d;
      try { d = await api('POST', '/api/pair/start'); } catch (e) { say(e.message, true); return; }
      pairBox.hidden = false;
      if (!d.urls.length) {
        pairBox.replaceChildren(h('p', { class: 'ls-p', text: 'Your computer isn’t reachable from other devices yet. Start research brain with RB_HOST set to your Tailscale address, or put it behind `tailscale serve` and list the name in RB_ALLOWED_HOSTS. See docs/mobile-capture.md.' }));
        return;
      }
      const qr = h('div', { class: 'm-qr', role: 'img', 'aria-label': 'QR code for pairing' });
      const link = h('div', { class: 'ls-path m-pair-url' });
      const left = h('span', { class: 'tnum' });
      const pick = h('select', { 'aria-label': 'Address the phone uses' }, d.urls.map((u, i) => h('option', { value: String(i), text: `${u.url}  (${u.label})` })));
      const draw = () => {
        const u = d.urls[Number(pick.value) || 0];
        try { qr.innerHTML = window.RBQR.svg(window.RBQR.encode(u.pair_url), { quiet: 3 }); } catch { qr.textContent = 'QR code unavailable'; }
        link.textContent = u.pair_url;
      };
      pick.addEventListener('change', draw);
      const t0 = Date.now();
      const tick = () => {
        const s = Math.max(0, d.expires_in - Math.round((Date.now() - t0) / 1000));
        left.textContent = s ? `Expires in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}. Works once.` : 'Expired. Make a new code.';
        if (!s) { clearInterval(timer); qr.classList.add('expired'); }
      };
      pairBox.replaceChildren(qr, h('div', { class: 'm-pair-side' },
        h('p', { class: 'ls-p', text: 'Scan with the phone’s camera, then tap Pair this phone.' }),
        d.urls.length > 1 ? pick : null, link, h('p', { class: 'ls-p' }, left),
        h('div', { class: 'ls-row' }, h('button', { type: 'button', class: 'btn btn-sm', text: 'Done', onclick: async () => {
          clearInterval(timer); pairBox.hidden = true; try { await api('DELETE', '/api/pair/codes'); } catch { /* fine */ } loadDevs();
        } }))));
      draw(); tick(); timer = setInterval(() => { tick(); if (!pairBox.isConnected) clearInterval(timer); }, 1000);
      // refresh the list while the code is up, so the new phone shows when it pairs
      const poll = setInterval(() => { if (!pairBox.isConnected || pairBox.hidden) { clearInterval(poll); return; } loadDevs(); }, 4000);
    });
    return sec;
  }
  window.__rbPair = { settingsSection };

  // ====================================================================== review badge on the tab
  function syncReviewBadge() {
    const n = $('#btn-review .review-n');
    const b = document.getElementById('m-review-n');
    if (b) b.textContent = n ? n.textContent : '';
  }

  // ====================================================================== service worker (app shell only)
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {}); });
  }

  // ====================================================================== boot
  syncTouch();
  coarse.addEventListener && coarse.addEventListener('change', syncTouch);
  small.addEventListener && small.addEventListener('change', syncShell);
  wireTips();
  const boot = async () => {
    await loadDevice();
    syncShell();
    wireGraphBackground();
    readShareTarget();
    const rn = $('#btn-review .review-n');
    if (rn) new MutationObserver(syncReviewBadge).observe(rn, { childList: true, characterData: true, subtree: true });
    syncReviewBadge();
  };
  // app.js boots asynchronously; wait until the graph has loaded once
  const ready = () => S.nodes && (S.nodes.size || S.loaded || S.graphLoaded);
  let tries = 0;
  (function wait() { if (ready() || tries++ > 40) boot(); else setTimeout(wait, 100); })();
  // keep the list fresh when the graph reloads (new notes, status changes elsewhere)
  let relist = null;
  setInterval(() => {
    if (!M.on || body.dataset.mtab !== 'library' || !libList) return;
    const sig = S.nodes.size + ':' + [...S.nodes.values()].reduce((a, n) => a + (n.status || '').length, 0);
    if (sig !== relist) { relist = sig; renderLibrary(); }
  }, 1500);
  window.__rbMobile = { setTab, state: M, renderLibrary, openTips };
})();
