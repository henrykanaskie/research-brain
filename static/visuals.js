/* Diagrams, charts and figures in AI answers, shared by the main app (static/app.js) and the PDF viewer
 * (static/viewer.html): window.RBVisuals.
 *
 *   ```mermaid blocks become diagrams and charts (static/vendor/mermaid.min.js, loaded the first time one shows):
 *     flowchart, sequenceDiagram, timeline, mindmap, pie, xychart-beta (bar and line charts) and the rest.
 *   ```svg blocks become figures, drawn as an image so nothing in them can run.
 *
 * Both fit the width they're in, with Show code and Save image under them. A diagram whose code has a mistake
 * is first tried again with the common mistakes fixed (labels with brackets or punctuation left unquoted);
 * if it still doesn't draw, its code shows with a short note. While an answer is still being written, a
 * diagram shows as a quiet placeholder (half a diagram never draws, so it never flashes an error).
 * Drawn diagrams are cached by their source, so drawing the chat again doesn't redraw them.
 */
(function () {
  'use strict';

  const CSS = `
.md-visual{margin:6px 0 12px;border:1px solid var(--rule,#D5D7D1);border-radius:var(--r,3px);background:var(--sheet,#F7F7F4);padding:10px 10px 4px;max-width:100%;box-sizing:border-box}
.md-visual-fig{display:flex;justify-content:center;overflow-x:auto}
.md-visual-fig svg,.md-visual-fig img{max-width:100%;height:auto;display:block}
.md-visual-svg .md-visual-fig img{background:#fff;border-radius:2px}
.md-visual-tools{display:flex;justify-content:flex-end;gap:2px;margin-top:4px}
.md-visual-tools button{font:12px/1.4 var(--sans,system-ui,sans-serif);color:var(--ink-2,#61676C);background:none;border:0;height:auto;min-height:0;padding:2px 6px;cursor:pointer;border-radius:3px}
.md-visual-tools button:hover{color:var(--ink,#15181B)}
.md-visual-tools button[hidden]{display:none}
.md-visual-code{margin:4px 0 6px;font-size:12px;max-height:240px;overflow:auto;white-space:pre}
.md-visual-err,.md-visual-wait{font-size:13px;color:var(--ink-2,#61676C);padding:10px 0;text-align:center}
.md-visual-pending{margin:6px 0 12px;border:1px dashed var(--rule,#D5D7D1);border-radius:var(--r,3px);padding:14px 10px;font-size:13px;color:var(--ink-2,#61676C);text-align:center}
`;
  function injectCSS() {
    if (document.getElementById('rb-visuals-style')) return;
    const st = document.createElement('style');
    st.id = 'rb-visuals-style';
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'text') e.textContent = v;
      else if (k === 'onclick') e.addEventListener('click', v);
      else if (k === 'hidden') e.hidden = true;
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids) if (c) e.appendChild(c);
    return e;
  }

  const darkTheme = () => document.documentElement.dataset.theme === 'dark'
    || (document.documentElement.dataset.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);

  let mermaidReady = null;
  function loadMermaid() {
    if (mermaidReady) return mermaidReady;
    mermaidReady = new Promise((resolve, reject) => {
      if (window.mermaid) { resolve(window.mermaid); return; }
      const sc = document.createElement('script');
      sc.src = '/static/vendor/mermaid.min.js';
      sc.onload = () => (window.mermaid ? resolve(window.mermaid) : reject(new Error('mermaid did not load')));
      sc.onerror = () => { mermaidReady = null; reject(new Error('mermaid did not load')); };
      document.head.appendChild(sc);
    });
    return mermaidReady;
  }

  let seq = 0, theme = '';
  function setTheme(mm) {
    const t = darkTheme() ? 'dark' : 'light';
    if (t === theme) return;
    // the page's own colours; a plain system font, which mermaid measures and draws the same (a web font it
    // measured before it loaded cut labels short)
    const cs = getComputedStyle(document.documentElement);
    const dark = t === 'dark';
    const v = (n, light, darkv) => cs.getPropertyValue(n).trim() || (dark ? darkv : light);
    const ink = v('--ink', '#15181B', '#E5E6E1'), ink2 = v('--ink-2', '#61676C', '#969CA0'), sheet = v('--sheet', '#F7F7F4', '#171B1F');
    const ground = v('--ground', '#EEEFEB', '#121518'), rule = v('--rule', '#D5D7D1', '#2A2F34'), machine = v('--machine', '#4A6487', '#8DA3C4');
    const red = v('--red', '#BD3A2C', '#EC6A58'), ochre = v('--ochre', '#A8761E', '#D2A24C'), moss = v('--moss', '#5E7A3A', '#94B36B'),
      violet = v('--violet', '#6E5A9A', '#A895D2');
    const font = 'Helvetica, Arial, sans-serif';
    mm.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'base', fontFamily: font,
      themeVariables: { fontFamily: font, fontSize: '14px', darkMode: dark, background: sheet,
        primaryColor: ground, primaryTextColor: ink, primaryBorderColor: machine, lineColor: ink2, secondaryColor: sheet,
        tertiaryColor: sheet, textColor: ink, mainBkg: ground, nodeBorder: machine, clusterBkg: sheet, clusterBorder: rule,
        titleColor: ink, edgeLabelBackground: sheet, noteBkgColor: sheet, noteTextColor: ink, noteBorderColor: rule,
        actorBkg: ground, actorBorder: machine, actorTextColor: ink, signalColor: ink2, signalTextColor: ink,
        pie1: machine, pie2: red, pie3: ochre, pie4: moss, pie5: violet, pie6: ink2, pieStrokeColor: sheet,
        pieTitleTextColor: ink, pieSectionTextColor: sheet, pieLegendTextColor: ink,
        xyChart: { backgroundColor: sheet, titleColor: ink, xAxisLabelColor: ink2, yAxisLabelColor: ink2, xAxisTitleColor: ink2,
          yAxisTitleColor: ink2, xAxisLineColor: rule, yAxisLineColor: rule, xAxisTickColor: rule, yAxisTickColor: rule,
          plotColorPalette: [machine, red, ochre, moss, violet].join(',') } },
      flowchart: { useMaxWidth: true, htmlLabels: false, padding: 12 }, sequence: { useMaxWidth: true }, gantt: { useMaxWidth: true },
      xyChart: { useMaxWidth: true, height: 320 } });
    theme = t;
  }

  /** mermaid leaves its scratch element (and, on a failed draw, a "Syntax error" picture) in <body>: remove it */
  function sweep(id) {
    for (const x of [id, 'd' + id, 'i' + id]) {
      const n = document.getElementById(x);
      if (n && !n.closest('.md-visual-fig')) n.remove();
    }
  }
  async function valid(mm, src) {
    try { return (await mm.parse(src, { suppressErrors: true })) !== false; } catch { return false; }
  }
  async function render(mm, src) {
    const id = 'rb-mm-' + (++seq);
    try { return (await mm.render(id, src)).svg; } finally { sweep(id); }
  }

  /** Draws mermaid source to SVG markup. Tries the source as written (with list-like labels made safe), then
   *  with common mistakes fixed; throws an Error whose message is mermaid's first line if neither parses. */
  async function mermaidSvg(src) {
    const mm = await loadMermaid();
    setTheme(mm);
    let code = mermaidSafe(src, false);
    if (!(await valid(mm, code))) {
      const fixed = mermaidSafe(fixMermaid(src), false);
      if (fixed !== code && (await valid(mm, fixed))) code = fixed;
      else {
        let why = '';
        try { await mm.parse(code); } catch (e) { why = String((e && e.message) || e || '').split('\n')[0]; }
        throw new Error(why || 'the diagram code has a mistake');
      }
    }
    // mermaid reads each label as Markdown and can't show a list or heading there: a label that starts like
    // "1. Fit", "- check" or "# Step" came out as "Unsupported markdown: list". Those starts are escaped
    // (mermaidSafe); if that error still shows in the drawing, draw it again with the markers left out.
    let svg = await render(mm, code);
    if (/Unsupported markdown/i.test(svg)) svg = await render(mm, mermaidSafe(code, true));
    return svg;
  }
  function mermaidSafe(src, strip) {
    const START = String.raw`(^|[\[("'|{]|\n|<br\s*/?>)([ \t]*)`;
    const num = new RegExp(START + String.raw`(\d+)\.(?=[ \t])`, 'g');
    const mark = new RegExp(START + String.raw`([-*+#>])(?=[ \t])`, 'g');
    return String(src).split('\n').map((line, i) => {
      if (i === 0 || /^\s*(%%|classDef|style|linkStyle|click|class\s)/.test(line)) return line;  // the diagram type and styling lines
      // a no-break space after the marker: looks the same, but Markdown no longer reads it as a list or heading
      return strip ? line.replace(num, '$1$2$3)').replace(mark, '$1$2')
        : line.replace(new RegExp(num.source + String.raw`[ \t]`, 'g'), '$1$2$3. ')
          .replace(new RegExp(START + String.raw`([-*+])[ \t]`, 'g'), '$1$2$3 ')
          .replace(new RegExp(START + String.raw`([#>])(?=[ \t])`, 'g'), '$1$2\\$3');  // mermaid's own syntax uses # and >: escaped
    }).join('\n');
  }

  // ---------- light fixes for the usual mistakes in AI-written diagrams ----------
  const OPEN = { '[': ']', '(': ')', '{': '}' };
  const CLOSE = { ']': '[', ')': '(', '}': '{' };
  const qtext = (s) => '"' + s.trim().replace(/"/g, '#quot;') + '"';
  /** A flowchart line with each plain node label quoted: A[Fit (step 2)] -> A["Fit (step 2)"]. Labels that
   *  are already quoted, and the shapes that start with two brackets ([( stadium, [[ subroutine, (( circle ...)
   *  are left as they are. */
  function quoteNodes(line) {
    let out = '', i = 0;
    while (i < line.length) {
      const c = line[i];
      if (c === '"') {  // a quoted label or edge label: as it is
        const e = line.indexOf('"', i + 1);
        const to = e < 0 ? line.length : e + 1;
        out += line.slice(i, to); i = to; continue;
      }
      if (OPEN[c] && i > 0 && /\w/.test(line[i - 1])) {
        const nx = line[i + 1];
        if (nx === '"' || nx === '`' || '[({/\\>'.includes(nx || '')) { out += c; i++; continue; }
        // the matching close: every kind of bracket nests, quotes are skipped
        const stack = [c];
        let j = i + 1, q = false;
        for (; j < line.length && stack.length; j++) {
          const d = line[j];
          if (d === '"') q = !q;
          else if (q) continue;
          else if (OPEN[d]) stack.push(d);
          else if (CLOSE[d]) { if (stack[stack.length - 1] === CLOSE[d]) stack.pop(); else break; }
        }
        if (!stack.length) {
          const inner = line.slice(i + 1, j - 1);
          if (inner.trim()) { out += c + qtext(inner) + OPEN[c]; i = j; continue; }
        }
      }
      out += c; i++;
    }
    return out;
  }
  function fixMermaid(src) {
    const s = String(src).replace(/[“”]/g, '"').replace(/\r\n?/g, '\n');
    const lines = s.split('\n');
    const head = (lines[0] || '').trim();
    if (/^(flowchart|graph)\b/i.test(head)) {
      return lines.map((line, i) => {
        if (i === 0 || /^\s*(%%|classDef|style|linkStyle|click|class\s|direction\b|end\s*$)/.test(line)) return line;
        if (/^\s*subgraph\b/.test(line)) return line.replace(/^(\s*subgraph\s+[\w-]+\s*)\[([^"\]][^\]]*)\]\s*$/, (m, a, t) => a + '[' + qtext(t) + ']');
        // edge labels: -->|f(x) > 0| -> -->|"f(x) > 0"|
        line = line.replace(/([-=.>ox])\|([^|"\n]+)\|/g, (m, a, t) => a + '|' + qtext(t) + '|');
        return quoteNodes(line);
      }).join('\n');
    }
    if (/^xychart(-beta)?\b/i.test(head)) {
      // axis categories and titles with spaces or punctuation must be quoted
      return lines.map((line) => line
        .replace(/^(\s*x-axis\s*)\[([^\]]*)\]/, (m, a, items) => a + '[' + items.split(',').map((t) => (/^\s*".*"\s*$/.test(t) ? t.trim() : qtext(t))).join(', ') + ']')
        .replace(/^(\s*(?:title|x-axis|y-axis)\s+)(?!["[])([^"\n]*?[^\d\s>-][^"\n]*?)(\s+-?\d[\d.]*\s*-->\s*-?\d[\d.]*)?\s*$/, (m, a, t, range) => a + qtext(t) + (range || ''))
      ).join('\n');
    }
    return s;
  }

  // ---------- the box a diagram or figure sits in ----------
  function visualBox(kind, src, makeFigure) {
    injectCSS();
    const fig = el('div', { class: 'md-visual-fig' });
    const code = el('pre', { class: 'md-visual-code', hidden: true }, el('code', { text: src }));
    const show = el('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Show code',
      onclick: () => { code.hidden = !code.hidden; show.textContent = code.hidden ? 'Show code' : 'Hide code'; } });
    const save = el('button', { class: 'btn btn-text btn-sm', type: 'button', text: 'Save image', onclick: () => {
      const svg = fig.querySelector('svg') ? fig.querySelector('svg').outerHTML : kind === 'svg' ? src : '';
      if (!svg) return;
      const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
      const a = el('a', { href: url, download: kind === 'svg' ? 'figure.svg' : 'diagram.svg' });
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
    } });
    const box = el('div', { class: 'md-visual md-visual-' + kind }, fig, el('div', { class: 'md-visual-tools' }, show, save), code);
    makeFigure(fig, { code, show, save });
    return box;
  }

  const cache = new Map();  // theme + source -> {svg} | {err} | Promise
  function drawMermaid(pre, src) {
    const key = (darkTheme() ? 'd' : 'l') + src;
    pre.replaceWith(visualBox('mermaid', src, (fig, t) => {
      const ok = (svg) => { fig.innerHTML = svg; };
      const fail = (e) => {
        fig.replaceChildren(el('div', { class: 'md-visual-err', title: (e && e.message) || '',
          text: 'Couldn’t draw this diagram: its code has a mistake. Here is the code instead.' }));
        t.code.hidden = false; t.show.textContent = 'Hide code'; t.save.hidden = true;
      };
      let v = cache.get(key);
      if (v && !(v instanceof Promise)) { if (v.err) fail(v.err); else ok(v.svg); return; }
      fig.appendChild(el('div', { class: 'md-visual-wait', text: 'Drawing the diagram' }));
      if (!v) {
        v = mermaidSvg(src).then((svg) => ({ svg }), (err) => ({ err }));
        cache.set(key, v);
        v.then((r) => cache.set(key, r));
      }
      v.then((r) => (r.err ? fail(r.err) : ok(r.svg)));
    }));
  }

  const CODE_SEL = 'pre > code.language-mermaid, pre > code.language-svg';
  /** Draws every ```mermaid and ```svg block inside `containers` (a selector) under root. */
  function draw(root, containers) {
    if (!root) return;
    const sel = String(containers || '*').split(',').map((c) => CODE_SEL.split(', ').map((x) => c.trim() + ' ' + x).join(', ')).join(', ');
    for (const code of root.querySelectorAll(sel)) {
      const pre = code.parentElement, src = code.textContent.trim();
      if (!src || !pre.parentElement) continue;
      if (code.classList.contains('language-svg')) {
        if (!/^(<\?xml[^>]*>\s*)?<svg[\s>]/i.test(src)) continue;
        pre.replaceWith(visualBox('svg', src, (fig) => {
          fig.appendChild(el('img', { src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(src), alt: 'Figure' }));
        }));
        continue;
      }
      drawMermaid(pre, src);
    }
  }
  /** While an answer is being written: each diagram or figure (complete or not) shows as a placeholder. */
  function pending(root) {
    if (!root) return;
    injectCSS();
    for (const code of root.querySelectorAll(CODE_SEL)) {
      const kind = code.classList.contains('language-svg') ? 'figure' : 'diagram';
      code.parentElement.replaceWith(el('div', { class: 'md-visual-pending', text: `A ${kind}, drawn when the answer is finished` }));
    }
  }

  window.RBVisuals = { draw, pending, mermaidSvg, fixMermaid, darkTheme };
})();
