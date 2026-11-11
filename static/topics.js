/* research_brain — reading status, "saved because", topics and the reading queue.
 * - Panel (papers and notes): a compact status control above the title, a one-line "Saved because"
 *   right under it, and a Topics chip row (type-ahead to add, x to remove).
 * - Park for later (L): status "later" and focus "Saved because".
 * - Topics drawer (T, or "Topics" in the top bar): home = "Next to read" across topics + the topic list
 *   + "Suggest topics from regions"; a topic = its queue (at most 5, "Show more" adds 5) and its papers
 *   grouped by status. "Show on graph" fades everything else via the views.js filter.
 * Server: /api/topics*, /api/queue, /api/notes/{id}/park (app/topics.py). Depends on window.__rb.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb || !rb.onPanelRender) return;
  const { S, h, api, toast, toastErr, truncate } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const enc = encodeURIComponent;

  const STATUSES = [['to-read', 'To read'], ['reading', 'Reading'], ['read', 'Read'], ['later', 'Later'], ['not-relevant', 'Not relevant']];
  const LABEL = Object.fromEntries(STATUSES);
  const GROUP_ORDER = ['to-read', 'reading', 'later', 'read', 'not-relevant', ''];
  const GROUP_LABEL = { ...LABEL, '': 'No status' };

  const CSS = `
  .st-row { display: flex; align-items: center; gap: 8px; margin: 0 0 14px; }
  .st-row .seg button { height: 24px; padding: 0 9px; }
  .st-row .seg button.on { color: var(--ink); background: var(--ground); font-weight: 500; }
  .st-row .seg button:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: -2px; }
  .because { display: flex; align-items: baseline; gap: 8px; margin: 10px 0 0; padding-left: 10px; border-left: 2px solid var(--red); }
  .because label { flex: none; font-size: 12.5px; color: var(--ink-2); }
  .because input { flex: 1; min-width: 0; height: 28px; font: italic 15px var(--serif); color: var(--ink); border-color: transparent; background: transparent; padding: 0 6px; margin-left: -6px; }
  .because input:hover { background: var(--ground); }
  .because input:focus { border-color: var(--rule); background: var(--sheet); }
  .because input::placeholder { color: var(--ink-2); opacity: .7; font-style: italic; }
  .because.empty { border-left-color: var(--rule); }
  .tp-row { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin: 10px 0 0; position: relative; }
  .tp-row > .tp-lbl { font-size: 12.5px; color: var(--ink-2); margin-right: 4px; }
  .tp-chip { display: inline-flex; align-items: center; font-size: 12.5px; color: var(--ink); border: 1px solid var(--rule); border-radius: var(--r); background: var(--sheet); padding: 1px 2px 1px 7px; }
  .tp-chip > .tp-go { all: unset; cursor: pointer; }
  .tp-chip > .tp-go:hover { text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--ink-2); }
  .tp-chip > .tp-x { all: unset; cursor: pointer; color: var(--ink-2); padding: 0 5px; font-size: 13px; line-height: 1; opacity: .7; }
  .tp-chip > .tp-x:hover { opacity: 1; color: var(--red); }
  .tp-add { height: 24px; width: 150px; font-size: 12.5px; padding: 0 6px; border-color: transparent; background: transparent; }
  .tp-add:hover { background: var(--ground); }
  .tp-add:focus { border-color: var(--rule); background: var(--sheet); }
  .tp-sugg { position: absolute; top: calc(100% + 4px); left: 0; z-index: 30; min-width: 240px; max-width: 100%; background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; padding: 4px; box-shadow: 0 12px 32px -12px color-mix(in srgb, var(--ink) 22%, transparent); }
  .tp-sugg button { all: unset; box-sizing: border-box; display: flex; justify-content: space-between; gap: 12px; width: 100%; cursor: pointer; padding: 6px 8px; border-radius: 4px; font-size: 13px; }
  .tp-sugg button .n { color: var(--ink-2); font-variant-numeric: tabular-nums; font-size: 12px; }
  .tp-sugg button.on, .tp-sugg button:hover { background: var(--ground); }

  #btn-topics.on { background: var(--ground); }
  .td { position: fixed; top: var(--topbar-h); left: 0; bottom: 0; width: 360px; max-width: 100vw; z-index: 22; background: var(--sheet); border-right: 1px solid var(--rule); display: flex; flex-direction: column; animation: tdIn .15s ease-out; }
  @keyframes tdIn { from { transform: translateX(-12px); opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .td { animation: none; } }
  body.topics-open .gv, body.topics-open .legend { left: 376px; }
  .td-head { display: flex; align-items: center; gap: 8px; padding: 12px 12px 0 24px; min-height: 52px; flex: none; }
  .td-head .td-crumb { all: unset; cursor: pointer; font-size: 12.5px; color: var(--ink-2); }
  .td-head .td-crumb:hover { color: var(--ink); }
  .td-head .td-here { font-size: 12.5px; color: var(--ink-2); }
  .td-body { flex: 1; overflow-y: auto; padding: 4px 24px 48px; }
  .td h2 { font: 600 22px/1.2 var(--serif); letter-spacing: -.01em; margin: 4px 0 4px; }
  .td h2 input[type=text] { all: unset; width: 100%; box-sizing: border-box; font: inherit; border-radius: var(--r); padding: 0 4px; margin-left: -4px; }
  .td h2 input:hover { background: var(--ground); }
  .td h2 input:focus { background: var(--sheet); box-shadow: inset 0 0 0 1px var(--rule); }
  .td input.td-desc { all: unset; display: block; width: 100%; box-sizing: border-box; font-size: 13px; color: var(--ink-2); padding: 2px 4px; margin-left: -4px; border-radius: var(--r); }
  .td input.td-desc:hover { background: var(--ground); }
  .td input.td-desc:focus { background: var(--sheet); box-shadow: inset 0 0 0 1px var(--rule); color: var(--ink); }
  .td-sec { margin-top: 28px; }
  .td-sec > .sec-head { margin-bottom: 4px; }
  .td-tools { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .td-list { display: flex; flex-direction: column; }
  .td-topic { all: unset; box-sizing: border-box; cursor: pointer; display: flex; align-items: baseline; gap: 10px; padding: 7px 8px; margin: 0 -8px; border-radius: 4px; }
  .td-topic:hover, .td-topic:focus-visible { background: var(--ground); }
  .td-topic .t-name { flex: 1; min-width: 0; font-size: 14px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .td-topic .t-sub { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .td-new { display: flex; gap: 6px; margin-top: 10px; }
  .td-new input { flex: 1; min-width: 0; height: 32px; }
  .td-empty { font-size: 13px; color: var(--ink-2); line-height: 1.55; padding: 4px 0; }
  .td-note { font-size: 12px; color: var(--ink-2); margin: 2px 0 6px; }
  .q-list { display: flex; flex-direction: column; gap: 2px; }
  .q-item { padding: 10px 0 10px; border-top: 1px solid var(--rule); }
  .q-item:first-child { border-top: none; }
  .q-title { all: unset; cursor: pointer; display: block; font: 15px/1.35 var(--serif); color: var(--ink); }
  .q-title:hover { text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--ink-2); }
  .q-item.rec .q-title { cursor: default; }
  .q-item.rec .q-title:hover { text-decoration: none; }
  .q-meta { font-size: 12px; color: var(--ink-2); margin-top: 3px; display: flex; flex-wrap: wrap; column-gap: 10px; }
  .q-reason { font-size: 12.5px; color: var(--machine); margin-top: 3px; }
  .q-acts { display: flex; gap: 4px; margin-top: 6px; margin-left: -8px; opacity: .55; transition: opacity .12s ease-out; }
  .q-item:hover .q-acts, .q-item:focus-within .q-acts { opacity: 1; }
  @media (hover: none) { .q-acts { opacity: 1; } }
  .q-acts .btn { height: 24px; font-size: 12px; }
  .q-more { margin-top: 6px; margin-left: -8px; }
  .g-sec { margin-top: 18px; }
  .g-head { display: flex; align-items: baseline; gap: 6px; font: 600 13px var(--sans); color: var(--ink); margin-bottom: 2px; }
  .g-head .n { font-weight: 400; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  details.g-sec > summary { list-style: none; cursor: pointer; }
  details.g-sec > summary::-webkit-details-marker { display: none; }
  details.g-sec > summary .g-head { color: var(--ink-2); font-weight: 500; }
  .g-row { display: flex; align-items: baseline; gap: 8px; padding: 5px 8px; margin: 0 -8px; border-radius: 4px; }
  .g-row:hover { background: var(--ground); }
  .g-row .g-open { all: unset; cursor: pointer; flex: 1; min-width: 0; font: 14px/1.35 var(--serif); color: var(--ink); }
  .g-row .g-why { display: block; font: italic 12.5px/1.4 var(--serif); color: var(--red); margin-top: 1px; }
  .g-row .g-year { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .g-row .g-x { all: unset; cursor: pointer; color: var(--ink-2); padding: 0 4px; opacity: 0; }
  .g-row:hover .g-x, .g-row .g-x:focus-visible { opacity: 1; }
  .g-row .g-x:hover { color: var(--red); }
  .sg-row { display: flex; gap: 6px; align-items: center; padding: 4px 0; }
  .sg-row input { flex: 1; min-width: 0; height: 30px; }
  .sg-row .n { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .td-danger { margin-top: 40px; }
  body.review-open .td { display: none; }
  @media (max-width: 760px) { .td { width: 100vw; } body.topics-open .gv, body.topics-open .legend { left: 16px; } }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  // ====================================================================== shared state
  const T = { list: null, sets: new Map(), view: null, open: false, regionDrafts: null };
  rb.topicSets = T.sets; // slug -> Set(note ids), read by views.js for "Show on graph"

  async function loadTopics() {
    try {
      T.list = await api('GET', '/api/topics');
    } catch (e) { T.list = T.list || []; }
    return T.list;
  }
  async function loadSet(slug) {
    const d = await api('GET', `/api/topics/${enc(slug)}`);
    T.sets.set(slug, new Set(d.members));
    return d;
  }

  // ====================================================================== panel: status, saved because, topics
  function statusRow(n) {
    const seg = h('div', { class: 'seg', role: 'radiogroup', 'aria-label': 'Reading status' });
    const draw = () => {
      seg.replaceChildren(...STATUSES.map(([v, label]) => {
        const on = (S.cur && S.cur.status) === v;
        return h('button', { type: 'button', role: 'radio', 'aria-checked': String(on), class: on ? 'on' : '', 'data-st': v, text: label,
          title: on ? 'Click again to clear' : `Mark as ${label.toLowerCase()}`,
          onclick: () => { setStatus(on ? '' : v); draw(); } });
      }));
    };
    draw();
    const row = h('div', { class: 'st-row', id: 'status-row' }, seg);
    row.__redraw = draw;
    return row;
  }

  function setStatus(v) {
    if (!S.cur) return;
    rb.queueSave({ status: v });
    const node = S.nodes.get(S.cur.id); if (node) node.status = v;
    const row = $('#status-row'); if (row && row.__redraw) row.__redraw();
    if (T.open) scheduleDrawerRefresh();
  }

  function becauseField(n) {
    const inp = h('input', { type: 'text', id: 'saved-because', maxlength: 300, spellcheck: true, placeholder: 'Why you kept this, in one line',
      'aria-label': 'Saved because' });
    inp.value = n.saved_because || '';
    const wrap = h('div', { class: 'because' + (inp.value ? '' : ' empty') }, h('label', { for: 'saved-because', text: 'Saved because' }), inp);
    inp.addEventListener('input', () => { wrap.classList.toggle('empty', !inp.value.trim()); rb.queueSave({ saved_because: inp.value }); });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } });
    return wrap;
  }

  function topicRow(n) {
    const row = h('div', { class: 'tp-row', id: 'topic-row' });
    let mine = (n.topics || []).slice();
    const input = h('input', { type: 'text', class: 'tp-add', placeholder: mine.length ? '+ Topic' : '+ Add to a topic', spellcheck: false,
      'aria-label': 'Add to a topic', 'aria-autocomplete': 'list', id: 'topic-add' });
    let sugg = null, hi = 0, opts = [];
    const close = () => { if (sugg) { sugg.remove(); sugg = null; } };
    const draw = () => {
      row.replaceChildren(h('span', { class: 'tp-lbl', text: 'Topics' }),
        ...mine.map((t) => h('span', { class: 'tp-chip', 'data-hue': String([...t.slug].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7) % 4) },
          h('button', { class: 'tp-go', title: 'Open this topic', text: t.name, onclick: () => openDrawer(t.slug) }),
          h('button', { class: 'tp-x', title: `Remove from ${t.name}`, 'aria-label': `Remove from ${t.name}`, text: '×', onclick: () => remove(t) }))),
        input);
      input.placeholder = mine.length ? '+ Topic' : '+ Add to a topic';
    };
    const suggest = async () => {
      if (!T.list) await loadTopics();
      const q = input.value.trim().toLowerCase();
      const have = new Set(mine.map((t) => t.slug));
      opts = (T.list || []).filter((t) => !have.has(t.slug) && (!q || t.name.toLowerCase().includes(q)))
        .sort((a, b) => (b.name.toLowerCase().startsWith(q) - a.name.toLowerCase().startsWith(q)) || a.name.localeCompare(b.name)).slice(0, 7)
        .map((t) => ({ t }));
      if (q && !(T.list || []).some((t) => t.name.toLowerCase() === q)) opts.push({ create: input.value.trim() });
      hi = Math.min(hi, Math.max(0, opts.length - 1));
      close();
      if (!opts.length || document.activeElement !== input) return;
      sugg = h('div', { class: 'tp-sugg', role: 'listbox' }, opts.map((o, i) => h('button', {
        type: 'button', role: 'option', class: i === hi ? 'on' : '',
        onmousedown: (e) => { e.preventDefault(); pick(o); } },
        o.t ? [h('span', { text: o.t.name }), h('span', { class: 'n', text: String(o.t.count) })] : [h('span', { text: `New topic “${o.create}”` })])));
      row.appendChild(sugg);
    };
    const pick = async (o) => {
      close();
      input.value = '';
      try {
        let slug = o.t && o.t.slug, name = o.t && o.t.name;
        if (o.create) { const t = await api('POST', '/api/topics', { name: o.create, members: [n.id] }); slug = t.slug; name = t.name; }
        else await api('POST', `/api/topics/${enc(slug)}/notes`, { ids: [n.id] });
        mine = [...mine, { slug, name }].sort((a, b) => a.name.localeCompare(b.name));
        if (S.cur && S.cur.id === n.id) S.cur.topics = mine;
        (T.sets.get(slug) || T.sets.set(slug, new Set()).get(slug)).add(n.id);
        await loadTopics();
        draw(); input.focus();
        if (T.open) scheduleDrawerRefresh();
      } catch (e) { toastErr(e, 'Could not add to the topic'); }
    };
    const remove = async (t) => {
      try {
        await api('DELETE', `/api/topics/${enc(t.slug)}/notes/${enc(n.id)}`);
        mine = mine.filter((x) => x.slug !== t.slug);
        if (S.cur && S.cur.id === n.id) S.cur.topics = mine;
        const s = T.sets.get(t.slug); if (s) s.delete(n.id);
        await loadTopics();
        draw();
        if (T.open) scheduleDrawerRefresh();
      } catch (e) { toastErr(e, 'Could not remove it from the topic'); }
    };
    input.addEventListener('focus', () => { hi = 0; suggest(); });
    input.addEventListener('input', () => { hi = 0; suggest(); });
    input.addEventListener('blur', () => setTimeout(close, 120));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (opts.length) { hi = (hi + (e.key === 'ArrowDown' ? 1 : opts.length - 1)) % opts.length; suggest(); } }
      else if (e.key === 'Enter') { e.preventDefault(); if (opts[hi]) pick(opts[hi]); }
      else if (e.key === 'Escape' && sugg) { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === 'Backspace' && !input.value && mine.length) { remove(mine[mine.length - 1]); }
    });
    draw();
    return row;
  }

  rb.onPanelRender((n, body) => {
    if (!n || n.type === 'fragment') return;
    const title = $('.title-input', body);
    if (!title) return;
    if (n.type === 'question') { title.after(topicRow(n)); return; } // a question has no reading status (static/questions.js)
    title.before(statusRow(n));
    title.after(becauseField(n), topicRow(n));
  });

  // ====================================================================== park for later
  function parkForLater() {
    if (!S.cur || S.cur.type === 'fragment' || $('#panel').hidden) { toast('Open a paper first, then park it for later.'); return; }
    setStatus('later');
    const inp = $('#saved-because');
    if (inp) { inp.focus(); inp.select(); }
    toast('Parked for later. Say why you kept it.');
  }

  // ====================================================================== drawer
  const btn = h('button', { id: 'btn-topics', class: 'btn btn-text', title: 'Topics and what to read next (T)', 'aria-expanded': 'false', text: 'Topics' });
  const anchor = $('#btn-review') || $('#btn-chat');
  if (anchor) anchor.before(btn);
  btn.addEventListener('click', () => (T.open ? closeDrawer() : openDrawer()));

  const drawer = h('aside', { class: 'td', hidden: true, 'aria-label': 'Topics' });
  const dHead = h('div', { class: 'td-head' });
  const dBody = h('div', { class: 'td-body' });
  drawer.append(dHead, dBody);
  document.body.appendChild(drawer);

  function openDrawer(slug) {
    T.open = true;
    drawer.hidden = false;
    document.body.classList.add('topics-open');
    btn.classList.add('on'); btn.setAttribute('aria-expanded', 'true');
    T.view = slug || null;
    render();
  }
  function closeDrawer() {
    if (!T.open) return false;
    T.open = false;
    drawer.hidden = true;
    document.body.classList.remove('topics-open');
    btn.classList.remove('on'); btn.setAttribute('aria-expanded', 'false');
    return true;
  }
  let refreshT = null;
  function scheduleDrawerRefresh() { clearTimeout(refreshT); refreshT = setTimeout(() => { if (T.open) render({ keepScroll: true }); }, 900); }

  const closeBtn = () => h('button', { class: 'btn btn-icon', title: 'Close (Esc)', 'aria-label': 'Close', onclick: closeDrawer,
    html: '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" stroke-width="1.3"/></svg>' });

  let renderSeq = 0;
  async function render({ keepScroll = false } = {}) {
    const seq = ++renderSeq;
    const top = dBody.scrollTop;
    try {
      const kids = T.view ? await renderTopic(T.view) : await renderHome();
      if (seq !== renderSeq) return;
      dBody.replaceChildren(...kids);
      dBody.scrollTop = keepScroll ? top : 0;
    } catch (e) {
      if (seq !== renderSeq) return;
      if (T.view && /No topic/.test(e.message)) { T.view = null; render(); return; }
      dBody.replaceChildren(h('div', { class: 'td-empty', text: e.message }));
    }
  }

  // ---------- queue (shared by home and topic) ----------
  function queueBlock(path, { topicSlug = null, title = 'Next to read' } = {}) {
    const sec = h('div', { class: 'td-sec q-sec' });
    const list = h('div', { class: 'q-list' });
    const note = h('div', { class: 'td-note' });
    const more = h('div', { class: 'q-more' });
    const headKids = [h('span', { class: 'sec-title', text: title })];
    sec.append(h('div', { class: 'sec-head' }, ...headKids), note, list, more);
    let offset = 0, polls = 0;
    const load = async (append = false) => {
      let q;
      try {
        q = await api('GET', `${path}${path.includes('?') ? '&' : '?'}offset=${offset}&limit=5`);
      } catch (e) {
        note.textContent = e.message; return;
      }
      if (!append) list.replaceChildren();
      note.textContent = q.note || '';
      note.hidden = !q.note;
      for (const it of q.items) list.appendChild(queueItem(it, topicSlug || (it.topic && it.topic.slug), () => { offset = 0; load(); }, !topicSlug));
      if (!list.children.length) {
        list.appendChild(h('div', { class: 'td-empty', text: topicSlug
          ? 'Nothing waiting. Add papers to this topic, or mark some as read so recommendations have something to go on.'
          : 'Nothing waiting. Papers you add start as to read, and topics bring recommendations.' }));
      }
      const shown = offset + q.items.length;
      more.replaceChildren(shown < q.total ? h('button', { class: 'btn btn-text btn-sm', text: 'Show more',
        title: `${q.total - shown} more`, onclick: () => { offset = shown; load(true); } }) : '');
      // recommendations being computed in the background: look again shortly (twice at most)
      if (q.refreshing && polls < 2 && !append) { polls++; setTimeout(() => { if (sec.isConnected) load(); }, 4000); }
    };
    sec.__reload = () => { offset = 0; load(); };
    load();
    return sec;
  }

  function queueItem(it, slug, reload, showTopic) {
    const isRec = it.kind === 'rec';
    const meta = [];
    if (it.year) meta.push(h('span', { class: 'tnum', text: String(it.year) }));
    if (it.authors && it.authors.length) meta.push(h('span', { text: it.authors.slice(0, 2).join(', ') + (it.authors.length > 2 ? ' et al.' : '') }));
    if (showTopic && it.topic) meta.push(h('span', { text: it.topic.name }));
    const el = h('div', { class: 'q-item' + (isRec ? ' rec' : ''), dataset: { key: it.key || it.id || it.s2_id } },
      isRec ? h('div', { class: 'q-title', text: it.title, title: it.abstract ? truncate(it.abstract, 400) : '' })
        : h('button', { class: 'q-title', text: it.title, onclick: () => rb.openNote(it.id, { center: true }) }),
      meta.length ? h('div', { class: 'q-meta' }, meta) : null,
      it.reason ? h('div', { class: 'q-reason', text: it.reason }) : null);
    const acts = h('div', { class: 'q-acts' });
    const busy = (b) => { for (const x of acts.querySelectorAll('button')) x.disabled = b; };
    const act = (label, title, fn, cls = 'btn btn-text') => h('button', { class: cls, text: label, title, onclick: async () => {
      busy(true);
      try { await fn(); } catch (e) { toastErr(e); busy(false); }
    } });
    if (isRec && slug) {
      acts.append(
        act('Accept', 'Add it to your library as to read, in this topic', async () => {
          const n = await api('POST', `/api/topics/${enc(slug)}/queue/accept`, { s2_id: it.s2_id });
          (T.sets.get(slug) || T.sets.set(slug, new Set()).get(slug)).add(n.id);
          toast(`Added “${truncate(n.title, 60)}” to read`);
          await rb.refreshGraph();
          reload();
        }, 'btn btn-sm'),
        act('Dismiss', 'Not useful. Never show it again for this topic', async () => {
          await api('POST', `/api/topics/${enc(slug)}/queue/dismiss`, { s2_id: it.s2_id, title: it.title });
          el.remove(); reload();
        }),
        act('Not now', 'Hide it for 14 days', async () => {
          await api('POST', `/api/topics/${enc(slug)}/queue/snooze`, { key: it.key || it.s2_id });
          el.remove(); reload();
        }));
    } else if (!isRec) {
      const setSt = async (v) => {
        await api('PUT', `/api/notes/${enc(it.id)}`, { status: v });
        const node = S.nodes.get(it.id); if (node) node.status = v;
        if (S.cur && S.cur.id === it.id) { S.cur.status = v; const r = $('#status-row'); if (r && r.__redraw) r.__redraw(); }
      };
      acts.append(
        act('Start reading', 'Mark as reading and open it', async () => { await setSt('reading'); rb.openNote(it.id, { center: true }); reload(); }, 'btn btn-sm'),
        act('Not relevant', 'Mark as not relevant', async () => { await setSt('not-relevant'); el.remove(); reload(); }),
        slug ? act('Not now', 'Hide it from the queue for 14 days', async () => {
          await api('POST', `/api/topics/${enc(slug)}/queue/snooze`, { key: `note:${it.id}` });
          el.remove(); reload();
        }) : null);
    }
    el.appendChild(acts);
    return el;
  }

  // ---------- home ----------
  async function renderHome() {
    dHead.replaceChildren(h('span', { class: 'td-here', text: 'Topics' }), h('div', { class: 'spacer' }), closeBtn());
    const list = await loadTopics();
    const out = [queueBlock('/api/queue')];
    const rows = list.map((t) => {
      const c = t.counts || {};
      const bits = [];
      if (c['to-read']) bits.push(`${c['to-read']} to read`);
      bits.push(`${t.count} paper${t.count === 1 ? '' : 's'}`);
      return h('button', { class: 'td-topic', onclick: () => { T.view = t.slug; render(); } },
        h('span', { class: 't-name', text: t.name, title: t.description || t.name }), h('span', { class: 't-sub', text: bits.join(', ') }));
    });
    const nameIn = h('input', { type: 'text', placeholder: 'New topic', 'aria-label': 'New topic name', id: 'new-topic-input', spellcheck: true });
    const create = async () => {
      const name = nameIn.value.trim();
      if (!name) { nameIn.focus(); return; }
      try {
        const t = await api('POST', '/api/topics', { name });
        T.sets.set(t.slug, new Set());
        T.view = t.slug; render();
      } catch (e) { toastErr(e, 'Could not create the topic'); }
    };
    nameIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); create(); } });
    out.push(h('div', { class: 'td-sec' },
      h('div', { class: 'sec-head' }, h('span', { class: 'sec-title' }, 'Your topics', list.length ? h('span', { class: 'n', text: String(list.length) }) : null)),
      list.length ? h('div', { class: 'td-list' }, rows)
        : h('div', { class: 'td-empty', text: 'A topic keeps every paper on one subject together, whatever region of the graph it sits in.' }),
      h('div', { class: 'td-new' }, nameIn, h('button', { class: 'btn', text: 'Create', onclick: create }))));
    out.push(suggestBlock());
    return out;
  }

  function suggestBlock() {
    const sec = h('div', { class: 'td-sec' });
    const body = h('div');
    const go = h('button', { class: 'btn btn-text btn-sm', text: 'Suggest topics from regions', title: 'Turn a region the graph found into a topic', onclick: async () => {
      go.disabled = true;
      try {
        const sug = await api('GET', '/api/topics/suggest');
        if (!sug.length) { body.replaceChildren(h('div', { class: 'td-empty', text: 'Every region is already a topic, or the library is too small to have regions yet.' })); return; }
        body.replaceChildren(...sug.slice(0, 8).map((s) => {
          const inp = h('input', { type: 'text', 'aria-label': 'Topic name', spellcheck: true });
          inp.value = s.name;
          const make = async () => {
            try {
              const t = await api('POST', '/api/topics', { name: inp.value.trim() || s.name, members: s.members });
              T.sets.set(t.slug, new Set(t.members));
              toast(`Made “${t.name}” from ${t.count} papers`);
              T.view = t.slug; render();
            } catch (e) { toastErr(e, 'Could not create the topic'); }
          };
          inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); make(); } });
          return h('div', { class: 'sg-row' }, inp, h('span', { class: 'n', text: `${s.size}` , title: `${s.size} papers and notes` }),
            h('button', { class: 'btn btn-sm', text: 'Make topic', onclick: make }));
        }));
      } catch (e) { toastErr(e); } finally { go.disabled = false; }
    } });
    sec.append(h('div', { class: 'td-tools' }, go), body);
    return sec;
  }

  // ---------- one topic ----------
  async function renderTopic(slug) {
    const d = await loadSet(slug);
    dHead.replaceChildren(h('button', { class: 'td-crumb', text: 'Topics', title: 'Back to all topics', onclick: () => { T.view = null; render(); } }),
      h('span', { class: 'td-here', text: '/' }), h('div', { class: 'spacer' }), closeBtn());
    const nameIn = h('input', { type: 'text', 'aria-label': 'Topic name', spellcheck: true });
    nameIn.value = d.name;
    const saveName = async () => {
      const v = nameIn.value.trim();
      if (!v || v === d.name) { nameIn.value = d.name; return; }
      try { const t = await api('PATCH', `/api/topics/${enc(slug)}`, { name: v }); d.name = t.name; loadTopics(); syncGraphLabel(); } catch (e) { nameIn.value = d.name; toastErr(e); }
    };
    nameIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); nameIn.blur(); } if (e.key === 'Escape') { nameIn.value = d.name; nameIn.blur(); } });
    nameIn.addEventListener('blur', saveName);
    const desc = h('input', { type: 'text', class: 'td-desc', placeholder: 'What this topic is for', 'aria-label': 'Topic description' });
    desc.value = d.description || '';
    desc.addEventListener('blur', async () => { if (desc.value.trim() !== (d.description || '')) { try { await api('PATCH', `/api/topics/${enc(slug)}`, { description: desc.value }); d.description = desc.value.trim(); } catch (e) { toastErr(e); } } });
    desc.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); desc.blur(); } });

    const onGraph = graphTopic() === slug;
    const showBtn = h('button', { class: 'btn btn-sm' + (onGraph ? ' on' : ''), text: onGraph ? 'Show everything' : 'Show on graph',
      title: onGraph ? 'Stop fading papers outside this topic' : 'Fade everything outside this topic on the graph',
      onclick: () => { showOnGraph(onGraph ? null : slug, d.name); render({ keepScroll: true }); } });
    const refreshBtn = h('button', { class: 'btn btn-text btn-sm', text: 'Refresh recommendations', title: 'Ask Semantic Scholar again now', onclick: async () => {
      refreshBtn.disabled = true;
      try { await api('POST', `/api/topics/${enc(slug)}/queue/refresh`); toast('Looking for recommendations in the background'); setTimeout(() => qb.__reload(), 2500); }
      catch (e) { toastErr(e); } finally { refreshBtn.disabled = false; }
    } });
    const qb = queueBlock(`/api/topics/${enc(slug)}/queue`, { topicSlug: slug, title: 'Next to read' });

    const groups = d.groups.filter((g) => g.count).sort((a, b) => GROUP_ORDER.indexOf(a.status) - GROUP_ORDER.indexOf(b.status)).map((g) => {
      const rows = g.items.map((it) => h('div', { class: 'g-row' },
        h('button', { class: 'g-open', onclick: () => rb.openNote(it.id, { center: true }) }, it.title,
          it.saved_because ? h('span', { class: 'g-why', text: it.saved_because }) : null),
        it.year ? h('span', { class: 'g-year', text: String(it.year) }) : null,
        h('button', { class: 'g-x', title: 'Remove from this topic', 'aria-label': `Remove ${it.title} from this topic`, text: '×', onclick: async () => {
          try { await api('DELETE', `/api/topics/${enc(slug)}/notes/${enc(it.id)}`); T.sets.get(slug) && T.sets.get(slug).delete(it.id); loadTopics(); render({ keepScroll: true }); if (S.cur && S.cur.id === it.id) rb.reloadCur(); }
          catch (e) { toastErr(e); }
        } })));
      const headEl = h('div', { class: 'g-head' }, GROUP_LABEL[g.status], h('span', { class: 'n', text: String(g.count) }));
      if (g.status === 'not-relevant' || g.status === '') {
        return h('details', { class: 'g-sec' }, h('summary', null, headEl), rows);
      }
      return h('div', { class: 'g-sec' }, headEl, rows);
    });
    const del = h('button', { class: 'btn btn-danger btn-sm', text: 'Delete topic', title: 'Remove the topic. Its papers stay in your library.', onclick: async () => {
      if (!window.confirm(`Delete the topic “${d.name}”? Its papers stay in your library.`)) return;
      try { await api('DELETE', `/api/topics/${enc(slug)}`); T.sets.delete(slug); if (graphTopic() === slug) showOnGraph(null); T.view = null; render(); if (S.cur) rb.reloadCur(); }
      catch (e) { toastErr(e); }
    } });
    return [
      h('h2', null, nameIn), desc,
      h('div', { class: 'td-tools' }, showBtn, refreshBtn, ...(window.__rbCite ? window.__rbCite.topicTools(slug) : [])),
      qb,
      h('div', { class: 'td-sec' },
        h('div', { class: 'sec-head' }, h('span', { class: 'sec-title' }, 'Papers', h('span', { class: 'n', text: String(d.count) }))),
        groups.length ? groups : h('div', { class: 'td-empty', text: 'No papers yet. Open a paper and add it under Topics, just below its title.' })),
      h('div', { class: 'td-danger' }, del),
    ];
  }

  // ====================================================================== graph
  const graphTopic = () => (rb.views && rb.views.filter && rb.views.filter.topic) || null;
  function showOnGraph(slug, name) {
    if (!rb.views || !rb.views.setFilter) { toast('The graph filter is not available'); return; }
    const f = JSON.parse(JSON.stringify(rb.views.filter || {}));
    f.topic = slug || null;
    f.topicName = slug ? name || '' : '';
    rb.views.setFilter(f);
  }
  function syncGraphLabel() {
    const slug = graphTopic();
    if (!slug) return;
    const t = (T.list || []).find((x) => x.slug === slug);
    if (t) showOnGraph(slug, t.name);
  }

  // ====================================================================== keyboard (via the command registry when present)
  const COMMANDS = [
    { id: 'topics.toggle', title: 'Topics and next to read', keys: 't', group: 'Topics', run: () => (T.open ? closeDrawer() : openDrawer()) },
    { id: 'note.park', title: 'Park for later (and say why)', keys: 'l', group: 'Reading', when: () => !!(S.cur && S.cur.type !== 'fragment' && !$('#panel').hidden), run: parkForLater },
    { id: 'note.addTopic', title: 'Add the open paper to a topic', keys: 'shift+t', group: 'Topics', when: () => !!(S.cur && S.cur.type !== 'fragment' && !$('#panel').hidden),
      run: () => { const i = $('#topic-add'); if (i) { i.focus(); } } },
    { id: 'topics.next', title: 'Next to read', keys: 'g then q', group: 'Reading', run: () => { openDrawer(); } },
    ...STATUSES.map(([v, label], i) => ({ id: `note.status.${v}`, title: `Mark as ${label.toLowerCase()}`, keys: `alt+${i + 1}`, group: 'Reading',
      when: () => !!(S.cur && S.cur.type !== 'fragment' && !$('#panel').hidden), run: () => setStatus(v) })),
  ];
  let registered = false;
  function tryRegister() {
    const reg = window.RB && window.RB.commands && window.RB.commands.register;
    if (!reg || registered) return registered;
    for (const c of COMMANDS) { try { window.RB.commands.register(c); } catch (e) { console.error(e); } }
    registered = true;
    // Esc closes the drawer through the registry's layers: before the panel when focus is in the drawer
    const inDrawer = (c) => !!(c && c.target && c.target.closest && c.target.closest('.td'));
    window.RB.commands.layer({ id: 'topics.drawer-focused', order: 45, isOpen: (c) => T.open && inDrawer(c), close: closeDrawer });
    window.RB.commands.layer({ id: 'topics.drawer', order: 35, isOpen: (c) => T.open && !(c && c.typing), close: closeDrawer });
    return true;
  }
  if (!tryRegister()) { window.addEventListener('load', tryRegister); setTimeout(tryRegister, 1500); }

  // fallback bindings until (unless) the registry exists
  const KEYMAP = { t: 'topics.toggle', l: 'note.park', T: 'note.addTopic' };
  document.addEventListener('keydown', (e) => {
    if (registered || document.body.classList.contains('review-open')) return; // the registry dispatches
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (e.key === 'Escape' && T.open && !typing && !S.modalClose && (!t.closest || !t.closest('#panel'))) {
      if ($('#panel').hidden || (t.closest && t.closest('.td'))) { e.preventDefault(); closeDrawer(); }
      return;
    }
    if (typing || e.metaKey || e.ctrlKey) return;
    let id = !e.altKey ? KEYMAP[e.key] : null;
    if (e.altKey && /^Digit[1-5]$/.test(e.code)) id = `note.status.${STATUSES[Number(e.code.slice(5)) - 1][0]}`;
    if (!id) return;
    const c = COMMANDS.find((x) => x.id === id);
    if (c && (!c.when || c.when())) { e.preventDefault(); c.run(); }
  });

  // keep topic membership sets fresh for the graph filter
  (async () => {
    const list = await loadTopics();
    await Promise.all(list.map((t) => loadSet(t.slug).catch(() => {})));
    if (graphTopic()) rb.views.setFilter(rb.views.filter);
  })();

  rb.topics = { open: openDrawer, close: closeDrawer, park: parkForLater, setStatus, showOnGraph, reload: () => (T.open ? render({ keepScroll: true }) : null), commands: COMMANDS };
})();
