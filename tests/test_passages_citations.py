"""Passage-level chat retrieval (page citations) and Semantic Scholar citation links.

Run:  .venv/bin/python -m pytest -q tests/test_passages_citations.py
"""
import importlib
import io
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FILLER = [
    "Hall effect thrusters accelerate ions through a radial magnetic field and an axial electric field.",
    "The discharge channel walls are made of boron nitride, which sets the secondary electron emission yield.",
    "Anomalous electron transport across the magnetic field is usually modelled with a Bohm-like coefficient.",
    "Plume divergence affects spacecraft integration and is measured with a Faraday probe sweep.",
    "Neutral xenon flow is injected through the anode, which doubles as the gas distributor.",
    "Erosion of the channel walls limits thruster lifetime and motivates magnetic shielding.",
]
FACT = ("The breathing mode frequency scales as f = v_n / L_ion, and in our Zirconium-lined channel we "
        "measured a peculiar quokka resonance at 23.7 kHz caused by cathode coupling.")


def make_long_pdf(pages: int = 12, fact_page: int = 9) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle("Breathing oscillations in a synthetic Hall thruster")
    for p in range(1, pages + 1):
        c.setFont("Helvetica", 10)
        y = 720
        if p == 1:
            c.setFont("Helvetica-Bold", 15)
            c.drawString(72, y, "Breathing oscillations in a synthetic Hall thruster")
            c.setFont("Helvetica", 10)
            y -= 30
        lines = [f"Section {p}. " + FILLER[(p + k) % len(FILLER)] for k in range(6)]
        if p == fact_page:
            cut = FACT.rfind(" ", 0, 95)
            lines.insert(3, FACT[:cut])
            lines.insert(4, FACT[cut + 1:])
        for k, line in enumerate(lines):
            c.drawString(72, y, line)
            y -= 14
            if k % 2 == 1:
                y -= 14  # paragraph gap
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    # never touch the network
    monkeypatch.setattr(srv.scholar, "lookup", lambda q: None)
    monkeypatch.setattr(srv.scholar, "s2_paper", lambda pid: None)
    monkeypatch.setattr(srv.citations, "_sleep", lambda s: None)
    return srv


@pytest.fixture()
def client(server):
    from fastapi.testclient import TestClient
    return TestClient(server.app)


# ---------------------------------------------------------------- passages
def test_chunker_carries_pages(server):
    from app import chunks
    text = "\n\n".join(f"[[page {i}]]\nPage {i} first paragraph is here and long enough.\n\n"
                       f"Second paragraph of page {i}, also long enough to keep." for i in range(1, 4))
    out = chunks.chunk_note({"type": "paper", "title": "T", "summary": "Mine: a short summary of the paper."}, text)
    assert [c["page"] for c in out if c["src"] == "text"] == [1, 2, 3]
    assert out[0]["src"] == "summary" and out[0]["page"] is None
    frag = chunks.chunk_note({"type": "fragment", "title": "Eq 3", "quote": "E = mc^2 holds here",
                              "body": "Rest energy of a massive particle.", "page": 7})
    assert frag[0]["page"] == 7 and "Quoted passage" in frag[0]["text"] and "Student's note" in frag[0]["text"]
    long = chunks.split_text(("This is one sentence about plasma. " * 120).strip())
    assert len(long) > 3 and all(len(c) <= chunks.MAX_CHUNK_CHARS for c in long)


def test_chat_retrieves_page_nine(server, client, monkeypatch):
    r = client.post("/api/import/pdf", files={"file": ("hall.pdf", make_long_pdf(), "application/pdf")})
    assert r.status_code == 201, r.text
    paper = r.json()
    assert paper["has_text"]
    other = client.post("/api/notes", json={"type": "note", "title": "Transformer notes",
                                            "body": "Attention layers mix tokens; nothing about plasmas."}).json()
    seen = {}
    real_ask = server.claude_cli.ask

    def spy(instruction, context="", system=""):
        seen["instruction"], seen["context"] = instruction, context
        return real_ask(instruction, context, system)
    monkeypatch.setattr(server.claude_cli, "ask", spy)
    res = client.post("/api/chat", json={"question": "What resonance frequency did they measure with the quokka "
                                                     "effect from cathode coupling?", "history": []})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"] and body["sources"]
    assert f"### [[{paper['id']}]] p. 9" in seen["context"]
    assert "23.7 kHz" in seen["context"]
    assert "[[note-id]] p. N" in seen["instruction"]
    first = body["passages"][0]
    assert first["id"] == paper["id"] and first["page"] == 9 and "quokka" in first["text"]
    assert set(first) >= {"id", "title", "page", "text"}
    assert sum(1 for p in body["passages"] if p["id"] == paper["id"]) <= server.CHAT_PER_NOTE
    assert any(s["id"] == paper["id"] for s in body["sources"])
    assert other["id"]  # the unrelated note may or may not appear, but must not break anything


def test_passage_cache_is_incremental(server, client, monkeypatch):
    a = client.post("/api/notes", json={"type": "note", "title": "A", "body": "Ion acoustic waves in plasma."}).json()
    b = client.post("/api/notes", json={"type": "note", "title": "B", "body": "Gradient descent with momentum."}).json()
    ci = server._chunk_index
    calls = []
    from app import chunks
    orig = chunks.chunk_note
    monkeypatch.setattr(chunks, "chunk_note", lambda n, t="": calls.append(n["id"]) or orig(n, t))
    ci.search("plasma waves")
    assert sorted(calls) == sorted([a["id"], b["id"]])
    calls.clear()
    ci.search("momentum")
    assert calls == []  # nothing changed: nothing re-chunked
    client.put(f"/api/notes/{a['id']}", json={"body": "Ion acoustic waves and Landau damping."})
    ci.search("damping")
    assert calls == [a["id"]]
    assert (server.store.index_dir / "chunks-tfidf.json").exists()


# ---------------------------------------------------------------- citation links
LIB = {
    "s2-a": dict(title="Paper A on breathing modes", doi="10.1/a", arxiv=""),
    "s2-b": dict(title="Paper B on wall erosion", doi="", arxiv="2101.00002"),
    "s2-c": dict(title="Paper C without identifiers in our library", doi="", arxiv=""),
}
REFS = {  # who cites whom on (mock) Semantic Scholar
    "s2-a": [{"s2_id": "", "title": "", "doi": "", "arxiv": "2101.00002"},          # B, by arXiv id
             {"s2_id": "s2-out", "title": "Some paper not in the library", "doi": "10.9/x", "arxiv": ""},
             {"s2_id": "", "title": "paper c WITHOUT identifiers in our library!", "doi": "", "arxiv": ""}],  # C by title
    "s2-b": [{"s2_id": "s2-out2", "title": "Another outside paper", "doi": "", "arxiv": ""}],
    "s2-c": [],
}


def _mk_papers(server):
    ids = {}
    for s2, f in LIB.items():
        n = server.store.create({"type": "paper", "title": f["title"], "doi": f["doi"], "arxiv": f["arxiv"],
                                 "s2_id": s2 if s2 != "s2-c" else ""})
        ids[s2] = n["id"]
    return ids


def _mock_refs(server, monkeypatch, calls):
    def references(key, citing=False, max_items=1000):
        calls.append((key, citing))
        if citing:
            return [{"s2_id": k, "title": "", "doi": "", "arxiv": ""} for k, v in REFS.items()
                    if any(r.get("s2_id") == key for r in v)]
        return list(REFS.get(key, []))
    monkeypatch.setattr(server.scholar, "references", references)


def _wait(client, jid):
    for _ in range(200):
        j = client.get(f"/api/backfill/citations/{jid}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("backfill job never finished")


def test_backfill_links_library_papers(server, client, monkeypatch):
    calls = []
    _mock_refs(server, monkeypatch, calls)
    ids = _mk_papers(server)
    before = server.store.get(ids["s2-a"])["updated"]
    r = client.post("/api/backfill/citations").json()
    job = _wait(client, r["job_id"])
    assert job["state"] == "done" and job["total"] == 2 and job["done"] == 2, job  # C has no identifier
    assert job["linked"] == 2
    a = server.store.get(ids["s2-a"])
    assert a["cites"] == [ids["s2-b"], ids["s2-c"]]   # out-of-library refs ignored
    assert a["updated"] == before                       # derived field: not a user edit
    assert server.store.get(ids["s2-b"])["cites"] == []

    # idempotent
    job2 = _wait(client, client.post("/api/backfill/citations").json()["job_id"])
    assert job2["state"] == "done" and job2["linked"] == 0
    assert server.store.get(ids["s2-a"])["cites"] == [ids["s2-b"], ids["s2-c"]]

    # graph: directed cites links, manual link wins
    g = client.get("/api/graph").json()
    cites = [l for l in g["links"] if l["kind"] == "cites"]
    assert {(l["source"], l["target"]) for l in cites} == {(ids["s2-a"], ids["s2-b"]), (ids["s2-a"], ids["s2-c"])}
    client.post(f"/api/notes/{ids['s2-c']}/link", json={"target": ids["s2-a"]})
    g = client.get("/api/graph").json()
    pairs = {(l["source"], l["target"], l["kind"]) for l in g["links"]}
    assert (ids["s2-a"], ids["s2-c"], "cites") not in pairs
    assert any(k == "manual" for *_, k in pairs)
    assert (ids["s2-a"], ids["s2-b"], "cites") in pairs
    assert client.get("/api/backfill/citations/nope").status_code == 404


def test_cites_frontmatter_normalised(server):
    p = server.store.notes_dir / "hand.md"
    p.write_text("---\ntype: paper\ntitle: Hand edited\ncites: other-paper\n---\nbody\n", encoding="utf-8")
    assert server.store.get("hand")["cites"] == ["other-paper"]
    p.write_text("---\ntype: paper\ntitle: Hand edited\ncites: [a, null, 3]\n---\n", encoding="utf-8")
    assert server.store.get("hand")["cites"] == ["a", "3"]


def test_import_links_in_background_and_backoff(server, client, monkeypatch):
    calls = []
    _mock_refs(server, monkeypatch, calls)
    ids = _mk_papers(server)
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "1")
    # A paper imported later that A cites by s2 id: the incoming pass should add it to A's cites.
    REFS["s2-a"].append({"s2_id": "s2-new", "title": "", "doi": "", "arxiv": ""})
    try:
        monkeypatch.setattr(server.scholar, "lookup", lambda q: {"s2_id": "s2-new", "title": "New paper", "doi": "",
                                                                 "arxiv": "", "authors": [], "year": 2024})
        new = client.post("/api/import/lookup", json={"query": "new paper"})
        assert new.status_code == 201
        nid = new.json()["id"]
        for _ in range(200):
            if nid in server.store.get(ids["s2-a"])["cites"]:
                break
            time.sleep(0.02)
        assert nid in server.store.get(ids["s2-a"])["cites"]
        assert ("s2-new", False) in calls and ("s2-new", True) in calls
    finally:
        REFS["s2-a"].pop()

    # 429 backoff: retried, then succeeds
    seq = iter([server.scholar.ScholarError("Semantic Scholar is rate-limiting requests right now"), []])
    slept = []
    monkeypatch.setattr(server.citations, "_sleep", lambda s: slept.append(s))

    def flaky(key, citing=False, max_items=1000):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(server.scholar, "references", flaky)
    assert server.citations._fetch("s2-b") == []
    assert server.citations.BACKOFF[0] in slept


# ---------------------------------------------------------------- embedding eval harness
def test_embed_eval_tfidf(tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "eval"))
    import embed_eval
    from app.store import Store
    st = Store(tmp_path / "v")
    topics = {
        "plasma": "Hall thruster plasma breathing mode oscillation xenon ionization discharge",
        "ml": "Transformer attention neural network language model training tokens",
        "robot": "Robot arm inverse kinematics trajectory planning gripper control",
    }
    ids = {}
    for t, words in topics.items():
        for k in range(3):
            ids[(t, k)] = st.create({"type": "note", "title": f"{t} note {k}",
                                     "body": f"{words} variant {k} {words.split()[k]}"})["id"]
    pairs = tmp_path / "pairs.csv"
    pairs.write_text("a_id,b_id,related\n"
                     f"{ids[('plasma', 0)]},{ids[('plasma', 1)]},1\n"
                     f"{ids[('ml', 0)]},{ids[('ml', 2)]},1\n"
                     f"{ids[('plasma', 0)]},{ids[('ml', 1)]},0\n"
                     f"{ids[('robot', 0)]},{ids[('ml', 0)]},0\n"
                     "missing-note,another,1\n", encoding="utf-8")
    out = tmp_path / "results.md"
    rc = embed_eval.main(["--vault", str(tmp_path / "v"), "--pairs", str(pairs), "--out", str(out),
                          "--models", "tfidf", "not-a-real/model-xyz"])
    assert rc == 0
    res = out.read_text(encoding="utf-8")
    assert "| tfidf | 1.000 |" in res
    assert "not-a-real/model-xyz | skipped" in res
    printed = capsys.readouterr()
    assert "missing-note" in printed.err
    rc = embed_eval.main(["--vault", str(tmp_path / "v"), "--suggest", "-n", "10"])
    lines = [l for l in capsys.readouterr().out.splitlines() if l]
    assert rc == 0 and lines[0].startswith("a_id,b_id,related") and len(lines) == 11
