"""Browser-extension annotate mode: selectors on /api/clip, GET /api/fragments?url, POST /api/import/url,
and a static check that the extension contains no AI and talks only to this computer.

The /api/import/url tests replace app.urlfetch's DNS lookup and HTTP GET, so nothing touches the network.
"""
import importlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EXT = ROOT / "extension"

ATTN = dict(s2_id="s2-attn", title="Attention Is All You Need", year=2017,
            authors=["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", "Jakob Uszkoreit"], venue="NeurIPS", doi="",
            arxiv="1706.03762", url="", abstract="The Transformer.", citationCount=1)


def _pdf(title="Sheath physics", line="The Bohm criterion holds.", arxiv=""):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle(title)
    c.drawString(72, 720, title)
    if arxiv:
        c.drawString(72, 700, f"arXiv:{arxiv}v1 [cs.CL]")
    c.drawString(72, 680, line)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    scholar = server.scholar

    def lookup(q):
        kind, value = scholar.parse_identifier(q)
        return dict(ATTN) if kind == "arxiv" and value == "1706.03762" else None
    monkeypatch.setattr(scholar, "lookup", lookup)
    monkeypatch.setattr(scholar, "s2_paper", lambda pid: dict(ATTN) if pid == "s2-attn" else None)
    from fastapi.testclient import TestClient
    return TestClient(server.app), server


# ------------------------------------------------------------------ fake web for urlfetch
class FakeResp:
    def __init__(self, status=200, body=b"", headers=None, chunk=1 << 16):
        self.status_code, self.body, self.headers, self.chunk = status, body, dict(headers or {}), chunk
        self.closed = False

    def iter_content(self, n):
        for i in range(0, len(self.body), self.chunk):
            yield self.body[i:i + self.chunk]

    def close(self):
        self.closed = True


@pytest.fixture()
def web(env, monkeypatch):
    """routes: url -> FakeResp; dns: host -> [addresses] (default: a public address)."""
    _, server = env
    uf = server.urlfetch
    state = {"routes": {}, "dns": {}, "calls": [], "addrs": [], "lookups": []}

    def resolve(host):
        state["lookups"].append(host)
        return state["dns"].get(host, ["93.184.216.34"])

    def _open(url, addr=None):
        state["calls"].append(url)
        state["addrs"].append(addr)  # the pinned, already-checked address the real _open connects to
        r = state["routes"].get(url)
        if r is None:
            return FakeResp(404)
        return r() if callable(r) else r
    monkeypatch.setattr(uf, "resolve", resolve)
    monkeypatch.setattr(uf, "_open", _open)
    return state


# ------------------------------------------------------------------ selectors + /api/fragments
def test_clip_selector_round_trip_and_page_fragments(env):
    c, server = env
    url = "https://blog.example.com/posts/sheaths"
    sel = {"exact": "the sheath edge", "prefix": "Ions reach ", "suffix": " at the Bohm speed"}
    r = c.post("/api/clip", json={"quote": "the sheath edge", "url": url, "page_title": "Sheaths, explained",
                                  "note": "Why $v \\geq c_s$?", "selector": sel})
    assert r.status_code == 201, r.text
    frag = r.json()["fragment"]
    assert frag["selector"] == sel and frag["url"] == url and frag["body"] == "Why $v \\geq c_s$?"
    parent = r.json()["parent"]
    assert parent["type"] == "note" and parent["title"] == "Sheaths, explained"

    # stored in the file (frontmatter), and read back after a cold start
    md = (Path(server.VAULT) / "notes" / f"{frag['id']}.md").read_text(encoding="utf-8")
    assert "selector:" in md and "the sheath edge" in md
    assert c.get(f"/api/notes/{frag['id']}").json()["selector"] == sel

    # a highlight with an empty note, filed under the same page note via target_id
    sel2 = {"exact": "Debye length", "prefix": "", "suffix": " sets"}
    r2 = c.post("/api/clip", json={"quote": "Debye length", "url": url + "#sec2", "note": "",
                                   "target_id": parent["id"], "selector": sel2})
    assert r2.status_code == 201 and r2.json()["parent"]["id"] == parent["id"]

    # junk selectors normalise to null; non-fragments carry no selector
    r3 = c.post("/api/clip", json={"quote": "x y z", "url": "https://other.example/a", "selector": {"exact": "  "}})
    assert r3.json()["fragment"]["selector"] is None
    assert c.get(f"/api/notes/{parent['id']}").json()["selector"] is None
    plain = (Path(server.VAULT) / "notes" / f"{parent['id']}.md").read_text(encoding="utf-8")
    assert "selector" not in plain  # not written for notes without one

    # GET /api/fragments?url — same page (hash and trailing slash ignored), oldest first, with selectors
    d = c.get("/api/fragments", params={"url": url + "/#top"}).json()
    assert [f["selector"] for f in d["fragments"]] == [sel, sel2]
    assert d["fragments"][0]["body"] == "Why $v \\geq c_s$?" and d["fragments"][1]["body"] == ""
    assert d["paper"]["id"] == parent["id"]  # the page note matches the url
    assert parent["id"] in d["papers"] and d["papers"][parent["id"]]["citation"]
    assert c.get("/api/fragments", params={"url": "https://other.example/b"}).json()["fragments"] == []
    assert c.get("/api/fragments").status_code == 400


def test_page_fragments_on_arxiv_carry_citation(env):
    c, _ = env
    url = "https://arxiv.org/abs/1706.03762v7"
    r = c.post("/api/clip", json={"quote": "multi-head attention", "url": url, "note": "",
                                  "selector": {"exact": "multi-head attention", "prefix": "we use ", "suffix": "."}})
    assert r.status_code == 201 and r.json()["created_parent"] and r.json()["parent"]["type"] == "paper"
    d = c.get("/api/fragments", params={"url": url}).json()
    p = d["paper"]
    assert p["title"] == "Attention Is All You Need" and p["year"] == 2017 and p["arxiv"] == "1706.03762"
    assert p["citation"].startswith("Vaswani, A., et al. (2017). Attention Is All You Need.")
    assert "arXiv:1706.03762" in p["citation"]
    assert len(d["fragments"]) == 1


def test_citation_format(env):
    _, server = env
    cite = server._citation
    assert cite({"authors": ["Ada Lovelace"], "year": 1843, "title": "Notes", "venue": "Taylor", "doi": "10.1/x"}) \
        == "Lovelace, A. (1843). Notes. Taylor. https://doi.org/10.1/x"
    assert cite({"authors": ["A B", "C D"], "year": None, "title": "T", "url": "https://e.x"}) == "B, A., & D, C. T. https://e.x"
    assert cite({"authors": [], "title": "Untitled."}) == "Untitled."


# ------------------------------------------------------------------ /api/import/url
def test_import_url_downloads_and_dedupes(env, web):
    c, server = env
    pdf = _pdf("Attention Is All You Need", "Self attention.", arxiv="1706.03762")
    web["routes"]["https://arxiv.org/pdf/1706.03762"] = lambda: FakeResp(
        301, headers={"Location": "https://arxiv.org/pdf/1706.03762v7"})
    web["routes"]["https://arxiv.org/pdf/1706.03762v7"] = lambda: FakeResp(
        200, pdf, {"Content-Type": "application/pdf", "Content-Length": str(len(pdf))}, chunk=700)
    r = c.post("/api/import/url", json={"url": "https://arxiv.org/pdf/1706.03762"})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["title"] == "Attention Is All You Need" and n["has_pdf"] and n["arxiv"] == "1706.03762"
    assert n["viewer"] == f"/viewer/{n['id']}" and not n.get("duplicate")
    assert c.get(f"/pdf/{n['id']}").content.startswith(b"%PDF")
    assert web["calls"] == ["https://arxiv.org/pdf/1706.03762", "https://arxiv.org/pdf/1706.03762v7"]

    # already in the library with a PDF: no download, just open the viewer
    web["calls"].clear()
    r = c.post("/api/import/url", json={"url": "https://arxiv.org/pdf/1706.03762v7"})
    assert r.status_code == 201 and r.json()["id"] == n["id"] and r.json()["existing"] and r.json()["duplicate"]
    assert web["calls"] == []

    # same paper behind another URL: downloaded, then deduped by arXiv id
    web["routes"]["https://mirror.example.org/attn.pdf"] = FakeResp(200, pdf)
    r = c.post("/api/import/url", json={"url": "https://mirror.example.org/attn.pdf"})
    assert r.json()["id"] == n["id"] and r.json()["duplicate"]
    assert len([x for x in server.store.all() if x["type"] == "paper"]) == 1


def test_import_url_attaches_pdf_to_known_paper(env, web):
    c, _ = env
    p = c.post("/api/import/lookup", json={"query": "arXiv:1706.03762"}).json()
    assert not p["has_pdf"]
    web["routes"]["https://arxiv.org/pdf/1706.03762v2"] = FakeResp(200, _pdf("Attention", arxiv="1706.03762"))
    r = c.post("/api/import/url", json={"url": "https://arxiv.org/pdf/1706.03762v2"})
    assert r.status_code == 201 and r.json()["id"] == p["id"] and r.json()["has_pdf"]


def test_import_url_plain_pdf_without_ids(env, web):
    c, _ = env
    url = "https://journal.example.com/files/sheath_paper.pdf?download=1"
    web["routes"][url] = FakeResp(200, _pdf("Sheath physics in Hall thrusters"))
    n = c.post("/api/import/url", json={"url": url}).json()
    assert n["title"] == "Sheath physics in Hall thrusters" and n["url"] == url and n["has_pdf"]


@pytest.mark.parametrize("url,status", [
    ("file:///etc/passwd", 400),
    ("ftp://example.com/a.pdf", 400),
    ("javascript:alert(1)", 400),
    ("http://127.0.0.1/pdf/x", 403),
    ("http://127.0.0.1:8765/pdf/x", 400),  # non-standard port refused first
    ("http://localhost/a.pdf", 403),
    ("http://10.0.0.5/a.pdf", 403),
    ("http://192.168.1.1/a.pdf", 403),
    ("http://172.16.3.4/a.pdf", 403),
    ("http://169.254.169.254/latest/meta-data", 403),
    ("http://100.64.0.1/a.pdf", 403),
    ("http://[::1]/a.pdf", 403),
    ("http://[::ffff:127.0.0.1]/a.pdf", 403),
    ("http://0.0.0.0/a.pdf", 403),
    ("https://user:pw@example.com/a.pdf", 400),
    ("https://example.com:22/a.pdf", 400),
    ("https://printer.local/a.pdf", 403),
])
def test_import_url_refuses_unsafe_urls(env, web, url, status):
    c, _ = env
    r = c.post("/api/import/url", json={"url": url})
    assert r.status_code == status, (url, r.text)
    assert web["calls"] == []


def test_import_url_refuses_hosts_resolving_private(env, web):
    c, _ = env
    web["dns"]["intranet.example.com"] = ["10.1.2.3"]
    web["dns"]["mixed.example.com"] = ["93.184.216.34", "127.0.0.1"]  # any private address -> refused
    for host in ("intranet.example.com", "mixed.example.com"):
        r = c.post("/api/import/url", json={"url": f"https://{host}/a.pdf"})
        assert r.status_code == 403
    assert web["calls"] == []


def test_import_url_redirects_are_checked_and_capped(env, web):
    c, _ = env
    # a public page redirecting to a private address is refused at the redirect
    web["routes"]["https://public.example.com/a.pdf"] = FakeResp(302, headers={"Location": "http://192.168.0.10/a.pdf"})
    r = c.post("/api/import/url", json={"url": "https://public.example.com/a.pdf"})
    assert r.status_code == 403 and web["calls"] == ["https://public.example.com/a.pdf"]
    # and to file://
    web["routes"]["https://public.example.com/b.pdf"] = FakeResp(302, headers={"Location": "file:///etc/passwd"})
    assert c.post("/api/import/url", json={"url": "https://public.example.com/b.pdf"}).status_code == 400
    # more than 3 redirects
    for i in range(5):
        web["routes"][f"https://loop.example.com/{i}"] = FakeResp(302, headers={"Location": f"/{i + 1}"})
    web["calls"].clear()
    r = c.post("/api/import/url", json={"url": "https://loop.example.com/0"})
    assert r.status_code == 502 and "redirect" in r.json()["detail"].lower()
    assert len(web["calls"]) == 4  # the first request + 3 redirects


def test_import_url_refuses_non_pdf_and_oversize(env, web, monkeypatch):
    c, server = env
    web["routes"]["https://example.com/page.pdf"] = FakeResp(200, b"<!doctype html><html>" + b" " * 3000)
    r = c.post("/api/import/url", json={"url": "https://example.com/page.pdf"})
    assert r.status_code == 415 and "isn't a PDF" in r.json()["detail"]
    web["routes"]["https://example.com/tiny"] = FakeResp(200, b"hello")
    assert c.post("/api/import/url", json={"url": "https://example.com/tiny"}).status_code == 415
    web["routes"]["https://example.com/404.pdf"] = FakeResp(404)
    assert c.post("/api/import/url", json={"url": "https://example.com/404.pdf"}).status_code == 502

    big = 100 << 20
    # declared too large: refused before reading the body
    web["routes"]["https://example.com/huge.pdf"] = FakeResp(200, b"%PDF-1.4", {"Content-Length": str(big + 1)})
    r = c.post("/api/import/url", json={"url": "https://example.com/huge.pdf"})
    assert r.status_code == 413 and "100 MB" in r.json()["detail"]

    # no Content-Length, streamed past the cap (cap lowered so the test stays small)
    real = server.urlfetch.download_pdf
    monkeypatch.setattr(server.urlfetch, "download_pdf", lambda u, max_bytes=None: real(u, max_bytes=50_000))
    web["routes"]["https://example.com/stream.pdf"] = FakeResp(200, b"%PDF-1.4\n" + b"0" * 80_000, chunk=10_000)
    assert c.post("/api/import/url", json={"url": "https://example.com/stream.pdf"}).status_code == 413
    assert [x for x in server.store.all() if x["type"] == "paper"] == []


def test_import_url_origin_guard(env, web):
    c, _ = env
    r = c.post("/api/import/url", json={"url": "https://example.com/a.pdf"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and web["calls"] == []


# ------------------------------------------------------------------ no AI in the extension
FORBIDDEN = [r"/api/chat", r"/api/research", r"critique", r"explain", r"/api/explain", r"anthropic", r"claude",
             r"openai", r"/api/review/[^\"'`]*/check"]
# URLs that may appear in extension files: this computer, plus inert XML namespace / licence strings in vendored libs
ALLOWED_URL = re.compile(r"^(https?://$|https?://(127\.0\.0\.1|localhost)(:\d+|:PORT|:\*)?(/|$|\s|;)|http://www\.w3\.org/|"
                         r"https://github\.com/markedjs/)")


def _ext_files():
    for p in sorted(EXT.rglob("*")):
        if p.is_file() and p.suffix in (".js", ".html", ".css", ".json", ".md"):
            yield p


def test_extension_has_no_ai_and_only_talks_to_localhost():
    files = list(_ext_files())
    assert any(p.name == "annotate.js" for p in files) and any(p.name == "background.js" for p in files)
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        if p.name == "manifest.json":  # WAR "matches" lists pages that may embed note.html, not hosts we call
            m = json.loads(text)
            for w in m.get("web_accessible_resources", []):
                w.pop("matches", None)
            text = json.dumps(m)
        code = text if p.suffix != ".md" else ""  # README explains the rule in prose; checked separately below
        low = code.lower()
        for pat in FORBIDDEN:
            if p.parts[-2:] == ("vendor", "latex-editor.js") and pat == "explain":
                continue
            assert not re.search(pat, low), f"{p.relative_to(ROOT)} mentions {pat!r}"
        for m in re.finditer(r"(?:https?|wss?|ftp)://[^\s\"'`)<>]*", text):
            u = m.group(0)
            if p.suffix == ".md" and ALLOWED_URL.match(u):
                continue
            assert ALLOWED_URL.match(u), f"{p.relative_to(ROOT)} references a non-local host: {u}"
        assert "fonts.googleapis" not in text and "cdn." not in low, p
    # the vendored editor really has no network or AI code either
    le = (EXT / "vendor" / "latex-editor.js").read_text(encoding="utf-8")
    assert not re.search(r"\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon", le)


def test_extension_manifest_permissions_are_minimal():
    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert set(m["permissions"]) <= {"activeTab", "scripting", "storage", "contextMenus"}
    assert set(m["host_permissions"]) == {"http://127.0.0.1:8765/*", "http://localhost:8765/*"}
    assert "content_scripts" not in m  # annotate.js is injected on demand only
    assert not set(m.get("optional_host_permissions", [])) - {"http://127.0.0.1:8765/*", "http://localhost:8765/*"}
    for bad in ("tabs", "<all_urls>", "webRequest", "cookies", "history", "clipboardRead", "downloads", "debugger"):
        assert bad not in m["permissions"] and bad not in m["host_permissions"]
    war = [r for w in m.get("web_accessible_resources", []) for r in w["resources"]]
    assert war == ["note.html"] and all(w.get("use_dynamic_url") for w in m["web_accessible_resources"])
    assert "toggle-annotate" in m["commands"]
    csp = m.get("content_security_policy", {}).get("extension_pages", "")
    directives = {d.split()[0]: d.split()[1:] for d in csp.split(";") if d.strip()}
    assert directives["script-src"] == ["'self'"]  # no remote or inline script sources
    assert directives["object-src"] == ["'none'"]
    # extension pages may only connect to this computer
    assert set(directives["connect-src"]) <= {"'self'", "http://127.0.0.1:*", "http://localhost:*"}
    assert "externally_connectable" not in m  # no web page can message the extension
    # the id is pinned by the public key, so the server can allow exactly this extension's origin
    from app.security import extension_id_from_key, Policy
    assert extension_id_from_key(m["key"]) in Policy().extension_ids


def test_extension_says_no_ai():
    footer = "No AI in this extension. Everything you save goes only to research_brain on this computer."
    assert footer in (EXT / "clip.html").read_text(encoding="utf-8")
    readme = (EXT / "README.md").read_text(encoding="utf-8")
    assert "No AI" in readme and "127.0.0.1" in readme
    assert "Alt+Shift+A" in readme and "| " in readme  # shortcuts table
    # background.js only fetches the local server (and its own bundled files)
    bg = (EXT / "background.js").read_text(encoding="utf-8")
    targets = re.findall(r"fetch\((base \+ path|chrome\.runtime\.getURL\(`vendor/fonts/\$\{file\}`\))", bg)
    assert len(targets) == len(re.findall(r"\bfetch\(", bg)) == 2, re.findall(r"fetch\([^\n]{0,40}", bg)
    # annotate.js / note.js / clip.js never fetch directly except clip.js to st.server
    for name in ("annotate.js", "note.js"):
        assert "fetch(" not in (EXT / name).read_text(encoding="utf-8"), name
    assert re.findall(r"fetch\(([^,]+),", (EXT / "clip.js").read_text(encoding="utf-8")) == ["st.server + path"]
