/* notes-editor.js: outliner keys and a small formatting bar for the Markdown <textarea>s you write notes in.
 *
 *   NotesEditor.attach(textarea, { toolbar: element|null|false, paste: (html) => Promise<string|null>,
 *                                  image: (file) => Promise<markdown> }) -> controller
 *   NotesEditor.toggleTask(src, index) -> src with the index-th "- [ ]" / "- [x]" (outside code) flipped
 *   NotesEditor.indentText / outdentText / continueList: the pure text edits, for tests
 *
 * Keys (the math and [[link]] autocompletes run first and keep Tab and Enter while their list is open):
 *   Tab / Shift+Tab   nest / un-nest the line(s) under the caret, never leave the field. Esc, then Tab, moves on.
 *   Enter             in a list, starts the next item (same bullet, next number, a fresh checkbox);
 *                     on an empty item, un-nests it, or ends the list at the outermost level
 *   Backspace         right after an empty item's bullet, un-nests it, or removes the bullet
 *   Cmd/Ctrl+B, I     bold, italic;  Cmd/Ctrl+Shift+H  highlight;  Cmd/Ctrl+Shift+X  checkbox on/off
 * Paste: formatted text (Google Docs, Word, Apple Notes, Notion, web pages) comes in as Markdown when `paste`
 * turns its HTML into Markdown; Cmd/Ctrl+Shift+V pastes plain text.
 * Images: pasting a screenshot or figure, dropping image files, or the toolbar's Image button stores each with
 * `image` and puts its Markdown (![name](/attachments/...)) on a line of its own where the caret is.
 */
(function () {
  'use strict';
  if (window.NotesEditor) return;

  const IND = '    ';  // one nesting level: 4 spaces (Markdown nests a sub-list under "- " and "1. " alike)
  // indent, marker, gap, optional checkbox; quotes ("> ") may come before
  const ITEM = /^((?:[ \t]*>[ \t]?)*)([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+)(\[[ xX]\][ \t]+)?/;
  const QUOTE = /^((?:[ \t]*>[ \t]?)+)/;

  const lineStart = (v, p) => v.lastIndexOf('\n', p - 1) + 1;
  const lineEnd = (v, p) => { const i = v.indexOf('\n', p); return i < 0 ? v.length : i; };
  /** true when position p is inside a ``` or ~~~ fenced code block */
  function inFence(v, p) {
    let open = null;
    for (const ln of v.slice(0, lineStart(v, p)).split('\n')) {
      const m = ln.match(/^[ \t]*(`{3,}|~{3,})/);
      if (!m) continue;
      if (!open) open = m[1][0];
      else if (m[1][0] === open) open = null;
    }
    return !!open;
  }

  /** the lines [a, b) that a selection s..e touches (a selection ending at a line's start leaves that line out) */
  function lineSpan(v, s, e) {
    const a = lineStart(v, s);
    let b = lineEnd(v, e > s && v[e - 1] === '\n' ? e - 1 : e);
    return [a, b];
  }

  const widthOf = (ws) => ws.replace(/\t/g, IND).length;
  /** the number an ordered item should carry at its (new) indent: one more than the item before it at that indent */
  function numberFor(lines, i) {
    const m = lines[i].match(ITEM);
    const w = widthOf(m[2]);
    for (let j = i - 1; j >= 0; j--) {
      const o = lines[j].match(ITEM);
      if (!o) { if (!lines[j].trim() && j > 0 && !lines[j - 1].trim()) break; continue; }
      const ow = widthOf(o[2]);
      if (ow < w) break;
      if (ow === w) return /\d/.test(o[3]) ? parseInt(o[3], 10) + 1 : 1;
    }
    return 1;
  }
  function renumber(lines, i) {
    const m = lines[i].match(ITEM);
    if (!m || !/\d/.test(m[3])) return;
    const n = numberFor(lines, i), delim = m[3].slice(-1);
    lines[i] = m[1] + m[2] + n + delim + lines[i].slice(m[1].length + m[2].length + m[3].length);
  }

  /** shift the lines s..e touches by one level. Returns { text, a, b, s, e } for the replaced span [a, b).
   *  A list item nests at most one level under the item above it (Markdown would read deeper as code), and a
   *  plain line outside a list keeps its place; inside ``` code every line moves. */
  function shiftLines(v, s, e, dir) {
    const [a, b] = lineSpan(v, s, e);
    const lines = v.slice(a, b).split('\n');
    const before = v.slice(0, a).split('\n');
    before.pop();  // the (empty) piece before a
    const code = inFence(v, a);
    const all = before.concat(lines);
    const deltas = [];
    const above = (k) => {  // indent width of the nearest list item above line k (blank lines skipped), or -1
      for (let j = k - 1; j >= 0; j--) {
        const o = all[j].match(ITEM);
        if (o) return widthOf(o[2]);
        if (all[j].trim() && !/^\s/.test(all[j])) return -1;  // a plain paragraph ends the list
      }
      return -1;
    };
    for (let i = 0; i < lines.length; i++) {
      const k = before.length + i, ln = all[k];
      const q = (ln.match(QUOTE) || [''])[0];
      const rest = ln.slice(q.length);
      let out = ln;
      if (dir > 0) {
        const m = ln.match(ITEM);
        if (!ln.trim()) out = ln;
        else if (code) out = q + IND + rest;
        else if (m) {
          const up = above(k);
          if (up >= 0 && widthOf(m[2]) + IND.length <= up + IND.length) out = q + IND + rest;
        } else if (above(k) >= 0 && !/^\s/.test(rest)) out = q + IND + rest;  // a line under an item: part of it
      } else {
        const lead = rest.match(/^( {1,4}|\t)/);
        if (lead) out = q + rest.slice(lead[0].length);
      }
      all[k] = out;
      renumber(all, k);
      deltas.push(all[k].length - ln.length);
    }
    const text = all.slice(before.length).join('\n');
    const first = deltas[0] || 0, total = deltas.reduce((x, y) => x + y, 0);
    return { text, a, b, s: Math.max(a, s + first), e: Math.max(a, e + total) };
  }
  const indentText = (v, s, e) => shiftLines(v, s, e, 1);
  const outdentText = (v, s, e) => shiftLines(v, s, e, -1);

  /** Enter at p (no selection): { text, a, b, caret } replacing [a, b), or null to let the browser add a newline */
  function continueList(v, p) {
    if (inFence(v, p)) return null;
    const a = lineStart(v, p), b = lineEnd(v, p), ln = v.slice(a, b);
    const m = ln.match(ITEM);
    if (m) {
      const head = m[0].length;
      if (p - a < head) return null;  // caret in the bullet itself
      const rest = ln.slice(head);
      if (!rest.trim()) {
        // an empty item: un-nest it, or (outermost) end the list
        if (m[2]) { const r = outdentText(v, p, p); return { text: r.text, a: r.a, b: r.b, caret: r.s }; }
        return { text: m[1], a, b, caret: a + m[1].length };
      }
      const num = /\d/.test(m[3]) ? (parseInt(m[3], 10) + 1) + m[3].slice(-1) : m[3];
      const box = m[5] ? '[ ] ' : '';
      const next = m[1] + m[2] + num + ' ' + box;
      const ins = '\n' + next;
      return { text: v.slice(a, p) + ins + v.slice(p, b).replace(/^[ \t]+/, ''), a, b, caret: p + ins.length };
    }
    const q = ln.match(QUOTE);
    if (q) {
      if (!ln.slice(q[0].length).trim()) return { text: '', a, b, caret: a };  // empty quote line: leave the quote
      const ins = '\n' + q[0].replace(/\s*$/, ' ');
      return { text: v.slice(a, p) + ins + v.slice(p, b), a, b, caret: p + ins.length };
    }
    return null;
  }

  /** flip the index-th task checkbox (outside fenced code) */
  function toggleTask(src, index) {
    const lines = String(src || '').split('\n');
    let n = 0, fence = null;
    for (let i = 0; i < lines.length; i++) {
      const f = lines[i].match(/^[ \t]*(`{3,}|~{3,})/);
      if (f) { if (!fence) fence = f[1][0]; else if (f[1][0] === fence) fence = null; continue; }
      if (fence) continue;
      const m = lines[i].match(/^((?:[ \t]*>[ \t]?)*[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+)\[([ xX])\](?=[ \t])/);
      if (!m) continue;
      if (n++ === index) {
        lines[i] = m[1] + (m[2] === ' ' ? '[x]' : '[ ]') + lines[i].slice(m[0].length);
        return lines.join('\n');
      }
    }
    return src;
  }

  // ---------------------------------------------------------------- editing that keeps native undo
  function replace(ta, a, b, text, s, e) {
    ta.focus();
    ta.setSelectionRange(a, b);
    let ok = false;
    try { ok = document.execCommand && document.execCommand('insertText', false, text); } catch (err) { ok = false; }
    if (!ok || ta.value.slice(a, a + text.length) !== text) {
      ta.setRangeText(text, a, b, 'end');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
    ta.setSelectionRange(s, e == null ? s : e);
  }
  function apply(ta, r) { if (r && (r.text !== ta.value.slice(r.a, r.b))) replace(ta, r.a, r.b, r.text, r.s != null ? r.s : r.caret, r.e != null ? r.e : r.caret); else if (r) ta.setSelectionRange(r.s != null ? r.s : r.caret, r.e != null ? r.e : r.caret); }

  /** wrap the selection in `mark` (or unwrap it when it already is); no selection: the word at the caret */
  function wrap(ta, mark) {
    const v = ta.value;
    let s = ta.selectionStart, e = ta.selectionEnd;
    if (s === e) {
      const left = v.slice(0, s).match(/[\p{L}\p{N}_'-]*$/u)[0].length, right = v.slice(s).match(/^[\p{L}\p{N}_'-]*/u)[0].length;
      if (left + right) { s -= left; e += right; }
    }
    const k = mark.length;
    if (s >= k && v.slice(s - k, s) === mark && v.slice(e, e + k) === mark) { replace(ta, s - k, e + k, v.slice(s, e), s - k, e - k); return; }
    const sel = v.slice(s, e);
    if (sel.length > 2 * k && sel.startsWith(mark) && sel.endsWith(mark)) { replace(ta, s, e, sel.slice(k, -k), s, e - 2 * k); return; }
    replace(ta, s, e, mark + sel + mark, s + k, e + k);
  }

  /** put `kind` at the start of every line the selection touches ('- ', '1. ', '- [ ] ', '> ', '## '), or take it off */
  function lineKind(ta, kind) {
    const v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
    const [a, b] = lineSpan(v, s, e);
    const lines = v.slice(a, b).split('\n');
    const has = (ln) => {
      const m = ln.match(ITEM);
      if (kind === 'task') return !!(m && m[5]);
      if (kind === 'ul') return !!(m && !m[5] && !/\d/.test(m[3]));
      if (kind === 'ol') return !!(m && /\d/.test(m[3]));
      if (kind === 'quote') return /^[ \t]*>/.test(ln);
      if (kind === 'h') return /^#{1,6} /.test(ln);
      return false;
    };
    const content = lines.filter((ln) => ln.trim());
    const off = content.length && content.every(has);
    let n = 0;
    const out = lines.map((ln) => {
      if (!ln.trim() && lines.length > 1) return ln;
      if (kind === 'h') {
        const m = ln.match(/^(#{1,6}) /);
        if (!m) return '## ' + ln;
        return m[1].length >= 3 ? ln.slice(m[0].length) : '#' + ln;  // ## -> ### -> plain
      }
      if (kind === 'quote') return off ? ln.replace(/^([ \t]*)>[ \t]?/, '$1') : '> ' + ln;
      const m = ln.match(ITEM);
      const indent = m ? m[2] : (ln.match(/^[ \t]*/) || [''])[0];
      const body = m ? ln.slice(m[0].length) : ln.slice(indent.length);
      const pre = m ? m[1] : '';
      if (off) return pre + indent + body;
      n += 1;
      const mark = kind === 'ol' ? n + '. ' : kind === 'task' ? '- [ ] ' : '- ';
      return pre + indent + mark + body;
    });
    const text = out.join('\n');
    const caret = s === e ? Math.max(a, Math.min(a + text.length, s + (out[0].length - lines[0].length))) : null;
    replace(ta, a, b, text, caret != null ? caret : a, caret != null ? caret : a + text.length);
  }

  /** a checkbox on the caret's line: none -> "- [ ] ", "[ ]" -> "[x]" -> "[ ]" */
  function toggleLineTask(ta) {
    const v = ta.value, p = ta.selectionStart, a = lineStart(v, p), b = lineEnd(v, p), ln = v.slice(a, b);
    const m = ln.match(ITEM);
    if (m && m[5]) {
      const at = a + m[0].length - m[5].length;
      const on = m[5][1] === ' ';
      replace(ta, at + 1, at + 2, on ? 'x' : ' ', p, p);
      return;
    }
    lineKind(ta, 'task');
  }

  // ---------------------------------------------------------------- toolbar
  const TOOLS = [
    { id: 'h', label: 'H', title: 'Heading (again for a smaller one, a third time for plain text)', run: (ta) => lineKind(ta, 'h') },
    { id: 'b', label: 'B', title: 'Bold (⌘B)', run: (ta) => wrap(ta, '**') },
    { id: 'i', label: 'I', title: 'Italic (⌘I)', run: (ta) => wrap(ta, '*') },
    { id: 'mark', label: 'Highlight', title: 'Highlight (⇧⌘H)', run: (ta) => wrap(ta, '==') },
    { sep: true },
    { id: 'ul', label: '•', title: 'Bulleted list. Tab nests an item, Shift+Tab un-nests it', run: (ta) => lineKind(ta, 'ul') },
    { id: 'ol', label: '1.', title: 'Numbered list', run: (ta) => lineKind(ta, 'ol') },
    { id: 'task', label: '☐', title: 'Checklist (⇧⌘X ticks the box on this line)', run: (ta) => lineKind(ta, 'task') },
    { id: 'quote', label: '❝', title: 'Quote', run: (ta) => lineKind(ta, 'quote') },
    { sep: true },
    { id: 'out', label: '⇤', title: 'Un-nest (Shift+Tab)', run: (ta) => apply(ta, outdentText(ta.value, ta.selectionStart, ta.selectionEnd)) },
    { id: 'in', label: '⇥', title: 'Nest (Tab)', run: (ta) => apply(ta, indentText(ta.value, ta.selectionStart, ta.selectionEnd)) },
    { sep: true, needs: 'image' },
    { id: 'img', label: 'Image', title: 'Add a figure or image (or paste one, or drop it here)', needs: 'image', run: (ta, ctl) => ctl.pickImage() },
  ];
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  function makeToolbar(ta, ctl, opts) {
    const bar = document.createElement('div');
    bar.className = 'ne-bar';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Formatting');
    for (const t of TOOLS) {
      if (t.needs && !opts[t.needs]) continue;
      if (t.sep) { const s = document.createElement('span'); s.className = 'ne-sep'; bar.appendChild(s); continue; }
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ne-btn ne-' + t.id;
      b.textContent = t.label;
      b.title = isMac ? t.title : t.title.replace(/⇧⌘/g, 'Ctrl+Shift+').replace(/⌘/g, 'Ctrl+');
      b.setAttribute('aria-label', t.title.replace(/\s*\(.*$/, ''));
      b.dataset.tool = t.id;
      // keep the textarea's selection: a mousedown on the button would blur it first
      b.addEventListener('mousedown', (e) => e.preventDefault());
      b.addEventListener('click', () => { t.run(ta, ctl); if (t.id !== 'img') ta.focus(); });
      bar.appendChild(b);
    }
    return bar;
  }

  // ---------------------------------------------------------------- attach
  function attach(ta, opts) {
    opts = opts || {};
    let released = false;   // Esc: the next Tab leaves the field, like everywhere else
    let plainPaste = false;
    const onKey = (e) => {
      if (e.defaultPrevented || e.isComposing) return;
      const mod = e.metaKey || e.ctrlKey;
      if (e.key === 'Escape') { released = true; return; }
      if (e.key === 'Tab' && !mod && !e.altKey) {
        if (released) { released = false; return; }
        e.preventDefault();
        const v = ta.value, s0 = ta.selectionStart, e0 = ta.selectionEnd;
        const r = (e.shiftKey ? outdentText : indentText)(v, s0, e0);
        if (r.text === v.slice(r.a, r.b) && !e.shiftKey && s0 === e0 && s0 > lineStart(v, s0)) {
          replace(ta, s0, e0, IND, s0 + IND.length);  // mid-line in plain text: a tab's worth of space
          return;
        }
        apply(ta, r);
        return;
      }
      released = false;
      if (e.key === 'Enter' && !e.shiftKey && !mod && !e.altKey && ta.selectionStart === ta.selectionEnd) {
        const r = continueList(ta.value, ta.selectionStart);
        if (r) { e.preventDefault(); apply(ta, r); }
        return;
      }
      if (e.key === 'Backspace' && !mod && !e.altKey && ta.selectionStart === ta.selectionEnd) {
        const v = ta.value, p = ta.selectionStart, a = lineStart(v, p), ln = v.slice(a, lineEnd(v, p));
        const m = ln.match(ITEM);
        if (m && p - a === m[0].length && !ln.slice(m[0].length).trim()) {
          e.preventDefault();
          if (m[2]) apply(ta, outdentText(v, p, p));
          else replace(ta, a, a + m[0].length, m[1], a + m[1].length);
        }
        return;
      }
      if (mod && !e.altKey) {
        const k = (e.key || '').toLowerCase();
        if (!e.shiftKey && k === 'b') { e.preventDefault(); wrap(ta, '**'); }
        else if (!e.shiftKey && k === 'i') { e.preventDefault(); wrap(ta, '*'); }
        else if (e.shiftKey && k === 'h') { e.preventDefault(); wrap(ta, '=='); }
        else if (e.shiftKey && k === 'x') { e.preventDefault(); toggleLineTask(ta); }
        else if (e.shiftKey && k === 'v') plainPaste = true;
      }
    };
    const onPaste = (e) => {
      const plain = plainPaste; plainPaste = false;
      if (plain || !opts.paste || !e.clipboardData) return;
      const html = e.clipboardData.getData('text/html');
      const text = e.clipboardData.getData('text/plain');
      // only formatted text that has structure worth keeping; copying from a note itself gives plain text
      if (!html || html.length > 2_000_000 || !/<(ul|ol|li|h[1-6]|b|strong|em|i|blockquote|table|pre|code|mark|s|del)\b|font-weight|font-style:\s*italic|mso-list/i.test(html)) return;
      e.preventDefault();
      Promise.resolve(opts.paste(html)).then((md) => {
        const out = md && md.trim() ? md : text;
        const a = ta.selectionStart, b = ta.selectionEnd;
        replace(ta, a, b, out, a + out.length);
      }, () => { const a = ta.selectionStart; replace(ta, a, ta.selectionEnd, text, a + text.length); });
    };
    // ---- images
    const isImage = (f) => f && /^image\//.test(f.type || '');
    const insertImages = (files, at) => {
      files = Array.from(files || []).filter(isImage);
      if (!files.length || !opts.image) return false;
      let pos = at == null ? ta.selectionEnd : at;
      (async () => {
        for (const f of files) {
          let md;
          try { md = await opts.image(f); } catch (err) { continue; }
          if (!md) continue;
          const v = ta.value;
          pos = Math.max(0, Math.min(pos, v.length));
          const lead = pos > 0 && v[pos - 1] !== '\n' ? '\n\n' : (pos > 1 && v[pos - 2] !== '\n' ? '\n' : '');
          const trail = pos < v.length && v[pos] !== '\n' ? '\n\n' : '\n';
          const text = lead + md + trail;
          replace(ta, pos, pos, text, pos + text.length);
          pos += text.length;
        }
      })();
      return true;
    };
    const onPasteImage = (e) => {
      const cd = e.clipboardData;
      if (!cd || !opts.image) return;
      const files = Array.from(cd.files || []).filter(isImage);
      // Word and Excel put a picture of the copied text next to the text itself: text wins
      if (!files.length || (cd.getData('text/plain') || '').trim()) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      insertImages(files);
    };
    const onDragOver = (e) => {
      if (!opts.image || !e.dataTransfer || !Array.from(e.dataTransfer.items || []).some((i) => i.kind === 'file')) return;
      e.preventDefault();
      ta.classList.add('ne-drop');
    };
    const onDragLeave = () => ta.classList.remove('ne-drop');
    const onDrop = (e) => {
      ta.classList.remove('ne-drop');
      const files = e.dataTransfer && Array.from(e.dataTransfer.files || []).filter(isImage);
      if (!files || !files.length || !opts.image) return;
      e.preventDefault();
      e.stopPropagation();
      insertImages(files, ta.selectionEnd);
    };
    const pickImage = () => {
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = 'image/*';
      inp.multiple = true;
      const at = ta.selectionEnd;
      inp.addEventListener('change', () => { insertImages(inp.files, at); ta.focus(); });
      inp.click();
    };
    ta.addEventListener('paste', onPasteImage);
    ta.addEventListener('dragover', onDragOver);
    ta.addEventListener('dragleave', onDragLeave);
    ta.addEventListener('drop', onDrop);

    ta.addEventListener('keydown', onKey);
    ta.addEventListener('paste', onPaste);
    ta.addEventListener('blur', () => { released = false; });
    let bar = null;
    const ctl = { pickImage, insertImages };
    if (opts.toolbar !== false) {
      bar = makeToolbar(ta, ctl, opts);
      if (opts.toolbar && opts.toolbar.nodeType === 1) opts.toolbar.appendChild(bar);
      else ta.parentNode && ta.parentNode.insertBefore(bar, ta);
    }
    ctl.toolbar = bar;
    ctl.detach = () => {
      ta.removeEventListener('keydown', onKey); ta.removeEventListener('paste', onPaste); ta.removeEventListener('paste', onPasteImage);
      ta.removeEventListener('dragover', onDragOver); ta.removeEventListener('dragleave', onDragLeave); ta.removeEventListener('drop', onDrop);
      if (bar) bar.remove();
    };
    return ctl;
  }

  window.NotesEditor = { attach, toggleTask, indentText, outdentText, continueList };
})();
