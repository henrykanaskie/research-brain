/* latex-editor.js — framework-free LaTeX input helpers for plain <textarea>s.
 *
 *   LatexEditor.attach(textarea, { preview: element|null, mode: "markdown"|"math", ...opts }) -> controller
 *   LatexEditor.render(src, { mode, inline, transform }) -> HTML string (markdown + KaTeX)
 *
 * Assumes window.katex (and optionally window.marked) are loaded. Uses no other globals.
 * Injects its own CSS using the research_brain DESIGN.md tokens via var(--token, fallback).
 *
 * attach() options:
 *   preview   element to live-render into (debounced ~120 ms), or null
 *   mode      "markdown" (default) or "math" (whole text is display math unless it contains $)
 *   render    optional (src, mode) => html, replaces the built-in renderer for the preview
 *   toolbar   optional element to put the "Symbols" toggle into (default: a row after the textarea)
 *   symbols   false to omit the Symbols toggle
 *
 * controller: { render(), insert(text), openSymbols(), closeSymbols(), detach() }
 */
(function () {
  'use strict';
  if (window.LatexEditor) return;

  // ====================================================================
  // Command table (~280 entries)
  //   k   = match key (what you type after "\")
  //   ins = inserted text; ▢ marks a slot (a position, not a literal char)
  //   g   = TeX used to draw the glyph
  //   tab = palette tab (null = autocomplete only)
  // ====================================================================
  const SLOT = '▢';
  const CMDS = [];
  const add = (tab, list) => {
    for (const it of list) {
      if (typeof it === 'string') CMDS.push({ k: it, ins: '\\' + it, g: '\\' + it, tab });
      else CMDS.push({ k: it[0], ins: it[1], g: it[2] !== undefined ? it[2] : it[1].split(SLOT).join('x'), tab: it[3] !== undefined ? it[3] : tab });
    }
  };
  const words = (s) => s.trim().split(/\s+/);

  add('Greek', words(`alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa varkappa
    lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
    Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega digamma`));

  add('Operators', words(`pm mp times div cdot circ bullet ast star dagger ddagger oplus ominus otimes oslash odot
    wedge vee cap cup sqcup setminus wr amalg`));
  add('Operators', words(`hbar ell infty degree prime Re Im wp aleph angle triangle square surd flat sharp checkmark`));
  add('Calculus', [
    ['sum', '\\sum'], ['prod', '\\prod'], ['coprod', '\\coprod'],
  ]);
  add('Operators', words(`bigcup bigcap bigoplus bigotimes bigodot bigvee bigwedge bigsqcup`));
  // operator names: autocomplete only
  add(null, words(`sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth exp log ln lg det dim ker
    max min sup inf arg deg gcd Pr hom limsup liminf argmax argmin`).map((w) => (w === 'argmax' || w === 'argmin' ? [w, `\\operatorname*{${w.slice(0, 3)}\\,${w.slice(3)}}_{${SLOT}}`, `\\operatorname*{${w.slice(0, 3)}\\,${w.slice(3)}}`] : w)));

  add('Relations', words(`leq geq neq equiv approx sim simeq cong propto ll gg prec succ preceq succeq
    subset supset subseteq supseteq in notin ni perp parallel mid models vdash dashv asymp doteq
    leqslant geqslant lesssim gtrsim coloneqq triangleq le ge ne`));
  add('Relations', words(`forall exists nexists neg land lor emptyset varnothing top bot therefore because`));
  add('Relations', [
    ['mathbb R', '\\mathbb{R}', '\\mathbb{R}'], ['mathbb C', '\\mathbb{C}', '\\mathbb{C}'], ['mathbb N', '\\mathbb{N}', '\\mathbb{N}'],
    ['mathbb Z', '\\mathbb{Z}', '\\mathbb{Z}'], ['mathbb Q', '\\mathbb{Q}', '\\mathbb{Q}'],
  ]);

  add('Arrows', words(`to rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow
    longrightarrow longleftarrow longleftrightarrow Longrightarrow Longleftarrow iff implies impliedby
    mapsto longmapsto uparrow downarrow updownarrow Uparrow Downarrow nearrow searrow swarrow nwarrow
    rightleftharpoons hookrightarrow hookleftarrow rightharpoonup leftharpoonup gets`));
  add('Arrows', [['xrightarrow', `\\xrightarrow{${SLOT}}`, '\\xrightarrow{f}']]);

  add('Calculus', words(`partial nabla int iint iiint oint lim ldots cdots vdots ddots`));
  add('Calculus', [
    ['dd', '\\mathrm{d}', '\\mathrm{d}'],
    ['int_', `\\int_{${SLOT}}^{${SLOT}}`, '\\int_a^b'],
    ['sum_', `\\sum_{${SLOT}}^{${SLOT}}`, '\\sum_{i}^{n}'],
    ['prod_', `\\prod_{${SLOT}}^{${SLOT}}`, '\\prod_{i}^{n}'],
    ['lim_', `\\lim_{${SLOT} \\to ${SLOT}}`, '\\lim_{x\\to 0}'],
    ['oint_', `\\oint_{${SLOT}}`, '\\oint_C'],
    ['dv', `\\frac{\\mathrm{d} ${SLOT}}{\\mathrm{d} ${SLOT}}`, '\\frac{\\mathrm{d}f}{\\mathrm{d}x}'],
    ['pdv', `\\frac{\\partial ${SLOT}}{\\partial ${SLOT}}`, '\\frac{\\partial f}{\\partial x}'],
  ]);

  add('Structures', [
    ['frac', `\\frac{${SLOT}}{${SLOT}}`, '\\frac{a}{b}'],
    ['dfrac', `\\dfrac{${SLOT}}{${SLOT}}`, '\\dfrac{a}{b}'],
    ['tfrac', `\\tfrac{${SLOT}}{${SLOT}}`, '\\tfrac{a}{b}'],
    ['sqrt', `\\sqrt{${SLOT}}`, '\\sqrt{x}'],
    ['sqrtn', `\\sqrt[${SLOT}]{${SLOT}}`, '\\sqrt[n]{x}'],
    ['superscript', `^{${SLOT}}`, 'x^{n}'],
    ['subscript', `_{${SLOT}}`, 'x_{i}'],
    ['binom', `\\binom{${SLOT}}{${SLOT}}`, '\\binom{n}{k}'],
    ['pmatrix', `\\begin{pmatrix}${SLOT} & ${SLOT} \\\\ ${SLOT} & ${SLOT}\\end{pmatrix}`, '\\begin{pmatrix}a&b\\\\c&d\\end{pmatrix}'],
    ['bmatrix', `\\begin{bmatrix}${SLOT} & ${SLOT} \\\\ ${SLOT} & ${SLOT}\\end{bmatrix}`, '\\begin{bmatrix}a&b\\\\c&d\\end{bmatrix}'],
    ['vmatrix', `\\begin{vmatrix}${SLOT} & ${SLOT} \\\\ ${SLOT} & ${SLOT}\\end{vmatrix}`, '\\begin{vmatrix}a&b\\\\c&d\\end{vmatrix}'],
    ['cases', `\\begin{cases}${SLOT} & ${SLOT} \\\\ ${SLOT} & ${SLOT}\\end{cases}`, '\\begin{cases}a&x>0\\\\b&x\\le0\\end{cases}'],
    ['aligned', `\\begin{aligned}${SLOT} &= ${SLOT}\\end{aligned}`, '\\begin{aligned}a&=b\\end{aligned}'],
    ['overset', `\\overset{${SLOT}}{${SLOT}}`, '\\overset{!}{=}'],
    ['underset', `\\underset{${SLOT}}{${SLOT}}`, '\\underset{x}{\\max}'],
    ['boxed', `\\boxed{${SLOT}}`, '\\boxed{x}'],
    ['text', `\\text{${SLOT}}`, '\\text{abc}'],
    ['operatorname', `\\operatorname{${SLOT}}`, '\\operatorname{op}'],
    ['quad', '\\quad', '\\square\\quad\\square', null],
    ['qquad', '\\qquad', '\\square\\qquad\\square', null],
  ]);
  add('Structures', [
    ['left(', `\\left( ${SLOT} \\right)`, '\\left( x \\right)'],
    ['left[', `\\left[ ${SLOT} \\right]`, '\\left[ x \\right]'],
    ['left{', `\\left\\{ ${SLOT} \\right\\}`, '\\left\\{ x \\right\\}'],
    ['left|', `\\left| ${SLOT} \\right|`, '\\left| x \\right|'],
    ['right)', '\\right)', ')', null], ['right]', '\\right]', ']', null], ['right}', '\\right\\}', '\\}', null], ['right|', '\\right|', '|', null],
    ['langle', `\\langle ${SLOT} \\rangle`, '\\langle x \\rangle'],
    ['rangle', '\\rangle', '\\rangle', null],
    ['norm', `\\| ${SLOT} \\|`, '\\| x \\|'],
    ['lVert', '\\lVert', '\\lVert', null], ['rVert', '\\rVert', '\\rVert', null],
    ['lvert', '\\lvert', '\\lvert', null], ['rvert', '\\rvert', '\\rvert', null],
    ['lfloor', `\\lfloor ${SLOT} \\rfloor`, '\\lfloor x \\rfloor'],
    ['lceil', `\\lceil ${SLOT} \\rceil`, '\\lceil x \\rceil'],
    ['rfloor', '\\rfloor', '\\rfloor', null], ['rceil', '\\rceil', '\\rceil', null],
    ['bra', `\\langle ${SLOT} |`, '\\langle \\psi |'], ['ket', `| ${SLOT} \\rangle`, '| \\psi \\rangle'],
  ]);
  // units (type e.g. \eV or \unit…)
  add('Structures', [
    ['eV', '\\mathrm{eV}'], ['keV', '\\mathrm{keV}'], ['MeV', '\\mathrm{MeV}'], ['K', '\\mathrm{K}'],
    ['m^-3', '\\mathrm{m^{-3}}'], ['cm^-3', '\\mathrm{cm^{-3}}'], ['ms', '\\mathrm{m\\,s^{-1}}'],
    ['Hz', '\\mathrm{Hz}'], ['Tesla', '\\mathrm{T}'], ['Pa', '\\mathrm{Pa}'], ['Torr', '\\mathrm{Torr}'],
    ['W', '\\mathrm{W}'], ['um', '\\mu\\mathrm{m}'], ['nm', '\\mathrm{nm}'], ['A', '\\mathrm{A}'], ['V', '\\mathrm{V}'],
  ].map(([k, ins]) => [k, ins, ins]));

  add('Accents & fonts', [
    ['hat', `\\hat{${SLOT}}`, '\\hat{a}'], ['bar', `\\bar{${SLOT}}`, '\\bar{a}'], ['vec', `\\vec{${SLOT}}`, '\\vec{a}'],
    ['dot', `\\dot{${SLOT}}`, '\\dot{a}'], ['ddot', `\\ddot{${SLOT}}`, '\\ddot{a}'], ['tilde', `\\tilde{${SLOT}}`, '\\tilde{a}'],
    ['widehat', `\\widehat{${SLOT}}`, '\\widehat{ab}'], ['widetilde', `\\widetilde{${SLOT}}`, '\\widetilde{ab}'],
    ['overline', `\\overline{${SLOT}}`, '\\overline{ab}'], ['underline', `\\underline{${SLOT}}`, '\\underline{ab}'],
    ['overbrace', `\\overbrace{${SLOT}}^{${SLOT}}`, '\\overbrace{ab}'], ['underbrace', `\\underbrace{${SLOT}}_{${SLOT}}`, '\\underbrace{ab}'],
    ['mathbf', `\\mathbf{${SLOT}}`, '\\mathbf{B}'], ['boldsymbol', `\\boldsymbol{${SLOT}}`, '\\boldsymbol{\\alpha}'],
    ['mathrm', `\\mathrm{${SLOT}}`, '\\mathrm{d}'], ['mathcal', `\\mathcal{${SLOT}}`, '\\mathcal{L}'],
    ['mathbb', `\\mathbb{${SLOT}}`, '\\mathbb{R}'], ['mathfrak', `\\mathfrak{${SLOT}}`, '\\mathfrak{g}'],
    ['mathsf', `\\mathsf{${SLOT}}`, '\\mathsf{A}'], ['mathit', `\\mathit{${SLOT}}`, '\\mathit{ab}'], ['mathtt', `\\mathtt{${SLOT}}`, '\\mathtt{x}'],
  ]);

  const TABS = ['Greek', 'Operators', 'Relations', 'Arrows', 'Calculus', 'Structures', 'Accents & fonts'];

  // ====================================================================
  // Rendering (markdown + KaTeX)
  // ====================================================================
  const ERR_COLOR = 'var(--red, #B23A33)';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const unesc = (s) => String(s == null ? '' : s).replace(/&(amp|lt|gt|quot|#39);/g, (m, k) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[k]));

  function tex(src, display) {
    if (window.katex) {
      try {
        return window.katex.renderToString(src, { displayMode: !!display, throwOnError: false, errorColor: ERR_COLOR, strict: 'ignore', trust: false, output: 'htmlAndMathml' });
      } catch (e) { /* fall through */ }
    }
    return `<span class="le-err" style="color:${ERR_COLOR}">${esc(display ? '$$' + src + '$$' : '$' + src + '$')}</span>`;
  }

  function safeUrl(href) {
    const s = String(href || '').trim();
    if (!s) return null;
    // Browsers drop tabs, newlines and control characters before reading a URL's scheme, so
    // "java\tscript:" runs as javascript:. Judge the scheme on the string the browser will see.
    const probe = s.replace(/[\u0000-\u0020\u007f-\u009f]/g, '');
    if (/^(https?:|mailto:)/i.test(probe)) return s;
    if (/^[a-z][a-z0-9+.-]*:/i.test(probe)) return null; // javascript:, data:, vbscript:, file:, ...
    return s; // relative: /path, #frag, ./x, x.html
  }

  let md = null;
  function getMarked() {
    if (md || !window.marked || !window.marked.Marked) return md;
    md = new window.marked.Marked({ gfm: true, breaks: false });
    md.use({
      renderer: {
        html(t) { return esc(typeof t === 'string' ? t : (t && (t.raw || t.text)) || ''); },
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
          return `<img src="${esc(u)}" alt="${esc(unesc(text || ''))}" style="max-width:100%">`;
        },
      },
    });
    return md;
  }

  /**
   * Markdown with math. Code spans/blocks are shielded; $$…$$, \[…\], \(…\) and $…$ become KaTeX
   * placeholders before marked runs. `transform(s, put)` may add more placeholders (put(html) -> token).
   */
  function render(src, opts) {
    opts = opts || {};
    src = String(src == null ? '' : src);
    if (!src.trim()) return '';
    if (opts.mode === 'math' && src.indexOf('$') < 0) return tex(src.trim(), !opts.inline);

    const slots = [];
    const put = (html) => `LEPH${slots.push(html) - 1}XL`;
    const codes = [];
    const hide = (m) => `LECODE${codes.push(m) - 1}XL`;
    let s = src.replace(/\r\n?/g, '\n');
    s = s.replace(/(^|\n)([ \t]*)(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n[ \t]*\3[ \t]*(?=\n|$)|$)/g, (m, pre) => pre + hide(m.slice(pre.length)));
    s = s.replace(/(`+)(?!`)([\s\S]*?[^`])\1(?!`)/g, (m) => hide(m));
    s = s.replace(/\$\$([\s\S]+?)\$\$/g, (m, t) => put(tex(t.trim(), true)));
    s = s.replace(/\\\[([\s\S]+?)\\\]/g, (m, t) => put(tex(t.trim(), true)));
    s = s.replace(/\\\(([\s\S]+?)\\\)/g, (m, t) => put(tex(t.trim(), false)));
    s = s.replace(/(^|[^\\$])\$(?=[^\s$])((?:\\.|[^$\\\n]|\n(?!\s*\n))+?)\$(?!\d)/g, (m, pre, t) => {
      if (/\s$/.test(t)) return m; // "$5 and $10": not math
      return pre + put(tex(t, false));
    });
    if (typeof opts.transform === 'function') s = opts.transform(s, put);
    s = s.replace(/LECODE(\d+)XL/g, (m, i) => codes[Number(i)]);

    const mk = getMarked();
    let out;
    if (mk) {
      try { out = opts.inline ? mk.parseInline(s) : mk.parse(s); }
      catch (e) { out = esc(s).replace(/\n/g, '<br>'); }
    } else {
      out = esc(s).replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>');
      if (!opts.inline) out = '<p>' + out + '</p>';
    }
    out = out.replace(/<p>\s*(LEPH\d+XL)\s*<\/p>/g, '$1');
    return out.replace(/LEPH(\d+)XL/g, (m, i) => (slots[Number(i)] != null ? slots[Number(i)] : ''));
  }

  const glyphCache = new Map();
  function glyph(c) {
    if (!glyphCache.has(c.g)) glyphCache.set(c.g, tex(c.g, false));
    return glyphCache.get(c.g);
  }

  // ====================================================================
  // CSS
  // ====================================================================
  function injectCSS() {
    if (document.getElementById('le-style')) return;
    const css = `
.le-pop{position:fixed;z-index:1000;min-width:220px;max-width:320px;max-height:264px;overflow-y:auto;margin:0;padding:3px 0;list-style:none;
  background:var(--sheet,#FAFAFA);color:var(--ink,#1E2226);border:1px solid var(--rule,#D3D7DC);border-radius:3px;
  font:13px/1.3 "Archivo",system-ui,-apple-system,"Segoe UI",sans-serif}
.le-pop li{display:flex;align-items:center;gap:10px;padding:4px 10px;cursor:pointer;white-space:nowrap}
.le-pop li.on{background:var(--red-wash,rgba(178,58,51,.10))}
.le-pop .le-g{min-width:44px;text-align:center;font-size:14px;color:var(--ink,#1E2226)}
.le-pop .le-g .katex{font-size:1em}
.le-pop .le-n{color:var(--ink-2,#5A6169);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;overflow:hidden;text-overflow:ellipsis}
.le-tools{display:flex;justify-content:flex-end;margin-top:3px}
.le-sym-btn{all:unset;cursor:pointer;font:12px/1.4 "Archivo",system-ui,sans-serif;color:var(--ink-2,#5A6169);padding:1px 4px;border-radius:3px}
.le-sym-btn:hover,.le-sym-btn.on{color:var(--ink,#1E2226);text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--rule,#D3D7DC)}
.le-sym-btn:focus-visible{outline:1px solid var(--ink-2,#5A6169)}
.le-pal{position:fixed;z-index:1000;width:332px;background:var(--sheet,#FAFAFA);color:var(--ink,#1E2226);border:1px solid var(--rule,#D3D7DC);border-radius:3px;
  font:12px/1.3 "Archivo",system-ui,sans-serif}
.le-tabs{display:flex;flex-wrap:wrap;gap:2px 10px;padding:7px 10px 6px;border-bottom:1px solid var(--rule,#D3D7DC)}
.le-tabs button{all:unset;cursor:pointer;color:var(--ink-2,#5A6169);padding:1px 0;border-bottom:1px solid transparent}
.le-tabs button:hover{color:var(--ink,#1E2226)}
.le-tabs button.on{color:var(--ink,#1E2226);border-bottom-color:var(--red,#B23A33)}
.le-grid{display:grid;grid-template-columns:repeat(8,1fr);gap:1px;padding:6px;max-height:208px;overflow-y:auto}
.le-grid.wide{grid-template-columns:repeat(4,1fr)}
.le-cell{all:unset;cursor:pointer;height:34px;display:flex;align-items:center;justify-content:center;border-radius:3px;font-size:14px;overflow:hidden}
.le-cell .katex{font-size:1em}
.le-grid.wide .le-cell{height:46px;font-size:13.5px}
.le-cell:hover,.le-cell:focus-visible{background:var(--red-wash,rgba(178,58,51,.10))}
.le-foot{padding:6px 10px 7px;border-top:1px solid var(--rule,#D3D7DC);color:var(--ink-2,#5A6169);font-size:11.5px}
.le-foot kbd{font:inherit;color:var(--ink,#1E2226)}
`;
    const st = document.createElement('style');
    st.id = 'le-style';
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ====================================================================
  // Caret measurement (mirror div)
  // ====================================================================
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

  // ====================================================================
  // Math context detection
  // ====================================================================
  function mathState(text, pos) {
    let inline = false, display = false;
    for (let i = 0; i < pos; i++) {
      const c = text[i];
      if (c === '\\') { i++; continue; }
      if (c === '$') {
        if (text[i + 1] === '$' && i + 1 < pos) { if (!inline) display = !display; i++; }
        else if (!display) inline = !inline;
      }
    }
    return inline || display;
  }

  // ====================================================================
  // Text insertion that keeps native undo
  // ====================================================================
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

  // ====================================================================
  // Shared floating UI (one autocomplete popup + one palette for the page)
  // ====================================================================
  let pop = null, pal = null, palOwner = null;
  function popEl() {
    if (!pop) {
      pop = document.createElement('ul');
      pop.className = 'le-pop';
      pop.setAttribute('role', 'listbox');
      pop.hidden = true;
      pop.addEventListener('mousedown', (e) => e.preventDefault());
      document.body.appendChild(pop);
    }
    return pop;
  }

  document.addEventListener('mousedown', (e) => {
    if (pal && !pal.hidden && !pal.contains(e.target) && !(palOwner && palOwner.btn && palOwner.btn.contains(e.target))) closePalette();
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && pal && !pal.hidden) { e.preventDefault(); e.stopPropagation(); closePalette(); }
  }, true);
  window.addEventListener('resize', () => { closePalette(); if (pop) pop.hidden = true; });
  document.addEventListener('scroll', (e) => {
    if (pal && !pal.hidden && !pal.contains(e.target)) closePalette();
    if (pop && !pop.hidden && !pop.contains(e.target)) pop.hidden = true;
  }, true);

  function closePalette() {
    if (!pal) return;
    pal.hidden = true;
    if (palOwner && palOwner.btn) { palOwner.btn.classList.remove('on'); palOwner.btn.setAttribute('aria-expanded', 'false'); }
    palOwner = null;
  }
  let palTab = 'Greek';
  function openPalette(ed) {
    if (!pal) {
      pal = document.createElement('div');
      pal.className = 'le-pal';
      pal.setAttribute('role', 'dialog');
      pal.setAttribute('aria-label', 'Symbols');
      pal.hidden = true;
      pal.addEventListener('mousedown', (e) => { if (e.target.closest('.le-cell,.le-tabs button')) e.preventDefault(); });
      document.body.appendChild(pal);
    }
    if (palOwner && palOwner !== ed && palOwner.btn) palOwner.btn.classList.remove('on');
    palOwner = ed;
    drawPalette();
    pal.hidden = false;
    if (ed.btn) { ed.btn.classList.add('on'); ed.btn.setAttribute('aria-expanded', 'true'); }
    // Left edge aligned to the toggle, below it (flip above if no room), clamped to the viewport.
    // If that would cover the field being edited, put the palette beside the field instead.
    const a = (ed.btn || ed.ta).getBoundingClientRect();
    const f = ed.ta.getBoundingClientRect();
    const w = pal.offsetWidth, hgt = pal.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight, M = 8;
    const clampX = (x) => Math.max(M, Math.min(x, vw - w - M));
    const clampY = (y) => Math.max(M, Math.min(y, vh - hgt - M));
    const overlaps = (x, y) => x < f.right && x + w > f.left && y < f.bottom && y + hgt > f.top;
    let left = clampX(a.left);
    let top = a.bottom + 4;
    if (top + hgt > vh - M) top = a.top - hgt - 4;
    top = clampY(top);
    if (overlaps(left, top)) {
      const cands = [
        { x: f.right + 8, y: clampY(f.top) },          // right of the field
        { x: f.left - w - 8, y: clampY(f.top) },       // left of the field
        { x: clampX(a.left), y: f.bottom + 4 },        // below the whole field
        { x: clampX(a.left), y: f.top - hgt - 4 },     // above the whole field
      ];
      const fit = cands.find((c) => c.x >= M && c.x + w <= vw - M && c.y >= M && c.y + hgt <= vh - M && !overlaps(c.x, c.y));
      if (fit) { left = fit.x; top = fit.y; }
    }
    pal.style.left = left + 'px';
    pal.style.top = top + 'px';
  }
  function drawPalette() {
    const ed = palOwner;
    pal.innerHTML = '';
    const tabs = document.createElement('div');
    tabs.className = 'le-tabs';
    tabs.setAttribute('role', 'tablist');
    for (const t of TABS) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = t;
      b.setAttribute('role', 'tab');
      if (t === palTab) b.className = 'on';
      b.addEventListener('click', () => { palTab = t; drawPalette(); });
      tabs.appendChild(b);
    }
    const items = CMDS.filter((c) => c.tab === palTab);
    const grid = document.createElement('div');
    grid.className = 'le-grid' + (palTab === 'Structures' || palTab === 'Accents & fonts' ? ' wide' : '');
    for (const c of items) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'le-cell';
      b.title = c.ins.split(SLOT).join('…');
      b.innerHTML = glyph(c);
      b.addEventListener('click', () => { if (ed) ed.insertCommand(c, null); });
      grid.appendChild(b);
    }
    const foot = document.createElement('div');
    foot.className = 'le-foot';
    foot.innerHTML = 'Type <kbd>\\</kbd> for commands, <kbd>' + (isMac ? '⌘' : 'Ctrl+') + 'M</kbd> for inline math';
    pal.append(tabs, grid, foot);
  }
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');

  // ====================================================================
  // attach()
  // ====================================================================
  function rank(q) {
    const ql = q.toLowerCase();
    const out = [];
    CMDS.forEach((c, i) => {
      const k = c.k;
      let r;
      if (k === q) r = 0;
      else if (k.startsWith(q)) r = 1;
      else if (k.toLowerCase().startsWith(ql)) r = 2;
      else if (k.toLowerCase().includes(ql)) r = 3;
      else return;
      out.push({ c, r, i });
    });
    out.sort((a, b) => a.r - b.r || (a.r <= 2 ? a.c.k.length - b.c.k.length : 0) || a.i - b.i);
    const seen = new Set();
    return out.filter((x) => (seen.has(x.c.ins) ? false : seen.add(x.c.ins))).slice(0, 40).map((x) => x.c);
  }

  function attach(ta, options) {
    if (!ta || ta.tagName !== 'TEXTAREA' && ta.tagName !== 'INPUT') throw new Error('LatexEditor.attach needs a textarea');
    if (ta.__latexEditor) return ta.__latexEditor;
    injectCSS();
    const opt = Object.assign({ preview: null, mode: 'markdown', symbols: true }, options || {});
    const ed = { ta, opt, btn: null };

    // ---------- preview ----------
    let rT = null;
    const doRender = () => {
      rT = null;
      if (!opt.preview) return;
      const html = opt.render ? opt.render(ta.value, opt.mode) : render(ta.value, { mode: opt.mode });
      opt.preview.innerHTML = html || '';
    };
    const schedule = () => { if (opt.preview) { clearTimeout(rT); rT = setTimeout(doRender, 120); } };

    const inMath = (pos) => {
      const v = ta.value;
      if (opt.mode === 'math' && v.indexOf('$') < 0) return true;
      return mathState(v, pos == null ? ta.selectionStart : pos);
    };

    // ---------- snippet slots ----------
    let slots = null; // {pos:[...], i, end}
    let prev = ta.value;
    let beforeSel = null;
    let suppress = false;
    const clearSlots = () => { slots = null; };

    function adjustSlots(start, oldEnd, newEnd) {
      if (!slots) return;
      const delta = newEnd - oldEnd;
      const f = (o, inclusive) => {
        if (o > start || (inclusive && o === start && oldEnd === start)) {
          if (o >= oldEnd) return o + delta;
          return newEnd;
        }
        return o;
      };
      slots.pos = slots.pos.map((o) => f(o, false));
      slots.end = f(slots.end, true);
      slots.start = slots.start;
    }
    function checkCaretInSnippet() {
      if (!slots) return;
      const p = ta.selectionStart, q = ta.selectionEnd;
      if (p < slots.start || q > slots.end) clearSlots();
    }

    /** Insert a command/snippet replacing [from, to). Wraps in $…$ if outside math (markdown mode). */
    ed.insertCommand = (c, range) => {
      const text = c.ins;
      let s0 = range ? range[0] : ta.selectionStart;
      let e0 = range ? range[1] : ta.selectionEnd;
      let wrap = !inMath(s0);
      let stepOver = 0;
      // caret sits right after the closing "$" of an inline span: extend that span instead
      if (wrap && s0 === e0 && s0 > 0 && ta.value[s0 - 1] === '$' && ta.value[s0 - 2] !== '$' && inMath(s0 - 1)) {
        s0 = e0 = s0 - 1; wrap = false; stepOver = 1;
      }
      const selected = !range && e0 > s0 ? ta.value.slice(s0, e0) : '';
      const parts = text.split(SLOT);
      let body = '';
      const rel = [];
      parts.forEach((p, i) => { body += p; if (i < parts.length - 1) { rel.push(body.length); } });
      // a selection fills the first slot
      if (selected && rel.length) {
        body = body.slice(0, rel[0]) + selected + body.slice(rel[0]);
        for (let i = 1; i < rel.length; i++) rel[i] += selected.length;
        rel[0] += selected.length;
        rel.shift();
        rel.unshift(null);
      }
      const pre = wrap ? '$' : '', post = wrap ? '$' : '';
      const full = pre + body + post;
      suppress = true;
      insertAt(ta, s0, e0, full);
      suppress = false;
      prev = ta.value;
      const base = s0 + pre.length;
      const pos = rel.filter((x) => x !== null).map((x) => base + x);
      const after = s0 + full.length;
      const snippetEnd = base + body.length;
      // where the caret goes now
      if (pos.length) {
        const exit = wrap ? after : snippetEnd + stepOver;
        if (pos[pos.length - 1] !== exit) pos.push(exit);
        slots = { pos, i: 0, start: s0, end: Math.max(after, exit) };
        ta.setSelectionRange(pos[0], pos[0]);
      } else {
        // plain symbol: caret continues after it (outside the $ we added / stepped over)
        slots = null;
        const p = after + stepOver;
        ta.setSelectionRange(p, p);
      }
      closePop();
      schedule();
    };

    // ---------- autocomplete ----------
    let acList = [], acIdx = 0, acFrom = -1;
    const closePop = () => { if (pop && pop.__owner === ed) { pop.hidden = true; pop.__owner = null; } acList = []; acFrom = -1; };
    const popOpen = () => pop && !pop.hidden && pop.__owner === ed && acList.length;
    function updateAC() {
      const p = ta.selectionStart;
      if (p !== ta.selectionEnd) { closePop(); return; }
      const before = ta.value.slice(Math.max(0, p - 40), p);
      const m = before.match(/(^|[^\\])\\([A-Za-z]+)$/);
      if (!m) { closePop(); return; }
      const q = m[2];
      acList = rank(q);
      if (!acList.length) { closePop(); return; }
      acFrom = p - q.length - 1;
      acIdx = 0;
      drawPop();
    }
    function drawPop() {
      const el = popEl();
      el.__owner = ed;
      el.innerHTML = '';
      acList.forEach((c, i) => {
        const li = document.createElement('li');
        li.setAttribute('role', 'option');
        if (i === acIdx) { li.className = 'on'; li.setAttribute('aria-selected', 'true'); }
        const g = document.createElement('span');
        g.className = 'le-g';
        g.innerHTML = glyph(c);
        const n = document.createElement('span');
        n.className = 'le-n';
        n.textContent = c.ins.split(SLOT).join('…');
        li.append(g, n);
        li.addEventListener('click', () => accept(i));
        li.addEventListener('mousemove', () => { if (acIdx !== i) { acIdx = i; mark(); } });
        el.appendChild(li);
      });
      el.hidden = false;
      const xy = caretXY(ta, acFrom);
      const w = el.offsetWidth, hgt = el.offsetHeight;
      let left = Math.min(Math.max(8, xy.x - 6), window.innerWidth - w - 8);
      let top = xy.y + xy.h + 2;
      if (top + hgt > window.innerHeight - 8) top = Math.max(8, xy.y - hgt - 2);
      el.style.left = left + 'px';
      el.style.top = top + 'px';
    }
    function mark() {
      const lis = pop.children;
      for (let i = 0; i < lis.length; i++) lis[i].className = i === acIdx ? 'on' : '';
      const on = lis[acIdx];
      if (on) on.scrollIntoView({ block: 'nearest' });
    }
    function accept(i) {
      const c = acList[i == null ? acIdx : i];
      if (!c) return;
      const from = acFrom, to = ta.selectionStart;
      closePop();
      ed.insertCommand(c, [from, to]);
    }

    // ---------- event handlers ----------
    const onBeforeInput = () => { beforeSel = [ta.selectionStart, ta.selectionEnd]; };
    const onInput = (e) => {
      const cur = ta.value;
      if (!suppress) {
        if (slots) {
          const t = e && e.inputType;
          if (t === 'historyUndo' || t === 'historyRedo' || !beforeSel) clearSlots();
          else {
            const nc = ta.selectionStart;
            const start = Math.min(beforeSel[0], nc);
            const oldEnd = start + (prev.length - cur.length) + (nc - start);
            adjustSlots(start, oldEnd, nc);
            checkCaretInSnippet();
          }
        }
        updateAC();
      }
      prev = cur;
      beforeSel = null;
      schedule();
    };

    const PAIRS = { '{': '}', '(': ')', '[': ']' };
    const CLOSERS = { '}': 1, ')': 1, ']': 1 };
    const onKeyDown = (e) => {
      if (e.isComposing) return;
      const mod = e.metaKey || e.ctrlKey;
      // autocomplete navigation
      if (popOpen()) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault(); e.stopImmediatePropagation();
          acIdx = (acIdx + (e.key === 'ArrowDown' ? 1 : -1) + acList.length) % acList.length;
          mark(); return;
        }
        if ((e.key === 'Tab' || e.key === 'Enter') && !e.shiftKey && !mod) {
          e.preventDefault(); e.stopImmediatePropagation(); accept(); return;
        }
        if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); closePop(); return; }
      }
      if (e.key === 'Escape' && pal && !pal.hidden && palOwner === ed) { e.preventDefault(); e.stopImmediatePropagation(); closePalette(); return; }
      // snippet slot navigation
      if (e.key === 'Tab' && slots && !mod && !e.altKey) {
        e.preventDefault(); e.stopImmediatePropagation();
        const p = ta.selectionStart;
        let next;
        if (e.shiftKey) {
          const back = slots.pos.filter((o) => o < p);
          next = back.length ? back[back.length - 1] : slots.pos[0];
        } else {
          next = slots.pos.find((o) => o > p);
          if (next === undefined) next = slots.pos[slots.pos.length - 1];
        }
        ta.setSelectionRange(next, next);
        if (!e.shiftKey && next === slots.pos[slots.pos.length - 1]) clearSlots();
        return;
      }
      // math shortcuts
      if (mod && !e.altKey && (e.code === 'KeyM' || e.key === 'm' || e.key === 'M')) {
        e.preventDefault(); e.stopImmediatePropagation();
        const s = ta.selectionStart, en = ta.selectionEnd, sel = ta.value.slice(s, en);
        if (e.shiftKey) {
          const v = ta.value;
          const lead = s > 0 && v[s - 1] !== '\n' ? '\n' : '';
          const trail = en < v.length && v[en] !== '\n' ? '\n' : '';
          const txt = lead + '$$\n' + sel + '\n$$' + trail;
          suppress = true; insertAt(ta, s, en, txt); suppress = false; prev = ta.value;
          const caret = s + lead.length + 3 + sel.length;
          ta.setSelectionRange(caret - sel.length, caret);
        } else {
          suppress = true; insertAt(ta, s, en, '$' + sel + '$'); suppress = false; prev = ta.value;
          if (sel) ta.setSelectionRange(s + 1, s + 1 + sel.length);
          else ta.setSelectionRange(s + 1, s + 1);
        }
        clearSlots(); closePop(); schedule();
        return;
      }
      if (mod || e.altKey) return;
      // auto-close brackets inside math
      if (PAIRS[e.key] && inMath()) {
        const s = ta.selectionStart, en = ta.selectionEnd;
        if (ta.value[s - 1] === '\\' && e.key !== '{') return; // \( \[ — leave alone
        e.preventDefault(); e.stopImmediatePropagation();
        const sel = ta.value.slice(s, en);
        const close = ta.value[s - 1] === '\\' ? '\\' + PAIRS[e.key] : PAIRS[e.key];
        const before = slots;
        insertAt(ta, s, en, e.key + sel + close);
        if (before && slots) { /* slot positions adjusted by onInput */ }
        ta.setSelectionRange(s + 1, s + 1 + sel.length);
        return;
      }
      if (CLOSERS[e.key] && ta.selectionStart === ta.selectionEnd && ta.value[ta.selectionStart] === e.key && inMath()) {
        e.preventDefault(); e.stopImmediatePropagation();
        const p = ta.selectionStart + 1;
        ta.setSelectionRange(p, p);
        checkCaretInSnippet();
        return;
      }
    };
    const onNav = () => { checkCaretInSnippet(); if (popOpen()) updateAC(); };
    const onBlur = () => { setTimeout(() => { if (document.activeElement !== ta) closePop(); }, 120); };

    ta.addEventListener('beforeinput', onBeforeInput);
    ta.addEventListener('input', onInput);
    ta.addEventListener('keydown', onKeyDown, true);
    ta.addEventListener('keyup', (e) => { if (e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End') onNav(); });
    ta.addEventListener('mouseup', onNav);
    ta.addEventListener('blur', onBlur);

    // ---------- symbols toggle ----------
    let toolsRow = null;
    if (opt.symbols !== false) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'le-sym-btn';
      btn.textContent = 'Symbols';
      btn.title = 'Insert a symbol or structure';
      btn.setAttribute('aria-haspopup', 'dialog');
      btn.setAttribute('aria-expanded', 'false');
      btn.addEventListener('mousedown', (e) => e.preventDefault());
      btn.addEventListener('click', () => {
        if (pal && !pal.hidden && palOwner === ed) closePalette();
        else { if (document.activeElement !== ta) ta.focus(); openPalette(ed); }
      });
      ed.btn = btn;
      if (opt.toolbar) opt.toolbar.appendChild(btn);
      else {
        toolsRow = document.createElement('div');
        toolsRow.className = 'le-tools';
        toolsRow.appendChild(btn);
        ta.insertAdjacentElement('afterend', toolsRow);
      }
    }

    const ctl = {
      textarea: ta,
      render: () => { clearTimeout(rT); doRender(); },
      insert: (text) => ed.insertCommand({ ins: text }, null),
      openSymbols: () => openPalette(ed),
      closeSymbols: () => { if (palOwner === ed) closePalette(); },
      detach: () => {
        ta.removeEventListener('beforeinput', onBeforeInput);
        ta.removeEventListener('input', onInput);
        ta.removeEventListener('keydown', onKeyDown, true);
        ta.removeEventListener('mouseup', onNav);
        ta.removeEventListener('blur', onBlur);
        if (ed.btn) ed.btn.remove();
        if (toolsRow) toolsRow.remove();
        if (palOwner === ed) closePalette();
        closePop();
        delete ta.__latexEditor;
      },
    };
    ed.ctl = ctl;
    ta.__latexEditor = ctl;
    doRender();
    return ctl;
  }

  window.LatexEditor = { attach, render, commands: CMDS, tabs: TABS, version: '1.0.0' };
})();
