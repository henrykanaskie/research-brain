"""Regions on the map: how papers group (app/server.py _clusters) and what the regions are called (app/regions.py).

Grouping: a paper you put in a topic groups with it, similar or not, and papers matching an open question
group with it. With a neural embedding model, a library in one field must still split into its sub-areas:
neural cosine similarities in a single-field library are nearly all high, so the plain threshold chained every
paper into one region (while TF-IDF, which discounts shared words, found three). The region code compares
papers by what sets them apart: embeddings minus the library's average.

Names: yours, else the AI's, else the matching topic's, else automatic.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app import regions, topics
from rb_app import load_server
from test_questions import Recorder, server  # noqa: F401


# ---------------------------------------------------------------- grouping
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


def test_moving_a_paper_to_another_region_moves_it_and_a_main_region_decides_between_two(server):
    """"Put in region" used to add a paper to the other region and leave it in its own, where the bigger region kept
    it: it never moved. Moving takes it out of the region it's in; a paper you keep in two regions is drawn in the
    one you make its main region; taking it out of every region gives it back to similarity."""
    srv, c = server
    def mk(title, abstract):
        return c.post("/api/notes", json={"type": "paper", "title": title, "abstract": abstract}).json()["id"]
    krs = [mk(f"Collisional radiative model for krypton plasma {i}", "collisional radiative model krypton plasma emission")
           for i in range(5)]
    hal = [mk(f"Hall thruster breathing mode oscillation {i}", "hall thruster breathing mode discharge oscillation")
           for i in range(3)]
    c.post("/api/topics", json={"name": "Krypton models", "members": krs})
    c.post("/api/topics", json={"name": "Hall thrusters", "members": hal})
    slug = {t["name"]: t["slug"] for t in c.get("/api/topics").json()}
    kr, ha = slug["Krypton models"], slug["Hall thrusters"]
    p = krs[0]

    def where():
        cl = {n["id"]: n["cluster"] for n in c.get("/api/graph").json()["nodes"]}
        return "kr" if cl[p] == cl[krs[1]] else "ha" if cl[p] == cl[hal[0]] else cl[p]
    assert where() == "kr"
    # the old "Put in region": added, not moved, so the bigger region keeps it
    c.post(f"/api/topics/{ha}/notes", json={"ids": [p]})
    assert where() == "kr"
    # main region: drawn in the region you choose, in both still
    r = c.put(f"/api/notes/{p}/main-region", json={"slug": ha}).json()
    assert r["main_region"] == ha and where() == "ha"
    r = c.put(f"/api/notes/{p}/main-region", json={"slug": None}).json()
    assert r["main_region"] is None and where() == "kr"
    assert c.put(f"/api/notes/{p}/main-region", json={"slug": "no-such-region"}).status_code == 400
    # move: out of Krypton models, into Hall thrusters (only there now, so no main region needed)
    c.delete(f"/api/topics/{ha}/notes/{p}")
    r = c.post(f"/api/notes/{p}/region", json={"to": ha, "out_of": [kr]}).json()
    assert [t["slug"] for t in r["topics"]] == [ha] and r["main_region"] is None
    assert where() == "ha"
    # move while it stays in another region too: the one it moved to becomes its main one
    c.post(f"/api/topics/{kr}/notes", json={"ids": [p]})
    r = c.post(f"/api/notes/{p}/region", json={"to": ha, "out_of": []}).json()
    assert r["main_region"] == ha and where() == "ha"
    # out of every region: the main region goes too
    r = c.post(f"/api/notes/{p}/region", json={"to": "", "out_of": [kr, ha]}).json()
    assert r["topics"] == [] and r["main_region"] is None


def test_papers_matching_an_open_question_group_with_it(server):
    """Papers on an open question's subject share its region even when they barely match each other."""
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


def _one_field_library(n_per_group=5, groups=3, seed=0):
    """Unit vectors sharing a strong common direction (the field) plus a weaker group direction and noise:
    every pair has a plain cosine well above 0.42, but each group is distinct once the field is removed."""
    rng = np.random.default_rng(seed)
    dim = 64
    field = rng.normal(size=dim)
    field /= np.linalg.norm(field)
    centres = [rng.normal(size=dim) for _ in range(groups)]
    ids, rows, truth = [], [], {}
    for g, c in enumerate(centres):
        c = c / np.linalg.norm(c)
        for k in range(n_per_group):
            v = 3.0 * field + 1.0 * c + 0.06 * rng.normal(size=dim)
            rows.append(v / np.linalg.norm(v))
            nid = f"p{g}-{k}"
            ids.append(nid)
            truth[nid] = g
    m = np.vstack(rows)
    by_id = {i: {"id": i, "type": "paper", "title": i} for i in ids}
    return ids, m, by_id, truth


def _neighbours(m, ids):
    sims = m @ m.T
    np.fill_diagonal(sims, -1.0)
    return {ids[r]: [(ids[c], float(sims[r, c])) for c in np.argsort(-sims[r])[:8]] for r in range(len(ids))}


def test_a_one_field_library_splits_into_its_sub_areas_with_a_neural_model(tmp_path, monkeypatch):
    """Plain cosines in a one-field library are all high; centred neighbours keep each paper's strong neighbours
    inside its own group, and the regions come out as the three groups. TF-IDF and small libraries keep the
    plain neighbours."""
    server = load_server(monkeypatch, tmp_path)
    ids, m, by_id, truth = _one_field_library()
    sims = m @ m.T
    off = sims[~np.eye(len(ids), dtype=bool)]
    assert off.min() > 0.42  # the old neural threshold keeps every pair: one region
    idx = {"ids": ids, "m": m, "by_id": by_id, "neighbours": _neighbours(m, ids)}
    # TF-IDF and small libraries keep plain neighbours
    monkeypatch.setattr(server.embedder, "kind", "tfidf")
    assert server._centred_neighbours(idx, sorted(ids)) is None
    monkeypatch.setattr(server.embedder, "kind", "sentence-transformers:test")
    assert server._centred_neighbours(idx, sorted(ids)[:5]) is None
    # centred neighbours separate the groups
    out = server._centred_neighbours(idx, sorted(ids))
    assert out is not None
    neigh, mc = out
    assert mc.shape == m.shape
    for i, ns in neigh.items():
        strong = [j for j, s in ns if s >= 0.15]
        assert strong, i
        assert all(truth[j] == truth[i] for j in strong), (i, strong)
    # and the regions split the library into its three groups
    monkeypatch.setattr(server, "_question_ties", lambda idx, thr: {})
    monkeypatch.setattr(server, "_cluster_names", lambda groups: [f"region {k}" for k in range(len(groups))])
    cluster_of, clusters = server._clusters(idx, [])
    assert len(clusters) == 3
    for g in range(3):
        assert len({cluster_of[i] for i in ids if truth[i] == g}) == 1, g


# ---------------------------------------------------------------- names
def _store(root):
    root.mkdir(parents=True, exist_ok=True)
    s = SimpleNamespace(root=root, docs=root / "docs")
    s.all = lambda: [{"id": i, "type": "paper"} for i in "abcdefgh"]
    s.get = lambda i: {"id": i, "type": "paper"}
    return s


def test_region_names_topic_custom_reset_ai_and_following_the_region(tmp_path):
    """A region takes its topic's name, else the automatic one; renaming a region renames its topic
    (one thing, one name); where an older version left them different, the region's name wins; AI names show until you
    rename, and your name follows a region as it grows and shrinks but not one that only brushes it."""
    s = _store(tmp_path / "topic")
    t = topics.create_topic(s, "Plasma waves", members=["a", "b", "c"])
    clusters = [{"id": 0, "label": "alfven, wave", "size": 3}, {"id": 1, "label": "lasers", "size": 2}]
    mem = {0: ["a", "b", "c"], 1: ["e", "f"]}
    out = regions.apply(s, clusters, mem)
    assert out[0]["label"] == "Plasma waves" and out[0]["name_source"] == "topic"
    assert out[0]["topic"]["slug"] == t["slug"]
    assert out[1]["label"] == "lasers" and out[1]["name_source"] == "auto" and out[1]["topic"] is None

    # left different by an older version: the region's name wins, and its topic takes it
    regions._save(s, [{"members": ["a", "b", "c"], "name": "Waves", "at": 1}])
    out = regions.apply(s, clusters, mem)
    assert out[0]["label"] == "Waves" and out[0]["topic"]["name"] == "Waves"
    assert topics.get_topic(s, t["slug"])["name"] == "Waves" and not regions._load(s)
    # not when another topic has that name, or the topic is only partly the same papers: nothing is overwritten
    other = topics.create_topic(s, "Beams", members=["h"])
    regions._save(s, [{"members": ["a", "b", "c"], "name": "Beams", "at": 1}])
    for _ in range(2):
        out = regions.apply(s, clusters, mem)
        assert out[0]["label"] == "Beams" and topics.get_topic(s, t["slug"])["name"] == "Waves"
    topics.update_topic(s, other["slug"], name="Ion beams")
    regions._save(s, [])

    # a slightly different membership is still the same region
    out = regions.apply(s, clusters, {0: ["a", "b", "c", "d"], 1: ["e", "f"]})
    assert out[0]["label"] == "Waves"

    # renaming either one renames both
    regions.rename(s, ["a", "b", "c"], "Alfvén waves", t["slug"])
    assert topics.get_topic(s, t["slug"])["name"] == "Alfvén waves"
    assert regions.apply(s, clusters, mem)[0]["label"] == "Alfvén waves"
    topics.update_topic(s, t["slug"], name="Kinetic waves")
    assert regions.apply(s, clusters, mem)[0]["label"] == "Kinetic waves"

    # a region without a topic just keeps its name, until you reset it
    regions.rename(s, ["e", "f"], "Laser physics", None)
    assert regions.apply(s, clusters, mem)[1]["label"] == "Laser physics"
    regions.reset(s, ["e", "f"])
    assert regions.apply(s, clusters, mem)[1]["label"] == "lasers"

    # a fresh library: AI names, then yours, which follows the region and beats AI and topics
    s = _store(tmp_path / "follow")
    clusters = [{"id": 0, "label": "cross, section", "size": 4}, {"id": 1, "label": "crm", "size": 3}]
    regions.save_ai_names(s, [(["a", "b", "c", "d"], "Atomic Cross Sections"), (["e", "f", "g"], "Collisional-Radiative Models")])
    out = regions.apply(s, clusters, {0: ["a", "b", "c", "d"], 1: ["e", "f", "g"]})
    assert [o["label"] for o in out] == ["Atomic Cross Sections", "Collisional-Radiative Models"]
    assert out[0]["name_source"] == "ai"
    regions.rename(s, ["e", "f", "g"], "My CRM work", None)
    # the region grows and loses a paper, step by step: the name stays with it
    for mem in (["e", "f", "g", "h"], ["f", "g", "h"], ["f", "g", "h", "a", "b"]):
        out = regions.apply(s, [clusters[1]], {1: mem})
        assert out[0]["label"] == "My CRM work" and out[0]["name_source"] == "custom", mem
    # a region that only brushes against it doesn't take it
    out = regions.apply(s, [{"id": 5, "label": "x", "size": 6}], {5: ["h", "c", "d", "x1", "x2", "x3"]})
    assert out[0]["name_source"] != "custom"


def test_ai_names_regions_from_titles_without_private_ones(tmp_path, monkeypatch):
    """The region namer sends only shareable titles to the AI, logged as region names; your name still wins."""
    srv = load_server(monkeypatch, tmp_path, fake_claude=False, RB_DATA_DIR=tmp_path / "data")
    by_id = {}
    for i, t in enumerate(["Krypton cross sections", "Xenon cross sections", "Secret unpublished idea"]):
        n = srv.store.create({"type": "paper", "title": t})
        by_id[n["id"]] = n
    ids = list(by_id)
    srv.store.update(ids[2], {"private": True})
    sent = {}

    def fake_ask(instruction, context, system="", backend=None, purpose="", sources=None):
        sent.update(context=context, purpose=purpose, sources=list(sources or []))
        return 'Sure: {"1": "Atomic Cross Sections"}'
    monkeypatch.setattr(srv.agents, "ask_parts", fake_ask)
    monkeypatch.setattr(srv, "_ai_agent", lambda: Recorder())
    priv = srv.privacy.private_ids()
    srv._region_namer._run([(frozenset(ids), [i for i in ids if i not in priv])], by_id)
    assert "Krypton cross sections" in sent["context"] and "Secret" not in sent["context"] and ids[2] not in sent["sources"]
    assert sent["purpose"] == "region names"
    out = srv.regions_mod.apply(srv.store, [{"id": 0, "label": "cross sections", "size": 3}], {0: ids})
    assert out[0]["label"] == "Atomic Cross Sections" and out[0]["name_source"] == "ai"
    srv.regions_mod.rename(srv.store, ids, "Mine", None)
    out = srv.regions_mod.apply(srv.store, [{"id": 0, "label": "cross sections", "size": 3}], {0: ids})
    assert out[0]["label"] == "Mine"
