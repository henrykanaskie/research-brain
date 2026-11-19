"""What the AI remembers about you across chats (app/memory.py about.md): saved from a <remember> tag at the
end of an answer (stripped before you see it), sent with every chat, editable, switchable, capped, and never
fed from private notes."""
from __future__ import annotations

from test_cards import FakeAgent, server, paper  # noqa: F401  (fixture)


def test_answer_tag_is_saved_stripped_and_sent_later(server):  # noqa: F811
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
    # switched off: neither sent nor saved
    c.put("/api/memory/about-on", json={"on": False})
    agent.reply = "Ok.<remember>Likes tables.</remember>"
    r = c.post("/api/chat", json={"question": "Again?", "history": []}).json()
    assert "WHAT YOU REMEMBER" not in agent.prompts[-1] and "remembered" not in r and r["answer"] == "Ok."
    assert len(c.get("/api/memory/about").json()["items"]) == 1


def test_edit_delete_cap_and_private(server):  # noqa: F811
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


def test_streamed_answer_strips_tag(server):  # noqa: F811
    srv, c, use = server
    paper(srv, c, "Iodine plasma", "Iodine plasma text. " * 20)
    use(FakeAgent("Answer body.\n<remember>Prefers short answers.</remember>", id="fakemem2"))
    r = c.post("/api/chat/stream", json={"question": "Keep answers short please", "history": []})
    done = [line for line in r.text.split("\n") if line.startswith("data:")][-1]
    assert '"answer": "Answer body."' in done and "Prefers short answers." in done and "<remember>" not in done
