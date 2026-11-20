// research_brain extension: background service worker (MV3)
//
// There is no AI in this extension. It talks to exactly one place: the research_brain server on this
// computer (http://127.0.0.1:8765 by default; the setting only accepts 127.0.0.1 / localhost).
//
// What it does:
//   - Context menu "Add to research_brain…" on a selection: opens the clip popup prefilled (quick clip).
//   - Annotate mode (toolbar popup button or Alt+Shift+A): injects annotate.js into the current tab on demand
//     (activeTab + scripting; there is no always-on content script and no broad host permission).
//   - Relays annotate.js / note.html requests to the local server (pages can't reach it themselves:
//     mixed content and page CSPs), and passes messages between the note iframe and its page.

const MENU_ID = "rb-clip";
const PENDING_KEY = "rb_pending_clip";
const DEFAULT_SERVER = "http://127.0.0.1:8765";
const LOCAL_RE = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;

/* ── settings ─────────────────────────────────────── */
async function server() {
  try {
    const { server: s } = await chrome.storage.local.get("server");
    const v = String(s || DEFAULT_SERVER).replace(/\/+$/, "");
    return LOCAL_RE.test(v) ? v : DEFAULT_SERVER;  // never anything but this computer
  } catch { return DEFAULT_SERVER; }
}

// Paths annotate.js / note.html may call. Everything else is refused here.
const ALLOWED_API = [
  /^\/api\/status$/,
  /^\/api\/match\?url=/,
  /^\/api\/fragments\?url=/,
  /^\/api\/clip$/,
];

async function callServer(path, method = "GET", body = null) {
  if (!ALLOWED_API.some((re) => re.test(path))) return { ok: false, status: 0, error: "Not allowed" };
  const base = await server();
  try {
    const r = await fetch(base + path, {
      method, headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null; try { data = await r.json(); } catch { }
    return r.ok ? { ok: true, status: r.status, data } : { ok: false, status: r.status, error: (data && data.detail) || `HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, status: 0, offline: true, error: "research brain isn't open. Open it (Start menu, Launchpad or the Dock), then try again." };
  }
}

/* ── context menu quick clip (unchanged flow) ─────── */
// Right-click a PDF (Chrome's PDF viewer, a PDF opened from your computer, or a link to one) to add it.
const MENU_PDF_PAGE = "rb-add-pdf";
const MENU_PDF_LINK = "rb-add-pdf-link";
const PDF_PATTERNS = [
  "*://*/*.pdf", "*://*/*.pdf?*", "*://*/*.pdf#*", "*://*/*.PDF", "*://*/*.PDF?*",
  "*://*/*/pdf/*", "*://*/*/epdf/*", "*://*/*/pdfdirect/*", "*://*/*/pdfft*",  // publishers' PDF routes
  "file:///*.pdf", "file:///*.PDF",
];
function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_ID, title: "Add to research_brain…", contexts: ["selection"] });
    chrome.contextMenus.create({ id: MENU_PDF_PAGE, title: "Add this PDF to research brain", contexts: ["page", "frame"],
      documentUrlPatterns: PDF_PATTERNS });
    chrome.contextMenus.create({ id: MENU_PDF_LINK, title: "Add linked PDF to research brain", contexts: ["link"],
      targetUrlPatterns: PDF_PATTERNS });
  });
}
chrome.runtime.onInstalled.addListener(createMenus);
chrome.runtime.onStartup.addListener(createMenus);

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === MENU_PDF_PAGE || info.menuItemId === MENU_PDF_LINK) {
    await addPdf(info.menuItemId === MENU_PDF_LINK ? info.linkUrl : (info.frameUrl || info.pageUrl || (tab && tab.url) || ""), tab);
    return;
  }
  if (info.menuItemId !== MENU_ID) return;
  const pending = {
    quote: info.selectionText || "",
    url: (tab && tab.url) || info.pageUrl || info.frameUrl || "",
    title: (tab && tab.title) || "",
    tabId: tab ? tab.id : null,
    frameId: typeof info.frameId === "number" ? info.frameId : 0,
    ts: Date.now(),
  };
  await setPending(pending);
  try {
    if (chrome.action.openPopup) {
      await chrome.action.openPopup(tab && tab.windowId != null ? { windowId: tab.windowId } : undefined);
      return;
    }
  } catch (e) { /* older Chrome / no focused window → small window */ }
  await openClipWindow(tab);
});

async function setPending(p) {
  try { await chrome.storage.session.set({ [PENDING_KEY]: p }); }
  catch { await chrome.storage.local.set({ [PENDING_KEY]: p }); }
}

async function openClipWindow(tab) {
  const w = 440, h = 640;
  let left, top;
  try {
    const win = tab && tab.windowId != null ? await chrome.windows.get(tab.windowId) : await chrome.windows.getLastFocused();
    left = Math.max(0, Math.round(win.left + win.width - w - 24));
    top = Math.max(0, Math.round(win.top + 72));
  } catch { }
  await chrome.windows.create({ url: chrome.runtime.getURL("clip.html?mode=window"), type: "popup", width: w, height: h, left, top, focused: true });
}

/* ── annotate mode ────────────────────────────────── */
// Fonts as data: URLs in an injected stylesheet, so no extension file has to be web-accessible.
const FONT_FILES = [
  ["Instrument Sans", "normal", 400, "instrument-sans-latin-400-normal.woff2"],
  ["Instrument Sans", "normal", 500, "instrument-sans-latin-500-normal.woff2"],
  ["Instrument Sans", "normal", 600, "instrument-sans-latin-600-normal.woff2"],
  ["STIX Two Text", "normal", 400, "stix-two-text-latin-400-normal.woff2"],
  ["STIX Two Text", "italic", 400, "stix-two-text-latin-400-italic.woff2"],
];
let fontCss = null;
async function getFontCss() {
  if (fontCss != null) return fontCss;
  const parts = [];
  for (const [fam, style, weight, file] of FONT_FILES) {
    try {
      const buf = await (await fetch(chrome.runtime.getURL(`vendor/fonts/${file}`))).arrayBuffer();
      let bin = ""; const u8 = new Uint8Array(buf);
      for (let i = 0; i < u8.length; i += 0x8000) bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
      // Private family names so the page's own fonts are never replaced.
      parts.push(`@font-face{font-family:"rb ${fam}";font-style:${style};font-weight:${weight};font-display:swap;src:url(data:font/woff2;base64,${btoa(bin)}) format("woff2")}`);
    } catch { }
  }
  fontCss = parts.join("\n");
  return fontCss;
}
// Highlights are drawn with the CSS Custom Highlight API (no DOM changes to the page).
const HIGHLIGHT_CSS = `
::highlight(rb-hl){background-color:rgba(178,58,51,.12);text-decoration:underline 1px rgb(178,58,51);text-underline-offset:3px}
::highlight(rb-hl-active){background-color:rgba(178,58,51,.24);text-decoration:underline 1px rgb(178,58,51);text-underline-offset:3px}
@media (prefers-color-scheme: dark){
  ::highlight(rb-hl){background-color:rgba(224,106,96,.18);text-decoration-color:rgb(224,106,96)}
  ::highlight(rb-hl-active){background-color:rgba(224,106,96,.32);text-decoration-color:rgb(224,106,96)}
}`;

function isPdfUrl(url) {
  return /\.pdf($|[?#])/i.test(url || "") || /^https?:\/\/(www\.)?arxiv\.org\/pdf\//i.test(url || "");
}

async function injectAnnotate(tabId) {
  const [{ result: state } = {}] = await chrome.scripting.executeScript({
    target: { tabId }, func: () => ({ loaded: !!window.__rbAnnotate, pdf: document.contentType === "application/pdf" }),
  });
  if (state && state.pdf) return { pdf: true };
  if (!state || !state.loaded) {
    await chrome.scripting.insertCSS({ target: { tabId }, css: (await getFontCss()) + HIGHLIGHT_CSS });
    await chrome.scripting.executeScript({ target: { tabId }, files: ["annotate.js"] });
  }
  return { pdf: false };
}

async function toggleAnnotate(tab, force) {
  if (!tab || tab.id == null) return { ok: false, error: "No tab" };
  if (!/^(https?|file):/i.test(tab.url || "")) return { ok: false, error: "This page can't be annotated." };
  if (isPdfUrl(tab.url)) return { ok: false, pdf: true };
  try {
    const r = await injectAnnotate(tab.id);
    if (r.pdf) return { ok: false, pdf: true };
    const res = await chrome.tabs.sendMessage(tab.id, { type: "rb-annotate", on: force });
    return { ok: true, on: res && res.on };
  } catch (e) {
    return { ok: false, error: "Chrome doesn't allow extensions on this page." };
  }
}

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== "toggle-annotate") return;
  if (!tab) [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  await toggleAnnotate(tab);
});

/* ── messages from the popup, annotate.js and note.html ── */
const pendingNotes = new Map(); // token -> {tabId, data, ts}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.type !== "string" || sender.id !== chrome.runtime.id) return;
  const tabId = sender.tab ? sender.tab.id : null;
  (async () => {
    switch (msg.type) {
      case "rb-close-window":
        if (sender.tab && sender.tab.windowId != null) chrome.windows.remove(sender.tab.windowId).catch(() => {});
        return { ok: true };
      case "rb-api":  // annotate.js / note.html → local server (whitelisted paths only)
        return callServer(String(msg.path || ""), msg.method === "POST" ? "POST" : "GET", msg.body || null);
      case "rb-toggle-annotate": {  // from the popup (activeTab was granted by opening it)
        let tab = null;
        if (typeof msg.tabId === "number") { try { tab = await chrome.tabs.get(msg.tabId); } catch { } }
        if (!tab) [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        return toggleAnnotate(tab, msg.on);
      }
      case "rb-note-open": {  // annotate.js parks the selection for the note iframe
        const token = crypto.randomUUID();
        pendingNotes.set(token, { tabId, data: msg.data, ts: Date.now() });
        for (const [k, v] of pendingNotes) if (Date.now() - v.ts > 3600e3) pendingNotes.delete(k);
        return { token };
      }
      case "rb-note-get": {  // note.html asks what it is annotating
        const p = pendingNotes.get(msg.token);
        return p && p.tabId === tabId ? { ok: true, data: p.data } : { ok: false };
      }
      case "rb-note-event": {  // note.html → its page's annotate.js (saved / cancel / resize)
        const p = pendingNotes.get(msg.token);
        if (!p || p.tabId !== tabId) return { ok: false };
        if (msg.event === "saved" || msg.event === "cancel") pendingNotes.delete(msg.token);
        await chrome.tabs.sendMessage(tabId, { type: "rb-note-event", event: msg.event, token: msg.token, detail: msg.detail }, { frameId: 0 });
        return { ok: true };
      }
      case "rb-open": {  // open a research_brain page (app or viewer) in a tab
        const base = await server();
        const path = String(msg.path || "/");
        if (!/^\/(viewer\/[^/?#]+|#note=[^/]*|)?([#?].*)?$/.test(path)) return { ok: false };
        await chrome.tabs.create({ url: base + path, index: sender.tab ? sender.tab.index + 1 : undefined });
        return { ok: true };
      }
    }
    return undefined;
  })().then(sendResponse, (e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
  return true;
});

/* ── "Open PDFs in research brain" (opt-in, Settings in the popup) ──────────────────────────────────
   When it's on, and Chrome gave the optional permission for http(s) pages, a tab that finishes loading a
   PDF is saved to research brain and switched to its viewer. The PDF is downloaded *in that tab*
   (grab_pdf.js: your own session and access, as with "Save PDF"), the bytes go to the app on this
   computer (/api/import/pdf-bytes), which keeps one copy of each file: a PDF you already have just opens.
   Each tab+URL is handled once, so going Back to the original PDF leaves it alone. */
const AUTO_KEY = "autoPdf";
const AUTO_PERM = { origins: ["http://*/*", "https://*/*"] };
const autoDone = new Map(); // tabId -> Set of PDF urls already handled in that tab

async function autoOn() {
  try {
    if (!(await chrome.storage.local.get(AUTO_KEY))[AUTO_KEY]) return false;
    return await chrome.permissions.contains(AUTO_PERM);
  } catch { return false; }
}

/** The PDF's bytes to research brain on this computer (base = server(): 127.0.0.1 or localhost only). */
async function postPdfBytes(base, got) {
  const q = new URLSearchParams({ source_url: got.finalUrl, doi: "", title: "" });
  const r = await fetch(`${base}/api/import/pdf-bytes?${q}`, { method: "POST", body: got.bytes, headers: { "Content-Type": "application/pdf" } });
  const n = await r.json().catch(() => null);
  if (!r.ok || !n) throw new Error((n && n.detail) || `HTTP ${r.status}`);
  return n;
}

async function grabInTab(tabId, url) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["grab_pdf.js"] });
  const run = async (func, args) => (await chrome.scripting.executeScript({ target: { tabId }, func, args: args || [] }))[0].result;
  const got = await run((u, max) => window.__rbGrab.start(u, max), [url, 150 << 20]);
  if (!got || !got.ok) throw new Error((got && got.error) || "Couldn't read the PDF");
  const bytes = new Uint8Array(got.size);
  for (let i = 0, off = 0; i < got.chunks; i++) {
    const b64 = await run((k) => window.__rbGrab.chunk(k), [i]);
    if (b64 == null) throw new Error("The download was interrupted");
    const bin = atob(b64);
    for (let j = 0; j < bin.length; j++) bytes[off + j] = bin.charCodeAt(j);
    off += bin.length;
  }
  run(() => window.__rbGrab.clear()).catch(() => {});
  return { bytes, finalUrl: got.finalUrl || url };
}

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== "complete" || !tab || !/^https?:/i.test(tab.url || "")) return;
  const base = await server();
  if ((tab.url || "").startsWith(base)) return;               // our own pages
  const seen = autoDone.get(tabId) || new Set();
  if (seen.has(tab.url)) return;                              // handled once; Back leaves it alone
  if (!(await autoOn())) return;
  let isPdf = isPdfUrl(tab.url);
  if (!isPdf) {
    try {
      const [{ result } = {}] = await chrome.scripting.executeScript({ target: { tabId }, func: () => document.contentType });
      isPdf = /application\/pdf/i.test(result || "");
    } catch { return; }
  }
  if (!isPdf) return;
  seen.add(tab.url);
  autoDone.set(tabId, seen);
  try {
    const got = await grabInTab(tabId, tab.url);
    const n = await postPdfBytes(base, got);
    const target = base + (n.viewer || `/viewer/${encodeURIComponent(n.id)}`);
    await chrome.tabs.update(tabId, { url: target });
  } catch (e) {
    // the app isn't running, the site refused, it isn't really a PDF: leave the tab as it is
    console.warn("[research_brain] couldn't open this PDF in research brain:", e && e.message);
  }
});
chrome.tabs.onRemoved.addListener((tabId) => autoDone.delete(tabId));

/* ── "Add this PDF to research brain" (right-click) ───────────────────────────────────────────────────
   A PDF on this computer (file:///…, e.g. opened from Downloads) is read by research brain from its path:
   the extension never reads the file, so it needs no "Allow access to file URLs". A web PDF is downloaded in
   the tab, with your own access (as "Save PDF"); if the site won't allow that (a link to another site),
   research brain downloads it itself, which works for open-access PDFs. Then the paper opens in its viewer. */
function fileUrlToPath(url) {
  const u = new URL(url);
  let p = decodeURIComponent(u.pathname);
  if (/^\/[A-Za-z]:\//.test(p)) p = p.slice(1).replace(/\//g, "\\");  // Windows: /C:/Users/… -> C:\Users\…
  else if (u.host) p = `\\\\${u.host}${p.replace(/\//g, "\\")}`;   // a network share: file://server/share/…
  return p;
}
async function flag(tab, ok, msg) {
  const tabId = tab && tab.id;
  try {
    await chrome.action.setBadgeBackgroundColor({ color: ok ? "#4a6b3a" : "#b23a33", tabId });
    await chrome.action.setBadgeText({ text: ok ? "✓" : "!", tabId });
    await chrome.action.setTitle({ title: `research_brain: ${msg}`, tabId });
    setTimeout(() => chrome.action.setBadgeText({ text: "", tabId }).catch(() => {}), ok ? 4000 : 15000);
  } catch { }
}
async function postJson(base, path, body) {
  const r = await fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => null);
  if (!r.ok) throw new Error((d && d.detail && (d.detail.message || d.detail)) || `HTTP ${r.status}`);
  return d;
}
async function addPdf(url, tab) {
  const base = await server();
  try {
    if (!url) throw new Error("No PDF here.");
    let id = "", viewer = "";
    if (/^file:/i.test(url)) {
      const r = await postJson(base, "/api/papers/import-files", { paths: [fileUrlToPath(url)] });
      const hit = (r.added || [])[0] || (r.duplicates || [])[0];
      if (!hit) {
        if ((r.followed || []).length) throw new Error("That PDF is in a folder you follow, so it's already in your library.");
        const err = ((r.failed || [])[0] || {}).error || "";
        throw new Error(/home folder/.test(err)
          ? "research brain only reads PDFs inside your user folder by itself. Drag this file onto the research brain window instead."
          : err || "research brain couldn't add that file.");
      }
      id = hit.note;
    } else if (/^https?:/i.test(url)) {
      let n = null, got = null;
      // in the tab first: your own access. A link to another site usually refuses that; then the server tries.
      if (tab && tab.id != null) { try { got = await grabInTab(tab.id, url); } catch { got = null; } }
      if (got) {
        n = await postPdfBytes(base, got);
      }
      if (!n) n = await postJson(base, "/api/import/url", { url });  // the server downloads it (open access)
      id = n.id; viewer = n.viewer || "";
    } else {
      throw new Error("Only web PDFs and PDFs on this computer can be added.");
    }
    await chrome.tabs.create({ url: base + (viewer || `/viewer/${encodeURIComponent(id)}`), index: tab ? tab.index + 1 : undefined });
    flag(tab, true, "added to your library");
  } catch (e) {
    const offline = e instanceof TypeError;
    const msg = offline ? "research brain isn't open. Open it, then try again." : String((e && e.message) || e);
    console.warn("[research_brain] couldn't add this PDF:", msg);
    flag(tab, false, msg);
  }
}
