"""Open questions (app/questions.py, app/questions_api.py): local evidence matching on a small realistic plasma
corpus with TF-IDF, thresholds, dedupe, dismissals that never come back, the daily cap, the triggers (new paper,
new note, new idea card), the API, and privacy: a question never reaches an AI agent. Also the question's reach
(private / library / scout / agent) and the scout that searches published papers (app/scout.py, app/agent_scout.py).

No network: the server runs with RB_EMBEDDER=tfidf and a scripted fake AI backend.
Run:  python3 -m pytest -q tests/test_questions.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rb_app import load_server  # noqa: E402

FX = json.loads((ROOT / "tests" / "fixtures" / "questions" / "plasma_corpus.json").read_text(encoding="utf-8"))

Q_TITLE = FX["questions"]["breathing_crm"]["title"]
Q_BODY = FX["questions"]["breathing_crm"]["body"]
Q_ANSWER = "Zanzibar heuristic: the quokka frequency ratio tracks electron temperature cubed over the channel length."


@pytest.fixture()
def server(tmp_path, monkeypatch):
    srv = load_server(monkeypatch, tmp_path, fake_claude=False, RB_DATA_DIR=tmp_path / "data", RB_AGENT=None,
                      RB_MEMORY_WATCH="0", RB_WATCH="0", RB_CITES_ON_IMPORT="0", RB_QUESTIONS_DEBOUNCE="0.05")
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
    raw = srv.store.note_path(q["id"]).read_text(encoding="utf-8")
    assert "type: question" in raw and "question_status: parked" in raw and "answer:" in raw
    # other notes don't grow the question properties
    n = c.post("/api/notes", json={"type": "note", "title": "Plain"}).json()
    raw = srv.store.note_path(n["id"]).read_text(encoding="utf-8")
    assert "question_status" not in raw and "answer" not in raw and "last_seen" not in raw
    assert c.put(f"/api/notes/{n['id']}", json={"question_status": "open"}).json()["question_status"] == ""
    # the answer's [[links]] are wikilinks (unresolved here)
    assert "Some paper" in c.get(f"/api/notes/{q['id']}").json()["unresolved"]
    # it shows up in search and in the graph as a question node
    assert any(h["id"] == q["id"] for h in c.get("/api/search", params={"q": "breathing"}).json())
    node = next(x for x in c.get("/api/graph").json()["nodes"] if x["id"] == q["id"])
    assert node["type"] == "question" and node["new_evidence"] == 0


# ---------------------------------------------------------------- matching
def test_matching_finds_the_right_passages_and_never_calls_an_agent(server, monkeypatch):
    """Rescans find exactly the relevant passages and note for each question, with snippets, page and source;
    neither the rescan nor the background matcher ever calls an AI agent."""
    srv, c = server
    from app import agents

    def boom(*a, **k):
        raise AssertionError("an AI agent was called while matching evidence")
    monkeypatch.setattr(agents, "ask_parts", boom)
    monkeypatch.setattr(agents, "stream_parts", boom)
    monkeypatch.setattr(agents, "active", boom)
    ids = library(srv, c)
    q1 = add_question(c, "breathing_crm")
    q2 = add_question(c, "shielding")
    assert c.post(f"/api/questions/{q1['id']}/rescan").status_code == 200
    assert c.post(f"/api/questions/{q2['id']}/rescan").status_code == 200
    assert evidence(c, q1["id"])["counts"]["total"] == 4
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
    settle(srv)
    assert matcher(srv).last_error == ""


def test_thresholds_separate_relevant_from_unrelated_and_are_settings(server):
    """The TF-IDF threshold sits with a margin between relevant passages and unrelated papers; sentence
    transformers use the dense threshold; both are settings, validated by the API."""
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
    # sentence transformers use the dense threshold, which is a setting too
    assert Q.embedder_family("sentence-transformers:all-MiniLM-L6-v2") == "dense"
    assert Q.threshold_for("sentence-transformers:all-MiniLM-L6-v2") == Q.THRESHOLDS["dense"]
    assert Q.threshold_for("tfidf") == Q.THRESHOLDS["tfidf"]
    Q.save_settings({"threshold_dense": 0.5})
    assert Q.threshold_for("sentence-transformers:x") == 0.5
    # the threshold is a setting; raising it keeps everything out
    r = c.put("/api/questions/settings", json={"threshold_tfidf": 0.95})
    assert r.status_code == 200 and r.json()["threshold"] == 0.95 and r.json()["family"] == "tfidf"
    assert c.post(f"/api/questions/{q['id']}/rescan").json()["added"] == 0
    assert c.put("/api/questions/settings", json={"threshold_tfidf": 2}).status_code == 400
    assert c.put("/api/questions/settings", json={"daily_cap": 0}).status_code == 400
    s = c.get("/api/questions/settings").json()
    assert s["defaults"]["threshold_tfidf"] == Q.THRESHOLDS["tfidf"] and s["defaults"]["threshold_dense"] == Q.THRESHOLDS["dense"]


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
    tp.write_text(tp.read_text(encoding="utf-8").replace("The breathing mode is a large-amplitude", "The breathing mode is a strong, large-amplitude"))
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
    data = json.loads(p.read_text(encoding="utf-8"))
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
            assert q["id"] not in log.read_text(encoding="utf-8")
        core = srv.store.root / "memory" / "core.md"
        if core.exists():
            assert q["id"] not in core.read_text(encoding="utf-8") and Q_TITLE not in core.read_text(encoding="utf-8")
        # once the user marks the question shareable, the summary may use the agent (the check is privacy's)
        c.post(f"/api/notes/{q['id']}/privacy", json={"private": False})
        fb.replies = ["Two pieces support it."]
        r = c.post(f"/api/questions/{q['id']}/summarize")
        assert r.status_code == 200 and r.json()["text"] == "Two pieces support it.", r.text
        assert Q_TITLE in fb.prompts[-1]
        assert FX["notes"]["idea_te"]["body"][:60] not in fb.prompts[-1]  # evidence from a private note stays out
    finally:
        agents.unregister(fb.id)


# ---------------------------------------------------------------- reach and the scout (app/scout.py)
def paper(title, abstract, doi="", arxiv="", year=2025):
    return {"title": title, "abstract": abstract, "year": year, "date": f"{year}-03-01", "authors": ["A. Researcher"],
            "doi": doi, "arxiv": arxiv, "venue": "J. Test", "url": f"https://doi.org/{doi}" if doi else "", "access": "closed",
            "found_via": "Fake"}


def fake_sources(found, calls):
    def search(query, since):
        calls.append((query, since))
        return list(found)
    return {"fake": search}


def test_reach_defaults_private_scout_errors_are_kept_and_privacy_wins(server):
    """A question starts private; set to scout, a failing source is reported and the searched-to date stays
    empty; the privacy lock still wins over the reach, and an unknown reach is refused."""
    srv, c = server
    from app import questions as Q, scout
    q = add_question(c)
    assert evidence(c, q["id"])["question"]["reach"] == "private"
    r = c.put(f"/api/questions/{q['id']}/reach", json={"reach": "scout"})
    assert r.status_code == 200 and r.json()["ai"]["available"]
    assert evidence(c, q["id"])["question"]["reach"] == "scout"

    def down(query, since):
        raise ConnectionError("offline")
    r = Q.scout_scan(matcher(srv).ctx, q["id"], sources={"openalex": down})
    assert "openalex: ConnectionError" in r["error"]
    assert Q.load(srv.store, q["id"])["scout"]["searched_to"] == ""  # the next try looks back the full window
    # the lock in the header (privacy API) still wins: a private question never scouts
    c.post(f"/api/notes/{q['id']}/privacy", json={"private": True})
    assert scout.reach(srv.store.get(q["id"])) == "private"
    assert c.post(f"/api/questions/{q['id']}/scout").status_code == 400
    assert c.put(f"/api/questions/{q['id']}/reach", json={"reach": "everyone"}).status_code == 400


def test_scout_adds_close_new_papers_and_skips_the_library(server):
    srv, c = server
    from app import questions as Q
    ids = library(srv, c)
    lib_title = srv.store.get(ids["breathing"])["title"]
    q = add_question(c)
    c.put(f"/api/questions/{q['id']}/reach", json={"reach": "scout"})
    title, body = FX["questions"]["breathing_crm"]["title"], FX["questions"]["breathing_crm"]["body"]
    found = [
        paper("Breathing mode oscillations and electron temperature from krypton line ratios",
              f"{title} {body} We measure breathing mode frequency against electron temperature.", doi="10.1/close"),
        paper(lib_title, "already in the library", doi="10.1/dup"),
        paper("Sourdough fermentation kinetics", "Yeast and lactobacilli in bread dough at room temperature.", doi="10.1/bread"),
    ]
    calls = []
    r = Q.scout_scan(matcher(srv).ctx, q["id"], sources=fake_sources(found, calls))
    titles = [i["title"] for i in r["added"]]
    assert titles == [found[0]["title"]] and calls[0][1] < dt.date.today() - dt.timedelta(days=365)
    ev = evidence(c, q["id"])
    ext = [i for i in ev["items"] if i["kind"] == "external"]
    assert len(ext) == 1 and ext[0]["source"]["external"] and ext[0]["href"] == "https://doi.org/10.1/close"
    assert ev["scout"]["last"] and ev["scout"]["query"]
    # a second scout looks only at what's new (from the last search, with a week's overlap) and adds nothing twice
    r2 = Q.scout_scan(matcher(srv).ctx, q["id"], sources=fake_sources(found, calls))
    assert r2["added"] == [] and calls[1][1] >= dt.date.today() - dt.timedelta(days=8)
    # dismissed stays dismissed
    c.put(f"/api/questions/{q['id']}/evidence/{ext[0]['id']}", json={"verdict": "dismissed"})
    data = Q.load(srv.store, q["id"])
    data["scout"]["searched_to"] = ""
    Q._save(srv.store, q["id"], data)
    assert Q.scout_scan(matcher(srv).ctx, q["id"], sources=fake_sources(found, calls))["added"] == []


def test_private_and_library_questions_never_leave_the_computer(server, monkeypatch):
    srv, c = server
    from app import questions as Q, scout
    monkeypatch.setenv("RB_METADATA", "1")
    calls = []
    monkeypatch.setattr(scout, "SOURCES", fake_sources([], calls))
    library(srv, c)
    p = add_question(c)
    lib = add_question(c, "breathing_crm")
    c.put(f"/api/questions/{lib['id']}/reach", json={"reach": "library"})
    matcher(srv).run_all(force={p["id"], lib["id"]})
    assert calls == []
    assert Q.scout_scan(matcher(srv).ctx, p["id"])["error"].startswith("This question doesn't scout")
    # scout is searched by the background matcher once, then not again for a day
    c.put(f"/api/questions/{lib['id']}/reach", json={"reach": "scout"})
    settle(srv)
    matcher(srv).run_all()
    matcher(srv).run_all()
    assert len(calls) == 1


def test_scout_query_and_openalex_and_arxiv_parsing(monkeypatch):
    """The scout's query is the question's words, never its answer, links or LaTeX; OpenAlex and arXiv
    replies are parsed into papers (old arXiv entries dropped)."""
    from app import scout
    from app.scout import query_for
    q = {"title": "Does the breathing mode frequency track $T_e$ in Hall thrusters?",
         "body": "Using krypton CRM line ratios. Electron temperature from CRM; see [[some-note]] https://x.y",
         "answer": "SECRET answer text"}
    out = query_for(q)
    assert "breathing" in out and "krypton" in out.lower() and "SECRET" not in out and "some-note" not in out
    assert "$" not in out and "http" not in out and len(out) <= 240

    class R:
        def __init__(self, js=None, content=b""):
            self._js, self.content, self.status_code = js, content, 200
        def raise_for_status(self):
            pass
        def json(self):
            return self._js
    oa = {"results": [{"id": "https://openalex.org/W1", "display_name": "Breathing mode", "publication_year": 2025,
                       "publication_date": "2025-02-01", "authorships": [{"author": {"display_name": "B. C"}}],
                       "doi": "https://doi.org/10.1/x", "abstract_inverted_index": {"Hall": [0], "thrusters": [1], "oscillate": [2]},
                       "primary_location": {"source": {"display_name": "Phys. Plasmas"}}, "open_access": {"oa_status": "closed"}}]}
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2501.01234v2</id>
      <published>2025-01-05T00:00:00Z</published><title>Krypton  CRM</title><summary>Line ratios.</summary>
      <author><name>D. E</name></author></entry>
      <entry><id>http://arxiv.org/abs/1901.00001v1</id><published>2019-01-01T00:00:00Z</published><title>Old</title><summary>x</summary></entry></feed>"""
    monkeypatch.setattr(scout.requests, "get", lambda url, **kw: R(js=oa) if "openalex" in url else R(content=atom))
    a = scout.search_openalex("breathing mode", dt.date(2024, 1, 1))[0]
    assert a["abstract"] == "Hall thrusters oscillate" and a["doi"] == "10.1/x" and a["access"] == "closed" and a["venue"] == "Phys. Plasmas"
    x = scout.search_arxiv("krypton crm", dt.date(2024, 1, 1))
    assert len(x) == 1 and x[0]["arxiv"] == "2501.01234" and x[0]["title"] == "Krypton CRM" and x[0]["access"] == "open"


# ---------------------------------------------------------------- agent search (reach "agent")
def test_agent_search_proposes_papers_with_reasons(server):
    srv, c = server
    from app import agents, questions as Q
    ids = library(srv, c)
    lib_title = srv.store.get(ids["breathing"])["title"]
    q = add_question(c)
    c.put(f"/api/notes/{q['id']}", json={"answer": "SECRET: my unpublished derivation"})
    r = c.put(f"/api/questions/{q['id']}/reach", json={"reach": "agent"})
    assert r.status_code == 200 and r.json()["ai"]["available"]
    found = [paper("Breathing-mode frequency scaling with electron temperature", "We relate f_b to T_e via line ratios.", doi="10.1/a"),
             paper(lib_title, "already in the library", doi="10.1/lib"),
             paper("Krypton collisional-radiative modeling", "A Kr CRM for Hall thrusters.", arxiv="2501.00001")]
    calls = []
    cited = [paper("A cited classic on ionization oscillations", "Predator-prey model of the breathing mode.", doi="10.1/c")]
    fb = Recorder(replies=[
        '{"action": "search", "queries": ["breathing mode frequency electron temperature", "krypton CRM Hall thruster"]}',
        '{"action": "read", "paper": "P1"}',
        '{"action": "propose", "paper": "P1", "stance": "supports", "why": "Directly fits $f_b \\\\propto T_e$."}',
        '{"action": "propose", "paper": "P3", "stance": "related", "why": "not read yet"}',
        '{"action": "citations", "paper": "P1", "direction": "references"}',
        '{"action": "read", "paper": "P4"}',
        '{"action": "propose", "paper": "P4", "stance": "related", "why": "The classic model."}',
    ] + ['{"action": "done", "note": "Searched two phrasings and one reference list."}'])
    out = Q.agent_scan(matcher(srv).ctx, q["id"], backend=fb, sources=fake_sources(found, calls),
                       cites=lambda p, d: cited)
    assert out["error"] == ""
    titles = [i["title"] for i in out["added"]]
    assert titles == [found[0]["title"], cited[0]["title"]]  # P3 was refused: never read
    first = out["added"][0]
    assert first["ai"] == {"stance": "supports", "why": "Directly fits $f_b \\propto T_e$."} and first["verdict"] == "new"
    sent = "\n".join(fb.prompts)
    assert "SECRET" not in sent and q["title"] in sent
    assert "read P3's abstract before proposing it" in sent  # the refusal went back to the agent
    ev = evidence(c, q["id"])
    assert ev["agent"]["added"] == 2 and ev["agent"]["tokens"] > 0 and ev["agent"]["turns"] == 7  # 5 + 2 at Balanced
    row = next(i for i in ev["items"] if i["kind"] == "external" and i.get("ai"))
    assert row["source"]["found_via"] == "AI search"
    # the next run is told what was already found, and dedupes it anyway
    fb.replies = ['{"action": "search", "queries": ["breathing mode"]}', '{"action": "read", "paper": "P1"}',
                  '{"action": "propose", "paper": "P1", "stance": "supports", "why": "again"}', '{"action": "done"}']
    again = Q.agent_scan(matcher(srv).ctx, q["id"], backend=fb, sources=fake_sources(found, calls), cites=lambda p, d: [])
    second = fb.prompts[7]
    assert again["added"] == [] and "ALREADY FOUND" in second and found[0]["title"] in second.split("ALREADY FOUND")[1]


def test_agent_search_needs_agent_reach_and_stops_on_turns(server):
    srv, c = server
    from app import questions as Q, agent_scout
    q = add_question(c)
    assert "Agent search is off" in Q.agent_scan(matcher(srv).ctx, q["id"], backend=Recorder())["error"]
    assert c.post(f"/api/questions/{q['id']}/agent-search").status_code == 400
    c.put(f"/api/questions/{q['id']}/reach", json={"reach": "agent"})
    # the lock in the header still wins
    c.post(f"/api/notes/{q['id']}/privacy", json={"private": True})
    assert "Agent search is off" in Q.agent_scan(matcher(srv).ctx, q["id"], backend=Recorder())["error"]
    c.post(f"/api/notes/{q['id']}/privacy", json={"private": False})
    c.put(f"/api/questions/{q['id']}/reach", json={"reach": "agent"})
    fb = Recorder(replies=['{"action": "search", "queries": ["x"]}'] * 50)
    r = agent_scout.run(fb, {"title": "t", "body": ""}, since=dt.date(2020, 1, 1), sources=fake_sources([], []), max_turns=3)
    assert r["turns"] == 3 and r["stopped"] == "max_turns" and len(fb.prompts) == 3
    assert "last" in fb.prompts[-1]  # the final turn says so
    # a failing agent is recorded on the question (and retried after a day, not a week)
    class Broken(Recorder):
        def ask_stream(self, prompt, *, system="", timeout=180):
            raise RuntimeError("signed out")
            yield ""
    out = Q.agent_scan(matcher(srv).ctx, q["id"], backend=Broken(), sources=fake_sources([], []))
    assert "signed out" in out["error"]
    data = Q.load(srv.store, q["id"])
    assert data["agent"]["error"] and Q.agent_due(data) is False


def test_a_busy_evidence_file_is_waited_for_and_never_read_as_empty(server, monkeypatch):
    """On Windows a question's evidence file can't be opened for an instant while it's being replaced (or held
    by a scanner or sync client). A short hold is waited out; a long one refuses instead of reading as "no
    evidence", so nothing is saved over the evidence, and the page is told to try again."""
    srv, c = server
    Q, fsio = srv.questions_mod, srv.questions_mod.fsio
    q = c.post("/api/notes", json={"type": "question", "title": "Why do sheaths form at walls?"}).json()
    Q._save(srv.store, q["id"], {"items": [{"id": "e1", "key": "k1", "verdict": "new", "user_note": ""}]})
    path = Q._path(srv.store, q["id"])
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(fsio, "WINDOWS", True)
    monkeypatch.setattr(fsio.time, "sleep", lambda s: None)
    real = Path.read_text
    busy = {"n": 0}

    def flaky(self, *a, **k):
        if self == path and busy["n"] > 0:
            busy["n"] -= 1
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", flaky)

    busy["n"] = 3  # a short hold: waited out
    assert [i["id"] for i in Q.load(srv.store, q["id"])["items"]] == ["e1"]

    busy["n"] = 10_000  # held for good: refuses, and a triage click changes nothing on disk
    with pytest.raises(Q.Unreadable):
        Q.load(srv.store, q["id"])
    r = c.put(f"/api/questions/{q['id']}/evidence/e1", json={"verdict": "dismissed"})
    assert r.status_code == 503 and "try again" in r.json()["detail"]
    busy["n"] = 0
    assert path.read_text(encoding="utf-8") == before
    # lists and counts still show (the busy question as empty for the moment)
    busy["n"] = 10_000
    assert c.get("/api/questions").status_code == 200
    busy["n"] = 0
    assert [i["verdict"] for i in Q.load(srv.store, q["id"])["items"]] == ["new"]
