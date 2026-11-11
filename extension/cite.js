// research_brain extension: the popup's Cite tab. No AI: it talks only to research_brain on this computer
// (st.server, the same 127.0.0.1 / localhost address clip.js uses) through clip.js's api().
//   GET  /api/cite/search?q=   papers to cite        POST /api/cite/keys {ids}    \cite{key1,key2}
//   GET  /api/cite/styles      styles + defaults     POST /api/cite/format {ids, style, mode, format}
// The most useful copy is the primary button: \cite{} on Overleaf, a formatted citation on Google Docs,
// otherwise what Library settings › Citations says.
"use strict";

const CITE = { open: false, hits: [], sel: new Map(), idx: 0, seq: 0, styles: null, primary: "citation" };

function citeSite(url) {
  try {
    const h = new URL(url).host;
    if (/(^|\.)overleaf\.com$/.test(h) || /(^|\.)sharelatex\.com$/.test(h)) return "latex";
    if (h === "docs.google.com") return "formatted";
  } catch { }
  return "";
}

async function citeOpen() {
  CITE.prev = { home: !$("#home").hidden, form: !$("#form").hidden, done: !$("#done").hidden };
  for (const s of ["#home", "#form", "#done", "#settings"]) $(s).hidden = true;
  $("#cite").hidden = false;
  CITE.open = true;
  $("#modeLbl").textContent = "Cite";
  $("#citeQ").focus();
  try {
    CITE.styles = CITE.styles || await api("/api/cite/styles");
    const selEl = $("#citeStyle");
    selEl.replaceChildren(...CITE.styles.styles.filter((s) => !s.error).map((s) => {
      const o = document.createElement("option"); o.value = s.id; o.textContent = s.short || s.title; return o;
    }));
    selEl.value = CITE.styles.default;
    const site = citeSite(st.ctx.url || "");
    CITE.primary = site === "latex" ? "latex" : site === "formatted" ? "citation"
      : CITE.styles.copy === "citekey" ? "latex" : "citation";
    $("#citeLatex").classList.toggle("primary", CITE.primary === "latex");
    $("#citeCit").classList.toggle("primary", CITE.primary === "citation");
    citeHint(site === "latex" ? "On Overleaf: \\cite{…} is the main button. Keys never change once copied."
      : site === "formatted" ? "On Google Docs: copies are formatted. Paste the bibliography at the end."
      : "Enter selects a paper, ⌘/Ctrl+Enter copies.");
  } catch (e) {
    if (e.offline) { st.online = false; $("#offline").hidden = false; }
    citeHint(`Couldn't load citation styles: ${e.message}`, "err");
  }
  if (st.match && st.match.type === "paper" && !CITE.sel.size) CITE.sel.set(st.match.id, { id: st.match.id, title: st.match.title });
  citeSearch();
}

function citeClose() {
  $("#cite").hidden = true;
  CITE.open = false;
  $("#modeLbl").textContent = "";
  const p = CITE.prev || {};
  if (p.form) { $("#form").hidden = false; $("#modeLbl").textContent = "Clip"; }
  else if (p.done) $("#done").hidden = false;
  else $("#home").hidden = false;
}

function citeHint(text, kind = "") {
  const h = $("#citeHint"); h.textContent = text; h.className = "hint" + (kind ? " " + kind : "");
}

async function citeSearch() {
  const my = ++CITE.seq;
  const q = $("#citeQ").value.trim();
  try {
    const r = await api(`/api/cite/search?${new URLSearchParams({ q, limit: "25" })}`);
    if (my !== CITE.seq) return;
    CITE.hits = r; CITE.idx = 0; citeDraw();
  } catch (e) {
    if (my !== CITE.seq) return;
    if (e.offline) { st.online = false; $("#offline").hidden = false; }
    $("#citeList").innerHTML = `<div class="opt msg">${esc(e.offline ? "research_brain isn't running." : e.message)}</div>`;
  }
}

function citeDraw() {
  const rows = CITE.hits.slice();
  for (const h of CITE.sel.values()) if (!rows.some((x) => x.id === h.id)) rows.unshift(h);
  CITE.rows = rows;
  const box = $("#citeList");
  if (!rows.length) box.innerHTML = `<div class="opt msg">${$("#citeQ").value.trim() ? "No papers match." : "No papers in your library yet."}</div>`;
  else box.innerHTML = rows.map((h, i) => {
    const a = (h.authors || []).join(", ") + (h.more_authors ? " et al." : "");
    const meta = [a, h.year].filter(Boolean).join(" · ");
    return `<div class="opt${CITE.sel.has(h.id) ? " sel" : ""}${i === CITE.idx ? " on" : ""}" role="option" aria-selected="${CITE.sel.has(h.id)}" data-i="${i}">
      <span class="dot"></span><span class="lab"><span class="ttl">${esc(h.title)}</span>
      <span class="meta">${esc(meta)}${h.citekey ? `<span class="key">${esc(h.citekey)}</span>` : ""}</span></span></div>`;
  }).join("");
  box.querySelectorAll(".opt[data-i]").forEach((el) => el.addEventListener("click", () => { CITE.idx = +el.dataset.i; citeToggle(rows[CITE.idx]); }));
  $("#citeN").textContent = CITE.sel.size ? `${CITE.sel.size} selected` : "Pick one or more papers";
}

function citeToggle(h) {
  if (!h) return;
  if (CITE.sel.has(h.id)) CITE.sel.delete(h.id); else CITE.sel.set(h.id, h);
  citeDraw();
}

async function citeMake(what) {
  const ids = [...CITE.sel.keys()];
  if (what === "latex") {
    const r = await api("/api/cite/keys", { method: "POST", body: JSON.stringify({ ids }) });
    return { text: r.latex, html: "", label: r.latex };
  }
  const style = $("#citeStyle").value || (CITE.styles && CITE.styles.default) || "";
  const r = await api("/api/cite/format", { method: "POST", body: JSON.stringify({ ids, style, mode: what, format: "html" }) });
  return { text: r.text, html: r.html, label: what === "citation" ? r.text : `${r.entries.length} references` };
}

async function citeCopy(what) {
  if (!CITE.sel.size) { citeHint("Pick at least one paper first.", "err"); $("#citeQ").focus(); return; }
  const job = citeMake(what);
  try {
    if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({
        "text/plain": job.then((r) => new Blob([r.text || ""], { type: "text/plain" })),
        "text/html": job.then((r) => new Blob([r.html || esc(r.text || "")], { type: "text/html" })),
      })]);
    } else {
      await navigator.clipboard.writeText((await job).text || "");
    }
    const r = await job;
    citeHint(what === "latex" ? `Copied ${r.label}` : what === "citation" ? `Copied ${r.label}` : `Copied the bibliography (${r.label}).`, "ok");
  } catch (e) {
    let msg = e.message;
    try { await job; } catch (e2) { msg = e2.offline ? "research_brain isn't running." : e2.message; }
    citeHint(`Couldn't copy: ${msg}`, "err");
  }
}

$("#citeTabBtn").addEventListener("click", () => (CITE.open ? citeClose() : citeOpen()));
$("#citeBack").addEventListener("click", citeClose);
$("#citeLatex").addEventListener("click", () => citeCopy("latex"));
$("#citeCit").addEventListener("click", () => citeCopy("citation"));
$("#citeBib").addEventListener("click", () => citeCopy("bibliography"));
let citeT = null;
$("#citeQ").addEventListener("input", () => { clearTimeout(citeT); citeT = setTimeout(citeSearch, 150); });
$("#citeQ").addEventListener("keydown", (e) => {
  const rows = CITE.rows || [];
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (rows.length) { CITE.idx = (CITE.idx + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length; citeDraw(); }
  } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault(); e.stopPropagation(); citeCopy(CITE.primary);
  } else if (e.key === "Enter") {
    e.preventDefault(); citeToggle(rows[CITE.idx]);
  }
});
$("#citeStyle").addEventListener("change", () => {
  api("/api/cite/settings", { method: "PUT", body: JSON.stringify({ style: $("#citeStyle").value }) }).catch(() => { });
});
// clip.js may show its home view later (server check, init); while Cite is open, keep the others hidden
// and remember which one to return to
new MutationObserver(() => {
  if (!CITE.open) return;
  for (const [sel, k] of [["#home", "home"], ["#form", "form"], ["#done", "done"]]) {
    if (!$(sel).hidden) { CITE.prev = { [k]: true }; $(sel).hidden = true; }
  }
}).observe($("#app"), { subtree: true, attributes: true, attributeFilter: ["hidden"] });
// clip.html?cite=1 opens straight on the Cite tab
// (after clip.js has worked out which tab this is: the page decides the main button)
if (new URLSearchParams(location.search).get("cite") === "1") {
  (async () => {
    for (let i = 0; i < 60 && !st.view; i++) await new Promise((r) => setTimeout(r, 50));
    citeOpen();
  })();
}
