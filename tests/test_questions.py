"""Open questions (app/questions.py, app/questions_api.py): local evidence matching on a small realistic plasma
corpus with TF-IDF, thresholds, dedupe, dismissals that never come back, the daily cap, the triggers (new paper,
new note, new idea card), the API, and privacy: a question never reaches an AI agent.

No network: the server runs with RB_EMBEDDER=tfidf and a scripted fake AI backend.
Run:  python3 -m pytest -q tests/test_questions.py
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FX = json.loads((ROOT / "tests" / "fixtures" / "questions" / "plasma_corpus.json").read_text())

Q_TITLE = FX["questions"]["breathing_crm"]["title"]
Q_BODY = FX["questions"]["breathing_crm"]["body"]
Q_ANSWER = "Zanzibar heuristic: the quokka frequency ratio tracks electron temperature cubed over the channel length."


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("RB_AGENT", raising=False)
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_MEMORY_WATCH", "0")
    monkeypatch.setenv("RB_WATCH", "0")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    monkeypatch.setenv("RB_QUESTIONS_DEBOUNCE", "0.05")
    monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    with TestClient(srv.app) as c:
        yield srv, c


def matcher(srv):
    return srv.questions_api.ctx.matcher


def settle(srv):
    assert matcher(srv).wait_idle(30), "the question matcher didn't finish"


def add_paper(srv, c, key):
    p = FX["papers"][key]
    n = c.post("/api/notes", json={"type": "paper", "title": p["title"], "abstract": p["abstract"],
                                   "authors": p["authors"], "year": p["year"]}).json()
    srv.store.text_path(n["id"]).write_text("".join(f"[[page {i + 1}]]\n{t}\n\n" for i, t in enumerate(p["pages"])))
    c.put(f"/api/notes/{n['id']}", json={"venue": "Test venue"})  # a save after the text exists
    return n["id"]


def add_note(c, key):
    n = FX["notes"][key]
    return c.post("/api/notes", json={"type": "note", "title": n["title"], "body": n["body"]}).json()["id"]


def add_question(c, key="breathing_crm", **extra):
    q = FX["questions"][key]
    r = c.post("/api/notes", json={"type": "question", "title": q["title"], "body": q["body"], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def library(srv, c, papers=None, notes=("idea_te", "shopping")):
    ids = {k: add_paper(srv, c, k) for k in (papers or FX["papers"])}
    ids.update({"note:" + k: add_note(c, k) for k in notes})
    return ids


def evidence(c, qid):
    r = c.get(f"/api/questions/{qid}/evidence")
    assert r.status_code == 200, r.text
    return r.json()


def where_set(ev, ids):
    inv = {v: k for k, v in ids.items()}
    out = set()
    for i in ev["items"]:
        src = inv.get(i["ref"].get("note"), i["ref"].get("note"))
        out.add((src, i["ref"].get("page")) if i["kind"] == "passage" else (src,))
    return out


# ---------------------------------------------------------------- storage and the notes API
def test_question_is_a_note(server):
    srv, c = server
    q = add_question(c)
    assert q["type"] == "question" and q["question_status"] == "open" and q["answer"] == "" and q["private"] is True
    r = c.put(f"/api/notes/{q['id']}", json={"answer": "Maybe, via $T_e$. See [[Some paper]].", "question_status": "parked"})
    assert r.status_code == 200 and r.json()["question_status"] == "parked" and "T_e" in r.json()["answer"]
    assert c.put(f"/api/notes/{q['id']}", json={"question_status": "maybe"}).status_code == 400
    raw = srv.store.note_path(q["id"]).read_text()
    assert "type: question" in raw and "question_status: parked" in raw and "answer:" in raw
    # other notes don't grow the question properties
    n = c.post("/api/notes", json={"type": "note", "title": "Plain"}).json()
    raw = srv.store.note_path(n["id"]).read_text()
    assert "question_status" not in raw and "answer" not in raw and "last_seen" not in raw
    assert c.put(f"/api/notes/{n['id']}", json={"question_status": "open"}).json()["question_status"] == ""
    # the answer's [[links]] are wikilinks (unresolved here)
    assert "Some paper" in c.get(f"/api/notes/{q['id']}").json()["unresolved"]
    # it shows up in search and in the graph as a question node
    assert any(h["id"] == q["id"] for h in c.get("/api/search", params={"q": "breathing"}).json())
    node = next(x for x in c.get("/api/graph").json()["nodes"] if x["id"] == q["id"])
    assert node["type"] == "question" and node["new_evidence"] == 0


# ---------------------------------------------------------------- matching
def test_matching_finds_the_right_passages(server):
    srv, c = server
    ids = library(srv, c)
    q1 = add_question(c, "breathing_crm")
    q2 = add_question(c, "shielding")
    c.post(f"/api/questions/{q1['id']}/rescan")
    c.post(f"/api/questions/{q2['id']}/rescan")
    got1 = where_set(evidence(c, q1["id"]), ids)
    want1 = {("breathing", 3), ("krcrm", 2), ("oes", 3), ("note:idea_te",)}
    assert got1 == want1, got1
    got2 = where_set(evidence(c, q2["id"]), ids)
    assert got2 == {("erosion", 1), ("erosion", 2)}, got2
    for ev in (evidence(c, q1["id"]), evidence(c, q2["id"])):
        assert all(i["verdict"] == "new" and i["new"] and i["snippet"] for i in ev["items"])
        assert not any(i["snippet"].startswith(("Abstract:", "Student's")) for i in ev["items"])
    # the passage carries where it is and a link to it
    p3 = next(i for i in evidence(c, q1["id"])["items"] if i["ref"].get("page") == 3 and i["ref"]["note"] == ids["breathing"])
    assert p3["where"] == "p. 3" and p3["source"]["title"] == FX["papers"]["breathing"]["title"]
    assert "predator-prey" in p3["snippet"]


def test_threshold_separates_relevant_from_unrelated(server):
    srv, c = server
    from app import questions as Q
    ids = library(srv, c)
    q = add_question(c)
    ctx = matcher(srv).ctx
    cands = Q.find_candidates(ctx, srv.store.get(q["id"]))
    relevant = {(ids[k], p) for k, p in FX["relevant"]["breathing_crm"] if k != "note"}
    unrelated = {ids[k] for k in FX["irrelevant_papers"]} | {ids["note:shopping"]}
    rel = [x["score"] for x in cands if x["kind"] == "passage" and (x["ref"]["note"], x["ref"]["page"]) in relevant]
    bad = [x["score"] for x in cands if x["ref"].get("note") in unrelated]
    thr = Q.threshold_for("tfidf")
    assert len(rel) == 3 and min(rel) > thr, (rel, thr)
    assert not bad or max(bad) < thr, (bad, thr)
    assert min(rel) - thr > 0.02 and thr - max(bad, default=0) > 0.02  # a margin, not a coin flip
    # the threshold is a setting; raising it keeps everything out
    r = c.put("/api/questions/settings", json={"threshold_tfidf": 0.95})
    assert r.status_code == 200 and r.json()["threshold"] == 0.95 and r.json()["family"] == "tfidf"
    assert c.post(f"/api/questions/{q['id']}/rescan").json()["added"] == 0
    assert c.put("/api/questions/settings", json={"threshold_tfidf": 2}).status_code == 400
    assert c.put("/api/questions/settings", json={"daily_cap": 0}).status_code == 400
    s = c.get("/api/questions/settings").json()
    assert s["defaults"]["threshold_tfidf"] == Q.THRESHOLDS["tfidf"] and s["defaults"]["threshold_dense"] == Q.THRESHOLDS["dense"]


def test_dense_threshold_is_used_for_sentence_transformers(server):
    from app import questions as Q
    assert Q.embedder_family("sentence-transformers:all-MiniLM-L6-v2") == "dense"
    assert Q.threshold_for("sentence-transformers:all-MiniLM-L6-v2") == Q.THRESHOLDS["dense"]
    assert Q.threshold_for("tfidf") == Q.THRESHOLDS["tfidf"]
    Q.save_settings({"threshold_dense": 0.5})
    assert Q.threshold_for("sentence-transformers:x") == 0.5


def test_dedupe_and_dismissed_never_returns(server):
    srv, c = server
    ids = library(srv, c)
    q = add_question(c)
    settle(srv)  # the background matcher may have found them already
    c.post(f"/api/questions/{q['id']}/rescan")
    assert evidence(c, q["id"])["counts"]["total"] == 4
    assert c.post(f"/api/questions/{q['id']}/rescan").json()["added"] == 0  # nothing twice
    ev = evidence(c, q["id"])
    keys = [i["key"] for i in ev["items"]]
    assert len(keys) == len(set(keys))
    bp = next(i for i in ev["items"] if i["ref"]["note"] == ids["breathing"])
    note_item = next(i for i in ev["items"] if i["kind"] == "note")
    for it in (bp, note_item):
        r = c.put(f"/api/questions/{q['id']}/evidence/{it['id']}", json={"verdict": "dismissed"})
        assert r.status_code == 200 and r.json()["item"]["verdict"] == "dismissed"
    # the passage's text changes a little (re-extracted PDF), the note is edited: still never comes back
    tp = srv.store.text_path(ids["breathing"])
    tp.write_text(tp.read_text().replace("The breathing mode is a large-amplitude", "The breathing mode is a strong, large-amplitude"))
    c.put(f"/api/notes/{ids['breathing']}", json={"venue": "Edited"})
    c.put(f"/api/notes/{ids['note:idea_te']}", json={"body": FX["notes"]["idea_te"]["body"] + " Worth a test."})
    assert c.post(f"/api/questions/{q['id']}/rescan").json()["added"] == 0
    settle(srv)
    ev = evidence(c, q["id"])
    assert sum(1 for i in ev["items"] if i["ref"]["note"] == ids["breathing"]) == 1
    assert sum(1 for i in ev["items"] if i["ref"]["note"] == ids["note:idea_te"]) == 1
    assert ev["counts"]["dismissed"] == 2
    # bad requests
    assert c.put(f"/api/questions/{q['id']}/evidence/{bp['id']}", json={"verdict": "maybe"}).status_code == 400
    assert c.put(f"/api/questions/{q['id']}/evidence/nope", json={"verdict": "supports"}).status_code == 404
    assert c.get("/api/questions/nope/evidence").status_code == 404
    assert c.get(f"/api/questions/{ids['breathing']}/evidence").status_code == 404  # not a question


def test_daily_cap(server):
    srv, c = server
    library(srv, c)
    c.put("/api/questions/settings", json={"daily_cap": 2})
    q = add_question(c)
    settle(srv)
    c.post(f"/api/questions/{q['id']}/rescan")
    assert evidence(c, q["id"])["counts"]["total"] == 2
    assert c.post(f"/api/questions/{q['id']}/rescan").json()["added"] == 0  # today's quota is used
    ev = evidence(c, q["id"])
    assert [i["score"] for i in ev["items"]] == sorted((i["score"] for i in ev["items"]), reverse=True)  # best first
    # the next day, more arrive
    p = srv.store.root / "state" / "questions" / f"{q['id']}.json"
    data = json.loads(p.read_text())
    yesterday = (dt.datetime.now().astimezone() - dt.timedelta(days=1)).isoformat(timespec="seconds")
    for i in data["items"]:
        i["found_at"] = yesterday
    p.write_text(json.dumps(data))
    assert c.post(f"/api/questions/{q['id']}/rescan").json()["added"] == 2


def test_triggers_new_paper_note_and_card(server, monkeypatch):
    srv, c = server
    from app import questions as Q
    for k in ("tokamak", "sheath"):
        add_paper(srv, c, k)
    q = add_question(c)
    settle(srv)
    assert evidence(c, q["id"])["items"] == []  # nothing relevant yet
    # a new paper: found in the background, no rescan asked
    bid = add_paper(srv, c, "breathing")
    settle(srv)
    ev = evidence(c, q["id"])
    assert [(i["ref"]["note"], i["ref"].get("page")) for i in ev["items"]] == [(bid, 3)]
    assert c.get("/api/questions").json()["total_new"] == 1
    # a new note
    nid = add_note(c, "idea_te")
    settle(srv)
    assert any(i["ref"]["note"] == nid and i["kind"] == "note" for i in evidence(c, q["id"])["items"])
    # a new idea card (app/cards.py): the cards-changed event wakes the matcher
    cards = [{"id": "c_0000beef", "paper": bid, "kind": "claim", "status": "confirmed",
              "text": "Breathing-mode frequency scales with the square root of ion and neutral velocity over the ionization length.",
              "locator": {"page": 3, "quote": "f = sqrt(V_i V_n)"}}]

    class FakeCards:
        @staticmethod
        def search(text, k=8):
            return [dict(x, score=0.9) for x in cards]

        @staticmethod
        def for_paper(pid, confirmed_only=True):
            return [dict(x) for x in cards if x["paper"] == pid]

        @staticmethod
        def signature():
            return str(len(cards))
    monkeypatch.setattr(Q, "_cards", lambda: FakeCards)
    srv.event_bus.publish({"type": "cards-changed", "ids": [bid]})
    settle(srv)
    card = [i for i in evidence(c, q["id"])["items"] if i["kind"] == "card"]
    assert len(card) == 1 and card[0]["ref"] == {"card": "c_0000beef", "note": bid, "page": 3}
    assert card[0]["source"]["id"] == bid and card[0]["where"] == "p. 3"
    # editing the question looks again; a parked question stops collecting
    c.put(f"/api/notes/{q['id']}", json={"question_status": "parked"})
    add_paper(srv, c, "krcrm")
    settle(srv)
    assert not any(i["ref"]["note"] != bid and i["kind"] == "passage" for i in evidence(c, q["id"])["items"])
    c.put(f"/api/notes/{q['id']}", json={"question_status": "open"})
    settle(srv)
    assert any(i["kind"] == "passage" and i["source"]["title"] == FX["papers"]["krcrm"]["title"]
               for i in evidence(c, q["id"])["items"])


def test_seen_counts_badge_list_graph_and_delete(server):
    srv, c = server
    ids = library(srv, c)
    q = add_question(c)
    c.post(f"/api/questions/{q['id']}/rescan")
    ev = evidence(c, q["id"])
    assert ev["counts"]["new"] == 4 and ev["counts"]["untriaged"] == 4
    lst = c.get("/api/questions").json()
    assert lst["total_new"] == 4 and lst["questions"][0]["id"] == q["id"] and lst["questions"][0]["new_evidence"] == 4
    items = {i["ref"]["note"]: i for i in ev["items"]}
    c.put(f"/api/questions/{q['id']}/evidence/{items[ids['breathing']]['id']}", json={"verdict": "supports", "user_note": "the formula"})
    c.put(f"/api/questions/{q['id']}/evidence/{items[ids['krcrm']]['id']}", json={"verdict": "related"})
    c.put(f"/api/questions/{q['id']}/evidence/{items[ids['oes']]['id']}", json={"verdict": "against"})
    ev = evidence(c, q["id"])
    assert ev["counts"] == {"new": 1, "untriaged": 1, "supports": 1, "against": 1, "related": 1, "dismissed": 0, "total": 4}
    assert next(i for i in ev["items"] if i["verdict"] == "supports")["user_note"] == "the formula"
    assert [g["verdict"] for g in ev["groups"]] == ["new", "supports", "against", "related", "dismissed"]
    # graph: the question node and links to the papers of kept evidence (the lead draws them)
    g = c.get("/api/graph").json()
    assert next(n for n in g["nodes"] if n["id"] == q["id"])["new_evidence"] == 1
    assert sorted((l["paper"], l["verdict"]) for l in g["question_links"] if l["q"] == q["id"]) == sorted(
        [(ids["breathing"], "supports"), (ids["krcrm"], "related"), (ids["oes"], "against")])
    # seen: the badge clears, untriaged stays; looking isn't an edit
    before = c.get(f"/api/notes/{q['id']}").json()["updated"]
    r = c.post(f"/api/questions/{q['id']}/seen").json()
    assert r["counts"]["new"] == 0 and r["counts"]["untriaged"] == 1 and r["last_seen"]
    assert c.get(f"/api/notes/{q['id']}").json()["updated"] == before
    assert c.get("/api/questions").json()["total_new"] == 0
    assert c.get("/api/graph").json()["nodes"] and next(n for n in c.get("/api/graph").json()["nodes"] if n["id"] == q["id"])["new_evidence"] == 0
    # deleting the question moves its evidence to the trash with it
    assert c.delete(f"/api/notes/{q['id']}").status_code == 200
    assert not (srv.store.root / "state" / "questions" / f"{q['id']}.json").exists()
    assert list(srv.store.trash_dir.glob(f"question-evidence-{q['id']}-*.json"))
    assert c.get("/api/questions").json()["questions"] == []


def test_questions_are_not_sources_for_each_other_or_the_passage_index(server):
    srv, c = server
    library(srv, c, papers=["breathing"], notes=())
    q1 = add_question(c)
    q2 = c.post("/api/notes", json={"type": "question", "title": "Is the breathing-mode frequency set by the Kr CRM outputs?",
                                    "body": Q_BODY}).json()
    c.post(f"/api/questions/{q1['id']}/rescan")
    assert all(i["ref"]["note"] != q2["id"] for i in evidence(c, q1["id"])["items"])
    hits = srv._chunk_index.search("breathing-mode frequency Kr CRM outputs", k=50, per_note=10,
                                   notes=srv.store.all())
    assert hits and all(h["id"] not in (q1["id"], q2["id"]) for h in hits)
    # a caller that hides notes (chat hides private ones) filters results; the index keeps everything, so it
    # doesn't drop and re-embed passages each time the matcher and chat take turns
    note = add_note(c, "idea_te")
    srv._chunk_index.search("breathing", notes=srv.store.all())
    before = dict(srv._chunk_index._notes)
    only = [n for n in srv.store.all() if n["id"] != note]
    hits = srv._chunk_index.search("electron temperature breathing", k=50, notes=only)
    assert hits and note not in {h["id"] for h in hits}
    assert note in srv._chunk_index._notes and srv._chunk_index._notes.keys() == before.keys()


# ---------------------------------------------------------------- privacy
from app.agents.base import Backend as _Backend, Detection as _Detection  # noqa: E402


class Recorder(_Backend):
    """A fake AI backend that records every prompt and answers with a canned reply (no network, no CLI)."""

    def __init__(self, id="qfake", replies=()):
        self.id, self.name, self.kind, self.home, self.cost_note = id, "Recorder", "cli", "https://example.invalid", ""
        self.prompts: list[str] = []
        self.replies = list(replies)

    def detect(self):
        return _Detection(True, version="1", path="/fake", signed_in=True)

    def ask_stream(self, prompt, *, system="", timeout=180):
        self.prompts.append(prompt + "\n" + (system or ""))
        reply = self.replies.pop(0) if self.replies else '{"action": "answer", "text": "An answer."}'
        yield reply


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


def test_privacy_question_never_reaches_an_agent(server):
    srv, c = server
    from app import agents
    ids = library(srv, c)
    c.put(f"/api/notes/{ids['breathing']}", json={"summary": "Breathing mode: an ionization instability at 10-30 kHz."})
    q = add_question(c)
    c.put(f"/api/notes/{q['id']}", json={"answer": Q_ANSWER, "tags": ["thesis"]})
    c.post(f"/api/questions/{q['id']}/rescan")
    ev = evidence(c, q["id"])
    for i in ev["items"][:2]:
        c.put(f"/api/questions/{q['id']}/evidence/{i['id']}", json={"verdict": "supports", "user_note": "quokka margin note"})
    fb = Recorder(replies=[
        "chat answer", "streamed chat", "critique", "streamed critique", "explained", "- Nothing important missing.",
        '{"action":"search","queries":["breathing mode frequency Kr CRM outputs", "zanzibar quokka"]}',
        '{"action":"read","id":"%s"}' % ids["breathing"],
        '{"action":"answer","text":"Done [[%s]]."}' % ids["breathing"]] + ["more"] * 20)
    agents.register(fb)
    agents.set_active(fb.id)
    try:
        # typing the private question itself into chat is refused by the privacy backstop: nothing is sent
        r = c.post("/api/chat", json={"question": f"{Q_TITLE} {Q_BODY}", "history": []})
        assert r.status_code == 403 and "private" in r.json()["detail"] and fb.prompts == []
        # every AI endpoint, asked about what the question is about (so retrieval would find it if it could)
        ask = "breathing mode oscillation frequency, electron temperature from Kr CRM line ratios, zanzibar quokka"
        assert c.post("/api/chat", json={"question": ask, "history": []}).status_code == 200
        assert _sse(c.post("/api/chat/stream", json={"question": ask, "history": []}).text)[-1][0] == "done"
        assert c.post(f"/api/notes/{ids['breathing']}/critique").status_code == 200
        assert _sse(c.post(f"/api/notes/{ids['breathing']}/critique/stream").text)[-1][0] == "done"
        assert c.post("/api/explain", json={"a": ids["breathing"], "b": ids["krcrm"]}).status_code == 200
        r = c.get("/api/review/queue").json()
        item = next((x for x in (r.get("items") or r.get("queue") or []) if isinstance(x, dict)), None)
        if item:
            c.post(f"/api/review/{item['id']}/check", json={"recalled_text": "ionization instability"})
        evs = _sse(c.post("/api/research/stream", json={"question": ask}).text)
        assert evs[-1][0] in ("done", "error")
        # the question itself: explain / critique refuse, the AI summary is unavailable while it is private
        assert c.post("/api/explain", json={"a": q["id"], "b": ids["breathing"]}).status_code in (400, 403)
        assert c.post(f"/api/notes/{q['id']}/critique").status_code in (400, 403)
        r = c.post(f"/api/questions/{q['id']}/summarize")
        assert r.status_code == 403 and "private" in r.json()["detail"]
        assert ev["ai"]["available"] is False and "private" in ev["ai"]["reason"]
        assert fb.prompts, "the fake agent was never called: the test isn't exercising anything"
        sent = "\n".join(fb.prompts)
        for secret in (Q_TITLE, Q_BODY, Q_ANSWER, "quokka margin note", q["id"], "Zanzibar heuristic"):
            assert secret not in sent, secret
        # the audit log has no question ids; neither has the memory map that goes to Research
        log = srv.store.root / "state" / "ai_log.jsonl"
        if log.exists():
            assert q["id"] not in log.read_text()
        core = srv.store.root / "memory" / "core.md"
        if core.exists():
            assert q["id"] not in core.read_text() and Q_TITLE not in core.read_text()
        # once the user marks the question shareable, the summary may use the agent (the check is privacy's)
        c.post(f"/api/notes/{q['id']}/privacy", json={"private": False})
        fb.replies = ["Two pieces support it."]
        r = c.post(f"/api/questions/{q['id']}/summarize")
        assert r.status_code == 200 and r.json()["text"] == "Two pieces support it.", r.text
        assert Q_TITLE in fb.prompts[-1]
        assert FX["notes"]["idea_te"]["body"][:60] not in fb.prompts[-1]  # evidence from a private note stays out
    finally:
        agents.unregister(fb.id)


def test_matching_never_calls_an_agent(server, monkeypatch):
    srv, c = server
    from app import agents

    def boom(*a, **k):
        raise AssertionError("an AI agent was called while matching evidence")
    monkeypatch.setattr(agents, "ask_parts", boom)
    monkeypatch.setattr(agents, "stream_parts", boom)
    monkeypatch.setattr(agents, "active", boom)
    library(srv, c)
    q = add_question(c)
    settle(srv)
    assert c.post(f"/api/questions/{q['id']}/rescan").status_code == 200
    assert evidence(c, q["id"])["counts"]["total"] == 4
    assert matcher(srv).last_error == ""
