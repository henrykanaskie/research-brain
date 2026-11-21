"""Sorting papers into topics with the AI (on import, a folder, or what's left unsorted), reorganizing topics by
what the papers are about (or into your own sections), and the AI connection analysis that links papers which
really belong together (app/server.py, app/connect_api.py).

Run:  .venv/bin/python -m pytest -q tests/test_reorganize.py
"""
import json
import time

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


def library(client):
    """Four papers filed by folder, not by what they're about: each folder mixes sheaths and language models."""
    ids = [paper(client, t, a) for t, a in [
        ("Sheath formation at a floating wall", "ion sheath presheath Bohm criterion wall potential"),
        ("Transformers for language", "attention tokens language model transformer"),
        ("Secondary electron emission and the sheath", "sheath wall secondary electron emission Bohm"),
        ("Scaling laws for language models", "language model scaling compute transformer tokens")]]
    a = client.post("/api/topics", json={"name": "Folder A", "members": ids[:2]}).json()["slug"]
    b = client.post("/api/topics", json={"name": "Folder B", "members": ids[2:]}).json()["slug"]
    return ids, a, b


def members(server, slug):
    return set(server.topics_mod.get_topic(server.store, slug)["members"])


# ---------------------------------------------------------------- sorting papers into topics
def test_ai_sorts_imported_papers_into_topics_or_falls_back_to_regions(client, server, monkeypatch):
    """The AI's groups become topics (an existing one reused, unknown ids ignored), nothing is left unsorted
    afterwards, and an answer that isn't usable falls back to the map's regions."""
    import json as _j
    ids = [paper(client, t, a) for t, a in [
        ("Sheath formation at a floating wall", "ion sheath presheath Bohm"),
        ("Secondary electron emission from walls", "sheath wall emission"),
        ("Transformers for language", "attention tokens language model"),
        ("Scaling laws for language models", "language model scaling compute")]]
    existing = client.post("/api/topics", json={"name": "Plasma sheaths"}).json()["slug"]
    seen = {}

    def fake_ask(instruction, context, purpose="ask", sources=None):
        seen.update(context=context, purpose=purpose)
        return "Here you go:\n" + _j.dumps({"topics": [
            {"name": "Plasma sheaths", "ids": ids[:2]},
            {"name": "Language models", "ids": ids[2:] + ["not-a-paper"]}]})
    monkeypatch.setattr(server, "_ask", fake_ask)
    r = client.post("/api/topics/auto-sort", json={"ids": ids}).json()
    assert r["by"] == "ai" and r["sorted"] == 4
    assert {(t["name"], t["count"], t["new"]) for t in r["topics"]} == {("Plasma sheaths", 2, False), ("Language models", 2, True)}
    assert set(server.topics_mod.get_topic(server.store, existing)["members"]) == set(ids[:2])  # reused, not duplicated
    assert "EXISTING TOPICS: Plasma sheaths" in seen["context"] and f"[[{ids[0]}]]" in seen["context"]
    # nothing left without a topic
    assert client.post("/api/topics/auto-sort", json={"unsorted": True}).json()["sorted"] == 0

    # no usable answer: the map's regions instead
    more = [paper(client, f"Sheath paper {i}", "ion sheath presheath Bohm wall") for i in range(3)]
    monkeypatch.setattr(server, "_ask", lambda *a, **k: "I can't do JSON today")
    r = client.post("/api/topics/auto-sort", json={"ids": more}).json()
    assert r["by"] == "regions" and r["sorted"] == 3 and r["topics"]


def test_sorting_a_folder_by_content_and_asking_for_a_number_of_topics(client, server, monkeypatch):
    """A folder's papers are all sent for sorting, and a requested number of topics is asked for (only when given)."""
    ids, a, b = library(client)
    monkeypatch.setattr(server.store.links, "files_in", lambda fid: {i: {} for i in ids} if fid == "f1" else {})
    seen = {}
    monkeypatch.setattr(server, "_ask", lambda i, c, *x, **k: seen.setdefault("c", c) and json.dumps(
        {"topics": [{"name": "Plasma sheaths", "ids": [ids[0], ids[2]]}, {"name": "Language models", "ids": [ids[1], ids[3]]}]}))
    r = client.post("/api/topics/auto-sort", json={"folder": "f1"}).json()
    assert r["sorted"] == 4 and all(f"[[{i}]]" in seen["c"] for i in ids)

    asked = []
    monkeypatch.setattr(server, "_ask", lambda i, c, *x, **k: asked.append(i) or json.dumps(
        {"topics": [{"name": "Everything", "ids": ids}]}))
    client.post("/api/topics/reorganize/plan", json={"topics": [a, b], "count": 3})
    assert "Make exactly 3 topics" in asked[-1]
    client.post("/api/topics/reorganize/plan", json={"topics": [a, b]})
    assert "Make exactly" not in asked[-1]
    client.post("/api/topics/auto-sort", json={"ids": ids, "count": 1})
    assert "Make exactly 1 topic in all" in asked[-1]


def test_import_sorts_only_into_your_existing_topics(client, server, monkeypatch):
    """With existing_only, papers go only into topics you have (matched by name, any case); a new name is left unplaced."""
    ids, a, b = library(client)
    new = [paper(client, "Bohm sheath at a probe", "sheath probe"), paper(client, "Tokenizers", "language tokens")]
    seen = {}

    def fake_ask(instruction, context, purpose="ask", sources=None):
        seen.update(instruction=instruction, context=context)
        return json.dumps({"topics": [{"name": "folder a", "ids": [new[0]]}, {"name": "Brand new", "ids": [new[1]]}],
                           "none": []})
    monkeypatch.setattr(server, "_ask", fake_ask)
    r = client.post("/api/topics/auto-sort", json={"ids": new, "existing_only": True}).json()
    assert "never make a new one" in seen["instruction"] and "- Folder A\n- Folder B" in seen["context"]
    assert r == {"topics": [{"name": "Folder A", "slug": a, "count": 1, "new": False}], "sorted": 1, "by": "ai", "unplaced": 1}
    assert new[0] in members(server, a) and {t["name"] for t in client.get("/api/topics").json()} == {"Folder A", "Folder B"}


def test_private_papers_are_never_sent_for_sorting_or_reorganizing(client, server, monkeypatch):
    """A private paper is left out of what auto-sort and the reorganize plan send (and of the plan), an unusable
    answer plans by regions, and a plan needs papers."""
    ids = [paper(client, f"Paper {i}", "words") for i in range(3)]
    client.put(f"/api/notes/{ids[0]}", json={"private": True})
    seen = {}
    monkeypatch.setattr(server, "_ask", lambda i, c, *a, **k: seen.setdefault("c", c) and '{"topics": []}')
    client.post("/api/topics/auto-sort", json={"ids": ids})
    assert f"[[{ids[0]}]]" not in seen["c"] and f"[[{ids[1]}]]" in seen["c"]

    ids, a, b = library(client)
    client.put(f"/api/notes/{ids[0]}", json={"private": True})
    seen.clear()
    plan = client.post("/api/topics/reorganize/plan", json={"topics": [a, b]}).json()
    assert ids[0] not in seen["c"] and f"[[{ids[1]}]]" in seen["c"] and ids[0] not in plan["ids"]
    assert plan["by"] == "regions"  # no usable answer: the map's regions instead
    assert client.post("/api/topics/reorganize/plan", json={"topics": []}).status_code == 400


# ---------------------------------------------------------------- reorganizing
def test_reorganize_plans_into_your_sections_or_by_content_then_moves_papers_and_can_be_undone(client, server, monkeypatch):
    """With your own section names the plan keeps exactly those names and leaves the rest unplaced; by content,
    the plan changes nothing until applied, then papers move out of the old topics (a topic still holding a
    note stays), and undo puts everything back."""
    ids, a, b = library(client)
    note = client.post("/api/notes", json={"type": "note", "title": "My reading plan"}).json()["id"]
    client.post(f"/api/topics/{b}/notes", json={"ids": [note]})  # a note isn't a paper: it stays where it is
    seen = {}

    def own_sections(instruction, context, purpose="ask", sources=None):
        seen["context"] = context
        return json.dumps({"topics": [{"name": "plasma physics", "ids": [ids[0], ids[2]]},
                                      {"name": "Something it made up", "ids": [ids[1]]}], "none": [ids[3]]})
    monkeypatch.setattr(server, "_ask", own_sections)
    plan = client.post("/api/topics/reorganize/plan",
                       json={"topics": [a, b], "names": ["Plasma physics", "Machine learning\nOptics; plasma physics"]}).json()
    assert "SECTIONS:\n- Plasma physics\n- Machine learning\n- Optics" in seen["context"]
    groups = {g["name"]: set(g["ids"]) for g in plan["groups"]}
    # only your names, exactly as you wrote them; the one it made up and the one it couldn't place are left over
    assert groups == {"Plasma physics": {ids[0], ids[2]}, "Machine learning": set(), "Optics": set()}
    assert {p["id"] for p in plan["unplaced"]} == {ids[1], ids[3]}

    def by_content(instruction, context, purpose="ask", sources=None):
        seen.update(instruction=instruction, context=context)
        return json.dumps({"topics": [{"name": "Plasma sheaths", "ids": [ids[0], ids[2]]},
                                      {"name": "Language models", "ids": [ids[1], ids[3]]}]})
    monkeypatch.setattr(server, "_ask", by_content)
    plan = client.post("/api/topics/reorganize/plan", json={"topics": [a, b]}).json()
    assert plan["by"] == "ai" and set(plan["ids"]) == set(ids) and plan["replace"] == [a, b]
    assert {g["name"]: set(g["ids"]) for g in plan["groups"]} == {"Plasma sheaths": {ids[0], ids[2]}, "Language models": {ids[1], ids[3]}}
    assert plan["removed"] == ["Folder A"]  # Folder B keeps the note, so it stays
    assert "Ignore how the papers are grouped now" in seen["instruction"] and "CURRENT TOPICS: Folder A; Folder B" in seen["context"]
    # nothing changed yet
    assert members(server, a) == set(ids[:2])

    r = client.post("/api/topics/reorganize", json={"groups": [{"name": g["name"], "ids": g["ids"]} for g in plan["groups"]],
                                                    "ids": plan["ids"], "replace": plan["replace"]}).json()
    assert {t["name"] for t in r["topics"]} == {"Plasma sheaths", "Language models"} and r["deleted"] == ["Folder A"]
    by_name = {t["name"]: t["slug"] for t in client.get("/api/topics").json()}
    assert set(by_name) == {"Plasma sheaths", "Language models", "Folder B"}
    assert members(server, by_name["Plasma sheaths"]) == {ids[0], ids[2]}
    assert members(server, b) == {note}

    assert client.post("/api/topics/reorganize/undo", json={"token": r["undo"]}).json()["ok"]
    assert {t["name"] for t in client.get("/api/topics").json()} == {"Folder A", "Folder B"}
    assert members(server, a) == set(ids[:2])
    assert client.post("/api/topics/reorganize/undo", json={"token": "../../x"}).status_code == 400


# ---------------------------------------------------------------- connection analysis
def wait(client, jid):
    for _ in range(200):
        j = client.get(f"/api/links/ai-connect/jobs/{jid}").json()
        if j["state"] not in ("waiting", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("the job didn't finish")


def test_connection_analysis_links_the_pairs_the_ai_keeps_and_remembers_what_it_asked(client, server, monkeypatch):
    """Without an AI the job says so (and one paper is refused); with one, the pairs it keeps become AI
    connections on the map (not links of yours: no red lines you didn't draw), pairs already asked about aren't
    sent again, undo takes them back, and one can be removed or made your own."""
    ids, a, b = library(client)

    def no_ai(*a, **k):
        raise server.HTTPException(502, "No AI agent is set up.")
    monkeypatch.setattr(server, "_ask", no_ai)
    j = wait(client, client.post("/api/links/ai-connect", json={"ids": ids}).json()["id"])
    assert j["state"] == "error" and "No AI agent" in j["message"]
    assert client.post("/api/links/ai-connect", json={"ids": ids[:1]}).status_code == 400

    asked = []

    def fake_ask(instruction, context, purpose="ask", sources=None):
        asked.append(context)
        assert purpose == "connection analysis" and "CANDIDATE PAIRS" in context
        return json.dumps({"links": [{"a": ids[0], "b": ids[2], "why": "both about the Bohm sheath"},
                                     {"a": ids[1], "b": "not-there"}]})
    monkeypatch.setattr(server, "_ask", fake_ask)
    job = client.post("/api/links/ai-connect", json={"topics": [a, b]}).json()
    assert "ids" not in job
    j = wait(client, job["id"])
    assert j["state"] == "done" and j["linked"] == 1 and j["checked"] == j["pairs"] > 0, j
    assert j["links"][0]["why"] == "both about the Bohm sheath" and j["links"][0]["a_title"]
    assert ids[2] not in client.get(f"/api/notes/{ids[0]}").json()["links"]  # not written as your link
    g = client.get("/api/graph").json()
    pair = lambda l: {l["source"], l["target"]} == {ids[0], ids[2]}
    assert any(l["kind"] == "ai" and pair(l) and l["why"] for l in g["links"])
    assert not any(l["kind"] == "manual" and pair(l) for l in g["links"])

    # again: every pair was asked about already, so nothing goes to the AI
    n = len(asked)
    j2 = wait(client, client.post("/api/links/ai-connect", json={"ids": ids}).json()["id"])
    assert j2["state"] == "done" and len(asked) == n and j2["linked"] == 0

    # undo takes the links back
    u = client.post(f"/api/links/ai-connect/jobs/{j['id']}/undo").json()
    assert u["undone"] == 1
    assert not any(l["kind"] == "ai" and pair(l) for l in client.get("/api/graph").json()["links"])

    # a new run's connection: remove it (never suggested again), or keep it as your own link
    monkeypatch.setattr(server, "_ask", lambda *a, **k: json.dumps({"links": [{"a": ids[1], "b": ids[3], "why": "x"}]}))
    from app import connect_api
    cache = connect_api._load_cache()
    for k in [k for k in cache if ids[1] in k]:
        del cache[k]
    connect_api._save_cache(cache)
    wait(client, client.post("/api/links/ai-connect", json={"ids": ids}).json()["id"])
    p13 = lambda l: {l["source"], l["target"]} == {ids[1], ids[3]}
    assert any(l["kind"] == "ai" and p13(l) for l in client.get("/api/graph").json()["links"])
    assert client.post("/api/links/ai/keep", json={"a": ids[1], "b": ids[3]}).status_code == 200
    links = client.get("/api/graph").json()["links"]
    assert any(l["kind"] == "manual" and p13(l) for l in links) and not any(l["kind"] == "ai" and p13(l) for l in links)
    assert client.post("/api/links/ai/remove", json={"a": ids[1], "b": ids[3]}).status_code == 404  # yours now


def test_links_an_older_version_made_turn_into_ai_connections_and_back(client, server):
    """An earlier version wrote the AI's answers as your own links. Those (and only those) can be turned into AI
    connections, and back."""
    ids, _a, _b = library(client)
    from app import connect_api
    client.post(f"/api/notes/{ids[0]}/link", json={"target": ids[2]})  # made by the old analysis
    client.post(f"/api/notes/{ids[1]}/link", json={"target": ids[3]})  # one you drew yourself
    connect_api._save_cache({connect_api._key(ids[0], ids[2]): {"v": 1, "at": 1, "why": "old"}})
    assert client.get("/api/links/ai-connect/legacy").json() == {"count": 1}
    assert client.post("/api/links/ai-connect/legacy", json={}).json() == {"converted": 1}
    assert client.get("/api/links/ai-connect/legacy").json() == {"count": 0}
    links = client.get("/api/graph").json()["links"]
    assert any(l["kind"] == "ai" and {l["source"], l["target"]} == {ids[0], ids[2]} for l in links)
    assert any(l["kind"] == "manual" and {l["source"], l["target"]} == {ids[1], ids[3]} for l in links)
    assert client.post("/api/links/ai-connect/legacy", json={"undo": True}).json() == {"converted": 1}
    assert ids[2] in client.get(f"/api/notes/{ids[0]}").json()["links"]


def test_every_section_is_joined_by_its_similarity_tree(client):
    """Inside each section, a tree of similarity lines joins every paper (n - 1 lines), whatever the threshold."""
    library(client)
    g = client.get("/api/graph").json()
    by = {}
    for n in g["nodes"]:
        if n["type"] != "fragment" and n["cluster"] >= 0:
            by.setdefault(n["cluster"], set()).add(n["id"])
    tree = [l for l in g["similar"] if l.get("tree")]
    assert by and tree
    for cid, mem in by.items():
        inside = [l for l in tree if l["source"] in mem and l["target"] in mem]
        assert len(inside) == len(mem) - 1, (cid, len(mem), len(inside))
        reach, todo = set(), [next(iter(mem))]
        while todo:
            x = todo.pop()
            if x in reach:
                continue
            reach.add(x)
            todo += [l["target"] if l["source"] == x else l["source"] for l in inside if x in (l["source"], l["target"])]
        assert reach == mem
