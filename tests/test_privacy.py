"""Privacy of your own ideas (app/privacy.py): what is private by default, inheritance and settings; the
guard_prompt choke point (ids, copied text, no false alarms on stock phrases, speed); every server AI path
refusing or filtering private notes; the agent tools; memory; the local-AI exception; the audit log and its
rotation; exports. No network, no real AI CLI.
"""
from __future__ import annotations

import importlib
import json
import random
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.base import Backend as _Backend, Detection as _Detection  # noqa: E402

IDEA = ("My novel idea is to drive the hollow cathode with a pulsed radio frequency waveform so the plume "
        "oscillation locks to the breathing mode and the anomalous electron transport drops by half")


class FakeBackend(_Backend):
    def __init__(self, id="pfake", kind="cli", script=()):
        self.id, self.name, self.kind, self.home, self.cost_note = id, f"Fake {id}", kind, "https://example.invalid", ""
        self.script = list(script)
        self.prompts: list[str] = []

    def detect(self):
        return _Detection(True, version="1", path="/fake", signed_in=True)

    def ask_stream(self, prompt, *, system="", timeout=180):
        self.prompts.append(prompt)
        reply = self.script.pop(0) if self.script else "An answer."
        yield reply


# ---------------------------------------------------------------- unit: a bare store
@pytest.fixture()
def lib(tmp_path):
    from app import privacy
    from app.store import Store
    s = Store(tmp_path / "vault")
    privacy.configure(s)
    yield s, privacy
    privacy.configure(None)


def test_defaults_inheritance_and_settings(lib):
    s, P = lib
    paper = s.create({"type": "paper", "title": "Hall thruster paper", "summary": "my summary"})
    note = s.create({"type": "note", "title": "Standalone idea", "body": "x"})
    shared = s.create({"type": "note", "title": "Shared note", "private": False})
    q = s.create({"type": "question", "title": "Why does it oscillate?"})
    frag = s.create({"type": "fragment", "parent": paper["id"], "quote": "q", "title": "f"})
    assert not P.is_private(paper) and not P.is_private(frag)       # papers, fragments, summaries: shareable
    assert P.is_private(note) and P.is_private(q)                   # standalone notes and questions: private
    assert not P.is_private(shared)                                 # explicit private: false wins over the default
    assert P.explicit_flag(shared) is False and P.explicit_flag(note) is None
    # a fragment of a private paper inherits, even with its own private: false
    s.update(paper["id"], {"private": True})
    P.refresh()
    assert P.is_private(s.get(paper["id"])) and P.is_private(s.get(frag["id"]))
    f2 = s.create({"type": "fragment", "parent": paper["id"], "quote": "q2", "title": "f2", "private": False})
    P.refresh()
    assert P.is_private(f2) and P.is_private_id(f2["id"])
    # the private flag round-trips through the file (an extra property, so other tools keep it)
    raw = s.note_path(paper["id"]).read_text()
    assert "private: true" in raw
    s.update(paper["id"], {"private": None})
    assert "private:" not in s.note_path(paper["id"]).read_text()
    # settings live in the library (vault/state/privacy.json), not the browser
    P.save_settings(private_by_default=["paper", "question"])
    P.refresh()
    assert json.loads((s.root / "state" / "privacy.json").read_text())["private_by_default"] == ["paper", "question"]
    assert P.is_private(s.get(paper["id"])) and P.is_private(s.get(frag["id"]))  # fragment inherits from paper
    assert not P.is_private(s.get(note["id"])) and P.is_private(s.get(q["id"]))
    P.configure(s)  # a fresh start reads the file again
    assert P.settings()["private_by_default"] == ["paper", "question"]
    with pytest.raises(ValueError):
        P.save_settings(private_by_default=["no spaces allowed"])


def test_allow_ai_and_local_exception(lib):
    s, P = lib
    note = s.create({"type": "note", "title": "Idea"})
    paper = s.create({"type": "paper", "title": "Paper"})
    cloud, local = FakeBackend("c", "cli"), FakeBackend("l", "local-server")
    assert not P.allow_ai(note, cloud) and not P.allow_ai(note, local) and P.allow_ai(paper, cloud)
    assert [n["id"] for n in P.filter_for_ai([note, paper], cloud)] == [paper["id"]]
    P.save_settings(private_ok_for_local_ai=True)
    assert P.allow_ai(note, local) and not P.allow_ai(note, cloud)
    assert len(P.filter_for_ai([note, paper], local)) == 2
    P.guard_prompt(f"[[{note['id']}]] {IDEA}", local)  # the exception: no refusal
    with pytest.raises(P.PrivacyError):
        P.guard_prompt(f"[[{note['id']}]]", cloud)


def test_guard_ids_and_copied_text(lib):
    s, P = lib
    idea = s.create({"type": "note", "title": "Pulsed cathode idea", "body": IDEA})
    s.create({"type": "note", "title": "plasma", "body": "short"})  # one-word id: only [[plasma]] counts
    s.create({"type": "paper", "title": "Public paper", "abstract": IDEA + " (also in a shareable paper)",
              "private": False})
    P.refresh()
    cloud = FakeBackend()
    with pytest.raises(P.PrivacyError) as e:
        P.guard_prompt(f"see [[{idea['id']}]] please", cloud)
    assert "This includes private notes" in str(e.value) and "Pulsed cathode idea" in str(e.value)
    with pytest.raises(P.PrivacyError):
        P.guard_prompt(f"bare id {idea['id']} in prose", cloud)
    with pytest.raises(P.PrivacyError):
        P.guard_prompt("[[Pulsed cathode idea]]", cloud)  # a link by title
    with pytest.raises(P.PrivacyError):
        P.guard_prompt("Context: " + IDEA.upper() + ". Thanks.", cloud)  # copied text, case and punctuation aside
    # the fingerprints of a private item still match when the same words also sit in a shareable item:
    # private wins. But ordinary prose doesn't trip it:
    P.guard_prompt("plasma physics is fun; the word plasma alone is fine", cloud)
    P.guard_prompt("The hollow cathode emits electrons. A pulsed waveform might help the plume.", cloud)  # paraphrase
    P.guard_prompt(" ".join(IDEA.split()[:10]), cloud)  # 10 copied words: below the 16-word bar
    with pytest.raises(P.PrivacyError):
        P.guard_prompt("[[plasma]]", cloud)
    assert P.scrub(f"a [[{idea['id']}|my idea]] and [[Pulsed cathode idea]] and [[other]]") == \
        "a [[a private note]] and [[a private note]] and [[other]]"


def test_a_private_note_linking_a_paper_does_not_block_that_paper(lib):
    """A link's words are the linked paper's title (ids are slugs of titles), not the private note's own
    text: fingerprinting them made every prompt that named the paper look like a leak."""
    s, P = lib
    paper = s.create({"type": "paper", "title": "A collisional-radiative model for krypton 2p levels in low-temperature plasmas",
                      "private": False})
    secret = s.create({"type": "note", "title": "Plan for the Kr CRM validation",
                       "body": f"Compare our model against [[{paper['id']}]]. " + IDEA})
    P.refresh()
    cloud = FakeBackend()
    P.guard_prompt("FOCUS PAPER: A collisional-radiative model for krypton 2p levels in low-temperature plasmas. "
                   "The 826/758 ratio is the workhorse.", cloud)  # naming the paper is fine
    with pytest.raises(P.PrivacyError):
        P.guard_prompt("Context: " + IDEA, cloud)  # the note's own words still are not
    with pytest.raises(P.PrivacyError):
        P.guard_prompt(f"see [[{secret['id']}]]", cloud)


def test_guard_ignores_shared_stock_phrases(lib):
    s, P = lib
    stock = ("on the other hand it is not clear that this is the case and in this paper we "
             "propose a novel method for the problem as shown in the results")
    s.create({"type": "note", "title": "Stock", "body": stock})
    P.refresh()
    cloud = FakeBackend()
    # a shareable paper that uses the same boilerplate: mostly stop words, never indexed
    P.guard_prompt("As is common, on the other hand it is not clear that this is the case and in this paper "
                   "we argue otherwise.", cloud)
    P.guard_prompt("In this paper we propose a novel method for the problem as shown in the results below, "
                   "which is about Hall thrusters.", cloud)


def test_guard_speed_2000_private_items(lib):
    s, P = lib
    rng = random.Random(7)
    vocab = [f"w{i}x" for i in range(5000)]
    for i in range(2000):
        s.create({"type": "note", "title": f"idea {i}", "body": " ".join(rng.choice(vocab) for _ in range(120))})
    t0 = time.perf_counter()
    P.refresh()
    build = time.perf_counter() - t0
    assert len(P.private_ids()) == 2000
    text = " ".join(rng.choice(vocab + ["the", "plasma", "of"]) for _ in range(9000))[:50_000]
    assert len(text) >= 45_000
    best = min(_timed(lambda: P.find_leaks(text)) for _ in range(5))
    assert best < 0.020, f"guard took {best * 1000:.1f} ms"
    assert build < 30, build


def _timed(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def test_audit_log_and_rotation(lib, monkeypatch):
    s, P = lib
    paper = s.create({"type": "paper", "title": "Paper"})
    note = s.create({"type": "note", "title": "Idea"})
    P.refresh()
    b = FakeBackend()
    e = P.log_send(b, "chat", [paper["id"], note["id"]], 1234)
    assert e["items"] == [paper["id"], note["id"]] and e["private_items"] == [note["id"]] and e["chars"] == 1234
    line = (s.root / "state" / "ai_log.jsonl").read_text()
    assert "Idea" not in line and set(json.loads(line)) >= {"at", "agent", "model", "purpose", "items", "chars"}
    monkeypatch.setattr(P, "LOG_MAX_BYTES", 600)
    for i in range(10):
        P.log_send(b, f"p{i}", [paper["id"]], i)
    assert (s.root / "state" / "ai_log.1.jsonl").exists()
    assert (s.root / "state" / "ai_log.jsonl").stat().st_size <= 600
    got = P.read_log(5)
    assert [x["purpose"] for x in got] == ["p9", "p8", "p7", "p6", "p5"]


def test_backend_wrapper_is_the_choke_point(lib):
    s, P = lib
    from app import agents
    idea = s.create({"type": "note", "title": "Idea", "body": IDEA})
    s.create({"type": "note", "title": "Pulsed cathode idea x"})
    P.refresh()
    fb = FakeBackend("wrapped")
    agents.register(fb)
    try:
        with pytest.raises(P.PrivacyError):  # even a direct backend call, bypassing the server
            list(fb.ask_stream("Please consider: " + IDEA))
        with pytest.raises(P.PrivacyError):
            fb.ask("bare id: pulsed-cathode-idea-x")
        assert fb.ask(f"see [[{idea['id']}]]") == "An answer." and fb.prompts == ["see [[a private note]]"]  # scrubbed
        fb.prompts.clear()
        with pytest.raises(P.PrivacyError):
            list(agents.stream_parts("Summarise.", IDEA, backend=fb))
        assert fb.prompts == []  # nothing reached the model
        assert agents.ask_parts("Hi", "about plasma", backend=fb, purpose="chat") == "An answer."
        log = P.read_log(10)
        assert log[0]["purpose"] == "chat" and log[1]["purpose"] == "other" and len(log) == 2  # each send logged once
    finally:
        agents.unregister("wrapped")


# ---------------------------------------------------------------- the server
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


def _library(c):
    paper = c.post("/api/notes", json={"type": "paper", "title": "Hall thruster oscillations",
                                       "abstract": "Breathing mode oscillations in Hall thrusters.",
                                       "summary": "The breathing mode is an ionisation instability."}).json()
    idea = c.post("/api/notes", json={"type": "note", "title": "Pulsed cathode idea", "body": IDEA}).json()
    secret_paper = c.post("/api/notes", json={"type": "paper", "title": "My unpublished draft", "private": True,
                                              "summary": "Breathing mode suppression by pulsing, my own result."}).json()
    return paper, idea, secret_paper


def test_note_api_privacy_roundtrip_and_graph(server):
    srv, c = server
    paper, idea, secret = _library(c)
    n = c.get(f"/api/notes/{idea['id']}").json()
    assert n["private"] is True and n["private_explicit"] is None
    assert c.get(f"/api/notes/{paper['id']}").json()["private"] is False
    r = c.post(f"/api/notes/{paper['id']}/privacy", json={"private": True}).json()
    assert r["private"] is True and r["private_explicit"] is True
    r = c.put(f"/api/notes/{paper['id']}", json={"private": False}).json()
    assert r["private"] is False and r["private_explicit"] is False
    nodes = {x["id"]: x for x in c.get("/api/graph").json()["nodes"]}
    assert nodes[idea["id"]]["private"] is True and nodes[paper["id"]]["private"] is False
    # settings change -> graph cache refreshes
    c.put("/api/privacy/settings", json={"private_by_default": ["question"]})
    nodes = {x["id"]: x for x in c.get("/api/graph").json()["nodes"]}
    assert nodes[idea["id"]]["private"] is False
    d = c.get("/api/privacy").json()
    assert d["settings"]["private_by_default"] == ["question"] and "note" in d["kinds"]
    assert c.post("/api/notes/nope/privacy", json={"private": True}).status_code == 404
    # search and similarity stay local and keep working for private notes
    assert any(h["id"] == secret["id"] for h in c.get("/api/search", params={"q": "unpublished"}).json())


def test_ai_paths_refuse_or_filter_private(server):
    srv, c = server
    paper, idea, secret = _library(c)
    fb = _use(srv, FakeBackend(script=["answer"] * 20))
    try:
        # critique of a private paper's summary: a clear refusal, nothing sent
        r = c.post(f"/api/notes/{secret['id']}/critique")
        assert r.status_code == 403 and "private" in r.json()["detail"] and "My unpublished draft" in r.json()["detail"]
        assert c.post(f"/api/notes/{secret['id']}/critique/stream").status_code == 403  # before the stream starts
        assert c.post("/api/explain", json={"a": paper["id"], "b": idea["id"]}).status_code == 403
        assert c.post(f"/api/review/{secret['id']}/check", json={"recalled_text": "x"}).status_code == 403
        assert fb.prompts == []
        # chat: the private notes are not retrieved, the shareable paper is
        r = c.post("/api/chat", json={"question": "breathing mode pulsed cathode idea unpublished draft?", "history": []})
        assert r.status_code == 200
        prompt = fb.prompts[-1]
        assert paper["id"] in prompt and idea["id"] not in prompt and secret["id"] not in prompt
        assert "anomalous electron transport" not in prompt and "my own result" not in prompt
        assert all(s["id"] != idea["id"] for s in r.json()["sources"])
        r = c.post("/api/chat/stream", json={"question": "pulsed cathode idea?", "history": []})
        assert _sse(r.text)[-1][0] == "done" and idea["id"] not in fb.prompts[-1]
        # the backstop: a shareable note that copies the private idea is refused by the guard
        c.post("/api/notes", json={"type": "note", "private": False, "title": "Copy", "body": "Pasted: " + IDEA})
        r = c.post("/api/chat", json={"question": "hollow cathode pulsed radio frequency waveform", "history": []})
        assert r.status_code == 403 and "This includes private notes" in r.json()["detail"]
        # ... and a shareable note linking to the private one is sent with the link scrubbed
        c.post("/api/notes", json={"type": "note", "private": False, "title": "Linker",
                                   "body": f"Zebra quokka see [[{idea['id']}]] and [[Pulsed cathode idea]]."})
        r = c.post("/api/chat", json={"question": "zebra quokka", "history": []})
        assert r.status_code == 200
        assert "[[a private note]]" in fb.prompts[-1] and idea["id"] not in fb.prompts[-1]
        log = c.get("/api/privacy/log").json()
        assert log["entries"][0]["purpose"] == "chat" and all("text" not in e for e in log["entries"])
        assert any(paper["id"] in e["items"] for e in log["entries"]) and log["titles"]
    finally:
        from app import agents
        agents.unregister(fb.id)


def test_research_and_tools_never_see_private(server):
    srv, c = server
    paper, idea, secret = _library(c)
    fb = _use(srv, FakeBackend(script=[
        '{"action":"search","queries":["pulsed cathode idea", "unpublished draft"]}',
        '{"action":"read","id":"%s"}' % idea["id"],
        '{"action":"answer","text":"Done [[%s]]."}' % paper["id"]]))
    try:
        evs = _sse(c.post("/api/research/stream", json={"question": "pulsed cathode breathing mode?"}).text)
        assert evs[-1][0] == "done", evs[-3:]
        sent = "\n".join(fb.prompts)
        assert idea["id"] not in sent and secret["id"] not in sent and "anomalous electron transport" not in sent
        assert "No note with id" in sent  # reading a private note looks like reading a missing one
        assert any(e["purpose"] == "research" for e in c.get("/api/privacy/log").json()["entries"])
        # the tools directly (also what the Claude native MCP path calls)
        run = srv.agent_mod.open_run("q")
        for name, args in (("search", {"queries": ["pulsed cathode", "unpublished draft"]}),
                           ("search_notes", {"query": "pulsed"}), ("recent", {}), ("similar", {"id": paper["id"]}),
                           ("citations", {"id": paper["id"]}), ("memory_map", {})):
            out = srv.agent_tools.call(name, args, run)
            assert idea["id"] not in out and secret["id"] not in out, (name, out)
        with pytest.raises(ValueError):
            srv.agent_tools.call("read_note", {"id": idea["id"]}, run)
        # memory refuses private material
        out = srv.agent_tools.call("remember", {"text": "Pulsing helps.", "sources": [secret["id"]]}, run)
        assert out.startswith("Not saved") and "private" in out
        out = srv.agent_tools.call("remember", {"text": " ".join(IDEA.split()[:20]) + ".", "sources": [paper["id"]]}, run)
        assert out.startswith("Not saved") and "private" in out
        assert srv.memory.findings()[0] == []
        srv.agent_mod.close_run(run)
    finally:
        from app import agents
        agents.unregister(fb.id)


def test_local_ai_exception(server):
    srv, c = server
    paper, idea, secret = _library(c)
    fb = _use(srv, FakeBackend("plocal", kind="local-server", script=["ok"] * 5))
    try:
        c.post("/api/chat", json={"question": "pulsed cathode idea", "history": []})
        assert idea["id"] not in fb.prompts[-1]  # off by default
        c.put("/api/privacy/settings", json={"private_ok_for_local_ai": True})
        r = c.post("/api/chat", json={"question": "pulsed cathode idea", "history": []})
        assert r.status_code == 200 and idea["id"] in fb.prompts[-1]
        assert c.post(f"/api/notes/{secret['id']}/critique").status_code == 200
        entries = c.get("/api/privacy/log").json()["entries"]
        assert entries[0]["private_items"] and entries[0]["kind"] == "local-server"
    finally:
        from app import agents
        agents.unregister(fb.id)


def test_exports_exclude_private_by_default(server):
    srv, c = server
    paper, idea, secret = _library(c)
    c.put(f"/api/notes/{paper['id']}", json={"summary": f"See [[{idea['id']}|my idea]] and [[Pulsed cathode idea]]."})
    r = c.post("/api/export", json={"ids": None})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["private_skipped"] == 2 and out["notes"] == 1
    html = "".join(p.read_text() for p in Path(out["path"]).rglob("*.html"))
    assert "Pulsed cathode idea" not in html and "anomalous electron" not in html and "My unpublished draft" not in html
    assert idea["id"] not in html and "a private note" in html
    r = c.post("/api/export", json={"ids": [idea["id"]]})
    assert r.status_code == 400 and "private" in r.json()["detail"]
    r = c.post("/api/export", json={"ids": None, "include_private": True}).json()
    assert r["notes"] == 3 and r["private_skipped"] == 0
    html = "".join(p.read_text() for p in Path(r["path"]).rglob("*.html"))
    assert "Pulsed cathode idea" in html


def test_guard_covers_private_idea_cards(lib):
    """Private idea cards are fingerprinted by the guard too, not only filtered by the cards code."""
    s, P = lib
    from app import cards
    paper = s.create({"type": "paper", "title": "Shareable paper", "abstract": "Hall thruster plume.", "private": False})
    cards.configure(s)
    mine = cards.card_store().add(paper["id"], {"kind": "claim", "text": IDEA}, origin="user", private=True)
    P.refresh()
    with pytest.raises(P.PrivacyError):
        P.guard_prompt("Context: " + IDEA + ". Thanks.", FakeBackend())
    # once shared, the same text may go out
    cards.card_store().update(mine["id"], {"private": False})
    P.refresh()
    P.guard_prompt("Context: " + IDEA + ". Thanks.", FakeBackend())
