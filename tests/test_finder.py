"""Find papers / On deck (app/finder.py): a one-shot AI search for a topic, reviewed before anything is added."""
from __future__ import annotations

import datetime as dt

from test_questions import Recorder, fake_sources, library, paper, server  # noqa: F401


def test_find_papers_puts_proposals_on_deck(server):
    srv, c = server
    from app import finder
    ids = library(srv, c)
    lib_title = srv.store.get(ids["breathing"])["title"]
    found = [paper("Breathing-mode oscillations: a review", "A review of ionization oscillations.", doi="10.1/rev"),
             paper(lib_title, "already in the library", doi="10.1/lib"),
             paper("Krypton CRM for Hall thrusters", "A collisional-radiative model.", arxiv="2501.00001"),
             paper("Unread paper", "never read", doi="10.1/unread")]
    calls = []
    fb = Recorder(replies=[
        '{"action": "search", "queries": ["breathing mode Hall thruster", "ionization oscillation review"]}',
        '{"action": "read", "papers": ["P1", "P2", "P3"]}',
        '{"action": "propose", "papers": [{"paper": "P1", "why": "The standard review."}, {"paper": "P2", "why": "dup"},'
        ' {"paper": "P3", "why": "A model you can use."}, {"paper": "P4", "why": "not read"}]}',
        '{"action": "done", "note": "Two phrasings."}'])
    row = finder.start(srv.store, "breathing mode in Hall thrusters", "reviews first", "5y",
                       backend=fb, sources=fake_sources(found, calls), cites=lambda p, d: [], wait=True)
    assert calls and calls[0][1] >= dt.date.today() - dt.timedelta(days=365 * 5 + 2)
    v = c.get("/api/finder").json()
    s = v["searches"][0]
    assert s["id"] == row["id"] and s["status"] == "done" and s["added"] == 2 and s["topic"] == "breathing mode in Hall thrusters"
    titles = [i["title"] for i in v["items"]]
    assert titles == [found[0]["title"], found[2]["title"]]  # library copy dropped, unread P4 refused
    assert v["deck"] == 2 and v["items"][0]["why"] == "The standard review." and v["items"][0]["paper"]["abstract"]
    sent = "\n".join(fb.prompts)
    assert "TOPIC: breathing mode in Hall thrusters" in sent and "reviews first" in sent
    assert "read P4's abstract before proposing it" in sent

    # dismiss one: off the deck, and the next search is told not to propose it
    item = v["items"][0]
    r = c.put(f"/api/finder/items/{item['id']}", json={"status": "dismissed"})
    assert r.status_code == 200 and r.json()["deck"] == 1
    assert c.put(f"/api/finder/items/{item['id']}", json={"status": "nope"}).status_code == 400
    assert c.put("/api/finder/items/missing", json={"status": "deck"}).status_code == 404
    fb2 = Recorder(replies=['{"action": "search", "queries": ["x"]}', '{"action": "read", "papers": ["P1"]}',
                            '{"action": "propose", "papers": [{"paper": "P1", "why": "again"}]}', '{"action": "done"}'])
    finder.start(srv.store, "breathing mode again", backend=fb2, sources=fake_sources(found, []), cites=lambda p, d: [], wait=True)
    assert "ALREADY FOUND OR DISMISSED" in fb2.prompts[0] and found[0]["title"] in fb2.prompts[0]
    v = c.get("/api/finder").json()
    assert v["searches"][0]["added"] == 0 and len(v["items"]) == 2

    # once it's in the library, the deck says so
    c.post("/api/notes", json={"type": "paper", "title": found[2]["title"], "arxiv": "2501.00001"})
    v = c.get("/api/finder").json()
    kr = next(i for i in v["items"] if i["title"] == found[2]["title"])
    assert kr["library_id"] and v["deck"] == 0

    # forget a search: its deck papers go, dismissed ones stay (so they aren't proposed again)
    assert c.delete(f"/api/finder/searches/{row['id']}").status_code == 200
    v = c.get("/api/finder").json()
    assert [i["status"] for i in v["items"]] == ["dismissed"] and all(s["id"] != row["id"] for s in v["searches"])


def test_find_papers_validates_records_failures_announces_and_ties_to_a_question(server):
    """Bad topics and windows are refused, the endpoint says online lookups are off, a failing agent is
    recorded; a finished search calls on_done and is announced on the live-update bus; a search is tied to
    the open question it is about."""
    srv, c = server
    from app import finder, finder_api
    import pytest
    for topic, since in (("x", "any"), ("plasma", "forever")):
        with pytest.raises(finder.FinderError):
            finder.start(srv.store, topic, since=since, backend=Recorder(), wait=True)
    assert not c.get("/api/finder").json()["searches"]
    # online lookups are off in the tests: the endpoint says so rather than starting a search
    assert c.post("/api/finder/search", json={"topic": "plasma sheaths"}).status_code == 503

    class Broken(Recorder):
        def ask_stream(self, prompt, *, system="", timeout=180):
            raise RuntimeError("signed out")
            yield ""
    finder.start(srv.store, "plasma sheaths", backend=Broken(), sources=fake_sources([], []), wait=True)
    s = c.get("/api/finder").json()["searches"][0]
    assert s["status"] == "error" and "signed out" in s["error"]
    # a finished search is announced
    seen, sent = [], []
    fb = Recorder(replies=['{"action": "search", "queries": ["x"]}', '{"action": "read", "papers": ["P1"]}',
                           '{"action": "propose", "papers": [{"paper": "P1", "why": "fits"}]}', '{"action": "done"}'])
    finder.start(srv.store, "plasma sheaths", backend=fb, sources=fake_sources([paper("Sheaths", "About sheaths.", doi="10.1/s")], []),
                 cites=lambda p, d: [], wait=True, on_done=seen.append)
    assert seen and seen[0]["status"] == "done" and seen[0]["added"] == 1
    # the API's announcer puts it on the live-update bus for the page (static/live.js -> static/finder.js)
    old = finder_api.ctx.publish
    finder_api.ctx.publish = sent.append
    try:
        finder_api._announce(seen[0])
    finally:
        finder_api.ctx.publish = old
    assert sent == [{"type": "finder-done", "id": seen[0]["id"], "topic": "plasma sheaths", "status": "done",
                     "added": 1, "error": "", "deck": 1}]
    # a search is tied to the open question it is about
    q = c.post("/api/notes", json={"type": "question", "title": "How accurate are atomic cross sections for krypton?",
                                   "body": "electron impact ionization cross sections krypton accuracy"}).json()["id"]
    for t in ("Tomato gardening", "Bread baking at altitude", "Hall thruster plume"):
        c.post("/api/notes", json={"type": "paper", "title": t, "abstract": t})
    m = srv._match_question("atomic cross sections krypton ionization")
    assert m and m["id"] == q
    assert srv._match_question("sourdough bread baking") is None
    row = finder.start(srv.store, "krypton cross sections", question=m, backend=Recorder(replies=['{"action": "done"}']),
                       sources=fake_sources([], []), wait=True, copies=[])
    s = next(x for x in c.get("/api/finder").json()["searches"] if x["id"] == row["id"])
    assert s["question"]["id"] == q


def test_paywalled_papers_get_a_free_copy_and_a_note(server):
    srv, c = server
    from app import finder, freecopy
    closed = paper("Paywalled sheath paper with a long enough title", "About sheaths.", doi="10.1/pay")
    closed["access"] = "closed"
    asked = []

    def repo(p, email):
        asked.append(p["doi"])
        return [{"url": "https://repo.example.org/pay.pdf", "page": "https://repo.example.org/pay", "host": "Example Repository",
                 "version": "accepted", "via": "OpenAlex"},
                {"url": "https://arxiv.org/pdf/2501.1", "page": "", "host": "arXiv", "version": "preprint", "via": "arXiv"}]
    fb = Recorder(replies=['{"action": "search", "queries": ["x"]}', '{"action": "read", "papers": ["P1"]}',
                           '{"action": "propose", "papers": [{"paper": "P1", "why": "fits"}]}', '{"action": "done"}'])
    finder.start(srv.store, "sheaths", backend=fb, sources=fake_sources([closed], []), cites=lambda p, d: [], wait=True,
                 copies=[repo])
    item = c.get("/api/finder").json()["items"][0]
    fc = item["free_copy"]
    assert asked == ["10.1/pay"]
    assert fc["version"] == "accepted" and fc["url"].endswith("pay.pdf")  # the accepted manuscript beats the preprint
    assert "accepted manuscript" in fc["note"] and "Example Repository" in fc["note"] and "10.1/pay" in fc["note"]
    # a preprint's note warns that it may differ; nothing found is recorded too
    pre = freecopy.find({"title": "t", "doi": "10.1/x"}, sources=[lambda p, e: [{"url": "https://x.org/a.pdf", "host": "", "version": "preprint"}]])
    assert "may predate peer review" in pre["note"]
    assert freecopy.find({"title": "t", "doi": "10.1/x"}, sources=[lambda p, e: []]) is None
    # adding the paper puts the note in its body (once)
    n = c.post("/api/notes", json={"type": "paper", "title": closed["title"], "doi": "10.1/pay"}).json()
    finder.use_free_copy(srv.store, n["id"], fc, wait=True)
    finder.use_free_copy(srv.store, n["id"], fc, wait=True)
    body = srv.store.get(n["id"])["body"]
    assert body.count(finder.NOTE_HEAD) == 1 and "accepted manuscript" in body
    assert c.post(f"/api/finder/items/{item['id']}/added", json={"note_id": "nope"}).status_code == 404

