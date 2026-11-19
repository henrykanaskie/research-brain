"""The AI is told how a paper is connected on the map: a research question you linked it to reaches the
agent (with the question itself), in a chat about the paper and in a library chat that finds it."""
from __future__ import annotations

from test_cards import FakeAgent, server, paper  # noqa: F401  (fixture)


def _setup(srv, c):
    pid = paper(srv, c, "Electron impact excitation of iodine", "Cross sections for iodine plasma. " * 20,
                abstract="We compute electron impact excitation cross sections for iodine.")
    other = paper(srv, c, "Stellar spectra of cool dwarfs", "Unrelated astronomy text. " * 20)
    q = c.post("/api/notes", json={"type": "question", "title": "What sets the line ratios in iodine thrusters?",
                                   "body": "I want to know which collision data matter for the diagnostics."}).json()["id"]
    assert c.post(f"/api/notes/{q}/link", json={"target": pid}).status_code == 200
    from app import privacy
    from app.store import set_private
    n = srv.store.get(q)
    set_private(n, False)  # questions are private by default; this one is shared with the AI
    srv.store.save(n)
    privacy.refresh()
    return pid, other, q


def test_paper_chat_sends_the_linked_question(server):  # noqa: F811
    srv, c, use = server
    pid, other, q = _setup(srv, c)
    agent = use(FakeAgent("An answer.", id="fakeconn"))
    r = c.post("/api/chat", json={"question": "How does this relate to my research question?", "history": [],
                                  "paper": pid})
    assert r.status_code == 200, r.text
    sent = agent.prompts[-1]
    assert "HOW THE FOCUS PAPER IS CONNECTED" in sent
    assert f"[[{q}]]" in sent and "research question" in sent and "linked by the student" in sent
    assert "which collision data matter" in sent  # the question's own text, not just its title
    assert f"[[{other}]]" not in sent.split("HOW THE FOCUS PAPER IS CONNECTED")[1].split("---")[0]


def test_library_chat_sends_connections(server):  # noqa: F811
    srv, c, use = server
    pid, other, q = _setup(srv, c)
    agent = use(FakeAgent("An answer.", id="fakeconn2"))
    r = c.post("/api/chat", json={"question": "iodine electron impact excitation cross sections", "history": []})
    assert r.status_code == 200, r.text
    sent = agent.prompts[-1]
    assert "HOW THESE ARE CONNECTED" in sent and f"[[{q}]]" in sent


def test_private_links_stay_out(server):  # noqa: F811
    srv, c, use = server
    pid, other, q = _setup(srv, c)
    from app import privacy
    from app.store import set_private
    n = srv.store.get(q)
    set_private(n, True)  # kept private again: neither it nor its link reaches the AI
    srv.store.save(n)
    privacy.refresh()
    agent = use(FakeAgent("An answer.", id="fakeconn3"))
    r = c.post("/api/chat", json={"question": "How does this relate?", "history": [], "paper": pid})
    assert r.status_code == 200, r.text
    assert f"[[{q}]]" not in agent.prompts[-1]
