// research_brain annotate mode: content script, injected on demand (toolbar button or Alt+Shift+A).
//
// No AI. Everything goes to the research_brain server on this computer, through background.js.
// Highlights are drawn with the CSS Custom Highlight API, so the page's own DOM is never changed.
// Each highlight is stored as a TextQuoteSelector {exact, prefix, suffix} and re-anchored on revisit
// by text search (exact, then case-insensitive, then a head/tail fuzzy match), scored by prefix/suffix.
(() => {
  "use strict";
  if (window.__rbAnnotate) return;

  const CTX = 32;            // prefix / suffix length stored with a selector
  const HOST_ID = "rb-annotate-host";
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const S = {
    on: false,
    frags: [],               // [{id,title,quote,body,locator,parent,selector,created}]
    papers: {},              // id -> {title, authors, year, venue, doi, arxiv, url, citation}
    paper: null,             // the page's matched paper (or the note created for the page)
    ranges: new Map(),       // frag id -> Range (anchored)
    activeId: null,
    sel: null,               // {range, selector, quote, locator} while the toolbar is shown
    note: null,              // {token, range, frame}
    sideOpen: false,
    observer: null, reanchorT: null, reanchorTries: 0,
  };

  /* ── server (via background.js; only whitelisted /api paths) ── */
  function api(path, method = "GET", body = null) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "rb-api", path, method, body }, (r) => {
          if (chrome.runtime.lastError) resolve({ ok: false, error: "The extension was reloaded. Refresh this page." });
          else resolve(r || { ok: false, error: "No answer" });
        });
      } catch (e) { resolve({ ok: false, error: "The extension was reloaded. Refresh this page." }); }
    });
  }
  const pageUrl = () => location.href.split("#")[0];
  const pageTitle = () => {
    const m = document.querySelector('meta[name="citation_title"]');
    return ((m && m.content) || document.title || "").replace(/^\[[\w.\/-]+\]\s*/, "").trim();
  };

  /* ── text index: the page's visible text with whitespace collapsed, mapped back to DOM points ── */
  const SKIP = "script,style,noscript,textarea,template,select,option,svg title,#" + HOST_ID;
  function buildIndex() {
    const nodes = [], starts = [];
    let text = "", prevSpace = true;
    const posNode = [], posOff = [];
    const w = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        const p = n.parentElement;
        if (!p || p.closest(SKIP)) return NodeFilter.FILTER_REJECT;
        return n.nodeValue ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    let n, ni = 0;
    while ((n = w.nextNode())) {
      nodes.push(n); starts.push(text.length);
      const v = n.nodeValue;
      let chunk = "";
      for (let i = 0; i < v.length; i++) {
        const c = v[i];
        if (c === "­") continue; // soft hyphen
        if (c === " " || c === "\n" || c === "\t" || c === "\r" || c === " " || c === "\f") {
          if (prevSpace) continue;
          chunk += " "; prevSpace = true;
        } else { chunk += c; prevSpace = false; }
        posNode.push(ni); posOff.push(i);
      }
      text += chunk; ni++;
    }
    return { text, nodes, starts, posNode, posOff, lower: null };
  }
  const norm = (s) => String(s || "").replace(/­/g, "").replace(/[\s ]+/g, " ");

  // DOM point -> index position (first indexed char at or after the point)
  function textPoint(container, offset, isEnd) {
    if (container.nodeType === Node.TEXT_NODE) return { node: container, off: offset };
    // element boundary: the text node right after it (start) or right before it (end)
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const child = container.childNodes[offset];
    if (!isEnd) {
      if (child) { w.currentNode = child; const t = child.nodeType === 3 ? child : w.nextNode(); return t ? { node: t, off: 0 } : null; }
      w.currentNode = container; let last = null, t; while ((t = w.nextNode()) && container.contains(t)) last = t;
      const nx = last ? (w.currentNode = last, w.nextNode()) : w.nextNode();
      return nx ? { node: nx, off: 0 } : null;
    }
    const before = child ? child.previousSibling : container.lastChild;
    let t = null;
    if (before) {
      if (before.nodeType === 3) t = before;
      else { w.currentNode = before; let x; while ((x = w.nextNode()) && before.contains(x)) t = x; if (!t) { w.currentNode = before; t = w.previousNode(); } }
    } else { w.currentNode = container; t = w.previousNode(); }
    return t ? { node: t, off: t.nodeValue.length } : null;
  }
  function indexPos(ix, pt) {
    if (!pt) return -1;
    let ni = ix.nodes.indexOf(pt.node);
    if (ni === -1) return -1;
    let p = ix.starts[ni];
    while (p < ix.posNode.length && ix.posNode[p] === ni && ix.posOff[p] < pt.off) p++;
    return p;
  }
  function rangeFromIndex(ix, s, e) {
    if (s < 0 || e <= s || e > ix.posNode.length) return null;
    const r = document.createRange();
    try {
      r.setStart(ix.nodes[ix.posNode[s]], ix.posOff[s]);
      r.setEnd(ix.nodes[ix.posNode[e - 1]], ix.posOff[e - 1] + 1);
    } catch { return null; }
    return r;
  }

  function selectorFor(range) {
    const ix = buildIndex();
    let s = indexPos(ix, textPoint(range.startContainer, range.startOffset, false));
    let e = indexPos(ix, textPoint(range.endContainer, range.endOffset, true));
    if (s < 0 || e < 0 || e <= s) {
      const exact = norm(range.toString()).trim();
      return exact ? { exact, prefix: "", suffix: "" } : null;
    }
    while (s < e && ix.text[s] === " ") s++;
    while (e > s && ix.text[e - 1] === " ") e--;
    if (e <= s) return null;
    return { exact: ix.text.slice(s, e), prefix: ix.text.slice(Math.max(0, s - CTX), s), suffix: ix.text.slice(e, e + CTX) };
  }

  function commonSuffix(a, b) { let i = 0; while (i < a.length && i < b.length && a[a.length - 1 - i] === b[b.length - 1 - i]) i++; return i; }
  function commonPrefix(a, b) { let i = 0; while (i < a.length && i < b.length && a[i] === b[i]) i++; return i; }
  function allIndexes(hay, needle, max = 200) {
    const out = []; if (!needle) return out;
    let i = hay.indexOf(needle);
    while (i !== -1 && out.length < max) { out.push(i); i = hay.indexOf(needle, i + 1); }
    return out;
  }
  // Best [start, end) for a selector in the index, or null.
  function locate(ix, sel) {
    const exact = norm(sel.exact).trim();
    if (!exact) return null;
    const pre = norm(sel.prefix), suf = norm(sel.suffix);
    const score = (s, e) => commonSuffix(ix.text.slice(Math.max(0, s - pre.length), s), pre)
      + commonPrefix(ix.text.slice(e, e + suf.length), suf);
    const best = (cands) => {
      let b = null, bs = -1;
      for (const [s, e] of cands) { const sc = score(s, e); if (sc > bs) { b = [s, e]; bs = sc; } }
      return b;
    };
    // 1. exact
    let c = allIndexes(ix.text, exact).map((s) => [s, s + exact.length]);
    if (c.length) return best(c);
    // 2. case-insensitive (only when lowercasing keeps the length, so positions stay valid)
    if (ix.lower == null) { const l = ix.text.toLowerCase(); ix.lower = l.length === ix.text.length ? l : ""; }
    const el = exact.toLowerCase();
    if (ix.lower && el.length === exact.length) {
      c = allIndexes(ix.lower, el).map((s) => [s, s + el.length]);
      if (c.length) return best(c);
    }
    // 3. fuzzy: the passage's head and tail still match, with the middle changed a little
    const hay = ix.lower || ix.text, ex = ix.lower ? el : exact;
    if (ex.length >= 24) {
      const k = Math.min(32, Math.floor(ex.length / 3));
      const head = ex.slice(0, k), tail = ex.slice(-k);
      const cands = [];
      for (const s of allIndexes(hay, head, 50)) {
        const lim = s + Math.ceil(ex.length * 1.3) + 16;
        const t = hay.indexOf(tail, s + k);
        if (t !== -1 && t + k <= lim && t + k - s >= ex.length * 0.7) cands.push([s, t + k]);
      }
      if (cands.length) return best(cands);
      // only one end of the passage survived: find the other boundary from the stored context
      // (the start of the suffix after the head, or the end of the prefix before the tail)
      const lo = (n) => (ix.lower ? n.toLowerCase() : n);
      const sufK = lo(suf.trimStart()).slice(0, 16), preK = lo(pre.trimEnd()).slice(-16);
      if (sufK.length >= 6) for (const s of allIndexes(hay, head, 50)) {
        const from = s + Math.floor(ex.length * 0.6), lim = s + Math.ceil(ex.length * 1.4) + 16;
        const t = hay.indexOf(sufK, from);
        if (t !== -1 && t <= lim) { let e = t; while (e > s && hay[e - 1] === " ") e--; cands.push([s, e]); }
      }
      if (preK.length >= 6) for (const t of allIndexes(hay, tail, 50)) {
        const end = t + k, from = Math.max(0, end - Math.ceil(ex.length * 1.4) - 16 - preK.length);
        const p0 = hay.lastIndexOf(preK, end - Math.floor(ex.length * 0.6));
        if (p0 !== -1 && p0 >= from) { let st = p0 + preK.length; while (st < end && hay[st] === " ") st++; cands.push([st, end]); }
      }
      if (cands.length) return best(cands);
      // last resort: keep the whole length from the surviving end if the prefix/suffix agree
      for (const s of allIndexes(hay, head, 50)) if (pre && commonSuffix(ix.text.slice(Math.max(0, s - pre.length), s), pre) >= Math.min(8, pre.length)) cands.push([s, Math.min(hay.length, s + ex.length)]);
      for (const t of allIndexes(hay, tail, 50)) if (suf && commonPrefix(ix.text.slice(t + k, t + k + suf.length), suf) >= Math.min(8, suf.length)) cands.push([Math.max(0, t + k - ex.length), t + k]);
      if (cands.length) return best(cands);
    }
    return null;
  }

  function anchorAll() {
    const ix = buildIndex();
    let missing = 0;
    for (const f of S.frags) {
      if (S.ranges.has(f.id) && !S.ranges.get(f.id).collapsed && document.contains(S.ranges.get(f.id).startContainer)) continue;
      const sel = f.selector || (f.quote ? { exact: f.quote, prefix: "", suffix: "" } : null);
      const loc = sel && locate(ix, sel);
      const r = loc && rangeFromIndex(ix, loc[0], loc[1]);
      if (r) S.ranges.set(f.id, r); else { S.ranges.delete(f.id); missing++; }
    }
    paint();
    renderSide();
    return missing;
  }

  /* ── painting (CSS Custom Highlight API; styles injected by background.js) ── */
  const HL = typeof Highlight === "function" && CSS.highlights;
  function paint() {
    if (!HL) return;
    const main = [], active = [];
    for (const [id, r] of S.ranges) (id === S.activeId ? active : main).push(r);
    if (S.note && S.note.range) active.push(S.note.range);
    CSS.highlights.set("rb-hl", new Highlight(...main));
    CSS.highlights.set("rb-hl-active", new Highlight(...active));
  }
  function unpaint() { if (HL) { CSS.highlights.delete("rb-hl"); CSS.highlights.delete("rb-hl-active"); } }

  /* ── UI (in a shadow root so the page's CSS can't touch it, and ours can't touch the page) ── */
  let host, root, bar, card, side, toast, noteWrap;
  const CSS_TEXT = `
:host{all:initial}
*{box-sizing:border-box}
.ui{--sheet:#FFFFFF;--ground:#F6F7F8;--ink:#1E2226;--ink-2:#5A6169;--rule:#E6E8EB;--rule-strong:#D3D7DC;
  --red:#B23A33;--red-wash:rgba(178,58,51,.10);--tint:rgba(30,34,38,.05);
  --sans:"rb Instrument Sans","Instrument Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --serif:"rb STIX Two Text","STIX Two Text",Cambria,Georgia,serif;
  font:13px/1.5 var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased}
@media (prefers-color-scheme: dark){
  .ui{--sheet:#1D2126;--ground:#16191D;--ink:#E4E7EA;--ink-2:#9AA2AB;--rule:#2E343B;--rule-strong:#444C55;
    --red:#E06A60;--red-wash:rgba(224,106,96,.14);--tint:rgba(228,231,234,.07)}
}
[hidden]{display:none !important}
button{font:inherit;color:inherit}
.bar{position:fixed;z-index:2147483646;display:flex;align-items:stretch;height:30px;background:var(--ink);color:var(--sheet);border-radius:3px;overflow:hidden}
.bar button{all:unset;cursor:pointer;display:flex;align-items:center;gap:8px;padding:0 12px;font:500 13px var(--sans);color:var(--sheet);white-space:nowrap}
.bar button+button{border-left:1px solid rgba(127,127,127,.35)}
.bar button:hover{background:rgba(255,255,255,.10)}
.bar button:focus-visible{outline:2px solid var(--red);outline-offset:-2px}
.bar kbd{font:500 11px var(--sans);opacity:.55}
.card{position:fixed;z-index:2147483646;width:340px;max-width:calc(100vw - 16px);background:var(--sheet);border:1px solid var(--rule-strong);border-radius:3px;padding:14px 16px 12px}
.card .q{font:14px/1.5 var(--serif);color:var(--ink);border-left:2px solid var(--red);padding-left:10px;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.card .b{font:14px/1.5 var(--serif);color:var(--red);margin-top:8px;white-space:pre-wrap;word-break:break-word;max-height:160px;overflow:auto}
.card .b:empty{display:none}
.card .src{margin-top:10px;color:var(--ink-2);font-size:12px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.acts{display:flex;align-items:center;gap:14px;margin-top:10px}
.lnk{all:unset;cursor:pointer;color:var(--ink-2);font:500 12.5px var(--sans)}
.lnk:hover{color:var(--ink);text-decoration:underline;text-underline-offset:2px}
.lnk:focus-visible{outline:2px solid var(--ink);outline-offset:1px;border-radius:2px}
.grow{flex:1}
.note{position:fixed;z-index:2147483647;width:420px;max-width:calc(100vw - 16px);border:1px solid var(--rule-strong);border-radius:3px;background:var(--sheet);overflow:hidden}
.note iframe{display:block;width:100%;height:300px;border:0;background:var(--sheet)}
.side{position:fixed;z-index:2147483645;right:16px;bottom:16px;display:flex;flex-direction:column;align-items:flex-end}
.tab{all:unset;cursor:pointer;display:flex;align-items:center;gap:8px;height:30px;padding:0 12px;background:var(--sheet);border:1px solid var(--rule-strong);border-radius:3px;font:500 12.5px var(--sans);color:var(--ink)}
.tab:hover{background:var(--ground)}
.tab:focus-visible{outline:2px solid var(--ink);outline-offset:1px}
.tab .dot{width:6px;height:6px;border-radius:50%;background:var(--red)}
.tab .n{color:var(--ink-2);font-variant-numeric:tabular-nums}
.panel{width:320px;max-width:calc(100vw - 32px);max-height:min(62vh,560px);display:flex;flex-direction:column;background:var(--sheet);border:1px solid var(--rule-strong);border-radius:3px;margin-bottom:8px}
.ph{display:flex;align-items:baseline;gap:8px;padding:14px 16px 10px;border-bottom:1px solid var(--rule)}
.ph b{font:600 13px var(--sans)}
.ph .n{color:var(--ink-2);font-variant-numeric:tabular-nums}
.ph .paper{display:block;color:var(--ink-2);font-size:12px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:280px}
.items{overflow:auto;padding:4px 0}
.it{padding:10px 16px;cursor:pointer}
.it:hover,.it.on{background:var(--tint)}
.it .q{font:13.5px/1.45 var(--serif);color:var(--ink);border-left:2px solid var(--red);padding-left:9px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}
.it .b{font:13.5px/1.45 var(--serif);color:var(--red);margin-top:4px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;white-space:pre-wrap;word-break:break-word}
.it .b:empty{display:none}
.it .m{display:flex;gap:12px;margin-top:5px;color:var(--ink-2);font-size:12px}
.it .m .miss{color:var(--red)}
.it .acts{margin-top:4px;display:none}
.it:hover .acts,.it.on .acts,.it:focus-within .acts{display:flex}
.empty{padding:16px;color:var(--ink-2);line-height:1.55}
.foot{padding:8px 16px 10px;border-top:1px solid var(--rule);color:var(--ink-2);font-size:11.5px;line-height:1.5}
.foot kbd{font:500 11px var(--sans);border:1px solid var(--rule);border-radius:3px;padding:0 4px}
.toast{position:fixed;z-index:2147483647;left:50%;bottom:22px;transform:translateX(-50%);background:var(--ink);color:var(--sheet);padding:7px 14px;border-radius:3px;font:13px var(--sans);opacity:0;transition:opacity .15s;pointer-events:none;max-width:calc(100vw - 32px)}
.toast.show{opacity:1}
.toast.err{background:var(--red);color:#fff}
@media (prefers-reduced-motion: reduce){.toast{transition:none}}
`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function mountUI() {
    host = document.createElement("div");
    host.id = HOST_ID;
    host.setAttribute("data-rb", "annotate");
    // closed: the page's own scripts can't reach into it (it lists your notes on this page, and holds the
    // note iframe's one-time token). The e2e test copy switches it to "open" so Playwright can see inside.
    root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `<style>${CSS_TEXT}</style>
<div class="ui">
  <div class="bar" role="toolbar" aria-label="research_brain" hidden>
    <button type="button" data-act="hl" title="Highlight (H)">Highlight <kbd>H</kbd></button>
    <button type="button" data-act="note" title="Highlight and write a note (N)">Highlight + note <kbd>N</kbd></button>
  </div>
  <div class="card" role="dialog" aria-label="Highlight" hidden></div>
  <div class="note" hidden></div>
  <div class="side">
    <div class="panel" hidden>
      <div class="ph"><div><b>Notes on this page</b> <span class="n"></span><span class="paper"></span></div></div>
      <div class="items"></div>
      <div class="foot">Select text, then <kbd>H</kbd> to highlight or <kbd>N</kbd> to add a note. <kbd>${isMac ? "⌥⇧A" : "Alt+Shift+A"}</kbd> turns annotate mode off.</div>
    </div>
    <button type="button" class="tab" aria-expanded="false" title="Notes on this page"><span class="dot"></span>Notes <span class="n">0</span></button>
  </div>
  <div class="toast" role="status"></div>
</div>`;
    (document.body || document.documentElement).appendChild(host);
    bar = root.querySelector(".bar"); card = root.querySelector(".card"); side = root.querySelector(".side");
    toast = root.querySelector(".toast"); noteWrap = root.querySelector(".note");
    bar.addEventListener("mousedown", (e) => e.preventDefault()); // keep the page selection
    bar.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.act === "hl") highlightOnly(); else openNote();
    });
    side.querySelector(".tab").addEventListener("click", () => setSide(!S.sideOpen));
    side.querySelector(".items").addEventListener("click", (e) => {
      const it = e.target.closest(".it"); if (!it) return;
      const f = S.frags.find((x) => x.id === it.dataset.id); if (!f) return;
      const a = e.target.closest("[data-act]");
      if (a && a.dataset.act === "cite") { copyCitation(f); return; }
      if (a && a.dataset.act === "open") { openInApp(f); return; }
      focusFrag(f, true);
    });
    card.addEventListener("click", (e) => {
      const a = e.target.closest("[data-act]"); if (!a) return;
      const f = S.frags.find((x) => x.id === card.dataset.id);
      if (a.dataset.act === "close") hideCard();
      else if (f && a.dataset.act === "cite") copyCitation(f);
      else if (f && a.dataset.act === "open") openInApp(f);
    });
    try { chrome.storage.local.get("rb_side_open", (o) => { if (o && o.rb_side_open) setSide(true, false); }); } catch { }
  }
  function say(msg, err = false) {
    toast.textContent = msg; toast.className = "toast show" + (err ? " err" : "");
    clearTimeout(say.t); say.t = setTimeout(() => (toast.className = "toast" + (err ? " err" : "")), err ? 4500 : 2200);
  }

  /* ── side list ─────────────────────────────────── */
  function setSide(open, persist = true) {
    S.sideOpen = open;
    side.querySelector(".panel").hidden = !open;
    side.querySelector(".tab").setAttribute("aria-expanded", String(open));
    if (persist) try { chrome.storage.local.set({ rb_side_open: open }); } catch { }
  }
  function docOrder(a, b) {
    const ra = S.ranges.get(a.id), rb = S.ranges.get(b.id);
    if (ra && rb) return ra.compareBoundaryPoints(Range.START_TO_START, rb);
    if (ra) return -1; if (rb) return 1;
    return String(a.created).localeCompare(String(b.created));
  }
  function renderSide() {
    if (!root) return;
    const list = [...S.frags].sort(docOrder);
    side.querySelector(".tab .n").textContent = String(list.length);
    side.querySelector(".ph .n").textContent = list.length ? String(list.length) : "";
    const p = S.paper;
    side.querySelector(".ph .paper").textContent = p ? `Filed under ${p.title}` : "";
    const box = side.querySelector(".items");
    if (!list.length) {
      box.innerHTML = `<div class="empty">No notes on this page yet. Select a passage to highlight it.</div>`;
      return;
    }
    box.innerHTML = list.map((f) => `
      <div class="it${f.id === S.activeId ? " on" : ""}" data-id="${esc(f.id)}" tabindex="0">
        <div class="q">${esc(f.quote || f.title)}</div>
        <div class="b">${esc(f.body || "")}</div>
        <div class="m">${f.locator ? `<span>${esc(f.locator)}</span>` : ""}${S.ranges.has(f.id) ? "" : `<span class="miss">Not found on this version of the page</span>`}</div>
        <div class="acts"><button class="lnk" data-act="cite" type="button">Copy citation</button><button class="lnk" data-act="open" type="button">Open in research_brain</button></div>
      </div>`).join("");
    box.querySelectorAll(".it").forEach((el) => el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { const f = S.frags.find((x) => x.id === el.dataset.id); if (f) focusFrag(f, true); }
    }));
  }
  function focusFrag(f, scroll) {
    S.activeId = f.id; paint(); renderSide();
    const r = S.ranges.get(f.id);
    if (!r) { say("That passage isn't on this version of the page."); return; }
    if (scroll) {
      const el = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement;
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
    }
  }

  /* ── selection → toolbar ───────────────────────── */
  function inOurUI(node) { return !!(node && host && (node === host || host.contains(node))); }
  function isEditable(el) {
    el = el && (el.nodeType === 1 ? el : el.parentElement);
    return !!(el && (el.closest("input,textarea,select,[contenteditable=''],[contenteditable=true]") || el.isContentEditable));
  }
  function currentSel() {
    const s = window.getSelection();
    if (!s || s.isCollapsed || !s.rangeCount) return null;
    const r = s.getRangeAt(0);
    if (inOurUI(r.commonAncestorContainer) || isEditable(r.commonAncestorContainer)) return null;
    if (!norm(s.toString()).trim()) return null;
    return r.cloneRange();
  }
  function nearestHeading(r) {
    try {
      const el = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement;
      let best = null;
      for (const h of document.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
        if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING || h.contains(el)) best = h; else break;
      }
      return best && best.tagName !== "H1" ? norm(best.textContent).trim().slice(0, 80) : "";
    } catch { return ""; }
  }
  function cleanQuote(t) {
    return String(t || "").replace(/­/g, "").replace(/([A-Za-z])-\s+([a-z])/g, "$1$2").replace(/\s+/g, " ").trim();
  }
  function showBar() {
    const r = currentSel();
    if (!r) { hideBar(); return; }
    const selector = selectorFor(r);
    if (!selector) { hideBar(); return; }
    S.sel = { range: r, selector, quote: cleanQuote(r.toString()) || selector.exact, locator: nearestHeading(r) };
    const rects = [...r.getClientRects()].filter((x) => x.width > 0 && x.height > 0);
    const last = rects[rects.length - 1] || r.getBoundingClientRect();
    bar.hidden = false;
    const bw = bar.offsetWidth || 220, bh = bar.offsetHeight || 30;
    let left = Math.max(8, Math.min(innerWidth - bw - 8, last.right - bw / 2));
    let top = last.bottom + 8;
    if (top + bh > innerHeight - 8) top = Math.max(8, (rects[0] || last).top - bh - 8);
    bar.style.left = `${left}px`; bar.style.top = `${top}px`;
  }
  function hideBar() { bar.hidden = true; S.sel = null; }

  /* ── saving ────────────────────────────────────── */
  function clipBody(sel, note) {
    return {
      quote: sel.quote, url: pageUrl(), page_title: pageTitle(), note: note || "",
      locator: sel.locator || "", target_id: S.paper ? S.paper.id : null, selector: sel.selector,
    };
  }
  async function highlightOnly() {
    const sel = S.sel; if (!sel) return;
    hideBar();
    const r = await api("/api/clip", "POST", clipBody(sel, ""));
    if (!r.ok) { say(r.offline ? r.error : `Couldn't save: ${r.error}`, true); return; }
    window.getSelection().removeAllRanges();
    await afterSave(r.data, sel.range);
    say(r.data.created_parent ? `Highlighted. Added “${trim(r.data.parent.title, 60)}” to your library.` : "Highlighted");
  }
  async function afterSave(data, range) {
    const f = data && data.fragment;
    if (data && data.parent) S.paper = S.paper && S.paper.id === data.parent.id ? S.paper : { id: data.parent.id, title: data.parent.title };
    if (f) {
      S.frags.push({ id: f.id, title: f.title, quote: f.quote, body: f.body, locator: f.locator, parent: f.parent, selector: f.selector, created: f.created });
      if (range) S.ranges.set(f.id, range);
      S.activeId = f.id;
    }
    paint(); renderSide();
    await refresh(false); // pick up the paper's citation metadata
  }
  const trim = (s, n) => (s = String(s || ""), s.length > n ? s.slice(0, n - 1) + "…" : s);

  /* ── note popover: an extension page in an iframe (LaTeX editor + KaTeX preview, isolated from the page) ── */
  async function openNote() {
    const sel = S.sel; if (!sel) return;
    hideBar(); hideCard();
    if (S.note) closeNote();
    const res = await new Promise((resolve) => chrome.runtime.sendMessage({
      type: "rb-note-open", data: { ...clipBody(sel, ""), paper_title: S.paper ? S.paper.title : "" },
    }, (x) => resolve(chrome.runtime.lastError ? null : x)));
    if (!res || !res.token) { say("The extension was reloaded. Refresh this page.", true); return; }
    const rects = [...sel.range.getClientRects()].filter((x) => x.width > 0 && x.height > 0);
    const last = rects[rects.length - 1] || sel.range.getBoundingClientRect();
    noteWrap.innerHTML = "";
    const frame = document.createElement("iframe");
    frame.src = chrome.runtime.getURL("note.html") + "#" + res.token; // the page's own (dynamic) extension URL frame.title = "Write a note"; frame.setAttribute("allow", "clipboard-write");
    noteWrap.appendChild(frame);
    noteWrap.hidden = false;
    S.note = { token: res.token, range: sel.range, frame };
    placeNote(last, 300);
    paint();
    window.getSelection().removeAllRanges();
  }
  function placeNote(anchor, h) {
    const w = noteWrap.offsetWidth || 420;
    const a = anchor || S.note.anchor;
    S.note.anchor = a;
    let left = Math.max(8, Math.min(innerWidth - w - 8, a.left));
    let top = a.bottom + 8;
    if (top + h > innerHeight - 8) top = Math.max(8, a.top - h - 8);
    if (top + h > innerHeight - 8) top = Math.max(8, innerHeight - h - 8);
    noteWrap.style.left = `${left}px`; noteWrap.style.top = `${top}px`;
    S.note.frame.style.height = `${h}px`;
  }
  function closeNote() {
    if (!S.note) return;
    noteWrap.hidden = true; noteWrap.innerHTML = ""; S.note = null; paint();
  }

  /* ── highlight card (click a highlight) ─────────── */
  function showCard(f, x, y) {
    S.activeId = f.id; paint(); renderSide();
    const p = S.papers[f.parent] || {};
    card.dataset.id = f.id;
    card.innerHTML = `<div class="q">${esc(f.quote)}</div><div class="b">${esc(f.body || "")}</div>
      ${p.citation ? `<div class="src">${esc(p.citation)}</div>` : ""}
      <div class="acts"><button class="lnk" data-act="cite" type="button">Copy citation</button>
      <button class="lnk" data-act="open" type="button">Open in research_brain</button><span class="grow"></span>
      <button class="lnk" data-act="close" type="button" title="Close (Esc)">Close</button></div>`;
    card.hidden = false;
    const w = card.offsetWidth, h = card.offsetHeight;
    card.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, x - 20))}px`;
    card.style.top = `${y + 14 + h > innerHeight - 8 ? Math.max(8, y - h - 14) : y + 14}px`;
  }
  function hideCard() { if (card && !card.hidden) { card.hidden = true; } }
  function fragAt(x, y) {
    let node, off;
    if (document.caretPositionFromPoint) { const c = document.caretPositionFromPoint(x, y); if (!c) return null; node = c.offsetNode; off = c.offset; }
    else if (document.caretRangeFromPoint) { const c = document.caretRangeFromPoint(x, y); if (!c) return null; node = c.startContainer; off = c.startOffset; }
    else return null;
    for (const [id, r] of S.ranges) {
      try {
        if (r.isPointInRange(node, off)) {
          // caret points sit between characters; make sure the click is on the highlight's boxes
          if ([...r.getClientRects()].some((b) => x >= b.left - 1 && x <= b.right + 1 && y >= b.top - 1 && y <= b.bottom + 1)) return S.frags.find((f) => f.id === id);
        }
      } catch { }
    }
    return null;
  }

  /* ── citation ──────────────────────────────────── */
  function citationText(f) {
    const p = S.papers[f.parent] || {};
    const quote = `“${(f.quote || "").trim()}”${f.locator ? ` (${f.locator})` : ""}`;
    const cite = p.citation || [pageTitle(), pageUrl()].filter(Boolean).join(". ");
    return `${quote}\n${cite}`;
  }
  async function copyCitation(f) {
    const text = citationText(f);
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch { }
    if (!ok) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.cssText = "position:fixed;opacity:0;top:0;left:0";
      root.appendChild(ta); ta.select();
      try { ok = document.execCommand("copy"); } catch { }
      ta.remove();
    }
    S.lastCopied = text;
    say(ok ? "Citation copied" : "Couldn't copy. Your browser blocked the clipboard.", !ok);
  }
  function openInApp(f) {
    chrome.runtime.sendMessage({ type: "rb-open", path: `/#note=${encodeURIComponent(f.id)}` });
  }

  /* ── events ────────────────────────────────────── */
  function onMouseUp(e) {
    if (inOurUI(e.target)) return;
    setTimeout(() => {
      if (S.note) return;
      const s = window.getSelection();
      if (s && !s.isCollapsed) { hideCard(); showBar(); return; }
      hideBar();
      const f = fragAt(e.clientX, e.clientY);
      if (f) showCard(f, e.clientX, e.clientY); else hideCard();
    }, 0);
  }
  function onKeyUp(e) { if (e.shiftKey || e.key === "Shift") { if (!S.note && currentSel()) showBar(); } }
  function onKeyDown(e) {
    if (e.isComposing) return;
    const editing = isEditable(e.target) || inOurUI(e.target) && e.target !== host;
    if (e.key === "Escape") {
      if (S.note) { e.preventDefault(); closeNote(); return; }
      if (!bar.hidden) { e.preventDefault(); hideBar(); window.getSelection().removeAllRanges(); return; }
      if (!card.hidden) { e.preventDefault(); hideCard(); S.activeId = null; paint(); renderSide(); return; }
      return;
    }
    if (bar.hidden || editing || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "h") { e.preventDefault(); e.stopPropagation(); highlightOnly(); }
    else if (k === "n") { e.preventDefault(); e.stopPropagation(); openNote(); }
  }
  function onSelChange() {
    if (!bar.hidden && !S.note) { const s = window.getSelection(); if (!s || s.isCollapsed) hideBar(); }
  }
  function onScroll() {
    if (!bar.hidden) showBar();
    hideCard();
    if (S.note && S.note.range) {
      const rects = [...S.note.range.getClientRects()].filter((x) => x.width > 0);
      const last = rects[rects.length - 1];
      if (last) placeNote(last, parseFloat(S.note.frame.style.height) || 300);
    }
  }
  function onMessage(msg, sender, sendResponse) {
    if (!msg || sender.id !== chrome.runtime.id) return;
    if (msg.type === "rb-annotate") {
      const want = typeof msg.on === "boolean" ? msg.on : !S.on;
      (want ? enable() : Promise.resolve(disable())).then(() => sendResponse({ on: S.on, count: S.frags.length }));
      return true;
    }
    if (msg.type === "rb-annotate-state") { sendResponse({ on: S.on, count: S.frags.length }); return; }
    if (msg.type === "rb-note-event" && S.note && msg.token === S.note.token) {
      if (msg.event === "resize") placeNote(null, Math.max(120, Math.min(innerHeight - 16, +msg.detail.h || 300)));
      else if (msg.event === "cancel") closeNote();
      else if (msg.event === "saved") {
        const range = S.note.range; closeNote();
        afterSave(msg.detail, range).then(() => say(msg.detail && msg.detail.created_parent
          ? `Saved. Added “${trim(msg.detail.parent.title, 60)}” to your library.` : "Note saved"));
      }
      sendResponse({ ok: true });
    }
  }

  /* ── load / enable / disable ───────────────────── */
  async function refresh(reanchor = true) {
    const r = await api(`/api/fragments?url=${encodeURIComponent(pageUrl())}`);
    if (!r.ok) { if (reanchor) say(r.offline ? r.error : `Couldn't load this page's notes: ${r.error}`, true); return false; }
    const d = r.data || {};
    S.papers = d.papers || {};
    if (d.paper) S.paper = d.paper;
    else if (!S.paper) {
      const lastParent = (d.fragments || []).map((f) => f.parent).filter(Boolean).pop();
      if (lastParent && S.papers[lastParent]) S.paper = S.papers[lastParent];
    }
    const known = new Set(S.frags.map((f) => f.id));
    for (const f of d.fragments || []) if (!known.has(f.id)) S.frags.push(f);
    const live = new Set((d.fragments || []).map((f) => f.id));
    S.frags = S.frags.filter((f) => live.has(f.id) || !reanchor); // deleted in the app → gone here
    for (const id of [...S.ranges.keys()]) if (!S.frags.some((f) => f.id === id)) S.ranges.delete(id);
    if (reanchor) anchorAll(); else renderSide();
    return true;
  }
  function watchForLateContent() {
    // Pages that render text after load (SPAs, MathJax): retry unanchored highlights a few times.
    S.observer = new MutationObserver((muts) => {
      if (muts.every((m) => inOurUI(m.target))) return;
      if (!S.frags.some((f) => !S.ranges.has(f.id)) || S.reanchorTries > 12) return;
      clearTimeout(S.reanchorT);
      S.reanchorT = setTimeout(() => { S.reanchorTries++; anchorAll(); }, 700);
    });
    S.observer.observe(document.body || document.documentElement, { childList: true, subtree: true, characterData: true });
  }
  async function enable() {
    if (S.on) return;
    S.on = true;
    if (!host) mountUI(); else host.hidden = false;
    document.addEventListener("mouseup", onMouseUp, true);
    document.addEventListener("keyup", onKeyUp, true);
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("selectionchange", onSelChange);
    window.addEventListener("scroll", onScroll, { passive: true, capture: true });
    window.addEventListener("resize", onScroll, { passive: true });
    renderSide();
    const ok = await refresh(true);
    watchForLateContent();
    if (ok) say(S.frags.length ? `Annotate mode on. ${S.frags.length} note${S.frags.length === 1 ? "" : "s"} on this page.` : "Annotate mode on. Select text to highlight it.");
    if (currentSel()) showBar();
  }
  function disable() {
    if (!S.on) return;
    S.on = false;
    document.removeEventListener("mouseup", onMouseUp, true);
    document.removeEventListener("keyup", onKeyUp, true);
    document.removeEventListener("keydown", onKeyDown, true);
    document.removeEventListener("selectionchange", onSelChange);
    window.removeEventListener("scroll", onScroll, { capture: true });
    window.removeEventListener("resize", onScroll);
    if (S.observer) S.observer.disconnect();
    closeNote(); hideBar(); hideCard(); unpaint();
    S.ranges.clear(); S.frags = []; S.activeId = null;
    if (host) host.hidden = true;
  }

  chrome.runtime.onMessage.addListener(onMessage);
  // exposed for tests and for the toolbar popup's state query; nothing secret here
  window.__rbAnnotate = { enable, disable, state: () => ({ on: S.on, count: S.frags.length, anchored: S.ranges.size }),
    _locate: (sel) => { const ix = buildIndex(); const l = locate(ix, sel); return l && ix.text.slice(l[0], l[1]); },
    _lastCopied: () => S.lastCopied || "" };
})();
