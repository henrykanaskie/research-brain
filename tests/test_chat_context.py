"""What a chat sends to the AI beyond the papers: how the focus paper is connected on the map (a research
question you linked it to, with the question's own text, never a private one), and what the AI remembers about
you (app/memory.py about.md): saved from a <remember> tag at the end of an answer (stripped before you see it),
sent with every chat, editable, switchable, capped, and never fed from private notes."""
from __future__ import annotations

from test_cards import FakeAgent, server, paper  # noqa: F401  (fixture)


def _set_private(srv, nid, private):
    from app import privacy
    from app.store import set_private
    n = srv.store.get(nid)
    set_private(n, private)
    srv.store.save(n)
    privacy.refresh()


def test_chat_tells_the_ai_how_papers_are_connected_but_not_private_links(server):  # noqa: F811
    """A paper chat and a library chat both get the linked research question (title and text) in a connections
    section; once the question is private again, neither it nor its link reaches the AI."""
    srv, c, use = server
    pid = paper(srv, c, "Electron impact excitation of iodine", "Cross sections for iodine plasma. " * 20,
                abstract="We compute electron impact excitation cross sections for iodine.")
    other = paper(srv, c, "Stellar spectra of cool dwarfs", "Unrelated astronomy text. " * 20)
    q = c.post("/api/notes", json={"type": "question", "title": "What sets the line ratios in iodine thrusters?",
                                   "body": "I want to know which collision data matter for the diagnostics."}).json()["id"]
    assert c.post(f"/api/notes/{q}/link", json={"target": pid}).status_code == 200
    _set_private(srv, q, False)  # questions are private by default; this one is shared with the AI
    agent = use(FakeAgent("An answer.", id="fakeconn"))

    # a chat about the paper
    r = c.post("/api/chat", json={"question": "How does this relate to my research question?", "history": [],
                                  "paper": pid})
    assert r.status_code == 200, r.text
    sent = agent.prompts[-1]
    assert "HOW THE FOCUS PAPER IS CONNECTED" in sent
    assert f"[[{q}]]" in sent and "research question" in sent and "linked by the student" in sent
    assert "which collision data matter" in sent  # the question's own text, not just its title
    assert f"[[{other}]]" not in sent.split("HOW THE FOCUS PAPER IS CONNECTED")[1].split("---")[0]

    # a library chat that finds the paper
    r = c.post("/api/chat", json={"question": "iodine electron impact excitation cross sections", "history": []})
    assert r.status_code == 200, r.text
    sent = agent.prompts[-1]
    assert "HOW THESE ARE CONNECTED" in sent and f"[[{q}]]" in sent

    # kept private again: neither it nor its link reaches the AI
    _set_private(srv, q, True)
    r = c.post("/api/chat", json={"question": "How does this relate?", "history": [], "paper": pid})
    assert r.status_code == 200, r.text
    assert f"[[{q}]]" not in agent.prompts[-1]


def test_remember_tag_is_saved_stripped_and_sent_later(server):  # noqa: F811
    """A <remember> tag is saved and stripped from the answer (plain and streamed), the memory goes with later
    chats, and with memory switched off it is neither sent nor saved."""
    srv, c, use = server
    paper(srv, c, "Iodine plasma", "Iodine plasma text. " * 20)
    agent = use(FakeAgent("Here is the answer.\n\n<remember>The user studies iodine Hall thrusters.</remember>", id="fakemem"))
    r = c.post("/api/chat", json={"question": "I work on iodine Hall thrusters. What matters?", "history": []}).json()
    assert r["answer"] == "Here is the answer." and r["remembered"] == ["The user studies iodine Hall thrusters."]
    about = c.get("/api/memory/about").json()
    assert [i["text"] for i in about["items"]] == ["The user studies iodine Hall thrusters."]
    agent.reply = "Another answer."
    c.post("/api/chat", json={"question": "Anything else?", "history": []})
    assert "WHAT YOU REMEMBER ABOUT THE USER" in agent.prompts[-1] and "iodine Hall thrusters" in agent.prompts[-1]

    # a streamed answer has its tag stripped too
    agent.reply = "Answer body.\n<remember>Prefers short answers.</remember>"
    r = c.post("/api/chat/stream", json={"question": "Keep answers short please", "history": []})
    done = [line for line in r.text.split("\n") if line.startswith("data:")][-1]
    assert '"answer": "Answer body."' in done and "Prefers short answers." in done and "<remember>" not in done
    n_items = len(c.get("/api/memory/about").json()["items"])

    # switched off: neither sent nor saved
    c.put("/api/memory/about-on", json={"on": False})
    agent.reply = "Ok.<remember>Likes tables.</remember>"
    r = c.post("/api/chat", json={"question": "Again?", "history": []}).json()
    assert "WHAT YOU REMEMBER" not in agent.prompts[-1] and "remembered" not in r and r["answer"] == "Ok."
    assert len(c.get("/api/memory/about").json()["items"]) == n_items


def test_memory_items_can_be_edited_deleted_are_capped_and_never_private(server):  # noqa: F811
    """Memory keeps at most ABOUT_MAX items (the newest), items can be edited and deleted, long text is refused
    or cut, and text copied from a private note is refused."""
    srv, c, use = server
    from app import memory as memory_mod
    for i in range(memory_mod.ABOUT_MAX + 5):
        assert c.post("/api/memory/about", json={"text": f"Fact number {i} about the research."}).status_code == 201
    items = c.get("/api/memory/about").json()["items"]
    assert len(items) == memory_mod.ABOUT_MAX and items[-1]["text"].startswith(f"Fact number {memory_mod.ABOUT_MAX + 4}")
    iid = items[0]["id"]
    assert c.put(f"/api/memory/about/{iid}", json={"text": "Edited fact."}).json()["text"] == "Edited fact."
    assert c.delete(f"/api/memory/about/{iid}").status_code == 200
    assert c.delete(f"/api/memory/about/{iid}").status_code == 404
    assert c.post("/api/memory/about", json={"text": "x" * 500}).status_code == 400 or \
        len(c.get("/api/memory/about").json()["items"][-1]["text"]) <= memory_mod.ABOUT_TEXT_MAX + 1
    # copied from a private note: refused
    secret = "My unpublished result is that the anomalous transport coefficient doubles at forty volts."
    nid = c.post("/api/notes", json={"type": "note", "title": "Secret", "body": secret}).json()["id"]
    from app import privacy
    privacy.refresh()
    assert privacy.is_private_id(nid)
    assert c.post("/api/memory/about", json={"text": secret}).status_code == 400
