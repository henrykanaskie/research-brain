/* research_brain — frontend (vanilla JS, no build step)
 * Libraries (CDN globals): ForceGraph, marked, katex.
 * Talks to the FastAPI backend described in API.md.
 */
(() => {
  'use strict';

  // ======================================================================
  // Constants & state
  // ======================================================================
  // colours come from the DESIGN.md tokens in style.css (re-read on theme change)
  /** short name of the active AI agent for UI copy ("Claude", "Codex", "Your local model") */
  const ai = () => window.RB_AI || 'Claude';
  const C = { ground: '#EEEFEB', sheet: '#F7F7F4', ink: '#15181B', ink2: '#61676C', ink3: '#9A9EA0', rule: '#D5D7D1', red: '#BD3A2C', machine: '#4A6487', dot: 'rgba(21,24,27,.16)' };
  function readTokens() {
    const cs = getComputedStyle(document.documentElement);
    const g = (n, d) => (cs.getPropertyValue(n) || '').trim() || d;
    C.ground = g('--ground', C.ground); C.sheet = g('--sheet', C.sheet); C.ink = g('--ink', C.ink);
    C.ink2 = g('--ink-2', C.ink2); C.rule = g('--rule', C.rule); C.red = g('--red', C.red); C.machine = g('--machine', C.machine);
    C.ink3 = g('--ink-3', C.ink3); C.dot = g('--dot', C.dot);
    C.violet = g('--violet', '#6B5A93'); C.ochre = g('--ochre', '#A9761F');
  }
  function rgba(col, a) {
    const m = /^#([0-9a-f]{6})$/i.exec(col);
    if (m) { const v = parseInt(m[1], 16); return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`; }
    const r = /rgba?\(([^)]+)\)/.exec(col);
    if (r) { const p = r[1].split(',').map((x) => x.trim()); return `rgba(${p[0]},${p[1]},${p[2]},${a})`; }
    return col;
  }
  const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const T = (ms) => (reducedMotion() ? 0 : ms);
  const SAVE_DEBOUNCE = 800;
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('rb.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('rb.' + k, JSON.stringify(v)); } catch { /* ignore */ } },
  };

  const S = {
    status: null,           // last /api/status
    online: false,
    claudeOK: false,
    claudeWhy: 'Checking for an AI agent…',
    embedder: '',
    threshold: 0.45,
    showSim: LS.get('showSim', true),
    showCites: LS.get('showCites', true),
    nodes: new Map(),       // id -> node object (persisted across refreshes to keep positions)
    links: [],              // current link objects in the graph
    simLinks: [],
    search: '',
    matches: new Set(),
    hover: null,
    hoverLink: null,
    selected: null,         // id of note open in panel
    cur: null,              // full note open in panel
    patch: {},              // pending unsaved fields for cur
    saveTimer: null,
    saving: null,           // promise of in-flight save
    saveErr: null,
    bodyMode: {},           // id -> 'edit'|'preview'
    ops: new Map(),         // key -> {start} pending long ops (Claude / S2)
    discover: new Map(),    // note id -> {data, tab}
    explain: null,          // {a, b}
    explainCache: new Map(),
    chat: [],               // [{role, content, sources?, error?}] — the conversation for the current scope
    chatPaper: null,        // {id, title} when talking about one paper; null = the whole library
    chatRelated: false,     // with chatPaper: also draw on the most similar papers
    chatThreads: {},        // scope key ('' = library, else paper id) -> conversation, so each paper keeps its own
    chatBusy: false,
    quoteIdx: new Map(),    // fragment id -> quote (for search)
    crawled: new Set(),     // parent ids whose fragments we've indexed
    noteMeta: new Map(),    // id -> {has_pdf, type, title} cache for parents
    firstGraph: true,
  };

  // ======================================================================
  // Small DOM helpers
  // ======================================================================
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'html') el.innerHTML = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k === 'dataset') Object.assign(el.dataset, v);
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (k in el && typeof v !== 'string' && k !== 'list') el[k] = v;
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    appendKids(el, children);
    return el;
  }
  function appendKids(el, kids) {
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
  }
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function fmtAuthors(a, n = 3) {
    if (!Array.isArray(a) || !a.length) return '';
    if (a.length <= n) return a.join(', ');
    return a.slice(0, n).join(', ') + ' et al.';
  }
  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) + ' ' +
      d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }
  function truncate(s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  const typeLabel = (t) => (t === 'paper' ? 'paper' : t === 'fragment' ? 'fragment' : 'note');

  // ======================================================================
  // API
  // ======================================================================
  function errText(data, status) {
    if (data && data.detail !== undefined) {
      const d = data.detail;
      if (typeof d === 'string') return d;
      if (Array.isArray(d)) return d.map((x) => (x && x.msg ? `${(x.loc || []).slice(1).join('.') || 'request'}: ${x.msg}` : JSON.stringify(x))).join('\n');
      return JSON.stringify(d);
    }
    return `Request failed (HTTP ${status})`;
  }
  async function api(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, opt); }
    catch (e) { throw new Error('Cannot reach the research_brain server. Is it running?'); }
    let data = null;
    const txt = await res.text();
    if (txt) { try { data = JSON.parse(txt); } catch { data = { detail: txt.slice(0, 400) }; } }
    if (!res.ok) throw new Error(errText(data, res.status));
    return data;
  }
  /**
   * POST to a text/event-stream endpoint (the /stream twins of critique and chat) and read it with
   * fetch + ReadableStream. Calls onDelta(fullTextSoFar) as text arrives; resolves with the `done`
   * JSON (same shape as the plain endpoint). Errors carry .claude = true when Claude itself failed
   * (an `error` event or an HTTP 4xx): those must not be retried. Anything else (the stream API
   * missing, the connection dropping before `done`) is a transport failure, and the caller falls back
   * to the plain endpoint.
   */
  async function apiStream(path, body, onDelta) {
    if (!window.ReadableStream || !window.TextDecoder) throw new Error('streaming not supported');
    const opt = { method: 'POST', headers: { Accept: 'text/event-stream' } };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const res = await fetch(path, opt);
    if (!res.ok) {
      let data = null;
      const txt = await res.text().catch(() => '');
      try { data = JSON.parse(txt); } catch { data = { detail: txt.slice(0, 400) }; }
      const err = new Error(errText(data, res.status));
      // a real 4xx answer (e.g. "write your summary first") is final; 404/405 with no detail means an
      // older server without the /stream route, so the caller falls back to the plain endpoint
      if (res.status >= 400 && res.status < 500 && !((res.status === 404 || res.status === 405) && /^(Not Found|Method Not Allowed)$/.test(err.message))) err.claude = true;
      throw err;
    }
    if (!res.body || !/event-stream/.test(res.headers.get('content-type') || '')) throw new Error('not a stream');
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '', text = '', done = null;
    const handle = (frame) => {
      let ev = 'message';
      const data = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (!data.length) return;
      let d;
      try { d = JSON.parse(data.join('\n')); } catch { return; }
      if (ev === 'delta' && d.text) { text += d.text; onDelta(text); }
      else if (ev === 'done') done = d;
      else if (ev === 'error') { const e = new Error(d.detail || 'Claude failed'); e.claude = true; throw e; }
    };
    for (;;) {
      const { value, done: end } = await reader.read();
      if (value) buf += dec.decode(value, { stream: true });
      buf = buf.replace(/\r\n?/g, '\n');
      let i;
      while ((i = buf.indexOf('\n\n')) !== -1) { const frame = buf.slice(0, i); buf = buf.slice(i + 2); handle(frame); }
      if (end) break;
    }
    if (buf.trim()) handle(buf);
    if (!done) throw new Error('The stream ended early');
    return done;
  }
  /** Markdown for a half-written answer: hold back an unclosed trailing $…$ / $$…$$ so KaTeX only
   *  ever sees balanced math (the open segment shows as plain text until it closes). */
  function partialMD(src) {
    const s = String(src || '');
    let i = 0, open = -1, openLen = 0;
    while (i < s.length) {
      const ch = s[i];
      if (ch === '\\') { i += 2; continue; }
      if (ch === '`') { // skip code spans/fences: $ inside code isn't math
        const m = /^`+/.exec(s.slice(i))[0];
        const close = s.indexOf(m, i + m.length);
        if (close === -1) break;
        i = close + m.length; continue;
      }
      if (ch === '$') {
        const dbl = s[i + 1] === '$';
        if (open === -1) { open = i; openLen = dbl ? 2 : 1; i += openLen; continue; }
        if (openLen === 2) { if (dbl) { open = -1; i += 2; } else i += 1; continue; } // lone $ inside $$…$$
        open = -1; i += 1; continue; // closes inline $…$
      }
      if (ch === '\n' && open !== -1 && openLen === 1 && s[i + 1] === '\n') open = -1; // inline math can't span paragraphs
      i++;
    }
    if (open === -1) return renderMD(s);
    return renderMD(s.slice(0, open)) + `<p class="stream-tail">${esc(s.slice(open))}</p>`;
  }
  /** Re-render a streaming answer at most every ~80 ms. Returns {push(text), flush()}. */
  function streamRenderer(paint) {
    let last = 0, t = null, latest = '';
    const run = () => { t = null; last = performance.now(); paint(latest); };
    return {
      push(text) {
        latest = text;
        if (t) return;
        const wait = Math.max(0, 80 - (performance.now() - last));
        t = setTimeout(run, wait);
      },
      flush() { if (t) { clearTimeout(t); run(); } },
      cancel() { if (t) clearTimeout(t); t = null; },
    };
  }
  /** multipart upload with progress (XHR) */
  function upload(path, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', path);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.upload.onload = () => onProgress && onProgress(1);
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch { data = { detail: (xhr.responseText || '').slice(0, 400) }; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(errText(data, xhr.status)));
      };
      xhr.onerror = () => reject(new Error('Upload failed — cannot reach the server.'));
      const fd = new FormData();
      fd.append('file', file, file.name);
      xhr.send(fd);
    });
  }

  // ======================================================================
  // Toasts, tasks, modal
  // ======================================================================
  function toast(msg, kind = 'info', opts = {}) {
    const box = $('#toasts');
    const el = h('div', { class: `toast ${kind}`, role: kind === 'error' ? 'alert' : 'status' },
      h('span', { class: 't-msg', text: msg }),
      opts.action ? h('button', { class: 'btn btn-sm', text: opts.action.label, onclick: (e) => { e.stopPropagation(); opts.action.fn(); kill(); } }) : null);
    const kill = () => { el.style.opacity = '0'; el.style.transition = 'opacity .2s'; setTimeout(() => el.remove(), 200); };
    el.addEventListener('click', kill);
    box.appendChild(el);
    while (box.children.length > 5) box.firstChild.remove();
    setTimeout(kill, opts.ms || (kind === 'error' ? 9000 : 4200));
    return el;
  }
  const toastErr = (e, prefix) => toast((prefix ? prefix + '. ' : '') + (e && e.message ? e.message : String(e)), 'error');

  function task(name) {
    const prog = h('i', { style: { width: '0%' } });
    const state = h('span', { class: 't-state', text: 'Uploading' });
    const el = h('div', { class: 'task' }, h('span', { class: 'spin' }), h('span', { class: 't-name', text: name }), h('span', { class: 'prog' }, prog), state);
    $('#tasks').appendChild(el);
    return {
      progress(p) { prog.style.width = Math.round(p * 100) + '%'; state.textContent = p >= 1 ? 'Processing' : Math.round(p * 100) + '%'; },
      label(t) { state.textContent = t; },
      done() { el.remove(); },
    };
  }

  function confirmDialog(title, text, okLabel = 'Delete') {
    return new Promise((resolve) => {
      const m = $('#modal');
      $('#modal-title').textContent = title;
      $('#modal-text').textContent = text;
      const ok = $('#modal-ok'), cancel = $('#modal-cancel');
      ok.textContent = okLabel;
      m.hidden = false;
      const prevFocus = document.activeElement;
      setTimeout(() => cancel.focus(), 0);
      const done = (v) => {
        m.hidden = true; ok.onclick = cancel.onclick = m.onclick = null; m.onkeydown = null;
        S.modalClose = null;
        if (prevFocus && prevFocus.focus) prevFocus.focus();
        resolve(v);
      };
      ok.onclick = () => done(true);
      cancel.onclick = () => done(false);
      m.onclick = (e) => { if (e.target === m) done(false); };
      S.modalClose = () => done(false);
    });
  }

  // elapsed-seconds ticker for long operations
  setInterval(() => {
    const now = Date.now();
    for (const el of $$('.elapsed[data-start]')) el.textContent = Math.floor((now - Number(el.dataset.start)) / 1000) + 's';
  }, 500);
  function working(label, start = Date.now()) {
    return h('div', { class: 'working' }, h('span', { class: 'spin' }), h('span', { text: label }),
      h('span', { class: 'elapsed', dataset: { start: String(start) }, text: Math.floor((Date.now() - start) / 1000) + 's' }));
  }

  // ======================================================================
  // Markdown + math rendering
  // ======================================================================
  function safeUrl(href) {
    if (!href) return null;
    const s = String(href).trim();
    // judge the scheme as the browser will: it drops tabs/newlines/control chars first ("java\tscript:")
    const probe = s.replace(/[\u0000-\u0020\u007f-\u009f]/g, '');
    if (/^(https?:|mailto:)/i.test(probe)) return s;
    if (/^[a-z][a-z0-9+.-]*:/i.test(probe)) return null; // javascript:, data:, vbscript:, file:, ...
    return s; // relative
  }
  // marked may hand us already-escaped hrefs/titles; normalise to avoid double escaping
  const unesc = (s) => String(s ?? '').replace(/&(amp|lt|gt|quot|#39);/g, (m, k) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[k]));

  let md = null;
  if (window.marked && window.marked.Marked) {
    md = new window.marked.Marked({ gfm: true, breaks: false });
    md.use({
      renderer: {
        html(token) { return esc(typeof token === 'string' ? token : (token && (token.raw || token.text)) || ''); },
        link(href, title, text) {
          if (href && typeof href === 'object') { title = href.title; text = href.text; href = href.href; }
          const u = safeUrl(unesc(href));
          if (!u) return text;
          return `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer"${title ? ` title="${esc(unesc(title))}"` : ''}>${text}</a>`;
        },
        image(href, title, text) {
          if (href && typeof href === 'object') { title = href.title; text = href.text; href = href.href; }
          const u = safeUrl(unesc(href));
          if (!u) return esc(text || '');
          return `<img src="${esc(u)}" alt="${esc(unesc(text || ''))}" style="max-width:100%"${title ? ` title="${esc(unesc(title))}"` : ''}>`;
        },
      },
    });
  }

  function renderTex(tex, display) {
    if (window.katex) {
      try {
        return window.katex.renderToString(tex, { displayMode: display, throwOnError: false, errorColor: 'var(--red)', strict: 'ignore', trust: false, output: 'htmlAndMathml' });
      } catch (e) { /* fall through */ }
    }
    return `<code class="math-err">${esc(display ? `$$${tex}$$` : `$${tex}$`)}</code>`;
  }

  /** [[target]] -> note: an id, else what the open note's server-side `mentions` say, else an exact
   *  (case-insensitive) title in S.nodes, preferring non-fragments, like the server does. */
  const wkey = (s) => String(s || '').trim().replace(/\s+/g, ' ').toLowerCase();
  function resolveWiki(target) {
    const t = String(target || '').trim(), k = wkey(t);
    if (S.nodes.has(t)) return S.nodes.get(t);
    const m = S.cur && Array.isArray(S.cur.mentions) && S.cur.mentions.find((x) => x.id === t || wkey(x.title) === k);
    if (m) return S.nodes.get(m.id) || m;
    let best = null;
    for (const x of S.nodes.values()) if (wkey(x.title) === k && (!best || (best.type === 'fragment' && x.type !== 'fragment'))) best = x;
    return best;
  }
  /** Chip for [[id]], [[Title]] or [[target|alias]]. With `put`, returns placeholder tokens; an alias
   *  holding math placeholders ($…$) stays outside them so the renderer can restore the math. */
  /** [[id]], optionally followed by a page: "[[id]] p. 9", "[[id]], p. 9" or "[[id]] (p. 9)", or by a section of a
   *  linked article (link + text, no PDF): "[[id]] §3.2", "[[id]] (§3.2)", "[[id]] §"Results"". */
  const SEC_PAT = String.raw`(\d{1,3}(?:\.\d{1,3})*|[A-Z](?:\.\d{1,3})*(?![\w])|[IVX]{1,6}(?![\w])|"[^"\n]{1,60}"|“[^”\n]{1,60}”)`;
  const CITE_RE = new RegExp(String.raw`\[\[([^\[\]\n|]+?)(?:\|([^\[\]\n]*))?\]\](?:,?[  ](?:pp?\.[  ]?(\d{1,4})|\(pp?\.[  ]?(\d{1,4})\)|§[  ]?` + SEC_PAT + String.raw`|\(§[  ]?` + SEC_PAT + String.raw`\)))?`, 'g');
  const citeSub = (s, put) => s.replace(CITE_RE, (m, id, alias, p1, p2, s1, s2) => citeHtml(id.trim(), alias, put, p1 || p2, (s1 || s2 || '').replace(/^["“]|["”]$/g, '')));
  /** linked articles (link + text, no PDF file): a small link glyph, and citations that open the article */
  const LINK_SVG = '<svg class="ico-link" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M6.6 9.4a2.6 2.6 0 0 0 3.7 0l2.4-2.4a2.6 2.6 0 0 0-3.7-3.7l-.9.9M9.4 6.6a2.6 2.6 0 0 0-3.7 0L3.3 9a2.6 2.6 0 0 0 3.7 3.7l.9-.9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
  const httpUrl = (u) => (/^https?:\/\//i.test(String(u || '')) ? String(u) : '');
  /** passages chat/research read carry the exact article link of their section (server: passage_links) */
  function rememberSections(ps) {
    S.secHints = S.secHints || new Map();
    for (const p of ps || []) if (p && p.section && p.linked && httpUrl(p.url) && !S.secHints.has(`${p.id}§${p.section}`)) S.secHints.set(`${p.id}§${p.section}`, p.url);
  }
  function linkedHref(root, page, sec) {
    const base = httpUrl(root.url).split('#')[0];
    if (!base) return '';
    if (page) return `${base}#page=${encodeURIComponent(page)}`;
    const hint = S.secHints && S.secHints.get(`${root.id}§${sec}`);
    if (hint) return hint;
    if (sec && !/^[A-Z0-9.]+$/.test(sec)) return `${base}#:~:text=${encodeURIComponent(sec).replace(/-/g, '%2D')}`;
    return base;
  }
  function citeHtml(id, alias, put, page, sec) {
    const n = resolveWiki(id);
    alias = String(alias || '').trim();
    // a page or section citation into a linked article (no PDF file) opens the article on its own site
    const root = n && (page || sec) && (n.type === 'fragment' ? S.nodes.get(n.parent) : n);
    if (root && !root.has_pdf && root.source === 'linked' && httpUrl(root.url)) {
      const where = page ? `p.&nbsp;${esc(page)}` : `§${esc(truncate(sec, 24))}`;
      const lbl = `${esc(truncate(alias || n.title, 48))}<span class="cite-page">${where}</span>`;
      const a = `<a class="cite linked" href="${esc(linkedHref(root, page, sec))}" target="_blank" rel="noopener noreferrer" title="${esc(`${root.title}, ${page ? 'page ' + page : '§' + sec} (opens the article)`)}">`;
      if (put && /(LEPH|KXPH)\d+X[LK]/.test(alias)) return put(a) + alias + put(`<span class="cite-page">${where}</span></a>`);
      return put ? put(a + lbl + '</a>') : a + lbl + '</a>';
    }
    if (n && sec && !page) alias = `${alias || n.title}, §${sec}`;
    // a page citation into a paper with a PDF opens the viewer at that page (fragments: their parent's PDF)
    const pdfNode = n && page && (n.type === 'fragment' ? S.nodes.get(n.parent) : n);
    if (pdfNode && pdfNode.has_pdf) {
      const lbl = `${esc(truncate(alias || n.title, 48))}<span class="cite-page">p.&nbsp;${esc(page)}</span>`;
      const a = `<a class="cite" href="/viewer/${encodeURIComponent(pdfNode.id)}#page=${esc(page)}" target="_blank" rel="noopener" title="${esc(`${pdfNode.title}, page ${page} (opens the PDF)`)}">`;
      if (put && /(LEPH|KXPH)\d+X[LK]/.test(alias)) return put(a) + alias + put(`<span class="cite-page">p.&nbsp;${esc(page)}</span></a>`);
      return put ? put(a + lbl + '</a>') : a + lbl + '</a>';
    }
    if (n && page) alias = `${alias || n.title}, p. ${page}`;
    const label = alias || (n ? n.title : id);
    const open = n ? `<button type="button" class="cite" data-open="${esc(n.id)}" title="${esc(n.title)}">`
      : `<button type="button" class="cite unresolved" data-create="${esc(id)}" title="${esc(`No note called “${id}” yet. Click to create it.`)}">`;
    if (put && /(LEPH|KXPH)\d+X[LK]/.test(label)) return put(open) + label + put('</button>');
    const html = open + esc(truncate(label, 48)) + '</button>';
    return put ? put(html) : html;
  }

  /**
   * Markdown + KaTeX. Code is shielded from math extraction; math and [[note-id]] citations
   * are swapped for inert placeholders before marked runs, then restored afterwards.
   */
  function renderMD(src, { inline = false } = {}) {
    if (window.LatexEditor) {
      return window.LatexEditor.render(src, {
        inline,
        transform: citeSub,
      });
    }
    src = String(src ?? '');
    if (!src.trim()) return '';
    const slots = [];
    const put = (html) => `KXPH${slots.push(html) - 1}XK`;
    const codes = [];
    const hide = (m) => `KXCODE${codes.push(m) - 1}XK`;

    let s = src.replace(/\r\n?/g, '\n');
    // 1. shield fenced + inline code
    s = s.replace(/(^|\n)([ \t]*)(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n[ \t]*\3[ \t]*(?=\n|$)|$)/g, (m, pre) => pre + hide(m.slice(pre.length)));
    s = s.replace(/(`+)(?!`)([\s\S]*?[^`])\1(?!`)/g, (m) => hide(m));
    // 2. math
    s = s.replace(/\$\$([\s\S]+?)\$\$/g, (m, t) => put(renderTex(t.trim(), true)));
    s = s.replace(/\\\[([\s\S]+?)\\\]/g, (m, t) => put(renderTex(t.trim(), true)));
    s = s.replace(/\\\(([\s\S]+?)\\\)/g, (m, t) => put(renderTex(t.trim(), false)));
    s = s.replace(/(^|[^\\$])\$(?=[^\s$])((?:\\.|[^$\\\n]|\n(?!\s*\n))+?)\$(?!\d)/g, (m, pre, t) => {
      if (/\s$/.test(t)) return m; // closing $ preceded by space: not math (e.g. "$5 and $")
      return pre + put(renderTex(t, false));
    });
    // 3. [[note-id]] citations
    s = citeSub(s, put);
    // 4. restore code
    s = s.replace(/KXCODE(\d+)XK/g, (m, i) => codes[Number(i)]);

    let out;
    if (md) {
      try { out = inline ? md.parseInline(s) : md.parse(s); }
      catch (e) { out = esc(s).replace(/\n/g, '<br>'); }
    } else {
      out = esc(s).replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>');
      if (!inline) out = '<p>' + out + '</p>';
    }
    out = out.replace(/<p>\s*(KXPH\d+XK)\s*<\/p>/g, '$1'); // block-level display math
    out = out.replace(/KXPH(\d+)XK/g, (m, i) => slots[Number(i)] ?? '');
    return out;
  }
  function mdEl(src, cls = 'md', opts) { return h('div', { class: cls, html: renderMD(src, opts) }); }
  /** Attach the shared LaTeX helper (autocomplete, Cmd/Ctrl+M, symbols, live preview) if loaded. */
  function latex(ta, preview, mode = 'markdown') {
    try { if (window.Wikilinks) window.Wikilinks.attach(ta, { nodes: () => S.nodes.values(), self: () => (S.cur && ta.closest('#panel') ? S.cur.id : null) }); } catch (e) { console.warn('Wikilinks.attach failed', e); }
    if (!window.LatexEditor) return null;
    try { return window.LatexEditor.attach(ta, { preview, mode, render: (src) => renderMD(src) }); }
    catch (e) { console.warn('LatexEditor.attach failed', e); return null; }
  }

  // ======================================================================
  // Status / Claude availability
  // ======================================================================
  async function loadStatus() {
    const el = $('#status');
    try {
      const st = await api('GET', '/api/status');
      S.status = st; S.online = true;
      const c = st.claude || {};
      S.claudeOK = !!c.available;
      const agentName = (st.agent && st.agent.name) || 'Claude';
      S.agentName = agentName;
      const SHORT = { claude: 'Claude', codex: 'Codex', gemini: 'Gemini', copilot: 'Copilot', opencode: 'OpenCode', cursor: 'Cursor', qwen: 'Qwen', ollama: 'Your local model', 'openai-local': 'Your local model' };
      window.RB_AI = (st.agent && (SHORT[st.agent.id] || st.agent.name)) || 'Claude';
      const rn = document.querySelector('.rs-notice');
      if (rn && window.__rbResearch) rn.textContent = window.__rbResearch.NOTICE;
      S.claudeWhy = c.available ? '' : (c.detail || 'No AI agent found on this computer.');
      el.className = 'status ' + (S.claudeOK ? 'ok' : 'bad');
      // just the agent (and model) with a green or red dot; the dot carries ready/unavailable
      const stText = $('.status-text', el);
      stText.textContent = window.RB_AI === 'Your local model' ? agentName : window.RB_AI;
      const mdl = st.agent && st.agent.model;
      if (mdl) stText.append(h('span', { class: 'st-model', text: mdl }));
      el.setAttribute('aria-label', `${agentName}${mdl ? ' ' + mdl : ''}, ${S.claudeOK ? 'ready' : 'unavailable'}. Click to switch agent or model.`);
      const emb = st.embedder || '';
      el.title = [
        S.claudeOK ? `${agentName} available${c.path ? ' — ' + c.path : ''}${c.detail ? '\n' + c.detail : ''}` : `${agentName} unavailable — ${S.claudeWhy}`,
        `Embedder: ${emb || 'unknown'}`,
        `Notes: ${st.notes ?? '?'}`,
        st.vault ? `Vault: ${st.vault}` : '',
      ].filter(Boolean).join('\n');
      if (emb !== S.embedder) {
        S.embedder = emb;
        const def = emb.startsWith('sentence-transformers') ? 0.45 : 0.15;
        const saved = LS.get('threshold.' + (emb.split(':')[0] || 'x'), null);
        setThreshold(typeof saved === 'number' ? saved : def, false);
      }
    } catch (e) {
      S.online = false; S.claudeOK = false; S.claudeWhy = "The research_brain server isn't responding. Start it with ./run.sh, then reload.";
      el.className = 'status down';
      $('.status-text', el).textContent = 'Server offline';
      el.title = 'Cannot reach the research_brain server.\n' + e.message;
    }
    applyClaude();
  }

  /** Disable/enable every Claude-dependent control, keeping a hover tooltip explaining why. */
  function applyClaude(root = document) {
    for (const b of $$('.needs-claude', root)) {
      const extraOff = b.dataset.off === '1'; // a local reason (e.g. empty summary)
      const off = !S.claudeOK || extraOff || b.classList.contains('busy');
      b.classList.toggle('is-disabled', off);
      b.setAttribute('aria-disabled', off ? 'true' : 'false');
      if (!b.dataset.title0) b.dataset.title0 = b.getAttribute('title') || '';
      let t = b.dataset.title0;
      if (!S.claudeOK) t = `${ai()} isn't available. ${S.claudeWhy}`;
      else if (extraOff && b.dataset.offWhy) t = b.dataset.offWhy;
      b.title = t;
    }
  }
  function claudeBlocked(btn) {
    if (!S.claudeOK) { toast(`${ai()} isn't available. ${S.claudeWhy}`, 'error'); return true; }
    if (btn && (btn.classList.contains('busy') || btn.dataset.off === '1')) {
      if (btn.dataset.offWhy && btn.dataset.off === '1') toast(btn.dataset.offWhy, 'info');
      return true;
    }
    return false;
  }

  // ======================================================================
  // Graph
  // ======================================================================
  const graphEl = $('#graph');
  let fg = null;

  const linkKey = (a, b) => (a < b ? a + '\u0001' + b : b + '\u0001' + a);
  const lid = (x) => (x && typeof x === 'object' ? x.id : x);

  /** Size = how much this note matters to YOU: words you've written about it (summary, notes,
   *  fragment notes), how often you've opened it, and — a little — how connected it is. A paper you
   *  filed and never touched stays small; the one you've annotated and keep returning to grows. */
  function nodeRadius(n) {
    if (n.type === 'fragment') return 1.1;
    const words = Number(n.mass) || 0, opens = Number(n.opens) || 0, d = Number(n.degree) || 0;
    const parts = Number(n.parts) || 0; // notes and highlights folded into this document
    const weight = Math.log2(1 + words / 40) + 0.6 * Math.log2(1 + opens) + 0.25 * Math.sqrt(d) + 0.35 * Math.sqrt(parts);
    return Math.min(7, 1.5 + 1.05 * weight);
  }
  const highDegree = (n) => n.type !== 'fragment' && (Number(n.degree) || 0) >= Math.max(3, S.degreeCut || 0);

  // ---------- focus / fade ----------
  /** neighbours of the selected node (manual, part, and visible similar links) */
  function focusSet() {
    if (!S.selected || !S.nodes.has(S.selected)) return null;
    const fc = S.focusCache;
    if (fc && fc.id === S.selected && fc.links === S.links && fc.thr === S.threshold && fc.sim === S.showSim) return fc.set;
    const set = new Set([S.selected]);
    for (const l of S.links) {
      if (!linkShown(l)) continue;
      const a = lid(l.source), b = lid(l.target);
      if (a === S.selected) set.add(b); else if (b === S.selected) set.add(a);
    }
    S.focusCache = { id: S.selected, links: S.links, thr: S.threshold, sim: S.showSim, set };
    return set;
  }
  const DIM = 0.2;
  function targetAlpha(n) {
    if (S.nodeFilter && !S.nodeFilter(n)) return DIM; // graph view filter (static/views.js)
    if (S.search) return S.matches.has(n.id) ? 1 : DIM;
    if (S.focusCluster != null && n.cluster !== S.focusCluster && S.selected !== n.id) return DIM;
    const f = focusSet();
    return f && !f.has(n.id) ? DIM : 1;
  }
  /** eased per-node alpha (150 ms), advanced once per frame in onRenderFramePre */
  let lastFrame = 0;
  function stepAlphas() {
    const now = performance.now();
    const dt = lastFrame ? Math.min(100, now - lastFrame) : 16;
    lastFrame = now;
    const rm = reducedMotion();
    const kf = rm ? 1 : 1 - Math.exp(-dt / 50); // ~95% in 150 ms
    let moving = false;
    for (const n of S.nodes.values()) {
      const t = targetAlpha(n);
      if (n.__a === undefined) n.__a = t;
      const d = t - n.__a;
      if (Math.abs(d) > 0.004) { n.__a += d * kf; moving = true; } else n.__a = t;
    }
    return moving;
  }
  // S.hidden: ids hidden outright by a neighbourhood focus (static/views.js); their links go too
  const nodeHidden = (id) => !!(S.hidden && S.hidden.has(id));
  const linkShown = (l) => !(S.hidden && (nodeHidden(lid(l.source)) || nodeHidden(lid(l.target))))
    && (l.kind !== 'similar' || (S.showSim && l.top && l.score >= S.threshold))
    && (l.kind !== 'cites' || S.showCites);

  // ---------- redraw control: only animate while something is moving ----------
  let animUntil = 0;
  function kick(ms = 400) {
    animUntil = Math.max(animUntil, performance.now() + ms);
    if (fg) fg.autoPauseRedraw(false);
  }

  // ---------- signal pulses ("action potential") ----------
  const pulses = [];
  function firePulse(id) {
    if (!fg || reducedMotion() || !S.nodes.has(id)) return;
    pulses.length = 0;
    const now = performance.now();
    const first = new Set([id]);
    const hop1 = [];
    for (const l of S.links) {
      if (!linkShown(l) || l.kind === 'part') continue;
      const a = lid(l.source), b = lid(l.target);
      if (a !== id && b !== id) continue;
      const to = a === id ? b : a;
      hop1.push(to); first.add(to);
      pulses.push({ l, rev: a !== id, t0: now, dur: 700, alpha: 1 });
    }
    // one more hop, quieter
    let n2 = 0;
    for (const l of S.links) {
      if (n2 > 60) break;
      if (!linkShown(l) || l.kind === 'part') continue;
      const a = lid(l.source), b = lid(l.target);
      const fromA = hop1.includes(a) && !first.has(b), fromB = hop1.includes(b) && !first.has(a);
      if (!fromA && !fromB) continue;
      pulses.push({ l, rev: !fromA, t0: now + 560, dur: 700, alpha: 0.45 }); n2++;
    }
    if (pulses.length) kick(1400);
  }

  // ---------- ambient activity: occasional quiet signals along random fibres ----------
  S.ambient = LS.get('ambient', true);
  setInterval(() => {
    if (!fg || !S.ambient || reducedMotion() || document.visibilityState !== 'visible') return;
    if (pulses.length > 6) return;
    const pool = S.links.filter((l) => l.kind !== 'part' && linkShown(l));
    if (pool.length < 3) return;
    const burst = 1 + (Math.random() < 0.35 ? 1 : 0);
    const now = performance.now();
    for (let i = 0; i < burst; i++) {
      const l = pool[Math.floor(Math.random() * pool.length)];
      pulses.push({ l, rev: Math.random() < 0.5, t0: now + i * 220, dur: 1500 + Math.random() * 700, alpha: 0.38 });
    }
    kick(2600);
  }, 1300);

  // ---------- geometry ----------
  /** quadratic Bézier point; control point comes from force-graph's own curvature calc */
  /** Fibre-tract geometry: links inside a region bow toward the region's centre, links between
   *  regions sweep through the middle of the brain, so connections bundle like white-matter tracts. */
  function linkGeom(l) {
    // Circuit traces: every link is routed octilinearly (straight runs joined by one 45-degree
    // diagonal), like a trace on a circuit board. Where the straight run splits varies per link so
    // parallel traces separate instead of sitting on top of each other.
    const s = l.source, t = l.target;
    if (!s || !t || s.x === undefined || t.x === undefined) return null;
    const k = S.k || 1;
    const sd = l.sPart ? partDot(s, l.sPart, k) : null, td = l.tPart ? partDot(t, l.tPart, k) : null;
    // a link that belongs to an attached note leaves from that note's red dot: first a short stub
    // pointing away from the paper (snapped to the nearest 45 degrees), then the usual trace, so it
    // never runs back across the paper's dot
    const stub = (d, host) => {
      if (!d) return null;
      const ang = Math.round(Math.atan2(d.y - host.y, d.x - host.x) / (Math.PI / 4)) * (Math.PI / 4), L = 7 / k;
      return { x: d.x + Math.cos(ang) * L, y: d.y + Math.sin(ang) * L };
    };
    const ss = stub(sd, s), ts = stub(td, t);
    const x0 = ss ? ss.x : s.x, y0 = ss ? ss.y : s.y, x1 = ts ? ts.x : t.x, y1 = ts ? ts.y : t.y;
    const dx = x1 - x0, dy = y1 - y0, ax = Math.abs(dx), ay = Math.abs(dy);
    const sx = Math.sign(dx) || 1, sy = Math.sign(dy) || 1;
    if (l.__split === undefined) l.__split = 0.3 + 0.4 * ((strHash(l.kind + linkKey(lid(s), lid(t))) % 1000) / 1000);
    const f = l.__split;
    let pts;
    if (ax >= ay) {
      const run = ax - ay, a = x0 + sx * run * f;
      pts = [x0, y0, a, y0, a + sx * ay, y1, x1, y1];
    } else {
      const run = ay - ax, a = y0 + sy * run * f;
      pts = [x0, y0, x0, a, x1, a + sy * ax, x1, y1];
    }
    if (sd) pts.unshift(sd.x, sd.y);
    if (td) pts.push(td.x, td.y);
    const seg = [];
    let total = 0;
    for (let i = 0; i + 3 < pts.length; i += 2) { const d = Math.hypot(pts[i + 2] - pts[i], pts[i + 3] - pts[i + 1]); seg.push(d); total += d; }
    return { x0: pts[0], y0: pts[1], x1: pts[pts.length - 2], y1: pts[pts.length - 1], pts, seg, total: total || 1, sd, td };
  }
  function updateCentroids() {
    const sums = new Map(); let ax = 0, ay = 0, an = 0;
    for (const n of S.nodes.values()) {
      if (n.x === undefined || n.type === 'fragment') continue;
      ax += n.x; ay += n.y; an++;
      if (n.cluster === undefined || n.cluster < 0) continue;
      const v = sums.get(n.cluster) || { x: 0, y: 0, n: 0 };
      v.x += n.x; v.y += n.y; v.n++; sums.set(n.cluster, v);
    }
    const cen = new Map();
    for (const [cid, v] of sums) cen.set(cid, { x: v.x / v.n, y: v.y / v.n });
    cen.all = an ? { x: ax / an, y: ay / an } : null;
    S.centroids = cen;
  }
  /** point and direction at fraction t along a trace */
  const bz = (g, t) => {
    let want = Math.max(0, Math.min(1, t)) * g.total;
    for (let i = 0; i < g.seg.length; i++) {
      const L = g.seg[i];
      if (want <= L || i === g.seg.length - 1) {
        const u = L ? Math.min(1, want / L) : 0, p = g.pts;
        return { x: p[2 * i] + (p[2 * i + 2] - p[2 * i]) * u, y: p[2 * i + 1] + (p[2 * i + 3] - p[2 * i + 1]) * u };
      }
      want -= L;
    }
    return { x: g.x1, y: g.y1 };
  };
  const bzD = (g, t) => {
    let want = Math.max(0, Math.min(1, t)) * g.total;
    for (let i = 0; i < g.seg.length; i++) {
      if (want <= g.seg[i] || i === g.seg.length - 1) { const p = g.pts; return { x: p[2 * i + 2] - p[2 * i], y: p[2 * i + 3] - p[2 * i + 1] }; }
      want -= g.seg[i];
    }
    return { x: g.x1 - g.x0, y: g.y1 - g.y0 };
  };
  /** trace path, trimmed by r0/r1 at the ends so it meets the soma's edge */
  function tracePath(ctx, g, r0, r1) {
    const p = g.pts.slice();
    const trim = (ia, ib, r) => {
      const dx = p[ib] - p[ia], dy = p[ib + 1] - p[ia + 1], d = Math.hypot(dx, dy);
      if (d > r) { p[ia] += (dx / d) * r; p[ia + 1] += (dy / d) * r; }
    };
    trim(0, 2, r0); trim(p.length - 2, p.length - 4, r1);
    ctx.moveTo(p[0], p[1]);
    for (let i = 2; i < p.length; i += 2) ctx.lineTo(p[i], p[i + 1]);
  }

  function strHash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function linkCurv(l) {
    if (l.__curv === undefined) {
      const h = strHash(l.kind + linkKey(lid(l.source), lid(l.target)));
      const mag = l.kind === 'part' ? 0.18 : [0.05, 0.09, 0.13][h % 3];
      l.__curv = (h & 8 ? 1 : -1) * mag;
    }
    return l.__curv;
  }

  function linkStyle(l) {
    const a = l.source, b = l.target;
    const fa = a.__a ?? 1, fb = b.__a ?? 1;
    const touchSel = S.selected && (a.id === S.selected || b.id === S.selected);
    let dim = touchSel ? 1 : Math.min(fa, fb);
    if (S.search) dim = Math.min(fa, fb);
    const hov = S.hoverLink === l;
    // long fibres recede a little, like depth in tissue
    const len = Math.hypot(b.x - a.x, b.y - a.y) || 0;
    const depth = Math.max(0.45, Math.min(1, 1.25 - len / 320));
    if (l.kind === 'part') return { col: C.ink, a: 0.45 * dim, w: 0.5 };
    if (l.kind === 'manual') return { col: C.red, a: (hov ? 1 : 0.8) * dim, w: hov ? 1.1 : 0.8 };
    if (l.kind === 'mention') return { col: C.red, a: (hov ? 0.9 : 0.45) * dim * depth, w: hov ? 0.9 : 0.6 };
    if (l.kind === 'cites') return { col: C.ink, a: (hov ? 0.9 : 0.4) * dim * depth, w: hov ? 0.8 : 0.5 };
    if (l.kind === 'question') return { col: C.violet, a: (hov ? 1 : l.verdict === 'related' ? 0.4 : 0.7) * dim, w: hov ? 1 : 0.7 };
    if (l.kind === 'conflict') return { col: C.ochre, a: (hov ? 1 : 0.85) * dim, w: hov ? 1.1 : 0.8, dash: true };
    const span = Math.max(1e-3, 1 - S.threshold);
    const sa = 0.22 + 0.4 * Math.min(1, Math.max(0, (l.score - S.threshold) / span));
    return { col: C.machine, a: (hov ? 0.95 : sa) * dim * depth, w: hov ? 0.8 : 0.5 };
  }

  /** Hairline fibres: a single fine stroke along the curve. Width is fixed in screen pixels so
   *  zooming in reveals structure instead of thickening lines. */
  function drawLink(l, ctx, k) {
    const g = linkGeom(l);
    if (!g) return;
    const st = linkStyle(l);
    if (st.a <= 0.01) return;
    // traces end exactly on the edge of the drawn dot (same radius drawNode uses at this zoom), so
    // they meet it flush and on its centre line; the red note-dots are drawn on top of them
    const r0 = g.sd ? g.sd.r : drawnRadius(l.source, k), r1 = g.td ? g.td.r : drawnRadius(l.target, k);
    if (Math.hypot(g.x1 - g.x0, g.y1 - g.y0) <= r0 + r1) return;
    ctx.beginPath();
    tracePath(ctx, g, r0, r1);
    ctx.lineJoin = 'miter';
    ctx.strokeStyle = rgba(st.col, st.a);
    ctx.lineWidth = st.w / k;
    // disagreements between papers are dashed, so they read as a warning rather than a connection
    if (st.dash) ctx.setLineDash([3 / k, 2.5 / k]);
    ctx.stroke();
    if (st.dash) ctx.setLineDash([]);
    // tiny vias where the trace bends
    if (k > 0.9 && l.kind !== 'part') {
      const vr = 0.7 / k;
      ctx.fillStyle = rgba(st.col, Math.min(1, st.a * 1.3));
      for (let i = 2; i < g.pts.length - 2; i += 2) ctx.fillRect(g.pts[i] - vr, g.pts[i + 1] - vr, 2 * vr, 2 * vr);
    }
    if (l.kind === 'cites') drawCiteArrow(l, g, ctx, k, st);
  }
  /** citations are directed: a small arrowhead just outside the cited paper's soma */
  function drawCiteArrow(l, g, ctx, k, st) {
    const chord = g.total;
    const r = nodeRadius(l.target) * (k > 1.5 ? Math.sqrt(1.5 / k) : 1) + 1.5 / k;
    if (chord < r * 3) return;
    const t = Math.max(0.5, 1 - r / chord);
    const p = bz(g, t), d = bzD(g, t), dl = Math.hypot(d.x, d.y) || 1;
    const ux = d.x / dl, uy = d.y / dl, size = 3.6 / k, half = 1.5 / k;
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(p.x - ux * size - uy * half, p.y - uy * size + ux * half);
    ctx.lineTo(p.x - ux * size + uy * half, p.y - uy * size - ux * half);
    ctx.closePath();
    ctx.fillStyle = rgba(st.col, Math.min(1, st.a * 1.4));
    ctx.fill();
  }

  // ---------- regions ----------
  // No outlines or fills: the layout's grouping shows the structure; each region only gets its name,
  // placed just above its members.
  function drawRegions(ctx, k) {
    S.regionLabels = [];
    if (!S.clusters || !S.clusters.length) return;
    const groups = new Map();
    for (const n of S.nodes.values()) {
      if (n.cluster === undefined || n.cluster < 0 || n.x === undefined || n.type === 'fragment' || nodeHidden(n.id)) continue;
      if (!groups.has(n.cluster)) groups.set(n.cluster, []);
      groups.get(n.cluster).push(n);
    }
    for (const [cid, mem] of groups) {
      if (mem.length < 2) continue;
      const info = S.clusters.find((c) => c.id === cid);
      if (!info || !info.label) continue;
      let cx = 0, minY = Infinity, maxY = -Infinity;
      for (const n of mem) { cx += n.x; minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y); }
      S.regionLabels.push({ cid, x: cx / mem.length, y: minY - 18 / Math.max(k, 0.3), cy: maxY, label: info.label, size: mem.length });
    }
    S.regionLabels.sort((a, b) => b.size - a.size);
  }
  function regionName(label) { return label ? label.charAt(0).toUpperCase() + label.slice(1) : ''; }
  /** Region names are small tags (sheet ground, hairline frame) placed above each region, so they stay
   *  readable over the traces. Hovering a tag, or any dot in the region, frames it in ink; clicking
   *  a tag focuses that region. When zoomed in past the tags, the region shows in the dot's tooltip. */
  function drawRegionLabels(ctx, k) {
    if (!S.regionLabels) return;
    const focused = S.focusCluster;
    // tags stay at every zoom (they are few, and kept inside the view), so you always know where you are
    const fade = 1;
    const hoverNode = S.hover && S.nodes.get(S.hover);
    const hot = S.hoverRegion != null ? S.hoverRegion : hoverNode ? hoverNode.cluster : null;
    ctx.save();
    ctx.textBaseline = 'middle';
    const placed = [], px = 1 / k, small = graphEl.clientWidth < 600 ? 0.82 : 1;
    const fs = 15.5 * small * px, padX = 8 * small * px, h = 24 * small * px, lh = h + 4 * px;
    // keep tags inside the visible plate
    const vtl = fg.screen2GraphCoords(0, 0), vbr = fg.screen2GraphCoords(graphEl.clientWidth, graphEl.clientHeight);
    const nameFont = `italic 500 ${fs}px "Bodoni Moda", Didot, Georgia, serif`;
    const numFont = `500 ${10.5 * small * px}px "Archivo", -apple-system, "Segoe UI", sans-serif`;
    for (const r of S.regionLabels) {
      r.box = null;
      if (focused != null) continue; // the focus bar names the region; no tag on top of it
      const text = regionName(r.label), num = String(r.size);
      ctx.font = nameFont; const wName = ctx.measureText(text).width;
      ctx.font = numFont; const wNum = ctx.measureText(num).width;
      const gap = 7 * px, w = padX + wName + gap + wNum + padX;
      // only regions that are on screen get a tag; a tag above the view slides down to its top edge
      if (r.x < vtl.x || r.x > vbr.x || r.y > vbr.y || (r.cy !== undefined && r.cy < vtl.y)) continue;
      const margin = 8 * px, x0 = Math.max(vtl.x + margin, Math.min(r.x - w / 2, vbr.x - margin - w));
      if (r.y - h / 2 < vtl.y + margin) r.y = vtl.y + margin + h / 2;
      let y = r.y, ok = false;
      for (let tries = 0; tries < 6 && !ok; tries++) {
        ok = !placed.some((p) => Math.abs(p.x - r.x) < (p.w + w) / 2 + 8 * px && Math.abs(p.y - y) < lh);
        if (!ok) y -= lh;
      }
      if (!ok) continue;
      placed.push({ x: r.x, y, w });
      r.y = y;
      const isHot = hot != null && r.cid === hot, isFocus = focused != null && r.cid === focused;
      const dimmed = !isHot && !isFocus && (S.selected || S.search);
      const a = fade * (dimmed ? 0.5 : 1);
      r.box = [x0, y - h / 2, x0 + w, y + h / 2];
      ctx.globalAlpha = a;
      ctx.fillStyle = C.sheet;
      ctx.fillRect(x0, y - h / 2, w, h);
      ctx.lineWidth = (isHot || isFocus ? 1 : 0.6) * px;
      ctx.strokeStyle = isHot || isFocus ? C.ink : rgba(C.ink3, 0.7);
      ctx.strokeRect(x0, y - h / 2, w, h);
      if (isFocus) { ctx.fillStyle = C.red; ctx.fillRect(x0, y + h / 2 - 1.5 * px, w, 1.5 * px); }
      ctx.textAlign = 'left';
      ctx.font = nameFont; ctx.fillStyle = C.ink; ctx.fillText(text, x0 + padX, y + 0.5 * px);
      ctx.font = numFont; ctx.fillStyle = C.ink3; ctx.fillText(num, x0 + padX + wName + gap, y + 0.5 * px);
    }
    ctx.restore();
  }
  function labelHit(e) {
    if (!fg || !e) return null;
    const rect = graphEl.getBoundingClientRect();
    const p = fg.screen2GraphCoords(e.clientX - rect.left, e.clientY - rect.top);
    return regionAt(p.x, p.y);
  }
  function regionAt(gx, gy) {
    for (const r of S.regionLabels || []) {
      const b = r.box;
      if (b && gx >= b[0] && gx <= b[2] && gy >= b[1] && gy <= b[3]) return r;
    }
    return null;
  }

  /** Focus one region: zoom to it and dim everything else, until "Show all" or Esc. */
  function focusRegion(cid) {
    if (!fg) return;
    const info = (S.clusters || []).find((c) => c.id === cid);
    if (!info) return;
    S.focusCluster = cid;
    const bar = $('#region-focus');
    if (bar) { $('.rf-name', bar).textContent = regionName(info.label); bar.hidden = false; }
    fg.zoomToFit(T(600), window.innerWidth < 700 ? 40 : 90, (n) => n.cluster === cid);
    kick(800);
  }
  function unfocusRegion(refit = true) {
    if (S.focusCluster == null) return;
    S.focusCluster = null;
    const bar = $('#region-focus');
    if (bar) bar.hidden = true;
    if (refit && fg) fg.zoomToFit(T(600), 70);
    kick(800);
  }
  function chooseRegion() {
    const regs = (S.regionLabels || []).filter((r) => r.label);
    if (!regs.length) { toast('No regions yet. They appear once a few papers are related.', 'info'); return; }
    const list = h('div', { class: 'rf-list', role: 'listbox', 'aria-label': 'Regions' },
      regs.map((r) => h('button', { type: 'button', class: 'rf-item', role: 'option',
        onclick: () => { closeChooser(); focusRegion(r.cid); } },
        h('span', { class: 'rf-item-name', text: regionName(r.label) }), h('span', { class: 'rf-item-n', text: String(r.size) }))));
    const sheet = h('div', { class: 'rf-chooser' }, h('div', { class: 'rf-head', text: 'Focus a region' }), list);
    const closeChooser = () => { sheet.remove(); S.regionChooserClose = null; };
    S.regionChooserClose = closeChooser;
    document.body.append(sheet);
    const first = $('.rf-item', sheet); if (first) first.focus();
    setTimeout(() => document.addEventListener('pointerdown', function off(e) {
      if (!sheet.contains(e.target)) { closeChooser(); document.removeEventListener('pointerdown', off); }
    }), 0);
  }

  // ---------- forces ----------
  /** pull each region toward its own anchor so regions form lobes; mild centering for the rest */
  function regionForce() {
    // Two-level hierarchy, same rule at both scales: regions are laid out around the centre,
    // sub-groups are laid out around their region's anchor, and nodes settle around their sub-group.
    let nodes = [];
    let anchors = new Map(), subAnchors = new Map();
    function force(alpha) {
      for (const n of nodes) {
        if (n.type === 'fragment' || n.fx !== undefined) continue;
        const sa = n.sub >= 0 ? subAnchors.get(n.sub) : null;
        const a = sa || anchors.get(n.cluster);
        const tx = a ? a.x : 0, ty = a ? a.y : 0;
        const s = (sa ? 0.075 : a ? 0.06 : 0.03) * alpha;
        n.vx += (tx - n.x) * s; n.vy += (ty - n.y) * s;
      }
    }
    force.initialize = (ns) => { nodes = ns; layoutAnchors(); };
    const golden = (ids, R, cx, cy, rot) => {
      const out = new Map();
      ids.forEach((id, i) => {
        const ang = rot + i * 2.39996;
        const rr = ids.length === 1 ? 0 : R * (0.45 + 0.55 * Math.sqrt((i + 0.5) / ids.length));
        out.set(id, { x: cx + Math.cos(ang) * rr, y: cy + Math.sin(ang) * rr });
      });
      return out;
    };
    function layoutAnchors() {
      const top = nodes.filter((n) => n.type !== 'fragment');
      const sizes = new Map(), subSizes = new Map(), subOf = new Map();
      for (const n of top) {
        if (n.cluster >= 0) sizes.set(n.cluster, (sizes.get(n.cluster) || 0) + 1);
        if (n.sub >= 0) { subSizes.set(n.sub, (subSizes.get(n.sub) || 0) + 1); subOf.set(n.sub, n.cluster); }
      }
      const ids = [...sizes.keys()].sort((a, b) => sizes.get(b) - sizes.get(a));
      anchors = ids.length <= 1 ? new Map(ids.map((id) => [id, { x: 0, y: 0 }])) : golden(ids, 12 * Math.sqrt(top.length) + 30, 0, 0, -Math.PI / 2);
      subAnchors = new Map();
      for (const cid of ids) {
        const subs = [...subSizes.keys()].filter((s) => subOf.get(s) === cid).sort((a, b) => subSizes.get(b) - subSizes.get(a));
        if (subs.length < 2) continue;
        const c = anchors.get(cid);
        const inner = golden(subs, 5.5 * Math.sqrt(sizes.get(cid)) + 8, c.x, c.y, cid * 1.3);
        for (const [k, v] of inner) subAnchors.set(k, v);
      }
    }
    force.relayout = layoutAnchors;
    return force;
  }
  /** simple collision (avoids loading d3-force just for forceCollide) */
  function collideForce() {
    let nodes = [];
    function force() {
      const n = nodes.length;
      for (let i = 0; i < n; i++) {
        const a = nodes[i];
        const ra = nodeRadius(a) + (a.type === 'fragment' ? 1 : 3.5);
        for (let j = i + 1; j < n; j++) {
          const b = nodes[j];
          const rb = nodeRadius(b) + (b.type === 'fragment' ? 1 : 3.5);
          let dx = b.x - a.x, dy = b.y - a.y;
          const min = ra + rb;
          if (dx > min || dx < -min || dy > min || dy < -min) continue;
          let d2 = dx * dx + dy * dy;
          if (d2 >= min * min) continue;
          if (d2 === 0) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = dx * dx + dy * dy; }
          const d = Math.sqrt(d2), push = ((min - d) / d) * 0.5;
          dx *= push; dy *= push;
          b.vx += dx; b.vy += dy; a.vx -= dx; a.vy -= dy;
        }
      }
    }
    force.initialize = (ns) => { nodes = ns; };
    return force;
  }

  function initGraph() {
    if (typeof window.ForceGraph !== 'function') {
      graphEl.appendChild(h('div', { class: 'empty-state' }, h('div', { class: 'empty-card' },
        h('h1', { text: 'The graph could not load' }),
        h('p', { text: 'The force-graph library did not load from static/vendor/. Check that folder exists, then reload. Notes, search and chat still work.' }))));
      return;
    }
    fg = window.ForceGraph()(graphEl)
      .backgroundColor('rgba(0,0,0,0)')
      .nodeId('id')
      .nodeRelSize(1)
      .nodeVal((n) => { const r = nodeRadius(n); return r * r; })
      .nodeLabel((n) => {
        const meta = [];
        meta.push(`<span>${esc(n.type === 'paper' ? 'Paper' : n.type === 'fragment' ? 'Fragment' : 'Note')}</span>`);
        if (n.year) meta.push(`<span class="tnum">${esc(n.year)}</span>`);
        if (n.authors && n.authors.length) meta.push(`<span>${esc(fmtAuthors(n.authors, 2))}</span>`);
        const q = n.type === 'fragment' && S.quoteIdx.get(n.id) ? `<div class="tt-quote">${esc(truncate(S.quoteIdx.get(n.id), 140))}</div>` : '';
        const tags = (n.tags || []).length ? `<div class="tt-meta">${n.tags.map(esc).join(', ')}</div>` : '';
        const reg = n.cluster >= 0 && (S.clusters || []).find((c) => c.id === n.cluster);
        const region = reg && reg.label ? `<div class="tt-meta">Region: <i>${esc(regionName(reg.label))}</i></div>` : '';
        return `<div class="tt-title">${esc(n.title || n.id)}</div><div class="tt-meta">${meta.join('')}</div>${q}${tags}${region}`;
      })
      .nodeCanvasObject(drawNode)
      .nodePointerAreaPaint((n, color, ctx) => {
        const r = Math.max(nodeRadius(n), 4) + 2;
        ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, r + 1, 0, 2 * Math.PI); ctx.fill();
      })
      .nodeVisibility((n) => !nodeHidden(n.id))
      .linkVisibility(linkShown)
      .linkCurvature(0)
      .linkPointerAreaPaint((l, color, ctx, k) => {
        const g = linkGeom(l); if (!g) return;
        ctx.strokeStyle = color; ctx.lineWidth = 7 / (k || 1);
        ctx.beginPath(); tracePath(ctx, g, 0, 0); ctx.stroke();
      })
      .linkCanvasObjectMode(() => 'replace')
      .linkCanvasObject(drawLink)
      .linkWidth(() => 1)
      .linkHoverPrecision(6)
      .linkLabel((l) => {
        const a = lid(l.source), b = lid(l.target);
        const ta = esc(truncate(nodeTitle(a), 60)), tb = esc(truncate(nodeTitle(b), 60));
        if (l.kind === 'part') return `<div class="tt-meta">Fragment of</div><div class="tt-title">${tb}</div>`;
        const head = l.kind === 'similar' ? `Similar, score <span class="tnum">${l.score.toFixed(2)}</span>` : l.kind === 'mention' ? 'Mentioned in your writing' : l.kind === 'cites' ? 'Cites' : 'Your link';
        if (l.kind === 'cites') return `<div class="tt-title">${ta}</div><div class="tt-meta">${head}</div><div class="tt-title">${tb}</div><div class="tt-meta">Click to see how they are connected</div>`;
        return `<div class="tt-meta">${head}</div><div class="tt-title">${ta}</div><div class="tt-meta">and</div><div class="tt-title">${tb}</div><div class="tt-meta">Click to see why they are connected</div>`;
      })
      .onNodeHover((n) => { S.hover = n ? n.id : null; graphEl.style.cursor = n ? 'pointer' : ''; kick(50); })
      .onLinkHover((l) => { S.hoverLink = l || null; graphEl.style.cursor = l ? 'pointer' : ''; kick(50); })
      .onNodeClick((n, e) => { if (S.nodeClickHook && S.nodeClickHook(n, e)) return; openNote(n.id, { center: true }); })
      .onBackgroundClick((e) => {
        const r = labelHit(e);
        if (r) { if (S.focusCluster === r.cid) unfocusRegion(); else focusRegion(r.cid); return; }
        if (S.backgroundClickHook) S.backgroundClickHook(e);
      })
      .onLinkClick(onLinkClick)
      .onRenderFramePre((ctx, k) => {
        S.k = k; S.partLayout = new Map();
        S.labelBoxes = [];
        const moving = stepAlphas();
        if (moving) kick(60);
        updateCentroids();
        drawDots(ctx, k);
        drawRegions(ctx, k);
      })
      .onRenderFramePost((ctx, k) => {
        drawRegionLabels(ctx, k);
        drawPulses(ctx, k);
        if (performance.now() > animUntil && !pulses.length) fg.autoPauseRedraw(true);
      })
      .cooldownTime(9000)
      .d3AlphaDecay(0.028)
      .d3VelocityDecay(0.38)
      .minZoom(0.12).maxZoom(10);

    fg.d3Force('charge').strength((n) => (n.type === 'fragment' ? -4 : -30)).distanceMax(180);
    fg.d3Force('center', null);
    fg.d3Force('regions', regionForce());
    fg.d3Force('collide', collideForce());
    configureLinkForce();
    const ro = new ResizeObserver(() => fg.width(graphEl.clientWidth).height(graphEl.clientHeight));
    ro.observe(graphEl);
  }

  function drawPulses(ctx, k) {
    if (!pulses.length) return;
    const now = performance.now();
    ctx.save();
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      const t = (now - p.t0) / p.dur;
      if (t < 0) continue;
      if (t >= 1 || !linkShown(p.l)) { pulses.splice(i, 1); continue; }
      const g = linkGeom(p.l);
      if (!g) continue;
      const e = 1 - Math.pow(1 - t, 2.2); // ease-out, like a signal losing speed
      const col = p.l.kind === 'manual' || p.l.kind === 'mention' ? C.red : C.machine;
      const fadeOut = t > 0.8 ? (1 - t) / 0.2 : 1;
      ctx.strokeStyle = col; ctx.lineCap = 'round';
      for (let j = 0; j < 6; j++) {
        const t1 = Math.max(0, e - j * 0.018), t0 = Math.max(0, e - (j + 1) * 0.018);
        const a = bz(g, p.rev ? 1 - t0 : t0), b2 = bz(g, p.rev ? 1 - t1 : t1);
        ctx.globalAlpha = p.alpha * fadeOut * (1 - j / 6);
        ctx.lineWidth = (1.3 - j * 0.15) / k;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b2.x, b2.y); ctx.stroke();
      }
      const head = bz(g, p.rev ? 1 - e : e);
      ctx.globalAlpha = p.alpha * fadeOut; ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(head.x, head.y, 1.1 / k, 0, 2 * Math.PI); ctx.fill();
    }
    ctx.restore();
  }

  /** The field is left clear. Fine measuring ticks run along the top and left edges and move
   *  with pan and zoom, so the plate keeps a sense of scale without any pattern behind the graph. */
  function drawDots(ctx, k) {
    const W = graphEl.clientWidth, H = graphEl.clientHeight;
    const tl = fg.screen2GraphCoords(0, 0), br = fg.screen2GraphCoords(W, H);
    let step = 10;
    while (step * k < 7) step *= 2;
    while (step * k > 16) step /= 2;
    const px = 1 / k;
    ctx.save();
    ctx.strokeStyle = rgba(C.ink3, 0.55);
    ctx.lineWidth = 0.5 * px;
    ctx.beginPath();
    const x0 = Math.ceil(tl.x / step), x1 = Math.floor(br.x / step);
    for (let i = x0; i <= x1; i++) {
      const L = (i % 10 === 0 ? 9 : i % 5 === 0 ? 5 : 2.5) * px, x = i * step;
      ctx.moveTo(x, tl.y); ctx.lineTo(x, tl.y + L);
      ctx.moveTo(x, br.y); ctx.lineTo(x, br.y - L);
    }
    const y0 = Math.ceil(tl.y / step), y1 = Math.floor(br.y / step);
    for (let i = y0; i <= y1; i++) {
      const L = (i % 10 === 0 ? 9 : i % 5 === 0 ? 5 : 2.5) * px, y = i * step;
      ctx.moveTo(tl.x, y); ctx.lineTo(tl.x + L, y);
      ctx.moveTo(br.x, y); ctx.lineTo(br.x - L, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  function configureLinkForce() {
    if (!fg) return;
    S.focusCache = null;
    const lf = fg.d3Force('link');
    if (!lf) return;
    lf.distance((l) => (l.kind === 'part' ? 9 : l.kind === 'manual' ? 44 : l.kind === 'mention' ? 50 : l.kind === 'cites' ? 56 : 38 + 46 * (1 - l.score)))
      .strength((l) => {
        if (l.kind === 'part') return 1;
        if (l.kind === 'manual') return 0.3;
        if (l.kind === 'mention') return 0.2;
        if (l.kind === 'cites') return S.showCites ? 0.12 : 0;
        return linkShown(l) ? 0.03 + 0.2 * l.score : 0;
      });
    kick(200);
  }

  /** the radius a node is drawn at for zoom k: zooming in reveals structure rather than inflating dots */
  function drawnRadius(n, k) { return nodeRadius(n) * (k > 1.5 ? Math.sqrt(1.5 / k) : 1); }

  /** Where each attached note's red dot sits on its paper's ring. A note that links somewhere faces
   *  that link (snapped to 45 degrees), so its trace leaves outward instead of crossing the paper;
   *  notes without links fill the free directions. Dots keep a minimum spacing. Cached per frame. */
  function partAngles(host) {
    if (S.partLayout && S.partLayout.has(host.id)) return S.partLayout.get(host.id);
    const ids = (S.partsOf && S.partsOf.get(host.id)) || [];
    const shown = ids.slice(0, 12);
    const want = new Map();
    for (const l of S.links) {
      const mine = (l.sPart && lid(l.source) === host.id) ? l.sPart : (l.tPart && lid(l.target) === host.id) ? l.tPart : null;
      if (!mine || want.has(mine)) continue;
      const other = l.sPart === mine ? l.target : l.source;
      if (!other || other.x === undefined) continue;
      want.set(mine, Math.round(Math.atan2(other.y - host.y, other.x - host.x) / (Math.PI / 4)) * (Math.PI / 4));
    }
    const a0 = ((strHash(host.id) % 360) / 360) * Math.PI * 2;
    const items = shown.map((id, i) => ({ id, a: want.has(id) ? want.get(id) : a0 + i * 2.39996 }));
    // keep dots apart: walk around the circle pushing any dot that is too close to the previous one
    const sep = (Math.PI * 2) / Math.max(10, shown.length + 2);
    items.sort((x, y) => x.a - y.a);
    for (let pass = 0; pass < 2; pass++) for (let i = 1; i < items.length; i++) if (items[i].a - items[i - 1].a < sep) items[i].a = items[i - 1].a + sep;
    const out = new Map(items.map((x) => [x.id, x.a]));
    if (S.partLayout) S.partLayout.set(host.id, out);
    return out;
  }
  function dotRadius(r, px) { return Math.max(0.85 * px, Math.min(1.25 * px, r * 0.22)); }
  function partDot(hostNode, partId, k) {
    const a = partAngles(hostNode).get(partId);
    if (a === undefined) return null;
    const px = 1 / k, r = drawnRadius(hostNode, k), ringR = r + 2.6 * px;
    return { x: hostNode.x + Math.cos(a) * ringR, y: hostNode.y + Math.sin(a) * ringR, r: dotRadius(r, px) };
  }

  /** how many red note-dots sit around a document: attached notes/highlights, or 1 for a summary alone */
  function markCount(n) {
    if (!n || n.type === 'fragment' || n.type === 'question') return 0;
    return Math.max(Number(n.parts) || 0, (Number(n.mass) || 0) > 0 ? 1 : 0);
  }

  function drawNode(n, ctx, k) {
    // zooming in should reveal structure, not inflate the dots: grow slower than the zoom
    const r = drawnRadius(n, k);
    const sel = S.selected === n.id;
    const hov = S.hover === n.id;
    const alpha = n.__a ?? 1;
    const px = 1 / k;
    ctx.save();
    ctx.globalAlpha = alpha;
    if (n.type === 'fragment') {
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = C.red; ctx.fill();
    } else {
      if (n.type === 'paper') {
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
        ctx.fillStyle = C.ink; ctx.fill();
      } else if (n.type === 'question') {
        const d = r * 1.25;
        ctx.beginPath(); ctx.moveTo(n.x, n.y - d); ctx.lineTo(n.x + d, n.y); ctx.lineTo(n.x, n.y + d); ctx.lineTo(n.x - d, n.y); ctx.closePath();
        ctx.fillStyle = C.ground; ctx.fill();
        ctx.strokeStyle = C.violet; ctx.lineWidth = Math.min(1.1, 1 * px); ctx.stroke();
        if ((Number(n.new_evidence) || 0) > 0) {
          ctx.beginPath(); ctx.arc(n.x, n.y, Math.max(0.9 * px, d * 0.28), 0, 2 * Math.PI);
          ctx.fillStyle = C.red; ctx.fill();
        }
      } else {
        ctx.beginPath(); ctx.arc(n.x, n.y, r * 0.92, 0, 2 * Math.PI);
        ctx.fillStyle = C.ground; ctx.fill();
        ctx.strokeStyle = C.ink; ctx.lineWidth = Math.min(0.9, 0.85 * px); ctx.stroke();
      }
    }
    // your notes on this document: one small red dot per attached note or highlight, on a ring just
    // outside the dot (a written summary with nothing attached shows as one dot). Drawn in every state,
    // hover and selection included, so they never disappear.
    const nMarks = markCount(n);
    const ringR = r + 2.6 * px;
    if (nMarks) {
      ctx.fillStyle = C.red;
      const angles = [...partAngles(n).values()];
      if (!angles.length) angles.push(((strHash(n.id) % 360) / 360) * Math.PI * 2); // a summary alone: one dot
      const dr = dotRadius(r, px);
      for (const a of angles) {
        ctx.beginPath(); ctx.arc(n.x + Math.cos(a) * ringR, n.y + Math.sin(a) * ringR, dr, 0, 2 * Math.PI); ctx.fill();
      }
    }
    const outer = nMarks ? ringR + 2 * px : r;
    if (sel) {
      // reticle: a hairline circle with four ticks
      ctx.globalAlpha = 1;
      const R = outer + 6 * px;
      ctx.strokeStyle = C.ink; ctx.lineWidth = 0.75 * px;
      ctx.beginPath(); ctx.arc(n.x, n.y, R, 0, 2 * Math.PI); ctx.stroke();
      ctx.beginPath();
      for (const [ux, uy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        ctx.moveTo(n.x + ux * (R - 3 * px), n.y + uy * (R - 3 * px));
        ctx.lineTo(n.x + ux * (R + 6 * px), n.y + uy * (R + 6 * px));
      }
      ctx.stroke();
    } else if (hov) {
      ctx.beginPath(); ctx.arc(n.x, n.y, outer + 3 * px, 0, 2 * Math.PI);
      ctx.strokeStyle = C.ink2; ctx.lineWidth = 0.6 * px; ctx.stroke();
    }

    const match = S.search && S.matches.has(n.id);
    const regionsShowing = S.regionLabels && S.regionLabels.length && k <= 3.0;  // node labels defer to tags when zoomed out
    const must = hov || sel || match;
    let show;
    if (n.type === 'fragment') show = hov || sel;
    else show = must || k >= 4.2 || (!regionsShowing && highDegree(n) && k >= 0.75);
    if (show && alpha > 0.3) {
      const big = sel || hov;
      const fs = (big ? 14 : 10.5) * px;
      ctx.font = big ? `italic 500 ${fs}px "Bodoni Moda", Didot, Georgia, serif`
        : `500 ${fs}px "Archivo", -apple-system, "Segoe UI", sans-serif`;
      if ('fontStretch' in ctx) ctx.fontStretch = big ? 'normal' : 'semi-condensed';
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      const label = truncate(n.title || n.id, big ? 72 : 34);
      const y = n.y + outer + (sel ? 15 : big ? 8 : 4) * px;
      const w = ctx.measureText(label).width, box = [n.x - w / 2 - 8 * px, y - 3 * px, n.x + w / 2 + 8 * px, y + fs + 4 * px];
      const hit = (S.labelBoxes || []).some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
      if (hit && !must) { ctx.restore(); return; }
      (S.labelBoxes || (S.labelBoxes = [])).push(box);
      ctx.globalAlpha = alpha;
      ctx.lineWidth = 3.5 * px; ctx.lineJoin = 'round'; ctx.strokeStyle = C.ground;
      ctx.strokeText(label, n.x, y);
      ctx.fillStyle = big ? C.ink : C.ink2;
      ctx.fillText(label, n.x, y);
    }
    ctx.restore();
  }

  function onLinkClick(l) {
    const a = lid(l.source), b = lid(l.target);
    if (l.kind === 'part') {
      const na = S.nodes.get(a);
      const frag = na && na.type === 'fragment' ? a : b;
      openNote(frag, { center: true });
      return;
    }
    openExplain(a, b);
  }

  /** Fetch /api/graph and merge into the live graph, preserving node positions. */
  let graphReq = null, graphAgain = false;
  async function refreshGraph() {
    if (graphReq) { graphAgain = true; return graphReq; }
    graphReq = (async () => {
      try {
        const g = await api('GET', '/api/graph');
        mergeGraph(g);
      } catch (e) {
        toastErr(e, 'Could not load the graph');
      } finally {
        graphReq = null;
        if (graphAgain) { graphAgain = false; refreshGraph(); }
      }
    })();
    return graphReq;
  }

  /** The graph is about documents. Anything attached to a paper or note (fragments, and notes
   *  whose parent is set) is folded into that document's dot instead of getting its own node; its
   *  links move to the host and its words already count toward the host's size. Notes you write on
   *  their own (no parent) stay separate. Folded items are kept in S.folded for titles and search. */
  function foldAttached(g) {
    const raw = Array.isArray(g.nodes) ? g.nodes : [];
    const byId = new Map(raw.filter((n) => n && n.id).map((n) => [n.id, n]));
    const into = new Map();
    const root = (id) => {
      let cur = id, hops = 0;
      while (hops++ < 8) { const n = byId.get(cur); if (!n || !n.parent || !byId.has(n.parent) || n.parent === cur) break; cur = n.parent; }
      return cur;
    };
    for (const n of byId.values()) { const r = root(n.id); if (r !== n.id) into.set(n.id, r); }
    const host = (id) => into.get(id) || id;
    const folded = new Map(), parts = new Map();
    for (const [id, r] of into) { folded.set(id, byId.get(id)); parts.set(r, (parts.get(r) || 0) + 1); }
    // each attached note gets a fixed slot on its host's ring (sorted by id, so slots are stable)
    const slots = new Map(), perHost = new Map();
    for (const id of [...into.keys()].sort()) { const r = into.get(id); const i = perHost.get(r) || 0; perHost.set(r, i + 1); slots.set(id, i); }
    S.partSlots = slots;
    S.partsOf = new Map();
    for (const id of [...into.keys()].sort()) { const r = into.get(id); if (!S.partsOf.has(r)) S.partsOf.set(r, []); S.partsOf.get(r).push(id); }
    const nodes = raw.filter((n) => n && n.id && !into.has(n.id)).map((n) => ({ ...n, parts: parts.get(n.id) || 0 }));
    const links = [];
    for (const l of g.links || []) {
      if (l.kind === 'part') continue;
      const s0 = lid(l.source), t0 = lid(l.target), s = host(s0), t = host(t0);
      if (s !== t) links.push({ ...l, source: s, target: t, sPart: s0 !== s ? s0 : null, tPart: t0 !== t ? t0 : null });
    }
    const best = new Map();
    for (const l of g.similar || []) {
      const s0 = lid(l.source), t0 = lid(l.target), s = host(s0), t = host(t0);
      if (s === t) continue;
      const k = linkKey(s, t), sc = Number(l.score) || 0;
      if (!best.has(k) || best.get(k).score < sc) best.set(k, { source: s, target: t, score: sc, sPart: s0 !== s ? s0 : null, tPart: t0 !== t ? t0 : null });
    }
    // open questions link to the papers whose evidence you kept; disagreeing measurements link papers
    for (const q of g.question_links || []) {
      const t = host(q.paper);
      if (q.q && t && q.q !== t) links.push({ source: q.q, target: t, kind: 'question', verdict: q.verdict });
    }
    const seenConflict = new Set();
    for (const c of g.card_links || []) {
      if (!c.conflict) continue;
      const a = host(c.a_paper), b = host(c.b_paper), key = linkKey(a, b);
      if (a === b || seenConflict.has(key)) continue;
      seenConflict.add(key);
      links.push({ source: a, target: b, kind: 'conflict' });
    }
    S.foldInto = into; S.folded = folded;
    return { ...g, nodes, links, similar: [...best.values()] };
  }
  const hostOf = (id) => (S.foldInto && S.foldInto.get(id)) || id;

  function mergeGraph(g) {
    g = foldAttached(g);
    const incoming = Array.isArray(g.nodes) ? g.nodes : [];
    const next = new Map();
    let structural = false;
    const added = [];
    for (const raw of incoming) {
      if (!raw || !raw.id) continue;
      let n = S.nodes.get(raw.id);
      if (n) {
        if (n.type !== raw.type || (n.parent || '') !== (raw.parent || '') || n.cluster !== raw.cluster || n.sub !== raw.sub) structural = true;
        Object.assign(n, raw);
        // don't clobber edits that haven't reached the server yet
        if (S.cur && S.cur.id === n.id) {
          const pend = { ...(S.inflight || {}), ...S.patch };
          if ('title' in pend) n.title = S.cur.title;
          if ('tags' in pend) n.tags = S.cur.tags;
        }
      } else {
        n = { ...raw };
        structural = true;
        added.push(n);
      }
      if (n.parent === undefined) n.parent = '';
      if (raw.quote) S.quoteIdx.set(raw.id, raw.quote);
      next.set(n.id, n);
    }
    if (next.size !== S.nodes.size) structural = true;
    // seed new nodes near a neighbour (parent / link) so they don't fly in from the origin
    const neighbour = new Map();
    const manual = [], part = [], seen = new Set();
    for (const l of g.links || []) {
      const s = lid(l.source), t = lid(l.target);
      if (!next.has(s) || !next.has(t) || s === t) continue;
      const kind = ['part', 'mention', 'cites', 'question', 'conflict'].includes(l.kind) ? l.kind : 'manual';
      const key = kind + linkKey(s + (l.sPart ? '#' + l.sPart : ''), t + (l.tPart ? '#' + l.tPart : ''));
      if (seen.has(key)) continue; seen.add(key);
      (kind === 'part' ? part : manual).push({ source: s, target: t, kind, sPart: l.sPart || null, tPart: l.tPart || null, verdict: l.verdict });
      if (!neighbour.has(s)) neighbour.set(s, t);
      if (!neighbour.has(t)) neighbour.set(t, s);
    }
    // a fragment's parent edge may be missing from links; synthesize so fragments orbit
    for (const n of next.values()) {
      if (n.type === 'fragment' && n.parent && next.has(n.parent) && !seen.has('part' + linkKey(n.id, n.parent))) {
        seen.add('part' + linkKey(n.id, n.parent));
        part.push({ source: n.id, target: n.parent, kind: 'part' });
      }
    }
    const linkedPairs = new Set([...manual, ...part].map((l) => linkKey(l.source, l.target)));
    const sim = [];
    const simSeen = new Set();
    for (const l of g.similar || []) {
      const s = lid(l.source), t = lid(l.target);
      if (!next.has(s) || !next.has(t) || s === t) continue;
      const key = linkKey(s, t);
      if (simSeen.has(key) || linkedPairs.has(key)) continue; // manual link wins visually
      simSeen.add(key);
      sim.push({ source: s, target: t, kind: 'similar', score: Number(l.score) || 0, sPart: l.sPart || null, tPart: l.tPart || null });
    }
    // keep each node's 3 strongest similarities: enough to show where it belongs, not a hairball
    const byNode = new Map();
    for (const l of sim) for (const id of [l.source, l.target]) { if (!byNode.has(id)) byNode.set(id, []); byNode.get(id).push(l); }
    for (const arr of byNode.values()) { arr.sort((a, b) => b.score - a.score); arr.slice(0, 2).forEach((l) => { l.top = true; }); }
    // compare link structure with current
    const sig = (arr) => arr.map((l) => l.kind + linkKey(lid(l.source), lid(l.target)) + (l.kind === 'similar' ? ':' + l.score.toFixed(3) : '')).sort().join('|');
    const newLinks = [...part, ...manual, ...sim];
    if (!structural && sig(newLinks) !== sig(S.links)) structural = true;

    for (const n of added) {
      const nb = (n.parent && next.get(n.parent)) || next.get(neighbour.get(n.id));
      if (nb && nb.x !== undefined) {
        n.x = nb.x + (Math.random() - 0.5) * 30; n.y = nb.y + (Math.random() - 0.5) * 30;
      }
    }
    S.nodes = next;
    S.clusters = Array.isArray(g.clusters) ? g.clusters : [];
    const degs = [...next.values()].filter((n) => n.type !== 'fragment').map((n) => Number(n.degree) || 0).sort((a, b) => b - a);
    S.degreeCut = degs.length > 12 ? degs[Math.floor(degs.length * 0.12)] : 3;
    if (!S.firstGraph && S.nodes.size === 0 && S.selected) { /* library emptied */ }
    updateEmpty();
    updateSearch(false);
    const pl = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
    const inside = S.folded ? S.folded.size : 0;
    $('#legend-count').textContent = [pl(countType('paper'), 'paper'), pl(countType('note'), 'note'), inside ? `${inside} attached` : ''].filter(Boolean).join(', ');
    crawlFragments();

    if (!fg) return;
    if (structural) {
      // pin already-placed nodes briefly so only new/changed nodes move into place
      const pinned = [];
      if (!S.firstGraph && added.length < next.size) {
        for (const n of next.values()) {
          if (added.includes(n) || n.x === undefined || n.fx !== undefined) continue;
          n.fx = n.x; n.fy = n.y; n.__tmpPin = true; pinned.push(n);
        }
      }
      S.links = newLinks;
      fg.graphData({ nodes: Array.from(next.values()), links: newLinks });
      configureLinkForce();
      if (pinned.length) {
        setTimeout(() => {
          for (const n of pinned) if (n.__tmpPin) { delete n.__tmpPin; n.fx = undefined; n.fy = undefined; }
        }, 900);
      }
      const rf = fg.d3Force('regions'); if (rf && rf.relayout) rf.relayout();
      kick(1000);
      if (S.firstGraph && next.size) setTimeout(() => fg.zoomToFit(T(600), 90), 1400);
    }
    S.firstGraph = false;
  }
  const countType = (t) => { let c = 0; for (const n of S.nodes.values()) if (n.type === t) c++; return c; };

  function updateEmpty() {
    const empty = S.nodes.size === 0;
    $('#empty-state').hidden = !empty;
    $('#legend').hidden = empty;
    $('.graph-tools').hidden = empty;
  }

  function centerOn(id, zoomIn) {
    if (!fg) return;
    const n = S.nodes.get(hostOf(id));
    if (!n || n.x === undefined) return;
    const k = Math.max(fg.zoom(), zoomIn ? 1.6 : 0);
    const panelW = $('#panel').hidden ? 0 : $('#panel').offsetWidth;
    const chatW = $('#chat').hidden ? 0 : $('#chat').offsetWidth;
    const off = (panelW + chatW) / 2 / k;
    if (zoomIn && fg.zoom() < k) fg.zoom(k, T(500));
    fg.centerAt(n.x + off, n.y, T(600));
  }

  /** Background-index fragment quotes so search can match them. */
  let crawling = false;
  async function crawlFragments() {
    if (crawling) return;
    crawling = true;
    try {
      const parents = new Set();
      for (const n of S.nodes.values()) {
        if (n.type === 'fragment' && n.parent && !S.quoteIdx.has(n.id) && !S.crawled.has(n.parent) && S.nodes.has(n.parent)) parents.add(n.parent);
      }
      const list = Array.from(parents);
      const worker = async () => {
        while (list.length) {
          const pid = list.shift();
          S.crawled.add(pid);
          try { indexNote(await api('GET', `/api/notes/${encodeURIComponent(pid)}`)); } catch { /* ignore */ }
        }
      };
      await Promise.all([worker(), worker(), worker()]);
      if (S.search) updateSearch(false);
    } finally { crawling = false; }
  }
  function indexNote(note) {
    if (!note) return;
    S.noteMeta.set(note.id, { has_pdf: !!note.has_pdf, type: note.type, title: note.title });
    if (note.type === 'fragment' && note.quote !== undefined) S.quoteIdx.set(note.id, note.quote || '');
    for (const f of note.fragments || []) S.quoteIdx.set(f.id, f.quote || '');
  }

  // ======================================================================
  // Search
  // ======================================================================
  function nodeMatches(n, q) {
    if ((n.title || '').toLowerCase().includes(q)) return 1;
    if ((n.tags || []).some((t) => String(t).toLowerCase().includes(q))) return 2;
    if (n.type === 'fragment' && (S.quoteIdx.get(n.id) || '').toLowerCase().includes(q)) return 3;
    if ((n.authors || []).some((a) => String(a).toLowerCase().includes(q))) return 4;
    return 0;
  }
  function updateSearch(fromInput = true) {
    const q = S.search.trim().toLowerCase();
    S.matches = new Set();
    S.ranked = [];
    if (q) {
      const ranked = [];
      for (const n of [...S.nodes.values(), ...(S.folded ? S.folded.values() : [])]) {
        const m = nodeMatches(n, q);
        if (m) ranked.push({ n, m, pre: (n.title || '').toLowerCase().startsWith(q) ? 0 : 1, frag: n.type === 'fragment' ? 1 : 0 });
      }
      ranked.sort((a, b) => a.pre - b.pre || a.m - b.m || a.frag - b.frag || (a.n.title || '').localeCompare(b.n.title || ''));
      S.ranked = ranked.map((r) => r.n);
      for (const r of ranked) S.matches.add(hostOf(r.n.id));
    }
    $('#search-count').textContent = q ? String(S.matches.size) : '';
    kick(300);
    if (fromInput && fg && q && S.matches.size && S.matches.size <= 3) {
      /* gentle: don't move camera while typing */
    }
  }

  // ======================================================================
  // Threshold / semantic toggle
  // ======================================================================
  function setThreshold(v, persist = true) {
    v = Math.max(0, Math.min(1, Number(v) || 0));
    S.threshold = v;
    $('#sim-threshold').value = String(v);
    $('#sim-threshold-val').textContent = v.toFixed(2);
    if (persist) LS.set('threshold.' + (S.embedder.split(':')[0] || 'x'), v);
    configureLinkForce();
    if (fg) fg.d3ReheatSimulation && simNudge();
  }
  let nudgeT = null;
  function simNudge() {
    // re-applying the link force is enough; a mild reheat lets the layout respond
    clearTimeout(nudgeT);
    nudgeT = setTimeout(() => { if (fg) fg.d3ReheatSimulation(); }, 350);
  }
  function setShowSim(v) {
    S.showSim = !!v;
    LS.set('showSim', S.showSim);
    $('#sim-toggle').checked = S.showSim;
    $('.threshold').classList.toggle('off', !S.showSim);
    configureLinkForce();
    simNudge();
  }

  // ======================================================================
  // Note panel
  // ======================================================================
  const panel = $('#panel');
  const pbody = $('#panel-body');
  let reopenTab = null;
  let openSeq = 0;

  function setPanelOpen(open) {
    panel.hidden = !open;
    document.body.classList.toggle('panel-open', open);
    if (reopenTab) { reopenTab.remove(); reopenTab = null; }
  }

  async function openNote(id, { center = false, focusTitle = false } = {}) {
    if (!id) return;
    if (S.onOpenNote) { try { S.onOpenNote(id); } catch (e) { console.warn(e); } } // static/shortcuts.js: back/forward history
    const seq = ++openSeq;
    if (S.cur && S.cur.id !== id) await flushSave();
    if (seq !== openSeq) return;
    if (S.selected !== hostOf(id) || (S.cur && S.cur.id !== id)) {
      S.selected = hostOf(id); firePulse(S.selected);
      // count a visit (fire-and-forget); grows the node a little each time you come back to it
      fetch(`/api/notes/${encodeURIComponent(id)}/opened`, { method: 'POST' }).then((r) => r.ok && r.json())
        .then((d) => { const nd = d && S.nodes.get(id); if (nd) { nd.opens = d.opens; kick(200); } }).catch(() => {});
    } else if (S.selected !== hostOf(id)) {
      S.selected = hostOf(id);
    }
    kick(300);
    setPanelOpen(true);
    if (!S.cur || S.cur.id !== id) {
      pbody.replaceChildren(working('Opening'));
      $('#panel-type').textContent = '';
      $('#save-indicator').textContent = '';
    }
    if (center) setTimeout(() => centerOn(id), 30);
    let note;
    try {
      note = await api('GET', `/api/notes/${encodeURIComponent(id)}`);
    } catch (e) {
      if (seq !== openSeq) return;
      toastErr(e, 'Could not open the note');
      pbody.replaceChildren(h('div', { class: 'empty-list', text: e.message }));
      return;
    }
    if (seq !== openSeq) return;
    indexNote(note);
    S.cur = note;
    S.patch = {};
    S.saveErr = null;
    renderPanel();
    if (center) centerOn(id);
    if (focusTitle) { const t = $('.title-input', pbody); if (t) { t.focus(); t.select(); } }
  }

  async function reloadCur({ keepScroll = true } = {}) {
    if (!S.cur) return;
    const id = S.cur.id;
    await flushSave();
    try {
      const note = await api('GET', `/api/notes/${encodeURIComponent(id)}`);
      if (!S.cur || S.cur.id !== id) return;
      indexNote(note);
      const top = pbody.scrollTop;
      S.cur = note;
      renderPanel();
      if (keepScroll) pbody.scrollTop = top;
    } catch (e) { toastErr(e); }
  }

  function closePanel() {
    if (panel.hidden) return;
    flushSave();
    const cur = S.cur;
    setPanelOpen(false);
    S.selected = null; kick(300);
    if (cur) {
      reopenTab = h('button', { class: 'reopen-tab', title: 'Reopen the note', text: truncate(cur.title || cur.id, 30), onclick: () => openNote(cur.id) });
      document.body.appendChild(reopenTab);
    }
  }

  // ---------- saving ----------
  function setSaveInd(state, text) {
    const el = $('#save-indicator');
    el.className = 'save-ind ' + (state || '');
    el.textContent = text || '';
    el.title = state === 'err' ? (S.saveErr || '') + '\nClick to retry' : '';
  }
  $('#save-indicator').addEventListener('click', () => { if (S.saveErr) { S.saveErr = null; doSave(); } });

  function queueSave(fields) {
    if (!S.cur) return;
    Object.assign(S.patch, fields);
    Object.assign(S.cur, fields);
    // local graph update for title/tags so labels stay in sync immediately
    const n = S.nodes.get(S.cur.id);
    if (n) {
      if ('title' in fields) n.title = fields.title;
      if ('tags' in fields) n.tags = fields.tags;
    }
    if ('title' in fields && reopenTab) reopenTab.textContent = truncate(fields.title, 30);
    setSaveInd('saving', 'Unsaved');
    clearTimeout(S.saveTimer);
    S.saveTimer = setTimeout(doSave, SAVE_DEBOUNCE);
  }

  async function doSave() {
    clearTimeout(S.saveTimer); S.saveTimer = null;
    if (S.saving) { await S.saving.catch(() => {}); }
    if (!S.cur || !Object.keys(S.patch).length) return;
    const id = S.cur.id;
    const patch = S.patch;
    S.patch = {};
    S.inflight = patch;
    setSaveInd('saving', 'Saving');
    const p = (async () => {
      try {
        const updated = await api('PUT', `/api/notes/${encodeURIComponent(id)}`, patch);
        S.saveErr = null;
        if (S.cur && S.cur.id === id) {
          // keep any newer local edits; take server-derived fields
          for (const k of ['updated', 'has_pdf', 'has_text', 'links', 'title']) if (updated && k in updated && !(k in S.patch)) S.cur[k] = updated[k];
          if (!Object.keys(S.patch).length) setSaveInd('', 'Saved');
          const upd = $('#upd-stamp', pbody); if (upd && updated) upd.textContent = 'updated ' + fmtDate(updated.updated);
        }
        scheduleGraphRefresh();
      } catch (e) {
        // restore patch so nothing is lost; retry on next edit or click
        if (S.cur && S.cur.id === id) S.patch = { ...patch, ...S.patch };
        S.saveErr = e.message;
        setSaveInd('err', 'Not saved. Retry');
        toastErr(e, 'Save failed');
        throw e;
      }
    })();
    S.saving = p;
    try { await p; } catch { /* reported */ } finally { if (S.saving === p) { S.saving = null; S.inflight = null; } }
  }
  async function flushSave() {
    if (S.saveTimer || Object.keys(S.patch).length) await doSave();
    else if (S.saving) await S.saving.catch(() => {});
  }
  let graphRefreshT = null;
  function scheduleGraphRefresh() { clearTimeout(graphRefreshT); graphRefreshT = setTimeout(refreshGraph, 1200); }
  window.addEventListener('beforeunload', (e) => {
    if (S.saveTimer || Object.keys(S.patch).length) {
      // best-effort synchronous-ish save
      try {
        const id = S.cur.id;
        fetch(`/api/notes/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(S.patch), keepalive: true });
      } catch { /* ignore */ }
    }
  });

  // ---------- render ----------
  function autosize(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 2 + 'px'; }
  // a narrower or wider panel (resizer, window, phone rotation) re-wraps the title: measure it again
  if (window.ResizeObserver) {
    let lastW = 0;
    new ResizeObserver(() => { const w = pbody.clientWidth; if (w && w !== lastW) { lastW = w; $$('textarea.title-input', pbody).forEach(autosize); } }).observe(pbody);
  }

  function renderPanel() {
    const n = S.cur;
    if (!n) return;
    const typeEl = $('#panel-type');
    const custom = (S.renderers || {})[n.type]; // other types (static/questions.js: "question") render themselves
    typeEl.replaceChildren(h('span', { class: 'tdot ' + (n.type || 'note') }), custom ? custom.label : n.type === 'paper' ? 'Paper' : n.type === 'fragment' ? 'Fragment' : 'Note',
      ...(n.type === 'paper' && n.source === 'linked' && !n.has_pdf ? [h('span', { class: 'kind-link', title: 'Linked article: the link, full text and metadata are saved, not the PDF file', html: LINK_SVG })] : []));
    setSaveInd('', '');
    const kids = custom ? custom.render(n) : n.type === 'fragment' ? renderFragmentView(n) : renderNoteView(n);
    pbody.replaceChildren(...kids);
    pbody.scrollTop = 0;
    $$('textarea.title-input', pbody).forEach(autosize);
    // the display face may still be loading: measure the title again once it has (else a long title is clipped)
    if (document.fonts && document.fonts.status !== 'loaded') document.fonts.ready.then(() => $$('textarea.title-input', pbody).forEach(autosize));
    applyClaude(pbody);
    for (const fn of S.panelHooks || []) { try { fn(n, pbody); } catch (e) { console.error(e); } } // static/topics.js etc.
  }

  function titleField(n) {
    const ta = h('textarea', { class: 'title-input', rows: 1, spellcheck: true, placeholder: 'Untitled', 'aria-label': 'Title' });
    ta.value = n.title || '';
    ta.addEventListener('input', () => { autosize(ta); queueSave({ title: ta.value }); });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ta.blur(); } });
    ta.addEventListener('blur', () => { if (!ta.value.trim()) { ta.value = n.title || 'Untitled'; queueSave({ title: ta.value }); autosize(ta); } });
    return ta;
  }

  function section(title, count, ...kids) {
    return h('div', { class: 'section', dataset: { sec: title } },
      h('div', { class: 'sec-head' }, h('span', { class: 'sec-title' }, title, count !== undefined && count !== null ? h('span', { class: 'n', text: String(count) }) : null), kids.flat().filter((k) => k && k.__head)),
      kids.flat().filter((k) => !(k && k.__head)));
  }
  const head = (el) => { if (el) el.__head = true; return el; };

  /** relative time like "2 min ago" */
  function ago(iso) {
    const t = new Date(iso).getTime();
    if (!t) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 45) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`;
    return new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function renderNoteView(n) {
    const out = [];
    out.push(titleField(n));

    if (n.type === 'paper') {
      const bits = [];
      if (n.authors && n.authors.length) bits.push(h('span', { class: 'authors', title: n.authors.join(', '), text: fmtAuthors(n.authors, 6) }));
      if (n.year) bits.push(h('span', { class: 'tnum', text: String(n.year) }));
      if (n.venue) bits.push(h('span', { class: 'venue', text: n.venue }));
      if (bits.length) out.push(h('div', { class: 'meta' }, bits));
    }
    const ids = [];
    if (n.doi) ids.push(h('span', null, 'DOI ', h('a', { class: 'ident', href: 'https://doi.org/' + n.doi, target: '_blank', rel: 'noopener', text: n.doi })));
    if (n.arxiv) ids.push(h('span', null, 'arXiv ', h('a', { class: 'ident', href: 'https://arxiv.org/abs/' + n.arxiv, target: '_blank', rel: 'noopener', text: n.arxiv })));
    if (n.url && /^https?:/i.test(n.url)) ids.push(h('a', { href: n.url, target: '_blank', rel: 'noopener', title: n.url, text: 'Source page' }));
    if (n.s2_id) ids.push(h('a', { href: 'https://www.semanticscholar.org/paper/' + n.s2_id, target: '_blank', rel: 'noopener', text: 'Semantic Scholar' }));
    if (ids.length) out.push(h('div', { class: 'ids' }, ids));

    const acts = [];
    if (n.has_pdf) {
      acts.push(h('a', { class: 'btn', href: `/viewer/${encodeURIComponent(n.id)}`, target: '_blank', rel: 'noopener', title: 'Read the PDF and select passages to save as fragments' }, 'Open in viewer and highlight'));
      acts.push(h('a', { class: 'btn btn-text', href: `/pdf/${encodeURIComponent(n.id)}`, target: '_blank', rel: 'noopener', title: 'Open the raw PDF in a new tab' }, 'Open PDF'));
    } else if (n.type === 'paper' && n.source === 'linked') {
      out.push(h('div', { class: 'linked-line' }, h('span', { class: 'kind-link', html: LINK_SVG }),
        h('span', null, h('b', { text: 'Linked article. ' }), n.has_text ? 'Its link, full text and metadata are saved; search, citations and the AI features use the text. The PDF file isn’t kept.' : 'Its link and metadata are saved, not the PDF file.')));
      if (httpUrl(n.url)) acts.push(h('a', { class: 'btn', href: httpUrl(n.url), target: '_blank', rel: 'noopener noreferrer', title: 'Open the article on its site (your browser, your access)' }, 'Open article'));
      acts.push(h('button', { class: 'btn', title: 'Look for a free legal copy; otherwise save it with the browser extension', onclick: (e) => getPdf(n, e.currentTarget) }, 'Get the PDF'));
      const inp = h('input', { type: 'file', accept: 'application/pdf,.pdf', hidden: true });
      const btn = h('button', { class: 'btn btn-text', title: 'Attach a PDF file you already have', onclick: () => inp.click() }, 'Attach PDF');
      inp.addEventListener('change', () => { const f = inp.files[0]; inp.value = ''; if (f) attachPdf(n.id, f, btn); });
      acts.push(btn, inp);
    } else if (n.type === 'paper') {
      const inp = h('input', { type: 'file', accept: 'application/pdf,.pdf', hidden: true });
      const btn = h('button', { class: 'btn', title: 'Attach a PDF file to this paper', onclick: () => inp.click() }, 'Attach PDF');
      inp.addEventListener('change', () => { const f = inp.files[0]; inp.value = ''; if (f) attachPdf(n.id, f, btn); });
      acts.push(btn, inp);
    }
    if (n.s2_id || n.doi || n.arxiv) {
      const busy = S.ops.has('discover:' + n.id);
      acts.push(h('button', { class: 'btn' + (busy ? ' busy' : ''), disabled: busy, title: 'References, citations and recommendations from Semantic Scholar', onclick: (e) => runDiscover(n.id, e.currentTarget) }, 'Find related papers'));
    }
    if (n.type === 'paper') {
      acts.unshift(h('button', { class: 'btn btn-ask-paper needs-claude', 'data-act': 'ask-paper', title: `Talk with ${ai()} about this paper: it reads the paper and your notes on it`, onclick: () => chatAboutPaper(n.id) }, 'Ask about this paper'));
    }
    if (n.type === 'paper') {
      const src = { crossref: 'Crossref', datacite: 'DataCite', arxiv: 'arXiv', pubmed: 'PubMed', openlibrary: 'Open Library', osti: 'OSTI', ntrs: 'NASA NTRS', 'translation-server': 'Zotero translation-server', s2: 'Semantic Scholar', page: 'the article page', zotero: 'Zotero' }[n.meta_source] || '';
      const when = n.meta_checked ? ` on ${String(n.meta_checked).slice(0, 10)}` : '';
      acts.push(h('button', { class: 'btn btn-text', 'data-act': 'refresh-metadata',
        title: (src ? `Metadata from ${src}${when}. ` : '') + 'Look the paper up again by its DOI, arXiv id, PMID or ISBN (Crossref, arXiv, PubMed, Open Library…) and update its details. Fields you edited are kept.',
        onclick: (e) => refreshMetadata(n.id, e.currentTarget) }, 'Refresh metadata'));
    }
    if (acts.length) out.push(h('div', { class: 'actions' }, acts));

    if (n.type === 'paper') out.push(renderDetailsEditor(n));

    if (n.abstract) {
      const det = h('details', { class: 'disclose abstract section', open: !(n.summary || '').trim() },
        h('summary', null, h('span', { class: 'sec-title', text: 'Abstract' })),
        mdEl(n.abstract, 'md abstract-text'));
      out.push(det);
    }

    out.push(renderSummary(n));
    out.push(renderBodyEditor(n, 'Notes', 'Your notes. Markdown, $inline$ and $$display$$ math. Type [[ to link another note.'));
    out.push(section('Tags', null, renderTags(n)));
    out.push(renderFragments(n));
    const disc = h('div', { id: 'discover-slot' });
    out.push(disc);
    fillDiscover(disc, n.id);
    out.push(renderRelated(n));
    out.push(renderBacklinks(n));
    out.push(renderLinked(n));
    out.push(renderDanger(n));
    return out;
  }

  function renderDetailsEditor(n) {
    const mk = (label, key, val, ph, type = 'text') => {
      const inp = h('input', { type, placeholder: ph || '', 'aria-label': label });
      inp.value = val ?? '';
      inp.addEventListener('input', () => {
        let v = inp.value;
        if (key === 'authors') v = v.split(';').map((s) => s.trim()).filter(Boolean);
        if (key === 'year') { v = v.trim() ? parseInt(v, 10) : null; if (v !== null && !Number.isFinite(v)) return; }
        queueSave({ [key]: v });
      });
      return [h('span', { text: label }), inp];
    };
    return h('details', { class: 'disclose', style: { marginTop: '12px' } },
      h('summary', null, h('span', { class: 'hint', text: 'Edit details' })),
      h('div', { class: 'meta-edit' },
        mk('Authors', 'authors', (n.authors || []).join('; '), 'Separate with semicolons'),
        mk('Year', 'year', n.year ?? '', '2024', 'number'),
        mk('Venue', 'venue', n.venue, 'Journal or conference'),
        mk('URL', 'url', n.url, 'https://')));
  }

  function renderSummary(n) {
    const wrap = h('div', { class: 'section', id: 'summary-sec' });
    const ta = h('textarea', {
      class: 'field', rows: 4, spellcheck: true, 'aria-label': 'Your summary',
      placeholder: 'Two or three sentences, in your own words: the problem, the method, the result, and why it matters to you.',
    });
    ta.value = n.summary || '';
    const live = h('div', { class: 'md live' });
    const key = 'critique:' + n.id;
    const btn = h('button', { class: 'btn btn-primary needs-claude', title: `${ai()} compares your summary with the paper`, onclick: () => runCritique(n.id) }, 'Critique my summary');
    const hint = h('span', { class: 'hint' });
    const critBox = h('div');
    const setBtnState = () => {
      const empty = !ta.value.trim();
      btn.dataset.off = empty ? '1' : '0';
      btn.dataset.offWhy = `Write your summary first, then ${ai()} can critique it.`;
      hint.textContent = empty ? 'Write a summary to get a critique.' : '';
      btn.classList.toggle('busy', S.ops.has(key));
      applyClaude(wrap);
      renderCritique(critBox, n, ta.value);
    };
    ta.addEventListener('input', () => { queueSave({ summary: ta.value }); setBtnState(); });
    const box = h('div', { class: 'mine summary-box' }, ta);
    wrap.append(h('div', { class: 'sec-head' }, h('span', { class: 'sec-title', text: 'Your summary' })), box, live, h('div', { class: 'actions' }, btn, hint), critBox);
    latex(ta, live);
    wrap.__refresh = setBtnState;
    setBtnState();
    return wrap;
  }

  function renderCritique(box, n, liveSummary) {
    const op = S.ops.get('critique:' + n.id);
    const kids = [];
    if (op && op.live) {
      kids.push(h('div', { class: 'machine streaming', 'aria-live': 'polite' },
        h('div', { class: 'machine-head' }, h('span', { text: `${ai()}'s critique, writing…` })),
        h('div', { class: 'md crit-live', html: partialMD(op.live) })));
    } else if (op) kids.push(h('div', { class: 'machine' }, working(`${ai()} is reading your summary`, op.start)));
    const c = op && op.live ? null : n.critique;
    if (c && c.text) {
      const stale = (liveSummary ?? n.summary ?? '') !== (c.summary_snapshot ?? '');
      kids.push(h('div', { class: 'machine' },
        h('div', { class: 'machine-head' },
          h('span', { title: fmtDate(c.at), text: `${ai()}'s critique, ${ago(c.at)}` }),
          stale ? h('span', { class: 'stale', title: 'Your summary has changed since this critique was written', text: 'Summary changed since' }) : null),
        mdEl(c.text)));
    }
    if (S.critiqueHistory) { try { kids.push(S.critiqueHistory(n, liveSummary ?? n.summary ?? '')); } catch (e) { console.warn(e); } } // static/review.js
    box.replaceChildren(...kids.filter(Boolean));
  }

  async function runCritique(id) {
    const sec = $('#summary-sec', pbody);
    const btn = sec && $('.needs-claude', sec);
    if (claudeBlocked(btn)) return;
    const key = 'critique:' + id;
    if (S.ops.has(key)) return;
    await flushSave();
    if (S.saveErr) { toast("Your summary hasn't saved yet. Fix the save error, then try again.", 'error'); return; }
    const snapshot = S.cur && S.cur.id === id ? S.cur.summary || '' : '';
    S.ops.set(key, { start: Date.now() });
    const refresh = () => { const s = S.cur && S.cur.id === id && $('#summary-sec', pbody); if (s && s.__refresh) s.__refresh(); };
    refresh();
    // stream the critique into the open panel as Claude writes it (falls back to the plain endpoint)
    const op = S.ops.get(key);
    const paintLive = (text) => {
      const box = S.cur && S.cur.id === id && $('#summary-sec', pbody) && $('#summary-sec .crit-live', pbody);
      if (box) box.innerHTML = partialMD(text);
    };
    const live = streamRenderer((text) => { op.live = text; paintLive(text); });
    try {
      let r;
      try {
        r = await apiStream(`/api/notes/${encodeURIComponent(id)}/critique/stream`, undefined, (t) => {
          if (!op.live) { op.live = t; refresh(); } // first words: swap the spinner for the live box
          live.push(t);
        });
        live.flush();
      } catch (e) {
        live.cancel();
        if (e.claude) throw e;
        console.warn('critique streaming failed, retrying without streaming:', e);
        op.live = '';
        refresh();
        r = await api('POST', `/api/notes/${encodeURIComponent(id)}/critique`);
      }
      const crit = { text: r.text, at: r.at, summary_snapshot: r.summary_snapshot ?? snapshot };
      if (S.cur && S.cur.id === id) { S.cur.critique = crit; S.cur.critiques = [...(S.cur.critiques || []), crit]; }
      else toast('Critique ready', 'ok', { action: { label: 'Open', fn: () => openNote(id) } });
    } catch (e) {
      toastErr(e, 'Critique failed');
    } finally {
      S.ops.delete(key);
      refresh();
    }
  }

  function renderBodyEditor(n, title, ph) {
    const mode = S.bodyMode[n.id] || ((n.body || '').trim() ? 'preview' : 'edit');
    const ta = h('textarea', { class: 'field body-field', rows: 10, spellcheck: true, placeholder: ph, 'aria-label': title });
    ta.value = n.body || '';
    const live = h('div', { class: 'md live' });
    const editWrap = h('div', null, ta, live);
    const prev = h('div', { class: 'preview md', title: 'Double-click to edit' });
    const bEdit = h('button', { type: 'button', text: 'Edit' });
    const bPrev = h('button', { type: 'button', text: 'Preview' });
    const autosizeBody = () => { ta.style.height = 'auto'; ta.style.height = Math.max(220, Math.min(ta.scrollHeight + 2, 900)) + 'px'; };
    const set = (m) => {
      S.bodyMode[n.id] = m;
      bEdit.classList.toggle('on', m === 'edit');
      bPrev.classList.toggle('on', m === 'preview');
      editWrap.hidden = m !== 'edit';
      prev.hidden = m !== 'preview';
      if (m === 'preview') {
        const html = renderMD(ta.value);
        if (html) prev.innerHTML = html; else prev.replaceChildren(h('div', { class: 'empty', text: 'Nothing written yet. Double-click to start.' }));
      } else autosizeBody();
    };
    bEdit.onclick = () => { set('edit'); ta.focus(); };
    bPrev.onclick = () => set('preview');
    prev.addEventListener('dblclick', (e) => { if (e.target.closest('a,button')) return; set('edit'); ta.focus(); });
    ta.addEventListener('input', () => { autosizeBody(); queueSave({ body: ta.value }); });
    ta.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); set('preview'); }
    });
    latex(ta, live);
    const sec = section(title, null, head(h('div', { class: 'spacer' })), head(h('div', { class: 'seg', role: 'group' }, bEdit, bPrev)), editWrap, prev);
    set(mode);
    setTimeout(() => { if (!editWrap.hidden) autosizeBody(); }, 0);
    return sec;
  }

  function renderTags(n) {
    const box = h('div', { class: 'tags-box', title: 'Comma or Enter adds a tag. Backspace removes the last one.' });
    const input = h('input', { type: 'text', placeholder: 'Add tags, separated by commas', spellcheck: false, 'aria-label': 'Add tags' });
    const tags = Array.isArray(n.tags) ? n.tags.slice() : [];
    const commit = () => queueSave({ tags: tags.slice() });
    const draw = () => {
      box.replaceChildren(...tags.map((t, i) => h('span', { class: 'chip' }, t,
        h('button', { type: 'button', title: 'Remove tag', 'aria-label': 'Remove tag ' + t, text: '×', onclick: (e) => { e.stopPropagation(); tags.splice(i, 1); commit(); draw(); input.focus(); } }))), input);
      input.placeholder = tags.length ? '' : 'Add tags, separated by commas';
    };
    const addFrom = (txt) => {
      let changed = false;
      for (const p of txt.split(',').map((x) => x.trim().replace(/^#/, '')).filter(Boolean)) {
        if (!tags.some((t) => t.toLowerCase() === p.toLowerCase())) { tags.push(p); changed = true; }
      }
      if (changed) commit();
      draw();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); if (input.value.trim()) { addFrom(input.value); input.value = ''; input.focus(); } }
      else if (e.key === 'Backspace' && !input.value && tags.length) { tags.pop(); commit(); draw(); input.focus(); }
    });
    input.addEventListener('input', () => { if (input.value.includes(',')) { const v = input.value; input.value = ''; addFrom(v); input.focus(); } });
    input.addEventListener('blur', () => { if (input.value.trim()) { addFrom(input.value); input.value = ''; } });
    box.addEventListener('click', () => input.focus());
    draw();
    return box;
  }

  // ---------- fragments ----------
  function pageFromLocator(loc) {
    const m = String(loc || '').match(/\b(?:p|pp|pg|page)\.?\s*(\d{1,5})/i);
    return m ? parseInt(m[1], 10) : null;
  }
  function fragLoc(f) { return f.locator || (f.page ? `p. ${f.page}` : ''); }

  function renderFragments(n) {
    const frags = Array.isArray(n.fragments) ? n.fragments : [];
    const list = h('div');
    if (!frags.length) list.appendChild(h('div', { class: 'empty-list', text: n.has_pdf ? 'No fragments yet. Highlight a passage in the viewer, or add one here.' : 'No fragments yet. Save a quote, equation or passage with your note on it.' }));
    for (const f of frags) {
      list.appendChild(h('button', { type: 'button', class: 'frag', onclick: () => openNote(f.id, { center: true }), title: f.title || '' },
        fragLoc(f) ? h('div', { class: 'frag-loc', text: fragLoc(f) }) : null,
        f.quote ? h('div', { class: 'frag-quote', html: renderMD(truncate(f.quote, 320), { inline: true }) }) : null,
        f.body ? h('div', { class: 'frag-note', html: renderMD(truncate(f.body, 220), { inline: true }) }) : null,
        !f.quote && !f.body && !fragLoc(f) ? h('div', { class: 'frag-note', text: f.title || 'Fragment' }) : null));
    }
    const form = h('form', { class: 'frag-form', hidden: true });
    const q = h('textarea', { rows: 3, class: 'field quote-field', placeholder: 'The passage or equation. LaTeX works: $E = mc^2$' });
    const qLive = h('div', { class: 'md live' });
    const loc = h('input', { type: 'text', placeholder: 'p. 4, Eq. 7' });
    const note = h('textarea', { rows: 2, class: 'field', placeholder: 'What it means to you (optional)' });
    const noteLive = h('div', { class: 'md live' });
    const save = h('button', { class: 'btn btn-primary', type: 'submit', text: 'Add fragment' });
    const cancel = h('button', { class: 'btn btn-text', type: 'button', text: 'Cancel' });
    form.append(
      h('label', null, 'Quote', q), qLive,
      h('label', null, 'Where in the paper', loc),
      h('label', null, 'Your note', note), noteLive,
      h('div', { class: 'frow' }, save, cancel));
    latex(q, qLive);
    latex(note, noteLive);
    const toggle = h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { form.hidden = !form.hidden; if (!form.hidden) q.focus(); } }, 'Add fragment');
    cancel.onclick = () => { form.hidden = true; };
    form.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); form.requestSubmit(); } });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!q.value.trim() && !note.value.trim() && !loc.value.trim()) { toast('Add a quote, a location or a note first.', 'info'); q.focus(); return; }
      save.disabled = true; save.textContent = 'Adding';
      try {
        const body = { type: 'fragment', parent: n.id, quote: q.value, locator: loc.value.trim(), body: note.value };
        const pg = pageFromLocator(loc.value);
        if (pg) body.page = pg;
        const created = await api('POST', '/api/notes', body);
        S.quoteIdx.set(created.id, created.quote || q.value);
        toast('Fragment added', 'ok', { action: { label: 'Open', fn: () => openNote(created.id, { center: true }) } });
        q.value = loc.value = note.value = ''; qLive.innerHTML = noteLive.innerHTML = '';
        await refreshGraph();
        if (S.cur && S.cur.id === n.id) await reloadCur();
      } catch (err) {
        toastErr(err, 'Could not add the fragment');
      } finally { save.disabled = false; save.textContent = 'Add fragment'; }
    });
    return section('Fragments', frags.length || null, head(h('div', { class: 'spacer' })), head(toggle), list, form);
  }

  function renderFragmentView(n) {
    const out = [];
    const parent = S.nodes.get(n.parent);
    const pmeta = S.noteMeta.get(n.parent);
    const ptitle = (parent && parent.title) || (pmeta && pmeta.title) || n.parent || 'No parent';
    out.push(h('div', { class: 'crumb' },
      h('span', { text: 'From' }),
      n.parent ? h('button', { type: 'button', title: 'Back to ' + ptitle, text: truncate(ptitle, 70), onclick: () => openNote(n.parent, { center: true }) }) : h('span', { text: ptitle })));
    out.push(titleField(n));

    // quote
    const qView = h('div', { class: 'quote-block md', title: 'Double-click to edit' });
    const qEdit = h('textarea', { class: 'field quote-field', rows: 4, placeholder: 'The passage or equation. LaTeX works.' });
    const qLive = h('div', { class: 'md live' });
    const qEditWrap = h('div', { class: 'mine', hidden: true }, qEdit, qLive);
    qEdit.value = n.quote || '';
    const drawQ = () => {
      const html = renderMD(qEdit.value);
      if (html) qView.innerHTML = html; else qView.replaceChildren(h('span', { class: 'empty', text: 'No quote yet. Choose Edit to add the passage.' }));
    };
    const qBtn = h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Edit' });
    qBtn.onclick = () => {
      const editing = qEditWrap.hidden;
      qEditWrap.hidden = !editing; qView.hidden = editing;
      qBtn.textContent = editing ? 'Done' : 'Edit';
      if (editing) { qEdit.focus(); autosize(qEdit); } else drawQ();
    };
    qView.addEventListener('dblclick', (e) => { if (!e.target.closest('a,button') && qEditWrap.hidden) qBtn.click(); });
    qEdit.addEventListener('input', () => { autosize(qEdit); S.quoteIdx.set(n.id, qEdit.value); queueSave({ quote: qEdit.value }); });
    latex(qEdit, qLive);
    drawQ();

    const locIn = h('input', { type: 'text', placeholder: 'p. 4, Eq. 7, or Sec. 3.2' });
    locIn.value = n.locator || '';
    const pageIn = h('input', { type: 'number', min: 1, step: 1, placeholder: '' });
    pageIn.value = n.page ?? '';
    const viewSlot = h('span');
    const drawViewLink = () => {
      const pm = S.noteMeta.get(n.parent);
      viewSlot.replaceChildren();
      if (pm && pm.has_pdf) {
        const pg = S.cur && S.cur.id === n.id ? S.cur.page : n.page;
        viewSlot.appendChild(h('a', { class: 'btn btn-sm', href: `/viewer/${encodeURIComponent(n.parent)}${pg ? '#page=' + pg : ''}`, target: '_blank', rel: 'noopener', title: 'Open the paper in the viewer at this page' },
          pg ? `View in PDF, p. ${pg}` : 'View in PDF'));
      }
    };
    locIn.addEventListener('input', () => {
      queueSave({ locator: locIn.value });
      const pg = pageFromLocator(locIn.value);
      if (pg && !pageIn.value) { pageIn.value = pg; queueSave({ page: pg }); drawViewLink(); }
    });
    pageIn.addEventListener('input', () => {
      const v = pageIn.value.trim() ? parseInt(pageIn.value, 10) : null;
      if (v !== null && (!Number.isFinite(v) || v < 1)) return;
      queueSave({ page: v }); drawViewLink();
    });

    out.push(h('div', { class: 'section' },
      h('div', { class: 'sec-head' }, h('span', { class: 'sec-title', text: 'Quote' }), h('div', { class: 'spacer' }), viewSlot, qBtn),
      qView, qEditWrap,
      h('div', { class: 'loc-grid' }, h('label', null, 'Where in the paper', locIn), h('label', null, 'Page', pageIn))));

    if (n.parent && !S.noteMeta.has(n.parent)) {
      api('GET', `/api/notes/${encodeURIComponent(n.parent)}`).then((p) => {
        indexNote(p);
        if (S.cur && S.cur.id === n.id) { drawViewLink(); const b = $('.crumb button', pbody); if (b) b.textContent = truncate(p.title, 70); }
      }).catch(() => {});
    } else drawViewLink();

    out.push(renderBodyEditor(n, 'Your note', 'What this passage means to you. Markdown and LaTeX.'));
    out.push(section('Tags', null, renderTags(n)));
    out.push(renderRelated(n));
    out.push(renderBacklinks(n));
    out.push(renderLinked(n));
    out.push(renderDanger(n));
    return out;
  }

  // ---------- related / linked ----------
  function nodeTitle(id) { const x = S.nodes.get(id) || (S.folded && S.folded.get(id)); return x ? x.title : (S.noteMeta.get(id) || {}).title || id; }
  function nodeType(id) { const x = S.nodes.get(id) || (S.folded && S.folded.get(id)); return x ? x.type : (S.noteMeta.get(id) || {}).type || 'note'; }
  const typeName = (t) => (t === 'paper' ? 'Paper' : t === 'fragment' ? 'Fragment' : 'Note');

  function rowSub(id, type) {
    const x = S.nodes.get(id);
    const bits = [h('span', { text: typeName(type) })];
    if (x && x.year) bits.push(h('span', { text: String(x.year) }));
    if (x && x.authors && x.authors.length) bits.push(h('span', { text: fmtAuthors(x.authors, 1) }));
    return h('div', { class: 'r-sub' }, bits);
  }

  function renderRelated(n) {
    const sim = (Array.isArray(n.similar) ? n.similar : []).filter((s) => s.id !== n.id && s.id !== n.parent);
    const linked = new Set(n.links || []);
    const rows = h('div', { class: 'rows' });
    if (!sim.length) rows.appendChild(h('div', { class: 'empty-list', text: 'Nothing similar yet. Neighbours appear as your library grows.' }));
    for (const s of sim) {
      const isLinked = linked.has(s.id);
      const pct = Math.max(0, Math.min(1, Number(s.score) || 0));
      const linkBtn = h('button', { class: 'btn btn-sm', type: 'button', disabled: isLinked, title: isLinked ? 'Already linked' : 'Link these two yourself', text: isLinked ? 'Linked' : 'Link' });
      linkBtn.onclick = () => linkNotes(n.id, s.id, linkBtn);
      rows.appendChild(h('div', { class: 'row' },
        h('span', { class: 'tdot ' + (s.type || 'note') }),
        h('div', { class: 'r-main', onclick: () => openNote(s.id, { center: true }), title: s.title },
          h('div', { class: 'r-title', text: s.title || s.id }), rowSub(s.id, s.type)),
        h('div', { class: 'score', title: 'Similarity ' + pct.toFixed(3) }, h('span', { class: 'bar' }, h('i', { style: { width: (pct * 100).toFixed(1) + '%' } })), h('span', { text: pct.toFixed(2) })),
        h('div', { class: 'r-actions' },
          h('button', { class: 'btn btn-text btn-sm needs-claude', type: 'button', title: `Ask ${ai()} why these are connected`, text: 'Why', onclick: (e) => { if (!claudeBlocked(e.currentTarget)) openExplain(n.id, s.id); } }),
          linkBtn)));
    }
    return section('Similar', sim.length || null, rows);
  }

  // ---------- [[wikilink]] backlinks ("Mentioned in") + unresolved links ----------
  function renderBacklinks(n) {
    const bl = Array.isArray(n.backlinks) ? n.backlinks : [];
    const rows = h('div', { class: 'rows' });
    if (!bl.length) rows.appendChild(h('div', { class: 'empty-list', text: `Nothing links here yet. Write [[${truncate(n.title || n.id, 40)}]] in another note to mention it.` }));
    for (const b of bl) {
      const snip = h('div', { class: 'bl-snip md', html: renderMD(b.snippet || '', { inline: true }) });
      // the chip for this note itself is just a marker here
      for (const c of $$(`.cite[data-open="${CSS.escape(n.id)}"]`, snip)) { c.classList.add('here'); c.removeAttribute('data-open'); }
      rows.appendChild(h('div', { class: 'row' },
        h('span', { class: 'tdot ' + (b.type || 'note') }),
        h('div', { class: 'r-main', onclick: (e) => { if (!e.target.closest('[data-open],[data-create]')) openNote(b.id, { center: true }); }, title: b.title },
          h('div', { class: 'r-title', text: b.title || b.id }), snip)));
    }
    const sec = section('Mentioned in', bl.length || null, rows);
    sec.classList.add('backlinks');
    const un = Array.isArray(n.unresolved) ? n.unresolved : [];
    if (un.length) {
      sec.appendChild(h('div', { class: 'unresolved-links' },
        h('div', { class: 'ul-head', text: un.length === 1 ? 'One link points to a note that doesn’t exist yet' : `${un.length} links point to notes that don’t exist yet` }),
        un.map((t) => h('div', { class: 'ul-row' },
          h('span', { class: 'ul-name', title: t, text: t }),
          h('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Create note', onclick: (e) => createFromLink(t, e.currentTarget) })))));
    }
    return sec;
  }

  /** Create a note titled `title` (from an unresolved [[title]]); the link then resolves by title. */
  async function createFromLink(title, btn) {
    title = String(title || '').trim();
    if (!title) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Creating'; }
    try {
      await flushSave();
      const note = await api('POST', '/api/notes', { type: 'note', title });
      await refreshGraph();
      S.bodyMode[note.id] = 'edit';
      toast(`Created “${truncate(title, 50)}”`, 'ok');
      await openNote(note.id, { center: true });
    } catch (e) {
      toastErr(e, 'Could not create the note');
      if (btn) { btn.disabled = false; btn.textContent = 'Create note'; }
    }
  }
  async function offerCreate(title) {
    const ok = await confirmDialog(`Create “${truncate(title, 60)}”?`, 'No note has this title yet. Create it now, and the link will point to it.', 'Create note');
    if (ok) createFromLink(title);
  }

  function renderLinked(n) {
    const ids = Array.isArray(n.links) ? n.links : [];
    const rows = h('div', { class: 'rows' });
    if (!ids.length) rows.appendChild(h('div', { class: 'empty-list', text: 'No links yet. Use Link on a similar note to connect it.' }));
    for (const id of ids) {
      const unl = h('button', { class: 'btn btn-text btn-sm', type: 'button', title: 'Remove this link', text: 'Unlink' });
      unl.onclick = () => unlinkNotes(n.id, id, unl);
      rows.appendChild(h('div', { class: 'row' },
        h('span', { class: 'tdot ' + nodeType(id) }),
        h('div', { class: 'r-main', onclick: () => openNote(id, { center: true }), title: nodeTitle(id) },
          h('div', { class: 'r-title', text: nodeTitle(id) }), rowSub(id, nodeType(id))),
        h('div', { class: 'r-actions' },
          h('button', { class: 'btn btn-text btn-sm needs-claude', type: 'button', title: `Ask ${ai()} why these are connected`, text: 'Why', onclick: (e) => { if (!claudeBlocked(e.currentTarget)) openExplain(n.id, id); } }),
          unl)));
    }
    return section('Your links', ids.length || null, rows);
  }

  async function linkNotes(a, b, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Linking'; }
    try {
      const upd = await api('POST', `/api/notes/${encodeURIComponent(a)}/link`, { target: b });
      if (S.cur && S.cur.id === a && upd) { S.cur.links = upd.links || [...(S.cur.links || []), b]; rerenderLists(); }
      refreshGraph();
    } catch (e) {
      toastErr(e, 'Could not link');
      if (btn) { btn.disabled = false; btn.textContent = 'Link'; }
    }
  }
  async function unlinkNotes(a, b, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Unlinking'; }
    try {
      const upd = await api('DELETE', `/api/notes/${encodeURIComponent(a)}/link/${encodeURIComponent(b)}`);
      if (S.cur && S.cur.id === a) { S.cur.links = upd && upd.links ? upd.links : (S.cur.links || []).filter((x) => x !== b); rerenderLists(); }
      refreshGraph();
    } catch (e) {
      toastErr(e, 'Could not unlink');
      if (btn) { btn.disabled = false; btn.textContent = 'Unlink'; }
    }
  }
  function rerenderLists() {
    if (!S.cur) return;
    const rel = $('.section[data-sec="Similar"]', pbody), lin = $('.section[data-sec="Your links"]', pbody);
    if (rel) { const x = renderRelated(S.cur); rel.replaceWith(x); applyClaude(x); }
    if (lin) { const x = renderLinked(S.cur); lin.replaceWith(x); applyClaude(x); }
  }

  function renderDanger(n) {
    const del = h('button', { class: 'btn btn-danger', type: 'button', text: n.type === 'fragment' ? 'Delete fragment' : n.type === 'paper' ? 'Delete paper' : 'Delete note' });
    del.onclick = async () => {
      const nf = (n.fragments || []).length;
      const ok = await confirmDialog(`Delete “${truncate(n.title || n.id, 60)}”?`,
        `It${nf ? ` and its ${nf} fragment${nf > 1 ? 's' : ''}` : ''} will be moved to the vault's .trash folder, so you can still recover ${nf ? 'them' : 'it'} from disk.`);
      if (!ok) return;
      del.disabled = true;
      try {
        clearTimeout(S.saveTimer); S.saveTimer = null; S.patch = {};
        await api('DELETE', `/api/notes/${encodeURIComponent(n.id)}`);
        toast('Moved to trash', 'ok');
        const parent = n.type === 'fragment' ? n.parent : null;
        S.cur = null; S.selected = null;
        setPanelOpen(false);
        await refreshGraph();
        if (parent && S.nodes.has(parent)) openNote(parent);
      } catch (e) { toastErr(e, 'Could not delete'); del.disabled = false; }
    };
    return h('div', { class: 'danger-zone' },
      h('span', { class: 'hint', id: 'upd-stamp', title: fmtDate(n.updated), text: n.updated ? 'Edited ' + ago(n.updated) : '' }),
      del);
  }

  // ---------- discover ----------
  async function runDiscover(id, btn) {
    const key = 'discover:' + id;
    if (S.ops.has(key)) return;
    S.ops.set(key, { start: Date.now() });
    if (btn) { btn.disabled = true; btn.classList.add('busy'); }
    const slot = () => (S.cur && S.cur.id === id ? $('#discover-slot', pbody) : null);
    if (slot()) fillDiscover(slot(), id);
    try {
      const data = await api('GET', `/api/notes/${encodeURIComponent(id)}/discover`);
      const prev = S.discover.get(id);
      const tab = prev ? prev.tab : (data.references || []).length ? 'references' : (data.citations || []).length ? 'citations' : 'recommended';
      S.discover.set(id, { data, tab });
    } catch (e) {
      toastErr(e, 'Could not find related papers');
    } finally {
      S.ops.delete(key);
      if (btn && btn.isConnected) { btn.disabled = false; btn.classList.remove('busy'); }
      if (slot()) fillDiscover(slot(), id);
    }
  }

  function fillDiscover(slot, id) {
    const key = 'discover:' + id;
    const op = S.ops.get(key);
    const st = S.discover.get(id);
    if (!op && !st) { slot.replaceChildren(); return; }
    const kids = [];
    if (op) kids.push(working('Asking Semantic Scholar', op.start));
    if (st) {
      const tabsDef = [['references', 'References'], ['citations', 'Cited by'], ['recommended', 'Recommended']];
      const tabs = h('div', { class: 'tabs', role: 'tablist' }, tabsDef.map(([k, label]) =>
        h('button', { type: 'button', role: 'tab', class: st.tab === k ? 'on' : '', onclick: () => { st.tab = k; fillDiscover(slot, id); } },
          label, h('span', { class: 'n', text: String((st.data[k] || []).length) }))));
      const list = h('div', { class: 'rows' });
      const cands = st.data[st.tab] || [];
      if (!cands.length) list.appendChild(h('div', { class: 'empty-list', text: 'Semantic Scholar has nothing listed here.' }));
      for (const c of cands) list.appendChild(candidateRow(c, id, slot));
      kids.push(tabs, list);
    }
    const close = st ? head(h('button', { class: 'btn btn-sm btn-text', type: 'button', text: 'Hide', onclick: () => { S.discover.delete(id); fillDiscover(slot, id); } })) : null;
    slot.replaceChildren(section('Related papers', null, head(h('div', { class: 'spacer' })), close, kids));
  }

  function candidateRow(c, curId, slot) {
    const sub = [c.year, fmtAuthors(c.authors, 2)].filter(Boolean);
    let right;
    if (c.in_library) {
      right = h('button', { class: 'btn btn-sm btn-text', type: 'button', title: 'Already in your library. Open it.', text: 'In library', onclick: () => openNote(c.in_library, { center: true }) });
    } else {
      right = h('button', { class: 'btn btn-sm', type: 'button', title: 'Add to your library and link it to this note' }, 'Add');
      right.onclick = async () => {
        if (!c.s2_id) { toast("This paper has no Semantic Scholar id, so it can't be added from here. Try adding it by title.", 'error'); return; }
        right.disabled = true; right.replaceChildren(h('span', { class: 'spin' }), 'Adding');
        try {
          const note = await api('POST', '/api/import/s2', { s2_id: c.s2_id, link_to: curId });
          c.in_library = note.id;
          if (note.duplicate) toast('Already in your library. Linked it to this note.', 'info');
          else toast(`Added “${truncate(note.title, 60)}”`, 'ok', { action: { label: 'Open', fn: () => openNote(note.id, { center: true }) } });
          await refreshGraph();
          if (S.cur && S.cur.id === curId) {
            if (!(S.cur.links || []).includes(note.id)) S.cur.links = [...(S.cur.links || []), note.id];
            rerenderLists();
          }
        } catch (e) {
          toastErr(e, 'Could not add the paper');
        } finally {
          if (slot.isConnected) fillDiscover(slot, curId);
        }
      };
    }
    const title = h('div', { class: 'r-title', text: c.title || '(untitled)' });
    const main = h('div', { class: 'r-main', title: c.title || '' }, title, h('div', { class: 'r-sub' }, sub.map((x) => h('span', { text: String(x) }))));
    if (c.in_library) main.onclick = () => openNote(c.in_library, { center: true });
    else if (c.url && /^https?:/i.test(c.url)) main.onclick = () => window.open(c.url, '_blank', 'noopener');
    else main.style.cursor = 'default';
    return h('div', { class: 'row cand' },
      h('span', { class: 'tdot ' + (c.in_library ? 'paper' : 'hollow') }),
      main,
      h('span', { class: 'cites', title: 'Citations', text: c.citationCount != null ? fmtNum(c.citationCount) + ' cit.' : '' }),
      h('div', { class: 'r-actions' }, right));
  }
  const fmtNum = (x) => (x >= 10000 ? Math.round(x / 1000) + 'k' : x >= 1000 ? (x / 1000).toFixed(1) + 'k' : String(x));

  // ======================================================================
  // Explain ("why are these connected?")
  // ======================================================================
  async function openExplain(a, b, refresh = false) {
    if (!a || !b) return;
    const box = $('#explain');
    const key = linkKey(a, b);
    S.explain = { a, b, key };
    box.hidden = false;
    const pair = $('#explain-pair');
    const mk = (id) => h('button', { type: 'button', title: nodeTitle(id), onclick: () => openNote(id, { center: true }) }, h('span', { class: 'tdot ' + nodeType(id) }), h('span', { class: 't', text: nodeTitle(id) }));
    pair.replaceChildren(mk(a), h('span', { class: 'and', text: 'and' }), mk(b));
    const body = $('#explain-body');
    const cached = S.explainCache.get(key);
    if (cached && !refresh) { showExplain(cached); applyClaude(box); return; }
    if (!S.claudeOK) {
      body.replaceChildren(h('div', { class: 'empty-list', text: `${ai()} isn't available. ${S.claudeWhy}` }));
      applyClaude(box); return;
    }
    const opKey = 'explain:' + key;
    if (S.ops.has(opKey)) { body.replaceChildren(h('div', { class: 'machine' }, working(`${ai()} is comparing the two`, S.ops.get(opKey).start))); return; }
    const start = Date.now();
    S.ops.set(opKey, { start });
    body.replaceChildren(h('div', { class: 'machine' }, working(`${ai()} is comparing the two`, start)));
    const rb = $('#btn-explain-refresh'); rb.classList.add('busy'); applyClaude(box);
    try {
      const r = await api('POST', '/api/explain', { a, b, refresh: !!refresh });
      S.explainCache.set(key, r);
      r.at = r.at || new Date().toISOString();
      if (S.explain && S.explain.key === key && !box.hidden) showExplain(r);
    } catch (e) {
      toastErr(e, `${ai()} could not explain this link`);
      if (S.explain && S.explain.key === key) body.replaceChildren(h('div', { class: 'machine' }, h('div', { class: 'msg error' }, h('div', { class: 'msg-body', text: e.message }))));
    } finally {
      S.ops.delete(opKey);
      rb.classList.remove('busy'); applyClaude(box);
    }
  }
  function showExplain(r) {
    $('#explain-body').replaceChildren(h('div', { class: 'machine' },
      h('div', { class: 'machine-head' }, h('span', { text: r.cached ? `${ai()}'s explanation, saved earlier` : `${ai()}'s explanation, ${ago(r.at)}` })),
      mdEl(r.text)));
  }
  function closeExplain() { $('#explain').hidden = true; S.explain = null; }

  // ======================================================================
  // Chat
  // ======================================================================
  const chatLog = $('#chat-log');
  function openChat() {
    $('#chat').hidden = false;
    document.body.classList.add('chat-open');
    renderChat();
    setTimeout(() => $('#chat-input').focus(), 0);
  }
  function closeChat() { $('#chat').hidden = true; document.body.classList.remove('chat-open'); }
  /** Switch the chat to talk about one paper (id) or back to the whole library (null). Each scope keeps its
   *  own conversation. */
  // a paper's conversation is kept in localStorage (rb.paperChat.<id>), shared with the PDF viewer's Ask tab
  const PAPER_THREAD_MAX = 40;
  function loadPaperThread(id) {
    try {
      const t = JSON.parse(localStorage.getItem(`rb.paperChat.${id}`) || 'null');
      if (Array.isArray(t)) return t.map((m) => ({ ...m, passages: [] }));
    } catch { /* storage unavailable */ }
    return S.chatThreads[id] || [];
  }
  function savePaperThread(id, thread) {
    S.chatThreads[id] = thread;
    const keep = thread.filter((m, i, a) => !m.pending && !m.error && !m.failed && !(a[i + 1] && a[i + 1].error)).slice(-PAPER_THREAD_MAX)
      .map((m) => ({ role: m.role, content: m.content, ...(m.sel ? { sel: m.sel } : {}), ...(m.sources ? { sources: m.sources } : {}) }));
    try { keep.length ? localStorage.setItem(`rb.paperChat.${id}`, JSON.stringify(keep)) : localStorage.removeItem(`rb.paperChat.${id}`); } catch { /* storage unavailable */ }
  }
  window.addEventListener('storage', (e) => {
    if (S.chatPaper && !S.chatBusy && e.key === `rb.paperChat.${S.chatPaper.id}`) { S.chat = loadPaperThread(S.chatPaper.id); renderChat(); }
  });
  function setChatScope(id) {
    if (S.chatBusy) { toast(`Wait for ${ai()} to finish the current answer first.`, 'info'); return false; }
    if (S.chatPaper) savePaperThread(S.chatPaper.id, S.chat); else S.chatThreads[''] = S.chat;
    if (id) {
      const t = (S.cur && S.cur.id === id && S.cur.title) || nodeTitle(id) || 'this paper';
      S.chatPaper = { id, title: t };
    } else S.chatPaper = null;
    S.chat = id ? loadPaperThread(id) : (S.chatThreads[''] || []);
    renderChatScope();
    return true;
  }
  function chatAboutPaper(id) {
    if (!id) return;
    if (setChatScope(id) === false) return;
    if (window.__rbResearch && window.__rbResearch.setMode) window.__rbResearch.setMode('quick');
    openChat();
  }
  function renderChatScope() {
    const bar = $('#chat-scope'), title = $('.chat-title');
    const ta = $('#chat-input');
    if (!bar) return;
    if (!S.chatPaper) {
      bar.hidden = true;
      if (title) title.textContent = 'Ask your library';
      if (ta) ta.placeholder = 'Ask about your papers and notes';
      document.body.classList.remove('chat-scoped');
      return;
    }
    bar.hidden = false;
    document.body.classList.add('chat-scoped');
    if (title) title.textContent = 'This paper';
    $('.cs-title', bar).textContent = S.chatPaper.title;
    $('.cs-title', bar).title = S.chatPaper.title;
    $('#cs-related').checked = !!S.chatRelated;
    if (ta) ta.placeholder = 'Ask anything about this paper';
  }

  function renderChat() {
    const kids = [];
    if (!S.chat.length && S.chatPaper) {
      const examples = [
        'What is the main result, and what evidence supports it?',
        'Walk me through the key derivation step by step.',
        'What assumptions does this rely on, and where could it break?',
        'How does this compare with what I wrote in my summary?',
      ];
      kids.push(h('div', { class: 'chat-empty' },
        `${ai()} reads this paper (and your notes on it) and answers with page citations. Your private notes are never included.`,
        examples.map((x) => h('button', { type: 'button', class: 'ex', text: x, onclick: () => { const ta = $('#chat-input'); ta.value = x; ta.dispatchEvent(new Event('input')); ta.focus(); } }))));
    } else if (!S.chat.length) {
      const examples = [
        'Which papers in my library discuss plasma instabilities, and how do their approaches differ?',
        'Summarise what my notes say about attention mechanisms.',
        'What open questions come up repeatedly across my notes?',
      ];
      kids.push(h('div', { class: 'chat-empty' },
        `${ai()} answers from your papers and notes, and cites the ones it used. Click a citation to open it.`,
        examples.map((x) => h('button', { type: 'button', class: 'ex', text: x, onclick: () => { const ta = $('#chat-input'); ta.value = x; ta.dispatchEvent(new Event('input')); ta.focus(); } }))));
    }
    for (const m of S.chat) rememberSections(m.passages);
    S.chat.forEach((m, i) => {
      const ext = m.mode === 'research' && window.__rbResearch ? window.__rbResearch.renderMsg(m, i) : null; // static/research.js
      if (ext) { kids.push(ext); return; }
      if (m.role === 'user') {
        kids.push(h('div', { class: 'msg user' }, h('div', { class: 'msg-role', text: m.sel ? `You · selection on p. ${m.sel.page}` : 'You' }),
          m.sel ? h('div', { class: 'msg-sel', text: m.sel.text }) : null,
          h('div', { class: 'msg-body md', html: renderMD(m.content) })));
      } else if (m.pending && m.live) { // streaming: the answer so far (re-rendered by sendChat)
        kids.push(h('div', { class: 'msg assistant' }, h('div', { class: 'machine streaming', 'aria-live': 'polite' },
          h('div', { class: 'machine-head' }, h('span', { text: `${ai()}, writing…` })),
          h('div', { class: 'msg-body md chat-live', html: partialMD(m.live) }))));
      } else if (m.pending) {
        kids.push(h('div', { class: 'msg assistant' }, h('div', { class: 'machine' }, working(S.chatPaper ? `${ai()} is reading the paper` : `${ai()} is reading your library`, m.start))));
      } else if (m.error) {
        kids.push(h('div', { class: 'msg error' }, h('div', { class: 'msg-role', text: `${ai()} couldn't answer` }), h('div', { class: 'msg-body', text: m.content }),
          h('div', { class: 'actions' }, h('button', { class: 'btn btn-sm needs-claude', type: 'button', text: 'Retry', onclick: () => retryChat(i) }))));
      } else {
        const src = (m.sources || []).length ? h('div', { class: 'sources' }, h('span', { class: 'lbl', text: 'Sources' }),
          m.sources.map((s) => h('button', { type: 'button', class: 'src', title: s.title, onclick: () => openNote(s.id, { center: true }) },
            h('span', { class: 'tdot ' + nodeType(s.id) }), h('span', { class: 't', text: s.title || s.id }),
            s.score != null ? h('span', { class: 's', text: Number(s.score).toFixed(2) }) : null))) : null;
        kids.push(h('div', { class: 'msg assistant' }, h('div', { class: 'machine' },
          h('div', { class: 'machine-head' }, h('span', { text: ai() })),
          h('div', { class: 'msg-body md', html: renderMD(m.content) }), src, chatPassages(m))));
      }
    });
    chatLog.replaceChildren(...kids);
    chatLog.scrollTop = chatLog.scrollHeight;
    applyClaude($('#chat'));
    const send = $('#btn-chat-send');
    send.classList.toggle('busy', S.chatBusy);
    applyClaude($('#chat'));
  }

  /** The passages chat retrieved: page + one-line excerpt; a paper page opens the PDF viewer there. */
  function chatPassages(m) {
    const ps = m.passages || [];
    if (!ps.length) return null;
    const SHOW = 4;
    const multi = new Set(ps.map((p) => p.id)).size > 1; // name the note only when passages span several
    const row = (p) => {
      const excerpt = String(p.text || '').replace(/^(Abstract|Student's (summary|notes)|Quoted passage): /, '').replace(/\s+/g, ' ');
      const where = p.page ? `p. ${p.page}` : p.section ? `§${truncate(p.section, 14)}` : p.src === 'fragment' ? 'fragment' : p.src === 'text' ? 'text' : 'note';
      const attrs = { class: 'psg', title: `${p.title}${p.page ? `, page ${p.page}` : p.section ? `, ${p.section_title || '§' + p.section}` : ''}${p.linked ? ' (opens the article)' : ''}\n\n${truncate(excerpt, 400)}` };
      const kids = [h('span', { class: 'pg', text: where }), h('span', { class: 'x' }, multi ? h('span', { class: 'pt', text: truncate(p.title || p.id, 32) }) : null, multi ? ' ' : null, excerpt)];
      if (p.pdf && p.page) return h('a', { ...attrs, href: `/viewer/${encodeURIComponent(p.pdf)}#page=${p.page}`, target: '_blank', rel: 'noopener' }, ...kids);
      if (p.linked && httpUrl(p.url) && p.src === 'text') return h('a', { ...attrs, href: httpUrl(p.url), target: '_blank', rel: 'noopener noreferrer' }, ...kids);
      return h('button', { ...attrs, type: 'button', onclick: () => openNote(p.id, { center: true }) }, ...kids);
    };
    const box = h('div', { class: 'passages' }, h('span', { class: 'lbl', text: `Passages ${ai()} read` }), ps.slice(0, SHOW).map(row));
    if (ps.length > SHOW) {
      const more = h('button', { type: 'button', class: 'psg-more', text: `Show ${ps.length - SHOW} more`,
        onclick: () => { more.replaceWith(...ps.slice(SHOW).map(row)); } });
      box.append(more);
    }
    return box;
  }

  async function sendChat(question, opts = {}) {
    if (S.chatBusy) return;
    if (claudeBlocked()) return;
    question = question.trim();
    if (!question) return;
    // Research mode searches the whole library; a chat about one paper always answers from that paper
    if (!S.chatPaper && window.__rbResearch && window.__rbResearch.wants(opts)) return window.__rbResearch.send(question);
    const scope = S.chatPaper ? { paper: S.chatPaper.id, include_related: !!S.chatRelated } : {};
    if (S.chatPaper) S.chat = loadPaperThread(S.chatPaper.id); // pick up what was asked in the viewer meanwhile
    const history = S.chat.filter((m) => !m.pending && !m.error && !m.failed)
      .map((m) => ({ role: m.role, content: m.sel ? `[about the passage on p. ${m.sel.page}: "${String(m.sel.text).slice(0, 300)}"]\n${m.content}` : m.content }));
    const userMsg = { role: 'user', content: question };
    const pending = { role: 'assistant', pending: true, start: Date.now() };
    S.chat.push(userMsg, pending);
    S.chatBusy = true;
    renderChat();
    // paint only the live bubble while streaming, and keep the log pinned to the bottom unless scrolled up
    const live = streamRenderer((text) => {
      pending.live = text;
      const el = $$('.chat-live', chatLog).pop();
      if (!el) { renderChat(); return; }
      const atEnd = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 40;
      el.innerHTML = partialMD(text);
      if (atEnd) chatLog.scrollTop = chatLog.scrollHeight;
    });
    try {
      let r;
      try {
        r = await apiStream('/api/chat/stream', { question, history, ...scope }, (t) => {
          if (!pending.live) { pending.live = t; renderChat(); }
          live.push(t);
        });
        live.flush();
      } catch (e) {
        live.cancel();
        if (e.claude) throw e;
        console.warn('chat streaming failed, retrying without streaming:', e);
        pending.live = '';
        renderChat();
        r = await api('POST', '/api/chat', { question, history, ...scope });
      }
      delete pending.live;
      Object.assign(pending, { pending: false, content: r.answer || '', sources: r.sources || [], passages: r.passages || [] });
    } catch (e) {
      userMsg.failed = true;
      delete pending.live;
      Object.assign(pending, { pending: false, error: true, content: e.message, question });
      toastErr(e, `${ai()} couldn't answer`);
    } finally {
      S.chatBusy = false;
      if (S.chatPaper) savePaperThread(S.chatPaper.id, S.chat);
      renderChat();
    }
  }
  function retryChat(i) {
    const m = S.chat[i];
    if (!m || !m.error || S.chatBusy) return;
    const q = m.question;
    S.chat.splice(i - 1, 2); // drop failed user+error pair
    sendChat(q);
  }

  $('#chat-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const ta = $('#chat-input');
    if (S.chatBusy) { toast(`${ai()} is still answering the last question.`, 'info'); return; }
    if (!ta.value.trim()) return;
    if (!S.claudeOK) { claudeBlocked(); return; }
    const q = ta.value; ta.value = ''; ta.style.height = '';
    $('#chat-preview').innerHTML = '';
    sendChat(q);
  });
  // LaTeX helper first, so its autocomplete gets Enter before "send" does
  const chatLE = latex($('#chat-input'), $('#chat-preview'));
  $('#chat-input').addEventListener('input', () => { const p = $('#chat-preview'); if (!/[$\\]/.test($('#chat-input').value)) p.innerHTML = ''; });
  $('#chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !e.defaultPrevented) { e.preventDefault(); $('#chat-form').requestSubmit(); }
  });
  void chatLE;
  $('#chat-input').addEventListener('input', (e) => { const t = e.target; t.style.height = 'auto'; t.style.height = Math.min(160, t.scrollHeight + 2) + 'px'; });
  $('#btn-chat-clear').addEventListener('click', () => { if (S.chatBusy) { toast('Wait for the current answer to finish first.', 'info'); return; } S.chat = []; if (S.chatPaper) savePaperThread(S.chatPaper.id, S.chat); renderChat(); });
  $('#cs-clear').addEventListener('click', () => { if (setChatScope(null) !== false) { renderChat(); $('#chat-input').focus(); } });
  $('#cs-open').addEventListener('click', () => S.chatPaper && openNote(S.chatPaper.id, { center: true }));
  $('#cs-related').addEventListener('change', (e) => { S.chatRelated = e.target.checked; });
  $('#btn-chat-close').addEventListener('click', closeChat);
  $('#btn-chat').addEventListener('click', () => {
    if (!$('#chat').hidden) { closeChat(); return; }
    openChat();
  });

  // ======================================================================
  // Imports
  // ======================================================================
  async function importPdfs(files) {
    const pdfs = Array.from(files || []).filter((f) => f && (f.type === 'application/pdf' || /\.pdf$/i.test(f.name)));
    if (!pdfs.length) { toast('Only PDF files can be added. Drop a .pdf file.', 'error'); return; }
    let last = null;
    const seq0 = openSeq;
    for (const f of pdfs) {
      const t = task(f.name);
      try {
        const note = await upload('/api/import/pdf', f, (p) => t.progress(p));
        last = note;
        if (note.duplicate) toast(`Already in your library: “${truncate(note.title, 60)}”`, 'info');
        else toast(`Added “${truncate(note.title, 70)}”`, 'ok');
      } catch (e) {
        toastErr(e, `Could not add ${f.name}`);
      } finally { t.done(); }
    }
    if (last) { await refreshGraph(); openOrOffer(last, seq0); }
  }

  /** Open a freshly imported note unless the user opened something else meanwhile (then offer it). */
  function openOrOffer(note, seq0) {
    const busy = openSeq !== seq0 && !panel.hidden && (Object.keys(S.patch).length || S.saveTimer || (document.activeElement && document.activeElement.closest && document.activeElement.closest('#panel')));
    if (busy || (openSeq !== seq0 && !panel.hidden)) {
      toast(`Ready: “${truncate(note.title, 60)}”`, 'ok', { action: { label: 'Open', fn: () => openNote(note.id, { center: true }) } });
    } else openNote(note.id, { center: true });
  }

  async function attachPdf(id, file, btn) {
    if (!(file.type === 'application/pdf' || /\.pdf$/i.test(file.name))) { toast('That file is not a PDF. Choose a .pdf file.', 'error'); return; }
    const t = task('Attach: ' + file.name);
    if (btn) btn.disabled = true;
    try {
      await upload(`/api/notes/${encodeURIComponent(id)}/pdf`, file, (p) => t.progress(p));
      toast('PDF attached', 'ok');
      if (S.cur && S.cur.id === id) await reloadCur();
      refreshGraph();
    } catch (e) {
      toastErr(e, 'Could not attach the PDF');
      if (btn && btn.isConnected) btn.disabled = false;
    } finally { t.done(); }
  }

  /** "Get the PDF" on a linked paper: (a) a free legal copy (the server's open-access lookup), else
   *  (b) the browser extension's "Save PDF too" on the article page, which uses the user's own access. */
  async function getPdf(n, btn) {
    const id = n.id;
    const extHint = 'Open the article and click “Save PDF too” in the research_brain browser extension while signed in to the site.';
    if (btn) { btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' }), 'Looking for a free copy'); }
    const reset = () => { if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Get the PDF'; } };
    const hint = () => {
      if (btn && btn.isConnected && btn.parentNode && !btn.parentNode.parentNode.querySelector('.get-pdf-hint')) btn.parentNode.after(h('p', { class: 'get-pdf-hint', text: extHint }));
    };
    let st;
    try { st = await api('POST', `/api/notes/${encodeURIComponent(id)}/acquire`); }
    catch (e) { reset(); hint(); toast(`Open-access lookup isn’t available (${e.message}). ${extHint}`, 'info'); return; }
    for (let i = 0; st && st.state === 'running' && i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try { st = await api('GET', `/api/notes/${encodeURIComponent(id)}/acquire`); } catch { break; }
    }
    if (st && (st.state === 'found' || st.state === 'has-pdf' || st.has_pdf)) {
      toast('Found a free copy. The PDF is attached.', 'ok');
      if (S.cur && S.cur.id === id) await reloadCur();
      refreshGraph();
      return;
    }
    reset(); hint();
    toast(`No free copy found. ${extHint}`, 'info');
  }

  /** "Refresh metadata": POST /api/notes/{id}/refresh-metadata (Crossref / arXiv / PubMed / ... by identifier). */
  async function refreshMetadata(id, btn) {
    if (btn) { btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' }), 'Looking up'); }
    try {
      const out = await api('POST', `/api/notes/${encodeURIComponent(id)}/refresh-metadata`, { keep_edits: true });
      const m = out.metadata || {};
      const names = { crossref: 'Crossref', datacite: 'DataCite', arxiv: 'arXiv', pubmed: 'PubMed', openlibrary: 'Open Library', osti: 'OSTI', ntrs: 'NASA NTRS', 'translation-server': 'Zotero translation-server', s2: 'Semantic Scholar', page: 'the article page' };
      if (!m.found) toast('No record found for this paper’s identifiers. You can edit the details by hand.', 'info');
      else toast(m.changed ? `Metadata updated from ${names[m.source] || m.source}.` : `Metadata already matches ${names[m.source] || m.source}.`, 'ok');
      if (S.cur && S.cur.id === id) await reloadCur();
      refreshGraph();
    } catch (e) {
      toastErr(e, 'Could not refresh the metadata');
    } finally {
      if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = 'Refresh metadata'; }
    }
  }

  async function lookup(query, btn, input) {
    query = (query || '').trim();
    if (!query) { input && input.focus(); toast('Type a DOI, arXiv id, URL or title first.', 'info'); return; }
    const label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' }), 'Adding'); }
    const t = task('Lookup: ' + truncate(query, 40));
    const seq0 = openSeq;
    t.label('Looking up');
    try {
      const note = await api('POST', '/api/import/lookup', { query });
      if (input) input.value = '';
      if (note.duplicate) toast(`Already in your library: “${truncate(note.title, 60)}”`, 'info');
      else toast(`Added “${truncate(note.title, 70)}”`, 'ok');
      await refreshGraph();
      openOrOffer(note, seq0);
    } catch (e) {
      toastErr(e, 'Could not add the paper');
    } finally {
      t.done();
      if (btn) { btn.disabled = false; btn.textContent = label; }
    }
  }

  async function newNote() {
    const btn = $('#btn-new-note');
    btn.disabled = true;
    // Until the new note opens, the previous note's fields must not take keystrokes: otherwise typing
    // right after "Write a note" renames whatever was open before.
    const pb = $('#panel-body');
    if (document.activeElement && pb && pb.contains(document.activeElement)) document.activeElement.blur();
    await flushSave();
    if (pb) pb.inert = true;
    try {
      const note = await api('POST', '/api/notes', { type: 'note', title: 'Untitled note' });
      await refreshGraph();
      S.bodyMode[note.id] = 'edit';
      if (pb) pb.inert = false;  // openNote renders the new note and focuses its title
      await openNote(note.id, { center: true, focusTitle: true });
    } catch (e) { toastErr(e, 'Could not create the note'); }
    finally { btn.disabled = false; if (pb) pb.inert = false; }
  }

  // ======================================================================
  // Wiring
  // ======================================================================
  // ---------- top-bar menus (Add / View) ----------
  const menus = [['#btn-add-menu', '#add-menu'], ['#btn-view-menu', '#view-menu']];
  function closeMenus(except) {
    let closed = false;
    for (const [b, m] of menus) {
      if (m === except || $(m).hidden) continue;
      $(m).hidden = true; $(b).setAttribute('aria-expanded', 'false'); closed = true;
    }
    return closed;
  }
  function openMenu(btnSel, menuSel) {
    closeMenus(menuSel);
    $(menuSel).hidden = false; $(btnSel).setAttribute('aria-expanded', 'true');
    const first = $('input:not([type=checkbox]):not([type=range]), .menu-item, input', $(menuSel));
    if (menuSel === '#add-menu') { const i = $('#lookup-input'); i.focus(); i.select(); }
    else if (first && first.focus) first.focus({ preventScroll: true });
  }
  for (const [b, m] of menus) {
    $(b).addEventListener('click', (e) => { e.stopPropagation(); if ($(m).hidden) openMenu(b, m); else closeMenus(); });
  }
  document.addEventListener('pointerdown', (e) => { if (!e.target.closest('.menu-wrap')) closeMenus(); });

  $('#btn-add-pdf').addEventListener('click', () => { closeMenus(); $('#pdf-input').click(); });
  $('#pdf-input').addEventListener('change', (e) => { const fs = Array.from(e.target.files || []); e.target.value = ''; if (fs.length) importPdfs(fs); });
  $('#lookup-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const q = $('#lookup-input').value.trim();
    if (q) closeMenus();  // progress + result show as a task and toast
    await lookup(q, $('#btn-lookup'), $('#lookup-input'));
  });
  $('#empty-lookup-form').addEventListener('submit', (e) => { e.preventDefault(); lookup($('#empty-lookup-input').value, $('button', e.currentTarget), $('#empty-lookup-input')); });
  $('#btn-new-note').addEventListener('click', () => { closeMenus(); newNote(); });
  $('#empty-state').addEventListener('click', (e) => {
    const a = e.target.closest('[data-action]');
    if (!a) return;
    if (a.dataset.action === 'pick-pdf') $('#pdf-input').click();
    if (a.dataset.action === 'new-note') newNote();
  });
  $('#btn-panel-close').addEventListener('click', closePanel);
  $('#btn-explain-close').addEventListener('click', closeExplain);
  $('#btn-explain-refresh').addEventListener('click', (e) => {
    if (claudeBlocked(e.currentTarget) || !S.explain) return;
    openExplain(S.explain.a, S.explain.b, true);
  });
  $('#btn-zoom-fit').addEventListener('click', () => { if (S.focusCluster != null) unfocusRegion(); else if (fg) fg.zoomToFit(T(600), 70); });
  graphEl.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch') return;
    const r = S.hover ? null : labelHit(e);
    const id = r ? r.cid : null;
    if (id !== (S.hoverRegion ?? null)) { S.hoverRegion = id; graphEl.style.cursor = r ? 'pointer' : (S.hover ? 'pointer' : ''); kick(50); }
  });
  graphEl.addEventListener('pointerleave', () => { if (S.hoverRegion != null) { S.hoverRegion = null; kick(50); } });
  $('#rf-show-all') && $('#rf-show-all').addEventListener('click', () => unfocusRegion());
  $('#btn-regions') && $('#btn-regions').addEventListener('click', (e) => { e.stopPropagation(); chooseRegion(); });

  // search
  const searchInput = $('#search-input');
  searchInput.addEventListener('input', () => { S.search = searchInput.value; updateSearch(); });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      updateSearch();
      const first = (S.ranked || [])[0];
      if (first) openNote(first.id, { center: true });
      else if (S.search.trim()) toast('Nothing matches that search.', 'info', { ms: 2000 });
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      if (searchInput.value) { searchInput.value = ''; S.search = ''; updateSearch(); }
      else searchInput.blur();
    }
  });

  // threshold + toggle
  $('#sim-threshold').addEventListener('input', (e) => setThreshold(e.target.value));
  $('#sim-toggle').addEventListener('change', (e) => setShowSim(e.target.checked));
  const citesToggle = $('#cites-toggle');
  if (citesToggle) {
    citesToggle.checked = S.showCites;
    citesToggle.addEventListener('change', (e) => {
      S.showCites = e.target.checked; LS.set('showCites', S.showCites);
      configureLinkForce(); simNudge();
    });
  }
  $('#ambient-toggle').checked = S.ambient;
  $('#ambient-toggle').addEventListener('change', (e) => { S.ambient = e.target.checked; LS.set('ambient', S.ambient); });

  // global citation chips ([[id]]) anywhere
  document.addEventListener('click', (e) => {
    const c = e.target.closest('[data-open]');
    if (c && !c.classList.contains('missing')) { e.preventDefault(); openNote(c.dataset.open, { center: true }); }
    const u = !c && e.target.closest('[data-create]');
    if (u) { e.preventDefault(); e.stopPropagation(); offerCreate(u.dataset.create); }
  });

  // keyboard: commands live in the registry (static/commands.js; most are in static/shortcuts.js).
  // Esc closes the innermost open layer first: dialog, menu, explanation, top-bar field, chat, panel.
  function addById() {
    if (!$('#empty-state').hidden) { const inp = $('#empty-lookup-input'); inp.focus(); inp.select(); return; }
    openMenu('#btn-add-menu', '#add-menu');
  }
  const CMD = window.RB && window.RB.commands;
  if (CMD) {
    const inside = (c, sel) => !!(c && c.typing && c.target && c.target.closest && c.target.closest(sel));
    CMD.layer({ id: 'app.modal', order: 100, isOpen: () => !!S.modalClose, close: () => S.modalClose() });
    CMD.layer({ id: 'app.menus', order: 90, isOpen: () => menus.some(([, m]) => !$(m).hidden), close: (c) => { closeMenus(); if (c.typing) c.target.blur(); } });
    CMD.layer({ id: 'app.explain', order: 70, isOpen: () => !$('#explain').hidden, close: closeExplain });
    CMD.layer({ id: 'app.topbar-field', order: 60, isOpen: (c) => inside(c, '#topbar'), close: (c) => c.target.blur() });
    CMD.layer({ id: 'app.chat', order: 50, isOpen: (c) => !$('#chat').hidden && (!(c && c.typing) || inside(c, '#chat')), close: closeChat });
    CMD.layer({ id: 'app.panel', order: 40, isOpen: () => !panel.hidden, close: (c) => { if (c.typing) c.target.blur(); closePanel(); } });
    CMD.layer({ id: 'app.region-chooser', order: 95, isOpen: () => !!S.regionChooserClose, close: () => S.regionChooserClose() });
    CMD.layer({ id: 'app.region-focus', order: 35, isOpen: () => S.focusCluster != null, close: () => unfocusRegion() });
    CMD.register({ id: 'graph.focusRegion', title: 'Focus a region…', group: 'Graph', keys: ['G then C'],
      aliases: ['cluster', 'topic', 'zoom to region', 'isolate', 'only this group'],
      description: 'Zooms to one region and dims the rest. Click a region\'s name on the graph to do the same; Esc shows everything again.',
      run: chooseRegion });
    CMD.register({ id: 'chat.aboutPaper', title: 'Ask about this paper', group: 'Note',
      when: () => !!(S.cur && S.cur.type === 'paper'),
      aliases: ['talk about paper', 'chat with paper', 'discuss', 'explain this paper', 'question this paper'],
      description: 'Opens a conversation about the open paper only: the agent reads the paper and your notes on it.',
      run: () => chatAboutPaper(S.cur.id) });
    CMD.register({ id: 'chat.wholeLibrary', title: 'Ask about the whole library', group: 'Research',
      when: () => !!S.chatPaper, description: 'Switches the chat back from one paper to your whole library.',
      run: () => { if (setChatScope(null) !== false) { renderChat(); openChat(); } } });
    CMD.register({ id: 'graph.showAllRegions', title: 'Show all regions', group: 'Graph', when: () => S.focusCluster != null,
      description: 'Leaves region focus and fits the whole library.', run: () => unfocusRegion() });
    CMD.register({ id: 'nav.search', title: 'Search your library', group: 'Navigate', keys: '/', aliases: ['find', 'filter', 'look up'],
      description: 'Highlights matching papers, notes and fragments. Enter opens the first match.',
      run: () => { searchInput.focus(); searchInput.select(); } });
    CMD.register({ id: 'library.addById', title: 'Add paper by DOI, arXiv id or title…', group: 'Library', keys: ['Mod+Shift+A', 'G then A'], allowInInput: true,
      aliases: ['doi', 'arxiv', 'import', 'lookup', 'new paper', 'semantic scholar'],
      description: 'Looks the paper up on Semantic Scholar and adds it, with its abstract.', run: addById });
  }

  // drag & drop PDFs
  let dragDepth = 0;
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  window.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; $('#drop-overlay').hidden = false; });
  window.addEventListener('dragover', (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  window.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('#drop-overlay').hidden = true; });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth = 0; $('#drop-overlay').hidden = true;
    importPdfs(e.dataTransfer.files);
  });

  // panel resize
  (() => {
    const rz = $('#panel-resizer');
    const setW = (w) => { w = Math.max(340, Math.min(Math.round(window.innerWidth * 0.75), w)); document.documentElement.style.setProperty('--panel-w', w + 'px'); return w; };
    const saved = LS.get('panelW', null);
    if (saved && window.innerWidth > 860) setW(saved);
    rz.addEventListener('pointerdown', (e) => {
      e.preventDefault(); rz.setPointerCapture(e.pointerId); rz.classList.add('drag');
      const move = (ev) => setW(window.innerWidth - ev.clientX);
      const up = (ev) => { rz.classList.remove('drag'); rz.removeEventListener('pointermove', move); rz.removeEventListener('pointerup', up); LS.set('panelW', setW(window.innerWidth - ev.clientX)); };
      rz.addEventListener('pointermove', move); rz.addEventListener('pointerup', up);
    });
    rz.addEventListener('dblclick', () => { document.documentElement.style.removeProperty('--panel-w'); LS.set('panelW', null); });
  })();

  // ======================================================================
  // Theme (light / dark / system)
  // ======================================================================
  function applyTheme(mode) {
    const root = document.documentElement;
    if (mode === 'light' || mode === 'dark') root.dataset.theme = mode; else delete root.dataset.theme;
    LS.set('theme', mode === 'light' || mode === 'dark' ? mode : null);
    for (const b of $$('.theme button')) b.classList.toggle('on', b.dataset.theme === (mode || 'system'));
    readTokens();
    kick(300);
  }
  $$('.theme button').forEach((b) => b.addEventListener('click', () => applyTheme(b.dataset.theme)));
  if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { readTokens(); });

  // ======================================================================
  // Boot
  // ======================================================================
  async function boot() {
    applyTheme(LS.get('theme', null) || 'system');
    setShowSim(S.showSim);
    $('#sim-threshold-val').textContent = S.threshold.toFixed(2);
    initGraph();
    await loadStatus();
    await refreshGraph();
    // deep links: the viewer, its fragment cards and the Chrome extension link to /#note=<id>
    const openFromHash = () => {
      const m = /(?:^#|&)note=([^&]+)/.exec(location.hash || '');
      if (!m) return;
      let id = m[1];
      try { id = decodeURIComponent(id); } catch { /* keep raw */ }
      history.replaceState(null, '', location.pathname + location.search);
      openNote(id, { center: true });
    };
    openFromHash();
    window.addEventListener('hashchange', openFromHash);
    setInterval(loadStatus, 30000);
    // refresh when returning to the tab (e.g. after clipping from the browser extension / viewer)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        refreshGraph();
        loadStatus();
        if (S.cur && !S.saveTimer && !Object.keys(S.patch).length && !document.activeElement.closest?.('#panel')) reloadCur();
      }
    });
  }
  // expose for debugging/tests
  window.__rb = { S, renderMD, refreshGraph, openNote, get fg() { return fg; } };
  // hooks for static/review.js and static/views.js (review mode, critique history, graph views)
  Object.assign(window.__rb, {
    h, api, toast, toastErr, latex, mdEl, ago, fmtDate, truncate, kick, closeMenus, flushSave, reloadCur, nodeRadius, applyClaude, loadStatus,
    setNodeFilter(fn) { S.nodeFilter = typeof fn === 'function' ? fn : null; kick(400); },
    setHidden(set) { S.hidden = set && set.size ? set : null; S.focusCache = null; configureLinkForce(); kick(400); },
    renderChat, partialMD, streamRenderer, chatPassages, apiStream, openChat, sendChat, chatAboutPaper, setChatScope, nodeType, working, claudeBlocked, confirmDialog,
    rerenderCritique() { const s = S.cur && $('#summary-sec', pbody); if (s && s.__refresh) s.__refresh(); },
    // static/topics.js: run fn(note, panelBody) after every panel render; queueSave patches the open note
    onPanelRender(fn) { (S.panelHooks || (S.panelHooks = [])).push(fn); if (S.cur && !panel.hidden) fn(S.cur, pbody); },
    queueSave, centerOn, closePanel,
    // static/questions.js: a note type with its own panel ({label, render(note) -> nodes}) and the parts it reuses
    registerRenderer(type, r) { (S.renderers || (S.renderers = {}))[type] = r; if (S.cur && S.cur.type === type && !panel.hidden) renderPanel(); },
    titleField, section, renderTags, renderBacklinks, renderLinked, renderDanger, renderMD, autosize, renderPanel,
    // static/shortcuts.js (keyboard commands, palette actions, tour)
    openMenu, addById, openChat, closeChat, closeExplain, newNote, runCritique, updateSearch, setShowSim, linkNotes, offerCreate,
    pickPdf: () => $('#pdf-input').click(),
    zoomFit: () => fg && fg.zoomToFit(T(600), 70),
    focusRegion, unfocusRegion, chooseRegion,
  });
  boot();
})();
