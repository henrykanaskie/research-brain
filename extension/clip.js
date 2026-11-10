// research_brain extension: toolbar popup / clip window UI
// No AI in this extension: it talks only to the research_brain server on this computer.
"use strict";

const DEFAULT_SERVER = "http://127.0.0.1:8765";
const PENDING_KEY = "rb_pending_clip";
const DRAFT_KEY = "rb_draft";
const AUTO = "__auto__";
const LOCAL_RE = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isWindow = new URLSearchParams(location.search).get("mode") === "window";
if (isWindow) document.body.classList.add("window");

const st = {
  server: DEFAULT_SERVER,
  online: false,
  ctx: { quote: "", url: "", title: "", tabId: null, frameId: 0 },
  match: null,          // {id,title,type} from /api/match
  results: [],          // search results
  target: AUTO,
  saving: false,
  saveMode: "link",     // Library settings: "link" (link + text) or "pdf" (link + text + PDF)
};

/* ── storage helpers ─────────────────────────────── */
const sess = chrome.storage.session || chrome.storage.local;
async function getServer() {
  try {
    const { server } = await chrome.storage.local.get("server");
    const v = (server || DEFAULT_SERVER).replace(/\/+$/, "");
    return LOCAL_RE.test(v) ? v : DEFAULT_SERVER; // only this computer
  } catch { return DEFAULT_SERVER; }
}
async function takePending() {
  let p = null;
  for (const area of [chrome.storage.session, chrome.storage.local].filter(Boolean)) {
    try {
      const o = await area.get(PENDING_KEY);
      if (o[PENDING_KEY]) { p = p || o[PENDING_KEY]; await area.remove(PENDING_KEY); }
    } catch { }
  }
  return p && Date.now() - (p.ts || 0) < 120000 ? p : null;
}

/* ── API ─────────────────────────────────────────── */
async function api(path, opts = {}, timeoutMs = 20000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  let r;
  try {
    r = await fetch(st.server + path, { ...opts, signal: ctl.signal, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  } catch (e) {
    const err = new Error("offline"); err.offline = true; throw err;
  } finally { clearTimeout(t); }
  let data = null; try { data = await r.json(); } catch { }
  if (!r.ok) { const e = new Error((data && data.detail) || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return data;
}

/* ── context: what is the user looking at? ───────── */
async function activeTab() {
  // clip.html?tab=<id> (opened as a tab, e.g. in tests): describe that tab instead of the focused one
  const forced = +new URLSearchParams(location.search).get("tab");
  if (forced) { try { return await chrome.tabs.get(forced); } catch { } }
  try { const [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true }); return t || null; }
  catch { return null; }
}
function isPdfUrl(url) { return /\.pdf($|[?#])/i.test(url || "") || /^https?:\/\/(www\.)?arxiv\.org\/pdf\//i.test(url || ""); }
function isScriptable(url) { return /^(https?|file):/i.test(url || "") && !isPdfUrl(url); }

// Runs in the page: selection text with line breaks + nearest preceding section heading.
function pageProbe() {
  const sel = window.getSelection();
  const text = sel ? sel.toString() : "";
  let heading = "";
  try {
    if (sel && sel.rangeCount) {
      let node = sel.getRangeAt(0).startContainer;
      const el = node.nodeType === 1 ? node : node.parentElement;
      const hs = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")];
      let best = null;
      for (const h of hs) {
        if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING || h.contains(el)) best = h; else break;
      }
      if (best && best.tagName !== "H1") heading = best.textContent.replace(/\s+/g, " ").trim();
    }
  } catch { }
  const meta = (n) => (document.querySelector(`meta[name="${n}" i]`) || {}).content || "";
  let ld = false;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    if (/"@type"\s*:\s*(\[[^\]]*)?"(Scholarly|Medical|Tech)?Article"/.test(s.textContent || "")) { ld = true; break; }
  }
  const article = !!(meta("citation_title") || meta("citation_doi") || (meta("dc.title") && meta("dc.identifier")) || ld
    || /arxiv\.org\/(abs|html)\//i.test(location.href));
  return { text, heading, citationTitle: meta("citation_title"), pdfUrl: meta("citation_pdf_url"),
           contentType: document.contentType || "", doi: meta("citation_doi") || meta("dc.identifier"), article };
}
async function probe(tabId, frameId) {
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId, frameIds: [frameId || 0] }, func: pageProbe,
    });
    return res && res.result;
  } catch { return null; } // chrome://, Web Store, PDF viewer, no permission… → fine
}
function cleanQuote(t) {
  return String(t || "").replace(/­/g, "").replace(/([A-Za-z])-\n\s*([a-z])/g, "$1$2")
    .replace(/[ \t]+/g, " ").replace(/\s*\n\s*/g, " ").trim();
}

/* ── init ────────────────────────────────────────── */
async function init() {
  st.server = await getServer();
  $("#serverIn").value = st.server;

  const pending = await takePending();
  const tab = await activeTab();
  if (pending) st.ctx = { ...pending };
  else if (tab) st.ctx = { quote: "", url: tab.url || "", title: tab.title || "", tabId: tab.id, frameId: 0 };

  // Better selection from the page itself (keeps line breaks; gives us a section heading).
  if (st.ctx.tabId != null && /^(https?|file):/i.test(st.ctx.url || "")) {
    const p = isScriptable(st.ctx.url) || !pending ? await probe(st.ctx.tabId, st.ctx.frameId) : null;
    if (p) {
      if (p.text && p.text.trim() && isScriptable(st.ctx.url)) st.ctx.quote = p.text;
      if (p.heading) st.ctx.heading = p.heading.slice(0, 80);
      if (p.citationTitle) st.ctx.citationTitle = p.citationTitle;
      if (p.contentType) st.ctx.contentType = p.contentType;
      if (p.pdfUrl) st.ctx.pdfUrl = p.pdfUrl;
      if (p.doi) st.ctx.doi = p.doi;
      if (p.article) st.ctx.article = true;
    }
  }
  st.ctx.pdf = isPdfUrl(st.ctx.url) || /application\/pdf/i.test(st.ctx.contentType || "");
  st.ctx.quote = cleanQuote(st.ctx.quote);
  // arXiv tab titles look like "[2401.01234] Title"; drop the id prefix.
  st.ctx.title = String(st.ctx.title || "").replace(/^\[[\w.\/-]+\]\s*/, "");

  // A context-menu clip or a live selection opens the quick-clip form; otherwise the home view.
  if (pending || st.ctx.quote || isWindow) await showForm();
  else showHome();
  checkServer();
}

async function showForm() {
  st.view = "form";
  $("#home").hidden = true; $("#form").hidden = false;
  renderContext();
  await restoreDraft();
  attachEditors();
}

/* ── home view: this page's paper, annotate, note count ── */
function showHome() {
  st.view = "home";
  $("#form").hidden = true; $("#home").hidden = false;
  $("#modeLbl").textContent = "";
  const c = st.ctx;
  const host = (() => { try { return new URL(c.url).host; } catch { return ""; } })();
  $("#homeSrc").innerHTML = `<span class="t">${esc(c.citationTitle || c.title || "Untitled page")}</span>${host ? `<span class="h">${esc(host)}</span>` : ""}`;
  $("#homeSrc").title = c.url || "";
  const btn = $("#annotateBtn");
  const web = /^(https?|file):/i.test(c.url || "");
  if (c.pdf) {
    btn.textContent = "Annotate this PDF in research_brain";
    $("#annotateHint").textContent = "Chrome's PDF viewer can't be annotated by extensions, so the PDF opens in research_brain's viewer, where you can highlight and write notes.";
    btn.disabled = !/^https?:/i.test(c.url || "");
    if (btn.disabled) $("#annotateHint").textContent = "Only PDFs from http or https links can be imported. Drop local files on research_brain instead.";
  } else {
    btn.textContent = "Annotate this page";
    $("#annotateHint").innerHTML = web ? `Highlight passages and write notes right on the page. <kbd>${/Mac/.test(navigator.platform) ? "⌥⇧A" : "Alt+Shift+A"}</kbd>` : "Chrome doesn't let extensions annotate this page.";
    btn.disabled = !web;
  }
  renderSaveLink();
  renderSavePdf();
  renderHomePaper();
}

/* ── Save link + text: the article as you see it (your browser, your access), without the PDF file ── */
function linkable() {
  const c = st.ctx;
  if (st.ctx.tabId == null || !/^https?:/i.test(c.url || "")) return false;
  return !!(c.pdf || c.article);
}
function renderSaveLink() {
  const show = linkable();
  const btn = $("#saveLinkBtn"), hint = $("#saveLinkHint");
  btn.hidden = hint.hidden = !show;
  if (!show) { $("#annotateBtn").classList.add("primary"); return; }
  const withPdf = st.saveMode === "pdf" && !!savePdfUrl();
  btn.textContent = withPdf ? "Save to research_brain (link + text + PDF)" : "Save to research_brain (link + text)";
  hint.textContent = st.ctx.pdf
    ? (withPdf ? "Reads this PDF in your browser, with your own access, and saves its text and the PDF file." : "Reads this PDF in your browser, with your own access, and saves its link and text page by page, without the PDF file.")
    : (withPdf ? "Saves the article’s link, full text and citation details, and downloads its PDF in your browser with your own access." : "Saves the article’s link, full text and citation details as you see them here, without the PDF file. You can add the PDF later.");
  btn.disabled = !st.online;
  $("#annotateBtn").classList.remove("primary");  // saving is the main thing to do on an article page
}

/* ── Save PDF: the browser downloads it with the user's own access; the bytes go to the local app ── */
function savePdfUrl() {
  const c = st.ctx;
  if (c.pdf && /^https?:/i.test(c.url || "")) return c.url;
  if (c.pdfUrl && /^https?:/i.test(c.pdfUrl)) return c.pdfUrl;
  return "";
}
function renderSavePdf() {
  const show = !!savePdfUrl() && st.ctx.tabId != null;
  const btn = $("#savePdfBtn");
  btn.hidden = !show; $("#savePdfHint").hidden = !show || linkable();
  btn.disabled = !st.online;
  if (!st.savingPdf) btn.textContent = pdfLabel();
}
function pdfLabel() {
  if (!linkable()) return "Save PDF to research_brain";
  return st.saveMode === "pdf" ? "Save link + text only" : "Save PDF too";
}
const SAVE_MAX = 150 << 20;
async function inTab(func, args) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId: st.ctx.tabId }, func, args: args || [] });
  return res ? res.result : null;
}
/** Download `url` inside the tab (grab_pdf.js: the page's own session and access), bytes back in chunks. */
async function grabPdf(url, progress) {
  await chrome.scripting.executeScript({ target: { tabId: st.ctx.tabId }, files: ["grab_pdf.js"] });
  const got = await inTab((u, max) => window.__rbGrab.start(u, max), [url, SAVE_MAX]);
  if (!got || !got.ok) throw new Error((got && got.error) || "Chrome doesn't allow extensions on this page.");
  const bytes = new Uint8Array(got.size);
  for (let i = 0, off = 0; i < got.chunks; i++) {
    const b64 = await inTab((k) => window.__rbGrab.chunk(k), [i]);
    if (b64 == null) throw new Error("The download was interrupted. Try again.");
    const bin = atob(b64);
    for (let j = 0; j < bin.length; j++) bytes[off + j] = bin.charCodeAt(j);
    off += bin.length;
    if (progress) progress(Math.round((i + 1) / got.chunks * 100));
  }
  inTab(() => window.__rbGrab.clear()).catch(() => {});
  return { bytes, finalUrl: got.finalUrl || url };
}
/** POST the PDF bytes: keep=false keeps only its text (a linked paper), noteId attaches to that paper. */
async function postPdf(bytes, sourceUrl, { keep = true, noteId = "" } = {}) {
  const q = new URLSearchParams({ source_url: sourceUrl, doi: st.ctx.doi || "", title: st.ctx.citationTitle || "" });
  if (!keep) q.set("keep_pdf", "0");
  if (noteId) q.set("note", noteId);
  return api(`/api/import/pdf-bytes?${q}`, { method: "POST", body: bytes, headers: { "Content-Type": "application/pdf" } }, 300000);
}
async function savePdf() {
  const btn = $("#savePdfBtn");
  if (btn.disabled) return;
  // with "Link + text + PDF" as the main action, this button is the plain link + text save
  if (linkable() && st.saveMode === "pdf") return saveLinked({ withPdf: false, btn });
  const url = savePdfUrl();
  if (!url) return;
  hideHomeErr();
  st.savingPdf = true;
  btn.disabled = true; btn.textContent = "Downloading the PDF";
  try {
    const got = await grabPdf(url, (pc) => { btn.textContent = `Saving the PDF (${pc}%)`; });
    const n = await postPdf(got.bytes, got.finalUrl, { noteId: st.match && st.match.type === "paper" ? st.match.id : "" });
    await chrome.tabs.create({ url: st.server + (n.viewer || `/viewer/${encodeURIComponent(n.id)}`) });
    window.close();
  } catch (e) {
    if (e.offline) { st.online = false; $("#offline").hidden = false; }
    else showHomeErr(`Couldn't save this PDF: ${e.message}`);
    btn.disabled = false; btn.textContent = pdfLabel();
  } finally { st.savingPdf = false; }
}
/** "Save to research_brain (link + text)": read the article in this tab (extract.js) or this PDF (its text,
 *  page by page, read by research_brain from the bytes your browser fetched; the file itself isn't kept). */
async function saveLinked({ withPdf = st.saveMode === "pdf", btn = $("#saveLinkBtn") } = {}) {
  if (btn.disabled || !linkable()) return;
  hideHomeErr();
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = st.ctx.pdf ? "Reading the PDF" : "Reading the article";
  try {
    let n;
    if (st.ctx.pdf) {
      const got = await grabPdf(st.ctx.url, (pc) => { btn.textContent = `Reading the PDF (${pc}%)`; });
      btn.textContent = "Saving";
      n = await postPdf(got.bytes, st.ctx.url, { keep: !!withPdf });
    } else {
      await chrome.scripting.executeScript({ target: { tabId: st.ctx.tabId }, files: ["extract.js"] });
      const data = await inTab(() => window.__rbExtract.run());
      if (!data || !(data.text || data.title)) throw new Error("No article text was found on this page.");
      btn.textContent = "Saving";
      n = await api("/api/import/linked", { method: "POST", body: JSON.stringify(data) }, 60000);
      n._partial = !!data.partial;
      n._chars = data.chars || 0;
      if (withPdf && !n.has_pdf && savePdfUrl()) {
        btn.textContent = "Downloading the PDF";
        try {
          const got = await grabPdf(savePdfUrl(), (pc) => { btn.textContent = `Saving the PDF (${pc}%)`; });
          n = Object.assign(await postPdf(got.bytes, got.finalUrl, { noteId: n.id }), { _partial: false });
        } catch (e) {
          if (e.offline) throw e;
          n._pdfError = e.message;
        }
      }
    }
    showSaved(n);
  } catch (e) {
    if (e.offline) { st.online = false; $("#offline").hidden = false; }
    else showHomeErr(`Couldn't save this article: ${e.message}`);
    btn.disabled = false; btn.textContent = label;
  }
}
function showSaved(n) {
  st.saved = n;
  $("#home").hidden = true; $("#form").hidden = true; $("#offline").hidden = true; $("#done").hidden = false;
  const kind = n.has_pdf ? "Saved with its PDF."
    : n._partial ? "Linked article: only the abstract is on this page. Open the full text (HTML or PDF) and save again to add it."
    : "Linked article: its link, full text and citation details are saved, without the PDF file.";
  const was = n.duplicate ? (n.text_attached ? "Already in your library; the text was added." : "Already in your library.") : "";
  $("#doneSub").innerHTML = `<b>${esc(n.title || n.id)}</b><span class="kind">${esc([was, kind].filter(Boolean).join(" "))}${n._pdfError ? ` The PDF couldn't be saved: ${esc(n._pdfError)}` : ""}</span>`;
  $("#anotherBtn").hidden = true;
  $("#donePdfBtn").hidden = !!n.has_pdf || !savePdfUrl();
  const url = st.server + (n.open || `/#note=${encodeURIComponent(n.id)}`);
  const a = $("#openFrag"); a.href = url;
  a.onclick = (e) => { e.preventDefault(); openUrl(url); };
  a.focus();
}
async function donePdf() {
  const btn = $("#donePdfBtn"), n = st.saved;
  if (!n || btn.disabled) return;
  btn.disabled = true; btn.textContent = "Downloading the PDF";
  try {
    const got = await grabPdf(savePdfUrl(), (pc) => { btn.textContent = `Saving the PDF (${pc}%)`; });
    const r = await postPdf(got.bytes, got.finalUrl, { noteId: n.id });
    btn.hidden = true;
    $("#doneSub .kind").textContent = "Saved with its PDF.";
    st.saved = r;
  } catch (e) {
    btn.disabled = false; btn.textContent = "Save PDF too";
    $("#doneSub .kind").textContent = `Couldn't save the PDF: ${e.message}`;
  }
}
function importableKind(url) {
  if (/arxiv\.org\/(abs|pdf|html)\//i.test(url || "")) return "arXiv";
  if (/(^|\/\/)(dx\.)?doi\.org\/|\b10\.\d{4,9}\/\S+/i.test(url || "")) return "DOI";
  return "";
}
function renderHomePaper() {
  const el = $("#homePaper");
  const p = st.match;
  if (p) {
    const ref = st.paperRef || {};
    const by = [(ref.authors || []).slice(0, 3).join(", ") + ((ref.authors || []).length > 3 ? " et al." : ""), ref.year].filter(Boolean).join(", ");
    const kind = ref.source === "linked" && !ref.has_pdf ? " · linked article, no PDF" : "";
    el.innerHTML = `<span class="lab">In your library${esc(kind)}</span><span class="pt">${esc(p.title)}</span>${by ? `<span class="by">${esc(by)}</span>` : ""}`;
  } else if (st.online && importableKind(st.ctx.url)) {
    el.innerHTML = `<span class="lab">Not in your library yet</span><span class="by">Added by its ${importableKind(st.ctx.url)} id, with authors and year for citations, when you save your first note.</span>`;
  } else if (st.online && /^https?:/i.test(st.ctx.url || "") && !st.ctx.pdf) {
    el.innerHTML = `<span class="lab">Not in your library yet</span><span class="by">Your first note here creates a source note for this page.</span>`;
  } else el.innerHTML = "";
  const n = st.pageCount;
  $("#noteCount").textContent = n == null ? "" : n === 0 ? "No notes on this page" : n === 1 ? "1 note on this page" : `${n} notes on this page`;
  if (st.annotating && !st.ctx.pdf) $("#annotateBtn").textContent = "Stop annotating";
}
async function loadHome() {
  if (!st.online || !/^https?:/i.test(st.ctx.url || "")) { renderHomePaper(); return; }
  try {
    const r = await api(`/api/fragments?url=${encodeURIComponent(st.ctx.url)}`, {}, 5000);
    st.pageCount = (r.fragments || []).length;
    if (r.paper) { st.match = { id: r.paper.id, title: r.paper.title, type: r.paper.type }; st.paperRef = r.paper; }
  } catch { }
  try {
    if (st.ctx.tabId != null) {
      const s = await chrome.tabs.sendMessage(st.ctx.tabId, { type: "rb-annotate-state" });
      st.annotating = !!(s && s.on);
    }
  } catch { st.annotating = false; } // not injected
  renderHomePaper();
}
async function annotate() {
  const btn = $("#annotateBtn");
  if (btn.disabled) return;
  hideHomeErr();
  if (st.ctx.pdf) return annotatePdf();
  const r = await chrome.runtime.sendMessage({ type: "rb-toggle-annotate", on: !st.annotating, tabId: st.ctx.tabId });
  if (r && r.pdf) { st.ctx.pdf = true; showHome(); return; }
  if (!r || !r.ok) { showHomeErr((r && r.error) || "Couldn't start annotate mode on this page."); return; }
  window.close();
}
async function annotatePdf() {
  const btn = $("#annotateBtn");
  btn.disabled = true; btn.textContent = "Importing the PDF";
  try {
    const n = await api("/api/import/url", { method: "POST", body: JSON.stringify({ url: st.ctx.url }) }, 180000);
    const url = st.server + (n.viewer || `/viewer/${encodeURIComponent(n.id)}`);
    await chrome.tabs.create({ url, index: undefined });
    window.close();
  } catch (e) {
    if (e.offline) { st.online = false; $("#offline").hidden = false; }
    else showHomeErr(`Couldn't import this PDF: ${e.message}` + (savePdfUrl() ? " If the site needs you to be signed in, use Save PDF to research_brain: it downloads with your browser's access." : ""));
    btn.disabled = false; btn.textContent = "Annotate this PDF in research_brain";
  }
}
function showHomeErr(m) { const el = $("#err"); el.textContent = m; el.hidden = false; $("#home").appendChild(el); }
function hideHomeErr() { $("#err").hidden = true; }

function renderContext() {
  const c = st.ctx;
  const host = (() => { try { return new URL(c.url).host; } catch { return ""; } })();
  $("#src").innerHTML = `<span class="t">${esc(c.citationTitle || c.title || "Untitled page")}</span>${host ? `<span class="h">${esc(host)}</span>` : ""}`;
  $("#src").title = c.url || "";
  $("#quote").value = c.quote;
  const quickNote = !c.quote;
  $("#modeLbl").textContent = quickNote ? "Quick note" : "Clip";
  if (quickNote) {
    $("#quote").rows = 2;
    $("#quote").placeholder = "Optional. Paste a passage, or just write a note below";
  }
  $("#pdfHint").hidden = !(quickNote && /\.pdf($|[?#])/i.test(c.url || ""));
  const m = /[#&]page=(\d+)/.exec(c.url || "");
  $("#locator").value = c.heading ? c.heading : m ? `p.${m[1]}` : "";
  setTimeout(() => $("#note").focus(), 30);
}

/* ── LaTeX input (vendor/latex-editor.js, same module the app uses) ── */
const ED = { quote: null, note: null, quoteMode: null };
const hasMath = (s) => /\$|\\\(|\\\[/.test(s);
const looksLikeProse = (t) => /\b[a-z]{3,}\s+[a-z]{2,}\s+[a-z]{3,}\b/i.test(t || "");
function attachEditors() {
  const LE = window.LatexEditor;
  if (!LE) return;
  // An equation-like selection is edited as pure TeX ("math"); prose stays markdown with $…$.
  const q = $("#quote").value;
  const mode = q.trim() && !looksLikeProse(q) ? "math" : "markdown";
  if (ED.quote && ED.quoteMode !== mode) { ED.quote.detach(); ED.quote = null; }
  if (!ED.quote) {
    ED.quote = LE.attach($("#quote"), {
      preview: $("#quotePrev"), mode, toolbar: $("#tools"),
      render: (src, m) => (src.trim() && (m === "math" || hasMath(src)) ? LE.render(src, { mode: m }) : ""),
    });
    ED.quoteMode = mode;
    $("#tools").lastElementChild.dataset.for = "quote";
  }
  if (!ED.note) {
    ED.note = LE.attach($("#note"), {
      preview: $("#notePrev"), mode: "markdown", toolbar: $("#tools"),
      render: (src) => (hasMath(src) || /[*_`#]/.test(src) ? LE.render(src, { mode: "markdown" }) : ""),
    });
    $("#tools").lastElementChild.dataset.for = "note";
    $("#quote").addEventListener("focus", () => showSym("quote"));
    $("#note").addEventListener("focus", () => showSym("note"));
  }
  ED.quote.render(); ED.note.render();
  showSym(document.activeElement === $("#quote") ? "quote" : "note");
}
// One "Symbols" control in the footer for whichever field was focused last.
function showSym(which) {
  document.querySelectorAll("#tools .le-sym-btn").forEach((b) => (b.hidden = b.dataset.for !== which));
}

/* ── drafts (popups close when you click away) ───── */
function draftKey() { return DRAFT_KEY + ":" + (st.ctx.url || "") + ":" + (st.ctx.quote || "").slice(0, 80); }
async function restoreDraft() {
  try {
    const k = draftKey(); const o = await sess.get(k); const d = o[k];
    if (d) { if (d.note) $("#note").value = d.note; if (d.locator) $("#locator").value = d.locator; if (d.quote) $("#quote").value = d.quote; }
  } catch { }
}
let draftT;
function saveDraft() {
  clearTimeout(draftT);
  draftT = setTimeout(() => {
    try { sess.set({ [draftKey()]: { note: $("#note").value, locator: $("#locator").value, quote: $("#quote").value } }); } catch { }
  }, 250);
}
function clearDraft() { try { sess.remove(draftKey()); } catch { } }
["#note", "#locator", "#quote"].forEach((s) => $(s).addEventListener("input", saveDraft));

/* ── server status, match, search ────────────────── */
async function checkServer() {
  $("#targets").innerHTML = `<div class="opt msg">Connecting to ${esc(st.server)}</div>`;
  try {
    await api("/api/status", {}, 3000);
    st.online = true;
  } catch (e) {
    st.online = false;
  }
  $("#offline").hidden = st.online;
  $("#saveBtn").disabled = !st.online;
  if (st.online) {
    try { st.saveMode = ((await api("/api/import/linked/prefs", {}, 3000)) || {}).save_mode || "link"; } catch { st.saveMode = "link"; }
  }
  if (st.view === "home") $("#annotateBtn").disabled = !st.online || $("#annotateBtn").disabled;
  if (!st.online) { renderTargets(); return; }

  if (st.ctx.url && /^https?:/i.test(st.ctx.url)) {
    try {
      const r = await api(`/api/match?url=${encodeURIComponent(st.ctx.url)}`, {}, 5000);
      st.match = r && r.note ? r.note : null;
      if (st.match) st.target = st.match.id;
    } catch { }
  }
  if (st.view === "home") { if (/^(https?|file):/i.test(st.ctx.url || "")) $("#annotateBtn").disabled = false; showHome(); await loadHome(); }
  await doSearch("");
}
let searchT, searchSeq = 0;
$("#search").addEventListener("input", (e) => { clearTimeout(searchT); searchT = setTimeout(() => doSearch(e.target.value), 180); });
async function doSearch(q) {
  if (!st.online) return;
  const seq = ++searchSeq;
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(q.trim())}&limit=${q.trim() ? 12 : 6}`, {}, 5000);
    if (seq !== searchSeq) return;
    st.results = Array.isArray(r) ? r.filter((x) => x.type !== "fragment") : [];
  } catch (e) {
    if (e.offline) { st.online = false; $("#offline").hidden = false; $("#saveBtn").disabled = true; }
    st.results = [];
  }
  renderTargets(q.trim());
}
function renderTargets(q = "") {
  const box = $("#targets");
  if (!st.online) { box.innerHTML = `<div class="opt msg">Not connected</div>`; return; }
  const opts = [];
  const autoDesc = st.match ? "Uses the paper matched to this page" : "Matches this page, imports it by arXiv id or DOI, or creates a source note";
  opts.push({ id: AUTO, title: "Automatic", meta: autoDesc });
  if (st.match) opts.push({ id: st.match.id, title: st.match.title, meta: st.match.type === "paper" ? "Paper" : "Note", tag: "Matches this page" });
  // keep a manually picked target visible even if not in current results
  const picked = st.target !== AUTO && !opts.some((o) => o.id === st.target) ? (st._pickedObj ? [st._pickedObj] : []) : [];
  for (const o of picked) opts.push(o);
  for (const r of st.results) {
    if (opts.some((o) => o.id === r.id)) continue;
    opts.push({ id: r.id, title: r.title || r.id, meta: [r.type === "paper" ? "Paper" : "Note", r.year].filter(Boolean).join(", ") });
  }
  box.innerHTML = opts.map((o) => `
    <div class="opt${o.id === st.target ? " sel" : ""}" role="option" data-id="${esc(o.id)}" aria-selected="${o.id === st.target}">
      <span class="dot"></span>
      <span class="lab"><span class="ttl">${esc(o.title)}</span>
      <span class="meta">${esc(o.meta || "")}${o.tag ? `<span class="tag">${esc(o.tag)}</span>` : ""}</span></span>
    </div>`).join("") + (q && !st.results.length ? `<div class="opt msg">Nothing in your library matches “${esc(q)}”</div>` : "");
  box.querySelectorAll(".opt[data-id]").forEach((el) => el.addEventListener("click", () => {
    st.target = el.dataset.id;
    const o = opts.find((x) => x.id === st.target);
    st._pickedObj = o && o.id !== AUTO ? o : null;
    renderTargets(q);
  }));
}

/* ── save ────────────────────────────────────────── */
async function save() {
  if (st.saving || !st.online) return;
  const body = {
    quote: $("#quote").value.trim(),
    url: st.ctx.url || "",
    page_title: st.ctx.citationTitle || st.ctx.title || "",
    note: $("#note").value.trim(),
    locator: $("#locator").value.trim(),
    target_id: st.target === AUTO ? null : st.target,
  };
  if (!body.quote && !body.note) { showErr("Add a quote or write a note first."); return; }
  st.saving = true;
  const btn = $("#saveBtn"); btn.disabled = true;
  btn.textContent = body.target_id || st.match ? "Saving" : "Saving, may import the paper";
  hideErr();
  try {
    const r = await api("/api/clip", { method: "POST", body: JSON.stringify(body) }, 60000);
    clearDraft();
    showDone(r);
  } catch (e) {
    if (e.offline) { st.online = false; $("#offline").hidden = false; }
    else showErr(`Couldn't save: ${e.message}`);
    btn.disabled = !st.online;
  } finally {
    st.saving = false; btn.textContent = "Save";
  }
}
function showDone(r) {
  const f = r.fragment || {}, p = r.parent || {};
  $("#form").hidden = true; $("#offline").hidden = true; $("#done").hidden = false;
  $("#anotherBtn").hidden = false; $("#donePdfBtn").hidden = true;
  $("#doneSub").innerHTML = `${f.quote ? `<span class="q">${esc(f.quote.length > 220 ? f.quote.slice(0, 220) + "…" : f.quote)}</span>` : ""}Filed under <b>${esc(p.title || p.id || "?")}</b>${r.created_parent ? `, <span class="new">added to your library as a new ${p.type === "paper" ? "paper" : "note"}</span>` : ""}.`;
  const url = `${st.server}/#note=${encodeURIComponent(f.id || "")}`;
  const a = $("#openFrag"); a.href = url;
  a.onclick = (e) => { e.preventDefault(); openUrl(url); };
  $("#openFrag").focus();
}
function showErr(m) { const el = $("#err"); el.textContent = m; el.hidden = false; }
function hideErr() { $("#err").hidden = true; }

async function openUrl(url) {
  // Reuse an existing research_brain tab if there is one.
  try {
    const tabs = await chrome.tabs.query({ url: [`${st.server}/*`] });
    const t = tabs.find((x) => !/\/viewer\//.test(x.url || ""));
    if (t) {
      await chrome.tabs.update(t.id, { url, active: true });
      await chrome.windows.update(t.windowId, { focused: true });
    } else await chrome.tabs.create({ url });
  } catch { await chrome.tabs.create({ url }); }
  window.close();
}

/* ── wiring ──────────────────────────────────────── */
$("#saveBtn").addEventListener("click", save);
$("#annotateBtn").addEventListener("click", annotate);
$("#savePdfBtn").addEventListener("click", savePdf);
$("#saveLinkBtn").addEventListener("click", () => saveLinked());
$("#donePdfBtn").addEventListener("click", donePdf);
$("#openAppHome").addEventListener("click", () => openUrl(`${st.server}/`));
$("#quickBtn").addEventListener("click", async () => { await showForm(); renderTargets(); });
$("#openApp").addEventListener("click", () => openUrl(`${st.server}/`));
$("#retryBtn").addEventListener("click", checkServer);
$("#anotherBtn").addEventListener("click", () => {
  $("#done").hidden = true; $("#form").hidden = false; st.view = "form";
  $("#quote").value = ""; $("#note").value = ""; st.ctx.quote = ""; renderContext(); attachEditors(); renderTargets();
});
$("#gearBtn").addEventListener("click", () => { $("#settings").hidden = !$("#settings").hidden; if (!$("#settings").hidden) $("#serverIn").focus(); });
$("#serverSave").addEventListener("click", async () => {
  let v = $("#serverIn").value.trim().replace(/\/+$/, "") || DEFAULT_SERVER;
  if (!/^https?:\/\//i.test(v)) v = "http://" + v;
  if (!LOCAL_RE.test(v)) { $("#serverIn").value = st.server; $("#settings .hint").innerHTML = "That address isn't on this computer. Use <code>http://127.0.0.1:PORT</code> or <code>http://localhost:PORT</code>."; return; }
  $("#serverIn").value = v;
  await chrome.storage.local.set({ server: v });
  st.server = v; $("#settings").hidden = true;
  checkServer();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!$("#form").hidden) save(); }
  else if (e.key === "Enter" && st.view === "home" && document.activeElement === document.body) { e.preventDefault(); annotate(); }
  else if (e.key === "Escape" && isWindow && !document.querySelector(".le-pop:not([hidden]), .le-pal:not([hidden])")) window.close();
});

init().catch((e) => { console.error(e); showErr(String(e && e.message || e)); });
