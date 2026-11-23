"""Papers dragged in from OneDrive on the web (and SharePoint): the PDF, downloaded with your own sign-in.

OneDrive on the web doesn't let a file be dragged out of the browser: a drop hands over only a link to it,
and that link opens only for someone signed in to the account (a work or school OneDrive is SharePoint,
<tenant>-my.sharepoint.com). So the server can't download it. research brain's own window can: the first
time, it opens a small Microsoft sign-in window (your organization's own login, with its second step); that
sign-in is kept with the window's other data (paths.user_data_dir()/window), like a browser's, and later
drops download at once with no window at all.

The download itself runs in that window, on the OneDrive site (so with its cookies, which never leave the
web view), then the bytes go to /api/import/pdf as if you had dropped the file. Nothing here talks to
Microsoft except the window, which only loads OneDrive's own pages and downloads the one file you dropped.

parse() reads a link (pure, tested in tests/test_cloudfetch.py); Fetcher does the window part, used by
app/macwindow.py and app/winwindow.py. A share link made for "Anyone with the link" needs no sign-in:
the server tries anonymous_url() first (app/server.py /api/import/url).
"""
from __future__ import annotations

import base64
import json
import re
import sys
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit, urlunsplit

SHAREPOINT = (".sharepoint.com", ".sharepoint.us", ".sharepoint-mil.us", ".sharepoint.cn", ".sharepoint.de")
CONSUMER = ("onedrive.live.com", "1drv.ms", "onedrive.cloud.microsoft", "photos.onedrive.com")
MAX_BYTES = 300 << 20       # what /api/import/pdf takes (RB_MAX_UPLOAD_MB's default)
SIGN_IN_SECONDS = 300       # how long the sign-in window waits for you
CHUNK = 3 << 20             # bytes per hop out of the web view (as base64, 4 MB)


class CloudError(Exception):
    pass


@dataclass
class Link:
    url: str
    host: str
    kind: str          # "sharepoint" (work or school OneDrive, SharePoint) or "consumer" (personal OneDrive)
    path: str = ""     # the file's server-relative path, decoded ("/personal/u/Documents/Papers/x.pdf")
    site: str = ""     # the site it's in ("/personal/u", "/sites/Lab"), or ""
    unique_id: str = ""  # the file's id ({guid}), when the link names it that way
    share: bool = False  # a sharing link (/:b:/g/...), which the site resolves itself
    name: str = ""     # the file's name, when the link says


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_cloud(url: str) -> bool:
    h = host_of(url)
    return bool(h) and (h.endswith(SHAREPOINT) or h in CONSUMER or h.endswith(".onedrive.live.com"))


def _site(path: str) -> str:
    m = re.match(r"^(/(?:personal|sites|teams)/[^/]+)", path or "", re.I)
    return m.group(1) if m else ""


def parse(url: str) -> Link | None:
    """What a dropped OneDrive or SharePoint link points at, or None if it isn't one."""
    url = (url or "").strip()
    if not url.lower().startswith("https://") or not is_cloud(url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host.endswith(SHAREPOINT):
        return Link(url=url, host=host, kind="consumer", share=True)
    q = {k.lower(): v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items() if v}
    raw = unquote(parts.path)
    link = Link(url=url, host=host, kind="sharepoint")
    if re.match(r"^/:[a-z]:/[a-z]/", parts.path, re.I):  # a sharing link: /:b:/g/personal/u/<token>?e=...
        link.share = True
        link.site = _site(re.sub(r"^/:[a-z]:/[a-z]", "", raw, flags=re.I))
        return link
    ident = q.get("id", "")
    if ident.startswith("/"):  # onedrive.aspx?id=/personal/u/Documents/x.pdf, AllItems.aspx?id=..., /my?id=...
        link.path = ident
    elif q.get("sourcedoc") or q.get("uniqueid"):  # Doc.aspx?sourcedoc={guid}&file=x.pdf
        guid = (q.get("sourcedoc") or q.get("uniqueid") or "").strip("{}")
        if re.fullmatch(r"[0-9a-fA-F-]{32,36}", guid):
            link.unique_id = guid
        link.name = q.get("file", "")
    elif "/_layouts/" not in raw.lower() and re.search(r"\.[a-z0-9]{2,5}$", raw, re.I):  # the file's own address
        link.path = raw
    link.site = _site(link.path or raw)
    if link.path and not link.name:
        link.name = link.path.rstrip("/").rsplit("/", 1)[-1]
    if not (link.path or link.unique_id):
        return None if not link.site else link  # a folder or a page, not a file: Fetcher says so
    return link


def _with(url: str, **params) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, vs in parse_qs(p.query, keep_blank_values=True).items() for v in vs if k not in params]
    q += list(params.items())
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q, doseq=True), ""))


def downloads(link: Link) -> list[str]:
    """Where the signed-in window asks for the file, first that works wins."""
    base = f"https://{link.host}{link.site}"
    if link.share:
        return [_with(link.url, download="1")]
    if link.unique_id:
        return [f"{base}/_layouts/15/download.aspx?UniqueId={quote(link.unique_id)}"]
    if not link.path:
        return []
    absolute = f"https://{link.host}{quote(link.path)}"
    rel = link.path.replace("'", "''")
    return [f"{base}/_layouts/15/download.aspx?SourceUrl={quote(absolute, safe='')}",
            f"{base}/_api/web/GetFileByServerRelativePath(decodedurl='{quote(rel, safe='/')}')/$value",
            absolute]


def start_page(link: Link) -> str:
    """A page on the link's site to sign in on, and to download from (a fetch from there carries its cookies)."""
    if link.share:
        return link.url
    return f"https://{link.host}{link.site}/" if link.site else f"https://{link.host}/"


def anonymous_url(url: str) -> str | None:
    """A download address that needs no sign-in, for a link shared with "Anyone with the link"."""
    link = parse(url)
    if not link or not link.share:
        return None
    if link.kind == "sharepoint":
        return _with(link.url, download="1")
    token = base64.urlsafe_b64encode(link.url.encode()).decode().rstrip("=")
    return f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"


# ---------------------------------------------------------------- the window part
# Runs in the OneDrive page: downloads the file into window.__rbCF (the bytes stay in the page until read).
_FETCH_JS = r"""(function (urls, max) {
  window.__rbCF = { state: 'busy' };
  (async function () {
    let why = '';
    for (const u of urls) {
      try {
        const r = await fetch(u, { credentials: 'include', redirect: 'follow' });
        if (!r.ok) { why = 'HTTP ' + r.status; continue; }
        const len = +(r.headers.get('content-length') || 0);
        if (len > max) { window.__rbCF = { state: 'failed', why: 'too big' }; return; }
        const b = new Uint8Array(await r.arrayBuffer());
        if (b.length > max) { window.__rbCF = { state: 'failed', why: 'too big' }; return; }
        const head = String.fromCharCode.apply(null, b.subarray(0, 1024));
        if (head.indexOf('%PDF') < 0) { why = 'not a PDF'; continue; }
        const cd = r.headers.get('content-disposition') || '';
        const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
        let name = '';
        try { name = m ? decodeURIComponent(m[1]) : ''; } catch (e) { name = m ? m[1] : ''; }
        window.__rbCF = { state: 'done', bytes: b, size: b.length, name: name };
        return;
      } catch (e) { why = String(e && e.message || e); }
    }
    window.__rbCF = { state: 'failed', why: why };
  })();
  return 1;
})(URLS, MAX)"""

_STATE_JS = ("(function(){var s=window.__rbCF;return s?JSON.stringify({state:s.state,why:s.why||'',"
             "size:s.size||0,name:s.name||''}):'';})()")
_CHUNK_JS = ("(function(i,n){var b=window.__rbCF.bytes.subarray(i,i+n),s='';"
             "for(var k=0;k<b.length;k+=32768)s+=String.fromCharCode.apply(null,b.subarray(k,k+32768));"
             "return btoa(s);})(%d,%d)")


def _signing_in(host: str, site_host: str) -> bool:
    """On a page that isn't the OneDrive site: Microsoft's sign-in, or your organization's own login."""
    return bool(host) and host != site_host


def _multipart(name: str, data: bytes) -> tuple[bytes, str]:
    boundary = "rb" + uuid.uuid4().hex
    safe = re.sub(r'["\r\n\\]', "_", name) or "paper.pdf"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def import_pdf(server: str, name: str, data: bytes) -> dict:
    """Hand the PDF to research brain as a dropped file (/api/import/pdf): the paper, as the page would get it."""
    body, ctype = _multipart(name, data)
    req = urllib.request.Request(server.rstrip("/") + "/api/import/pdf", data=body, method="POST",
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise CloudError(str(detail or f"research brain couldn't add it (HTTP {e.code}).")) from None
    except OSError as e:
        raise CloudError(f"research brain couldn't add it: {e}") from None


class Fetcher:
    """Downloads dropped OneDrive links in a window of research brain's own, one at a time.

    reply(dict) sends the page its answer (static/live.js cloudFetch): {"cloud": id, "signin": true} when the
    sign-in window opens, then {"cloud": id, "note": {...}} or {"cloud": id, "error": "..."}."""

    def __init__(self, server: str, reply):
        self.server = server
        self.reply = reply
        self.lock = threading.Lock()

    def handle(self, msg_id: str, url: str) -> None:
        threading.Thread(target=self._run, args=(str(msg_id)[:64], str(url)[:4000]), daemon=True).start()

    def _run(self, msg_id: str, url: str) -> None:
        with self.lock:
            try:
                name, data = self.fetch(url, on_signin=lambda: self.reply({"cloud": msg_id, "signin": True}))
                out = {"cloud": msg_id, "note": import_pdf(self.server, name, data)}
            except CloudError as e:
                out = {"cloud": msg_id, "error": str(e)}
            except Exception as e:  # never lose the window over it; say why in app.log
                print(f"[research_brain] OneDrive download: {e!r}", file=sys.stderr)
                out = {"cloud": msg_id, "error": f"Couldn't download it from OneDrive ({type(e).__name__})."}
            try:
                self.reply(out)
            except Exception as e:
                print(f"[research_brain] OneDrive download reply: {e!r}", file=sys.stderr)

    def fetch(self, url: str, on_signin=lambda: None) -> tuple[str, bytes]:
        link = parse(url)
        if link is None:
            raise CloudError("That isn't a link to a file in OneDrive.")
        if link.kind == "consumer":
            raise CloudError("A personal OneDrive file can't be downloaded from its link. In OneDrive, Download "
                             "it and drop the PDF here, or share it with Anyone with the link and drop that.")
        urls = downloads(link)
        if not urls:
            raise CloudError("That's a folder or a page in OneDrive, not a file. Drag a PDF.")
        if link.name and not link.name.lower().endswith(".pdf"):
            raise CloudError(f"“{link.name}” isn't a PDF. Only PDFs can be added.")
        import webview
        win = webview.create_window("Sign in to OneDrive", start_page(link), width=520, height=720, hidden=True)
        closed = threading.Event()
        win.events.closed += closed.set
        try:
            self._signed_in(win, link, closed, on_signin)
            return self._download(win, link, urls, closed)
        finally:
            if not closed.is_set():
                try:
                    win.destroy()
                except Exception:
                    pass

    def _signed_in(self, win, link: Link, closed: threading.Event, on_signin) -> None:
        """Wait until the window is on the OneDrive site, signed in. Shown only if a login page stays up."""
        if not win.events.loaded.wait(45) and not closed.is_set():
            raise CloudError("OneDrive didn't answer. Check the connection and drop it again.")
        shown, away, end = False, None, time.monotonic() + SIGN_IN_SECONDS
        while True:
            if closed.is_set():
                raise CloudError("Sign-in was closed, so nothing was downloaded. Drop the file again to sign in.")
            if time.monotonic() > end:
                raise CloudError("Sign-in took too long. Drop the file again to sign in.")
            try:
                cur = win.get_current_url() or ""
                ready = win.evaluate_js("document.readyState") if host_of(cur) == link.host else ""
            except Exception:
                cur, ready = "", ""
            if host_of(cur) == link.host and ready in ("interactive", "complete"):
                if shown:
                    win.hide()
                return
            if _signing_in(host_of(cur), link.host):
                away = away or time.monotonic()
                if not shown and time.monotonic() - away > 1.5:  # a quick hop through the login (still signed in) shows nothing
                    win.show()
                    shown = True
                    on_signin()
            time.sleep(0.4)

    def _download(self, win, link: Link, urls: list[str], closed: threading.Event) -> tuple[str, bytes]:
        win.evaluate_js(_FETCH_JS.replace("URLS, MAX", f"{json.dumps(urls)}, {MAX_BYTES}"))
        end = time.monotonic() + 600
        while True:
            if closed.is_set():
                raise CloudError("The OneDrive window was closed before the download finished.")
            if time.monotonic() > end:
                raise CloudError("The download from OneDrive took too long.")
            try:
                st = json.loads(win.evaluate_js(_STATE_JS) or "{}")
            except (ValueError, TypeError):
                st = {}
            if st.get("state") == "done":
                break
            if st.get("state") == "failed":
                why = st.get("why", "")
                if why == "too big":
                    raise CloudError(f"That PDF is larger than {MAX_BYTES >> 20} MB.")
                if why == "not a PDF":
                    raise CloudError("That file isn't a PDF. Only PDFs can be added.")
                if why in ("HTTP 401", "HTTP 403"):
                    raise CloudError("OneDrive says this account can't open that file. Is it shared with you?")
                if why == "HTTP 404":
                    raise CloudError("OneDrive couldn't find that file. Was it moved or renamed?")
                raise CloudError(f"Couldn't download it from OneDrive ({why or 'no answer'}).")
            time.sleep(0.25)
        size = int(st.get("size") or 0)
        parts = []
        for i in range(0, size, CHUNK):
            parts.append(base64.b64decode(win.evaluate_js(_CHUNK_JS % (i, CHUNK)) or ""))
        data = b"".join(parts)
        if len(data) != size or b"%PDF" not in data[:1024]:
            raise CloudError("The download from OneDrive came out incomplete. Drop it again.")
        name = st.get("name") or link.name or "OneDrive paper.pdf"
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return name, data
