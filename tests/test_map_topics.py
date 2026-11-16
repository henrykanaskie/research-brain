"""Topics and the map: a paper you put in a topic groups with it, similar or not (app/server.py _clusters)."""
from __future__ import annotations

from test_questions import server  # noqa: F401


def test_a_paper_added_to_a_topic_joins_its_region(server):
    """A paper with no similar neighbours still groups with its topic on the map."""
    srv, c = server
    ids = [c.post("/api/notes", json={"type": "paper", "title": f"Collisional radiative model for krypton plasma {i}",
                                      "abstract": "collisional radiative model krypton plasma emission line ratios"}).json()["id"] for i in range(4)]
    odd = c.post("/api/notes", json={"type": "paper", "title": "Gardening with tomatoes",
                                     "abstract": "soil compost watering"}).json()["id"]
    g = c.get("/api/graph").json()
    cl = {n["id"]: n["cluster"] for n in g["nodes"]}
    assert cl[ids[0]] >= 0 and cl[odd] != cl[ids[0]]
    c.post("/api/topics", json={"name": "CRMs", "members": ids + [odd]})
    g = c.get("/api/graph").json()
    cl = {n["id"]: n["cluster"] for n in g["nodes"]}
    assert cl[odd] == cl[ids[0]] >= 0


def test_papers_matching_an_open_question_group_with_it(server):
    srv, c = server
    q = c.post("/api/notes", json={"type": "question", "title": "How accurate are atomic cross sections for krypton ionization?"}).json()["id"]
    # three papers on the question's subject, worded differently enough that they barely match each other
    ps = [c.post("/api/notes", json={"type": "paper", "title": t, "abstract": a}).json()["id"] for t, a in [
        ("Krypton ionization cross sections measured", "electron beam apparatus absolute measurement krypton ionization"),
        ("Atomic cross sections benchmark", "accurate atomic cross sections compared across databases"),
        ("Uncertainty in krypton atomic data", "accurate krypton atomic data uncertainty propagation")]]
    other = [c.post("/api/notes", json={"type": "paper", "title": f"Tomato gardening {i}", "abstract": "soil compost watering tomato garden"}).json()["id"] for i in range(3)]
    g = c.get("/api/graph").json()
    cl = {n["id"]: n["cluster"] for n in g["nodes"]}
    assert cl[q] >= 0 and all(cl[p] == cl[q] for p in ps), cl
    assert all(cl[o] != cl[q] for o in other)


def test_a_paper_search_is_tied_to_the_open_question_it_is_about(server):
    srv, c = server
    q = c.post("/api/notes", json={"type": "question", "title": "How accurate are atomic cross sections for krypton?",
                                   "body": "electron impact ionization cross sections krypton accuracy"}).json()["id"]
    for t in ("Tomato gardening", "Bread baking at altitude", "Hall thruster plume"):
        c.post("/api/notes", json={"type": "paper", "title": t, "abstract": t})
    m = srv._match_question("atomic cross sections krypton ionization")
    assert m and m["id"] == q
    assert srv._match_question("sourdough bread baking") is None
    from app import finder
    from test_questions import Recorder
    from test_scout import fake_sources
    row = finder.start(srv.store, "krypton cross sections", question=m, backend=Recorder(replies=['{"action": "done"}']),
                       sources=fake_sources([], []), wait=True, copies=[])
    s = next(x for x in c.get("/api/finder").json()["searches"] if x["id"] == row["id"])
    assert s["question"]["id"] == q
