"""POST /api/export: a self-contained, read-only HTML site under vault/exports/<timestamp>/."""
import importlib
import io
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    return TestClient(server.app), server


def _mk(c, **kw):
    r = c.post("/api/notes", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("src", "href"):
            if a.get(k):
                self.refs.append((tag, a[k]))


def _refs(text):
    p = _Refs()
    p.feed(text)
    return p.refs


@pytest.fixture()
def lib(env):
    c, server = env
    paper = _mk(c, type="paper", title="Hall thruster breathing mode", authors=["A. Physicist"], year=2016,
                doi="10.1063/1.4945558", abstract="Oscillations of $n_e$ in Hall thrusters.",
                summary="Ionization front moves at $v \\approx \\sqrt{T_e/m_i}$. See [[Plasma notes]].")
    note = _mk(c, type="note", title="Plasma notes",
               body="Compare [[Hall thruster breathing mode]] with [[Secret draft|my other idea]] and [[Nowhere]].\n\n"
                    "$$\\omega_{pe} = \\sqrt{\\frac{n e^2}{\\varepsilon_0 m_e}}$$\n\n<script>alert(1)</script>")
    secret = _mk(c, type="note", title="Secret draft", body="private thoughts")
    frag = _mk(c, type="fragment", parent=paper["id"], quote="$\\nabla \\cdot E = \\rho/\\varepsilon_0$",
               locator="p. 4, Eq. 7", page=4, body="Gauss, links to [[Plasma notes]]")
    c.post(f"/api/notes/{paper['id']}/link", json={"target": secret["id"]})
    c.post(f"/api/notes/{paper['id']}/critique")
    return c, server, paper, note, secret, frag


def test_export_selected_notes(lib):
    c, server, paper, note, secret, frag = lib
    r = c.post("/api/export", json={"ids": [paper["id"], note["id"]], "include_pdfs": False})
    assert r.status_code == 201, r.text
    out = r.json()
    site = Path(out["path"])
    assert site.parent == server.store.root / "exports"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}", site.name)
    assert out["notes"] == 2 and out["fragments"] == 1 and out["pdfs"] == 0
    assert sorted(p.name for p in (site / "notes").iterdir()) == sorted([f"{paper['id']}.html", f"{note['id']}.html"])
    assert not list(site.parent.glob(".*partial"))

    index = (site / "index.html").read_text()
    assert "Hall thruster breathing mode" in index and "Plasma notes" in index
    assert "Secret draft" not in index
    assert f'href="notes/{paper["id"]}.html"' in index

    page = (site / "notes" / f"{paper['id']}.html").read_text()
    assert "Ionization front" in page and "p. 4, Eq. 7" in page and f'id="{frag["id"]}"' in page
    assert "fake Claude" not in page  # critiques are not exported
    assert "Secret draft" not in page  # manual link to a non-exported note is dropped entirely

    npage = (site / "notes" / f"{note['id']}.html").read_text()
    data = re.search(r'<script type="application/json" id="rb-data">(.*?)</script>', npage, re.S).group(1)
    import json
    links = json.loads(data)["links"]
    assert links["Hall thruster breathing mode"] == {"href": f"{paper['id']}.html", "title": "Hall thruster breathing mode"}
    assert links["Secret draft"] == {} and links["Nowhere"] == {}  # rendered as plain text, title not leaked
    assert "private thoughts" not in npage
    assert "<script>alert(1)</script>" not in npage  # raw HTML in notes is escaped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in npage

    # a fragment's [[link]] to an exported note resolves; a link to a fragment points at its parent page + anchor
    assert json.loads(re.search(r'id="rb-data">(.*?)</script>', page, re.S).group(1))["links"]["Plasma notes"]["href"] \
        == f"{note['id']}.html"


def test_export_is_self_contained(lib):
    c, *_ = lib
    site = Path(c.post("/api/export", json={"ids": None}).json()["path"])
    for html in site.rglob("*.html"):
        for tag, url in _refs(html.read_text()):
            if tag in ("script", "link"):
                assert not re.match(r"^(https?:)?//", url), f"{html.name}: {url}"
                assert (html.parent / url).resolve().is_file(), f"{html.name}: {url} missing"
            elif tag == "a" and not re.match(r"^(https?:|mailto:|#)", url):
                target = (html.parent / url.split("#")[0]).resolve()
                assert target.is_file(), f"{html.name}: broken link {url}"
    css = (site / "assets" / "katex" / "katex.min.css").read_text()
    for font in re.findall(r"url\((fonts/[^)]+)\)", css):
        assert (site / "assets" / "katex" / font).is_file()
    assert (site / "assets" / "fonts" / "fonts.css").is_file()


def test_export_all_and_pdfs(lib):
    c, server, paper, note, secret, frag = lib
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    cv.drawString(72, 720, "hello")
    cv.showPage()
    cv.save()
    r = c.post(f"/api/notes/{paper['id']}/pdf", files={"file": ("p.pdf", buf.getvalue(), "application/pdf")})
    assert r.status_code == 200, r.text
    out = c.post("/api/export", json={"ids": None, "include_pdfs": True, "title": "Lab  reading\nlist"}).json()
    site = Path(out["path"])
    assert out["notes"] == 3 and out["pdfs"] == 1
    assert (site / "pdfs" / f"{paper['id']}.pdf").read_bytes().startswith(b"%PDF")
    assert f'href="../pdfs/{paper["id"]}.pdf"' in (site / "notes" / f"{paper['id']}.html").read_text()
    assert "<title>Lab reading list</title>" in (site / "index.html").read_text()
    assert not (site / "notes" / f"{frag['id']}.html").exists()  # fragments live on their parent's page
    # second export in the same second gets its own folder
    again = Path(c.post("/api/export", json={"ids": None}).json()["path"])
    assert again != site and again.is_dir()
    assert not (again / "pdfs").exists()


def test_export_lone_fragment_and_errors(lib):
    c, server, paper, note, secret, frag = lib
    out = c.post("/api/export", json={"ids": [frag["id"]]}).json()
    site = Path(out["path"])
    page = (site / "notes" / f"{frag['id']}.html").read_text()
    assert "Quote" in page and "p. 4, Eq. 7" in page
    assert "Hall thruster breathing mode" not in page  # parent not exported
    assert c.post("/api/export", json={"ids": ["no-such-note"]}).status_code == 404
    assert c.post("/api/export", json={"ids": []}).status_code == 400
    assert c.post("/api/export", json={"ids": None}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_rb_host_origin_is_allowed(tmp_path, monkeypatch):
    """With RB_HOST set to e.g. a Tailscale IP, the app opened at that address can still save;
    other origins (and other hosts) are still rejected; no Origin header (iOS Shortcuts) is allowed."""
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_HOST", "100.101.102.103")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    c = TestClient(server.app)
    body = {"type": "note", "title": "x"}
    assert c.post("/api/notes", json=body, headers={"Origin": "http://100.101.102.103:8765"}).status_code == 201
    assert c.post("/api/notes", json=body, headers={"Origin": "http://100.101.102.1030:8765"}).status_code == 403
    assert c.post("/api/notes", json=body, headers={"Origin": "http://100.101.102.103.evil.com"}).status_code == 403
    assert c.post("/api/notes", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/clip", json={"quote": "from a phone", "page_title": "Phone inbox"}).status_code == 201
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
