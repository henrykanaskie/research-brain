"""Reorganizing topics by what the papers are about (or into your own sections), and the AI connection analysis
that links papers which really belong together (app/server.py, app/connect_api.py).

Run:  .venv/bin/python -m pytest -q tests/test_reorganize.py
"""
import importlib
import json
import sys
import time
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


def paper(client, title, abstract=""):
    r = client.post("/api/notes", json={"type": "paper", "title": title, "abstract": abstract})
    assert r.status_code == 201, r.text
    return r.json()["id"]


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


def test_reorganize_plans_then_moves_papers_out_of_the_old_topics_and_can_be_undone(client, server, monkeypatch):
    ids, a, b = library(client)
    note = client.post("/api/notes", json={"type": "note", "title": "My reading plan"}).json()["id"]
    client.post(f"/api/topics/{b}/notes", json={"ids": [note]})  # a note isn't a paper: it stays where it is
    seen = {}

    def fake_ask(instruction, context, purpose="ask", sources=None):
        seen.update(instruction=instruction, context=context)
        return json.dumps({"topics": [{"name": "Plasma sheaths", "ids": [ids[0], ids[2]]},
                                      {"name": "Language models", "ids": [ids[1], ids[3]]}]})
    monkeypatch.setattr(server, "_ask", fake_ask)
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


def test_reorganize_into_your_own_sections(client, server, monkeypatch):
    ids, a, b = library(client)
    seen = {}

    def fake_ask(instruction, context, purpose="ask", sources=None):
        seen["context"] = context
        return json.dumps({"topics": [{"name": "plasma physics", "ids": [ids[0], ids[2]]},
                                      {"name": "Something it made up", "ids": [ids[1]]}], "none": [ids[3]]})
    monkeypatch.setattr(server, "_ask", fake_ask)
    plan = client.post("/api/topics/reorganize/plan",
                       json={"topics": [a, b], "names": ["Plasma physics", "Machine learning\nOptics; plasma physics"]}).json()
    assert "SECTIONS:\n- Plasma physics\n- Machine learning\n- Optics" in seen["context"]
    groups = {g["name"]: set(g["ids"]) for g in plan["groups"]}
    # only your names, exactly as you wrote them; the one it made up and the one it couldn't place are left over
    assert groups == {"Plasma physics": {ids[0], ids[2]}, "Machine learning": set(), "Optics": set()}
    assert {p["id"] for p in plan["unplaced"]} == {ids[1], ids[3]}


def test_reorganize_needs_papers_and_never_sends_private_ones(client, server, monkeypatch):
    ids, a, b = library(client)
    client.put(f"/api/notes/{ids[0]}", json={"private": True})
    seen = {}
    monkeypatch.setattr(server, "_ask", lambda i, c, *x, **k: seen.setdefault("c", c) and '{"topics": []}')
    plan = client.post("/api/topics/reorganize/plan", json={"topics": [a, b]}).json()
    assert ids[0] not in seen["c"] and f"[[{ids[1]}]]" in seen["c"] and ids[0] not in plan["ids"]
    assert plan["by"] == "regions"  # no usable answer: the map's regions instead
    assert client.post("/api/topics/reorganize/plan", json={"topics": []}).status_code == 400


def wait(client, jid):
    for _ in range(200):
        j = client.get(f"/api/links/ai-connect/jobs/{jid}").json()
        if j["state"] not in ("waiting", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("the job didn't finish")


def test_connection_analysis_links_the_pairs_the_ai_keeps_and_remembers_what_it_asked(client, server, monkeypatch):
    ids, a, b = library(client)
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
    assert ids[2] in client.get(f"/api/notes/{ids[0]}").json()["links"]
    g = client.get("/api/graph").json()
    assert any(l["kind"] == "manual" and {l["source"], l["target"]} == {ids[0], ids[2]} for l in g["links"])

    # again: every pair was asked about already, so nothing goes to the AI
    n = len(asked)
    j2 = wait(client, client.post("/api/links/ai-connect", json={"ids": ids}).json()["id"])
    assert j2["state"] == "done" and len(asked) == n and j2["linked"] == 0

    # undo takes the links back
    u = client.post(f"/api/links/ai-connect/jobs/{j['id']}/undo").json()
    assert u["undone"] == 1 and ids[2] not in client.get(f"/api/notes/{ids[0]}").json()["links"]


def test_connection_analysis_without_an_ai_says_so(client, server, monkeypatch):
    ids, a, b = library(client)

    def no_ai(*a, **k):
        raise server.HTTPException(502, "No AI agent is set up.")
    monkeypatch.setattr(server, "_ask", no_ai)
    j = wait(client, client.post("/api/links/ai-connect", json={"ids": ids}).json()["id"])
    assert j["state"] == "error" and "No AI agent" in j["message"]
    assert client.post("/api/links/ai-connect", json={"ids": ids[:1]}).status_code == 400


def test_sorting_a_folder_of_papers_by_content(client, server, monkeypatch):
    ids, a, b = library(client)
    monkeypatch.setattr(server.store.links, "files_in", lambda fid: {i: {} for i in ids} if fid == "f1" else {})
    seen = {}
    monkeypatch.setattr(server, "_ask", lambda i, c, *x, **k: seen.setdefault("c", c) and json.dumps(
        {"topics": [{"name": "Plasma sheaths", "ids": [ids[0], ids[2]]}, {"name": "Language models", "ids": [ids[1], ids[3]]}]}))
    r = client.post("/api/topics/auto-sort", json={"folder": "f1"}).json()
    assert r["sorted"] == 4 and all(f"[[{i}]]" in seen["c"] for i in ids)
