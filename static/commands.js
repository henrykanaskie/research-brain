/* research_brain — command registry, keyboard dispatcher, command palette and shortcuts sheet.
 *
 * Loaded before app.js (and by viewer.html), so other scripts can register at any time.
 *
 *   RB.commands.register({
 *     id: 'note.critique',              // unique; registering the same id again updates it
 *     title: 'Critique my summary',     // sentence case, names the action
 *     group: 'Note',                    // General, Navigate, Library, Note, Graph, Review,
 *                                       // Research, Viewer, Extension
 *     keys: 'Mod+Shift+Enter',          // or an array: ['N', 'G then N']; omit for palette-only
 *     description: 'Claude compares your summary with the paper.',
 *     aliases: ['feedback', 'check'],   // extra search words for the sheet and the palette
 *     when: () => !!RB.app.S.cur,       // available right now? (default: always)
 *     run: (event) => { ... },          // return false to let the browser handle the key
 *     allowInInput: true,               // may fire while typing (modifier, arrow, Esc and Enter
 *                                       // keys only; plain letters never fire in a text field)
 *     scope: 'app',                     // app | viewer | review | extension | any (default from
 *                                       // group); commands only fire in their scope
 *     passive: true,                    // listed only; the key is handled elsewhere (review.js)
 *   })  -> returns an unregister function
 *
 *   Key notation (Mac first): "Mod+K" (⌘ on Mac, Ctrl elsewhere), "Shift+Mod+P", "Alt+1", "?",
 *   "Esc", "Up", "Mod+[", "G then T" (a chord: G, then T within 800 ms). Case-insensitive.
 *
 *   RB.commands.layer({id, order, isOpen(ctx), close()})  Esc closes the open layer with the
 *       highest order (ctx = {typing, target}). Use it instead of a keydown listener for Esc.
 *   RB.commands.annotate(id, {title, description, aliases, group})  extra copy for a command
 *       registered elsewhere (applied whenever it registers).
 *   RB.commands.run(id) / get(id) / list() / conflicts() / keysText(id) / tooltip(el, id, base)
 *   RB.commands.openSheet(query) / openPalette(query) / pick({...}) / panel({...}) / close()
 *
 * One dispatcher (document, bubbling phase) handles every registered key, so element-level
 * handlers run first: the LaTeX and [[link]] autocompletes keep Tab, Enter, Arrows and Esc, and
 * any handler that calls preventDefault wins.
 */
(() => {
  'use strict';
  const RB = (window.RB = window.RB || {});
  if (RB.commands) return;

  const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || '');
  const CHORD_MS = 800;
  const GROUPS = ['General', 'Navigate', 'Library', 'Note', 'Graph', 'Review', 'Research', 'Viewer', 'Extension'];
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('rb.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('rb.' + k, JSON.stringify(v)); } catch { /* private window */ } },
  };

  // ====================================================================== key notation
  const NAMES = {
    esc: 'escape', escape: 'escape', return: 'enter', enter: 'enter', del: 'delete', delete: 'delete',
    backspace: 'backspace', tab: 'tab', space: ' ', spacebar: ' ', plus: '+', minus: '-',
    up: 'arrowup', down: 'arrowdown', left: 'arrowleft', right: 'arrowright',
    arrowup: 'arrowup', arrowdown: 'arrowdown', arrowleft: 'arrowleft', arrowright: 'arrowright',
    '↑': 'arrowup', '↓': 'arrowdown', '←': 'arrowleft', '→': 'arrowright', home: 'home', end: 'end',
    pageup: 'pageup', pagedown: 'pagedown',
  };
  const NAMED = new Set(Object.values(NAMES).filter((k) => k.length > 1));
  function parseStep(s) {
    s = String(s).trim();
    let mods, key;
    if (s === '+') { mods = []; key = '+'; }
    else if (s.endsWith('++')) { mods = s.slice(0, -2).split('+'); key = '+'; }
    else { const p = s.split('+'); key = p.pop(); mods = p; }
    const st = { mod: false, alt: false, shift: false, key: '' };
    for (const m of mods.map((x) => x.trim().toLowerCase())) {
      if (m === 'mod' || m === 'cmd' || m === 'meta' || m === 'ctrl' || m === 'control' || m === '⌘') st.mod = true;
      else if (m === 'alt' || m === 'option' || m === 'opt' || m === '⌥') st.alt = true;
      else if (m === 'shift' || m === '⇧') st.shift = true;
    }
    const k = key.trim() === '' && key.length ? ' ' : key.trim();
    st.key = NAMES[k.toLowerCase()] || (k.length === 1 ? k.toLowerCase() : k.toLowerCase());
    return st;
  }
  const parseSeq = (s) => String(s).split(/\s+then\s+/i).map(parseStep);
  const stepId = (st) => `${st.mod ? 'M' : ''}${st.alt ? 'A' : ''}${st.shift ? 'S' : ''}:${st.key}`;
  const seqId = (seq) => seq.map(stepId).join(' ');

  function stepMatches(e, st) {
    const mod = e.metaKey || e.ctrlKey;
    if (mod !== st.mod || e.altKey !== st.alt) return false;
    const code = e.code || '';
    if (/^[a-z]$/.test(st.key)) {
      const k = (e.altKey || mod) && code.startsWith('Key') ? code.slice(3).toLowerCase() : String(e.key || '').toLowerCase();
      return k === st.key && e.shiftKey === st.shift;
    }
    if (/^[0-9]$/.test(st.key)) {
      return (code === 'Digit' + st.key || code === 'Numpad' + st.key || (!e.shiftKey && e.key === st.key)) && e.shiftKey === st.shift;
    }
    if (NAMED.has(st.key) || st.key === ' ') return String(e.key || '').toLowerCase() === st.key && e.shiftKey === st.shift;
    // symbols: the character already says whether Shift was down ("?" is Shift+/)
    return e.key === st.key && (!st.shift || e.shiftKey);
  }
  const plainStep = (st) => !st.mod && !st.alt && !NAMED.has(st.key); // a printable character

  const CAP_MAC = { escape: 'Esc', enter: '↵', backspace: '⌫', delete: '⌦', tab: '⇥', ' ': 'Space', arrowup: '↑', arrowdown: '↓', arrowleft: '←', arrowright: '→', home: 'Home', end: 'End', pageup: 'Page up', pagedown: 'Page down' };
  const CAP_PC = { escape: 'Esc', enter: 'Enter', backspace: 'Backspace', delete: 'Delete', tab: 'Tab', ' ': 'Space', arrowup: '↑', arrowdown: '↓', arrowleft: '←', arrowright: '→', home: 'Home', end: 'End', pageup: 'Page up', pagedown: 'Page down' };
  function stepCaps(st) {
    const caps = [];
    if (isMac) { if (st.alt) caps.push('⌥'); if (st.shift) caps.push('⇧'); if (st.mod) caps.push('⌘'); }
    else { if (st.mod) caps.push('Ctrl'); if (st.alt) caps.push('Alt'); if (st.shift) caps.push('Shift'); }
    caps.push((isMac ? CAP_MAC : CAP_PC)[st.key] || (st.key.length === 1 ? st.key.toUpperCase() : st.key));
    return caps;
  }
  const seqText = (seq) => seq.map((st) => stepCaps(st).join(isMac ? '' : '+')).join(' then ');
  // words the search understands: "g then t", "cmd k", "ctrl k", "shift"
  const seqWords = (seq) => seq.map((st) => [st.mod ? 'mod cmd ctrl ⌘' : '', st.alt ? 'alt option ⌥' : '', st.shift ? 'shift ⇧' : '',
    ({ ' ': 'space', escape: 'esc escape', enter: 'enter return', arrowup: 'up arrow', arrowdown: 'down arrow', arrowleft: 'left arrow', arrowright: 'right arrow' })[st.key] || st.key].join(' ')).join(' then ');

  // ====================================================================== registry
  const cmds = new Map();
  const notes = new Map(); // annotations
  const order = [];
  const scopeOfGroup = (g) => ({ Viewer: 'viewer', Extension: 'extension', Review: 'review' })[g] || 'app';

  function normalize(c) {
    const keys = c.keys == null ? [] : Array.isArray(c.keys) ? c.keys : [c.keys];
    c.keys = keys.filter(Boolean).map(String);
    c.seqs = c.keys.map(parseSeq);
    c.aliases = Array.isArray(c.aliases) ? c.aliases : c.aliases ? [String(c.aliases)] : [];
    c.group = GROUPS.includes(c.group) ? c.group : (c.group ? c.group : 'General');
    c.scope = c.scope || scopeOfGroup(c.group);
    c.description = c.description || '';
    return c;
  }
  function register(def) {
    if (!def || !def.id) throw new Error('RB.commands.register: a command needs an id');
    const prev = cmds.get(def.id);
    const c = Object.assign({}, prev || {});
    for (const [k, v] of Object.entries(def)) if (v !== undefined) c[k] = v;
    if (!('passive' in def)) c.passive = def.run ? false : (prev ? !!prev.passive : true);
    const ann = notes.get(def.id);
    if (ann) Object.assign(c, ann);
    if (!prev) order.push(def.id);
    cmds.set(def.id, normalize(c));
    resolveFallbacks();
    changed();
    return () => unregister(def.id);
  }
  /** a command registered with fallback: true gives up any key another command claims in its scope */
  function resolveFallbacks() {
    const taken = new Set();
    for (const c of cmds.values()) if (!c.fallback) for (const s of c.seqs) taken.add(c.scope + '|' + seqId(s));
    for (const c of cmds.values()) if (c.fallback) {
      c.seqs = c.keys.map(parseSeq).filter((s) => !taken.has(c.scope + '|' + seqId(s)));
      c.yielded = c.seqs.length < c.keys.length;
    }
  }
  function unregister(id) { if (cmds.delete(id)) { order.splice(order.indexOf(id), 1); resolveFallbacks(); changed(); } }
  function annotate(id, meta) {
    notes.set(id, Object.assign({}, notes.get(id) || {}, meta));
    const c = cmds.get(id);
    if (c) { Object.assign(c, meta); normalize(c); resolveFallbacks(); changed(); }
  }
  const all = () => order.map((id) => cmds.get(id)).filter(Boolean);
  function available(c) {
    if (c.passive || typeof c.run !== 'function') return false;
    if (c.scope !== 'any' && c.scope !== scope()) return false;
    try { return !c.when || !!c.when(); } catch (e) { console.warn('command when() failed:', c.id, e); return false; }
  }
  function scope() {
    if (document.body && document.body.classList.contains('review-open')) return 'review';
    return /^\/viewer\//.test(location.pathname) ? 'viewer' : 'app';
  }
  function runCmd(c, e) {
    if (!c) return false;
    remember(c);
    try { return c.run(e); } catch (err) { console.error('command failed:', c.id, err); }
    return undefined;
  }
  function remember(c) {
    if (c.id === 'general.close' || c.noRecent) return;
    const r = LS.get('cmd.recent', []).filter((x) => x !== c.id);
    r.unshift(c.id);
    LS.set('cmd.recent', r.slice(0, 6));
  }
  let changeT = null;
  function changed() { clearTimeout(changeT); changeT = setTimeout(() => { if (ov.mode === 'sheet') drawSheet(); for (const f of tipQueue) f(); }, 0); }

  /** pairs of commands whose keys collide in an overlapping scope (equal, or one a prefix of the other) */
  function conflicts() {
    const out = [];
    const list = all().filter((c) => c.seqs.length);
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const a = list[i], b = list[j];
      const anyOverlap = (x, y) => x.scope === 'any' && y.scope !== 'extension'; // the extension runs in its own window
      if (!(a.scope === b.scope || anyOverlap(a, b) || anyOverlap(b, a))) continue;
      for (const sa of a.seqs) for (const sb of b.seqs) {
        const n = Math.min(sa.length, sb.length);
        if (seqId(sa.slice(0, n)) === seqId(sb.slice(0, n))) out.push({ a: a.id, b: b.id, keys: [seqText(sa), seqText(sb)], scope: a.scope === 'any' ? b.scope : a.scope });
      }
    }
    return out;
  }

  // ====================================================================== Esc layers
  const layers = new Map();
  function layer(l) { layers.set(l.id, l); return () => layers.delete(l.id); }
  function topLayer(ctx) {
    let best = null;
    for (const l of layers.values()) {
      let open = false;
      try { open = !!l.isOpen(ctx); } catch { open = false; }
      if (open && (!best || (l.order || 0) > (best.order || 0))) best = l;
    }
    return best;
  }

  // ====================================================================== dispatcher
  const NON_TEXT = new Set(['checkbox', 'radio', 'range', 'button', 'submit', 'reset', 'color', 'file', 'image']);
  const isTyping = (t) => !!t && ((t.tagName === 'INPUT' && !NON_TEXT.has((t.type || '').toLowerCase())) || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  const visible = (sel) => Array.from(document.querySelectorAll(sel)).some((el) => !el.hidden && el.offsetParent !== null);
  let pending = null; // {steps, t}

  document.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.isComposing) return;
    if (['Shift', 'Meta', 'Control', 'Alt', 'CapsLock', 'Dead'].includes(e.key)) return;
    const t = e.target;
    const typing = isTyping(t);
    // the editor popups own these keys (their handlers run first; this is a second guard)
    if (['Tab', 'Enter', 'ArrowUp', 'ArrowDown', 'Escape'].includes(e.key) && visible('.le-pop, .wl-pop')) return;
    const ctx = { typing, target: t, event: e };
    const live = all().filter(available);
    const okInInput = (c, st) => !typing || (c.allowInInput && !plainStep(st));

    if (pending) {
      const p = pending; pending = null; hideChordHint();
      if (Date.now() - p.t <= CHORD_MS) {
        const n = p.steps.length;
        for (const c of live) for (const seq of c.seqs) {
          if (seq.length === n + 1 && seqId(seq.slice(0, n)) === seqId(p.steps) && stepMatches(e, seq[n])) {
            e.preventDefault();
            runCmd(c, e);
            return;
          }
        }
        if (e.key === 'Escape') { e.preventDefault(); return; } // Esc cancels a started chord
      }
    }
    for (const c of live) for (const seq of c.seqs) {
      if (seq.length !== 1 || !stepMatches(e, seq[0]) || !okInInput(c, seq[0])) continue;
      if (c.id === 'general.close' && !topLayer(ctx)) continue;
      const r = runCmd(c, e);
      if (r !== false) { e.preventDefault(); }
      return;
    }
    if (typing) return;
    const starts = [];
    for (const c of live) for (const seq of c.seqs) if (seq.length > 1 && stepMatches(e, seq[0])) starts.push({ c, seq });
    if (starts.length) {
      e.preventDefault();
      pending = { steps: [starts[0].seq[0]], t: Date.now() };
      showChordHint(starts);
      setTimeout(() => { if (pending && Date.now() - pending.t >= CHORD_MS) { pending = null; hideChordHint(); } }, CHORD_MS + 20);
    }
  });

  // the one Esc command: closes the innermost open layer
  register({
    id: 'general.close', title: 'Close', group: 'Navigate', keys: 'Esc', scope: 'any', allowInInput: true, noRecent: true,
    description: 'Closes the innermost open thing first: a dialog, a menu, the explanation, the chat, the side panel.',
    aliases: ['escape', 'dismiss', 'hide', 'cancel', 'close panel'],
    when: () => true,
    run: (e) => {
      const l = topLayer({ typing: isTyping(e && e.target), target: e && e.target, event: e });
      if (!l) return false;
      l.close({ typing: isTyping(e && e.target), target: e && e.target });
      return undefined;
    },
  });

  // ====================================================================== styles
  const CSS = `
  .rbk-ov { position: fixed; inset: 0; z-index: 68; display: flex; justify-content: center; align-items: flex-start;
    padding: 10vh 16px 16px; background: color-mix(in srgb, var(--ground) 62%, transparent); font: 13px/1.45 var(--sans); }
  .rbk-ov[hidden] { display: none !important; }
  .rbk-sheet { width: min(640px, 100%); max-height: 78vh; display: flex; flex-direction: column; background: var(--sheet); color: var(--ink);
    border: 1px solid var(--rule); border-radius: 6px; box-shadow: 0 12px 32px -12px color-mix(in srgb, var(--ink) 22%, transparent); overflow: hidden; }
  .rbk-sheet.wide { width: min(720px, 100%); }
  .rbk-head { display: flex; align-items: center; gap: 12px; padding: 16px 20px 12px; }
  .rbk-title { font: 600 15px/1.2 var(--sans); letter-spacing: -.005em; }
  .rbk-head .rbk-sp { flex: 1; }
  .rbk-x { all: unset; cursor: pointer; width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; border-radius: 3px; color: var(--ink-2); }
  .rbk-x:hover { background: var(--ground); color: var(--ink); }
  .rbk-x:focus-visible { outline: 2px solid color-mix(in srgb, var(--ink) 35%, transparent); }
  .rbk-search { margin: 0 20px 4px; display: flex; align-items: center; gap: 8px; height: 36px; padding: 0 10px; border: 1px solid var(--rule); border-radius: 3px; background: var(--sheet); }
  .rbk-search:focus-within { border-color: var(--ink-2); }
  .rbk-search svg { color: var(--ink-2); flex: none; }
  .rbk-search input { all: unset; flex: 1; min-width: 0; font: 14px var(--sans); color: var(--ink); }
  .rbk-search input::placeholder { color: var(--ink-2); }
  .rbk-palette .rbk-search { margin: 0; border: 0; border-bottom: 1px solid var(--rule); border-radius: 0; height: 48px; padding: 0 16px; }
  .rbk-palette .rbk-search input { font-size: 15px; }
  .rbk-body { overflow-y: auto; padding: 8px 20px 20px; }
  .rbk-palette .rbk-body { padding: 6px 6px 8px; }
  .rbk-group { margin-top: 16px; }
  .rbk-group:first-child { margin-top: 8px; }
  .rbk-glabel { font-size: 12px; color: var(--ink-2); padding: 0 0 6px; border-bottom: 1px solid var(--rule); }
  .rbk-row { display: flex; align-items: center; gap: 16px; padding: 8px 0; border-bottom: 1px solid color-mix(in srgb, var(--rule) 60%, transparent); cursor: default; }
  .rbk-row:last-child { border-bottom: 0; }
  .rbk-row.can { cursor: pointer; }
  .rbk-row.can:hover .rbk-rt { text-decoration: underline; text-decoration-color: var(--rule); text-underline-offset: 3px; }
  .rbk-txt { flex: 1; min-width: 0; }
  .rbk-rt { font: 500 13px/1.35 var(--sans); color: var(--ink); }
  .rbk-rd { font-size: 12px; line-height: 1.45; color: var(--ink-2); margin-top: 1px; }
  .rbk-keys { flex: none; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; justify-content: flex-end; max-width: 50%; }
  .rbk-keys .w { font-size: 11.5px; color: var(--ink-2); margin: 0 2px; }
  .rbk-cap { display: inline-flex; align-items: center; justify-content: center; min-width: 20px; height: 20px; padding: 0 5px; box-sizing: border-box;
    font: 500 11.5px/1 var(--sans); color: var(--ink); background: var(--sheet); border: 1px solid var(--rule); border-bottom-width: 1.5px; border-radius: 4px; font-variant-numeric: tabular-nums; }
  .rbk-empty { color: var(--ink-2); font-size: 13px; padding: 24px 0 8px; }
  .rbk-foot { display: flex; align-items: center; gap: 6px; padding: 10px 20px; border-top: 1px solid var(--rule); color: var(--ink-2); font-size: 12px; }
  .rbk-foot .rbk-sp { flex: 1; }
  .rbk-item { display: flex; align-items: center; gap: 12px; padding: 8px 10px; border-radius: 4px; cursor: pointer; min-width: 0; }
  .rbk-item.on { background: var(--ground); }
  .rbk-item .rbk-txt { display: flex; flex-direction: column; }
  .rbk-item .rbk-it { font: 500 13.5px/1.3 var(--sans); color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rbk-item .rbk-it.serif { font: 14.5px/1.3 var(--serif); }
  .rbk-item .rbk-is { font-size: 12px; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rbk-item .rbk-ig { flex: none; font-size: 12px; color: var(--ink-2); }
  .rbk-sec { font-size: 12px; color: var(--ink-2); padding: 10px 10px 4px; }
  .rbk-chord { position: fixed; left: 50%; bottom: 20px; transform: translateX(-50%); z-index: 66; display: flex; align-items: center; gap: 12px;
    padding: 8px 12px; background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; font: 12px var(--sans); color: var(--ink-2);
    box-shadow: 0 12px 32px -12px color-mix(in srgb, var(--ink) 22%, transparent); max-width: calc(100vw - 32px); flex-wrap: wrap; }
  .rbk-chord .opt { display: inline-flex; align-items: center; gap: 6px; color: var(--ink); }
  .rbk-guide h3 { font: 600 13px var(--sans); margin: 20px 0 6px; color: var(--ink); }
  .rbk-guide h3:first-child { margin-top: 8px; }
  .rbk-guide p { margin: 0 0 6px; color: var(--ink); font: 14.5px/1.55 var(--serif); max-width: 64ch; }
  .rbk-guide .rbk-row { align-items: flex-start; }
  .rbk-guide .rbk-row .rbk-rd { font: 14px/1.5 var(--serif); color: var(--ink); }
  @media (max-width: 560px) { .rbk-keys { max-width: 45%; } .rbk-ov { padding-top: 24px; } }
  `;
  function injectCSS() {
    if (document.getElementById('rbk-style')) return;
    const st = document.createElement('style');
    st.id = 'rbk-style';
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }
  injectCSS();

  // ====================================================================== DOM helpers
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'html') n.innerHTML = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return n;
  }
  function capsEl(seqs) {
    const box = el('span', { class: 'rbk-keys' });
    seqs.forEach((seq, i) => {
      if (i) box.append(el('span', { class: 'w', text: 'or' }));
      seq.forEach((st, j) => {
        if (j) box.append(el('span', { class: 'w', text: 'then' }));
        for (const cap of stepCaps(st)) box.append(el('kbd', { class: 'rbk-cap', text: cap }));
      });
    });
    return box;
  }
  const X_SVG = '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" stroke-width="1.3"/></svg>';
  const SEARCH_SVG = '<svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true"><circle cx="5.5" cy="5.5" r="4.25" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M8.7 8.7 12 12" stroke="currentColor" stroke-width="1.2"/></svg>';

  // ====================================================================== search
  const words = (s) => String(s || '').toLowerCase();
  function fieldScore(tok, text) {
    if (!text) return 0;
    if (text.startsWith(tok)) return 100;
    const i = text.indexOf(tok);
    if (i > 0 && /[\s\-/([“"']/.test(text[i - 1])) return 80;
    if (i >= 0) return 45;
    return 0;
  }
  function subseq(tok, text) { let i = 0; for (const ch of text) if (ch === tok[i]) i++; return i === tok.length; }
  /** score >= 0 is a match; higher is better */
  function score(c, q) {
    q = words(q).trim();
    if (!q) return 0;
    const title = words(c.title), al = words(c.aliases.join(' | ')), desc = words(c.description), grp = words(c.group);
    const keyFirst = c.seqs.map((s) => s[0].key);
    if (q.length === 1) {
      let s = -1;
      if (keyFirst.includes(q)) s = 300 + (c.seqs.some((x) => x.length > 1 && x[0].key === q) ? 20 : 0);
      if (title.split(/\s+/).some((w) => w.startsWith(q))) s = Math.max(s, 40);
      return s;
    }
    // a typed key combination: "mod+k", "⌘k", "g then t", "shift+t"
    let bonus = 0;
    if (/\+|then|⌘|⇧|^ctrl|^cmd|^mod/.test(q)) {
      try {
        const qs = seqId(parseSeq(q.replace(/⌘/g, 'mod+').replace(/⇧/g, 'shift+').replace(/⌥/g, 'alt+').replace(/\+\+/g, '+')));
        if (c.seqs.some((s) => seqId(s) === qs)) bonus = 500;
        else if (c.seqs.some((s) => seqId(s).startsWith(qs))) bonus = 250;
      } catch { /* not a key */ }
    }
    const keyw = words(c.seqs.map(seqWords).join(' | '));
    let total = 0;
    for (const tok of q.split(/\s+/).filter(Boolean)) {
      const best = Math.max(fieldScore(tok, title) * 1.0, fieldScore(tok, al) * 0.9, fieldScore(tok, keyw) * 0.7,
        fieldScore(tok, desc) * 0.6, fieldScore(tok, grp) * 0.4, tok.length >= 3 && subseq(tok, title) ? 12 : 0);
      if (!best) return bonus ? bonus : -1;
      total += best;
    }
    return total + bonus;
  }
  const rank = (list, q) => list.map((c, i) => ({ c, s: score(c, q), i })).filter((x) => x.s >= 0).sort((a, b) => b.s - a.s || a.i - b.i).map((x) => x.c);

  // ====================================================================== overlay (sheet / palette / picker / panel)
  const ov = { root: null, mode: null, q: '', idx: 0, items: [], prevFocus: null, pick: null, panel: null };
  function ensureRoot() {
    if (ov.root) return ov.root;
    ov.root = el('div', { class: 'rbk-ov', hidden: true, onmousedown: (e) => { if (e.target === ov.root) close(); } });
    document.body.appendChild(ov.root);
    return ov.root;
  }
  function isOpen() { return !!(ov.root && !ov.root.hidden); }
  function show(mode) {
    ensureRoot();
    if (!isOpen()) ov.prevFocus = document.activeElement;
    ov.mode = mode;
    ov.root.hidden = false;
    ov.root.dataset.mode = mode;
  }
  function close(restore = true) {
    if (!isOpen()) return false;
    ov.root.hidden = true;
    ov.root.replaceChildren();
    const m = ov.mode;
    ov.mode = null; ov.pick = null; ov.panel = null;
    if (restore && ov.prevFocus && ov.prevFocus.focus && document.contains(ov.prevFocus)) { try { ov.prevFocus.focus({ preventScroll: true }); } catch { /* ignore */ } }
    else if (restore && document.activeElement && document.activeElement !== document.body && ov.root.contains(document.activeElement)) document.activeElement.blur();
    ov.prevFocus = null;
    return m;
  }
  function searchBox(ph, onInput) {
    const inp = el('input', { type: 'text', placeholder: ph, spellcheck: 'false', autocomplete: 'off', 'aria-label': ph });
    inp.value = ov.q;
    inp.addEventListener('input', () => { ov.q = inp.value; ov.idx = 0; onInput(); });
    return [el('label', { class: 'rbk-search', html: SEARCH_SVG }, inp), inp];
  }
  function headEl(title) {
    return el('div', { class: 'rbk-head' }, el('span', { class: 'rbk-title', text: title }), el('span', { class: 'rbk-sp' }),
      el('button', { class: 'rbk-x', type: 'button', title: 'Close (Esc)', 'aria-label': 'Close', html: X_SVG, onclick: () => close() }));
  }

  // ---------- shortcuts sheet
  let sheetBody = null;
  function openSheet(q = '') {
    show('sheet');
    ov.q = q;
    const [box, inp] = searchBox('Search shortcuts and actions, for example “link” or “g”', drawSheet);
    sheetBody = el('div', { class: 'rbk-body' });
    const foot = el('div', { class: 'rbk-foot' },
      el('span', { text: 'Click a row to run it.' }), el('span', { class: 'rbk-sp' }),
      el('span', null, 'Find any action with '), capsEl([parseSeq('Mod+K')]));
    ov.root.replaceChildren(el('div', { class: 'rbk-sheet wide rbk-shortcuts', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Keyboard shortcuts' },
      headEl('Keyboard shortcuts'), box, sheetBody, foot));
    drawSheet();
    inp.focus();
  }
  function drawSheet() {
    if (ov.mode !== 'sheet' || !sheetBody) return;
    const here = scope();
    const list = all().filter((c) => !c.hidden);
    const shown = ov.q.trim() ? rank(list, ov.q) : list.filter((c) => c.seqs.length).concat(list.filter((c) => !c.seqs.length)); // keyed first
    const groups = new Map();
    for (const c of shown) { if (!groups.has(c.group)) groups.set(c.group, []); groups.get(c.group).push(c); }
    const searching = !!ov.q.trim();
    const gorder = [...groups.keys()].sort((a, b) => {
      if (searching) return shown.indexOf(groups.get(a)[0]) - shown.indexOf(groups.get(b)[0]); // best match first
      const rankOf = (g) => (here === 'viewer' && g === 'Viewer' ? -1 : GROUPS.includes(g) ? GROUPS.indexOf(g) : 99);
      return rankOf(a) - rankOf(b);
    });
    const kids = [];
    for (const g of gorder) {
      kids.push(el('div', { class: 'rbk-group', 'data-group': g }, el('div', { class: 'rbk-glabel', text: g }),
        groups.get(g).map((c) => {
          const can = available(c) && c.id !== 'general.close';
          return el('div', { class: 'rbk-row' + (can ? ' can' : ''), 'data-cmd': c.id, title: can ? 'Run it' : '',
            onclick: can ? () => { close(false); runCmd(c); } : null },
          el('div', { class: 'rbk-txt' }, el('div', { class: 'rbk-rt', text: c.title }), c.description ? el('div', { class: 'rbk-rd', text: c.description }) : null),
          c.seqs.length ? capsEl(c.seqs) : null);
        })));
    }
    if (!kids.length) kids.push(el('div', { class: 'rbk-empty', text: `Nothing matches “${ov.q.trim()}”. Try a word like “link”, “pdf” or “summary”.` }));
    sheetBody.replaceChildren(...kids);
  }

  // ---------- palette (and picker)
  let palList = null, palInput = null;
  function openPalette(q = '') {
    show('palette');
    ov.q = q; ov.idx = 0;
    const [box, inp] = searchBox('Type a command, for example “new note” or “review”', drawPalette);
    palInput = inp;
    palList = el('div', { class: 'rbk-body', role: 'listbox' });
    const foot = el('div', { class: 'rbk-foot' }, capsEl([parseSeq('Up'), parseSeq('Down')]), el('span', { text: 'to move,' }),
      capsEl([parseSeq('Enter')]), el('span', { text: 'to run' }), el('span', { class: 'rbk-sp' }),
      el('span', null, 'All shortcuts '), capsEl([parseSeq('?')]));
    ov.root.replaceChildren(el('div', { class: 'rbk-sheet rbk-palette', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Command palette' }, box, palList, foot));
    drawPalette();
    inp.focus();
  }
  function drawPalette() {
    if (!palList) return;
    let items = [];
    const sections = [];
    if (ov.mode === 'pick') {
      items = ov.pick.items(ov.q).slice(0, 60);
    } else {
      const live = all().filter((c) => available(c) && !c.hidden && c.id !== 'general.close');
      if (!ov.q.trim()) {
        const rec = LS.get('cmd.recent', []).map((id) => cmds.get(id)).filter((c) => c && live.includes(c)).slice(0, 5);
        const rest = live.filter((c) => !rec.includes(c));
        items = rec.concat(rest);
        if (rec.length) sections.push([0, 'Recent'], [rec.length, 'All actions']);
      } else items = rank(live, ov.q);
    }
    ov.items = items;
    ov.idx = Math.max(0, Math.min(ov.idx, items.length - 1));
    const kids = [];
    items.forEach((it, i) => {
      const sec = sections.find((s) => s[0] === i);
      if (sec) kids.push(el('div', { class: 'rbk-sec', text: sec[1] }));
      const on = i === ov.idx;
      const row = ov.mode === 'pick'
        ? el('div', { class: 'rbk-item' + (on ? ' on' : ''), role: 'option', 'aria-selected': String(on), 'data-id': it.id },
          el('div', { class: 'rbk-txt' }, el('span', { class: 'rbk-it serif', text: it.title }), it.sub ? el('span', { class: 'rbk-is', text: it.sub }) : null))
        : el('div', { class: 'rbk-item' + (on ? ' on' : ''), role: 'option', 'aria-selected': String(on), 'data-cmd': it.id },
          el('div', { class: 'rbk-txt' }, el('span', { class: 'rbk-it', text: it.title }), on && it.description ? el('span', { class: 'rbk-is', text: it.description }) : null),
          el('span', { class: 'rbk-ig', text: it.group }),
          it.seqs.length ? capsEl(it.seqs.slice(0, 1)) : null);
      row.addEventListener('mousemove', () => { if (ov.idx !== i) { ov.idx = i; drawPalette(); } });
      row.addEventListener('click', () => { ov.idx = i; accept(); });
      kids.push(row);
    });
    if (!items.length) kids.push(el('div', { class: 'rbk-empty', style: 'padding: 16px 10px', text: ov.mode === 'pick' ? (ov.pick.empty || 'Nothing matches.') : `No action matches “${ov.q.trim()}”. Press ? to browse every shortcut.` }));
    palList.replaceChildren(...kids);
    const cur = palList.querySelector('.rbk-item.on');
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
  }
  function accept() {
    const it = ov.items[ov.idx];
    if (!it) return;
    if (ov.mode === 'pick') { const p = ov.pick; close(false); p.onPick(it); return; }
    close(false);
    runCmd(it);
  }
  /** a palette-style picker: {title, placeholder, items(q) -> [{id,title,sub}], onPick(item), empty} */
  function pick(opts) {
    show('pick');
    ov.pick = opts; ov.q = ''; ov.idx = 0;
    const [box, inp] = searchBox(opts.placeholder || 'Search', drawPalette);
    palInput = inp;
    palList = el('div', { class: 'rbk-body', role: 'listbox' });
    ov.root.replaceChildren(el('div', { class: 'rbk-sheet rbk-palette rbk-pick', role: 'dialog', 'aria-modal': 'true', 'aria-label': opts.title || 'Pick' },
      opts.title ? el('div', { class: 'rbk-sec', style: 'padding: 12px 16px 0', text: opts.title }) : null, box, palList,
      el('div', { class: 'rbk-foot' }, capsEl([parseSeq('Enter')]), el('span', { text: opts.hint || 'to choose' }), el('span', { class: 'rbk-sp' }), capsEl([parseSeq('Esc')]), el('span', { text: 'to cancel' }))));
    drawPalette();
    inp.focus();
  }
  /** a plain sheet with custom content: {title, render(body), wide} */
  function panel(opts) {
    show('panel');
    ov.panel = opts;
    const body = el('div', { class: 'rbk-body ' + (opts.className || '') });
    ov.root.replaceChildren(el('div', { class: 'rbk-sheet' + (opts.wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': opts.title }, headEl(opts.title), body,
      opts.foot ? el('div', { class: 'rbk-foot' }, opts.foot) : null));
    opts.render(body, { el, capsEl, keysFor: (id) => (cmds.get(id) ? cmds.get(id).seqs : []), close });
    setTimeout(() => { const b = ov.root.querySelector('.rbk-x'); if (b) b.focus(); }, 0);
  }

  // keys while the overlay is open: handled here first (window, capture), and kept from the page
  window.addEventListener('keydown', (e) => {
    if (!isOpen()) return;
    const mod = e.metaKey || e.ctrlKey;
    const stop = () => { e.preventDefault(); e.stopPropagation(); };
    if (e.key === 'Escape') {
      stop();
      if (ov.q && (ov.mode === 'sheet' || ov.mode === 'palette' || ov.mode === 'pick') && document.activeElement && document.activeElement.tagName === 'INPUT') {
        const inp = document.activeElement; inp.value = ''; ov.q = ''; ov.idx = 0; ov.mode === 'sheet' ? drawSheet() : drawPalette();
        return;
      }
      close();
      return;
    }
    if (mod && !e.altKey && (e.code === 'KeyK' || e.key === 'k' || e.key === 'K') && !e.shiftKey) {
      stop();
      if (ov.mode === 'palette') close(); else { const q = ov.mode === 'sheet' ? ov.q : ''; close(false); openPalette(q); }
      return;
    }
    if (ov.mode === 'palette' || ov.mode === 'pick') {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || (e.ctrlKey && (e.key === 'n' || e.key === 'p'))) {
        stop();
        const n = ov.items.length; if (!n) return;
        const d = e.key === 'ArrowDown' || e.key === 'n' ? 1 : -1;
        ov.idx = (ov.idx + d + n) % n; drawPalette(); return;
      }
      if (e.key === 'Enter' && !e.isComposing) { stop(); accept(); return; }
    }
    if (ov.mode === 'sheet' && e.key === '?' && document.activeElement && document.activeElement.tagName !== 'INPUT') { stop(); close(); return; }
    if (e.key === 'Tab') { // keep focus inside
      const f = Array.from(ov.root.querySelectorAll('input, button')).filter((x) => x.offsetParent !== null);
      if (f.length) {
        const i = f.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
      }
    }
    e.stopPropagation(); // typing still works; page shortcuts don't fire behind the dialog
  }, true);

  // ---------- chord hint ("G then …")
  let chordEl = null;
  function showChordHint(starts) {
    if (!document.body) return;
    hideChordHint();
    const first = starts[0].seq[0];
    chordEl = el('div', { class: 'rbk-chord', role: 'status' },
      el('span', { class: 'opt' }, stepCaps(first).map((c) => el('kbd', { class: 'rbk-cap', text: c })), el('span', { style: 'color: var(--ink-2)', text: 'then' })),
      starts.slice(0, 8).map(({ c, seq }) => el('span', { class: 'opt' }, stepCaps(seq[1]).map((x) => el('kbd', { class: 'rbk-cap', text: x })), el('span', { text: c.title }))));
    document.body.appendChild(chordEl);
  }
  function hideChordHint() { if (chordEl) { chordEl.remove(); chordEl = null; } }

  // ---------- tooltips that teach the shortcut
  const tipQueue = new Set();
  function keysText(id) { const c = cmds.get(id); return c && c.seqs.length ? c.seqs.map(seqText).join(' or ') : ''; }
  function tooltip(node, id, base) {
    if (!node) return;
    const f = () => {
      if (!node.dataset.tipBase) node.dataset.tipBase = base != null ? base : (node.getAttribute('title') || '');
      const k = keysText(id);
      const b = node.dataset.tipBase;
      node.setAttribute('title', k ? (b ? `${b} (${k})` : k) : b);
    };
    tipQueue.add(f);
    f();
  }

  // ====================================================================== built-in commands
  register({
    id: 'general.shortcuts', title: 'Keyboard shortcuts', group: 'General', keys: '?', scope: 'any',
    description: 'Every shortcut, grouped, with a search field.',
    aliases: ['help', 'keys', 'hotkeys', 'cheat sheet', 'keyboard', 'list'],
    run: () => { if (ov.mode === 'sheet') close(); else { close(false); openSheet(); } },
  });
  register({
    id: 'general.palette', title: 'Command palette', group: 'General', keys: 'Mod+K', scope: 'any', allowInInput: true, noRecent: true,
    description: 'Find any action by name and run it.',
    aliases: ['commands', 'actions', 'run', 'find action', 'search actions'],
    run: () => { if (ov.mode === 'palette') close(); else { close(false); openPalette(); } },
  });

  // the Viewer and the Chrome extension: listed everywhere; viewer.html attaches the real handlers
  const DOCS = [
    { id: 'viewer.nextPage', title: 'Next page', keys: ['J', 'Right'], description: 'Scrolls to the next page of the PDF.', aliases: ['down', 'forward', 'page'] },
    { id: 'viewer.prevPage', title: 'Previous page', keys: ['K', 'Left'], description: 'Scrolls to the previous page.', aliases: ['up', 'back', 'page'] },
    { id: 'viewer.zoomIn', title: 'Zoom in', keys: ['+', '='], description: 'Makes the page bigger.', aliases: ['larger', 'magnify'] },
    { id: 'viewer.zoomOut', title: 'Zoom out', keys: ['-'], description: 'Makes the page smaller.', aliases: ['smaller'] },
    { id: 'viewer.fit', title: 'Fit width', keys: ['0'], description: 'Fits the page to the window width.', aliases: ['reset zoom', 'actual size'] },
    { id: 'viewer.addNote', title: 'Add note from selection', keys: ['N', 'Mod+Shift+H'], description: 'Select a passage or equation on a page, then save it as a fragment with your note.',
      aliases: ['highlight', 'clip', 'fragment', 'quote', 'annotate', 'excerpt', 'save passage'] },
    { id: 'viewer.save', title: 'Save the fragment', keys: ['Mod+Enter'], allowInInput: true, description: 'Saves the note you are writing on a selected passage.', aliases: ['submit'] },
    { id: 'viewer.ask', title: 'Ask about this paper or the selection', keys: ['A'], description: 'Opens the Ask tab beside the PDF to talk to your AI agent about this paper. With a passage selected, the question is about that passage.', aliases: ['chat', 'ai', 'claude', 'question', 'explain'] },
    { id: 'viewer.nextFrag', title: 'Next fragment', keys: [']'], description: 'Jumps to your next saved fragment in this paper.', aliases: ['highlight', 'annotation'] },
    { id: 'viewer.prevFrag', title: 'Previous fragment', keys: ['['], description: 'Jumps to the previous saved fragment.', aliases: ['highlight', 'annotation'] },
    { id: 'viewer.goPage', title: 'Go to page…', keys: ['G then P'], description: 'Puts the cursor in the page number box.', aliases: ['jump', 'page number'] },
    { id: 'viewer.side', title: 'Show or hide your notes', keys: ['S'], description: 'Toggles the fragments sidebar.', aliases: ['sidebar', 'panel'] },
    { id: 'viewer.back', title: 'Back to the graph', keys: ['G then G'], description: 'Returns to this paper in the graph. Esc does the same when nothing is selected.', aliases: ['home', 'leave', 'exit', 'close viewer'] },
    { id: 'extension.clip', title: 'Clip the selection from any page', keys: ['Alt+Shift+S'], description: 'In Chrome, with the research_brain clipper installed: files the selected text as a fragment.',
      aliases: ['highlight', 'web', 'chrome', 'browser', 'save quote'] },
    { id: 'extension.save', title: 'Save the clip', keys: ['Mod+Enter'], description: 'In the clipper window: saves the quote and your note.' },
    { id: 'extension.close', title: 'Close the clipper', keys: ['Esc'], description: 'In the clipper window.' },
    { id: 'extension.cite', title: 'Cite from the browser', description: 'In the research_brain popup, click Cite: search your library and copy \\cite{key}, a citation or a bibliography (\\cite first on Overleaf, formatted on Google Docs).',
      aliases: ['cite', 'citation', 'overleaf', 'google docs', 'bibliography', 'latex'] },
  ];
  for (const d of DOCS) register(Object.assign({ group: d.id.startsWith('viewer.') ? 'Viewer' : 'Extension', passive: true }, d));

  RB.commands = {
    register, unregister, annotate, layer, run: (id) => runCmd(cmds.get(id)), get: (id) => cmds.get(id), list: all,
    available: (id) => !!cmds.get(id) && available(cmds.get(id)), conflicts, keysText, tooltip, scope, isMac, capsEl, stepCaps,
    openSheet, openPalette, pick, panel, close, isOpen, parseSeq, seqText, score, _cmds: cmds, version: '1.0.0',
  };
})();
