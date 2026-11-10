// research_brain extension: "Save PDF to research_brain", the page side.
// No AI in this extension.
//
// Injected into the tab you are on, only when you click "Save PDF" in the popup (activeTab + scripting).
// It downloads the PDF that tab shows (or the page's citation_pdf_url) *in this page*, so the request
// carries your own browser session: whatever access you have on this site (a subscription, your
// university's network or proxy login) is used by the browser, never by research_brain, which never sees
// a cookie or password. The bytes are handed to the popup in chunks; the popup sends them to
// research_brain on this computer. Nothing else is fetched, and only the one URL you asked for.
(() => {
  if (window.__rbGrab) return;
  const CHUNK = 4 << 20; // 4 MB per message (base64)
  let buf = null;

  function isPdf(u8) {
    const head = u8.subarray(0, 1024);
    for (let i = 0; i + 3 < head.length; i++) {
      if (head[i] === 0x25 && head[i + 1] === 0x50 && head[i + 2] === 0x44 && head[i + 3] === 0x46) return true; // %PDF
    }
    return false;
  }

  window.__rbGrab = {
    async start(url, maxBytes) {
      buf = null;
      let target;
      try { target = new URL(url, location.href); } catch { return { ok: false, error: "That isn't a valid PDF link." }; }
      if (!/^https?:$/.test(target.protocol)) return { ok: false, error: "Only http and https PDFs can be saved." };
      let r;
      try {
        r = await fetch(target.href, { credentials: "include", cache: "force-cache", redirect: "follow" });
      } catch (e) {
        const cross = target.origin !== location.origin;
        return { ok: false, error: cross
          ? "The site won't let this page download its PDF. Open the PDF itself (its own tab), then click Save PDF again."
          : "The download failed. Reload the page and try again." };
      }
      if (!r.ok) return { ok: false, error: `The site answered HTTP ${r.status}. Are you signed in to it?` };
      const len = +r.headers.get("content-length") || 0;
      if (len > maxBytes) return { ok: false, error: `The PDF is larger than ${Math.round(maxBytes / 1048576)} MB.` };
      const u8 = new Uint8Array(await r.arrayBuffer());
      if (u8.length > maxBytes) return { ok: false, error: `The PDF is larger than ${Math.round(maxBytes / 1048576)} MB.` };
      if (!isPdf(u8)) return { ok: false, error: "That link gave a web page, not a PDF (often a sign-in or cookie page). Open the PDF itself and try again." };
      buf = u8;
      return { ok: true, size: u8.length, chunks: Math.ceil(u8.length / CHUNK), finalUrl: r.url || target.href };
    },
    chunk(i) {
      if (!buf) return null;
      const part = buf.subarray(i * CHUNK, (i + 1) * CHUNK);
      let bin = "";
      for (let j = 0; j < part.length; j += 0x8000) bin += String.fromCharCode.apply(null, part.subarray(j, j + 0x8000));
      return btoa(bin);
    },
    clear() { buf = null; return true; },
  };
})();
