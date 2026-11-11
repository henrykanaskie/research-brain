"""End-to-end API tests with a fake Claude and a mocked Semantic Scholar.

Run:  .venv/bin/python -m pytest -q tests
"""
import importlib
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAPERS = {
    "ARXIV:1706.03762": dict(s2_id="s2-attn", title="Attention Is All You Need", year=2017,
                             authors=["Ashish Vaswani", "Noam Shazeer"], venue="NeurIPS", doi="",
                             arxiv="1706.03762", url="https://www.semanticscholar.org/p/attn",
                             abstract="We propose the Transformer, a network architecture based solely on attention "
                                      "mechanisms, dispensing with recurrence and convolutions entirely.",
                             citationCount=100000),
    "DOI:10.1063/1.4945558": dict(s2_id="s2-hall", title="Hall thruster plasma oscillations", year=2016,
                                  authors=["A. Physicist"], venue="Physics of Plasmas", doi="10.1063/1.4945558",
                                  arxiv="", url="", citationCount=12,
                                  abstract="Breathing mode oscillations in Hall effect thrusters are studied with a "
                                           "hybrid particle-in-cell plasma model of xenon ionization."),
    "s2-bert": dict(s2_id="s2-bert", title="BERT: Pre-training of Deep Bidirectional Transformers", year=2019,
                    authors=["Jacob Devlin"], venue="NAACL", doi="10.18653/v1/N19-1423", arxiv="1810.04805",
                    url="", citationCount=90000,
                    abstract="We introduce BERT, a bidirectional transformer language representation model "
                             "pre-trained with masked attention objectives."),
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    scholar = server.scholar

    def s2_paper(pid):
        p = PAPERS.get(pid) or next((v for v in PAPERS.values() if v["s2_id"] == pid), None)
        return dict(p) if p else None

    def lookup(q):
        kind, value = scholar.parse_identifier(q)
        key = {"arxiv": f"ARXIV:{value}", "doi": f"DOI:{value}"}.get(kind)
        if key:
            return s2_paper(key)
        return next((dict(v) for v in PAPERS.values() if value.lower() in v["title"].lower()), None)

    def discover(pid):
        cand = lambda p: {k: p[k] for k in ("s2_id", "title", "year", "authors", "doi", "arxiv", "url", "citationCount")}
        return {"references": [cand(PAPERS["ARXIV:1706.03762"])], "citations": [cand(PAPERS["s2-bert"])],
                "recommended": []}

    monkeypatch.setattr(scholar, "s2_paper", s2_paper)
    monkeypatch.setattr(scholar, "lookup", lookup)
    monkeypatch.setattr(scholar, "discover", discover)
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def make_pdf(title: str, body: str, arxiv: str = "") -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(title)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, title)
    c.setFont("Helvetica", 10)
    if arxiv:
        c.drawString(72, 700, f"arXiv:{arxiv}v2 [cs.CL] 6 Dec 2017")
    y = 680
    for line in body.split("\n"):
        c.drawString(72, y, line)
        y -= 14
    c.showPage()
    c.drawString(72, 720, "Page two: the loss is L = -sum log p(y|x).")
    c.save()
    return buf.getvalue()


def test_full_flow(client):
    st = client.get("/api/status").json()
    assert st["claude"]["available"] and st["embedder"] == "tfidf" and st["notes"] == 0
    assert client.get("/api/graph").json()["nodes"] == []

    # PDF import with arXiv id in the text -> enriched from (mock) Semantic Scholar
    pdf = make_pdf("Attention Is All You Need", "The Transformer uses multi-head self attention.", "1706.03762")
    r = client.post("/api/import/pdf", files={"file": ("attn.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    attn = r.json()
    assert attn["title"] == "Attention Is All You Need" and attn["has_pdf"] and attn["has_text"]
    assert attn["s2_id"] == "s2-attn" and attn["year"] == 2017
    assert client.get(f"/pdf/{attn['id']}").headers["content-type"] == "application/pdf"

    # duplicate import
    r = client.post("/api/import/pdf", files={"file": ("again.pdf", pdf, "application/pdf")})
    assert r.json().get("duplicate") is True and r.json()["id"] == attn["id"]

    # non-pdf rejected
    assert client.post("/api/import/pdf", files={"file": ("x.pdf", b"hello", "application/pdf")}).status_code == 400

    # lookup imports
    hall = client.post("/api/import/lookup", json={"query": "https://doi.org/10.1063/1.4945558"}).json()
    assert hall["title"].startswith("Hall thruster") and not hall["has_pdf"]
    assert client.post("/api/import/lookup", json={"query": "zzz no such paper"}).status_code == 404

    # plain note
    note = client.post("/api/notes", json={"type": "note", "private": False, "title": "Self-attention intuition",
                                           "body": "Attention mechanisms weight tokens; transformer layers stack them.",
                                           "tags": "ml, attention"}).json()
    assert note["tags"] == ["ml", "attention"]

    # update + autosave fields
    r = client.put(f"/api/notes/{attn['id']}", json={"summary": "Replaces recurrence with attention. $O(n^2)$ cost.",
                                                     "body": "Key eq: $$\\mathrm{softmax}(QK^T/\\sqrt{d_k})V$$",
                                                     "tags": ["ml"]})
    assert r.status_code == 200 and r.json()["summary"].startswith("Replaces")

    # semantic similarity: attention paper and attention note should be neighbours, plasma paper less so
    g = client.get("/api/graph").json()
    pairs = {tuple(sorted((s["source"], s["target"]))): s["score"] for s in g["similar"]}
    a_n = pairs.get(tuple(sorted((attn["id"], note["id"]))), 0)
    a_h = pairs.get(tuple(sorted((attn["id"], hall["id"]))), 0)
    assert a_n > a_h, (a_n, a_h)

    # manual link both ways, then unlink
    r = client.post(f"/api/notes/{attn['id']}/link", json={"target": note["id"]})
    assert note["id"] in r.json()["links"]
    assert attn["id"] in client.get(f"/api/notes/{note['id']}").json()["links"]
    g = client.get("/api/graph").json()
    assert any(l["kind"] == "manual" for l in g["links"])
    assert client.post(f"/api/notes/{attn['id']}/link", json={"target": attn["id"]}).status_code == 400
    client.delete(f"/api/notes/{attn['id']}/link/{note['id']}")
    assert note["id"] not in client.get(f"/api/notes/{attn['id']}").json()["links"]

    # fragments
    fr = client.post("/api/notes", json={"type": "fragment", "parent": attn["id"],
                                         "quote": "Attention(Q,K,V) = softmax(QK^T / sqrt(d_k)) V",
                                         "locator": "Eq. 1", "page": 4,
                                         "body": "Scaling by sqrt(d_k) keeps softmax out of saturation."})
    assert fr.status_code == 201, fr.text
    fr = fr.json()
    assert fr["type"] == "fragment" and fr["title"] == "Eq. 1" and fr["page"] == 4
    assert fr["parent_info"]["id"] == attn["id"]
    assert all(s["id"] != attn["id"] for s in fr["similar"])  # parent excluded
    parent = client.get(f"/api/notes/{attn['id']}").json()
    assert [f["id"] for f in parent["fragments"]] == [fr["id"]]
    assert any(l["kind"] == "part" for l in client.get("/api/graph").json()["links"])
    assert client.post("/api/notes", json={"type": "fragment", "quote": "x"}).status_code == 400
    assert client.post("/api/notes", json={"type": "fragment", "parent": fr["id"], "quote": "x"}).status_code == 400
    r = client.put(f"/api/notes/{fr['id']}", json={"page": "5", "locator": "Eq. 1 (p.5)"})
    assert r.json()["page"] == 5

    # Claude features (fake)
    crit = client.post(f"/api/notes/{attn['id']}/critique")
    assert crit.status_code == 200 and "fake Claude" in crit.json()["text"]
    assert client.get(f"/api/notes/{attn['id']}").json()["critique"]["summary_snapshot"].startswith("Replaces")
    assert client.post(f"/api/notes/{hall['id']}/critique").status_code == 400  # empty summary
    ex = client.post("/api/explain", json={"a": attn["id"], "b": note["id"]}).json()
    assert ex["cached"] is False
    assert client.post("/api/explain", json={"a": note["id"], "b": attn["id"]}).json()["cached"] is True
    ch = client.post("/api/chat", json={"question": "what does scaling by sqrt d_k do?", "history": []}).json()
    assert ch["answer"] and ch["sources"]

    # discovery
    d = client.get(f"/api/notes/{attn['id']}/discover").json()
    assert d["references"][0]["in_library"] == attn["id"] and d["citations"][0]["in_library"] is None
    bert = client.post("/api/import/s2", json={"s2_id": "s2-bert", "link_to": attn["id"]}).json()
    assert attn["id"] in bert["links"]
    assert client.get(f"/api/notes/{note['id']}/discover").status_code == 400

    # search / match / clip
    assert client.get("/api/search", params={"q": "attention"}).json()[0]["id"] == attn["id"]
    assert all(x["type"] != "fragment" for x in client.get("/api/search").json())
    m = client.get("/api/match", params={"url": "https://arxiv.org/abs/1706.03762v7"}).json()
    assert m["note"]["id"] == attn["id"]
    assert client.get("/api/match", params={"url": "https://example.com/blog"}).json()["note"] is None
    c1 = client.post("/api/clip", json={"quote": "multi-head attention lets the model attend jointly",
                                        "url": "https://arxiv.org/pdf/1706.03762v7", "page_title": "x", "note": "nice"})
    assert c1.status_code == 201 and c1.json()["parent"]["id"] == attn["id"] and not c1.json()["created_parent"]
    c2 = client.post("/api/clip", json={"quote": "pretraining helps", "url": "https://arxiv.org/abs/1810.04805",
                                        "page_title": "BERT"})
    assert c2.json()["parent"]["id"] == bert["id"]
    c3 = client.post("/api/clip", json={"quote": "a blog insight", "url": "https://example.com/post",
                                        "page_title": "Some Blog Post"})
    assert c3.json()["created_parent"] and c3.json()["parent"]["type"] == "note"
    assert client.post("/api/clip", json={"quote": " ", "note": ""}).status_code == 400

    # origin guard
    assert client.post("/api/notes", json={"title": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    from app.security import Policy
    ext_id = sorted(Policy().extension_ids)[0]  # the research_brain extension (pinned by its manifest key)
    assert client.post("/api/notes", json={"title": "ok"},
                       headers={"Origin": f"chrome-extension://{ext_id}"}).status_code == 201
    assert client.post("/api/notes", json={"title": "x"},
                       headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}).status_code == 403
    assert client.get("/api/status", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200

    # delete cascades fragments to trash
    n_before = len(client.get("/api/graph").json()["nodes"])
    assert client.delete(f"/api/notes/{attn['id']}").json()["ok"]
    nodes = client.get("/api/graph").json()["nodes"]
    assert all(n["parent"] != attn["id"] and n["id"] != attn["id"] for n in nodes)
    assert len(nodes) == n_before - 3  # paper + 2 fragments
    assert client.get(f"/api/notes/{attn['id']}").status_code == 404
    assert client.get("/api/notes/../../etc").status_code == 404


def test_identifier_parsing():
    sys.path.insert(0, str(ROOT))
    from app.scholar import find_ids_in_text, parse_identifier
    assert parse_identifier("https://arxiv.org/abs/2301.01234v3") == ("arxiv", "2301.01234")
    assert parse_identifier("https://arxiv.org/pdf/2301.01234v3.pdf") == ("arxiv", "2301.01234")
    assert parse_identifier("arXiv:hep-th/9901001") == ("arxiv", "hep-th/9901001")
    assert parse_identifier("2301.01234") == ("arxiv", "2301.01234")
    assert parse_identifier("https://doi.org/10.1063/1.4945558") == ("doi", "10.1063/1.4945558")
    assert parse_identifier("doi:10.1103/PhysRevLett.116.061102.") == ("doi", "10.1103/PhysRevLett.116.061102")
    assert parse_identifier("Attention is all you need")[0] == "title"
    ids = find_ids_in_text("Some paper\narXiv:1706.03762v5 [cs.CL]\nDOI: 10.1000/xyz123")
    assert ids == {"arxiv": "1706.03762", "doi": "10.1000/xyz123"}


def test_store_roundtrip(tmp_path):
    from app.store import Store
    s = Store(tmp_path)
    n = s.create({"type": "paper", "title": "A --- tricky: title\n", "year": 2020,
                  "body": "line1\n---\nline after a hr", "summary": "has: colons and 'quotes'"})
    got = s.get(n["id"])
    assert got["body"] == "line1\n---\nline after a hr"
    assert got["summary"] == "has: colons and 'quotes'"
    assert got["year"] == 2020 and got["title"] == "A --- tricky: title"
    n2 = s.create({"type": "paper", "title": "A --- tricky: title", "year": 2020})
    assert n2["id"] != n["id"]


def test_hand_edited_frontmatter_does_not_break_library(client):
    """A note file edited outside the app (e.g. Obsidian) with odd or broken YAML must not take
    down /api/graph, search, or note creation (regression: `tags: foo` / `authors: 5` raised TypeError)."""
    from app.server import store
    ok = client.post("/api/notes", json={"type": "note", "title": "Plasma notes", "body": "plasma"}).json()
    nd = store.notes_dir
    (nd / "broken-yaml.md").write_text("---\ntitle: [unclosed\ntags: {a: \n---\nbody text\n")
    (nd / "list-front.md").write_text("---\n- just\n- a list\n---\nhello\n")
    (nd / "weird-types.md").write_text("---\ntitle: 123\ntags: notalist, two\nauthors: 5\nlinks: [missing, null]\n"
                                       "year: abc\ntype: alien\ncritique: hello\npage: x\n---\n")
    assert client.get("/api/graph").status_code == 200
    assert client.get("/api/search?q=a").status_code == 200
    w = client.get("/api/notes/weird-types").json()
    assert w["tags"] == ["notalist", "two"] and w["authors"] == ["5"] and w["links"] == ["missing"]
    assert w["critique"] is None and w["type"] == "note" and w["title"] == "123"
    assert client.get("/api/notes/broken-yaml").json()["title"] == "broken-yaml"
    assert client.put("/api/notes/weird-types", json={"body": "edited"}).status_code == 200
    assert client.post("/api/notes", json={"type": "note", "title": "Still works"}).status_code == 201
    assert client.get(f"/api/notes/{ok['id']}").status_code == 200


def test_put_with_wrong_field_types(client):
    n = client.post("/api/notes", json={"type": "note", "title": "Typed"}).json()
    for body in ({"tags": 5}, {"title": 123}, {"authors": {"a": 1}}, {"summary": None}, {"body": ["x"]}):
        r = client.put(f"/api/notes/{n['id']}", json=body)
        assert r.status_code == 200, (body, r.text)
    got = client.get(f"/api/notes/{n['id']}").json()
    assert got["title"] == "123" and got["tags"] == [] and isinstance(got["body"], str)
    assert client.get("/api/graph").status_code == 200


def test_pdfjs_modules_are_served_as_javascript(client):
    """Browsers refuse ES modules with a non-JavaScript type (and we send nosniff). Some Pythons don't
    map .mjs, which broke the PDF viewer on macOS; the server registers the type itself."""
    import mimetypes
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
        r = client.get(f"/static/vendor/pdfjs/build/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith("text/javascript"), (name, r.headers["content-type"])
    assert mimetypes.guess_type("x.mjs")[0] == "text/javascript"
