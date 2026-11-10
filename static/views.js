/* research_brain — graph views: filter, saved views, neighbourhood focus.
 * - Filter (small "Filter" control at the top left of the graph): type, year range, tag, region.
 *   Non-matching nodes fade to the graph's dim alpha (via __rb.setNodeFilter -> targetAlpha).
 * - Saved views: name the current filter + search; kept in this browser (localStorage).
 * - Neighbourhood focus: double-click a node, or press F with a node selected, to show only its
 *   2-hop neighbourhood (others hidden via __rb.setHidden) and zoom to it. Esc or "Show all" restores.
 * Depends on window.__rb from app.js.
 */
(() => {
  'use strict';
  const rb = window.__rb;
  if (!rb || !rb.setNodeFilter) return;
  const { S, h } = rb;
  const $ = (sel, root = document) => root.querySelector(sel);
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('rb.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('rb.' + k, JSON.stringify(v)); } catch { /* private window etc. */ } },
  };
  const reduced = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const CSS = `
  .gv { position: absolute; left: 16px; top: 12px; z-index: 6; display: flex; align-items: flex-start; gap: 8px; }
  .gv-btn { background: color-mix(in srgb, var(--ground) 88%, transparent); }
  .gv-btn .gv-n { color: var(--ink-2); font-weight: 400; font-variant-numeric: tabular-nums; }
  .gv-btn.on { border-color: var(--ink-2); background: var(--sheet); }
  .gv-pop { top: calc(100% + 6px); left: 0; width: 300px; }
  .gv-row { display: flex; align-items: center; gap: 8px; padding: 0 8px 8px; }
  .gv-row > span.l { width: 52px; flex: none; font-size: 12px; color: var(--ink-2); }
  .gv-row input[type=number] { width: 72px; height: 28px; padding: 0 8px; font-variant-numeric: tabular-nums; }
  .gv-row .dash { color: var(--ink-2); }
  .gv-row select { flex: 1; min-width: 0; height: 28px; font: 13px var(--sans); color: var(--ink); background: var(--sheet);
    border: 1px solid var(--rule); border-radius: var(--r); padding: 0 6px; }
  .gv-row select:hover { border-color: color-mix(in srgb, var(--ink-2) 35%, var(--rule)); }
  .gv-row .seg { flex: 1; }
  .gv-row .seg button { flex: 1; justify-content: center; height: 26px; }
  .gv-foot { display: flex; align-items: center; gap: 8px; padding: 0 8px 2px; font-size: 12px; color: var(--ink-2); }
  .gv-foot .tnum { color: var(--ink); }
  .gv-views { display: flex; flex-direction: column; padding: 0 0 4px; }
  .gv-view { display: flex; align-items: center; border-radius: 4px; }
  .gv-view:hover { background: var(--ground); }
  .gv-view .v-name { all: unset; flex: 1; min-width: 0; cursor: pointer; padding: 6px 8px; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .gv-view .v-name:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); }
  .gv-view .v-sub { font-size: 12px; color: var(--ink-2); padding-right: 4px; white-space: nowrap; }
  .gv-view .v-del { all: unset; cursor: pointer; color: var(--ink-2); padding: 4px 8px; font-size: 13px; line-height: 1; opacity: 0; }
  .gv-view:hover .v-del, .gv-view .v-del:focus-visible { opacity: 1; }
  .gv-view .v-del:hover { color: var(--red); }
  .gv-view.cur .v-name { font-weight: 600; }
  .gv-empty { font-size: 12px; color: var(--ink-2); padding: 0 8px 8px; }
  .gv-save { display: flex; gap: 6px; padding: 0 8px 4px; }
  .gv-save input { flex: 1; min-width: 0; height: 28px; }
  .gv-pill { display: inline-flex; align-items: center; gap: 10px; height: 32px; padding: 0 4px 0 12px; font-size: 12.5px; color: var(--ink-2);
    background: var(--sheet); border: 1px solid var(--rule); border-radius: 16px; max-width: min(520px, 50vw); }
  .gv-pill .p-title { color: var(--ink); font-family: var(--serif); font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .gv-pill .btn { height: 24px; border-radius: 12px; }
  body.review-open .gv { display: none; }
  `;
  const style = document.createElement('style');
  style.textContent = CSS;
  document.head.appendChild(style);

  // ====================================================================== filter state
  const TYPES = [['paper', 'Papers'], ['note', 'Notes'], ['fragment', 'Fragments']];
  const EMPTY = () => ({ types: ['paper', 'note', 'fragment'], yearFrom: null, yearTo: null, tag: '', region: null, status: '', topic: null, topicName: '' });
  // reading status (static/topics.js). Never drawn as colour on the graph: a filter only fades the rest.
  const STATUS_OPTS = [['', 'Any status'], ['to-read', 'To read'], ['reading', 'Reading'], ['read', 'Read'], ['later', 'Later'], ['not-relevant', 'Not relevant'], ['none', 'No status']];
  let F = Object.assign(EMPTY(), LS.get('graphFilter', {}) || {});
  let views = LS.get('graphViews', []);
  if (!Array.isArray(views)) views = [];
  let curView = null;

  const isActive = (f = F) => f.types.length < 3 || f.yearFrom != null || f.yearTo != null || !!f.tag || f.region != null || !!f.status || !!f.topic;
  const parentOf = (n) => (n.type === 'fragment' && n.parent && S.nodes.get(n.parent)) || n;

  function matches(n, f = F) {
    if (!f.types.includes(n.type)) return false;
    const base = parentOf(n); // fragments are judged by their paper's year, tags and region
    if (f.yearFrom != null || f.yearTo != null) {
      const y = Number(base.year);
      if (!y) return false;
      if (f.yearFrom != null && y < f.yearFrom) return false;
      if (f.yearTo != null && y > f.yearTo) return false;
    }
    if (f.tag) {
      const tags = new Set([...(n.tags || []), ...(base.tags || [])].map((t) => String(t).toLowerCase()));
      if (!tags.has(f.tag.toLowerCase())) return false;
    }
    if (f.region != null && Number(base.cluster) !== Number(f.region)) return false;
    if (f.status && (f.status === 'none' ? !!base.status : (base.status || '') !== f.status)) return false;
    if (f.topic) { const set = rb.topicSets && rb.topicSets.get(f.topic); if (!set || !set.has(base.id)) return false; }
    return true;
  }

  function apply() {
    rb.setNodeFilter(isActive() ? (n) => matches(n) : null);
    LS.set('graphFilter', F);
    updateButton();
  }

  // ====================================================================== UI
  const stage = $('#stage');
  const wrap = h('div', { class: 'gv menu-wrap' });
  const btn = h('button', { class: 'btn btn-sm gv-btn', 'aria-haspopup': 'true', 'aria-expanded': 'false', title: 'Filter the graph and pick a saved view' });
  const pop = h('div', { class: 'menu gv-pop', hidden: true, role: 'dialog', 'aria-label': 'Filter the graph' });
  const pill = h('div', { class: 'gv-pill', hidden: true });
  wrap.append(h('div', { style: { position: 'relative' } }, btn, pop), pill);
  stage.appendChild(wrap);

  function counts() {
    let all = 0, hit = 0;
    for (const n of S.nodes.values()) { all++; if (matches(n)) hit++; }
    return { all, hit };
  }
  function updateButton() {
    const on = isActive();
    btn.classList.toggle('on', on);
    const name = curView && views.find((v) => v.name === curView) ? curView : null;
    const kids = [name || (F.topic && F.topicName ? truncateName(F.topicName) : 'Filter')];
    if (on) { const c = counts(); kids.push(h('span', { class: 'gv-n', text: `${c.hit} of ${c.all}` })); }
    btn.replaceChildren(...kids);
    if (!pop.hidden) renderFoot();
  }

  function truncateName(s) { return rb.truncate ? rb.truncate(s, 28) : s; }

  function openPop() {
    rb.closeMenus();
    render();
    pop.hidden = false; btn.setAttribute('aria-expanded', 'true');
    const f = pop.querySelector('button, input, select'); if (f) f.focus({ preventScroll: true });
  }
  function closePop() { if (pop.hidden) return false; pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); return true; }
  btn.addEventListener('click', (e) => { e.stopPropagation(); if (pop.hidden) openPop(); else closePop(); });
  document.addEventListener('pointerdown', (e) => { if (!e.target.closest('.gv')) closePop(); });
  // app.js closes its menus on outside clicks; opening an app menu should close ours too
  document.addEventListener('click', (e) => { if (e.target.closest('#topbar .menu-wrap > .btn')) closePop(); });

  function changed() { curView = null; apply(); }

  let footEl = null;
  function renderFoot() {
    if (!footEl) return;
    const c = counts();
    footEl.replaceChildren(isActive()
      ? h('span', null, h('span', { class: 'tnum', text: String(c.hit) }), ` of ${c.all} match`)
      : h('span', { text: 'Showing everything' }),
    h('div', { class: 'spacer' }),
    isActive() ? h('button', { class: 'btn btn-text btn-sm', text: 'Clear filter', onclick: () => { F = EMPTY(); changed(); render(); } }) : null);
  }

  function render() {
    // type
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Types' }, TYPES.map(([t, label]) => {
      const b = h('button', { type: 'button', class: F.types.includes(t) ? 'on' : '', 'aria-pressed': String(F.types.includes(t)), text: label });
      b.onclick = () => {
        const on = F.types.includes(t);
        if (on && F.types.length === 1) return; // keep at least one type
        F.types = on ? F.types.filter((x) => x !== t) : [...F.types, t];
        b.classList.toggle('on', !on); b.setAttribute('aria-pressed', String(!on));
        changed();
      };
      return b;
    }));
    // years
    const years = [...S.nodes.values()].map((n) => Number(n.year)).filter(Boolean);
    const ymin = years.length ? Math.min(...years) : '', ymax = years.length ? Math.max(...years) : '';
    const yIn = (key, ph) => {
      const i = h('input', { type: 'number', placeholder: String(ph), 'aria-label': key === 'yearFrom' ? 'From year' : 'To year', min: 1800, max: 2200 });
      i.value = F[key] ?? '';
      i.addEventListener('input', () => { const v = parseInt(i.value, 10); F[key] = Number.isFinite(v) ? v : null; changed(); });
      return i;
    };
    // tag
    const tagCount = new Map();
    for (const n of S.nodes.values()) for (const t of n.tags || []) { const k = String(t); tagCount.set(k, (tagCount.get(k) || 0) + 1); }
    const tags = [...tagCount.keys()].sort((a, b) => a.localeCompare(b));
    if (F.tag && !tags.some((t) => t.toLowerCase() === F.tag.toLowerCase())) tags.unshift(F.tag);
    const tagSel = h('select', { 'aria-label': 'Tag' }, h('option', { value: '', text: 'Any tag' }),
      tags.map((t) => h('option', { value: t, text: tagCount.has(t) ? `${t} (${tagCount.get(t)})` : t })));
    tagSel.value = tags.find((t) => t.toLowerCase() === (F.tag || '').toLowerCase()) || '';
    tagSel.addEventListener('change', () => { F.tag = tagSel.value; changed(); });
    // region
    const regions = (S.clusters || []).filter((c) => c && c.label);
    const regSel = h('select', { 'aria-label': 'Region' }, h('option', { value: '', text: 'Any region' }),
      regions.map((c) => h('option', { value: String(c.id), text: c.label.charAt(0).toUpperCase() + c.label.slice(1) })));
    regSel.value = F.region != null ? String(F.region) : '';
    regSel.addEventListener('change', () => { F.region = regSel.value === '' ? null : Number(regSel.value); changed(); });
    // reading status
    const stSel = h('select', { 'aria-label': 'Reading status' }, STATUS_OPTS.map(([v, l]) => h('option', { value: v, text: l })));
    stSel.value = F.status || '';
    stSel.addEventListener('change', () => { F.status = stSel.value; changed(); });
    const topicRow = F.topic ? h('div', { class: 'gv-row' }, h('span', { class: 'l', text: 'Topic' }),
      h('span', { style: { flex: '1', minWidth: '0', fontSize: '13px' }, text: F.topicName || F.topic }),
      h('button', { class: 'btn btn-text btn-sm', text: 'Clear', onclick: () => { F.topic = null; F.topicName = ''; changed(); render(); } })) : null;

    footEl = h('div', { class: 'gv-foot' });
    renderFoot();

    // saved views
    const list = views.length
      ? h('div', { class: 'gv-views' }, views.map((v) => h('div', { class: 'gv-view' + (v.name === curView ? ' cur' : '') },
          h('button', { class: 'v-name', title: describe(v), text: v.name, onclick: () => applyView(v) }),
          h('span', { class: 'v-sub', text: v.search ? `“${rb.truncate(v.search, 14)}”` : '' }),
          h('button', { class: 'v-del', title: `Remove “${v.name}”`, 'aria-label': `Remove ${v.name}`, text: '×',
            onclick: (e) => { e.stopPropagation(); views = views.filter((x) => x !== v); if (curView === v.name) curView = null; LS.set('graphViews', views); render(); updateButton(); } }))))
      : h('div', { class: 'gv-empty', text: 'Save the current filter and search to come back to it.' });
    const nameIn = h('input', { type: 'text', placeholder: 'Name this view', 'aria-label': 'View name', spellcheck: false });
    const save = () => {
      const name = nameIn.value.trim();
      if (!name) { nameIn.focus(); return; }
      const v = { name, filter: JSON.parse(JSON.stringify(F)), search: S.search || '' };
      views = [v, ...views.filter((x) => x.name.toLowerCase() !== name.toLowerCase())];
      LS.set('graphViews', views);
      curView = name;
      render(); updateButton();
    };
    nameIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
    const saveBtn = h('button', { class: 'btn btn-sm', type: 'button', text: 'Save', onclick: save });

    pop.replaceChildren(
      h('span', { class: 'menu-label', text: 'Show' }),
      h('div', { class: 'gv-row' }, seg),
      h('div', { class: 'gv-row' }, h('span', { class: 'l', text: 'Years' }), yIn('yearFrom', ymin), h('span', { class: 'dash', text: 'to' }), yIn('yearTo', ymax)),
      h('div', { class: 'gv-row' }, h('span', { class: 'l', text: 'Tag' }), tagSel),
      h('div', { class: 'gv-row' }, h('span', { class: 'l', text: 'Region' }), regSel),
      h('div', { class: 'gv-row' }, h('span', { class: 'l', text: 'Status' }), stSel),
      topicRow,
      footEl,
      h('div', { class: 'menu-sep' }),
      h('span', { class: 'menu-label', text: 'Saved views' }),
      list,
      h('div', { class: 'gv-save' }, nameIn, saveBtn));
  }

  function describe(v) {
    const f = Object.assign(EMPTY(), v.filter || {}), bits = [];
    if (f.types.length < 3) bits.push(TYPES.filter(([t]) => f.types.includes(t)).map(([, l]) => l.toLowerCase()).join(' and '));
    if (f.yearFrom != null || f.yearTo != null) bits.push(`${f.yearFrom ?? '…'} to ${f.yearTo ?? '…'}`);
    if (f.tag) bits.push(`tagged ${f.tag}`);
    if (f.region != null) { const c = (S.clusters || []).find((x) => Number(x.id) === Number(f.region)); bits.push(c ? `region ${c.label}` : 'a region'); }
    if (f.status) bits.push(f.status === 'none' ? 'no status' : (STATUS_OPTS.find((o) => o[0] === f.status) || [, f.status])[1].toLowerCase());
    if (f.topic) bits.push(`topic ${f.topicName || f.topic}`);
    if (v.search) bits.push(`search “${v.search}”`);
    return bits.length ? bits.join(', ') : 'Everything';
  }

  function setSearch(q) {
    const inp = $('#search-input');
    if (!inp) return;
    inp.value = q || '';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function applyView(v) {
    F = Object.assign(EMPTY(), JSON.parse(JSON.stringify(v.filter || {})));
    apply();
    setSearch(v.search || '');
    curView = v.name;
    updateButton();
    render();
  }

  // ====================================================================== neighbourhood focus
  let focus = null; // {id, links}
  const HOPS = 2;
  const visibleLink = (l) => l.kind !== 'similar' || (S.showSim && l.top && l.score >= S.threshold);
  const lid = (x) => (x && typeof x === 'object' ? x.id : x);

  function neighbourhood(id, hops = HOPS) {
    const adj = new Map();
    for (const l of S.links) {
      if (!visibleLink(l)) continue;
      const a = lid(l.source), b = lid(l.target);
      if (!adj.has(a)) adj.set(a, []); if (!adj.has(b)) adj.set(b, []);
      adj.get(a).push(b); adj.get(b).push(a);
    }
    const keep = new Set([id]);
    let frontier = [id];
    for (let d = 0; d < hops; d++) {
      const next = [];
      for (const x of frontier) for (const y of adj.get(x) || []) if (!keep.has(y)) { keep.add(y); next.push(y); }
      frontier = next;
    }
    // a kept paper's own fragments come along (they sit on it), so its marginalia stay visible
    for (const n of S.nodes.values()) if (n.type === 'fragment' && keep.has(n.parent)) keep.add(n.id);
    return keep;
  }

  function hiddenFor(id) {
    const keep = neighbourhood(id);
    const hide = new Set();
    for (const nid of S.nodes.keys()) if (!keep.has(nid)) hide.add(nid);
    return { keep, hide };
  }

  function focusOn(id, delay = 30) {
    if (!S.nodes.has(id)) return;
    const { keep, hide } = hiddenFor(id);
    focus = { id, links: S.links };
    rb.setHidden(hide);
    const n = S.nodes.get(id);
    const others = keep.size - 1;
    pill.replaceChildren(
      h('span', { text: 'Around' }),
      h('span', { class: 'p-title', title: n.title, text: rb.truncate(n.title || id, 48) }),
      h('span', { class: 'tnum', text: `${others} within 2 links` }),
      h('button', { class: 'btn btn-sm', title: 'Show the whole graph (Esc)', text: 'Show all', onclick: unfocus }));
    pill.hidden = false;
    setTimeout(() => fitTo(keep), delay);
  }
  /** zoom to a set of nodes inside the part of the graph not covered by the side panels */
  function fitTo(ids) {
    const fg = rb.fg, g = $('#graph');
    if (!fg || !g) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const id of ids) { const n = S.nodes.get(id); if (!n || n.x === undefined) continue; x0 = Math.min(x0, n.x); x1 = Math.max(x1, n.x); y0 = Math.min(y0, n.y); y1 = Math.max(y1, n.y); }
    if (!Number.isFinite(x0)) return;
    const cover = ['#panel', '#chat'].reduce((w, sel) => w + ($(sel) && !$(sel).hidden ? $(sel).offsetWidth : 0), 0);
    const W = Math.max(200, g.clientWidth - cover), H = g.clientHeight, pad = 90;
    const k = Math.max(0.3, Math.min(4, (W - 2 * pad) / Math.max(1, x1 - x0), (H - 2 * pad) / Math.max(1, y1 - y0)));
    const ms = reduced() ? 0 : 600;
    fg.zoom(k, ms);
    fg.centerAt((x0 + x1) / 2 + cover / 2 / k, (y0 + y1) / 2, ms);
  }
  function unfocus() {
    if (!focus) return false;
    focus = null;
    rb.setHidden(null);
    pill.hidden = true;
    const fg = rb.fg;
    if (fg) fg.zoomToFit(reduced() ? 0 : 600, 70);
    return true;
  }
  // graph refreshed (note added, link made): recompute the neighbourhood around the same node
  setInterval(() => {
    if (focus && focus.links !== S.links) {
      if (!S.nodes.has(focus.id)) { unfocus(); return; }
      focus.links = S.links;
      rb.setHidden(hiddenFor(focus.id).hide);
    }
  }, 800);

  // double-click a node: app.js opens it on the first click; the second, quick click focuses
  let last = null;
  S.nodeClickHook = (n) => {
    const now = performance.now();
    if (last && last.id === n.id && now - last.t < 420) { last = null; focusOn(n.id, 700); return true; } // after the first click's open-and-centre settles
    last = { id: n.id, t: now };
    return false;
  };

  const CMD = window.RB && window.RB.commands;
  if (CMD) { // Esc goes through the registry's layers; F is registered in static/shortcuts.js
    CMD.layer({ id: 'views.filter', order: 92, isOpen: () => !pop.hidden, close: closePop });
    CMD.layer({ id: 'views.focus', order: 80, isOpen: (c) => !!focus && !(c && c.typing), close: unfocus });
  }
  window.addEventListener('keydown', (e) => {
    if (CMD || document.body.classList.contains('review-open')) return;
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (e.key === 'Escape') {
      if (closePop()) { e.stopPropagation(); e.preventDefault(); return; }
      if (focus && !typing && !S.modalClose && $('#view-menu').hidden && $('#add-menu').hidden) { e.stopPropagation(); e.preventDefault(); unfocus(); }
      return;
    }
    if ((e.key === 'f' || e.key === 'F') && !typing && !e.metaKey && !e.ctrlKey && !e.altKey && S.selected) {
      e.preventDefault();
      if (focus && focus.id === S.selected) unfocus(); else focusOn(S.selected);
    }
  }, true);

  // keep the count in the button current as the graph changes
  let lastSize = -1;
  setInterval(() => { if (isActive() && S.nodes.size !== lastSize) { lastSize = S.nodes.size; updateButton(); } }, 1000);

  rb.views = { focusOn, unfocus, applyView, get filter() { return F; }, setFilter(f) { F = Object.assign(EMPTY(), f || {}); curView = null; apply(); } };
  apply();
})();
