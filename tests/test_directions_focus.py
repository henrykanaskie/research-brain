"""Research directions (app/directions.py) under the same controls as every AI feature, and the viewer's
Focus (app/focus.py), which is local only."""
from __future__ import annotations

from test_questions import FX, Q_ANSWER, Q_TITLE, Recorder, add_question, evidence, library, server  # noqa: F401


def _keep_two(c, q):
    c.post(f"/api/questions/{q['id']}/rescan")
    ev = evidence(c, q["id"])
    kept = ev["items"][:3]
    for i in kept:
        c.put(f"/api/questions/{q['id']}/evidence/{i['id']}", json={"verdict": "supports", "user_note": "my margin note"})
    return kept


def test_directions_preview_controls_and_safeguards(server):
    srv, c = server
    from app import agents
    ids = library(srv, c)
    q = add_question(c)
    c.put(f"/api/notes/{q['id']}", json={"answer": Q_ANSWER})
    kept = _keep_two(c, q)
    fb = Recorder(replies=["## Gaps\n- None [[%s]] p. 1" % ids["breathing"]])
    agents.register(fb)
    agents.set_active(fb.id)
    try:
        # a private question (the default): refused, nothing sent
        r = c.post(f"/api/questions/{q['id']}/directions", json={"dry": True})
        assert r.status_code == 403 and "private" in r.json()["detail"]
        c.put(f"/api/questions/{q['id']}/reach", json={"reach": "library"})
        pv = c.post(f"/api/questions/{q['id']}/directions", json={"dry": True}).json()
        assert pv["used"] and pv["tokens"] > 0 and pv["has_answer"] and not pv["answer_included"]
        private = [s for s in pv["left_out"] if s["why"] == "private"]
        assert fb.prompts == []  # the preview sends nothing
        # untick one source: it is left out, and says why
        drop = pv["used"][0]["id"]
        pv2 = c.post(f"/api/questions/{q['id']}/directions", json={"dry": True, "exclude": [drop]}).json()
        assert drop not in [s["id"] for s in pv2["used"]] and any(s["id"] == drop and s["why"] == "you unticked it" for s in pv2["left_out"])
        r = c.post(f"/api/questions/{q['id']}/directions", json={"exclude": [drop], "focus": "lab experiments"})
        assert r.status_code == 200, r.text
        sent = fb.prompts[-1]
        assert Q_TITLE in sent and "lab experiments" in sent and "## Gaps" in sent
        assert Q_ANSWER not in sent  # the answer goes only when ticked
        assert FX["notes"]["idea_te"]["body"][:60] not in sent  # a private note's evidence never goes
        assert r.json()["run"]["text"].startswith("## Gaps") and len(r.json()["runs"]) == 1
        # ticking the answer sends it
        fb.replies = ["ok"]
        c.post(f"/api/questions/{q['id']}/directions", json={"include_answer": True})
        assert Q_ANSWER in fb.prompts[-1]
        assert len(c.post(f"/api/questions/{q['id']}/directions", json={"dry": True}).json()["runs"]) == 2
        # it is logged like every AI call
        log = (srv.store.root / "state" / "ai_log.jsonl").read_text(encoding="utf-8")
        assert "research directions" in log
        assert private is not None
    finally:
        agents.unregister(fb.id)


def test_topic_directions_leave_private_notes_out(server):
    srv, c = server
    from app import agents, topics as T
    ids = library(srv, c)
    c.put(f"/api/notes/{ids['breathing']}", json={"summary": "Breathing mode: an ionization instability at 10-30 kHz."})
    secret = c.post("/api/notes", json={"type": "note", "title": "My unpublished idea", "body": "Zanzibar quokka trick for thrusters"}).json()
    t = T.create_topic(srv.store, "Oscillations", members=[ids["breathing"], secret["id"]])
    fb = Recorder(replies=["## Gaps\n- x"])
    agents.register(fb)
    agents.set_active(fb.id)
    try:
        pv = c.post(f"/api/topics/{t['slug']}/directions", json={"dry": True}).json()
        assert [s["id"] for s in pv["used"]] == [ids["breathing"]]
        assert any(s["id"] == secret["id"] and s["why"] == "private" for s in pv["left_out"])
        r = c.post(f"/api/topics/{t['slug']}/directions", json={})
        assert r.status_code == 200 and "Zanzibar" not in fb.prompts[-1] and "Oscillations" in fb.prompts[-1]
        assert c.post("/api/topics/nope/directions", json={"dry": True}).status_code == 404
    finally:
        agents.unregister(fb.id)


def test_focus_finds_the_passages_for_a_question_without_ai(server, monkeypatch):
    srv, c = server
    from app import agents
    ids = library(srv, c)
    q = add_question(c)  # private: Focus still works, it's local
    called = []
    monkeypatch.setattr(agents, "ask_parts", lambda *a, **k: called.append(1))
    r = c.post(f"/api/notes/{ids['breathing']}/focus", json={"question": q["id"]}).json()
    assert r["passages"] and r["label"] == q["title"] and r["pdf"] == ids["breathing"]
    top = r["passages"][0]
    assert top["page"] and top["quote"] and top["quote"] in top["text"].replace("\n", " ") or len(top["quote"]) <= 320
    assert called == []
    r = c.post(f"/api/notes/{ids['breathing']}/focus", json={"text": "quantum chromodynamics lattice gluon"}).json()
    assert r["passages"] == []
    assert c.post(f"/api/notes/{ids['breathing']}/focus", json={}).status_code == 400


def test_writing_help_sends_only_what_you_tick(server):
    srv, c = server
    from app import agents
    ids = library(srv, c)
    secret = c.post("/api/notes", json={"type": "note", "title": "Lab secret", "body": "Zanzibar quokka trick for thrusters"}).json()
    draft = c.post("/api/notes", json={"type": "note", "title": "Chapter 2 draft",
                                       "body": f"The breathing mode [[{ids['breathing']}]] and our trick [[{secret['id']}]]. My private sentence about walrus calibration."}).json()
    fb = Recorder(replies=["A paragraph [[%s]] p. 1." % ids["breathing"]])
    agents.register(fb)
    agents.set_active(fb.id)
    try:
        pv = c.post(f"/api/notes/{draft['id']}/write", json={"dry": True, "request": "Turn this into a paragraph"}).json()
        by = {s["id"]: s for s in pv["sources"]}
        assert by[ids["breathing"]]["allowed"] and not by[secret["id"]]["allowed"]
        assert pv["draft"]["allowed"] is False and "private" in pv["draft"]["why"]  # notes are private by default
        assert fb.prompts == []
        # ticking the private draft doesn't send it: it stays private until you change its lock
        r = c.post(f"/api/notes/{draft['id']}/write", json={"request": "Turn this into a paragraph", "include_draft": True,
                                                            "sources": [ids["breathing"], secret["id"]]})
        assert r.status_code == 200, r.text
        sent = fb.prompts[-1]
        assert "walrus" not in sent and "Zanzibar" not in sent and "Turn this into a paragraph" in sent
        assert r.json()["text"].startswith("A paragraph")
        # made shareable, and ticked: the draft goes
        c.post(f"/api/notes/{draft['id']}/privacy", json={"private": False})
        fb.replies = ["ok"]
        c.post(f"/api/notes/{draft['id']}/write", json={"request": "Tighten", "include_draft": True, "sources": [ids["breathing"]]})
        assert "walrus" in fb.prompts[-1]
        # not ticked: it doesn't
        fb.replies = ["ok"]
        c.post(f"/api/notes/{draft['id']}/write", json={"request": "Tighten", "include_draft": False, "sources": [ids["breathing"]]})
        assert "walrus" not in fb.prompts[-1]
        assert "writing help" in (srv.store.root / "state" / "ai_log.jsonl").read_text(encoding="utf-8")
    finally:
        agents.unregister(fb.id)
