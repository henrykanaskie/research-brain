/* wikilinks.js — "[[" autocomplete for note links in plain <textarea>s (Obsidian-style).
 *
 *   Wikilinks.attach(textarea, { nodes, self, search }) -> controller
 *
 * Typing "[[" opens a popup of note titles. Up/Down move, Enter/Tab insert, Esc closes.
 * Inserted text: [[Title]] when that title belongs to exactly one note (and has no [ ] |),
 * otherwise [[note-id|Title]], so the link always resolves to the note that was picked.
 *
 * options:
 *   nodes   () => iterable of {id, title, type, parent, year}; used for fragments (which
 *           /api/search leaves out), for the uniqueness check, and as an offline fallback
 *   self    () => id of the note being edited (left out of the suggestions)
 *   search  (q) => Promise<[{id,title,type,year}]>; default GET /api/search?q=
 *
 * Positioning and styling follow latex-editor.js (mirror-div caret measurement, fixed popup,
 * DESIGN.md tokens). It never handles keys while the LaTeX popup owns the textarea: that one
 * opens on "\", this one on "[[", and each only intercepts keys while its own popup is open.
 * Also injects the CSS for wikilink chips and the "Mentioned in" panel section.
 */
(function () {
  'use strict';
  if (window.Wikilinks) return;

  const UNSAFE = /[\[\]|\n]/;
  const key = (s) => String(s || '').trim().replace(/\s+/g, ' ').toLowerCase();

  function injectCSS() {
    if (document.getElementById('wl-style')) return;
    const css = `
.wl-pop{position:fixed;z-index:1000;min-width:260px;max-width:380px;max-height:300px;overflow-y:auto;margin:0;padding:3px 0;list-style:none;
  background:var(--sheet,#FFFFFF);color:var(--ink,#1E2226);border:1px solid var(--rule,#E6E8EB);border-radius:3px;
  font:13px/1.3 "Archivo",system-ui,-apple-system,"Segoe UI",sans-serif}
.wl-pop li{display:flex;align-items:center;gap:10px;padding:5px 10px;cursor:pointer;min-width:0}
.wl-pop li.on{background:var(--red-wash,rgba(178,58,51,.10))}
.wl-pop .wl-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:14px/1.3 "STIX Two Text","Times New Roman",Georgia,serif}
.wl-pop .wl-s{flex:none;max-width:45%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink-2,#5A6169);font-size:11.5px;font-variant-numeric:tabular-nums}
.wl-pop .wl-empty{color:var(--ink-2,#5A6169);cursor:default;font-size:12.5px}
.wl-pop .wl-new .wl-t{font-family:"Archivo",system-ui,sans-serif;font-size:13px;color:var(--ink-2,#5A6169)}
.wl-pop .tdot{width:8px;height:8px;flex:none;border-radius:50%;display:inline-block}
.wl-pop .tdot.paper{background:var(--ink,#1E2226)}
.wl-pop .tdot.note{border:1.5px solid var(--ink,#1E2226)}
.wl-pop .tdot.fragment{width:5px;height:5px;background:var(--red,#B23A33);margin:0 1.5px}
.wl-pop .tdot.new{border:1px dashed var(--ink-2,#5A6169)}
.cite.unresolved{cursor:pointer;color:var(--ink-2,#5A6169);border-bottom:1px dashed var(--ink-2,#5A6169)}
.cite.unresolved::before,.cite.unresolved::after{color:var(--rule,#E6E8EB)}
.cite.unresolved:hover{color:var(--ink,#1E2226);border-bottom-color:var(--ink,#1E2226)}
.cite.here{color:var(--red,#B23A33);border-bottom-color:var(--red,#B23A33);cursor:default}
.backlinks .row{align-items:flex-start}
.backlinks .tdot{margin-top:7px}
.backlinks .bl-snip{font:13.5px/1.5 "STIX Two Text","Times New Roman",Georgia,serif;color:var(--ink-2,#5A6169);margin-top:2px;
  overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.backlinks .bl-snip .katex{font-size:1em}
.unresolved-links{margin-top:14px}
.unresolved-links .ul-head{font-size:12px;color:var(--ink-2,#5A6169);margin-bottom:2px}
.unresolved-links .ul-row{display:flex;align-items:center;gap:8px;min-height:28px}
.unresolved-links .ul-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;color:var(--ink-2,#5A6169)}
`;
    const st = document.createElement('style');
    st.id = 'wl-style';
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ---------- caret measurement (same mirror-div approach as latex-editor.js) ----------
  const MIRROR = ['direction', 'boxSizing', 'width', 'overflowX', 'overflowY', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
    'borderStyle', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'fontStyle', 'fontVariant', 'fontWeight', 'fontStretch', 'fontSize',
    'fontSizeAdjust', 'lineHeight', 'fontFamily', 'textAlign', 'textTransform', 'textIndent', 'textDecoration', 'letterSpacing', 'wordSpacing', 'tabSize'];
  function caretXY(ta, pos) {
    const cs = getComputedStyle(ta);
    const div = document.createElement('div');
    const st = div.style;
    for (const p of MIRROR) st[p] = cs[p];
    st.position = 'absolute'; st.visibility = 'hidden'; st.top = '0'; st.left = '-9999px';
    st.whiteSpace = 'pre-wrap'; st.wordWrap = 'break-word'; st.overflow = 'hidden'; st.height = 'auto';
    div.textContent = ta.value.slice(0, pos);
    const span = document.createElement('span');
    span.textContent = ta.value.slice(pos) || '.';
    div.appendChild(span);
    document.body.appendChild(div);
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4;
    const r = ta.getBoundingClientRect();
    const x = r.left + span.offsetLeft - ta.scrollLeft;
    const y = r.top + span.offsetTop - ta.scrollTop;
    div.remove();
    return { x, y, h: lh };
  }

  // native-undo-friendly insertion (as in latex-editor.js)
  function insertAt(ta, start, end, text) {
    ta.focus();
    ta.setSelectionRange(start, end);
    let ok = false;
    try { ok = document.execCommand && document.execCommand('insertText', false, text); } catch (e) { ok = false; }
    if (!ok || ta.value.slice(start, start + text.length) !== text) {
      ta.setRangeText(text, start, end, 'end');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  // ---------- one shared popup for the page ----------
  let pop = null;
  function popEl() {
    if (!pop) {
      pop = document.createElement('ul');
      pop.className = 'wl-pop';
      pop.setAttribute('role', 'listbox');
      pop.setAttribute('aria-label', 'Link to a note');
      pop.hidden = true;
      pop.addEventListener('mousedown', (e) => e.preventDefault());
      document.body.appendChild(pop);
    }
    return pop;
  }
  window.addEventListener('resize', () => { if (pop && pop.__owner) pop.__owner.close(); });
  document.addEventListener('scroll', (e) => {
    if (pop && !pop.hidden && pop.__owner && !pop.contains(e.target) && e.target !== pop.__owner.ta) pop.__owner.close();
  }, true);

  async function defaultSearch(q) {
    const r = await fetch('/api/search?limit=8&q=' + encodeURIComponent(q));
    if (!r.ok) throw new Error('search failed');
    return r.json();
  }

  /** "[[" + query immediately before the caret, or null. */
  function trigger(ta) {
    const p = ta.selectionStart;
    if (p !== ta.selectionEnd) return null;
    const before = ta.value.slice(Math.max(0, p - 80), p);
    // no "\" in the query, so this never opens alongside the LaTeX popup (which opens on "\")
    const m = before.match(/(^|[^\\[])\[\[([^\[\]\n|\\$]{0,60})$/);
    if (!m) return null;
    // inside a code span on this line? (odd number of backticks before) -> no
    const line = ta.value.slice(ta.value.lastIndexOf('\n', p - 1) + 1, p);
    if (((line.match(/`/g) || []).length % 2) === 1) return null;
    return { from: p - m[2].length - 2, q: m[2] };
  }

  function attach(ta, options) {
    if (!ta || (ta.tagName !== 'TEXTAREA' && ta.tagName !== 'INPUT')) throw new Error('Wikilinks.attach needs a textarea');
    if (ta.__wikilinks) return ta.__wikilinks;
    injectCSS();
    const opt = Object.assign({ nodes: () => [], self: () => null, search: defaultSearch }, options || {});
    const nodeList = () => { try { return Array.from(opt.nodes() || []); } catch (e) { return []; } };
    const ed = { ta };
    let items = [], idx = 0, from = -1, seq = 0, timer = null, query = '';

    const isOpen = () => pop && !pop.hidden && pop.__owner === ed;
    ed.close = () => {
      clearTimeout(timer); seq++;
      if (pop && pop.__owner === ed) { pop.hidden = true; pop.__owner = null; }
      items = []; from = -1;
    };

    function localMatches(q, types) {
      const ql = q.trim().toLowerCase();
      const out = [];
      for (const n of nodeList()) {
        if (!types.includes(n.type)) continue;
        const t = String(n.title || '').toLowerCase();
        if (!ql || t.includes(ql)) out.push({ n, r: t.startsWith(ql) ? 0 : 1 });
      }
      out.sort((a, b) => a.r - b.r || String(a.n.title).localeCompare(String(b.n.title)));
      return out.map((x) => x.n);
    }

    async function update() {
      const t = trigger(ta);
      if (!t) { if (isOpen() || from >= 0) ed.close(); return; }
      // the LaTeX popup has the floor while it is showing
      const le = document.querySelector('.le-pop');
      if (le && !le.hidden) { ed.close(); return; }
      from = t.from; query = t.q;
      const my = ++seq;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        let server = [];
        try { server = await opt.search(t.q); }
        catch (e) { server = localMatches(t.q, ['paper', 'note']).slice(0, 8); }
        if (my !== seq) return;
        const self = opt.self();
        const byId = new Map(nodeList().map((n) => [n.id, n]));
        const seen = new Set();
        const list = [];
        for (const r of server) {
          if (!r || r.id === self || seen.has(r.id)) continue;
          seen.add(r.id); list.push(Object.assign({}, byId.get(r.id) || {}, r));
        }
        // fragments are left out of /api/search: match them here (title only, after the notes)
        const fr = t.q.trim() ? localMatches(t.q, ['fragment']) : [];
        for (const f of fr) {
          if (list.length >= 10) break;
          if (f.id === self || seen.has(f.id)) continue;
          seen.add(f.id); list.push(f);
        }
        const q = t.q.trim();
        if (q && !UNSAFE.test(q) && !list.some((x) => key(x.title) === key(q))) list.push({ create: true, title: q });
        items = list.slice(0, 11);
        idx = 0;
        draw();
      }, t.q ? 70 : 0);
    }

    function sub(n) {
      if (n.type === 'fragment') {
        const p = nodeList().find((x) => x.id === n.parent);
        return p ? 'in ' + p.title : 'Fragment';
      }
      return [n.type === 'paper' ? 'Paper' : 'Note', n.year].filter(Boolean).join(', ');
    }

    function draw() {
      const el = popEl();
      el.__owner = ed;
      el.innerHTML = '';
      if (!items.length) {
        const li = document.createElement('li');
        li.className = 'wl-empty';
        li.textContent = query.trim() ? 'No notes match' : 'Type a note title';
        el.appendChild(li);
      }
      items.forEach((n, i) => {
        const li = document.createElement('li');
        li.setAttribute('role', 'option');
        if (n.create) li.classList.add('wl-new');
        if (i === idx) { li.classList.add('on'); li.setAttribute('aria-selected', 'true'); }
        const dot = document.createElement('span');
        dot.className = 'tdot ' + (n.create ? 'new' : (n.type || 'note'));
        const t = document.createElement('span');
        t.className = 'wl-t';
        t.textContent = n.create ? `Link to a new note “${n.title}”` : (n.title || n.id);
        li.append(dot, t);
        if (!n.create) {
          const s = document.createElement('span');
          s.className = 'wl-s';
          s.textContent = sub(n);
          li.appendChild(s);
        }
        li.title = n.create ? 'Unresolved until you create it' : (n.title || '');
        li.addEventListener('click', () => accept(i));
        li.addEventListener('mousemove', () => { if (idx !== i) { idx = i; mark(); } });
        el.appendChild(li);
      });
      el.hidden = false;
      const xy = caretXY(ta, from);
      const w = el.offsetWidth, hgt = el.offsetHeight;
      const left = Math.min(Math.max(8, xy.x - 6), window.innerWidth - w - 8);
      let top = xy.y + xy.h + 2;
      if (top + hgt > window.innerHeight - 8) top = Math.max(8, xy.y - hgt - 2);
      el.style.left = left + 'px';
      el.style.top = top + 'px';
    }
    function mark() {
      const lis = pop.children;
      for (let i = 0; i < lis.length; i++) {
        lis[i].classList.toggle('on', i === idx);
        if (i === idx) lis[i].setAttribute('aria-selected', 'true'); else lis[i].removeAttribute('aria-selected');
      }
      const on = lis[idx];
      if (on) on.scrollIntoView({ block: 'nearest' });
    }

    /** [[Title]] if the title names exactly one note and is link-safe, else [[id|Title]]. */
    function linkText(n) {
      if (n.create) return `[[${n.title}]]`;
      const title = String(n.title || '').trim();
      const same = nodeList().filter((x) => key(x.title) === key(title));
      const unique = same.length <= 1 && (!same.length || same[0].id === n.id);
      if (title && unique && !UNSAFE.test(title) && !nodeList().some((x) => x.id === title && x.id !== n.id)) return `[[${title}]]`;
      return `[[${n.id}|${title.replace(/[\[\]|\n]/g, ' ').trim() || n.id}]]`;
    }

    function accept(i) {
      const n = items[i == null ? idx : i];
      if (!n || from < 0) return;
      const start = from;
      let end = ta.selectionStart;
      if (ta.value.slice(end, end + 2) === ']]') end += 2; // swallow an existing closer
      const text = linkText(n);
      ed.close();
      insertAt(ta, start, end, text);
      const p = start + text.length;
      ta.setSelectionRange(p, p);
    }

    const onInput = () => update();
    const onKeyDown = (e) => {
      if (e.isComposing || !isOpen()) return;
      const mod = e.metaKey || e.ctrlKey;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); e.stopImmediatePropagation();
        if (items.length) { idx = (idx + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; mark(); }
        return;
      }
      if ((e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey && !mod) {
        if (!items.length) { ed.close(); return; }
        e.preventDefault(); e.stopImmediatePropagation(); accept(); return;
      }
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); ed.close(); }
    };
    const onNav = (e) => { if (!e.key || e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End') { if (isOpen() || from >= 0) update(); } };
    const onBlur = () => { setTimeout(() => { if (document.activeElement !== ta) ed.close(); }, 120); };

    ta.addEventListener('input', onInput);
    ta.addEventListener('keydown', onKeyDown, true);
    ta.addEventListener('keyup', onNav);
    ta.addEventListener('mouseup', onNav);
    ta.addEventListener('blur', onBlur);

    const ctl = {
      textarea: ta,
      close: ed.close,
      isOpen,
      detach() {
        ta.removeEventListener('input', onInput);
        ta.removeEventListener('keydown', onKeyDown, true);
        ta.removeEventListener('keyup', onNav);
        ta.removeEventListener('mouseup', onNav);
        ta.removeEventListener('blur', onBlur);
        ed.close();
        delete ta.__wikilinks;
      },
    };
    ta.__wikilinks = ctl;
    return ctl;
  }

  window.Wikilinks = { attach, version: '1.0.0' };
})();
