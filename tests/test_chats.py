"""Remembered conversations (app/chats.py) and Past chats kept as <key>~<stamp>, asking about the papers in a topic
or two, the general chat (attachments, web search and the papers a web answer names, app/finder.py from_answer),
the folder chooser, and continuing a conversation on the phone.

Run:  .venv/bin/python -m pytest -q tests/test_chats.py
"""
import pytest

from rb_app import add_paper as paper, load_server


@pytest.fixture()
def server(tmp_path, monkeypatch):
    srv = load_server(monkeypatch, tmp_path, RB_CITES_ON_IMPORT="0", RB_RECS_AUTO="0")
    monkeypatch.setattr(srv.scholar, "lookup", lambda q: None)
    return srv


@pytest.fixture()
def client(server):
    from fastapi.testclient import TestClient
    return TestClient(server.app)


def test_a_conversation_is_remembered_older_turns_condensed_and_past_chats_kept(client, server):
    """A long conversation keeps the last KEEP messages (trimmed) plus a summary that goes to the agent; a new
    conversation forgets it; earlier conversations are kept under a stamped key, with long abstracts cut."""
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

    # Past chats: the earlier conversation is kept as <key>~<stamp>
    msgs = [{"role": "user", "content": "What is a cross section?"},
            {"role": "assistant", "content": "An effective area.", "papers": [
                {"id": "x1", "key": "external:doi:10.1000/aaa", "title": "Paper A", "paper": {"doi": "10.1000/aaa", "abstract": "y" * 5000},
                 "status": "deck", "library_id": ""}]}]
    assert client.put("/api/chats/general", json={"messages": msgs, "title": "General"}).status_code == 200
    assert client.put("/api/chats/general~lx3k9a01", json={"messages": msgs[:1] + [{"role": "assistant", "content": "Old."}],
                                                            "title": "General"}).status_code == 200
    keys = {t["key"] for t in client.get("/api/chats").json()}
    assert {"general", "general~lx3k9a01"} <= keys
    got = client.get("/api/chats/general").json()["messages"][1]
    assert got["papers"][0]["title"] == "Paper A" and len(got["papers"][0]["paper"]["abstract"]) == 1500
    assert client.delete("/api/chats/general~lx3k9a01").status_code == 200
    assert "general~lx3k9a01" not in {t["key"] for t in client.get("/api/chats").json()}


def test_conversation_names_are_checked_and_space_is_capped(client, server, monkeypatch):
    """Bad conversation keys are refused, and only the newest MAX_THREADS conversations are kept."""
    for key in ("../../etc", "Library", "paper-", "topics-a+b+c+d+e+f+g+h+i+j"):
        assert client.put(f"/api/chats/{key}", json={"messages": []}).status_code in (400, 404), key
    assert client.get("/api/chats/paper-..").status_code in (400, 404)
    assert client.put("/api/chats/general~BAD", json={"messages": [{"role": "user", "content": "q"}]}).status_code == 400

    from app import chats
    monkeypatch.setattr(chats, "MAX_THREADS", 3)
    for i in range(5):
        client.put(f"/api/chats/paper-p{i}", json={"messages": [{"role": "user", "content": f"q{i}"}]})
    keys = {t["key"] for t in client.get("/api/chats").json()}
    assert len(keys) == 3 and "paper-p4" in keys and "paper-p0" not in keys


def test_asking_about_topics_uses_only_their_papers(client, server):
    """A chat about topics sees only their papers (your summary standing for a paper), through the API too,
    and an empty topic is refused."""
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


def test_general_chat_leaves_out_the_library_and_web_search_is_off_until_turned_on(client, server):
    """The general chat sends no library content, refuses a private attachment, and refuses web search until
    the setting is on (then needs Claude Code); the setting can be turned off again."""
    paper(client, "Secret sheath paper", "a very particular phrase about sheaths")
    r = client.post("/api/general/stream", json={"question": "What is a Debye length?"})
    evs = _sse(r.text)
    done = [d for e, d in evs if e == "done"]
    assert done and done[0]["web"] is False
    assert "particular phrase" not in done[0]["answer"]  # no library content goes along

    note = client.post("/api/notes", json={"type": "note", "title": "Private idea", "body": "secret"}).json()["id"]
    client.put(f"/api/notes/{note}", json={"private": True})
    assert client.post("/api/general/stream", json={"question": "Thoughts?", "attach": [note]}).status_code == 403

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
    """A web call passes only WebSearch and WebFetch to Claude Code and the session guard allows only those;
    an ordinary call has no tools at all."""
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


def test_attachments_in_general_chat(client, server, monkeypatch):
    """Attached items go along in full without web search; with web search only a published paper's public
    parts go along, and your own writing needs a confirmation setting first."""
    sent = {}

    def fake_sse(instruction, context, finish, purpose="ask", sources=None):
        sent.update(instruction=instruction, context=context, web=False)
        yield "event: done\ndata: {}\n\n"

    def fake_web(instruction, context, system="", heartbeat=None, model=None, tools=None):
        sent.update(instruction=instruction, context=context, web=True, tools=tools)
        yield "answer"
    monkeypatch.setattr(server, "_claude_sse", fake_sse)
    monkeypatch.setattr(server.claude_cli, "ask_stream", fake_web)

    class Claude:
        id, name = "claude", "Claude Code"
    monkeypatch.setattr(server, "_ai_agent", lambda: Claude())
    monkeypatch.setattr(server.agents, "guard_send", lambda b, purpose, i, c, **k: [i, c])

    a = paper(client, "Sheath formation at a floating wall", "Published abstract about the Bohm criterion.")
    client.put(f"/api/notes/{a}", json={"summary": "MY OWN SUMMARY of the sheath paper."})
    note = client.post("/api/notes", json={"type": "note", "title": "My idea", "body": "MY OWN IDEA about presheaths."}).json()["id"]
    client.put(f"/api/notes/{note}", json={"private": False})  # notes are private by default; this one is shared

    # nothing attached: just the question
    client.post("/api/general/stream", json={"question": "What is a sheath?"})
    assert "Bohm criterion" not in sent["context"] and "MY OWN" not in sent["context"]
    # attached, no web: everything, your summary too
    client.post("/api/general/stream", json={"question": "Explain the sheath", "attach": [a, note]})
    assert "MY OWN SUMMARY" in sent["context"] and "MY OWN IDEA" in sent["context"] and f"[[{a}]]" in sent["context"]
    assert "ATTACHED" in sent["context"] and "attached items" in sent["instruction"]

    client.put("/api/privacy/settings", json={"web_search": True})
    # web + a published paper only: no confirmation, and only the published parts go along
    r = client.post("/api/general/stream", json={"question": "Newer work on this?", "web": True, "attach": [a]})
    assert r.status_code == 200 and sent["web"] and sent["tools"] == ["WebSearch", "WebFetch"]
    assert "Bohm criterion" in sent["context"] and "MY OWN SUMMARY" not in sent["context"]
    # web + your own note: asks you to confirm first
    r = client.post("/api/general/stream", json={"question": "Is my idea new?", "web": True, "attach": [note]})
    assert r.status_code == 409
    # confirmed: your writing goes along too
    client.put("/api/privacy/settings", json={"web_with_attachments": True})
    r = client.post("/api/general/stream", json={"question": "Is my idea new?", "web": True, "attach": [a, note]})
    assert r.status_code == 200 and "MY OWN IDEA" in sent["context"] and "MY OWN SUMMARY" in sent["context"]


def _row(title, doi="", arxiv=""):
    return {"title": title, "doi": doi, "arxiv": arxiv, "year": 2024, "authors": ["A. Author"], "venue": "J. Test",
            "access": "closed", "url": f"https://doi.org/{doi}" if doi else "", "abstract": "An abstract."}


def test_papers_named_in_a_web_answer_go_on_deck(client, server):
    """DOIs and arXiv ids are found in an answer's text (deduplicated); the papers named go On deck, linked to the
    library copy when there is one; dismissed ones aren't shown again and a repeat makes no new search."""
    from app import finder
    text = ("See [Bray](https://doi.org/10.1103/PhysRevA.65.042708), arXiv:2401.01234v2, "
            "https://arxiv.org/abs/2401.01234 again, iopscience.iop.org/article/10.1088/1361-6455/ab1234/pdf, "
            "and https://doi.org/10.48550/arXiv.2312.00001.")
    assert finder.ids_in(text) == [("doi", "10.1103/PhysRevA.65.042708"), ("arxiv", "2401.01234"),
                                   ("doi", "10.1088/1361-6455/ab1234"), ("arxiv", "2312.00001")]
    assert finder.ids_in("no papers here") == []

    known = {("doi", "10.1000/aaa"): _row("Paper A", doi="10.1000/aaa"), ("arxiv", "2401.00001"): _row("Paper B", arxiv="2401.00001")}
    lib = client.post("/api/notes", json={"type": "paper", "title": "Paper C", "doi": "10.1000/ccc"}).json()["id"]
    known[("doi", "10.1000/ccc")] = _row("Paper C", doi="10.1000/ccc")
    text = "Read https://doi.org/10.1000/aaa, arXiv:2401.00001, 10.1000/ccc and 10.1000/unknown."
    r = finder.from_answer(server.store, "iodine cross sections", text, lookup=lambda k, v: known.get((k, v)),
                           copies=[], wait=True)
    titles = {i["title"]: i for i in r["items"]}
    assert set(titles) == {"Paper A", "Paper B", "Paper C"}
    assert titles["Paper C"]["library_id"] == lib and titles["Paper A"]["library_id"] == ""
    deck = client.get("/api/finder").json()
    assert {i["title"] for i in deck["items"] if i["status"] == "deck"} == {"Paper A", "Paper B"}
    assert deck["searches"][0]["topic"] == "From a web search: iodine cross sections"
    # dismissed papers aren't shown again; a repeat shows the same card without a new search
    a = titles["Paper A"]["id"]
    client.put(f"/api/finder/items/{a}", json={"status": "dismissed"})
    r2 = finder.from_answer(server.store, "again", text, lookup=lambda k, v: known.get((k, v)), copies=[], wait=True)
    assert {i["title"] for i in r2["items"]} == {"Paper B", "Paper C"} and r2["search"] == ""
    assert finder.from_answer(server.store, "q", "nothing named", lookup=lambda k, v: None) is None


def test_choose_folder_uses_the_computers_own_dialog(client, server, monkeypatch, tmp_path):
    """The folder chooser runs the computer's own dialog (osascript on a Mac), handles cancel and a computer
    without one, and a paired phone can never open it."""
    import subprocess
    import sys
    from app import pairing
    assert any(rx.match("/api/pick-folder") for rx, _ in pairing.COMPUTER_ONLY)
    if sys.platform == "darwin":
        cmd = server.pick_folder_command('Pick "this"')
        assert cmd[0] == "/usr/bin/osascript" and "choose folder" in cmd[-1] and '"Pick \'this\'"' in cmd[-1]

    picked = tmp_path / "Lab Drive" / "research-brain"
    picked.mkdir(parents=True)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=str(picked) + "/\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(server, "pick_folder_command", lambda prompt: ["chooser", prompt])
    r = client.post("/api/pick-folder", json={"prompt": "Choose your lab folder"})
    assert r.status_code == 200 and r.json() == {"path": str(picked)}
    assert calls and calls[0] == ["chooser", "Choose your lab folder"]
    # cancelled
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="User canceled"))
    assert client.post("/api/pick-folder", json={}).json() == {"path": None}
    # no chooser on this computer
    monkeypatch.setattr(server, "pick_folder_command", lambda prompt: None)
    assert client.post("/api/pick-folder", json={}).status_code == 501


def test_continue_on_phone_writes_one_prompt_without_private_notes_and_condenses(client, server):
    """Handoff writes the conversation as one prompt with the papers cited in full, leaves out private notes
    (saying so), refuses an empty conversation, and condenses a long one to fit."""
    pid = paper(client, "Sheath formation in plasmas", "How ions reach the wall.", authors=["Ada Bray", "Ben Cole"], year=2002, doi="10.1/x")
    secret = client.post("/api/notes", json={"type": "note", "title": "My secret idea",
                                             "body": "the wall potential secretly depends on the purple electron mood ring"}).json()["id"]
    msgs = [{"role": "user", "content": "Why does a sheath form?"},
            {"role": "assistant", "content": "Because electrons are faster than ions.", "sources": [{"id": pid, "title": "Sheath"}]},
            {"role": "user", "content": "Compare with the wall potential secretly depends on the purple electron mood ring idea"},
            {"role": "assistant", "content": f"See [[{secret}]] for that."}]
    r = client.post(f"/api/chats/paper-{pid}/handoff", json={"messages": msgs, "title": "Sheath"})
    assert r.status_code == 200, r.text
    d = r.json()
    t = d["text"]
    assert "Why does a sheath form?" in t and "electrons are faster" in t
    assert '"Sheath formation in plasmas" (Bray and Cole, 2002), doi:10.1/x' in t and "How ions reach the wall." in t
    assert "purple electron" not in t and secret not in t and "My secret idea" not in t
    assert d["left_out"] == 1 and "a private note" in t
    # nothing to continue
    assert client.post("/api/chats/library/handoff", json={"messages": []}).status_code == 400

    from app import chats
    msgs = []
    for i in range(40):
        msgs += [{"role": "user", "content": f"Question {i}?"}, {"role": "assistant", "content": f"Answer {i}. " + "word " * 800}]
    d = client.post("/api/chats/general/handoff", json={"messages": msgs}).json()
    assert len(d["text"]) <= chats.HANDOFF_CHARS + 500 and d["condensed"] > 0
    assert "Answer 39." in d["text"] and "Earlier in the conversation" in d["text"]
