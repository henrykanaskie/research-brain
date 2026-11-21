"""AI summary of a paper (app/ai_summary.py and its endpoints in app/server.py): the prompt text, the digest
of a long paper, storage under state/ai-summaries/, caching of an unchanged paper, the SSE stream, deletion
with the paper, and the refusals (not a paper, private, nothing to summarise). No network, no real AI CLI.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.base import Backend as _Backend, Detection as _Detection  # noqa: E402


class FakeBackend(_Backend):
    """An agent that records every prompt it was sent and answers from a script."""

    def __init__(self, id="sumfake", kind="cli", script=()):
        self.id, self.name, self.kind, self.home, self.cost_note = id, f"Fake {id}", kind, "https://example.invalid", ""
        self.script = list(script)
        self.prompts: list[str] = []

    def detect(self):
        return _Detection(True, version="1", path="/fake", signed_in=True)

    def ask_stream(self, prompt, *, system="", timeout=180):
        self.prompts.append(prompt)
        reply = self.script.pop(0) if self.script else "## In one paragraph\nA summary."
        # two pieces, so the stream has more than one delta
        half = len(reply) // 2
        yield reply[:half]
        yield reply[half:]


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("RB_AGENT", raising=False)
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_MEMORY_WATCH", "0")
    monkeypatch.setenv("RB_WATCH", "0")
    monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    with TestClient(srv.app) as c:
        yield srv, c


def _use(srv, fb):
    from app import agents
    agents.register(fb)
    agents.set_active(fb.id)
    return fb


def _sse(text):
    out = []
    for frame in text.split("\n\n"):
        ev, data = None, []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if ev:
            out.append((ev, json.loads("\n".join(data))))
    return out


PAPER_TEXT = ("[[page 1]]\nAbstract. Breathing mode oscillations in Hall thrusters arise from an ionisation "
              "predator-prey cycle.\n[[page 2]]\nWe measure the frequency at 20 kHz.\n")


def _paper(srv, c, text=PAPER_TEXT, **kw):
    body = {"type": "paper", "title": "Hall thruster oscillations", "authors": ["A. Author"], "year": 2016,
            "abstract": "Breathing mode oscillations in Hall thrusters."}
    body.update(kw)
    r = c.post("/api/notes", json=body)
    assert r.status_code == 201, r.text
    p = r.json()
    if text is not None:
        tp = srv.store.text_path(p["id"])
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(text, encoding="utf-8")
    return p


def _file(srv, nid):
    return Path(srv.store.root) / "state" / "ai-summaries" / f"{nid}.json"


# ---------------------------------------------------------------- unit: prompt text and digest
def test_instruction_diagrams_and_abstract():
    from app import ai_summary as A
    with_d = A.instruction(True, False)
    without = A.instruction(False, False)
    assert "```mermaid" in with_d and "DIAGRAMS:" in with_d and A.NO_DIAGRAMS not in with_d
    assert "```mermaid" not in without and "DIAGRAMS:" not in without and without.endswith(A.NO_DIAGRAMS)
    for t in (with_d, without):
        assert t.startswith(A.INSTRUCTION) and "ONLY THE ABSTRACT" not in t
    abs_only = A.instruction(False, True)
    assert "ONLY THE ABSTRACT IS AVAILABLE" in abs_only and "Never invent details" in abs_only
    assert "ONLY THE ABSTRACT IS AVAILABLE" in A.instruction(True, True)


def test_digest_keeps_head_and_tail_and_stays_near_cap():
    from app import ai_summary as A
    head_mark, tail_mark = "HEADSTART" + "h" * 50, "t" * 50 + "TAILEND"
    text = head_mark + "".join(f"[[page {i}]] middle passage {i}. " * 20 for i in range(1, 400)) + tail_mark
    cap = 20_000
    assert len(text) > 5 * cap
    d = A.digest(text, cap)
    assert d.startswith("THE PAPER IS LONG")
    assert head_mark in d and tail_mark in d
    assert text[: int(cap * 0.4)] in d and text[-int(cap * 0.2):] in d
    # middle passages come from across the paper, not only its start
    assert "middle passage 1." in d and "[[page 3" in d
    # near the cap: the framing text and separators add a little, never a lot
    assert cap * 0.9 <= len(d) <= cap + 1000
    # a cap with no room for the middle still keeps head and tail
    small = A.digest("x" * 1000 + "END", 10)
    assert "END" in small and len(small) < 1000


def test_store_roundtrip(tmp_path):
    from app import ai_summary as A
    assert A.load(tmp_path, "p1") is None
    rec = A.save(tmp_path, "p1", "Hello", agent="Fake", paper_chars=5, source="full")
    assert A.load(tmp_path, "p1") == rec and rec["source"] == "full" and rec["at"]
    assert not list((tmp_path / "state" / "ai-summaries").glob("*.tmp"))
    (tmp_path / "state" / "ai-summaries" / "p2.json").write_text("{not json", encoding="utf-8")
    assert A.load(tmp_path, "p2") is None
    (tmp_path / "state" / "ai-summaries" / "p3.json").write_text(json.dumps({"text": "  "}), encoding="utf-8")
    assert A.load(tmp_path, "p3") is None
    assert A.delete(tmp_path, "p1") is True and A.delete(tmp_path, "p1") is False


# ---------------------------------------------------------------- endpoints
def test_get_post_cache_refresh_delete(server):
    srv, c = server
    fb = _use(srv, FakeBackend(script=["## In one paragraph\nFirst summary.", "## In one paragraph\nSecond one."]))
    p = _paper(srv, c)
    nid = p["id"]
    assert c.get(f"/api/notes/{nid}/ai-summary").json() == {"text": ""}
    assert not _file(srv, nid).exists()

    r = c.post(f"/api/notes/{nid}/ai-summary")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["text"] == "## In one paragraph\nFirst summary." and d["source"] == "full" and d["at"]
    assert d["agent"] == "Fake sumfake" and "cached" not in d
    assert len(fb.prompts) == 1
    sent = fb.prompts[0]
    assert "FULL PAPER TEXT" in sent and "20 kHz" in sent and "Hall thruster oscillations" in sent
    assert "```mermaid" in sent  # a hosted agent gets the diagram rules
    stored = json.loads(_file(srv, nid).read_text(encoding="utf-8"))
    assert stored["text"] == d["text"] and stored["paper_chars"] == len(PAPER_TEXT) and stored["source"] == "full"
    g = c.get(f"/api/notes/{nid}/ai-summary").json()
    assert g["text"] == d["text"] and "cached" not in g

    # unchanged paper: the stored one, nothing sent
    again = c.post(f"/api/notes/{nid}/ai-summary").json()
    assert again["cached"] is True and again["text"] == d["text"]
    assert len(fb.prompts) == 1

    # refresh: written again
    fresh = c.post(f"/api/notes/{nid}/ai-summary", params={"refresh": "true"}).json()
    assert fresh["text"] == "## In one paragraph\nSecond one." and "cached" not in fresh
    assert len(fb.prompts) == 2
    assert json.loads(_file(srv, nid).read_text(encoding="utf-8"))["text"] == fresh["text"]

    # the paper's text changed: no longer cached
    srv.store.text_path(nid).write_text(PAPER_TEXT + "\n[[page 3]]\nMore.\n", encoding="utf-8")
    changed = c.post(f"/api/notes/{nid}/ai-summary").json()
    assert "cached" not in changed and len(fb.prompts) == 3

    r = c.delete(f"/api/notes/{nid}/ai-summary")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert not _file(srv, nid).exists()
    assert c.get(f"/api/notes/{nid}/ai-summary").json() == {"text": ""}
    assert c.delete(f"/api/notes/{nid}/ai-summary").json() == {"ok": False}
    assert c.get("/api/notes/nope/ai-summary").status_code == 404
    assert c.post("/api/notes/nope/ai-summary").status_code == 404


def test_stream_emits_delta_and_done_and_stores(server):
    srv, c = server
    fb = _use(srv, FakeBackend(script=["## In one paragraph\nStreamed summary text."]))
    p = _paper(srv, c)
    nid = p["id"]
    r = c.post(f"/api/notes/{nid}/ai-summary/stream")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = _sse(r.text)
    kinds = [k for k, _ in evs]
    assert kinds.count("delta") >= 1 and kinds[-1] == "done" and "error" not in kinds
    assert "".join(x["text"] for k, x in evs if k == "delta") == "## In one paragraph\nStreamed summary text."
    done = evs[-1][1]
    assert done["text"] == "## In one paragraph\nStreamed summary text." and done["source"] == "full"
    assert json.loads(_file(srv, nid).read_text(encoding="utf-8"))["text"] == done["text"]
    assert len(fb.prompts) == 1

    # again: the stored one as a single delta + done, cached, nothing sent
    evs = _sse(c.post(f"/api/notes/{nid}/ai-summary/stream").text)
    assert [k for k, _ in evs] == ["delta", "done"] and evs[1][1]["cached"] is True
    assert evs[0][1]["text"] == done["text"] and len(fb.prompts) == 1
    # refresh: sent again
    evs = _sse(c.post(f"/api/notes/{nid}/ai-summary/stream", params={"refresh": "true"}).text)
    assert evs[-1][0] == "done" and "cached" not in evs[-1][1] and len(fb.prompts) == 2


def test_stream_empty_answer_is_an_error(server):
    srv, c = server
    _use(srv, FakeBackend(script=["   "]))
    p = _paper(srv, c)
    evs = _sse(c.post(f"/api/notes/{p['id']}/ai-summary/stream").text)
    assert evs[-1][0] == "error" and "empty" in evs[-1][1]["detail"]
    assert not _file(srv, p["id"]).exists()


def test_refusals(server):
    srv, c = server
    fb = _use(srv, FakeBackend())
    paper = _paper(srv, c)
    frag = c.post("/api/notes", json={"type": "fragment", "parent": paper["id"], "quote": "q", "body": "b",
                                      "private": False}).json()
    note = c.post("/api/notes", json={"type": "note", "title": "A note", "body": "x", "private": False}).json()
    for nid in (frag["id"], note["id"]):
        assert c.post(f"/api/notes/{nid}/ai-summary").status_code == 400
        assert c.post(f"/api/notes/{nid}/ai-summary/stream").status_code == 400
    secret = _paper(srv, c, title="My unpublished draft", private=True)
    r = c.post(f"/api/notes/{secret['id']}/ai-summary")
    assert r.status_code == 403 and "private" in r.json()["detail"]
    assert c.post(f"/api/notes/{secret['id']}/ai-summary/stream").status_code == 403
    bare = _paper(srv, c, text=None, title="Nothing yet", abstract="")
    r = c.post(f"/api/notes/{bare['id']}/ai-summary")
    assert r.status_code == 400 and "no text or abstract" in r.json()["detail"]
    assert c.post(f"/api/notes/{bare['id']}/ai-summary/stream").status_code == 400
    assert fb.prompts == []
    assert not list((Path(srv.store.root) / "state" / "ai-summaries").glob("*.json"))


def test_abstract_only(server):
    srv, c = server
    fb = _use(srv, FakeBackend())
    p = _paper(srv, c, text=None, abstract="Breathing mode oscillations in Hall thrusters, 20 kHz.")
    d = c.post(f"/api/notes/{p['id']}/ai-summary").json()
    assert d["source"] == "abstract"
    sent = fb.prompts[0]
    assert "ABSTRACT ONLY (no PDF attached)" in sent and "ONLY THE ABSTRACT IS AVAILABLE" in sent
    assert "FULL PAPER TEXT" not in sent
    # no text still: the stored one is cached
    assert c.post(f"/api/notes/{p['id']}/ai-summary").json()["cached"] is True and len(fb.prompts) == 1


def test_long_paper_is_digested(server, monkeypatch):
    srv, c = server
    fb = _use(srv, FakeBackend())
    monkeypatch.setattr(srv, "CLAUDE_CONTEXT_CHARS", 2000)
    text = "OPENINGWORDS " + "middle text. " * 2000 + " CLOSINGWORDS"
    p = _paper(srv, c, text=text)
    d = c.post(f"/api/notes/{p['id']}/ai-summary").json()
    assert d["source"] == "digest"
    sent = fb.prompts[0]
    assert "THE PAPER IS LONG" in sent and "OPENINGWORDS" in sent and "CLOSINGWORDS" in sent
    assert len(sent) < len(text)


def test_local_server_agent_gets_no_diagrams(server):
    srv, c = server
    fb = _use(srv, FakeBackend(id="localfake", kind="local-server"))
    p = _paper(srv, c)
    assert c.post(f"/api/notes/{p['id']}/ai-summary").status_code == 200
    sent = fb.prompts[0]
    assert "```mermaid" not in sent and "Do not draw diagrams" in sent


def test_deleting_the_paper_removes_its_summary(server):
    srv, c = server
    _use(srv, FakeBackend())
    p = _paper(srv, c)
    other = _paper(srv, c, title="Another paper")
    c.post(f"/api/notes/{p['id']}/ai-summary")
    c.post(f"/api/notes/{other['id']}/ai-summary")
    assert _file(srv, p["id"]).exists() and _file(srv, other["id"]).exists()
    r = c.delete(f"/api/notes/{p['id']}")
    assert r.status_code in (200, 204), r.text
    assert not _file(srv, p["id"]).exists()
    assert _file(srv, other["id"]).exists()
