"""Papers named in a web-search answer become On deck cards (app/finder.py from_answer), and earlier
conversations are kept as <key>~<stamp> (app/chats.py) for Past chats."""
from __future__ import annotations

from test_cards import server  # noqa: F401  (fixture)


def _row(title, doi="", arxiv=""):
    return {"title": title, "doi": doi, "arxiv": arxiv, "year": 2024, "authors": ["A. Author"], "venue": "J. Test",
            "access": "closed", "url": f"https://doi.org/{doi}" if doi else "", "abstract": "An abstract."}


def test_ids_in_answer():
    from app import finder
    text = ("See [Bray](https://doi.org/10.1103/PhysRevA.65.042708), arXiv:2401.01234v2, "
            "https://arxiv.org/abs/2401.01234 again, iopscience.iop.org/article/10.1088/1361-6455/ab1234/pdf, "
            "and https://doi.org/10.48550/arXiv.2312.00001.")
    assert finder.ids_in(text) == [("doi", "10.1103/PhysRevA.65.042708"), ("arxiv", "2401.01234"),
                                   ("doi", "10.1088/1361-6455/ab1234"), ("arxiv", "2312.00001")]
    assert finder.ids_in("no papers here") == []


def test_from_answer_puts_papers_on_deck(server):  # noqa: F811
    srv, c, use = server
    from app import finder
    known = {("doi", "10.1000/aaa"): _row("Paper A", doi="10.1000/aaa"), ("arxiv", "2401.00001"): _row("Paper B", arxiv="2401.00001")}
    lib = c.post("/api/notes", json={"type": "paper", "title": "Paper C", "doi": "10.1000/ccc"}).json()["id"]
    known[("doi", "10.1000/ccc")] = _row("Paper C", doi="10.1000/ccc")
    text = "Read https://doi.org/10.1000/aaa, arXiv:2401.00001, 10.1000/ccc and 10.1000/unknown."
    r = finder.from_answer(srv.store, "iodine cross sections", text, lookup=lambda k, v: known.get((k, v)),
                           copies=[], wait=True)
    titles = {i["title"]: i for i in r["items"]}
    assert set(titles) == {"Paper A", "Paper B", "Paper C"}
    assert titles["Paper C"]["library_id"] == lib and titles["Paper A"]["library_id"] == ""
    deck = c.get("/api/finder").json()
    assert {i["title"] for i in deck["items"] if i["status"] == "deck"} == {"Paper A", "Paper B"}
    assert deck["searches"][0]["topic"] == "From a web search: iodine cross sections"
    # dismissed papers aren't shown again; a repeat shows the same card without a new search
    a = titles["Paper A"]["id"]
    c.put(f"/api/finder/items/{a}", json={"status": "dismissed"})
    r2 = finder.from_answer(srv.store, "again", text, lookup=lambda k, v: known.get((k, v)), copies=[], wait=True)
    assert {i["title"] for i in r2["items"]} == {"Paper B", "Paper C"} and r2["search"] == ""
    assert finder.from_answer(srv.store, "q", "nothing named", lookup=lambda k, v: None) is None


def test_past_chats_are_kept(server):  # noqa: F811
    srv, c, use = server
    msgs = [{"role": "user", "content": "What is a cross section?"},
            {"role": "assistant", "content": "An effective area.", "papers": [
                {"id": "x1", "key": "external:doi:10.1000/aaa", "title": "Paper A", "paper": {"doi": "10.1000/aaa", "abstract": "y" * 5000},
                 "status": "deck", "library_id": ""}]}]
    assert c.put("/api/chats/general", json={"messages": msgs, "title": "General"}).status_code == 200
    assert c.put("/api/chats/general~lx3k9a01", json={"messages": msgs[:1] + [{"role": "assistant", "content": "Old."}],
                                                       "title": "General"}).status_code == 200
    keys = {t["key"] for t in c.get("/api/chats").json()}
    assert {"general", "general~lx3k9a01"} <= keys
    got = c.get("/api/chats/general").json()["messages"][1]
    assert got["papers"][0]["title"] == "Paper A" and len(got["papers"][0]["paper"]["abstract"]) == 1500
    assert c.put("/api/chats/general~BAD", json={"messages": msgs}).status_code == 400
    assert c.delete("/api/chats/general~lx3k9a01").status_code == 200
    assert "general~lx3k9a01" not in {t["key"] for t in c.get("/api/chats").json()}
