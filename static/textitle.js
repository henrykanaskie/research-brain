/* research_brain — math in titles, for showing them. A title keeps the LaTeX it came with ("Cross-field transport by
 * $\vec E \times \vec B$ drift"); where it's shown:
 *   RBTitle.text(t)  plain text with the math as Unicode ("E⃗ × B⃗"): the map (a canvas), menus, lists, tooltips
 *   RBTitle.html(t)  HTML with the math typeset by KaTeX (window.katex), the rest escaped: the paper's panel, the
 *                    viewer, Next to read. Falls back to text() without KaTeX.
 * Only $...$ and \( ... \) are math; a title without them comes back as it is. Results are remembered.
 */
(() => {
  'use strict';
  const GREEK = {
    alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε', zeta: 'ζ', eta: 'η', theta: 'θ',
    vartheta: 'ϑ', iota: 'ι', kappa: 'κ', lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', pi: 'π', varpi: 'ϖ', rho: 'ρ',
    varrho: 'ϱ', sigma: 'σ', varsigma: 'ς', tau: 'τ', upsilon: 'υ', phi: 'φ', varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω',
    Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π', Sigma: 'Σ', Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω',
  };
  const SYM = {
    times: '×', cdot: '·', pm: '±', mp: '∓', leq: '≤', le: '≤', geq: '≥', ge: '≥', neq: '≠', ne: '≠', approx: '≈',
    sim: '∼', simeq: '≃', propto: '∝', infty: '∞', partial: '∂', nabla: '∇', rightarrow: '→', to: '→', leftarrow: '←',
    leftrightarrow: '↔', Rightarrow: '⇒', degree: '°', circ: '∘', ell: 'ℓ', hbar: 'ℏ', AA: 'Å', prime: '′',
    parallel: '∥', perp: '⊥', langle: '⟨', rangle: '⟩', ldots: '…', dots: '…', cdots: '⋯', sum: '∑', int: '∫',
    in: '∈', cap: '∩', cup: '∪', equiv: '≡', ll: '≪', gg: '≫', star: '⋆', ast: '∗', odot: '⊙', otimes: '⊗', oplus: '⊕',
  };
  const ACCENT = { vec: '⃗', hat: '̂', bar: '̄', overline: '̅', tilde: '̃', dot: '̇', ddot: '̈' };
  const FONT = new Set(['mathrm', 'text', 'textrm', 'mathbf', 'mathit', 'mathcal', 'mathsf', 'mathbb', 'textit', 'textbf',
    'operatorname', 'rm', 'bf', 'it', 'boldsymbol', 'bm', 'mbox', 'emph']);
  const SUP = { 0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹', '+': '⁺', '-': '⁻', '−': '⁻', '=': '⁼',
    '(': '⁽', ')': '⁾', n: 'ⁿ', i: 'ⁱ', a: 'ᵃ', b: 'ᵇ', c: 'ᶜ', d: 'ᵈ', e: 'ᵉ', f: 'ᶠ', g: 'ᵍ', h: 'ʰ', j: 'ʲ', k: 'ᵏ', l: 'ˡ',
    m: 'ᵐ', o: 'ᵒ', p: 'ᵖ', r: 'ʳ', s: 'ˢ', t: 'ᵗ', u: 'ᵘ', v: 'ᵛ', w: 'ʷ', x: 'ˣ', y: 'ʸ', z: 'ᶻ', '*': '*', '′': '′' };
  const SUB = { 0: '₀', 1: '₁', 2: '₂', 3: '₃', 4: '₄', 5: '₅', 6: '₆', 7: '₇', 8: '₈', 9: '₉', '+': '₊', '-': '₋', '−': '₋', '=': '₌',
    '(': '₍', ')': '₎', a: 'ₐ', e: 'ₑ', h: 'ₕ', i: 'ᵢ', j: 'ⱼ', k: 'ₖ', l: 'ₗ', m: 'ₘ', n: 'ₙ', o: 'ₒ', p: 'ₚ', r: 'ᵣ', s: 'ₛ',
    t: 'ₜ', u: 'ᵤ', v: 'ᵥ', x: 'ₓ' };

  /** {group} or one token at s[i]: [text, next index] */
  function arg(s, i) {
    while (s[i] === ' ') i++;
    if (s[i] === '{') {
      let depth = 1, j = i + 1;
      while (j < s.length && depth) { if (s[j] === '{') depth++; else if (s[j] === '}') depth--; j++; }
      return [s.slice(i + 1, j - 1), j];
    }
    if (s[i] === '\\') { const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i)); return [m[0], i + m[0].length]; }
    return [s[i] || '', i + 1];
  }
  function script(t, map, mark) {
    const out = [...t].map((ch) => map[ch]);
    return out.every((x) => x !== undefined) ? out.join('') : (t.length === 1 ? mark + t : `${mark}(${t})`);
  }
  /** One piece of LaTeX math as Unicode. */
  function mathText(src) {
    let s = String(src), out = '', i = 0;
    while (i < s.length) {
      const ch = s[i];
      if (ch === '\\') {
        const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i));
        const name = m[1];
        i += m[0].length;
        if (GREEK[name]) out += GREEK[name];
        else if (SYM[name]) out += SYM[name];
        else if (ACCENT[name]) { const [a, j] = arg(s, i); i = j; const t = mathText(a); out += t.length === 1 ? t + ACCENT[name] : t; }
        else if (FONT.has(name)) { const [a, j] = arg(s, i); i = j; out += mathText(a); }
        else if (name === 'frac' || name === 'dfrac' || name === 'tfrac') {
          const [a, j] = arg(s, i); const [b, k] = arg(s, j); i = k;
          const x = mathText(a), y = mathText(b);
          out += (x.length > 1 ? `(${x})` : x) + '/' + (y.length > 1 ? `(${y})` : y);
        } else if (name === 'sqrt') { const [a, j] = arg(s, i); i = j; const t = mathText(a); out += '√' + (t.length > 1 ? `(${t})` : t); }
        else if (name === 'left' || name === 'right' || name === 'big' || name === 'Big' || name === 'displaystyle') { /* sizing: nothing */ }
        else if (/^[,;:! ]$/.test(name) || name === 'quad' || name === 'qquad') out += ' ';
        else if (/^[{}$%&#_]$/.test(name)) out += name;
        else out += name;  // an unknown command: its name reads better than nothing
      } else if (ch === '^' || ch === '_') {
        const [a, j] = arg(s, i + 1); i = j;
        out += script(mathText(a), ch === '^' ? SUP : SUB, ch);
      } else if (ch === '{' || ch === '}') i++;
      else if (ch === '~') { out += ' '; i++; }
      else { out += ch; i++; }
    }
    return out.replace(/\s*([×·±≤≥≠≈→=])\s*/g, ' $1 ').replace(/ {2,}/g, ' ').trim();
  }

  const MATH = /\$\$([\s\S]+?)\$\$|\$(?!\s)([^$]+?)(?<!\s)\$(?!\d)|\\\(([\s\S]+?)\\\)/g;  // Pandoc's rule: \"$5 and $10\" isn't math
  const memoT = new Map(), memoH = new Map();
  const remember = (memo, k, v) => { if (memo.size > 4000) memo.clear(); memo.set(k, v); return v; };

  function text(t) {
    const s = String(t ?? '');
    if (!/[$\\]/.test(s)) return s;
    if (memoT.has(s)) return memoT.get(s);
    return remember(memoT, s, s.replace(MATH, (m, a, b, c) => mathText(a || b || c)).replace(/\s{2,}/g, ' '));
  }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function html(t) {
    const s = String(t ?? '');
    if (!/[$\\]/.test(s) || !window.katex) return esc(text(s));
    if (memoH.has(s)) return memoH.get(s);
    let out = '', last = 0;
    s.replace(MATH, (m, a, b, c, at) => {
      out += esc(s.slice(last, at));
      try {
        out += window.katex.renderToString(a || b || c, { displayMode: false, throwOnError: true, strict: 'ignore', trust: false, output: 'html' });
      } catch { out += esc(mathText(a || b || c)); }
      last = at + m.length;
      return m;
    });
    out += esc(s.slice(last));
    return remember(memoH, s, out);
  }
  window.RBTitle = { text, html, mathText };
})();
