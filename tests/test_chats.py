"""Remembered conversations (app/chats.py) and asking about the papers in a topic or two.

Run:  .venv/bin/python -m pytest -q tests/test_chats.py
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_CITES_ON_IMPORT", "0")
    monkeypatch.setenv("RB_RECS_AUTO", "0")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    monkeypatch.setattr(srv.scholar, "lookup", lambda q: None)
    return srv


@pytest.fixture()
def client(server):
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def paper(client, title, abstract="", **kw):
    r = client.post("/api/notes", json={"type": "paper", "title": title, "abstract": abstract, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_conversation_is_remembered_and_older_turns_are_condensed(client, server):
    from app import chats
    msgs = []
    for i in range(30):
        msgs += [{"role": "user", "content": f"Question number {i} about sheaths?"},
                 {"role": "assistant", "content": f"Answer {i}. The sheath forms because ions accelerate. " + "x " * 50,
                  "sources": [{"id": "a", "title": "A", "score": 1}], "passages": [{"text": "big" * 1000}]}]
    r = client.put("/api/chats/library", json={"messages": msgs, "title": "Whole library"})
    assert r.status_code == 200
    d = client.get("/api/chats/library").json()
    assert len(d["messages"]) == chats.KEEP and d["messages"][0]["role"] == "user"
    assert d["messages"][-1]["content"].startswith("Answer 29")
    assert "passages" not in d["messages"][-1] and d["messages"][-1]["sources"] == [{"id": "a", "title": "A"}]
    assert "Question number 0" in d["summary"] and "→ A: Answer 0." in d["summary"]
    assert len(d["summary"]) <= chats.SUMMARY_CHARS
    # the list says what's there
    rows = client.get("/api/chats").json()
    assert rows[0]["key"] == "library" and rows[0]["summarised"] and rows[0]["count"] == chats.KEEP
    # the summary goes to the agent with the recent messages
    paper(client, "Sheath paper", "sheath ions")
    instruction, context, _ = server._chat_prompt(server.ChatIn(question="And now?", history=d["messages"][-4:], thread="library"))
    assert "Earlier in this conversation, condensed" in context and "Question number 0" in context
    # New conversation: forgotten
    client.put("/api/chats/library", json={"messages": []})
    assert client.get("/api/chats/library").json()["messages"] == []
    assert client.get("/api/chats").json() == []


def test_bad_conversation_names_are_refused(client):
    for key in ("../../etc", "Library", "paper-", "topics-a+b+c+d+e+f+g+h+i+j"):
        assert client.put(f"/api/chats/{key}", json={"messages": []}).status_code in (400, 404), key
    assert client.get("/api/chats/paper-..").status_code in (400, 404)


def test_space_is_capped(client, server, monkeypatch):
    from app import chats
    monkeypatch.setattr(chats, "MAX_THREADS", 3)
    for i in range(5):
        client.put(f"/api/chats/paper-p{i}", json={"messages": [{"role": "user", "content": f"q{i}"}]})
    keys = {t["key"] for t in client.get("/api/chats").json()}
    assert len(keys) == 3 and "paper-p4" in keys and "paper-p0" not in keys


def test_asking_about_topics_uses_only_their_papers(client, server):
    a = paper(client, "Sheath formation at a floating wall", "ion acceleration sheath presheath Bohm criterion")
    b = paper(client, "Secondary electron emission in Hall thrusters", "wall material secondary electron emission sheath")
    c = paper(client, "Transformers for language modelling", "attention tokens sheath")  # another topic
    client.put(f"/api/notes/{a}", json={"summary": "Ions reach the Bohm speed before the wall."})
    t1 = client.post("/api/topics", json={"name": "Sheaths", "members": [a]}).json()["slug"]
    t2 = client.post("/api/topics", json={"name": "Thruster walls", "members": [b]}).json()["slug"]
    client.post("/api/topics", json={"name": "Language models", "members": [c]})
    instruction, context, extra = server._chat_prompt(server.ChatIn(question="What do these say about the sheath?",
                                                                     topics=[t1, t2]))
    assert "“Sheaths”" in instruction and "“Thruster walls”" in instruction
    assert "PAPERS IN SCOPE (2 in the topics Sheaths, Thruster walls)" in context
    assert f"[[{a}]]" in context and f"[[{b}]]" in context and f"[[{c}]]" not in context
    assert "Ions reach the Bohm speed" in context  # your summary stands for the paper in the overview
    assert {s["id"] for s in extra["sources"]} <= {a, b}
    # through the API, too
    r = client.post("/api/chat", json={"question": "Compare them", "topics": [t1, t2], "thread": f"topics-{t1}+{t2}"})
    assert r.status_code == 200 and r.json()["topics"] == [t1, t2]
    # an empty topic says so
    t3 = client.post("/api/topics", json={"name": "Empty"}).json()["slug"]
    assert client.post("/api/chat", json={"question": "Anything?", "topics": [t3]}).status_code == 400


def _sse(text):
    import json as _j
    out = []
    for block in text.split("\n\n"):
        ev = next((l[7:] for l in block.splitlines() if l.startswith("event: ")), None)
        data = next((l[6:] for l in block.splitlines() if l.startswith("data: ")), None)
        if ev and data:
            out.append((ev, _j.loads(data)))
    return out


def test_general_chat_answers_without_the_library(client, server):
    paper(client, "Secret sheath paper", "a very particular phrase about sheaths")
    r = client.post("/api/general/stream", json={"question": "What is a Debye length?"})
    evs = _sse(r.text)
    done = [d for e, d in evs if e == "done"]
    assert done and done[0]["web"] is False
    assert "particular phrase" not in done[0]["answer"]  # no library content goes along


def test_web_search_is_off_until_you_turn_it_on(client, server):
    r = client.post("/api/general/stream", json={"question": "Latest on Hall thrusters?", "web": True})
    assert r.status_code == 403 and "Web search is off" in r.json()["detail"]
    s = client.put("/api/privacy/settings", json={"web_search": True}).json()
    assert s["settings"]["web_search"] is True
    r = client.post("/api/general/stream", json={"question": "Latest on Hall thrusters?", "web": True})
    if server._ai_agent() and server._ai_agent().id == "claude":
        assert r.status_code == 200 and any(e == "done" for e, _ in _sse(r.text))
    else:
        assert r.status_code == 400 and "Claude Code" in r.json()["detail"]
    client.put("/api/privacy/settings", json={"web_search": False})
    assert client.get("/api/privacy").json()["settings"]["web_search"] is False


def test_web_calls_get_exactly_the_web_tools(monkeypatch):
    from app import claude_cli
    seen = {}

    def fake_run(cmd, stdin, env, state, heartbeat):
        seen["cmd"], seen["allowed"] = cmd, state.get("allowed")
        state["ok"] = True
        yield "answer"
    monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    monkeypatch.setattr(claude_cli, "find_claude", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_cli, "_run_stream", fake_run)
    assert "".join(claude_cli.ask_stream("i", "c", tools=["WebSearch", "WebFetch", "Bash"])) == "answer"
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"  # Bash is never passed through
    assert seen["allowed"] == frozenset({"WebSearch", "WebFetch"})
    # the guard: the session may report only those
    claude_cli.check_init({"tools": ["WebSearch", "WebFetch"]}, seen["allowed"])
    with pytest.raises(claude_cli.ToolGuardError):
        claude_cli.check_init({"tools": ["WebSearch", "Bash"]}, seen["allowed"])
    # and an ordinary call still has no tools at all
    claude_cli._stream_strict_ok = None
    list(claude_cli.ask_stream("i", "c"))
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == "" and seen["allowed"] is None
