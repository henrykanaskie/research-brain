"""Linked papers: link + full text + metadata, no PDF file (POST /api/import/linked, GET /api/import/linked/prefs,
POST /api/import/pdf-bytes?keep_pdf=0 and ?note=<id>), section markers -> passages with section labels, and
citations of linked passages (section / page links into the article). No network: Semantic Scholar and the
open-access downloader are replaced.
"""
from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HALL_URL = "https://pubs.example.org/doi/10.1063/1.4945558"
TEXT = ("[[section Abstract]]\n\nThe breathing mode is a low-frequency oscillation of the discharge current.\n\n"
        "[[section 1. Introduction]]\n\nHall thrusters are electric propulsion devices used for satellite station "
        "keeping and orbit raising, and their current oscillates.\n\n"
        "[[section 3.2 Frequency scaling]]\n\nThe breathing-mode frequency rose from 14 kHz at 200 V to 23 kHz at "
        "400 V, roughly linearly with the square root of the discharge voltage.\n\n"
        "[[section Materials and methods for the thruster experiments]]\n\nWe operated a laboratory thruster on "
        "xenon at discharge voltages from 200 to 400 V and recorded the current.")
ARTICLE = {"url": HALL_URL, "title": "Breathing-mode oscillations in Hall thrusters",
           "authors": ["Ada Physicist", "David J. Bohm"], "doi": "10.1063/1.4945558", "year": "2016/05/02",
           "venue": "Physics of Plasmas", "abstract": "The breathing mode is a low-frequency oscillation.",
           "text": TEXT, "kind": "html", "pdf_url": "https://pubs.example.org/doi/pdf/10.1063/1.4945558"}


def _pdf(title="Breathing-mode oscillations in Hall thrusters", lines=("Page one text about breathing modes.",),
         pages=2):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle(title)
    for p in range(pages):
        c.drawString(72, 720, f"{title} page {p + 1}")
        for i, ln in enumerate(lines):
            c.drawString(72, 690 - 16 * i, f"{ln} (p{p + 1})")
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    monkeypatch.setenv("RB_PDF_INPROCESS", "1")
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    lookups = []

    def lookup(q):
        lookups.append(q)
        return None
    monkeypatch.setattr(server.scholar, "lookup", lookup)
    from fastapi.testclient import TestClient
    c = TestClient(server.app)
    return c, server, lookups


def _text(server, nid):
    return server.store.text_path(nid).read_text(encoding="utf-8")


# ------------------------------------------------------------------ the endpoint
def test_import_linked_creates_a_paper_with_text_and_no_pdf(env):
    c, server, lookups = env
    r = c.post("/api/import/linked", json=ARTICLE)
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["type"] == "paper" and n["source"] == "linked" and n["has_text"] and not n["has_pdf"]
    assert n["doi"] == "10.1063/1.4945558" and n["year"] == 2016 and n["venue"] == "Physics of Plasmas"
    assert n["authors"] == ["Ada Physicist", "David J. Bohm"] and n["url"] == HALL_URL
    assert n["status"] == "to-read"
    assert "viewer" not in n and n["open"] == f"/#note={n['id']}"
    assert "[[section 3.2 Frequency scaling]]" in _text(server, n["id"])
    assert not server.store.pdf_path(n["id"]).exists()
    assert lookups == []  # the page's own metadata was enough: Semantic Scholar wasn't asked
    # the note file says source: linked; the graph shows it as a paper (same dot) with its link
    md = server.store.note_path(n["id"]).read_text()
    assert "source: linked" in md
    node = next(x for x in c.get("/api/graph").json()["nodes"] if x["id"] == n["id"])
    assert node["type"] == "paper" and node["source"] == "linked" and node["url"] == HALL_URL
    # an ordinary note's file doesn't grow a source line
    other = c.post("/api/notes", json={"title": "Plain note"}).json()
    assert "source:" not in server.store.note_path(other["id"]).read_text()


def test_import_linked_validation_and_caps(env, monkeypatch):
    c, server, _ = env
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x.example/a", "https://user:pw@x.example/a", "", "nope"):
        assert c.post("/api/import/linked", json=dict(ARTICLE, url=bad)).status_code == 400, bad
    assert c.post("/api/import/linked", json=dict(ARTICLE, kind="docx")).status_code == 400
    assert c.post("/api/import/linked", json={"url": "https://x.example/empty"}).status_code == 400
    monkeypatch.setattr(server.storage_api, "LINKED_MAX_CHARS", 1000)
    r = c.post("/api/import/linked", json=dict(ARTICLE, text="x" * 1001))
    assert r.status_code == 413
    monkeypatch.setattr(server.storage_api, "LINKED_MAX_CHARS", 5_000_000)
    # control characters are stripped; an HTML article has no page markers; one-line fields stay one line
    r = c.post("/api/import/linked", json=dict(ARTICLE, doi="", url="https://x.example/ctl", title="Bad\x00 tit\x1ble\nhere",
                                               text="[[page 3]]\n[[section Intro\x07]]\n\nBody\x00 text\x1b here. "))
    assert r.status_code == 201
    n = r.json()
    assert n["title"] == "Bad title here"
    t = _text(server, n["id"])
    assert "[[page" not in t and "\x00" not in t and "\x1b" not in t and "\x07" not in t and " " not in t
    assert "[[section Intro]]" in t and "Body text here." in t
    # a PDF read in the browser keeps pages, not sections
    r = c.post("/api/import/linked", json=dict(ARTICLE, doi="", url="https://x.example/p.pdf", kind="pdf",
                                               text="[[page 1]]\nOne.\n\n[[section Fake]]\n[[page 2]]\nTwo."))
    t = _text(server, r.json()["id"])
    assert "[[page 2]]" in t and "[[section" not in t
    # the JSON-only rule: a form post or text/plain body is refused before the endpoint runs
    r = c.post("/api/import/linked", content=json.dumps(ARTICLE), headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    # and the request size limit applies (16 MB JSON by default)
    from app.security import Policy
    assert Policy().limit_for("/api/import/linked") == Policy().max_json


def test_import_linked_origin(env):
    c, server, _ = env
    r = c.post("/api/import/linked", json=ARTICLE, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/import/linked", json=ARTICLE, headers={"Origin": "chrome-extension://" + "a" * 32})
    assert r.status_code == 403  # another extension
    from app.security import Policy
    ext = sorted(Policy().extension_ids)[0]
    r = c.post("/api/import/linked", json=ARTICLE, headers={"Origin": f"chrome-extension://{ext}"})
    assert r.status_code == 201
    assert c.get("/api/import/linked/prefs", headers={"Origin": f"chrome-extension://{ext}"}).json() == {"save_mode": "link"}


def test_import_linked_dedupes(env):
    c, server, _ = env
    # 1. a paper added by DOI earlier (no text): the text is attached, it becomes a linked paper
    p = server.store.create({"type": "paper", "title": "Hall thruster oscillations", "doi": "10.1063/1.4945558"})
    r = c.post("/api/import/linked", json=ARTICLE)
    assert r.status_code == 201
    n = r.json()
    assert n["id"] == p["id"] and n["duplicate"] and n["text_attached"] and n["source"] == "linked"
    assert n["title"] == "Hall thruster oscillations"  # the user's title is kept; empty fields are filled
    assert n["url"] == HALL_URL and n["venue"] == "Physics of Plasmas"
    # 2. the same article again (by URL): no second paper
    again = c.post("/api/import/linked", json=dict(ARTICLE, doi="")).json()
    assert again["id"] == p["id"] and again["duplicate"]
    assert len([x for x in server.store.all() if x["type"] == "paper"]) == 1
    # 3. an arXiv abstract page first, then its full HTML text: the longer text replaces the abstract
    ab = c.post("/api/import/linked", json={"url": "https://arxiv.org/abs/2401.00001v2", "title": "Sheaths",
                                            "authors": ["I. L"], "text": "[[section Abstract]]\n\nShort."}).json()
    assert ab["arxiv"] == "2401.00001"
    full = c.post("/api/import/linked", json={"url": "https://arxiv.org/html/2401.00001v2", "title": "Sheaths",
                                              "authors": ["I. L"], "text": "[[section 1 Introduction]]\n\n" + "Long text. " * 50}).json()
    assert full["id"] == ab["id"] and full["text_attached"]
    assert "[[section 1 Introduction]]" in _text(server, ab["id"])


def test_import_linked_keeps_an_existing_pdf(env):
    c, server, _ = env
    r = c.post("/api/import/pdf", files={"file": ("hall.pdf", _pdf(lines=("doi:10.1063/1.4945558",)), "application/pdf")})
    assert r.status_code == 201
    pid = r.json()["id"]
    before = _text(server, pid)
    n = c.post("/api/import/linked", json=ARTICLE).json()
    assert n["id"] == pid and n["existing"] and n["has_pdf"] and n["source"] == "pdf"
    assert _text(server, pid) == before and "[[page 1]]" in before  # the PDF's page text wins
    assert server.store.get(pid)["url"] == HALL_URL  # the article link is recorded


def test_import_linked_turns_a_web_source_note_into_the_paper(env):
    c, server, _ = env
    clip = c.post("/api/clip", json={"quote": "breathing mode", "url": HALL_URL, "page_title": "Breathing"}).json()
    src = clip["parent"]
    assert src["type"] == "note"
    n = c.post("/api/import/linked", json=dict(ARTICLE, doi="")).json()
    assert n["id"] == src["id"] and n["type"] == "paper" and n["source"] == "linked"
    assert [f["id"] for f in n["fragments"]] == [clip["fragment"]["id"]]


def test_no_pdf_download_for_linked_papers_unless_asked(env, monkeypatch):
    c, server, _ = env
    sa = server.storage_api
    monkeypatch.setenv("RB_ACQUIRE", "1")
    calls = []
    monkeypatch.setattr(sa.acquire, "schedule", lambda store, nid, meta=None, **kw: calls.append(nid) or True)
    n = c.post("/api/import/linked", json=ARTICLE).json()
    assert calls == []  # default: link + text, nothing fetched
    assert c.put("/api/settings", json={"save_mode": "pdf"}).json()["save_mode"] == "pdf"
    c.put("/api/settings", json={"auto_open_access": False})
    n2 = c.post("/api/import/linked", json=dict(ARTICLE, doi="10.5555/two", url="https://x.example/two", pdf_url="")).json()
    assert calls == []  # "Link + text + PDF" but open-access downloads are off
    c.put("/api/settings", json={"auto_open_access": True})
    n3 = c.post("/api/import/linked", json=dict(ARTICLE, doi="10.5555/three", url="https://x.example/three", pdf_url="")).json()
    assert calls == [n3["id"]]  # only now: a free legal copy, through the usual open-access lookup
    assert n3["save_mode"] == "pdf"
    c.put("/api/settings", json={"save_mode": "link"})
    assert len({n["id"], n2["id"], n3["id"]}) == 3


def test_thin_metadata_is_filled_from_semantic_scholar(env, monkeypatch):
    c, server, lookups = env
    monkeypatch.setattr(server.scholar, "lookup", lambda q: lookups.append(q) or {
        "title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "year": 2017, "arxiv": "1706.03762",
        "s2_id": "s2-attn", "venue": "NeurIPS", "doi": "", "abstract": "Transformers."})
    n = c.post("/api/import/linked", json={"url": "https://arxiv.org/abs/1706.03762v7", "text": "[[section Abstract]]\n\nT."}).json()
    assert lookups == ["arXiv:1706.03762"]
    assert n["title"] == "Attention Is All You Need" and n["s2_id"] == "s2-attn" and n["year"] == 2017


def test_references_become_cites(env):
    c, server, _ = env
    boeuf = server.store.create({"type": "paper", "title": "Low frequency oscillations", "doi": "10.1063/1.368529"})
    attn = server.store.create({"type": "paper", "title": "Attention", "arxiv": "1706.03762"})
    refs = ["J. P. Boeuf and L. Garrigues, J. Appl. Phys. 84, 3541 (1998). https://doi.org/10.1063/1.368529",
            "A. Vaswani et al., Attention is all you need, arXiv:1706.03762 (2017).",
            "E. Y. Choueiri, Phys. Plasmas 8, 1411 (2001)."]
    n = c.post("/api/import/linked", json=dict(ARTICLE, references=refs)).json()
    assert n["cites_added"] == 2
    assert set(server.store.get(n["id"])["cites"]) == {boeuf["id"], attn["id"]}
    links = [l for l in c.get("/api/graph").json()["links"] if l["kind"] == "cites"]
    assert {(l["source"], l["target"]) for l in links} == {(n["id"], boeuf["id"]), (n["id"], attn["id"])}


# ------------------------------------------------------------------ sections -> passages -> citations
def test_section_markers_become_passages_with_section_labels():
    from app.chunks import chunk_note, passage_where, section_label, split_sections
    assert section_label("3.2 Frequency scaling") == "3.2"
    assert section_label("1. Introduction") == "1"
    assert section_label("A.1 Proofs") == "A.1"
    assert section_label("Appendix B Derivations") == "B"
    assert section_label("III. RESULTS") == "III"
    assert section_label("A note on sheaths") == "A note on sheaths"
    assert section_label("Materials and methods for the thruster experiments") == "Materials and methods for the…"
    rows = chunk_note({"type": "paper", "title": "T"}, TEXT)
    by_sec = {r["section"]: r for r in rows if r["src"] == "text"}
    assert set(by_sec) == {"Abstract", "1", "3.2", "Materials and methods for the…"}
    assert by_sec["3.2"]["section_title"] == "3.2 Frequency scaling" and by_sec["3.2"]["page"] is None
    assert "14 kHz" in by_sec["3.2"]["text"] and "[[section" not in by_sec["3.2"]["text"]
    assert passage_where(by_sec["3.2"]) == " §3.2"
    assert passage_where(by_sec["Abstract"]) == ' §"Abstract"'
    assert passage_where({"page": 4}) == " p. 4"
    # PDF text keeps pages exactly as before (no section keys)
    pdf_rows = chunk_note({"type": "paper", "title": "T"}, "[[page 1]]\nFirst page text that is long enough.\n\n[[page 2]]\nSecond page text, also long enough.")
    assert [(r["page"], "section" in r) for r in pdf_rows] == [(1, False), (2, False)]
    # a heading carries over a page break when a text has both
    assert split_sections("[[section 2 Theory]]\nA\n[[page 5]]\nB") == [(None, "2 Theory", "\nA\n"), (5, "2 Theory", "\nB")]


def test_chat_cites_sections_and_links_into_the_article(env):
    c, server, _ = env
    nid = c.post("/api/import/linked", json=ARTICLE).json()["id"]
    idx = server.index()
    context, sources, passages = server._chat_context(idx, "breathing-mode frequency kHz discharge voltage", [])
    assert f"### [[{nid}]] §3.2\n" in context
    p = next(x for x in passages if x.get("section") == "3.2")
    assert p["linked"] and p["pdf"] == "" and p["section_title"] == "3.2 Frequency scaling"
    assert p["url"] == HALL_URL + "#:~:text=The%20breathing%2Dmode%20frequency%20rose%20from%2014%20kHz%20at"
    # an unnumbered section is cited by its (short) title
    _, _, ps = server._chat_context(idx, "xenon laboratory thruster recorded current", [])
    q = next(x for x in ps if x.get("section", "").startswith("Materials"))
    assert server.passage_where(q) == ' §"Materials and methods for the…"'
    # /api/chat carries the same passage fields
    r = c.post("/api/chat", json={"question": "breathing-mode frequency kHz"}).json()
    assert any(x.get("section") == "3.2" and x.get("url", "").startswith(HALL_URL + "#:~:text=") for x in r["passages"])
    # the research agent's passage rows too
    rows = server.agent_tools._passage_rows(server._Lib.passages("breathing-mode frequency kHz", 4, 2))
    row = next(x for x in rows if x.get("section") == "3.2")
    assert row["url"].startswith(HALL_URL) and server.agent_tools.fmt_passage(row).startswith(f"[[{nid}]] §3.2 |")


def test_article_url_fragments():
    from app.server import article_url
    assert article_url("https://x.example/a#top", 3, "whatever") == "https://x.example/a#page=3"
    assert article_url("https://x.example/a", None, "The ion-acoustic speed sets everything here") == \
        "https://x.example/a#:~:text=The%20ion%2Dacoustic%20speed%20sets%20everything%20here"
    # math or symbols in the first words: fall back to the section heading
    assert article_url("https://x.example/a", None, "$T_e$ = 5 eV in all runs", "2.1 Probe analysis") == \
        "https://x.example/a#:~:text=2.1%20Probe%20analysis"
    assert article_url("https://x.example/a", None, "", "") == "https://x.example/a"


# ------------------------------------------------------------------ PDF bytes: link + text, and the upgrade
def test_pdf_bytes_keep_pdf_0_makes_a_linked_paper_with_pages(env):
    c, server, _ = env
    url = "https://pubs.example.org/doi/pdf/10.1063/1.4945558"
    r = c.post("/api/import/pdf-bytes", params={"source_url": url, "keep_pdf": "0", "title": "Breathing modes"},
               content=_pdf(), headers={"Content-Type": "application/pdf"})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["source"] == "linked" and not n["has_pdf"] and n["has_text"] and n["linked"] and "viewer" not in n
    assert not server.store.pdf_path(n["id"]).exists() and not list(server.store.pdf_dir.glob("*.pdf"))
    assert "[[page 2]]" in _text(server, n["id"]) and n["url"] == url
    _, _, ps = server._chat_context(server.index(), "breathing modes page", [])
    p = next(x for x in ps if x["id"] == n["id"] and x["src"] == "text")
    assert p["page"] in (1, 2) and p["url"] == f"{url}#page={p['page']}"


def test_upgrade_attaches_the_pdf_and_page_text_wins(env):
    c, server, _ = env
    nid = c.post("/api/import/linked", json=ARTICLE).json()["id"]
    r = c.post("/api/import/pdf-bytes", params={"note": nid, "source_url": ARTICLE["pdf_url"]},
               content=_pdf(), headers={"Content-Type": "application/pdf"})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["id"] == nid and n["attached"] and n["has_pdf"] and n["source"] == "pdf" and n["viewer"] == f"/viewer/{nid}"
    t = _text(server, nid)
    assert "[[page 1]]" in t and "[[section" not in t
    assert "source: pdf" in server.store.note_path(nid).read_text()
    node = next(x for x in c.get("/api/graph").json()["nodes"] if x["id"] == nid)
    assert node["source"] == "pdf" and node["has_pdf"] and node["url"] == ""
    # passages now cite pages and open the viewer, not the article
    _, _, ps = server._chat_context(server.index(), "breathing modes page", [])
    p = next(x for x in ps if x["id"] == nid and x["src"] == "text")
    assert p["page"] and p["pdf"] == nid and "url" not in p
    # a second PDF for the same paper is refused politely (it already has one)
    r = c.post("/api/import/pdf-bytes", params={"note": nid}, content=_pdf(title="Other"),
               headers={"Content-Type": "application/pdf"})
    assert r.json()["existing"] and r.json()["id"] == nid
    # ?note= must name a paper
    note = c.post("/api/notes", json={"title": "x"}).json()
    assert c.post("/api/import/pdf-bytes", params={"note": note["id"]}, content=_pdf(title="Third"),
                  headers={"Content-Type": "application/pdf"}).status_code == 400
    assert c.post("/api/import/pdf-bytes", params={"note": "no-such-note"}, content=_pdf(title="Fourth"),
                  headers={"Content-Type": "application/pdf"}).status_code == 404


def test_upload_attach_also_upgrades(env):
    c, server, _ = env
    nid = c.post("/api/import/linked", json=ARTICLE).json()["id"]
    r = c.post(f"/api/notes/{nid}/pdf", files={"file": ("x.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 200 and r.json()["source"] == "pdf" and r.json()["has_pdf"]
    assert "[[page 1]]" in _text(server, nid)
